"""The reminder about a changed decision runs inside hooks with a time limit: a record too large to check in time costs the
reminder, never the brief, and the hook says so (one fixed line, and an entry in hooks.py --doctor)."""

import contextlib
import io
import json
import os
import signal
import sqlite3
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from tests import test_scripts as ts
import decision_impact
import guidance
import hooks
import recortex
import storage
from examples.dashboard import preview


def forever(*args, **kwargs):
    """A stand-in for the projection of a record that is far too large: Python code that does not return in time. It
    gives up after 6 s with a failure of its own, so that a missing limit fails the test instead of hanging it."""
    stop = time.monotonic() + 6
    while time.monotonic() < stop:
        time.sleep(0.01)
        sum(range(1000))
    raise AssertionError("nothing stopped the projection")


class TimeLimitTests(unittest.TestCase):
    def tearDown(self):
        signal.setitimer(signal.ITIMER_REAL, 0)

    def test_slow_work_is_cut_off_at_the_limit_with_no_timer_left_behind(self):
        for work in (forever, lambda: time.sleep(30)):
            began = time.monotonic()
            with self.assertRaises(decision_impact.TooSlow):
                with decision_impact.time_limit(0.3):
                    work()
            self.assertLess(time.monotonic() - began, 3)
            self.assertEqual((0.0, 0.0), signal.getitimer(signal.ITIMER_REAL))
        with decision_impact.time_limit(5):   # quick work passes, and its timer is gone with it
            result = sum(range(1000))
        self.assertEqual(499500, result)
        self.assertEqual((0.0, 0.0), signal.getitimer(signal.ITIMER_REAL))

    def test_the_projection_itself_is_bounded_when_a_slow_stand_in_replaces_it(self):
        with mock.patch.object(decision_impact, "from_index", side_effect=forever):
            began = time.monotonic()
            with self.assertRaises(decision_impact.TooSlow):
                with decision_impact.time_limit(0.3):
                    decision_impact.from_index(None, "octo-org/octo-repo", {"octocat"})
        self.assertLess(time.monotonic() - began, 3)

    def test_a_long_query_is_interrupted_and_an_interrupt_that_the_work_swallowed_is_still_reported(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        slow = "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c WHERE n < 2000000000) SELECT count(*) FROM c"
        # No timer at all (a platform without SIGALRM): the query's own progress handler has to do it.
        for swallow in (False, True):
            began = time.monotonic()
            with mock.patch.object(decision_impact, "signal", types.SimpleNamespace()):
                with self.assertRaises(decision_impact.TooSlow):
                    with decision_impact.time_limit(0.3, db):
                        try:
                            db.execute(slow).fetchall()
                        except sqlite3.Error:
                            if not swallow:
                                raise
            self.assertLess(time.monotonic() - began, 5, swallow)
        self.assertEqual(1, db.execute("SELECT 1").fetchone()[0])   # the connection is usable, with no handler left

    def test_a_stand_in_for_a_connection_with_no_progress_hook_is_left_alone(self):
        with decision_impact.time_limit(5, object()):   # tests give the hooks bare stand-ins for the index connection
            self.assertEqual(3, 1 + 2)
        with self.assertRaises(decision_impact.TooSlow):
            with decision_impact.time_limit(0.3, object()):
                forever()

    def test_a_timer_the_caller_set_is_kept_and_armed_again(self):
        called = []
        theirs = lambda *args: called.append(1)  # noqa: E731
        previous = signal.signal(signal.SIGALRM, theirs)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        signal.setitimer(signal.ITIMER_REAL, 2.0)
        began = time.monotonic()
        with self.assertRaises(decision_impact.TooSlow):
            with decision_impact.time_limit(60):   # given no more than half of the 2 s that were left
                time.sleep(30)
        used = time.monotonic() - began
        self.assertTrue(0.5 < used < 1.6, used)
        left, _ = signal.getitimer(signal.ITIMER_REAL)
        self.assertTrue(0 < left < 2.0 - used + 0.2, left)   # the caller's timer runs on, shortened by what was used
        self.assertEqual([], called)
        self.assertIs(theirs, signal.getsignal(signal.SIGALRM))


class ReminderHookTests(unittest.TestCase):
    """A project with a changed decision and an active task, so that the return pointer has something to work out."""

    def setUp(self):
        self.case = ts.MindSandbox()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        tests = str(Path(__file__).resolve().parent)
        path = mock.patch.object(sys, "path", [tests] + sys.path)
        path.start()
        self.addCleanup(path.stop)
        preview.add_decision_change(self.case, "software")
        preview.add_project_map(self.case, "software")
        self.case.ready()
        self.case.in_process(extra={"CLAUDE_PROJECT_DIR": self.case.project, "RECORTEX_NOTICE": None})
        self.config = recortex.load_config()
        self.repo = self.config["repo"]
        self.hub = self.config["hub"]

    def produce(self, event="session-start", session="s1"):
        with mock.patch.object(hooks, "pointer_seconds", return_value=0.3):
            return hooks.produce(event, {"session_id": session}, 10, self.config, self.repo, self.hub)

    def doctor(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hooks.doctor()
        return out.getvalue()

    def test_in_time_the_pointer_is_shown_and_nothing_is_said_about_time(self):
        with mock.patch.object(hooks, "pointer_seconds", return_value=30):
            text = hooks.produce("session-start", {"session_id": "s1"}, 10, self.config, self.repo, self.hub)
        self.assertIn("Open judgment:", text)
        self.assertNotIn(guidance.SKIPPED, text)
        self.assertFalse(hooks.LAST["pointer_skipped"])
        self.assertIn("decision reminders: no hook has left them out for lack of time", self.doctor())

    def test_session_start_keeps_its_brief_and_says_the_reminder_was_left_out(self):
        with mock.patch.object(decision_impact, "from_index", side_effect=forever):
            began = time.monotonic()
            text = self.produce()
        self.assertLess(time.monotonic() - began, 8)
        self.assertIn("===== BEGIN PROJECT STATUS DATA", text)   # the brief
        self.assertIn("===== END PROJECT STATUS DATA", text)
        self.assertNotIn("Open judgment:", text)
        self.assertEqual(1, text.count(guidance.SKIPPED))
        self.assertTrue(hooks.LAST["pointer_skipped"])
        self.assertGreater(text.index(guidance.SKIPPED), text.index("===== END PROJECT STATUS DATA"))   # outside the frame
        shown = self.doctor()
        self.assertIn("decision reminders: the session-start hook left them out on ", shown)
        self.assertIn("longer than 0.3 s", shown)
        self.assertNotIn("no hook has left them out", shown)

    def test_the_first_prompt_of_a_session_without_a_brief_does_the_same(self):
        with mock.patch.object(decision_impact, "from_index", side_effect=forever):
            text = self.produce("prompt-submit", "s2")
        self.assertIn("===== BEGIN PROJECT STATUS DATA", text)
        self.assertEqual(1, text.count(guidance.SKIPPED))
        self.assertIn("decision reminders: the prompt-submit hook left them out", self.doctor())

    def test_the_user_sees_the_line_beside_the_status_line_and_the_model_in_the_context(self):
        stdin, out = io.StringIO(json.dumps({"session_id": "s3", "source": "startup"})), io.StringIO()
        with mock.patch.object(decision_impact, "from_index", side_effect=forever), \
                mock.patch.object(hooks, "pointer_seconds", return_value=0.3), \
                mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(out):
            self.assertEqual(0, hooks.main(["session-start"]))
        printed = json.loads(out.getvalue())
        self.assertTrue(printed["systemMessage"].endswith("\n" + guidance.SKIPPED), printed["systemMessage"])
        self.assertIn(guidance.SKIPPED, printed["hookSpecificOutput"]["additionalContext"])

    def test_a_note_that_cannot_be_written_does_not_stop_the_line(self):
        with mock.patch.object(decision_impact, "from_index", side_effect=forever), \
                mock.patch.object(guidance.storage, "write_file", side_effect=OSError("disk full")):
            self.assertIn(guidance.SKIPPED, self.produce())

    def test_a_damaged_note_is_reported_without_being_echoed(self):
        folder = storage.folder(storage.state_top(), storage.repo_folder(self.repo.lower()))
        for text in ("{not json", json.dumps({"at": 1, "limit": 1.0, "source": "<script>"}),
                     json.dumps({"at": 1, "limit": 1.0, "source": "observer", "more": 1}), json.dumps([1])):
            storage.write_file(os.path.join(folder, guidance.SKIP_FILE), text)
            line, = guidance.skip_lines(self.repo)
            self.assertIn("not in the form this version writes", line)
            self.assertNotIn("script", line)

    def test_the_time_given_follows_the_hooks_budget(self):
        self.assertEqual(2.0, hooks.pointer_seconds(10, "session-start"))
        self.assertEqual(1.0, hooks.pointer_seconds(10, "prompt-submit"))
        self.assertEqual(0.4, hooks.pointer_seconds(2, "session-start"))   # a fifth of a short budget
        self.assertEqual(0.2, hooks.pointer_seconds(1, "prompt-submit"))   # never nothing
        self.assertLess(hooks.pointer_seconds(15, "session-start"), 15 / 3)


if __name__ == "__main__":
    unittest.main()
