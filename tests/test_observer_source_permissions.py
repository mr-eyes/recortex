"""Source authority and read-only capture diagnosis; no personal source reads."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/recortex/scripts"))
import observer_sources as sources
from tests.observer_source_fixture import fixture_trusted_ancestors


def changed_stat(info, **changes):
    values = {key: getattr(info, key) for key in ("st_mode", "st_uid", "st_gid", "st_dev", "st_ino")}
    values.update(changes)
    return SimpleNamespace(**values)


class SourcePermissionTests(unittest.TestCase):
    def setUp(self):
        # Native-source fixtures need private ancestors; a checkout may be 0775.
        self.temporary = tempfile.TemporaryDirectory(prefix="recortex-source-check-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        metadata = fixture_trusted_ancestors(self.base)
        metadata.__enter__()
        self.addCleanup(metadata.__exit__, None, None, None)
        self.project = self.base / "project"
        self.project.mkdir(mode=0o700)
        self.native = self.base / "native"
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.native)})
        env.start()
        self.addCleanup(env.stop)
        self.folder = Path(sources.project_directory(str(self.project)))
        self.folder.mkdir(parents=True)
        for directory in (self.folder, self.folder.parent, self.native):
            directory.chmod(0o700)
        self.path = self.folder / "session-1.jsonl"
        self.path.write_text('{"message":{"role":"user","content":"Choose blue."}}\n')
        self.path.chmod(0o600)

    def altered_owner(self, path, mode, owner):
        original = os.lstat

        def inspect(candidate, *args, **kwargs):
            info = original(candidate, *args, **kwargs)
            return changed_stat(info, st_uid=owner, st_mode=stat.S_IFDIR | mode) if os.fspath(candidate) == str(path) else info

        return mock.patch.object(sources.os, "lstat", side_effect=inspect)

    def assert_owner_refused(self, path, mode):
        foreign = max(os.getuid(), 1) + 10000
        with self.altered_owner(path, mode, foreign):
            result = sources.preflight(str(self.project))
            self.assertFalse(result["ready"])
            self.assertEqual("source-owner", result["code"])
            self.assertEqual("ancestor", result["boundary"])
            with self.assertRaisesRegex(sources.SourceError, "source-owner"):
                sources.approved_path(str(self.path), str(self.project), "session-1")

    def test_foreign_owned_nonwritable_ancestor_is_not_authority(self):
        self.assert_owner_refused(self.base, 0o755)

    def test_sticky_bit_does_not_make_foreign_owner_trusted(self):
        self.assert_owner_refused(self.base, 0o1777)

    def test_overflow_owner_is_not_assumed_root_outside_the_fixture_model(self):
        info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o1777, st_uid=65534)
        self.assertEqual("source-owner", sources._component_problem("/tmp", info, "/unrelated/native/project"))

    def test_root_owned_sticky_shared_ancestor_remains_usable(self):
        with self.altered_owner(self.base, 0o1777, 0):
            self.assertTrue(sources.preflight(str(self.project))["ready"])
            self.assertEqual(str(self.path), sources.approved_path(str(self.path), str(self.project), "session-1"))

    @unittest.skipIf(os.getuid() == 0, "current user is root")
    def test_root_owned_project_is_not_the_current_users_source_tree(self):
        with self.altered_owner(self.folder, 0o755, 0):
            self.assertEqual("source-owner", sources.preflight(str(self.project))["code"])
            with self.assertRaisesRegex(sources.SourceError, "source-owner"):
                sources.approved_path(str(self.path), str(self.project), "session-1")

    def test_user_owned_sticky_shared_ancestor_remains_usable(self):
        with self.altered_owner(self.base, 0o1777, os.getuid()):
            self.assertTrue(sources.preflight(str(self.project))["ready"])
            self.assertEqual(str(self.path), sources.approved_path(str(self.path), str(self.project), "session-1"))

    def test_sticky_mode_does_not_allow_writable_native_project_directory(self):
        with self.altered_owner(self.folder, 0o1777, os.getuid()):
            self.assertEqual("source-permissions", sources.preflight(str(self.project))["code"])
            with self.assertRaisesRegex(sources.SourceError, "source-permissions"):
                sources.approved_path(str(self.path), str(self.project), "session-1")

    def test_details_identify_all_failed_boundaries_without_changing_modes(self):
        self.native.chmod(0o775)
        self.folder.parent.chmod(0o775)
        result = sources.preflight(str(self.project))
        self.assertEqual("source-permissions", result["code"])
        self.assertEqual("native-projects", result["boundary"])
        self.assertNotIn(str(self.base), json.dumps(result))
        detailed = sources.preflight(str(self.project), details=True)
        self.assertEqual({str(self.native), str(self.folder.parent)}, {issue["path"] for issue in detailed["issues"]})
        self.assertTrue(all(issue["mode"] == "0775" and issue["owner"] == "current-user" for issue in detailed["issues"]))
        for directory in (self.native, self.folder.parent):
            self.assertEqual(0o775, stat.S_IMODE(directory.stat().st_mode))

    def test_opened_foreign_leaf_is_refused_before_any_bytes_are_read(self):
        actual = os.fstat

        def swapped(descriptor):
            return changed_stat(actual(descriptor), st_uid=max(os.getuid(), 1) + 10000)

        with mock.patch.object(sources.os, "fstat", side_effect=swapped), mock.patch.object(sources.os, "read") as read:
            with self.assertRaisesRegex(sources.SourceError, "source-owner"):
                sources._read(str(self.path), 0, 16)
            read.assert_not_called()

    def test_opened_writable_leaf_is_refused_before_any_bytes_are_read(self):
        actual = os.fstat

        def writable(descriptor):
            return changed_stat(actual(descriptor), st_mode=stat.S_IFREG | 0o666)

        with mock.patch.object(sources.os, "fstat", side_effect=writable), mock.patch.object(sources.os, "read") as read:
            with self.assertRaisesRegex(sources.SourceError, "source-permissions"):
                sources._read(str(self.path), 0, 16)
            read.assert_not_called()

    def test_cli_source_details_are_explicit_read_only_and_content_free(self):
        config = self.project / ".recortex.json"
        config.write_text(json.dumps({"repo": "octo-org/octo-repo"}))
        config.chmod(0o600)
        trusted = self.base / "trusted"
        trusted.write_text("octo-org/octo-repo octocat\n")
        trusted.chmod(0o600)
        self.native.chmod(0o775)
        environment = {"PATH": os.environ.get("PATH", os.defpath), "CLAUDE_PROJECT_DIR": str(self.project),
                       "CLAUDE_CONFIG_DIR": str(self.native), "RECORTEX_TRUSTED_FILE": str(trusted),
                       "XDG_STATE_HOME": str(self.base / "state"), "XDG_CACHE_HOME": str(self.base / "cache"),
                       "XDG_CONFIG_HOME": str(self.base / "config")}
        outputs = []
        for flags in ([], ["--source-details"]):
            process = subprocess.run([sys.executable, str(ROOT / "skills/recortex/scripts/observer.py"), "status"] + flags,
                                     cwd=self.project, env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(0, process.returncode, process.stderr)
            self.assertNotIn("Choose blue", process.stdout)
            outputs.append(json.loads(process.stdout)["capture_preflight"])
        self.assertNotIn(str(self.base), json.dumps(outputs[0]))
        self.assertEqual(str(self.native), outputs[1]["path"])
        self.assertEqual("review-owned-permissions", outputs[1]["action"])
        self.assertFalse((self.base / "state").exists())
        self.assertEqual(0o775, stat.S_IMODE(self.native.stat().st_mode))


if __name__ == "__main__":
    unittest.main()
