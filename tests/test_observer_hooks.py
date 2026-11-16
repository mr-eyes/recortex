"""Native source capture and immediate local control checks; no model/network."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "recortex" / "scripts"))
import observer_hooks as hooks
import observer_sources as sources
from observer_store import Store, StoreError
from tests.observer_source_fixture import fixture_trusted_ancestors

REPO = "octo-org/octo-repo"


class ObserverHookTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        # Native transcript validation rejects group-writable ancestors; this
        # checkout can itself be group-writable. The sticky /tmp parent is valid.
        tmp = tempfile.TemporaryDirectory(prefix="observer-hooks-", dir="/tmp")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        metadata = fixture_trusted_ancestors(self.base)
        metadata.__enter__()
        self.addCleanup(metadata.__exit__, None, None, None)
        self.project = self.base / "project"
        self.project.mkdir(mode=0o700)
        patch = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.base / "state"), "HOME": str(self.base / "home"),
                               "XDG_CACHE_HOME": str(self.base / "cache"), "XDG_CONFIG_HOME": str(self.base / "config"),
                               "CLAUDE_CONFIG_DIR": str(self.base / "claude"), "CLAUDE_PROJECT_DIR": str(self.project)}, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.path = Path(sources.project_directory(str(self.project))) / "session1.jsonl"
        self.path.parent.mkdir(mode=0o700, parents=True)
        for folder in (self.base / "claude", self.base / "claude" / "projects", self.path.parent):
            folder.chmod(0o700)
        self.path.write_text("")
        self.path.chmod(0o600)
        self.config = {"repo": REPO, "hub": 1, "observer": {"enabled": True}}
        self.data = {"session_id": "session1", "transcript_path": str(self.path), "cwd": str(self.project)}
        for target, value in (("recortex.trusted_repos", {REPO: set()}),
                              ("recortex.allowed_users", {"octocat"}),
                              ("observer_runtime.approval_valid", True),
                              ("observer_runtime.gate_valid", False), ("_context", "")):
            patch = mock.patch("observer_hooks." + target, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch("observer_hooks.launch_worker")
        self.worker = patch.start()
        self.addCleanup(patch.stop)

    def append(self, text, role="user", **extra):
        record = dict(type=role, message={"role": role, "content": text}, **extra)
        with self.path.open("a") as handle:
            handle.write(json.dumps(record) + "\n")

    def run_hook(self, event="pre-tool", command="git push"):
        data = dict(self.data, tool_name="Bash", tool_input={"command": command})
        return hooks.handle(event, data, self.config, REPO)

    def test_original_hold_is_enforced_before_worker_or_inference(self):
        self.append("/recortex hold git-push")
        result = self.run_hook()
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        self.worker.assert_not_called()
        with Store(REPO) as store:
            self.assertEqual(1, len(store.holds()))
            self.assertEqual(1, len(store.pending()))
            metadata = store.pending()[0]["metadata"]
            self.assertNotIn("text", metadata)
            self.assertEqual("user", metadata["role"])

    def test_compaction_assistant_and_tool_result_cannot_create_holds(self):
        self.append("/recortex hold git-push", role="assistant")
        self.append("/recortex hold git-push", isMeta=True)
        self.append([{"type": "tool_result", "content": "/recortex hold git-push"}])
        result = self.run_hook()
        self.assertNotIn("hookSpecificOutput", result)
        with Store(REPO) as store:
            self.assertEqual([], store.holds())

    def test_release_requires_direct_exact_source_and_persists(self):
        self.append("/recortex hold git-push")
        self.run_hook()
        with Store(REPO) as store:
            hold_id = store.holds()[0]["id"]
        self.append("/recortex release " + hold_id, role="assistant")
        self.assertEqual("deny", self.run_hook()["hookSpecificOutput"]["permissionDecision"])
        self.append("/recortex release " + hold_id)
        self.assertNotIn("hookSpecificOutput", self.run_hook())
        with Store(REPO) as store:
            self.assertEqual("released", store.holds()[0]["state"])

    def test_other_project_command_does_not_inherit_current_hold(self):
        self.append("/recortex hold gh-release")
        self.assertNotIn("hookSpecificOutput", self.run_hook(command="gh release create v1 --repo other/project"))
        self.assertEqual("deny", self.run_hook(command="gh release create v1")["hookSpecificOutput"]["permissionDecision"])

    def test_unpersisted_hook_prompt_is_not_claimed_as_user_authority(self):
        data = dict(self.data, prompt="/recortex hold git-push")
        result = hooks.handle("prompt-submit", data, self.config, REPO)
        self.assertIn("new holds may not be active", result["systemMessage"])
        with Store(REPO) as store:
            self.assertEqual([], store.holds())
        with self.path.open("a") as handle:
            handle.write('{"message":')
        result = self.run_hook()
        self.assertNotIn("hookSpecificOutput", result)
        with Store(REPO) as store:
            self.assertEqual("source-incomplete", store.get_control("hook_degraded"))

    def test_recursion_optout_and_untrusted_are_silent_without_store(self):
        with mock.patch("observer_hooks.observer_store.Store") as store:
            with mock.patch.dict(os.environ, {"RECORTEX_OBSERVER": "1"}):
                self.assertEqual({}, self.run_hook())
            self.config["observer"]["enabled"] = False
            self.assertEqual({}, self.run_hook())
            self.config["observer"]["enabled"] = True
            with mock.patch("observer_hooks.recortex.trusted_repos", return_value={}):
                self.assertEqual({}, self.run_hook())
            store.assert_not_called()

    def test_pending_budget_and_storage_failure_are_fixed_visible_notices(self):
        self.config["observer"]["max_invocations"] = 1
        self.append("A result was reported", role="assistant")
        with Store(REPO) as store:
            store.reserve_invocation("reviewer", limit=1)
        result = self.run_hook(event="stop")
        self.assertIn("budget is exhausted", result["systemMessage"])
        self.assertIn("not yet verified in GitHub", result["systemMessage"])
        with mock.patch("observer_hooks.observer_store.Store", side_effect=ValueError("SECRET SOURCE")):
            result = self.run_hook()
            self.assertIn("capture is incomplete", result["systemMessage"])
            self.assertNotIn("SECRET", json.dumps(result))

    def test_worker_launch_only_after_local_gate_and_approval(self):
        with mock.patch("observer_hooks.observer_runtime.gate_valid", return_value=True):
            self.run_hook(event="stop")
            self.worker.assert_called_once_with(str(self.project))
        self.worker.reset_mock()
        with mock.patch("observer_hooks.observer_runtime.approval_valid", return_value=False):
            self.assertEqual({}, self.run_hook(event="stop"))
            result = hooks.handle("session-start", self.data, self.config, REPO)
            self.assertIn("approval", result["systemMessage"])
            self.worker.assert_not_called()

    def test_invalid_approval_gets_one_line_per_session_and_creates_no_state(self):
        with mock.patch("observer_hooks.observer_runtime.approval_valid", return_value=False), \
                mock.patch("observer_hooks.observer_store.Store", side_effect=AssertionError("no store")) as opened:
            outputs = [self.run_hook(event=e) for e in ("pre-tool", "post-tool", "stop", "prompt-submit")]
            start = hooks.handle("session-start", self.data, self.config, REPO)
        self.assertEqual([{}, {}, {}, {}], outputs)
        self.assertIn("approval is missing", start["systemMessage"])
        opened.assert_not_called()

    def test_capture_pause_keeps_established_hold_active(self):
        self.append("/recortex hold git-push")
        self.run_hook()
        with Store(REPO) as store:
            store.set_control("capture_paused", True)
        result = self.run_hook()
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        self.assertIn("capture is paused", result["systemMessage"])
        with Store(REPO) as store:
            hold_id = store.holds()[0]["id"]
        self.append("/recortex release " + hold_id)
        self.assertNotIn("hookSpecificOutput", self.run_hook())

    def test_exact_control_containing_restricted_text_is_refused_before_storage(self):
        self.config["restricted_patterns"] = ["PRIVATE-SAMPLE"]
        self.append("/recortex hold command publish PRIVATE-SAMPLE")
        result = self.run_hook(command="publish PRIVATE-SAMPLE")
        self.assertIn("control could not be stored safely", result["systemMessage"])
        with Store(REPO) as store:
            self.assertEqual([], store.holds())
            self.assertNotIn(b"PRIVATE-SAMPLE", Path(store.path).read_bytes())

    def test_unchanged_notices_are_suppressed_until_stop_or_change(self):
        first, second = self.run_hook(), self.run_hook()
        self.assertIn("systemMessage", first)
        self.assertNotIn("systemMessage", second)
        self.assertIn("systemMessage", self.run_hook(event="stop"))

    def test_existing_hold_survives_capture_and_diagnostic_write_failure(self):
        self.append("/recortex hold git-push")
        self.run_hook()
        with mock.patch.object(Store, "capture", side_effect=StoreError("capacity")), \
                mock.patch.object(Store, "set_control", side_effect=StoreError("capacity")):
            result = self.run_hook()
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        self.assertIn("capture is incomplete", result["systemMessage"])

    def test_complete_capture_clears_source_gap_after_recovery(self):
        with self.path.open("a") as handle:
            handle.write('{"message":')
        self.run_hook()
        with self.path.open("a") as handle:
            handle.write('{"role":"user","content":"hello"}}\n')
        self.run_hook()
        with Store(REPO) as store:
            self.assertFalse(store.get_control("hook_degraded"))

    def test_deadline_stops_after_capture_and_preserves_only_an_existing_hold(self):
        with mock.patch("observer_hooks._capture", side_effect=TimeoutError), \
                mock.patch("observer_hooks._context") as context, \
                mock.patch("observer_hooks.observer_runtime.gate_valid", return_value=True):
            result = self.run_hook()
        context.assert_not_called()
        self.worker.assert_not_called()
        self.assertNotIn("hookSpecificOutput", result)
        self.append("/recortex hold git-push")
        self.run_hook()
        with mock.patch("observer_hooks._capture", side_effect=TimeoutError):
            result = self.run_hook()
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])

    def test_deadline_during_context_does_not_continue_to_worker_dispatch(self):
        with mock.patch("observer_hooks._context", side_effect=TimeoutError), \
                mock.patch("observer_hooks.observer_runtime.gate_valid", return_value=True):
            result = self.run_hook()
        self.worker.assert_not_called()
        self.assertNotIn("hookSpecificOutput", result)
        self.assertIn("capture is incomplete", result["systemMessage"])

    def test_handled_retention_dispatch_does_not_require_model_gate_or_capture(self):
        with Store(REPO) as store:
            store.set_control("capture_paused", True)
        status = {"events": {"verified": 1}, "observations": {}, "invocations_24h": 0}
        with mock.patch.object(Store, "status", return_value=status):
            result = self.run_hook(event="stop")
        self.worker.assert_called_once_with(str(self.project))
        self.assertIn("independent checks are unavailable", result["systemMessage"])
        self.worker.reset_mock()
        import time
        with Store(REPO) as store:
            store.set_control("retention_last", time.time())
        with mock.patch.object(Store, "status", return_value=status):
            self.run_hook(event="stop")
        self.worker.assert_not_called()


class SharedProjectFileTests(unittest.TestCase):
    """A shared project file cannot switch the observer on for another machine (no approval patch here)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="observer-shared-", dir="/tmp")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.project = self.base / "project"
        self.project.mkdir(mode=0o700)
        trusted = self.base / "trusted"
        trusted.write_text(REPO + " octocat\n")
        patch = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.base / "state"), "HOME": str(self.base / "home"),
                                             "XDG_CACHE_HOME": str(self.base / "cache"),
                                             "XDG_CONFIG_HOME": str(self.base / "config"),
                                             "RECORTEX_TRUSTED_FILE": str(trusted),
                                             "CLAUDE_CONFIG_DIR": str(self.base / "claude"),
                                             "CLAUDE_PROJECT_DIR": str(self.project)}, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_enabled_project_file_does_nothing_on_a_machine_without_its_own_approval(self):
        import observer_runtime
        shared = {"repo": REPO, "hub": 1, "observer": {"enabled": True, "mode": "automatic", "model": "sonnet",
                                                       "publisher_id": "a" * 32}}
        config = observer_runtime.local_config(shared)
        self.assertNotIn("observer", config)
        data = {"session_id": "session1", "transcript_path": str(self.base / "none.jsonl"), "cwd": str(self.project),
                "tool_name": "Bash", "tool_input": {"command": "git push"}}
        for event in ("session-start", "pre-tool", "post-tool", "stop"):
            self.assertEqual({}, hooks.handle(event, data, config, REPO), event)
            # Even the project file's own entry, were it ever passed unchecked, finds no approval and creates nothing.
            self.assertNotIn("hookSpecificOutput", hooks.handle(event, data, shared, REPO), event)
        self.assertFalse((self.base / "state").exists())


if __name__ == "__main__":
    unittest.main()
