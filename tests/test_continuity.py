"""One end-to-end offline check of the published continuity demonstration."""

import importlib.util
from pathlib import Path
import unittest


class ContinuityTests(unittest.TestCase):
    def test_record_survives_a_fresh_environment_and_unposted_note_does_not(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "continuity" / "run.py"
        spec = importlib.util.spec_from_file_location("continuity_demo", path)
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        report = demo.run_demo()
        self.assertEqual(9, len(report["checks"]))
        self.assertFalse(report["claude_evaluated"])
        self.assertIsNone(report["token_usage"])
