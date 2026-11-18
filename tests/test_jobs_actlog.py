"""Tests of the failed log of acts (1.5.1): a post whose act could not be written stands, counts one failure
(observer_store.note_act_failure), and is told in `observer.py status`, in `hooks.py --doctor` and, once a day while it keeps
failing, by one fixed notice at session start (observer_hooks). Nothing here reaches GitHub; the scripts run against
tests/fake_gh.py."""

import contextlib
import io
import json
import os
import re
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_jobs_jobs as tjj  # noqa: E402
import test_jobs_keeper as tk  # noqa: E402
import test_scripts as ts  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import keeper_rules  # noqa: E402
import observer_drafts  # noqa: E402
import observer_hooks as hooks  # noqa: E402
import observer_jobs as jobs  # noqa: E402
import observer_store as store_module  # noqa: E402

REPO = ts.REPO
DAY = 86400



class ActsFailedTests(tjj.JobsBox):
    """A post whose act could not be logged stands, counts one failure, and is told in status and --doctor."""

    def failing_log(self):
        return mock.patch.object(store_module.Store, "add_act", side_effect=store_module.StoreError("invalid_act"))

    def post(self):
        key, _ = self.result()
        with self.failing_log():
            return self.pub(key), key

    def failed(self):
        with store_module.Store(REPO, config=self.pcfg) as db:
            return db.acts_failed()

    def status(self):
        code, text = self.cli("status")
        self.assertEqual(0, code)
        return json.loads(text)

    def doctor(self):
        done = self.tool("hooks.py", "--doctor")
        return done.stdout

    def test_a_post_whose_act_could_not_be_logged_stands_and_is_counted_once(self):
        before = len(self.comments_on(5))
        done, key = self.post()
        self.assertEqual("verified", done["state"])
        self.assertEqual("verified", self.event(key)["state"])
        self.assertEqual(before + 1, len(self.comments_on(5)))
        self.assertIn(str(done["receipt"]["comment_id"]), self.repo()["comments"])
        self.assertEqual([], self.acts())
        self.assertEqual({"n": 1, "day": time.strftime("%Y-%m-%d", time.gmtime())}, self.failed())
        done, _ = self.post()
        self.assertEqual("verified", done["state"])
        self.assertEqual(2, self.failed()["n"])

    def test_a_post_whose_act_was_logged_counts_no_failure(self):
        key, _ = self.result()
        self.assertEqual("verified", self.pub(key)["state"])
        self.assertEqual(1, len(self.acts(["memory"])))
        self.assertIsNone(self.failed())

    def test_status_shows_the_count_and_the_day(self):
        with store_module.Store(REPO, config=self.pcfg):
            pass   # the store exists (status of one that was never made is the "empty" answer, which has no counts)
        found = self.status()
        self.assertEqual((0, ""), (found["acts_failed"], found["acts_failed_day"]))
        self.post()
        self.post()
        found = self.status()
        self.assertEqual(2, found["acts_failed"])
        self.assertRegex(found["acts_failed_day"], r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

    def test_doctor_prints_one_line_with_a_number_and_a_day_and_none_without_a_failure(self):
        self.assertNotIn("observer log:", self.doctor())
        self.post()
        self.post()
        lines = [line for line in self.doctor().splitlines() if line.startswith("observer log:")]
        self.assertEqual(1, len(lines), lines)
        self.assertRegex(lines[0], r"^observer log: the log of what the observer did could not be written 2 time\(s\), "
                                   r"the last on [0-9]{4}-[0-9]{2}-[0-9]{2} \(UTC\); the comments it posted stand in "
                                   r"GitHub\. observer\.py status shows the count\.$")

    def test_a_control_that_is_not_a_count_and_a_day_is_no_failure(self):
        with store_module.Store(REPO, config=self.pcfg) as db:
            for bad in ({"n": "3", "day": "2026-11-18"}, {"n": 0, "day": "2026-11-18"}, {"n": True, "day": "2026-11-18"},
                        {"n": 2, "day": "yesterday"}, {"n": 2}, {"n": 10 ** 10, "day": "2026-11-18"}, [2], "2", 2):
                db.set_control(store_module.ACTS_FAILED, bad)
                self.assertIsNone(db.acts_failed(), bad)
        self.assertEqual(0, self.status()["acts_failed"])
        self.assertNotIn("observer log:", self.doctor())

    def test_the_count_starts_again_when_the_last_failure_is_more_than_a_week_old(self):
        with store_module.Store(REPO, config=self.pcfg) as db:
            long_ago = time.time() - 10 * DAY
            db.note_act_failure(long_ago)
            db.note_act_failure(long_ago)
            self.assertEqual(2, db.acts_failed()["n"])
            db.note_act_failure()
            self.assertEqual(1, db.acts_failed()["n"])
            db.note_act_failure()
            self.assertEqual(2, db.acts_failed()["n"])

    def test_the_failed_count_cannot_fail_the_post_either(self):
        key, _ = self.result()
        with self.failing_log(), mock.patch.object(store_module.Store, "note_act_failure",
                                                    side_effect=store_module.StoreError("capacity")):
            done = self.pub(key)
        self.assertEqual("verified", done["state"])
        self.assertIsNone(self.failed())


class ActsFailedNoticeTests(tjj.HookBox):
    """The one fixed line at session start, at most once a day while the log keeps failing."""

    LINE = re.compile(r"recortex observer: the log of what it did could not be written ([0-9]+) time\(s\); "
                      r"run hooks\.py --doctor\.")

    def log_fails(self, times=1, now=None):
        with self.store() as db:
            for _ in range(times):
                db.note_act_failure(now)

    def said(self, out):
        return self.LINE.findall(out.get("systemMessage", ""))

    def test_the_line_is_fixed_words_and_a_whole_number_and_comes_once_a_day(self):
        self.assertEqual([], self.said(self.handle("session-start")))
        self.log_fails(3)
        out = self.handle("session-start")
        self.assertEqual(["3"], self.said(out))
        self.assertIn("recortex observer: the log of what it did could not be written 3 time(s); run hooks.py --doctor.",
                      out["systemMessage"])
        self.assertEqual("", self.context(out))   # a notice for the user, nothing for the model's context
        self.assertEqual([], self.said(self.handle("session-start")))
        self.assertEqual([], self.said(self.handle("session-start", session="session2")))   # the same day, another session
        self.assertEqual([], self.said(self.handle("prompt-submit", prompt="x")))   # at session start only
        self.log_fails(1)   # still failing, and still the same day
        self.assertEqual([], self.said(self.handle("session-start", session="session3")))
        with self.store() as db:
            self.assertEqual(time.strftime("%Y-%m-%d", time.gmtime()), db.get_control(store_module.ACTS_FAILED_TOLD))
            db.set_control(store_module.ACTS_FAILED_TOLD, "2000-01-01")   # the next day
        self.assertEqual(["4"], self.said(self.handle("session-start", session="session4")))

    def test_a_failure_that_stopped_a_week_ago_is_not_told(self):
        self.log_fails(2, time.time() - 8 * DAY)
        self.assertEqual([], self.said(self.handle("session-start")))
        self.log_fails(1)   # the last failure was over a week ago: the count began again at 1
        self.assertEqual(["1"], self.said(self.handle("session-start")))

    def test_a_control_that_is_not_a_count_and_a_day_says_nothing(self):
        for number, bad in enumerate(({"n": "3", "day": "2026-11-18"}, {"n": -1, "day": "2026-11-18"}, {"n": 2, "day": "x"},
                                      "3", [3])):
            with self.store() as db:
                db.set_control(store_module.ACTS_FAILED, bad)
            self.assertEqual([], self.said(self.handle("session-start", session="session-%d" % number)), bad)

    def test_the_line_names_no_title_and_holds_only_fixed_words_and_an_integer(self):
        self.log_fails(2)
        self.hold_task()
        out = self.handle("session-start")
        self.assertNotIn(self.HOSTILE, json.dumps(out))
        self.assertEqual(["2"], self.said(out))
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9 .,:;#()'\"_\n-]*", hooks.ACTS_FAILED % 2), hooks.ACTS_FAILED)


def failing_log():
    return mock.patch.object(store_module.Store, "add_act", side_effect=store_module.StoreError("invalid_act"))


class DraftActsFailedTests(tk.KeeperBox):
    """The acts of the paper's drafts (proposed, applied) are logged like the others: when the log cannot take one, the
    draft stands, the failure is counted once, and status and --doctor say so."""

    def failed(self):
        with self.open_store() as store:
            return store.acts_failed()

    def told(self):
        status = json.loads(self.cmd("observer.py", "status").stdout)
        doctor = [line for line in self.cmd("hooks.py", "--doctor").stdout.splitlines() if line.startswith("observer log:")]
        return status, doctor

    def propose(self):
        self.consider()
        with failing_log():
            done = self.consider(now=self.t0 + 31 * 60)
        self.assertEqual("drafted", done["code"], done)
        return done["draft"]

    def test_a_draft_proposed_stands_when_its_act_could_not_be_logged_and_the_failure_is_counted(self):
        ident = self.propose()
        (found,) = self.kept()
        self.assertEqual((ident, "waiting"), (found["id"], found["state"]))
        with self.open_store() as store:
            self.assertEqual([], store.acts(days=1))
            self.assertIsNotNone(keeper_rules.recorded(store.get_control(keeper_rules.CONTENTS, []), ident))
        self.assertEqual(1, self.failed()["n"])
        status, doctor = self.told()
        self.assertEqual(1, status["acts_failed"])
        self.assertEqual(1, len(doctor), doctor)
        self.assertIn("could not be written 1 time(s)", doctor[0])

    def test_a_draft_applied_stands_when_its_act_could_not_be_logged_and_the_failure_is_counted(self):
        self.consider()
        ident = self.consider(now=self.t0 + 31 * 60)["draft"]
        with self.open_store() as store:
            content = keeper_rules.recorded(store.get_control(keeper_rules.CONTENTS, []), ident)
            self.assertTrue(store.grant_answer(keeper_rules.grant_key(ident, content), "toolu_01approve"))
            before = len(store.acts(days=1))
            with failing_log(), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, observer_drafts.apply(store, ident, self.repo_name, self.project))
            self.assertEqual("applied", store.draft(ident)["state"])
            self.assertEqual(before, len(store.acts(days=1)))   # no act of the application was logged
        self.assertIn(tk.DRAFT_TEXT, self.body(5))   # the text is written
        self.assertEqual(1, self.failed()["n"])
        status, doctor = self.told()
        self.assertEqual(1, status["acts_failed"])
        self.assertEqual(1, len(doctor), doctor)


class JobsActsFailedTests(tjj.JobsBox):
    """The acts of the housekeeping (a list rendered, a task closed, the hub's list, the board) are logged like the others:
    the write was made, so a log that cannot take its act is counted and the step goes on."""

    def failed(self):
        with store_module.Store(REPO, config=self.pcfg) as db:
            return db.acts_failed()

    def test_a_rendered_task_stands_and_the_failed_log_is_counted_instead_of_stopping_the_step(self):
        with failing_log():
            steps = self.jobs()
        self.assertEqual("ok", steps["render"])
        self.assertIn("- [x] 1. Carry the quote state across physical lines", self.body(7))
        self.assertEqual([], self.acts(["task_rendered"]))
        self.assertGreaterEqual(self.failed()["n"], 1)

    def test_a_closed_task_stands_and_the_failed_log_is_counted(self):
        self.finish_task()
        with failing_log():
            steps = self.jobs()
        self.assertEqual("ok", steps["close"])
        self.assertEqual("closed", self.issue_state(7))
        self.assertEqual([], self.acts(["task_closed"]))
        self.assertGreaterEqual(self.failed()["n"], 1)

    def test_the_hub_and_the_board_acts_are_counted_when_they_cannot_be_logged(self):
        with mock.patch.object(jobs.board, "apply_for", return_value={"code": "ok", "written": 2, "waiting": 0}), \
                failing_log():
            steps = self.jobs()
        self.assertEqual(("ok", "ok"), (steps["hub"], steps["board"]))
        self.assertEqual([], self.acts(["hub_rendered", "board"]))
        self.assertGreaterEqual(self.failed()["n"], 3)   # a list, the hub's list and the board, at least


class LogActTests(tjj.HookBox):
    """Store.log_act: the id of the act, or None and one counted failure."""

    def test_it_returns_the_act_or_counts_the_failure_and_never_raises(self):
        with self.store() as db:
            self.assertIsInstance(db.log_act("memory", issue=5, comment=9101), int)
            self.assertIsNone(db.acts_failed())
            self.assertIsNone(db.log_act("not_a_kind"))   # the store refuses it
            self.assertEqual(1, db.acts_failed()["n"])
            with mock.patch.object(store_module.Store, "add_act", side_effect=RuntimeError("boom")), \
                    mock.patch.object(store_module.Store, "note_act_failure", side_effect=store_module.StoreError("full")):
                self.assertIsNone(db.log_act("memory", issue=5))   # not even a failed count raises
            self.assertEqual(1, db.acts_failed()["n"])


if __name__ == "__main__":
    unittest.main()
