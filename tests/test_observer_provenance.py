"""Independent probes of tool identity and evidence passed to both model stages."""

import json
from pathlib import Path
import unittest
from unittest import mock

from tests import test_observer_worker as fixtures

import observer_sources as sources
import observer_store as store
import observer_worker as worker


class ObserverProvenanceTests(unittest.TestCase):
    setUp = fixtures.WorkerTests.setUp
    invoke = fixtures.WorkerTests.invoke
    run_worker = fixtures.WorkerTests.run_worker

    def tool_pair(self, name="Bash", command="python bench/run.py --repeat 5", output="Observed benchmark output"):
        request = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call-1", "name": name, "input": {"command": command}}]}}
        result = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call-1", "is_error": False, "content": output}]}}
        self.path.write_text(json.dumps(request) + "\n" + json.dumps(result) + "\n")
        rows, _ = sources.scan(self.hook, self.project)
        # Exercise the bounded backward lookup when the request was not queued.
        with store.Store(fixtures.REPO, config=self.config) as db:
            db.capture([rows[1]])
        body = (fixtures.ROOT / "examples/octo-repo/result-speed-1.md").read_text()
        candidate = {"kind": "result", "issue": 2, "body": body,
                     "sources": [rows[0]["source_id"], rows[1]["source_id"]], "excerpts": [output]}
        return rows, candidate

    def test_exact_request_and_result_reach_both_models_before_durable_review(self):
        rows, candidate = self.tool_pair(command="python bench/run.py --repeat 5 --probe ORIGINAL-ARGS")
        delegate = self.invoke(candidate)

        def inspect(payload, schema, purpose, options):
            supplied = {item["id"]: item for item in payload["sources"]}
            self.assertEqual(set(candidate["sources"]), set(supplied))
            result = supplied[rows[1]["source_id"]]["tool_results"][0]
            self.assertEqual("Bash", result["tool_name"])
            self.assertEqual("call-1", result["tool_use_id"])
            self.assertFalse(result["is_error"])
            self.assertEqual(rows[0]["source_id"], result["invocation_source_id"])
            self.assertIn("ORIGINAL-ARGS", result["input"]["command"])
            self.assertEqual("Observed benchmark output", supplied[rows[1]["source_id"]]["text"])
            self.assertNotIn("output", result)  # the output is the source's text, shown once
            with store.Store(fixtures.REPO, config=self.config) as db:
                self.assertEqual([], db.events())
                self.assertNotIn(b"ORIGINAL-ARGS", Path(db.path).read_bytes())
                self.assertNotIn(b"Observed benchmark output", Path(db.path).read_bytes())
            return delegate(payload, schema, purpose, options)

        report = self.run_worker(inspect)
        self.assertEqual(["extract", "review"], [purpose for purpose, _ in self.calls])
        self.assertEqual(1, len(report["ready"]))
        with store.Store(fixtures.REPO, config=self.config) as db:
            self.assertEqual(set(candidate["sources"]), set(db.event(report["ready"][0])["candidate"]["sources"]))

    def test_read_output_is_not_promoted_to_an_executed_result(self):
        rows, candidate = self.tool_pair(name="Read", output="Tests passed in an invented example")
        report = self.run_worker(self.invoke(candidate))
        self.assertEqual([], report["ready"])
        self.assertEqual(["extract"], [purpose for purpose, _ in self.calls])
        with store.Store(fixtures.REPO, config=self.config) as db:
            self.assertEqual([], db.events())
            self.assertIn("observed_evidence", {row["reason"] for row in db.pending()})

    def test_a_result_citing_only_its_output_is_reviewed_with_its_matched_request(self):
        # The selector cites the result of a Bash call; the call is found by matching ids in the transcript, and the
        # reviewer sees it beside the result.
        rows, candidate = self.tool_pair(command="python bench/run.py --repeat 5 --probe MATCHED-CALL")
        candidate["sources"] = [rows[1]["source_id"]]
        report = self.run_worker(self.invoke(candidate))
        self.assertEqual(["extract", "review"], [purpose for purpose, _ in self.calls])
        self.assertEqual(1, len(report["ready"]))
        reviewed = {item["id"]: item for item in self.calls[1][1]["sources"]}
        self.assertEqual([rows[1]["source_id"], rows[0]["source_id"]], list(reviewed))
        self.assertIn("MATCHED-CALL", reviewed[rows[0]["source_id"]]["text"])

    def test_a_result_resting_on_a_shown_file_or_a_printed_line_is_not_an_observed_run(self):
        for command in ("cat results.txt", "curl -s http://bench.example.test/notes", "echo 4 tests pass"):
            with self.subTest(command=command):
                self.calls = []
                rows, candidate = self.tool_pair(command=command, output="4 tests pass in " + command)
                candidate["sources"] = [rows[1]["source_id"]]
                report = self.run_worker(self.invoke(candidate))
                self.assertEqual(([], ["extract"]), (report["ready"], [purpose for purpose, _ in self.calls]))

    def test_changed_request_is_deferred_without_either_model(self):
        rows, candidate = self.tool_pair()
        original = sources.resolve

        def resolve(descriptor, project):
            if descriptor["source_id"] == rows[0]["source_id"]:
                raise sources.SourceError("source-changed")
            return original(descriptor, project)

        with mock.patch.object(sources, "resolve", side_effect=resolve):
            report = self.run_worker(self.invoke(candidate))
        self.assertEqual([], report["ready"])
        self.assertEqual([], self.calls)
        with store.Store(fixtures.REPO, config=self.config) as db:
            self.assertEqual("source_unavailable", db.observation(rows[1]["source_id"])["reason"])

    def test_restricted_dictionary_keys_and_original_tabs_are_checked(self):
        self.assertTrue(worker.source_privacy_problem({"nested": {"PRIVATE-SAMPLE": "value"}},
                        {"restricted_patterns": ["^PRIVATE-SAMPLE$"]}))
        self.assertTrue(worker.source_privacy_problem({"input": {"command": "PRIVATE\tSAMPLE"}},
                        {"restricted_patterns": [r"PRIVATE\s+SAMPLE"]}))


if __name__ == "__main__":
    unittest.main()
