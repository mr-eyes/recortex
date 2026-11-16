"""Main-session posting shares observer identities, reviewed drafts and receipts."""

import contextlib
import io
import json
import os
import shutil
import unittest
from unittest import mock

from tests import test_observer_publisher as publisher_fixtures
from tests import test_observer_store as fixtures

import observer_store as store
import post_verified
import create_issues


class ObserverPostingTests(unittest.TestCase):
    metadata = publisher_fixtures.ObserverPublisherTests.metadata
    setUp = publisher_fixtures.ObserverPublisherTests.setUp
    state = publisher_fixtures.ObserverPublisherTests.state
    calls = publisher_fixtures.ObserverPublisherTests.calls

    def post(self, eventkey=True, issue=3, kind="idea", text=None, *extra):
        if text is None:
            with store.Store(fixtures.REPO, config=self.config, readonly=True) as db:
                text = db.event(self.key)["body"]
        path = self.base / "reviewed.md"
        path.write_text(text)
        args = ["comment", "--repo", fixtures.REPO, "--issue", str(issue), "--kind", kind,
                "--body-file", str(path)]
        if eventkey:
            args.extend(["--eventkey", self.key])
        args.extend(extra)
        out = io.StringIO()
        with mock.patch.object(post_verified.recortex, "load_config", return_value=self.config), contextlib.redirect_stdout(out):
            result = post_verified.main(args)
        return result, out.getvalue()

    def test_shared_event_posts_once_and_main_session_retry_uses_receipt(self):
        result, output = self.post()
        self.assertEqual(0, result, output)
        self.assertIn("memory id: 9100", output)
        self.assertEqual(0, self.post()[0])
        self.assertEqual(1, len([args for args in self.calls() if args[:2] == ["issue", "comment"]]))
        with store.Store(fixtures.REPO, config=self.config) as db:
            self.assertEqual("verified", db.event(self.key)["state"])
            self.assertEqual([], db.pending())

    def test_approved_memory_cannot_bypass_queue_by_omitting_key_or_kind(self):
        for kind in ("idea", "generic"):
            with self.subTest(kind=kind):
                result, output = self.post(eventkey=False, kind=kind)
                self.assertEqual(1, result, output)
                self.assertIn("require --eventkey", output)
                self.assertEqual([], self.calls())

    def test_exact_reviewed_body_target_and_kind_are_required_before_network(self):
        for changes in ({"issue": 2}, {"kind": "decision"}, {"text": "Altered private draft"}):
            with self.subTest(changes=changes):
                result, output = self.post(**changes)
                self.assertEqual(1, result, output)
                self.assertIn("exact reviewed draft", output)
                self.assertEqual([], self.calls())

    def test_pre_marker_draft_matches_same_frozen_event(self):
        with store.Store(fixtures.REPO, config=self.config, readonly=True) as db:
            draft = db.event(self.key)["candidate"]["body"]
        result, output = self.post(text=draft)
        self.assertEqual(0, result, output)
        remote = self.state()["comments"]["9100"]["body"]
        self.assertIn("eventkey=" + self.key, remote)

    def test_offline_preview_has_no_network_or_queue_write(self):
        before, stamp = self.db_path.read_bytes(), self.db_path.stat().st_mtime_ns
        result, output = self.post(True, 3, "idea", None, "--dry-run", "--offline")
        self.assertEqual(0, result, output)
        self.assertIn("would run: gh issue comment 3", output)
        self.assertEqual([], self.calls())
        self.assertEqual(before, self.db_path.read_bytes())
        self.assertEqual(stamp, self.db_path.stat().st_mtime_ns)

    def test_uncertain_main_session_request_is_reconciled_without_another_post(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL_AFTER": "issue comment"}):
            result, output = self.post()
        self.assertEqual(1, result, output)
        self.assertIn("remains uncertain", output)
        self.assertEqual(0, self.post()[0])
        self.assertEqual(1, len([args for args in self.calls() if args[:2] == ["issue", "comment"]]))

    def test_missing_local_approval_preserves_legacy_comment_workflow(self):
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.set_control("observer_approval", None)
            draft = db.event(self.key)["candidate"]["body"]
        result, output = self.post(eventkey=False, text=draft)
        self.assertEqual(0, result, output)
        self.assertEqual(1, len([args for args in self.calls() if args[:2] == ["issue", "comment"]]))

    def test_shadow_approval_does_not_allow_either_publication_path(self):
        self.config["observer"]["mode"] = "shadow"
        import observer_runtime
        with store.Store(fixtures.REPO, config=self.config) as db:
            approval = db.get_control("observer_approval")
            approval["settings"] = observer_runtime.settings(self.config)
            db.set_control("observer_approval", approval)
        self.assertEqual(1, self.post(eventkey=False)[0])
        result, output = self.post()
        self.assertEqual(1, result, output)
        self.assertIn("Shadow mode", output)
        self.assertEqual([], self.calls())

    def test_manifest_memory_refuses_every_write_before_creating_its_issues(self):
        drafts = self.base / "drafts"
        shutil.copytree(fixtures.ROOT / "examples/octo-repo", drafts)
        manifest = drafts / "manifest.json"
        before, stamp = self.db_path.read_bytes(), self.db_path.stat().st_mtime_ns
        for extra in ([], ["--dry-run"]):
            out = io.StringIO()
            with mock.patch.object(create_issues.recortex, "load_config", return_value=self.config), contextlib.redirect_stdout(out):
                result = create_issues.main(["--manifest", str(manifest)] + extra)
            self.assertEqual(1, result, out.getvalue())
            self.assertIn("shared reviewed queue", out.getvalue())
            self.assertEqual([], self.calls())
            self.assertFalse((drafts / "manifest.json.state.json").exists())
            self.assertEqual(before, self.db_path.read_bytes())
            self.assertEqual(stamp, self.db_path.stat().st_mtime_ns)

    def test_manifest_nonmemory_and_unapproved_projects_keep_legacy_preview(self):
        drafts = self.base / "drafts"
        shutil.copytree(fixtures.ROOT / "examples/octo-repo", drafts)
        manifest = drafts / "manifest.json"
        full = json.loads(manifest.read_text())
        cases = [(True, [comment for comment in full["comments"] if comment["kind"] == "resume"]),
                 (False, full["comments"])]
        for approved, comments in cases:
            manifest.write_text(json.dumps(dict(full, comments=comments)))
            if not approved:
                with store.Store(fixtures.REPO, config=self.config) as db:
                    db.set_control("observer_approval", None)
            out = io.StringIO()
            with mock.patch.object(create_issues.recortex, "load_config", return_value=self.config), contextlib.redirect_stdout(out):
                result = create_issues.main(["--manifest", str(manifest), "--dry-run"])
            self.assertEqual(0, result, out.getvalue())
            self.assertIn("would run: gh issue create", out.getvalue())
            self.assertEqual([], self.calls())


if __name__ == "__main__":
    unittest.main()
