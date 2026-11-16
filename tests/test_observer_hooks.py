"""Native source capture and immediate local control checks; no model/network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "recortex" / "scripts"))
import names
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
        self._context = patch  # the last one; a test of the real context stops it
        patch = mock.patch("observer_hooks.launch_worker")
        self.worker = patch.start()
        self.addCleanup(patch.stop)

    def append(self, text, role="user", **extra):
        record = dict(dict(type=role, userType="external", message={"role": role, "content": text}), **extra)
        with self.path.open("a") as handle:
            handle.write(json.dumps(record) + "\n")

    def run_hook(self, event="pre-tool", command="git push"):
        data = dict(self.data, tool_name="Bash", tool_input={"command": command})
        return hooks.handle(event, data, self.config, REPO)

    def test_a_hold_outlasts_the_project_files_switch_and_session_release_routes(self):
        import observer_runtime
        self.append("/recortex hold git-push")
        self.assertEqual("deny", self.run_hook()["hookSpecificOutput"]["permissionDecision"])
        observer_runtime.approve(REPO, {"observer": {"enabled": True, "model": "haiku",
                                                     "publisher_id": observer_runtime.publisher_id(create=True)}},
                                 str(self.project))
        on, self.config = self.config, {"repo": REPO, "hub": 1}  # local_config once the project file says false
        self.assertEqual("deny", self.run_hook()["hookSpecificOutput"]["permissionDecision"])
        self.config = on
        with Store(REPO) as store:
            hold_id = store.holds()[0]["id"]
        for command in ('script -qec "python3 observer.py release %s" /dev/null' % hold_id,
                        "python3 skills/recortex/scripts/observer.py disable"):
            self.assertEqual("deny", self.run_hook(command=command)["hookSpecificOutput"]["permissionDecision"], command)

    def entry_and_hold(self):
        import observer_runtime
        self.append("/recortex hold git-push")
        self.run_hook()
        observer_runtime.approve(REPO, {"observer": {"enabled": True, "model": "haiku",
                                                     "publisher_id": observer_runtime.publisher_id(create=True)}},
                                 str(self.project))

    def test_the_held_repository_comes_from_this_folders_entry_not_the_project_file(self):
        import contextlib, io, observer
        self.entry_and_hold()
        data = dict(self.data, tool_name="Bash", tool_input={"command": "git push"})
        for name in ("octo-org/other", "not a repository", None):
            edited = {"repo": name, "hub": 1, "observer": {"enabled": True}} if name else {"hub": 1}
            result = hooks.handle("pre-tool", data, edited, name)
            self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"], name)
        for loaded in ({"repo": "octo-org/other"}, {"repo": "not a repository"}, ValueError("not JSON")):
            out = io.StringIO()
            with mock.patch.object(observer.recortex, "load_config", side_effect=[loaded] if isinstance(loaded, dict) else loaded), \
                    mock.patch.object(observer.sys, "stdin", io.StringIO(json.dumps(data))), contextlib.redirect_stdout(out):
                self.assertEqual(0, observer.hook("pre-tool"))
            self.assertEqual("deny", json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], loaded)

    def test_the_project_files_repository_holds_apply_when_the_approval_does_not(self):
        # The entry names another repository, so this folder's approval does not hold for the project file's; the
        # holds of both repositories still apply before a Bash call.
        import observer_runtime
        self.append("/recortex hold git-push")
        self.run_hook()
        observer_runtime.approve("octo-org/other", {"observer": {"enabled": True, "model": "haiku",
                                                                 "publisher_id": observer_runtime.publisher_id(create=True)}},
                                 str(self.project))
        with mock.patch("observer_hooks.observer_runtime.approval_valid", return_value=False):
            result = self.run_hook()
            self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
            self.assertNotIn("hookSpecificOutput", self.run_hook(command="git status"))

    def cli(self, *args, answer=None):
        """observer.py in this process, with a terminal that answers `answer`, or none at all."""
        import contextlib, io, observer
        tty = None
        if answer is not None:
            tty = mock.MagicMock()
            tty.__enter__.return_value = tty
            tty.ask.return_value = answer
        out = io.StringIO()
        with mock.patch.object(observer.recortex, "load_config", return_value={"repo": REPO, "hub": 1}), \
                mock.patch.object(observer, "terminal", return_value=tty), contextlib.redirect_stdout(out):
            code = observer.main(list(args))
        return code, json.loads(out.getvalue().splitlines()[-1]), tty

    def folder_entry(self):
        import observer_runtime
        return observer_runtime.stored_approval(str(self.project))

    def test_automatic_mode_is_asked_on_the_users_terminal(self):
        with mock.patch("observer_hooks.observer_runtime.gate_valid", return_value=True):
            self.assertEqual(0, self.cli("enable", "--model", "haiku", "--yes")[0])  # shadow: nothing to ask
            for answer, code in ((None, "terminal-required"), ("no", "not-confirmed")):
                result, value, tty = self.cli("enable", "--mode", "automatic", "--model", "haiku", "--yes", answer=answer)
                self.assertEqual((1, code, False), (result, value["code"], value["approved"]), answer)
                self.assertEqual("shadow", self.folder_entry()["settings"]["mode"])
            result, value, tty = self.cli("enable", "--mode", "automatic", "--model", "haiku", "--yes", answer="enable")
            self.assertEqual((0, True), (result, value["approved"]))
            self.assertIn("post reviewed memories", tty.ask.call_args.args[0])
            self.assertEqual("automatic", self.folder_entry()["settings"]["mode"])

    def test_replacing_this_folders_entry_while_holds_apply_is_asked_on_the_users_terminal(self):
        self.entry_and_hold()
        result, value, _ = self.cli("enable", "--model", "sonnet", "--yes")
        self.assertEqual((1, "terminal-required"), (result, value["code"]))
        self.assertIn("separate terminal", value["note"])
        self.assertEqual("haiku", self.folder_entry()["settings"]["model"])
        result, value, tty = self.cli("enable", "--model", "sonnet", "--yes", answer="enable")
        self.assertEqual(0, result, value)
        self.assertIn("while holds apply", tty.ask.call_args.args[0])
        self.assertEqual("sonnet", self.folder_entry()["settings"]["model"])
        with Store(REPO) as store:
            self.assertEqual(["active"], [hold["state"] for hold in store.holds()])

    def test_resume_is_asked_on_the_users_terminal(self):
        import observer_runtime
        observer_runtime.approve(REPO, {"observer": {"enabled": True, "model": "haiku",
                                                     "publisher_id": observer_runtime.publisher_id(create=True)}},
                                 str(self.project))
        self.assertEqual(0, self.cli("resume", "publication")[0])  # nothing paused: nothing to ask
        self.assertEqual(0, self.cli("pause", "publication")[0])
        for answer, code in ((None, "terminal-required"), ("no", "not-confirmed")):
            result, value, _ = self.cli("resume", "all", answer=answer)
            self.assertEqual((1, code), (result, value["code"]), answer)
            with Store(REPO) as store:
                self.assertTrue(store.get_control("publication_paused"))
        self.assertEqual(0, self.cli("resume", "all", answer="resume")[0])
        with Store(REPO) as store:
            self.assertFalse(store.get_control("publication_paused"))

    def test_resume_and_automatic_mode_are_denied_from_the_session_while_a_hold_applies(self):
        self.append("/recortex hold git-push")
        self.run_hook()
        for command in ("python3 skills/recortex/scripts/observer.py resume all",
                        "python3 observer.py enable --mode automatic --model haiku --yes",
                        "python3 observer.py enable --mode=automatic --model haiku --yes"):
            result = self.run_hook(command=command)
            self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"], command)
            self.assertIn("in a separate terminal", result["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertNotIn("hookSpecificOutput", self.run_hook(command="python3 observer.py status"))

    def test_disable_checks_the_entrys_holds_whatever_the_project_file_names(self):
        import contextlib, io, observer, observer_runtime
        self.entry_and_hold()
        out = io.StringIO()
        with mock.patch.object(observer.recortex, "load_config", return_value={"repo": "octo-org/other", "hub": 1}), \
                mock.patch.object(observer, "terminal", return_value=None), contextlib.redirect_stdout(out):
            self.assertEqual(1, observer.main(["disable"]))
        self.assertEqual("terminal-required", json.loads(out.getvalue())["code"])
        self.assertIsNotNone(observer_runtime.stored_approval(str(self.project)))

    def test_unreadable_approvals_stop_what_a_hold_could_stop(self):
        import contextlib, io, observer, observer_runtime
        self.entry_and_hold()
        path = Path(observer_runtime.approvals_folder()) / observer_runtime.APPROVALS
        path.write_text("not JSON")
        for command, denied in (("git push", True), ("python other.py", True), ("ls", False)):
            data = dict(self.data, tool_name="Bash", tool_input={"command": command})
            result = hooks.handle("pre-tool", data, {"repo": REPO, "hub": 1}, REPO)
            self.assertEqual(denied, "hookSpecificOutput" in result, command)
            out = io.StringIO()
            with mock.patch.object(observer.recortex, "load_config", return_value={"repo": REPO, "hub": 1}), \
                    mock.patch.object(observer.sys, "stdin", io.StringIO(json.dumps(data))), contextlib.redirect_stdout(out):
                observer.hook("pre-tool")
            self.assertEqual(denied, "could not be checked" in out.getvalue(), command)

    def test_disable_from_the_session_keeps_the_holds(self):
        import contextlib, io, observer, observer_runtime
        self.append("/recortex hold git-push")
        self.run_hook()
        observer_runtime.approve(REPO, {"observer": {"enabled": True, "model": "haiku",
                                                     "publisher_id": observer_runtime.publisher_id(create=True)}},
                                 str(self.project))
        with mock.patch.object(observer.recortex, "load_config", return_value={"repo": REPO, "hub": 1}), \
                mock.patch.object(observer, "terminal", return_value=None), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(1, observer.main(["disable"]))
        self.assertIsNotNone(observer_runtime.stored_approval(str(self.project)))

    def test_plain_phrase_holds_only_this_session(self):
        self.append("Don't push.")
        self.assertEqual("deny", self.run_hook()["hookSpecificOutput"]["permissionDecision"])
        later = self.path.with_name("session2.jsonl")
        later.write_text("")
        later.chmod(0o600)
        data = dict(self.data, session_id="session2", transcript_path=str(later), tool_name="Bash",
                    tool_input={"command": "git push"})
        self.assertNotIn("hookSpecificOutput", hooks.handle("pre-tool", data, self.config, REPO))

    def release(self, hold_id):
        """What observer.py release does once the user confirmed in a terminal of their own."""
        import observer_holds
        with Store(REPO) as store:
            store.update_holds(lambda holds: observer_holds.release(holds, hold_id))

    def test_typed_recortex_command_in_its_stored_form_holds_for_the_repository(self):
        stored = ("<command-message>recortex</command-message>\n<command-name>/recortex</command-name>\n"
                  "<command-args>%s</command-args>")
        self.append(stored % "hold git-push")
        self.assertEqual("deny", self.run_hook()["hookSpecificOutput"]["permissionDecision"])
        later = self.path.with_name("session2.jsonl")
        later.write_text("")
        later.chmod(0o600)
        data = dict(self.data, session_id="session2", transcript_path=str(later), tool_name="Bash",
                    tool_input={"command": "git push"})
        self.assertEqual("deny", hooks.handle("pre-tool", data, self.config, REPO)["hookSpecificOutput"]["permissionDecision"])

    def test_nothing_in_a_session_releases_a_hold(self):
        import contextlib, io, observer
        stored = ("<command-message>recortex</command-message>\n<command-name>/recortex</command-name>\n"
                  "<command-args>%s</command-args>")
        self.append("/recortex hold git-push")
        self.run_hook()
        with Store(REPO) as store:
            hold_id = store.holds()[0]["id"]
        typed = "/recortex release " + hold_id
        # The wrapper a model's SlashCommand call can produce, and plain transcript text, release nothing.
        for text in (stored % ("release " + hold_id), typed):
            self.append(text)
            result = self.run_hook()
            self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"], text)
            if "command-name" in text:  # the session is told how, once; the same set of lines is not repeated
                self.assertIn("run observer.py release HOLD_ID in a separate terminal", result["systemMessage"])
        # Neither does a hook's own prompt field, which a model can craft by running the hook itself.
        hooks.handle("prompt-submit", dict(self.data, prompt=typed), self.config, REPO)
        with mock.patch.object(observer.sys, "stdin", io.StringIO(json.dumps(dict(self.data, prompt=typed)))), \
                mock.patch.object(observer.recortex, "allowed_users", return_value={"octocat"}), \
                contextlib.redirect_stdout(io.StringIO()):
            observer.hook("prompt-submit", config=self.config, repo=REPO)
        self.assertEqual("deny", self.run_hook()["hookSpecificOutput"]["permissionDecision"])
        with Store(REPO) as store:
            self.assertEqual(["active"], [hold["state"] for hold in store.holds()])

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
        self.release(hold_id)
        self.assertNotIn("hookSpecificOutput", self.run_hook())
        with Store(REPO) as store:
            self.assertEqual(("released", "terminal"), (store.holds()[0]["state"], store.holds()[0]["released_by"]))

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
            self.assertEqual({}, self.run_hook())  # a tool call stays silent
            result = self.run_hook(event="prompt-submit")
            self.assertIn("capture is incomplete", result["systemMessage"])
            self.assertNotIn("SECRET", json.dumps(result))

    def test_unreadable_holds_stop_all_but_the_plainest_reads(self):
        self.append("/recortex hold command python publish.py")
        self.run_hook()
        with Store(REPO) as store:
            path = Path(store.path)
        path.write_bytes(b"not a database" * 100)  # a damaged queue: neither read nor opened
        for command in ("git push", "git tag v1", "gh release create v1", "git -C ../other push", "python publish.py",
                        "git status", "python other.py"):
            result = self.run_hook(command=command)
            self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"], command)
            self.assertIn("could not be checked", result["hookSpecificOutput"]["permissionDecisionReason"])
        for command in ("ls", "pwd", "cat README.md"):
            self.assertNotIn("hookSpecificOutput", self.run_hook(command=command), command)
        self.assertNotIn("hookSpecificOutput", hooks.handle("pre-tool", dict(self.data, tool_name="Read",
                                                                            tool_input={"file_path": "x"}), self.config, REPO))

    def test_unknown_held_commands_stop_everything_but_reading(self):
        with mock.patch("observer_hooks.observer_store.read_holds", side_effect=StoreError("unreadable")), \
                mock.patch("observer_hooks.observer_store.Store", side_effect=StoreError("corrupt")), \
                mock.patch("observer_hooks.observer_store.held_commands", side_effect=StoreError("unreadable")):
            for command in ("python other.py", "cat a > b", "git log --output=x", "ls; rm -r x", "make release",
                            "rg --pre x quote", "git diff --ext-diff", "ls -la", "cat a; git push", "ls $(echo x)",
                            "git status", "grep -n quote reader.py", "cat 'a b'", "pwd -P", "ls ~"):
                self.assertEqual("deny", self.run_hook(command=command)["hookSpecificOutput"]["permissionDecision"], command)
            for command in ("ls", "pwd", "cat README.md", "ls docs/notes", "cat notes.txt"):
                self.assertNotIn("hookSpecificOutput", self.run_hook(command=command), command)
        # With the holds readable, the ordinary rules apply: no hold, no decision.
        for command in ("rg --pre x quote", "git diff --ext-diff", "ls -la"):
            self.assertNotIn("hookSpecificOutput", self.run_hook(command=command), command)

    def test_a_missing_side_file_is_unknown_not_empty(self):
        self.append("/recortex hold command python publish.py")
        self.run_hook()
        with Store(REPO) as store:
            folder, path = Path(store.folder), Path(store.path)
        path.write_bytes(b"not a database" * 100)
        os.unlink(folder / "command-holds.json")
        for command in ("python publish.py", "python other.py"):
            self.assertEqual("deny", self.run_hook(command=command)["hookSpecificOutput"]["permissionDecision"], command)
        self.assertNotIn("hookSpecificOutput", self.run_hook(command="ls"))

    def test_a_failed_side_file_write_never_leaves_fewer_held_commands_than_the_queue(self):
        import observer_holds
        import observer_store
        self.append("/recortex hold command python publish.py")
        self.run_hook()
        with Store(REPO) as store:
            folder, path = Path(store.folder), Path(store.path)
            hold_id = store.holds()[0]["id"]
        side = folder / "command-holds.json"
        before = side.read_text()
        real = observer_store.storage.write_file
        # The write before the commit fails: the release is not made, and the file is as it was.
        with mock.patch.object(observer_store.storage, "write_file", side_effect=observer_store.storage.StorageError("x")):
            with Store(REPO) as store, self.assertRaises(observer_store.storage.StorageError):
                store.update_holds(lambda holds: observer_holds.release(holds, hold_id))
        with Store(REPO) as store:
            self.assertEqual("active", store.holds()[0]["state"])
        self.assertEqual(before, side.read_text())
        # The write after the commit fails: the hold is released, and the file keeps the superset, so that with the
        # queue unreadable the command is stopped rather than allowed.
        calls = []
        def second_fails(target, text):
            calls.append(target)
            if len(calls) == 2:
                raise observer_store.storage.StorageError("x")
            return real(target, text)
        with mock.patch.object(observer_store.storage, "write_file", side_effect=second_fails):
            with Store(REPO) as store:
                store.update_holds(lambda holds: observer_holds.release(holds, hold_id))
        with Store(REPO) as store:
            self.assertEqual("released", store.holds()[0]["state"])
        self.assertEqual(before, side.read_text())
        path.write_bytes(b"not a database" * 100)
        self.assertEqual("deny", self.run_hook(command="python publish.py")["hookSpecificOutput"]["permissionDecision"])

    def test_a_busy_queue_does_not_keep_a_hold_from_being_checked(self):
        import fcntl
        import sqlite3
        self.append("/recortex hold git-push")
        self.run_hook()
        with Store(REPO) as store:
            folder, path = store.folder, store.path
        holder = os.open(os.path.join(folder, "initialization.lock"), os.O_RDWR)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        writer = sqlite3.connect(path, isolation_level=None)
        self.addCleanup(writer.close)
        writer.execute("BEGIN IMMEDIATE")  # another process in the middle of a write
        result = self.run_hook(command="git push")
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        self.assertNotIn("could not be checked", result["hookSpecificOutput"]["permissionDecisionReason"])
        writer.execute("ROLLBACK")

    def test_an_observer_that_is_off_gives_no_decision(self):
        self.append("/recortex hold git-push")
        self.run_hook()
        with mock.patch("observer_hooks.observer_store.read_holds", side_effect=StoreError("unreadable")):
            self.assertEqual({}, hooks.handle("pre-tool", dict(self.data, tool_name="Bash",
                                                              tool_input={"command": "git push"}),
                                              dict(self.config, observer={"enabled": False}), REPO))

    def test_daily_cap_has_its_own_line(self):
        with Store(REPO) as store:
            store.set_control("worker_degraded", "publish_daily_cap")
        result = self.run_hook(event="stop")
        self.assertIn("limit of automatic comments", result["systemMessage"])
        self.assertNotIn("not yet verified", result["systemMessage"])

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
        self.release(hold_id)
        self.assertNotIn("hookSpecificOutput", self.run_hook())

    def test_the_denial_says_the_user_asked_and_invites_no_release(self):
        # The first live test's denial ("release that hold explicitly before this action") led the session to try a
        # release. The denial now says only that the user has asked that this action not be taken.
        self.append("/recortex hold git-push")
        reason = self.run_hook()["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual("The user has asked that this action not be taken.", reason)
        self.assertNotIn("release", reason.lower())

    def test_a_hold_typed_while_capture_is_paused_takes_effect(self):
        # While capture is paused the transcript is still scanned for holds, under a cursor of its own
        # ("controls_" and the capture stream's name, which observer_store.SOURCE_ID accepts).
        self.run_hook(event="stop")
        with Store(REPO) as store:
            store.set_control("capture_paused", True)
        self.append("/recortex hold git-push")
        result = self.run_hook()
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        self.assertIn("explicit hold was recorded", result["systemMessage"])
        with Store(REPO) as store:
            self.assertEqual(["git-push"], [hold["action"] for hold in store.holds()])
            self.assertEqual([], [row for row in store.pending() if row["metadata"]["offset"] > 0])  # nothing captured
            self.assertIsNotNone(store.cursor("controls_" + __import__("hashlib").sha256(str(self.path).encode()).hexdigest()))

    def test_exact_control_containing_restricted_text_is_refused_before_storage(self):
        self.config["restricted_patterns"] = ["PRIVATE-SAMPLE"]
        self.append("/recortex hold command publish PRIVATE-SAMPLE")
        result = self.run_hook(command="publish PRIVATE-SAMPLE")
        self.assertIn("control could not be stored safely", result["systemMessage"])
        with Store(REPO) as store:
            self.assertEqual([], store.holds())
            self.assertNotIn(b"PRIVATE-SAMPLE", Path(store.path).read_bytes())

    def test_an_unchanged_set_of_notices_shows_once_per_session_stop_included(self):
        first, second = self.run_hook(event="stop"), self.run_hook(event="stop")
        self.assertIn("systemMessage", first)
        self.assertNotIn("systemMessage", second)
        self.assertNotIn("systemMessage", self.run_hook())
        with Store(REPO) as store:
            store.set_control("worker_degraded", "runtime_gate")  # a changed set shows again
        self.assertIn("not yet verified", self.run_hook(event="stop")["systemMessage"])
        with Store(REPO) as store:
            store.set_control("capture_paused", True)  # a new line, which Claude Code would discard at compaction
        self.assertEqual({}, self.run_hook(event="pre-compact"))

    def test_each_notice_shows_once_per_session_across_turns(self):
        lines = []
        for turn in range(5):
            self.append("Turn %d result." % turn, role="assistant")
            for event in ("prompt-submit", "pre-tool", "post-tool", "stop"):
                lines += self.run_hook(event=event).get("systemMessage", "").splitlines()
        self.assertEqual(1, sum("independent checks are unavailable" in line for line in lines))
        self.assertEqual(1, sum("not yet verified in GitHub" in line for line in lines))

    def test_a_failing_queue_is_silent_on_tool_calls_and_shown_at_the_turns_edge(self):
        import fcntl
        import time as clock
        with Store(REPO) as store:
            folder = store.folder
        holder = os.open(os.path.join(folder, "initialization.lock"), os.O_RDWR)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = clock.monotonic()
        result = self.run_hook(event="pre-tool")
        self.assertLess(clock.monotonic() - started, 0.5)
        self.assertNotIn("systemMessage", result)
        self.assertIn("capture is incomplete", self.run_hook(event="stop")["systemMessage"])

    def test_the_worker_starts_from_the_edges_of_a_turn_only(self):
        with mock.patch("observer_hooks.observer_runtime.gate_valid", return_value=True):
            for event in ("pre-tool", "post-tool", "tool-failure"):
                self.run_hook(event=event)
            self.worker.assert_not_called()
            self.run_hook(event="stop")
            self.worker.assert_called_once_with(str(self.project))

    def entry(self, event, text, handle=None):
        import contextlib, io, observer
        out = io.StringIO()
        with mock.patch.object(observer.sys, "stdin", io.StringIO(text)), contextlib.redirect_stdout(out), \
                mock.patch.object(observer.recortex, "allowed_users", return_value={"octocat"}), \
                mock.patch("observer_hooks.handle", side_effect=handle or (lambda *args: {})) as called:
            self.assertEqual(0, observer.hook(event, config=self.config, repo=REPO))
        return out.getvalue(), called

    def test_hook_entry_bounds_its_input(self):
        large = json.dumps({"tool_input": {"content": "x" * 300000}})
        output, called = self.entry("post-tool", large)
        self.assertEqual("", output)
        called.assert_not_called()  # a large tool result: the next hook scans its transcript lines
        output, called = self.entry("pre-tool", large)
        self.assertEqual("", output)
        called.assert_called_once()  # before an action the input is read, holds are checked
        huge = "{}" + " " * 9000000
        for event, line in (("stop", True), ("pre-tool", False)):
            output, called = self.entry(event, huge)
            called.assert_not_called()
            self.assertEqual(line, "hook deadline or local state failure" in output, event)

    def test_hook_entry_ends_at_its_deadline(self):
        import time as clock
        started = clock.monotonic()
        output, _ = self.entry("stop", "{}", handle=lambda *args: clock.sleep(10))
        self.assertLess(clock.monotonic() - started, 4)
        self.assertEqual({"systemMessage": "recortex observer unavailable: hook deadline or local state failure; "
                                           "run observer.py status."}, json.loads(output))

    def test_stop_and_compaction_never_carry_context(self):
        class Empty:
            def execute(self, *args):
                return []

            def close(self):
                pass
        self._context.stop()
        packet = {"text": "CONTEXT", "selected": [1], "resume_id": None}
        with mock.patch("observer_hooks.index.connect", return_value=Empty()), \
                mock.patch("observer_hooks.observer_context.build_packet", return_value=packet):
            for event in ("stop", "subagent-stop", "pre-compact"):
                self.assertNotIn("hookSpecificOutput", self.run_hook(event=event), event)
            prompt = hooks.handle("prompt-submit", dict(self.data, prompt="the parser"), self.config, REPO)
            self.assertIn("CONTEXT", prompt["hookSpecificOutput"]["additionalContext"])
        for event in ("Stop", "SubagentStop", "PreCompact"):  # the second filter, on whatever context reaches it
            self.assertNotIn("hookSpecificOutput", hooks._output(event, context="CONTEXT"), event)

    def test_tool_calls_get_context_only_before_a_holdable_action_and_once_per_selection(self):
        class Empty:
            def execute(self, *args):
                return []

            def close(self):
                pass
        self._context.stop()
        packets = iter([{"text": "CONTEXT synced %d" % n, "selected": [1], "resume_id": None} for n in range(40)])
        with mock.patch("observer_hooks.index.connect", return_value=Empty()), \
                mock.patch("observer_hooks.observer_context.build_packet", side_effect=lambda *a, **k: next(packets)):
            for n in range(20):
                tool, given = ("Read", {"file_path": "reader%d.py" % n}) if n % 2 else ("Grep", {"pattern": "quote%d" % n})
                data = dict(self.data, tool_name=tool, tool_input=given)
                self.assertNotIn("hookSpecificOutput", hooks.handle("pre-tool", data, self.config, REPO))
            first = self.run_hook(command="git push")
            self.assertIn("CONTEXT", first["hookSpecificOutput"]["additionalContext"])
            again = self.run_hook(command="git push")  # a newer sync time, the same selection
            self.assertNotIn("hookSpecificOutput", again)

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
            prompt = hooks.handle("prompt-submit", dict(self.data, prompt="the parser"), self.config, REPO)
        self.worker.assert_not_called()
        self.assertNotIn("hookSpecificOutput", result)
        self.assertNotIn("systemMessage", result)
        self.assertIn("capture is incomplete", prompt["systemMessage"])

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


SCRIPT = ROOT / "skills" / "recortex" / "scripts" / "run_observer_hook.sh"
EVENT_NAMES = ("session-start", "subagent-start", "prompt-submit", "pre-compact", "pre-tool", "post-tool",
               "tool-failure", "stop", "subagent-stop")


class FastPathTests(unittest.TestCase):
    """run_observer_hook.sh starts no Python unless this machine enabled the observer for this very folder."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="observer-fast-", dir="/tmp")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.shim = self.base / "shim"
        self.shim.mkdir()
        self.marker = self.base / "python-started"
        (self.shim / "python3").write_text("#!/bin/sh\ntouch '%s'\ncat >/dev/null\nexit 0\n" % self.marker)
        (self.shim / "python3").chmod(0o755)
        self.state = self.base / "state"
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith((names.ENV_PREFIX, names.OLD_ENV_PREFIX, "CLAUDE_", "XDG_"))}
        self.env.update(PATH=str(self.shim) + os.pathsep + os.environ["PATH"], HOME=str(self.base / "home"),
                        XDG_STATE_HOME=str(self.state))

    def run_all(self, project):
        env = dict(self.env, CLAUDE_PROJECT_DIR=str(project))
        for event in EVENT_NAMES:
            done = subprocess.run(["bash", str(SCRIPT), event], input="{}", capture_output=True, text=True, env=env)
            self.assertEqual((0, ""), (done.returncode, done.stdout), (project, event))

    def test_no_python_starts_without_this_folders_entry(self):
        bare = self.base / "bare"
        configured = self.base / "configured"
        for folder in (bare, configured):
            folder.mkdir()
        # A shared project file that names the observer cannot start it on this machine.
        (configured / ".recortex.json").write_text(json.dumps({"repo": REPO, "observer": {"enabled": True}}))
        approvals = self.state / "recortex" / "private" / "observer-approvals.json"
        for case in ("no state", "another folder"):
            if case == "another folder":
                approvals.parent.mkdir(parents=True)
                elsewhere = str(self.base / "elsewhere")
                approvals.write_text(json.dumps({"form": 1, "folders": {elsewhere: {"root": elsewhere}}}))
            for project in (bare, configured):
                self.run_all(project)
                self.assertFalse(self.marker.exists(), (case, project))
        # A symlinked path to an enabled folder is found by its real path: Python decides.
        link = self.base / "link"
        link.symlink_to(configured)
        approvals.write_text(json.dumps({"form": 1, "folders": {os.path.realpath(configured): {}}}, indent=1))
        self.run_all(link)
        self.assertTrue(self.marker.exists())

    def test_every_event_on_an_enabled_folder_ends_cleanly_with_hook_json(self):
        import observer_runtime
        project = self.base / "enabled"
        project.mkdir(mode=0o700)
        (project / ".recortex.json").write_text(json.dumps({"repo": REPO, "hub": 1}))
        trusted = self.base / "trusted"
        trusted.write_text(REPO + " octocat\n")
        env = dict(self.env, PATH=os.environ["PATH"], CLAUDE_PROJECT_DIR=str(project), RECORTEX_TRUSTED_FILE=str(trusted),
                   XDG_CACHE_HOME=str(self.base / "cache"), XDG_CONFIG_HOME=str(self.base / "config"),
                   CLAUDE_CONFIG_DIR=str(self.base / "claude"))
        with mock.patch.dict(os.environ, env, clear=True):
            settings = {"enabled": True, "model": "sonnet", "publisher_id": observer_runtime.publisher_id(create=True)}
            observer_runtime.approve(REPO, {"observer": settings}, str(project))
        transcript = self.base / "claude" / "session1.jsonl"
        transcript.parent.mkdir(mode=0o700)
        transcript.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "Ordinary work."}}) + "\n")
        data = {"session_id": "session1", "transcript_path": str(transcript), "cwd": str(project), "prompt": "x",
                "tool_name": "Bash", "tool_input": {"command": "ls"}, "agent_transcript_path": str(transcript)}
        for event in EVENT_NAMES:
            done = subprocess.run(["bash", str(SCRIPT), event], input=json.dumps(data), capture_output=True,
                                  text=True, env=env, timeout=10)
            self.assertEqual(0, done.returncode, event)
            if done.stdout:
                out = json.loads(done.stdout)
                self.assertTrue(set(out) <= {"systemMessage", "hookSpecificOutput"}, (event, out))
                if event in ("stop", "subagent-stop", "pre-compact"):
                    self.assertNotIn("hookSpecificOutput", out, event)

    def test_without_a_place_for_the_state_python_decides(self):
        project = self.base / "project"
        project.mkdir()
        env = dict(self.env, CLAUDE_PROJECT_DIR=str(project), HOME="relative", XDG_STATE_HOME="")
        subprocess.run(["bash", str(SCRIPT), "stop"], input="{}", capture_output=True, text=True, env=env)
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
