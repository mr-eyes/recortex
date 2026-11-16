"""Independent offline replay, sustained-arrival and source-authority probes."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("observer_longitudinal", ROOT / "examples/observer/run.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class ObserverLongitudinalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A hostile inherited override must never replace the executable's fake gh.
        with mock.patch.dict(os.environ, {"RECORTEX_GH": "/does-not-exist/live-gh", "FAKE_GH_FAIL_ON": "api"}):
            cls.report = evaluation.run_replay()

    def test_sixty_days_cover_both_domains_and_separate_elapsed_time(self):
        replay = self.report["simulated"]
        self.assertEqual(60, replay["days"])
        self.assertEqual(["coding", "research"], replay["domains"])
        self.assertEqual(list(range(1, 61)), [item["day"] for item in replay["daily"]])
        self.assertGreaterEqual(replay["native_sessions"], 120)
        for stream in replay["continuous_sessions"]:
            self.assertEqual(1, stream["session_count"])
            self.assertEqual(60, stream["days"])
            self.assertEqual(0, stream["resume_hooks"])
            self.assertIn(1, stream["retained_meaningful_days"])
            self.assertIn(60, stream["retained_meaningful_days"])
        self.assertGreater(self.report["elapsed_wall_seconds"], 0)
        self.assertEqual(0, self.report["live_github_calls"])
        self.assertEqual(0, self.report["real_model_invocations"])

    def test_scripted_model_is_not_claimed_to_meet_quality_targets(self):
        for name, target in (("recording_precision", .98), ("meaningful_recall", .90), ("advice_relevance", .90)):
            self.assertEqual(target, self.report["efficacy"][name]["target"])
            self.assertIsNone(self.report["efficacy"][name]["measured"])
        self.assertIn("pending", self.report["efficacy"]["status"])
        self.assertIn("not established", self.report["runtime_feasibility"])

    def test_capture_latency_is_measured_and_threshold_is_not_a_ci_assertion(self):
        for name in ("scan_store_capture", "hook_capture_controls"):
            result = self.report["capture_latency"][name]
            self.assertGreaterEqual(result["samples"], 30)
            self.assertGreater(result["p50_ms"], 0)
            self.assertGreaterEqual(result["p95_ms"], result["p50_ms"])
            self.assertGreaterEqual(result["max_ms"], result["p95_ms"])
            self.assertEqual(100, result["target_ms"])
            self.assertEqual(self.report["source_ancestry"]["simulated"], result["source_ancestry_simulated"])
            if result["source_ancestry_simulated"]:
                self.assertEqual("fixture_only", result["status"])
                self.assertIsNone(result["target_met"])
        # Slow machines report a missed target; that is evidence, not a reason
        # to discard the observation or turn a functional test into a speed test.
        self.assertEqual(95, evaluation.percentile(list(range(1, 101)), 95))
        full = self.report["capture_latency"]["full_hook_process"]
        self.assertFalse(full["source_ancestry_simulated"])
        if full["status"] == "available":
            self.assertEqual(30, full["observations_captured"])
            self.assertEqual(30, full["samples"])
            self.assertTrue(full["preflight"]["ready"])
            self.assertGreater(full["p95_ms"], 0)
        else:
            self.assertEqual("unavailable", full["status"])
            self.assertEqual(0, full["observations_captured"])
            self.assertEqual(0, full["samples"])
            self.assertFalse(full["preflight"]["ready"])
            for key in ("p50_ms", "p95_ms", "max_ms", "target_met"):
                self.assertIsNone(full[key])
        self.assertEqual(0, full["invocations"])
        self.assertEqual(0, full["gh_calls"])

    def test_fixture_ancestry_is_disclosed_without_claiming_native_ownership(self):
        ancestry = self.report["source_ancestry"]
        self.assertEqual(2, self.report["schema_version"])
        self.assertEqual(ancestry["actual_preflight"]["ready"], ancestry["native_ownership_established"])
        if ancestry["simulated"]:
            self.assertFalse(ancestry["native_ownership_established"])
            self.assertTrue(ancestry["changed_boundaries"])

    def test_real_retrieval_retains_replacement_chains_and_snapshot_age(self):
        for packet in self.report["context_retrieval"]:
            self.assertTrue(set(packet["required_ids"]) <= set(packet["selected_ids"]))
            self.assertFalse(packet["live_revalidated"])
            self.assertEqual(60, packet["snapshot_age_days"])
        delivery = self.report["continuous_context_delivery"]
        self.assertEqual(0, delivery["resume_hooks"])
        self.assertTrue(delivery["unchanged_context_deduplicated"])
        self.assertTrue(delivery["replacement_refreshes_context"])

    def test_all_matched_explicit_hold_cases_pass(self):
        checks = self.report["simulated"]["checks"]
        self.assertEqual(56 * 8, sum("explicit hold:" in item for item in checks))
        self.assertEqual(56, sum("hold remains scoped" in item for item in checks))


class ObserverLongSessionStressTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="observer-stress-")
        self.addCleanup(self.temporary.cleanup)
        fixture = json.loads(evaluation.FIXTURE.read_text())
        self.replay = evaluation.Replay(self.temporary.name, fixture)
        self.replay.__enter__()
        self.addCleanup(self.replay.__exit__, None, None, None)

    def test_continuous_new_arrivals_do_not_starve_older_retryable_work(self):
        replay = self.replay
        for n in range(9):
            data, _ = replay.append("retry-%02d" % n, "Old local work %d" % n)
            replay.capture(data)
        old_ids = {row["source_id"] for row in replay.observations()}
        with replay.store() as store:
            for sid in old_ids:
                store.defer(sid, "review_failed")
        replay.now += 301
        for wave in range(4):
            for n in range(8):
                data, _ = replay.append("fresh-%d-%d" % (wave, n), "New task %d %d" % (wave, n))
                replay.capture(data)
            replay.work()
        with replay.store() as store:
            self.assertTrue(all(store.observation(sid)["state"] == "ignored" for sid in old_ids),
                            "Old retryable work must progress even when every pass has at least eight new sources")

    def test_meaningful_burst_drains_after_the_candidate_limit_without_manual_retry(self):
        replay = self.replay
        for n in range(8):
            data, _ = replay.append("meaningful-%02d" % n, "Work item %d paused with validation still open." % n,
                                    "checkpoint")
            replay.capture(data)
        replay.work()
        for wave in range(3):
            replay.now += 301
            replay.work()
        with replay.store() as store:
            self.assertEqual(8, len(store.events()),
                             "The four-candidate cap must defer excess meaningful sources for a later finite pass")
            self.assertTrue(all(row["state"] == "ready" for row in replay.observations()))

    def test_fork_dedup_keeps_one_identity_and_a_subagent_is_not_the_conversation(self):
        # A fork repeats a native message under the same identity; a subagent's transcript is not the conversation
        # with the user, so nothing of it is captured.
        replay = self.replay
        text = replay.fixture["domains"]["coding"]["decision"]
        main, identity = replay.append("main-session", text, "decision", role="user")
        subagent, _ = replay.append("analysis-session", text, "decision", role="user", identity=identity, subagent=True)
        fork, _ = replay.append("fork-session", text, "decision", role="user", identity=identity)
        all_rows = []
        for data in (main, subagent, fork):
            rows, _ = evaluation.observer_sources.scan(data, str(replay.project))
            all_rows.append(rows)
            replay.capture(data)
        self.assertEqual([], all_rows[1])
        self.assertEqual(all_rows[0][0]["source_id"], all_rows[2][0]["source_id"])
        self.assertTrue(evaluation.observer_sources.resolve(all_rows[0][0], str(replay.project))["direct_user"])
        self.assertEqual(1, len(replay.observations()))

    def test_capacity_failure_cannot_advance_the_native_scan_cursor(self):
        replay = self.replay
        data, _ = replay.append("capacity-session", "First pending item")
        replay.append("capacity-session", "Second pending item")
        replay.append("capacity-session", "Third pending item")
        rows, cursor = evaluation.observer_sources.scan(data, str(replay.project))
        with replay.store(max_pending=2) as store:
            with self.assertRaisesRegex(evaluation.observer_store.StoreError, "capacity"):
                store.capture(rows, "capacity-stream", cursor)
            self.assertIsNone(store.cursor("capacity-stream"))
            self.assertEqual([], store.pending())
        with replay.store() as store:
            store.capture(rows, "capacity-stream", cursor)
            self.assertEqual(cursor, store.cursor("capacity-stream"))
            self.assertEqual(3, len(store.pending()))

    def test_a_tool_result_is_never_read_and_a_failure_rests_on_the_agents_message(self):
        # A claim in a file the agent read, or in a command's output, reaches no model; the failure the agent tells
        # the user about is what the observer records.
        replay = self.replay
        text = "The validation command failed on four of six input cases."
        for name in ("Read", "Bash"):
            data, _ = replay.append("read-claim", text, "failure", role="tool", tool_name=name)
            replay.capture(data)
        self.assertEqual([], replay.observations())
        self.assertEqual([], replay.work()[0]["ready"])
        self.assertEqual([], replay.model_calls)
        data, _ = replay.append("read-claim", text, "failure", role="assistant")
        replay.capture(data)
        self.assertEqual(1, len(replay.work()[0]["ready"]))
        self.assertEqual(["extract", "review"], [call["purpose"] for call in replay.model_calls])

    def test_untrusted_real_ancestry_prevents_the_full_hook_benchmark(self):
        denied = {"ready": False, "code": "source-owner", "boundary": "ancestor"}
        with mock.patch.object(evaluation.observer_sources, "preflight", return_value=denied), \
                mock.patch.object(evaluation, "fixture_trusted_ancestors") as modeled, \
                mock.patch.object(evaluation.subprocess, "run") as launched:
            result = evaluation.full_hook_latency(self.replay.fixture)
        modeled.assert_not_called()
        launched.assert_not_called()
        self.assertEqual("unavailable", result["status"])
        self.assertEqual("source-owner", result["reason"])
        self.assertEqual([], result["measurements"])
        self.assertEqual(0, result["samples"])
        for key in ("p50_ms", "p95_ms", "max_ms", "target_met"):
            self.assertIsNone(result[key])


if __name__ == "__main__":
    unittest.main()
