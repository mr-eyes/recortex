"""Failure boundaries of the private pending queue; no network calls."""

import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "recortex" / "scripts"))
import observer_store as store
import storage

REPO = "octo-org/octo-repo"
BODY = "<!-- ghim:event type=checkpoint at=2026-03-04T10:00:00Z -->\n### Checkpoint: 2026-03-04 10:00 UTC - Saved work\n"


def initialize_together(barrier, results):
    try:
        barrier.wait(timeout=5)
        with store.Store(REPO, config={}) as db:
            results.put(db.status()["observations"])
    except Exception as error:
        results.put(type(error).__name__ + ": " + str(error))


def interrupted_transaction():
    with store.Store(REPO, config={}) as db:
        with db.transaction():
            db.db.execute("INSERT INTO cursors VALUES ('crashed',10)")
            os._exit(17)


class ObserverStoreTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="observer-store-", dir=str(ROOT / ".tmp"))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.base / "state"),
                                  "XDG_CACHE_HOME": str(self.base / "cache"),
                                  "XDG_CONFIG_HOME": str(self.base / "config")}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_discarded_event_loses_its_excerpts_after_the_retention(self):
        import observer
        with store.Store(REPO, config={}) as db:
            key, _, _ = self.ready(db, excerpts=["raw transcript words"])
        with mock.patch.object(observer.recortex, "load_config", return_value={"repo": REPO}), \
                mock.patch.object(observer.recortex, "allowed_users", return_value={"octocat"}), \
                mock.patch.object(observer, "on_terminal", return_value=True), \
                mock.patch.object(observer, "emit") as emitted:
            self.assertEqual(0, observer.main(["discard", key]))
            self.assertEqual(1, observer.main(["discard", key]))  # only a ready or deferred event
        self.assertEqual({"code": "event-not-discardable"}, emitted.call_args.args[0])
        with store.Store(REPO, config={}) as db:
            self.assertEqual("rejected", db.event(key)["state"])
            db.db.execute("UPDATE events SET updated=? WHERE eventkey=?", (time.time() - 8 * 86400, key))
            db.expire_handled()
            self.assertNotIn("excerpts", db.event(key)["candidate"])

    def metadata(self, source="source1"):
        return {"source_id": source, "session_id": "session1", "role": "assistant", "event_type": "stop",
                "path": "/example/session.jsonl", "offset": 0, "end": 10,
                "sha256": hashlib.sha256(b"source").hexdigest(), "observed_at": 1234}

    def ready(self, db, source="source1", body=BODY, excerpts=None):
        db.capture([self.metadata(source)])
        candidate = {"kind": "checkpoint", "issue": 3, "body": body, "sources": [source]}
        if excerpts is not None:
            candidate["excerpts"] = excerpts
        invocation = db.reserve_invocation("reviewer")
        db.complete_invocation(invocation, True, output_hash=store.candidate_hash(candidate))
        review = {"reservation_id": invocation, "accepted": True, "candidate_hash": store.candidate_hash(candidate)}
        return db.review_complete(source, candidate, review), candidate, review

    def test_queue_uses_private_state_and_survives_cache_loss(self):
        with store.Store(REPO, config={}) as db:
            key, _, _ = self.ready(db)
            path = db.path
            self.assertIn(str(self.base / "state"), path)
            self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
            self.assertEqual(0o700, os.stat(db.folder).st_mode & 0o777)
        with store.Store(REPO, config={}) as db:
            self.assertEqual("ready", db.event(key)["state"])
            self.assertIn(" eventkey=" + key + " -->", db.event(key)["body"])
        self.assertFalse((self.base / "cache").exists())

    def test_raw_text_is_refused_before_review_and_cursor_stays(self):
        with store.Store(REPO, config={}) as db:
            bad = dict(self.metadata(), text="PRIVATE RAW SOURCE")
            with self.assertRaisesRegex(store.StoreError, "raw_source_refused"):
                db.capture([bad], "stream", 10)
            self.assertIsNone(db.cursor("stream"))
            self.assertEqual([], db.pending())
            self.assertNotIn(b"PRIVATE RAW SOURCE", Path(db.path).read_bytes())

    def test_capacity_failure_rolls_back_entire_capture_and_cursor(self):
        with store.Store(REPO, config={}, max_pending=1) as db:
            with self.assertRaisesRegex(store.StoreError, "capacity"):
                db.capture([self.metadata("one"), self.metadata("two")], "stream", 20)
            self.assertIsNone(db.cursor("stream"))
            self.assertEqual([], db.pending())

    def test_duplicate_sources_are_stable_and_changed_metadata_is_refused(self):
        with store.Store(REPO, config={}) as db:
            db.capture([self.metadata()], "stream", 10)
            db.capture([self.metadata()], "stream", 10, expected_cursor=10)
            self.assertEqual(1, len(db.pending()))
            changed = dict(self.metadata(), sha256="f" * 64)
            with self.assertRaisesRegex(store.StoreError, "source_changed"):
                db.capture([changed], "stream", 20, expected_cursor=10)
            self.assertEqual(10, db.cursor("stream"))

    def test_compare_cursor_prevents_two_readers_advancing_over_each_other(self):
        with store.Store(REPO, config={}) as first, store.Store(REPO, config={}) as second:
            first.capture([self.metadata("one")], "stream", 10)
            with self.assertRaisesRegex(store.StoreError, "cursor_changed"):
                second.capture([self.metadata("two")], "stream", 20)
            self.assertIsNone(second.observation("two"))

    def test_failed_and_unfinished_invocations_count_against_budget(self):
        with store.Store(REPO, config={}) as db:
            first = db.reserve_invocation("reviewer", limit=2)
            db.complete_invocation(first, False)
            self.assertIsNotNone(db.reserve_invocation("judge", limit=2))
            self.assertIsNone(db.reserve_invocation("reviewer", limit=2))
            self.assertEqual(2, db.status()["invocations_24h"])

    def test_review_needs_successful_independent_reservation_and_exact_payload(self):
        with store.Store(REPO, config={}) as db:
            db.capture([self.metadata()])
            candidate = {"kind": "checkpoint", "issue": 3, "body": BODY, "sources": ["source1"]}
            invocation = db.reserve_invocation("judge")
            db.complete_invocation(invocation, True)
            review = {"reservation_id": invocation, "accepted": True, "candidate_hash": store.candidate_hash(candidate)}
            with self.assertRaisesRegex(store.StoreError, "invalid_review"):
                db.review_complete("source1", candidate, review)
            review["reservation_id"] = db.reserve_invocation("reviewer")
            with self.assertRaisesRegex(store.StoreError, "invalid_review"):
                db.review_complete("source1", candidate, review)
            db.complete_invocation(review["reservation_id"], True, output_hash=store.candidate_hash(candidate))
            candidate["body"] += "Changed after review."
            with self.assertRaisesRegex(store.StoreError, "review_changed"):
                db.review_complete("source1", candidate, review)
            self.assertEqual([], db.events())

    def test_restricted_excerpts_are_not_stored_even_after_review(self):
        config = {"restricted_patterns": [{"name": "private identifier", "pattern": "PRIVATE-[0-9]+"}]}
        with store.Store(REPO, config=config) as db:
            with self.assertRaisesRegex(store.StoreError, "restricted"):
                self.ready(db, excerpts=["PRIVATE-12345"])
            self.assertNotIn(b"PRIVATE-12345", Path(db.path).read_bytes())
            self.assertEqual([], db.events())

    def test_same_source_overlap_defers_different_payload_instead_of_reposting(self):
        with store.Store(REPO, config={}) as db:
            key, _, _ = self.ready(db)
            second, _, _ = self.ready(db, body=BODY + "Different summary.\n")
            self.assertIsNone(second)
            self.assertEqual(1, len(db.events()))
            self.assertEqual("ready", db.event(key)["state"])
            self.assertEqual("source_overlap", db.observation("source1")["reason"])

    def test_sending_requires_uncertain_reconciliation_never_blind_ready(self):
        with store.Store(REPO, config={}) as db:
            key, _, _ = self.ready(db)
            db.transition(key, "sending", account="octocat")
            db.transition(key, "uncertain", reason="request_failed")
            with self.assertRaisesRegex(store.StoreError, "invalid_transition"):
                db.transition(key, "ready")
            with self.assertRaisesRegex(store.StoreError, "account_changed"):
                db.transition(key, "verified", account="hubot", receipt={"comment_id": 10})
            db.transition(key, "verified", receipt={"comment_id": 10})
            self.assertEqual([], db.pending())

    def test_local_publication_lock_refuses_a_second_writer(self):
        with store.Store(REPO, config={}) as first, store.Store(REPO, config={}) as second:
            with first.publication_lock():
                with self.assertRaisesRegex(store.StoreError, "publisher_busy"):
                    with second.publication_lock():
                        self.fail("second writer entered")
            with second.publication_lock():
                pass

    def test_no_lock_fallback_silently_allows_writes(self):
        with store.Store(REPO, config={}) as db, mock.patch.object(store, "fcntl", None):
            with self.assertRaisesRegex(store.StoreError, "locking_unavailable"):
                with db.publication_lock():
                    self.fail("lockless publication")

    def test_expiry_removes_handled_excerpts_but_never_pending(self):
        with store.Store(REPO, config={}) as db:
            pending, _, _ = self.ready(db, "pending", excerpts=["pending evidence"])
            handled, _, _ = self.ready(db, "handled", excerpts=["handled evidence"])
            db.transition(handled, "sending", account="octocat")
            db.transition(handled, "verified", receipt={"comment_id": 10})
            db.db.execute("UPDATE events SET updated=?", (time.time() - 8 * 86400,))
            db.expire_handled()
            self.assertEqual(["pending evidence"], db.event(pending)["candidate"]["excerpts"])
            self.assertNotIn("excerpts", db.event(handled)["candidate"])
            self.assertEqual({"comment_id": 10}, db.event(handled)["receipt"])

    def test_corrupt_existing_store_is_not_recreated(self):
        with store.Store(REPO, config={}) as db:
            path = db.path
        Path(path).write_bytes(b"not a SQLite database")
        with self.assertRaisesRegex(store.StoreError, "corrupt"):
            store.Store(REPO, config={})
        self.assertEqual(b"not a SQLite database", Path(path).read_bytes())

    def test_truncated_store_is_not_assumed_empty(self):
        with store.Store(REPO, config={}) as db:
            path = db.path
        Path(path).write_bytes(b"")
        with self.assertRaisesRegex(store.StoreError, "corrupt"):
            store.Store(REPO, config={})

    def test_missing_table_with_valid_identity_is_not_repaired(self):
        with store.Store(REPO, config={}) as db:
            self.ready(db)
            db.db.execute("DROP TABLE observations")
            path = Path(db.path)
        before = path.read_bytes()
        for options in ({}, {"create": False}, {"readonly": True}):
            with self.assertRaisesRegex(store.StoreError, "corrupt"):
                store.Store(REPO, config={}, **options)
            self.assertEqual(before, path.read_bytes())

    def test_changed_columns_are_not_silently_accepted(self):
        with store.Store(REPO, config={}) as db:
            db.db.execute("ALTER TABLE observations ADD COLUMN unexpected TEXT")
            path = Path(db.path)
        before = path.read_bytes()
        with self.assertRaisesRegex(store.StoreError, "corrupt"):
            store.Store(REPO, config={})
        self.assertEqual(before, path.read_bytes())

    def test_symlinked_queue_is_refused_without_reading_target(self):
        with store.Store(REPO, config={}) as db:
            path = Path(db.path)
        saved = path.with_name("saved.sqlite")
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises(storage.StorageError):
            store.Store(REPO, config={})

    def test_status_does_not_echo_sources_candidates_or_controls(self):
        with store.Store(REPO, config={}) as db:
            self.ready(db, excerpts=["private source excerpt"])
            db.set_control("private_test", "private control")
            status = json.dumps(db.status())
            self.assertNotIn("private source", status)
            self.assertNotIn("private control", status)
            self.assertNotIn("/example/", status)

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires fork")
    def test_two_processes_initialize_the_same_new_store(self):
        ctx = multiprocessing.get_context("fork")
        barrier, results = ctx.Barrier(2), ctx.Queue()
        processes = [ctx.Process(target=initialize_together, args=(barrier, results)) for _ in range(2)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join()
            self.assertEqual(0, process.exitcode)
        self.assertEqual([{}, {}], [results.get(timeout=2) for _ in range(2)])

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "requires fork")
    def test_killed_writer_does_not_commit_its_cursor(self):
        with store.Store(REPO, config={}):
            pass
        process = multiprocessing.get_context("fork").Process(target=interrupted_transaction)
        process.start()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join()
        self.assertEqual(17, process.exitcode)
        with store.Store(REPO, config={}) as db:
            self.assertIsNone(db.cursor("crashed"))

    def test_database_swapped_during_connect_is_not_read(self):
        with store.Store(REPO, config={}) as db:
            path = Path(db.path)
        original = sqlite3.connect

        def swapping(*args, **kwargs):
            connection = original(*args, **kwargs)
            path.rename(path.with_name("before.sqlite"))
            path.write_bytes(b"planted private text")
            path.chmod(0o600)
            return connection

        with mock.patch.object(store.sqlite3, "connect", side_effect=swapping):
            with self.assertRaisesRegex(store.StoreError, "changed"):
                store.Store(REPO, config={})

    def test_review_reservation_cannot_be_reused_for_a_different_candidate(self):
        with store.Store(REPO, config={}) as db:
            _, _, review = self.ready(db)
            db.capture([self.metadata("another")])
            another = {"kind": "checkpoint", "issue": 3, "body": BODY, "sources": ["another"]}
            forged = dict(review, candidate_hash=store.candidate_hash(another))
            with self.assertRaisesRegex(store.StoreError, "invalid_review"):
                db.review_complete("another", another, forged)
            self.assertEqual(1, len(db.events()))

    def test_disk_error_rolls_back_source_and_cursor(self):
        with store.Store(REPO, config={}) as db:
            db.db.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON cursors BEGIN SELECT RAISE(ABORT, 'disk error'); END")
            with self.assertRaisesRegex(store.StoreError, "storage"):
                db.capture([self.metadata()], "stream", 10)
            self.assertIsNone(db.cursor("stream"))
            self.assertEqual([], db.pending())

    def test_capture_replay_ignores_new_observation_wall_time(self):
        with store.Store(REPO, config={}) as db:
            db.capture([self.metadata()])
            db.capture([dict(self.metadata(), observed_at=9999)])
            self.assertEqual(1, len(db.pending()))

    def test_native_record_copied_to_fork_keeps_first_pointer(self):
        original = dict(self.metadata(), content_sha256="e" * 64)
        fork = dict(original, session_id="fork-session", path="/example/fork.jsonl", sha256="a" * 64,
                    offset=200, end=300, observed_at=5000)
        with store.Store(REPO, config={}) as db:
            db.capture([original], "original-stream", 10)
            db.capture([fork], "fork-stream", 300)
            self.assertEqual(original, db.observation("source1")["metadata"])
            self.assertEqual(1, len(db.pending()))
            self.assertEqual(300, db.cursor("fork-stream"))
            for changed in (dict(fork, content_sha256="b" * 64), dict(fork, role="user")):
                with self.assertRaisesRegex(store.StoreError, "source_changed"):
                    db.capture([changed])

    def test_configured_byte_limit_is_never_silently_raised(self):
        with store.Store(REPO, config={}) as db:
            path = db.path
        with store.Store(REPO, config={}, max_bytes=1000) as db:
            self.assertEqual(1000, db.max_bytes)
            with self.assertRaisesRegex(store.StoreError, "capacity"):
                db.capture([self.metadata()], "stream", 10)
            self.assertIsNone(db.cursor("stream"))
            self.assertEqual([], db.pending())

    def test_hold_transforms_keep_other_sessions_changes(self):
        with store.Store(REPO, config={}) as one, store.Store(REPO, config={}) as two:
            one.update_holds(lambda holds: holds + [{"id": "first"}])
            two.update_holds(lambda holds: holds + [{"id": "second"}])
            self.assertEqual([{"id": "first"}, {"id": "second"}], one.holds())

    def test_readonly_missing_store_creates_nothing(self):
        with self.assertRaisesRegex(store.StoreError, "missing"):
            store.Store(REPO, config={}, readonly=True)
        self.assertFalse((self.base / "state").exists())

    def test_configured_longer_budget_window_and_shorter_retention(self):
        with store.Store(REPO, config={}) as db:
            key, _, _ = self.ready(db, excerpts=["handled excerpt"])
            db.db.execute("UPDATE invocations SET created=?", (time.time() - 2 * 86400,))
            self.assertIsNone(db.reserve_invocation("reviewer", limit=1, window_seconds=7 * 86400))
            db.transition(key, "sending", account="octocat")
            db.transition(key, "verified", receipt={"comment_id": 10})
            db.db.execute("UPDATE events SET updated=?", (time.time() - 2 * 86400,))
            db.expire_handled(days=1)
            self.assertNotIn("excerpts", db.event(key)["candidate"])


if __name__ == "__main__":
    unittest.main()
