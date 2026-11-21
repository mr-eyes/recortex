"""A quiet, partial, or invalid model run cannot count as successful maintenance."""
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
TRIAL = runpy.run_path(str(ROOT / "examples/observer/map_trial.py"))


class MapTrialTests(unittest.TestCase):
    def test_return_trials_recover_the_governing_source_and_deliver_only_in_the_visible_arm(self):
        runner = runpy.run_path(str(ROOT / "examples/observer/map_returns.py"))
        for case in runner["CASES"]:
            with self.subTest(domain=case[0]):
                _, arms = runner["packets"](case)
                self.assertEqual(arms["ordinary"], arms["shadow"])
                self.assertTrue({51, 52} <= set(arms["delivered"]["selected"]))
                self.assertTrue(arms["delivered"]["advisory"])
                self.assertFalse(arms["ordinary"]["advisory"])

    def test_cases_and_answers_are_disjoint_and_complete(self):
        cases = json.loads((ROOT / "examples/observer/map_cases.json").read_text())
        answers = json.loads((ROOT / "examples/observer/map_labels.json").read_text())
        self.assertEqual(set(answers), {c["case"] for c in cases})
        self.assertTrue(all("actions" not in c for c in cases))
        self.assertEqual({"software", "research", "mixed"}, {c["domain"] for c in cases})

    def test_missed_changes_and_forbidden_automatic_restores_count(self):
        def row(case, change=None, routine=False):
            return {"case": case, "domain": "mixed", "calls": [{"ok": True, "usage": {}}],
                    "status": "shadow" if change else "quiet", "change": change, "routine": routine}
        rows = [row("wanted"), row("quiet"), row("restore", {"action": "restore", "id": "paint"}, True)]
        labels = {"wanted": {"actions": ["add"]}, "quiet": {"actions": ["none"]},
                  "restore": {"actions": ["restore"], "review": True}}
        counts = TRIAL["score"](labels, rows)["domains"]["mixed"]
        self.assertEqual(1, counts["missed"])
        self.assertEqual(1, counts["unauthorized"])
        self.assertEqual(0, counts["correct_automatic"])
        with self.assertRaises(ValueError):
            TRIAL["score"](labels, rows[:-1])
        rows[0]["calls"] = [{"ok": False}]
        with self.assertRaises(ValueError):
            TRIAL["score"](labels, rows)
