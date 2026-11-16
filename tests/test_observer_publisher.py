"""Append-only publication and ambiguous-request recovery, against fake GitHub."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import time
import unittest
from unittest import mock

from tests import test_observer_store as fixtures

import observer_publisher as publisher
import observer_runtime as runtime
import observer_store as store
import lint_drafts
import record_format


class ObserverPublisherTests(unittest.TestCase):
    metadata = fixtures.ObserverStoreTests.metadata

    def setUp(self):
        fixtures.ObserverStoreTests.setUp(self)
        self.remote = self.base / "remote.json"
        shutil.copyfile(fixtures.ROOT / "tests/fixtures/repo-state.json", self.remote)
        trusted = self.base / "trusted"
        trusted.write_text(fixtures.REPO + " octocat hubot\n")
        os.environ.update(RECORTEX_GH=shlex.join([sys.executable, str(fixtures.ROOT / "tests/fake_gh.py")]),
                          FAKE_GH_STATE=str(self.remote), RECORTEX_TRUSTED_FILE=str(trusted))
        self.config = {"observer": {"enabled": True, "mode": "automatic", "publisher_id": runtime.publisher_id(create=True)}}
        body = (fixtures.ROOT / "examples/octo-repo/chain/1-idea.md").read_text()
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.capture([self.metadata()])
            candidate = {"kind": "idea", "issue": 3, "body": body, "sources": ["source1"]}
            ident = db.reserve_invocation("reviewer")
            db.complete_invocation(ident, True, output_hash=store.candidate_hash(candidate))
            self.key = db.review_complete("source1", candidate, {"reservation_id": ident, "accepted": True,
                                          "candidate_hash": store.candidate_hash(candidate)})
            self.db_path = Path(db.path)
            db.set_control("observer_approval", {"repo": fixtures.REPO, "settings": runtime.settings(self.config),
                                                 "publisher_id": self.config["observer"]["publisher_id"]})
            db.set_control("runtime_gate", {"test": True})
        self.gate = mock.patch.object(runtime, "gate_valid", return_value=True)
        self.gate.start()
        self.addCleanup(self.gate.stop)

    def state(self):
        return json.loads(self.remote.read_text())

    def calls(self):
        return self.state().get("log", [])

    def publish(self, **kwargs):
        return publisher.publish(fixtures.REPO, self.key, self.config, **kwargs)

    def test_verified_post_is_append_only_and_retry_is_noop(self):
        result = self.publish()
        self.assertEqual("verified", result["state"])
        self.assertEqual(9100, result["receipt"]["comment_id"])
        second = self.publish()
        self.assertEqual(result, second)
        writes = [args for args in self.calls() if args[:2] in (["issue", "comment"], ["issue", "edit"], ["issue", "create"])]
        self.assertEqual(1, len(writes))
        self.assertEqual(["issue", "comment"], writes[0][:2])
        with store.Store(fixtures.REPO, config=self.config) as db:
            self.assertTrue(db.get_control("sync_needed"))
            self.assertEqual([], db.pending())

    def test_verified_receipt_survives_handled_excerpt_expiry(self):
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.capture([self.metadata("excerpt-source")])
            candidate = dict(db.event(self.key)["candidate"], sources=["excerpt-source"], excerpts=["Reviewed evidence"])
            ident = db.reserve_invocation("reviewer")
            digest = store.candidate_hash(candidate)
            db.complete_invocation(ident, True, output_hash=digest)
            self.key = db.review_complete("excerpt-source", candidate,
                                          {"reservation_id": ident, "accepted": True, "candidate_hash": digest})
        first = self.publish()
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.db.execute("UPDATE events SET updated=? WHERE eventkey=?", (time.time() - 8 * 86400, self.key))
            db.expire_handled()
            self.assertNotIn("excerpts", db.event(self.key)["candidate"])
            self.assertEqual(digest, db.event(self.key)["review"]["candidate_hash"])
        self.assertEqual(first, self.publish())
        self.assertEqual(first, publisher.reconcile(fixtures.REPO, self.key, self.config))
        self.assertEqual(1, len([args for args in self.calls() if args[:2] == ["issue", "comment"]]))

    def test_verified_shortcut_still_rejects_a_changed_frozen_payload(self):
        self.publish()
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.db.execute("UPDATE events SET body=body || ? WHERE eventkey=?", ("Changed after verification", self.key))
        before = len(self.calls())
        with self.assertRaisesRegex(publisher.PublishError, "payload_changed"):
            self.publish()
        with self.assertRaisesRegex(publisher.PublishError, "payload_changed"):
            publisher.reconcile(fixtures.REPO, self.key, self.config)
        self.assertEqual(before, len(self.calls()))

    def test_verified_shortcut_rejects_a_receipt_bound_to_another_payload(self):
        self.publish()
        with store.Store(fixtures.REPO, config=self.config) as db:
            receipt = db.event(self.key)["receipt"]
            receipt["payload_hash"] = "a" * 64
            db.db.execute("UPDATE events SET receipt=? WHERE eventkey=?", (json.dumps(receipt), self.key))
        with self.assertRaisesRegex(publisher.PublishError, "receipt_changed"):
            self.publish()

    def test_timeout_after_acceptance_reconciles_without_second_post(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL_AFTER": "issue comment"}):
            result = self.publish()
        self.assertEqual("uncertain", result["state"])
        recovered = publisher.reconcile(fixtures.REPO, self.key, self.config)
        self.assertEqual("verified", recovered["state"])
        self.assertEqual(1, len([args for args in self.calls() if args[:2] == ["issue", "comment"]]))

    def test_uncertain_absence_does_not_retry(self):
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.transition(self.key, "sending", account="octocat")
        result = self.publish()
        self.assertEqual("uncertain", result["state"])
        self.assertEqual("remote_not_found", result["reason"])
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])

    def test_conflicting_remote_copies_stay_uncertain(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL_AFTER": "issue comment"}):
            self.publish()
        data = self.state()
        data["comments"]["9101"] = copy.deepcopy(data["comments"]["9100"])
        self.remote.write_text(json.dumps(data))
        result = publisher.reconcile(fixtures.REPO, self.key, self.config)
        self.assertEqual("duplicate_event", result["reason"])
        self.assertEqual("uncertain", result["state"])

    def test_wrong_author_is_not_adopted(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL_AFTER": "issue comment"}):
            self.publish()
        data = self.state()
        data["comments"]["9100"]["user"] = "hubot"
        self.remote.write_text(json.dumps(data))
        result = publisher.reconcile(fixtures.REPO, self.key, self.config)
        self.assertEqual("wrong_author", result["reason"])

    def test_shadow_mode_cannot_post(self):
        self.config["observer"]["mode"] = "shadow"
        with self.assertRaisesRegex(publisher.PublishError, "not_approved"):
            self.publish()
        self.assertEqual([], self.calls())

    def test_runtime_failure_and_publication_pause_make_no_network_call(self):
        with mock.patch.object(runtime, "gate_valid", return_value=False):
            with self.assertRaisesRegex(publisher.PublishError, "runtime_unavailable"):
                self.publish()
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.set_control("publication_paused", True)
        with self.assertRaisesRegex(publisher.PublishError, "publication_paused"):
            self.publish()
        self.assertEqual([], self.calls())

    def test_changed_settings_invalidate_local_approval(self):
        self.config["observer"]["max_invocations"] = 10
        with self.assertRaisesRegex(publisher.PublishError, "not_approved"):
            self.publish()
        self.assertEqual([], self.calls())

    def test_account_changed_after_lint_is_not_used_for_a_post(self):
        with mock.patch.object(publisher, "_account", side_effect=["octocat", "hubot"]):
            with self.assertRaisesRegex(publisher.PublishError, "account_changed"):
                self.publish()
        with store.Store(fixtures.REPO, config=self.config) as db:
            self.assertEqual("ready", db.event(self.key)["state"])
            self.assertEqual("", db.event(self.key)["account"])
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])

    def test_account_changed_after_intent_remains_uncertain_without_post(self):
        with mock.patch.object(publisher, "_account", side_effect=["octocat", "octocat", "hubot"]):
            result = self.publish()
        self.assertEqual("account_changed", result["reason"])
        self.assertEqual("uncertain", result["state"])
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])

    def test_dry_run_changes_no_store_bytes_and_makes_no_network_call(self):
        before = self.db_path.read_bytes()
        stamp = self.db_path.stat().st_mtime_ns
        result = self.publish(dry_run=True)
        self.assertIn("gh issue comment 3", result["command"])
        self.assertEqual(before, self.db_path.read_bytes())
        self.assertEqual(stamp, self.db_path.stat().st_mtime_ns)
        self.assertEqual([], self.calls())

    def test_dry_run_on_missing_store_creates_no_queue(self):
        before = set((self.base / "state").rglob("*"))
        with self.assertRaisesRegex(store.StoreError, "missing"):
            publisher.publish("octo-org/another", self.key, self.config, dry_run=True)
        self.assertEqual(before, set((self.base / "state").rglob("*")))

    def test_review_reservation_tampering_refuses_before_network(self):
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.db.execute("UPDATE invocations SET output_hash=?", ("a" * 64,))
        with self.assertRaisesRegex(publisher.PublishError, "review_changed"):
            self.publish()
        self.assertEqual([], self.calls())

    def test_event_key_lint_and_legacy_parser_compatibility(self):
        with store.Store(fixtures.REPO, config=self.config) as db:
            event = db.event(self.key)
        self.assertEqual(self.key, record_format.parse_marker(record_format.first_lines(event["body"])[0])["eventkey"])
        findings = lint_drafts.lint_text(event["body"], kind="idea", config={})
        self.assertFalse(any(f.code in ("event-key", "marker-key") for f in findings))
        bad = event["body"].replace(self.key, "G" * 32)
        self.assertTrue(any(f.code == "event-key" for f in lint_drafts.lint_text(bad, kind="idea", config={})))
        import importlib.util
        spec = importlib.util.spec_from_file_location("legacy_event_reader", fixtures.ROOT / "tests/fixtures/compat-0.5.0/record_format.py")
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        # 0.5.0 did not know ideas but still retains the event marker's time and extra key.
        self.assertEqual(self.key, legacy.parse_marker(record_format.first_lines(event["body"])[0])["eventkey"])
        self.assertEqual("2026-03-05T10:00-08:00", legacy.parse_event(event["body"])["at"])


if __name__ == "__main__":
    unittest.main()
