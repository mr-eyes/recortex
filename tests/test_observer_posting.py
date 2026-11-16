"""Main-session posting shares observer identities, reviewed drafts and receipts."""

import contextlib
import io
import json
import os
import re
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

    def test_a_copy_of_a_queued_event_posts_only_through_that_event(self):
        # The same reviewed body with its eventkey dropped, posted as the session's own memory, would pass around the
        # event's confirmation and the session's withhold: it is refused and named. A rewritten memory still posts.
        import re
        import observer_holds
        hold = observer_holds.make_hold({"source_id": "s1", "role": "user", "direct_user": True, "text": "off the record"},
                                        fixtures.REPO, "publish", scope={"session_id": "session1"})
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.update_holds(lambda holds: holds + [hold])
            body = db.event(self.key)["body"]
        for copy in (re.sub(r" eventkey=[0-9a-f]{32}", "", body, count=1), body):
            result, output = self.post(False, 3, "idea", copy)
            self.assertEqual(1, result, output)
            self.assertIn("--eventkey %s" % self.key, output)
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])
        result, output = self.post(False, 3, "idea", self.own_idea())
        self.assertEqual(0, result, output)

    def discard(self, key):
        import observer
        out = io.StringIO()
        with mock.patch.object(observer.recortex, "load_config", return_value=self.config), contextlib.redirect_stdout(out):
            self.assertEqual(0, observer.main(["--repo", fixtures.REPO, "discard", key]), out.getvalue())
        with store.Store(fixtures.REPO, config=self.config, readonly=True) as db:
            event = db.event(key)
        self.assertEqual(("rejected", "discarded"), (event["state"], event["reason"]))
        return event["held"]

    def test_discarding_a_withheld_event_does_not_free_its_copy(self):
        # Discarding is allowed and posts nothing; it does not lift the copy check: the same body as the session's
        # own memory is still refused, with no hint to discard, and a rewritten memory still posts.
        import re
        import observer_holds
        hold = observer_holds.make_hold({"source_id": "s1", "role": "user", "direct_user": True, "text": "off the record"},
                                        fixtures.REPO, "publish", scope={"session_id": "session1"})
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.update_holds(lambda holds: holds + [hold])
            body = db.event(self.key)["body"]
        self.assertEqual(1, self.discard(self.key))
        result, output = self.post(False, 3, "idea", re.sub(r" eventkey=[0-9a-f]{32}", "", body, count=1))
        self.assertEqual(1, result, output)
        self.assertIn("discarded (event %s)" % self.key, output)
        self.assertNotIn("observer.py discard", output)
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])
        result, output = self.post(False, 3, "idea", self.own_idea())
        self.assertEqual(0, result, output)

    def test_a_discarded_decision_blocks_its_exact_words_only_when_it_was_held(self):
        from tests.test_observer_worker import BODY as decision  # a plain decision: no link, no change of status
        def queue(source):
            with store.Store(fixtures.REPO, config=self.config) as db:
                db.capture([self.metadata(source)])
                candidate = {"kind": "decision", "issue": 3, "body": decision, "sources": [source]}
                ident = db.reserve_invocation("reviewer")
                db.complete_invocation(ident, True, output_hash=store.candidate_hash(candidate))
                return db.review_complete(source, candidate, {"reservation_id": ident, "accepted": True,
                                                              "candidate_hash": store.candidate_hash(candidate)})
        plain = queue("source9")
        self.assertEqual(0, self.discard(plain))
        result, output = self.post(False, 3, "decision", decision.replace("Strict parser", "Strict reader"))
        self.assertEqual(0, result, output)  # discarded and never held: the session may record it itself
        held = queue("source8")
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.transition(held, "deferred", reason="status_change")
        self.assertEqual(1, self.discard(held))
        result, output = self.post(False, 3, "decision", decision.replace("Strict parser", "Strict quoting"))
        self.assertEqual(1, result, output)
        self.assertIn("discarded (event %s)" % held, output)

    def test_discarding_an_event_under_a_recortex_hold_does_not_free_its_copy(self):
        # A /recortex hold on the comment's exact command: the event is held whether the worker deferred it first
        # (explicit_hold marks it held) or it is discarded while still ready (the hold is checked at the discard).
        import re
        import observer_holds
        import observer_publisher
        second = publisher_fixtures.ObserverPublisherTests._second(self, self.own_idea().replace("quote state", "quote "
                                                                                                   "tracking"))
        for key, deferred in ((self.key, False), (second, True)):
            with store.Store(fixtures.REPO, config=self.config) as db:
                event = db.event(key)
                command = __import__("recortex").show_command(observer_publisher._command(fixtures.REPO, event))
                hold = observer_holds.make_hold({"source_id": "s1", "role": "user", "direct_user": True,
                                                 "text": "/recortex hold command " + command}, fixtures.REPO, "command",
                                                command=command)
                db.update_holds(lambda holds: holds + [hold])
                if deferred:  # the worker met the hold first, then the user lifted it
                    db.transition(key, "deferred", reason="explicit_hold")
                    self.assertEqual(1, db.event(key)["held"])
                    db.update_holds(lambda holds: observer_holds.release(holds, hold["id"]))
            self.assertEqual(1, self.discard(key))
            with store.Store(fixtures.REPO, config=self.config) as db:
                if not deferred:  # discarded under the hold, which is lifted afterwards
                    db.update_holds(lambda holds: observer_holds.release(holds, hold["id"]))
                body = db.event(key)["body"]
            result, output = self.post(False, 3, "idea", re.sub(r" eventkey=[0-9a-f]{32}", "", body, count=1))
            self.assertEqual(1, result, output)
            self.assertIn("discarded (event %s)" % key, output)
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])
        result, output = self.post(False, 3, "idea", self.own_idea())
        self.assertEqual(0, result, output)

    def test_a_copy_is_found_among_more_than_ten_thousand_events(self):
        # The lookup is by digest over the whole queue, whatever its size: the fixture's event is the newest of
        # 10,051, and its copy is still refused.
        import re
        with store.Store(fixtures.REPO, config=self.config) as db:
            body = db.event(self.key)["body"]
            filler = [("%032x" % n, "filler", "{}", "{}", "x", "0" * 64, "rejected", 1.0 + n, 1.0 + n,
                       store.copy_digests("idea", "filler %d" % n)[0]) for n in range(10050)]
            with db.transaction():
                db.db.executemany("INSERT INTO events(eventkey,source_id,candidate,review,body,payload_hash,state,"
                                  "created,updated,body_hash) VALUES (?,?,?,?,?,?,?,?,?,?)", filler)
            self.assertEqual(10051, db.db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        result, output = self.post(False, 3, "idea", re.sub(r" eventkey=[0-9a-f]{32}", "", body, count=1))
        self.assertEqual(1, result, output)
        self.assertIn("already holds this memory (event %s" % self.key, output)
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])

    def test_an_earlier_queue_is_migrated_and_its_events_found(self):
        # A format 1 queue: the columns and indexes are added and every event back-filled on the next writable open,
        # held reasons included; read only, it is read as it is and a lookup on it refuses the post.
        import re
        import sqlite3
        with store.Store(fixtures.REPO, config=self.config) as db:
            body, path = db.event(self.key)["body"], db.path
            db.transition(self.key, "deferred", reason="withheld")
        connection = sqlite3.connect(path)
        for statement in store.INDEXES:
            connection.execute("DROP INDEX %s" % statement.split()[5])
        for name, _ in reversed(store.ADDED):
            connection.execute("ALTER TABLE events DROP COLUMN %s" % name)
        connection.execute("UPDATE meta SET value='1' WHERE key='format'")
        connection.commit()
        connection.close()
        copy = re.sub(r" eventkey=[0-9a-f]{32}", "", body, count=1)
        with store.Store(fixtures.REPO, config=self.config, readonly=True) as db:
            self.assertEqual("1", db.format)
            with self.assertRaisesRegex(store.StoreError, "lookup_unavailable"):
                db.copy_of(copy)
        result, output = self.post(False, 3, "idea", copy)
        self.assertEqual(1, result, output)
        self.assertIn("could not be validated", output)  # the lookup could not complete: refused
        with store.Store(fixtures.REPO, config=self.config) as db:
            self.assertEqual(store.FORMAT, db.format)
            self.assertEqual(1, db.event(self.key)["held"])
            self.assertEqual(self.key, db.copy_of(copy)["eventkey"])
        result, output = self.post(False, 3, "idea", copy)
        self.assertEqual(1, result, output)
        self.assertIn("already holds this memory (event %s" % self.key, output)
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])

    def test_a_copy_check_that_cannot_complete_refuses_the_post(self):
        with mock.patch.object(store.Store, "copy_of", side_effect=store.StoreError("lookup_unavailable")):
            result, output = self.post(False, 3, "idea", self.own_idea())
        self.assertEqual(1, result, output)
        self.assertIn("could not be validated", output)
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])

    def test_every_reason_that_keeps_an_event_back_marks_it_held(self):
        # One registry: what the publisher raises for a hold or a held-back event, what the worker defers with, and
        # what marks an event held are the same set.
        import inspect
        import observer_holds
        import observer_publisher
        source = inspect.getsource(observer_publisher.held_back) + inspect.getsource(observer_publisher.hold_reason)
        named = set(re.findall(r'(?:append|return)\("([a-z_]+)"', source))
        self.assertTrue(named)
        self.assertLessEqual(named, set(observer_holds.HELD_REASONS))
        self.assertLessEqual(set(observer_holds.HELD_REASONS), observer_publisher.EVENT_PROBLEMS)
        for reason in observer_holds.HELD_REASONS:
            key = publisher_fixtures.ObserverPublisherTests._second(self, self.own_idea().replace(
                "quote state", "quote " + reason.replace("_", " ")), source="source-" + reason.replace("_", "-"))
            with store.Store(fixtures.REPO, config=self.config) as db:
                db.transition(key, "deferred", reason=reason)
                self.assertEqual(1, db.event(key)["held"], reason)
                db.transition(key, "ready")
                self.assertEqual(1, db.event(key)["held"], reason)  # for good
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.transition(self.key, "deferred", reason="main_session")
            self.assertEqual(0, db.event(self.key)["held"])

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
        self.assertIn("already holds this memory (event %s" % held, output)
        self.assertEqual([], self.calls())

    def test_a_held_back_event_posts_through_its_eventkey_on_the_users_answer(self):
        # Held back by the automatic gate (here: its reviewer doubted a marked project's memory), it is listed for the
        # session's question with pending --held, and --eventkey posts it with no terminal step. The daily cap of
        # automatic comments is not the session's; a pause still stops it, and a copy without --eventkey is refused.
        import observer
        self.config["observer"]["max_posts"] = 1
        observer_runtime.approve(fixtures.REPO, self.config)
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.transition(self.key, "deferred", reason="confidentiality")
            db.set_control("automatic_posts", [__import__("time").time()])
            body = db.event(self.key)["body"]
        out = io.StringIO()
        with mock.patch.object(observer.recortex, "load_config", return_value=self.config), contextlib.redirect_stdout(out):
            self.assertEqual(0, observer.main(["--repo", fixtures.REPO, "pending", "--held"]))
        listed = json.loads(out.getvalue())
        self.assertEqual([(self.key, "idea", 3, "confidentiality")],
                         [(item["eventkey"], item["kind"], item["issue"], item["reason"]) for item in listed["held"]])
        self.assertTrue(listed["held"][0]["line"])
        self.assertIn("multiple-choice question", listed["note"])
        result, output = self.post(False, 3, "idea", re.sub(r" eventkey=[0-9a-f]{32}", "", body, count=1))
        self.assertEqual(1, result, output)
        self.assertIn("--eventkey %s" % self.key, output)
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.set_control("publication_paused", True)
        result, output = self.post()
        self.assertIn("refused this event (publication_paused)", output)  # a refusal names its real reason
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.set_control("publication_paused", False)
        with mock.patch.object(observer, "terminal", side_effect=AssertionError("no terminal step")):
            result, output = self.post()
        self.assertEqual(0, result, output)
        self.assertIn("memory id: 9100", output)
        self.assertEqual(1, len([args for args in self.calls() if args[:2] == ["issue", "comment"]]))

    def test_an_event_the_gate_did_not_hold_back_needs_no_terminal(self):
        import observer
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.transition(self.key, "deferred", reason="main_session")
        with mock.patch.object(observer, "terminal", return_value=None):
            result, output = self.post()
        self.assertEqual(0, result, output)

    def test_a_withheld_session_is_not_posted_by_the_main_session_either(self):
        import observer_holds
        hold = observer_holds.make_hold({"source_id": "s1", "role": "user", "direct_user": True, "text": "off the record"},
                                        fixtures.REPO, "publish", scope={"session_id": "session1"})
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.update_holds(lambda holds: holds + [hold])
        result, output = self.post()
        self.assertEqual(1, result, output)
        self.assertIn("withheld", output)
        self.assertEqual([], [args for args in self.calls() if args[:2] == ["issue", "comment"]])
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.update_holds(lambda holds: observer_holds.release(holds, hold["id"]))
        self.assertEqual(0, self.post()[0])

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
