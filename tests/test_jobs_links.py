"""Tests of the observer's sub-issue and board steps run for real (1.5.1): steps 5 and 6 of observer_jobs.run call
sub_issues.main and board.apply_for, which test_jobs_jobs.py replaces with mocks. Here nothing is replaced: a task is opened
through the publisher, the housekeeping runs, and the fake GitHub (tests/fake_gh.py, with tests/fake_projects.py for the
GraphQL sub-issue and Projects calls) says what was written. Nothing reaches GitHub."""

import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_jobs_publisher as tp  # noqa: E402
import test_jobs_tasks as tj  # noqa: E402
import test_scripts as ts  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import board_rules  # noqa: E402
import observer_jobs as jobs  # noqa: E402
import observer_runtime as runtime  # noqa: E402
import observer_store as store_module  # noqa: E402
import recortex  # noqa: E402
import sub_issues  # noqa: E402

REPO = ts.REPO
TOPIC = 5       # the example record's topic that a task opens under
OTHER = 6       # another topic of the record
STEPS = ("sync", "render", "close", "hub", "links", "board")   # the steps this file looks at (others may be added)
FIVE = ("Run the benchmark", "Write the table", "Plot the curve", "Check the units", "Post the note")


class LinksBox(tp.PublisherBox):
    """The example record with the observer approved at the level Everything, `jobs()` to run the housekeeping once and
    `open_task()` to open a task through the publisher, as the worker does."""

    def jobs(self, level="everything", mode="automatic", posted=True, **limits):
        self.use(level=level, mode=mode, **limits)
        options = runtime.settings(self.pcfg)
        with store_module.Store(REPO, config=self.pcfg) as db:
            steps = jobs.run(REPO, self.pcfg, db, options, refresh=lambda: True, posted=posted)
        if "code" not in steps:   # a step tells the user nothing but one of the fixed words
            self.assertTrue(all(name in steps for name in STEPS), steps)
            self.assertTrue(all(steps[name] in jobs.CODES for name in STEPS), steps)
        return steps

    def log_end(self):
        """Where the fake's log ends now: what a later call adds is `self.since(this)`."""
        return len(self.repo().get("log", []))

    def since(self, mark):
        return self.repo().get("log", [])[mark:]

    def graphql(self, mark):
        return [" ".join(call) for call in self.since(mark) if tuple(call[:2]) == ("api", "graphql")]

    def mutations(self, mark, name=""):
        return [text for text in self.graphql(mark) if "mutation" in text and name in text]

    def board_calls(self, mark):
        return [text for text in self.graphql(mark) if "projectv2" in text.lower()]

    def writes(self, mark=0):
        """The calls of the fake since `mark` that changed something on GitHub."""
        return [call for call in self.since(mark)
                if tuple(call[:2]) in (("issue", "edit"), ("issue", "close"), ("issue", "reopen"), ("issue", "comment"),
                                       ("issue", "create"))
                or (tuple(call[:2]) == ("api", "graphql") and "mutation" in " ".join(call))]

    def acts(self, kinds=None):
        with store_module.Store(REPO, config=self.pcfg) as db:
            return db.acts(days=1, kinds=kinds)

    def control(self, name, default=None):
        with store_module.Store(REPO, config=self.pcfg) as db:
            return db.get_control(name, default)

    def parent_of(self, number):
        return self.repo()["issues"][str(number)].get("parent")

    def touch(self, number, **fields):
        """Change an issue in the fake as someone on GitHub would, and move its updated_at on, so that the next sync reads
        it again (the fake's own link does not)."""
        self.touches = getattr(self, "touches", 0) + 1
        data = self.repo()
        data["issues"][str(number)].update(fields, updated_at="2026-06-01T00:%02d:00Z" % self.touches)
        self.save(data)

    def open_task(self, title="Task: write the benchmark note", steps=("Run the benchmark", "Write the table"),
                  linked=False):
        """Open a task under the topic through the publisher (the act `task_opened` is logged as in a real pass), with
        GitHub refusing the link unless `linked`, so that it has no parent: the step has something to do. Returns the
        number, with the index read again."""
        key, _ = self.task_event(title=title, steps=steps)
        with mock.patch.dict(os.environ, {} if linked else {"FAKE_GH_SUBISSUES": "refuse"}):
            out = self.pub(key)
        self.assertEqual("verified", out["state"], out)
        self.ready()
        self.receipt = out["receipt"]
        return out["receipt"]["issue"]


class SubIssueStepTests(LinksBox):
    def test_a_task_the_observer_opened_is_linked_under_its_topic_and_read_back(self):
        number = self.open_task()
        self.assertIsNone(self.parent_of(number))
        self.assertEqual(["task_opened"], [row["kind"] for row in self.acts(["task_opened"])])
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("ok", steps["links"])
        self.assertEqual(TOPIC, self.parent_of(number))
        added = self.mutations(mark, "addSubIssue")
        self.assertEqual(1, len(added), added)
        self.assertIn('issueId: "I_%d", subIssueId: "I_%d"' % (TOPIC, number), added[0])
        # read back through the fake's own GraphQL, and through the index
        ids, parents = sub_issues.read_parents(REPO, [number])
        self.assertEqual(TOPIC, parents[number])
        self.touch(number)
        self.ready()
        self.assertEqual([(number, TOPIC)], self.sql("SELECT issue, parent FROM parents WHERE issue = %d" % number))
        self.assertEqual([], [f for f in self.query("consistency") if f["kind"] == "sub-issue"])
        self.assertGreater(self.control(jobs.LINKS_LAST, 0), 0)

    def test_the_links_the_record_already_has_are_not_written_again(self):
        number = self.open_task()
        mark = self.log_end()
        self.jobs()
        self.assertEqual([4, 4, 5], [self.parent_of(n) for n in (5, 6, 7)])
        self.assertEqual(1, len(self.mutations(mark)), self.mutations(mark))   # only the new task's
        self.assertEqual(TOPIC, self.parent_of(number))

    def test_a_second_run_makes_no_link_and_no_write(self):
        number = self.open_task()
        self.jobs()
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("nothing", steps["links"])
        self.assertEqual([], self.graphql(mark))   # the step did not even read the parents
        self.assertEqual([], self.writes(mark))
        self.assertEqual(TOPIC, self.parent_of(number))

    def test_a_task_opened_later_is_linked_and_the_first_one_is_left_as_it_was(self):
        first = self.open_task()
        self.jobs()
        second = self.open_task(title="Task: tag the release", steps=("Update the notes", "Tag it"))
        self.assertNotEqual(first, second)
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("ok", steps["links"])
        added = self.mutations(mark, "addSubIssue")
        self.assertEqual(1, len(added), added)
        self.assertIn('subIssueId: "I_%d"' % second, added[0])
        self.assertEqual((TOPIC, TOPIC), (self.parent_of(first), self.parent_of(second)))

    def test_a_link_that_is_missing_for_a_task_nobody_opened_through_the_observer_is_not_the_steps_to_make(self):
        data = self.repo()
        data["issues"]["7"].pop("parent")
        self.save(data)
        self.touch(7)
        self.ready()
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("nothing", steps["links"])   # no act task_opened: the step has no reason to run
        self.assertEqual([], self.graphql(mark))
        self.assertIsNone(self.parent_of(7))
        self.assertIn("sub-issue", [f["kind"] for f in self.query("consistency")])   # the record still says it is missing

    def test_a_link_another_parent_holds_is_left_alone_and_the_consistency_check_reports_it(self):
        number = self.open_task()
        self.touch(number, parent=OTHER)   # someone put the task under another topic by hand
        self.ready()
        shown = io.StringIO()
        with contextlib.redirect_stdout(shown):
            self.assertEqual(0, sub_issues.main(["plan", "--repo", REPO]))
        self.assertIn("#%d under #%d: has another parent on GitHub (left alone)" % (number, TOPIC), shown.getvalue())
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("nothing", steps["links"])   # it ran and found nothing it may write: it does not say it linked
        self.assertGreater(self.control(jobs.LINKS_LAST, 0), 0)   # and it need not run again for this task
        self.assertEqual(OTHER, self.parent_of(number))
        self.assertEqual([], self.mutations(mark))
        found = [f for f in self.query("consistency") if f["kind"] == "sub-issue" and f["issue"] == number]
        self.assertEqual(1, len(found), found)
        self.assertIn("GitHub shows its parent as #%d" % OTHER, found[0]["detail"])
        self.assertEqual("nothing", self.jobs()["links"])

    def test_a_task_that_is_already_linked_is_nothing_to_link(self):
        number = self.open_task(linked=True)
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("nothing", steps["links"])
        self.assertEqual([], self.mutations(mark))
        self.assertGreater(self.control(jobs.LINKS_LAST, 0), 0)
        self.assertEqual(TOPIC, self.parent_of(number))

    def test_links_left_by_the_cap_of_one_run_are_made_by_the_next_runs(self):
        first = self.open_task()
        second = self.open_task(title="Task: tag the release", steps=("Update the notes", "Tag it"))
        with mock.patch.object(sub_issues, "LIMIT", 1):   # the cap of one run (60 in use), so that two tasks pass it
            steps = self.jobs()
            self.assertEqual("waiting", steps["links"])
            self.assertEqual([TOPIC, None], [self.parent_of(first), self.parent_of(second)])
            self.assertEqual(0, self.control(jobs.LINKS_LAST, 0))   # not kept: the next run goes on
            steps = self.jobs()
            self.assertEqual("ok", steps["links"])
            self.assertEqual([TOPIC, TOPIC], [self.parent_of(first), self.parent_of(second)])
            self.assertGreater(self.control(jobs.LINKS_LAST, 0), 0)
            mark = self.log_end()
            self.assertEqual("nothing", self.jobs()["links"])
        self.assertEqual([], self.graphql(mark))

    def test_a_github_that_refuses_the_link_costs_one_fixed_word_and_the_run_goes_on(self):
        number = self.open_task()
        shown = io.StringIO()
        with mock.patch.dict(os.environ, {"FAKE_GH_SUBISSUES": "refuse"}), contextlib.redirect_stdout(shown):
            steps = self.jobs()
        self.assertEqual("failed", steps["links"])
        self.assertEqual("", shown.getvalue())   # what sub_issues.py said stays with the script
        self.assertIsNone(self.parent_of(number))
        self.assertEqual("ok", steps["hub"])   # the steps before it were done
        self.assertEqual("nothing", steps["board"])   # and the one after it still ran
        last = self.control(jobs.JOBS_LAST)
        self.assertTrue(all(last["steps"][name] in jobs.CODES for name in STEPS), last)
        self.assertNotIn("refused", json.dumps(last))
        self.assertEqual(0, self.control(jobs.LINKS_LAST, 0))   # not marked as done: the next run tries again
        steps = self.jobs()
        self.assertEqual("ok", steps["links"])
        self.assertEqual(TOPIC, self.parent_of(number))

    def test_a_link_that_is_accepted_and_not_applied_is_a_failed_step_and_is_tried_again(self):
        number = self.open_task()
        with mock.patch.dict(os.environ, {"FAKE_GH_IGNORE_SUBISSUE": "1"}):
            steps = self.jobs()
        self.assertEqual("failed", steps["links"])
        self.assertIsNone(self.parent_of(number))
        self.assertEqual(0, self.control(jobs.LINKS_LAST, 0))
        self.assertEqual("ok", self.jobs()["links"])
        self.assertEqual(TOPIC, self.parent_of(number))

    def test_a_github_that_cannot_be_read_is_a_failed_step_and_writes_nothing(self):
        number = self.open_task()
        mark = self.log_end()
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL_ON": "api graphql"}):
            steps = self.jobs()
        self.assertEqual("failed", steps["links"])
        self.assertEqual([], self.mutations(mark))
        self.assertIsNone(self.parent_of(number))
        self.assertEqual("ok", self.jobs()["links"])


class BoardJobsBox(ts.BoardSandbox, LinksBox):
    """The same, with the private board of the project set up on this machine (board.py setup), as the user does."""

    def board_on(self):
        self.make_board()
        with open(os.path.join(self.project, recortex.CONFIG_NAME), encoding="utf-8") as handle:
            self.config = json.load(handle)
        self.assertEqual({"owner": "octo-org", "number": 1}, self.config["board"])

    def mark_steps(self, comment, steps):
        """A result on the topic that checks off `steps` of the steps memory `comment`."""
        self.touches = getattr(self, "touches", 0) + 1
        text = tj.result_text(marker=" done=%d:%s" % (comment, ",".join(map(str, steps))))
        self.put(comment + 1000 + self.touches, TOPIC, text, created="2026-06-02T00:%02d:00Z" % self.touches)
        self.ready()


class BoardStepTests(BoardJobsBox):
    def test_the_task_gets_its_card_in_the_right_column_with_its_step_count_in_the_why(self):
        number = self.open_task(steps=FIVE, linked=True)
        self.mark_steps(self.receipt["comment_id"], (1, 2, 3))
        self.board_on()
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("ok", steps["board"])
        self.assertEqual(("In Progress", "In Progress 3 of 5 steps"), self.cards()[number])
        self.assertEqual("In Progress", self.want()[number][0])   # the column the record puts it in
        self.assertEqual(("Todo", "Todo"), self.cards()[3])        # the cards of the others, as board.py apply makes them
        self.assertEqual(("In Progress", "In Progress 1 of 2 steps"), self.cards()[7])
        self.assertTrue(self.board_calls(mark))
        self.assertEqual([[len(self.cards())]], [row["numbers"] for row in self.acts(["board"])])
        with store_module.Store(REPO, config=self.pcfg) as db:
            lines = jobs.log_lines(db, REPO, self.pcfg, 1)
        self.assertRegex(lines[0], r"^act [0-9]+  .*  updated the board  numbers %d$" % len(self.cards()))

    def test_a_task_with_no_step_checked_says_so_in_its_why(self):
        number = self.open_task(steps=FIVE, linked=True)
        self.board_on()
        self.jobs()
        self.assertEqual(("In Progress", "In Progress 0 of 5 steps"), self.cards()[number])

    def test_a_second_run_writes_no_card(self):
        number = self.open_task(steps=FIVE, linked=True)
        self.board_on()
        self.jobs()
        before = dict(self.cards())
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("nothing", steps["board"])
        self.assertEqual([], self.mutations(mark))
        self.assertEqual(before, self.cards())
        self.assertEqual(1, len(self.acts(["board"])))

    def test_a_card_changes_with_the_record_and_only_that_card(self):
        number = self.open_task(steps=FIVE, linked=True)
        self.board_on()
        self.jobs()
        self.mark_steps(self.receipt["comment_id"], (1, 2, 3, 4))
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("ok", steps["board"])
        self.assertEqual(("In Progress", "In Progress 4 of 5 steps"), self.cards()[number])
        written = self.mutations(mark)
        self.assertEqual(1, len(written), written)   # the Why of one card: its column did not change
        self.assertIn('value: {text: "In Progress 4 of 5 steps"}', written[0])

    def test_a_card_the_user_moved_by_hand_stays_where_it_is(self):
        number = self.open_task(steps=FIVE, linked=True)
        self.board_on()
        self.jobs()
        self.moved_by_hand(number, to="Blocked")
        self.mark_steps(self.receipt["comment_id"], (1, 2, 3, 4))   # the record moves on and would change the Why
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("nothing", steps["board"])
        self.assertEqual([], self.mutations(mark))
        self.assertEqual("Blocked", self.cards()[number][0])
        self.assertEqual(1, len(self.acts(["board"])))   # the run that wrote the cards, and no other

    def test_a_run_that_stops_half_way_is_a_failed_step_and_the_next_run_finishes_the_cards(self):
        number = self.open_task(steps=FIVE, linked=True)
        self.board_on()
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL_MUTATION": "updateProjectV2ItemFieldValue"}):
            steps = self.jobs()
        self.assertEqual("failed", steps["board"])
        self.assertEqual([], self.acts(["board"]))
        self.assertEqual("ok", self.jobs()["board"])
        self.assertEqual(("In Progress", "In Progress 0 of 5 steps"), self.cards()[number])
        self.assertEqual("nothing", self.jobs()["board"])

    def test_one_run_writes_at_most_the_cap_and_the_rest_waits_for_the_next(self):
        self.open_task(steps=FIVE, linked=True)
        for planted in range(30, 30 + board_rules.WRITE_CAP + 2):
            self.pull(planted)
        self.board_on()
        self.ready()
        wanted = len(self.want())
        self.assertGreater(wanted, board_rules.WRITE_CAP)
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("waiting", steps["board"])
        self.assertEqual(board_rules.WRITE_CAP, len(self.cards()))
        self.assertEqual(board_rules.WRITE_CAP, len(self.mutations(mark, "addProjectV2ItemById")))
        self.assertEqual([[board_rules.WRITE_CAP]], [row["numbers"] for row in self.acts(["board"])])
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("ok", steps["board"])
        self.assertEqual(wanted, len(self.cards()))
        self.assertEqual(wanted - board_rules.WRITE_CAP, len(self.mutations(mark, "addProjectV2ItemById")))
        self.assertEqual("nothing", self.jobs()["board"])

    def test_a_project_with_no_board_makes_no_board_call(self):
        self.open_task(linked=True)
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("nothing", steps["board"])
        self.assertEqual([], self.board_calls(mark))
        self.assertEqual([], self.acts(["board"]))

    def test_a_board_the_user_did_not_approve_on_this_machine_is_not_touched(self):
        self.open_task(linked=True)
        self.board_on()
        self.config = dict(self.config, board={"owner": "octo-org", "number": 2})   # what a cloned project file may say
        mark = self.log_end()
        steps = self.jobs()
        self.assertEqual("failed", steps["board"])
        self.assertEqual([], self.board_calls(mark))
        self.assertEqual({}, self.cards())
        self.assertEqual([], self.acts(["board"]))

    def test_a_login_without_the_project_scope_is_a_failed_step_and_the_others_go_on(self):
        number = self.open_task()
        self.board_on()
        shown = io.StringIO()
        mark = self.log_end()
        with mock.patch.dict(os.environ, {"FAKE_GH_NO_PROJECT_SCOPE": "1"}), contextlib.redirect_stdout(shown):
            steps = self.jobs()
        self.assertEqual("failed", steps["board"])
        self.assertEqual("ok", steps["links"])   # the sub-issue link needs no such scope
        self.assertEqual(TOPIC, self.parent_of(number))
        self.assertEqual("", shown.getvalue())
        self.assertEqual({}, self.cards())
        self.assertEqual([], self.mutations(mark, "ProjectV2"))
        self.assertEqual([], self.acts(["board"]))
        self.assertEqual("ok", self.jobs()["board"])   # the scope came back: the next run writes the cards


class LevelTests(BoardJobsBox):
    """An unlinked task and a board that waits for its cards: only the level Everything writes either."""

    def test_assistant_and_notes_only_write_neither_a_link_nor_a_card(self):
        number = self.open_task()
        self.board_on()
        for level in ("assistant", "notes"):
            mark = self.log_end()
            steps = self.jobs(level=level)
            self.assertEqual(("level", "level"), (steps["links"], steps["board"]), level)
            self.assertEqual([], self.mutations(mark), level)
            self.assertEqual([], self.board_calls(mark), level)
            self.assertIsNone(self.parent_of(number), level)
            self.assertEqual({}, self.cards(), level)
        self.assertEqual([], self.acts(["board"]))
        self.assertEqual(0, self.control(jobs.LINKS_LAST, 0))
        steps = self.jobs(level="everything")   # the same record and board, one level up
        self.assertEqual(("ok", "ok"), (steps["links"], steps["board"]))
        self.assertEqual(TOPIC, self.parent_of(number))
        self.assertIn(number, self.cards())

    def test_the_shadow_mode_writes_nothing_at_any_level(self):
        number = self.open_task()
        self.board_on()
        mark = self.log_end()
        for level in ("notes", "assistant", "everything"):
            self.assertEqual({"code": "shadow"}, self.jobs(level=level, mode="shadow"), level)
        self.assertEqual([], self.since(mark))
        self.assertIsNone(self.parent_of(number))
        self.assertEqual({}, self.cards())
        self.assertEqual([], self.acts(["board"]))


if __name__ == "__main__":
    unittest.main()
