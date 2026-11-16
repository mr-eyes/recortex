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

import observer_runtime
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

    def own_idea(self):
        return (fixtures.ROOT / "examples/octo-repo/chain/1-idea.md").read_text().replace("open quote", "quote state")

    def test_main_session_posts_its_own_memory_and_the_queue_registers_it(self):
        outputs = []
        for kind in ("idea", "generic"):
            with self.subTest(kind=kind):
                text = self.own_idea().replace("quote state", "quote state for " + kind)
                result, output = self.post(False, 3, kind, text)
                self.assertEqual(0, result, output)
                self.assertIn("observer: registered", output)
                outputs.append(output)
        self.assertEqual(2, len([args for args in self.calls() if args[:2] == ["issue", "comment"]]))
        with store.Store(fixtures.REPO, config=self.config) as db:
            posts = db.get_control("main_posts")
            self.assertEqual([(3, "idea"), (3, "idea")], [(post["issue"], post["kind"]) for post in posts])
            self.assertTrue(db.get_control("sync_needed"))
            # the observer's own idea for #3, queued before, now waits for the main session
            self.assertEqual(("deferred", "main_session"), (db.event(self.key)["state"], db.event(self.key)["reason"]))
        self.assertIn("event %s, a idea it had queued for #3, now waits for you" % self.key, outputs[0])
        self.assertNotIn("now waits for you", outputs[1])

    def test_a_decision_the_queue_already_holds_is_not_posted_twice(self):
        decision = (fixtures.ROOT / "examples/octo-repo/chain/3-decision.md").read_text()
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.capture([self.metadata("source9")])
            candidate = {"kind": "decision", "issue": 3, "body": decision, "sources": ["source9"]}
            ident = db.reserve_invocation("reviewer")
            db.complete_invocation(ident, True, output_hash=store.candidate_hash(candidate))
            held = db.review_complete("source9", candidate, {"reservation_id": ident, "accepted": True,
                                                              "candidate_hash": store.candidate_hash(candidate)})
        result, output = self.post(False, 3, "decision", decision.replace("2026-03", "2026-04"))
        self.assertEqual(1, result, output)
        self.assertIn("already holds this decision (event %s" % held, output)
        self.assertEqual([], self.calls())

    def test_the_main_session_posts_a_deferred_event_of_a_marked_project_past_the_daily_cap(self):
        # post_verified.py --eventkey is the main session's own post, after its review and the user's word
        # (references/observer.md): a deferred event, a marked project and a full day of automatic comments do not
        # stop it, and a refusal names its real reason.
        self.config["fields"] = {"sensitive": ["people"]}
        self.config["observer"]["max_posts"] = 1
        observer_runtime.approve(fixtures.REPO, self.config)
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.transition(self.key, "deferred", reason="sensitive_project")
            db.set_control("automatic_posts", [__import__("time").time()])
            db.set_control("publication_paused", True)
        result, output = self.post()
        self.assertEqual(1, result, output)
        self.assertIn("refused this event (publication_paused)", output)
        self.assertEqual([], self.calls())
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.set_control("publication_paused", False)
        result, output = self.post()
        self.assertEqual(0, result, output)
        self.assertIn("memory id: 9100", output)
        self.assertEqual(1, len([args for args in self.calls() if args[:2] == ["issue", "comment"]]))

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
        self.assertTrue(observer_runtime.forget())
        with store.Store(fixtures.REPO, config=self.config) as db:
            draft = db.event(self.key)["candidate"]["body"]
        result, output = self.post(eventkey=False, text=draft)
        self.assertEqual(0, result, output)
        self.assertEqual(1, len([args for args in self.calls() if args[:2] == ["issue", "comment"]]))

    def test_shadow_approval_keeps_the_main_sessions_own_path(self):
        self.config["observer"]["mode"] = "shadow"
        observer_runtime.approve(fixtures.REPO, self.config)
        result, output = self.post()
        self.assertEqual(1, result, output)
        self.assertIn("Shadow mode", output)
        self.assertEqual([], self.calls())
        result, output = self.post(False, 3, "idea", self.own_idea())
        self.assertEqual(0, result, output)
        self.assertIn("observer: registered", output)
        self.assertEqual(1, len([args for args in self.calls() if args[:2] == ["issue", "comment"]]))

    def test_manifest_memories_are_posted_and_registered(self):
        drafts = self.base / "drafts"
        shutil.copytree(fixtures.ROOT / "examples/octo-repo", drafts)
        manifest = drafts / "manifest.json"
        full = json.loads(manifest.read_text())
        memories = [comment for comment in full["comments"] if comment["kind"] not in ("resume", "generic")]
        out = io.StringIO()
        import setup_labels
        with mock.patch.object(create_issues.recortex, "load_config", return_value=dict(self.config, repo=fixtures.REPO)), \
                mock.patch.dict(os.environ, {"RECORTEX_LINK_PAUSE": "0"}), contextlib.redirect_stdout(out):
            self.assertEqual(0, setup_labels.main(["--repo", fixtures.REPO, "--profile", "software,research",
                                                   "--topic", "reader", "--topic", "speed"]))
            result = create_issues.main(["--manifest", str(manifest)])
        self.assertEqual(0, result, out.getvalue())
        with store.Store(fixtures.REPO, config=self.config) as db:
            self.assertEqual(len(memories), len(db.get_control("main_posts")))

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
                observer_runtime.forget()
            out = io.StringIO()
            with mock.patch.object(create_issues.recortex, "load_config", return_value=self.config), contextlib.redirect_stdout(out):
                result = create_issues.main(["--manifest", str(manifest), "--dry-run"])
            self.assertEqual(0, result, out.getvalue())
            self.assertIn("would run: gh issue create", out.getvalue())
            self.assertEqual([], self.calls())


if __name__ == "__main__":
    unittest.main()
