"""Human-labeled evaluation keeps absent measurements distinct from zero."""

import importlib.util
import json
import unittest
from pathlib import Path


path = Path(__file__).resolve().parents[1] / "examples" / "observer" / "score_handoffs.py"
spec = importlib.util.spec_from_file_location("score_handoffs", str(path))
scoring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoring)


class HandoffScoreTests(unittest.TestCase):
    def row(self, **changes):
        base = {"purpose": "coding", "case": "reader", "trial": "1", "arm": "ordinary",
                "facts": {"result": "retrieved", "decision": "queued", "scope": "missed"},
                "claims": [True, False], "advice": [], "correct": False, "seconds": 18,
                "interruptions": 0, "calls": 0, "tokens": 0}
        base.update(changes)
        return json.dumps(base)

    def test_stage_losses_visible_claims_and_unique_cases(self):
        rows = [self.row(), self.row(trial="2"), self.row(arm="delivered", advice=[True, False])]
        result = scoring.score(rows)["coding"]
        ordinary = result["ordinary"]
        self.assertEqual((1, 2), (ordinary["distinct_cases"], ordinary["trials"]))
        self.assertEqual(6, ordinary["meaningful_recall"]["total"])
        self.assertEqual(2, ordinary["meaningful_recall"]["count"])
        self.assertEqual(4, ordinary["fact_coverage"]["queued"]["count"])
        self.assertEqual(0.5, ordinary["recording_precision"]["rate"])
        self.assertIsNone(ordinary["advice_relevance"]["rate"])
        self.assertIsNone(result["shadow"])
        self.assertEqual(0.5, result["delivered"]["advice_relevance"]["rate"])

    def test_rejects_ambiguous_or_double_counted_trials(self):
        for bad in (self.row(claims=[1]), self.row(arm="shadow", advice=[True]),
                    self.row(facts={"decision": "maybe"}), self.row(seconds=-1)):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "line 1"):
                scoring.score([bad])
        with self.assertRaisesRegex(ValueError, "line 2: duplicate"):
            scoring.score([self.row(), self.row()])
