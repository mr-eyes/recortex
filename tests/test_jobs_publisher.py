"""Tests of the publisher of 1.5.0: the marks and plan links composed into the marker by code, the events that wait for
the task another event opens, opening a task, the caps and the levels. Nothing here reaches GitHub; the scripts run
against tests/fake_gh.py. (The format and the rules of tasks are in test_jobs_tasks.py, which this file builds on.)"""

import datetime
import hashlib
import json
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_jobs_tasks as tj  # noqa: E402
import test_scripts as ts  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import observer_holds  # noqa: E402
import observer_publisher as publisher  # noqa: E402
import observer_runtime as runtime  # noqa: E402
import observer_sources  # noqa: E402
import observer_store as store  # noqa: E402
import record_format  # noqa: E402
import tasks  # noqa: E402

REPO = ts.REPO
STEPS2 = 9120   # a second steps memory on the task (#7), after the first (9108) and the result (9109)


def candidate_for(kind, issue, body, sources, **extra):
    return dict({"kind": kind, "issue": issue, "body": body, "sources": list(sources)}, **extra)


class PublisherBox(tj.TaskBox):
    """The example record with its task (#7: steps memory 9108 with two steps, step 1 checked by 9109), the observer
    approved for it at the level Everything, and helpers to queue reviewed events and publish them."""

    def setUp(self):
        super().setUp()
        self.in_process(extra={"CLAUDE_PROJECT_DIR": self.project})
        gate = mock.patch.object(runtime, "gate_valid", return_value=True)
        gate.start()
        self.addCleanup(gate.stop)
        self.sources = 0
        self.use()

    # -- the observer's settings for this folder
    def use(self, level="everything", mode="automatic", **limits):
        self.pcfg = dict(self.config, observer=dict(enabled=True, mode=mode, level=level,
                                                     publisher_id=runtime.publisher_id(create=True), **limits))
        runtime.approve(REPO, self.pcfg)

    def metadata(self, source):
        return {"source_id": source, "session_id": "session1", "role": "assistant", "event_type": "stop",
                "path": "/example/session.jsonl", "offset": 0, "end": 10,
                "sha256": hashlib.sha256(b"source").hexdigest(), "observed_at": 1234}

    def queue(self, kind, issue, body, review=None, **extra):
        """A reviewed event in the queue: returns (its key, its first source)."""
        self.sources += 1
        source = "source%d" % self.sources
        with store.Store(REPO, config=self.pcfg) as db:
            db.capture([self.metadata(source)])
            candidate = candidate_for(kind, issue, body, [source], **extra)
            ident = db.reserve_invocation("review")
            db.complete_invocation(ident, True, output_hash=store.candidate_hash(candidate))
            key = db.review_complete(source, candidate, dict({"reservation_id": ident, "accepted": True,
                                                              "candidate_hash": store.candidate_hash(candidate)},
                                                             **(review or {})))
        return key, source

    def event(self, key):
        with store.Store(REPO, config=self.pcfg) as db:
            return db.event(key)

    def pub(self, key, **kwargs):
        return publisher.publish(REPO, key, self.pcfg, **kwargs)

    def task_event(self, title="Task: write the benchmark note", topic=5, steps=("Run the benchmark", "Write the table"),
                   **extra):
        body = tj.steps_text(at="2026-03-13T09:00-08:00", plain="write the benchmark note in two steps", steps=steps,
                             done="The note is posted.")
        return self.queue("steps", 0, body, task_title=title, topic=topic, **extra)

    def result(self, issue=5, marks=None, **extra):
        return self.queue("result", issue, tj.result_text(), **dict(extra, **({"marks": marks} if marks else {})))

    def comments_on(self, number):
        return [(cid, c["body"]) for cid, c in sorted(self.repo()["comments"].items(), key=lambda row: int(row[0]))
                if c["issue"] == number]

    def titled(self, title):
        return [int(n) for n, i in self.repo()["issues"].items() if i["title"] == title]

    def marker(self, text):
        return record_format.parse_marker(record_format.first_lines(text)[0])

    def second_steps(self):
        """A second steps memory on the task, numbered 3 and 4 after the first one's 1 and 2."""
        self.post(STEPS2, 7, tj.steps_text(at="2026-03-14T09:00-08:00", plain="finish the note and tag it",
                                           agreed="By the project lead in the chat, 2026-03-14 09:00 PST:\n\n> Yes, add them.",
                                           steps=("Update the known-issues text", "Tag the release"),
                                           done="The tag is pushed."))


class SchemaTests(PublisherBox):
    """The fields 1.5.0 adds to a reviewed candidate are strict."""

    def refused(self, **change):
        body = tj.result_text()
        candidate = dict(candidate_for("result", 5, body, ["sourceX"]), **change)
        with store.Store(REPO, config=self.pcfg) as db:
            db.capture([self.metadata("sourceX")])
            ident = db.reserve_invocation("review")
            db.complete_invocation(ident, True, output_hash=store.candidate_hash(candidate))
            with self.assertRaisesRegex(store.StoreError, "invalid_candidate"):
                db.review_complete("sourceX", candidate, {"reservation_id": ident, "accepted": True,
                                                          "candidate_hash": store.candidate_hash(candidate)})

    def test_marks_and_links_have_their_forms(self):
        for marks in ([{"task": 7}], [{"task": 7, "steps": []}], [{"task": 7, "steps": [0]}],
                      [{"task": 7, "steps": [21]}], [{"task": 7, "steps": [1, 1]}], [{"task": -1, "steps": [1]}],
                      [{"task": 7, "steps": [1], "extra": 1}], "7:1", [{"task": 7, "steps": [1]}] * 7,
                      [{"task": True, "steps": [1]}]):
            self.refused(marks=marks)
        for links in ([{"key": "because", "part": 3}], [{"key": "checks"}], [{"key": "checks", "part": 0}],
                      [{"key": "checks", "part": "9302"}], [{"key": "against", "part": 3}] * 5, "9302"):
            self.refused(links=links)

    def test_issue_zero_is_for_a_new_task_only(self):
        self.refused(issue=0)
        self.refused(issue=-1)
        self.refused(task_title="Task: x", topic=5)           # a title belongs to a steps memory that opens a task
        self.refused(with_="x")

    def test_a_task_that_opens_names_its_title_and_topic(self):
        body = tj.steps_text()
        for extra in ({}, {"task_title": "Task: x"}, {"topic": 5}, {"task_title": "", "topic": 5},
                      {"task_title": "a\nb", "topic": 5}, {"task_title": "x" * 121, "topic": 5},
                      {"task_title": "Task: x", "topic": "5"}):
            self.sources += 1
            source = "source%d" % self.sources
            candidate = candidate_for("steps", 0, body, [source], **extra)
            with store.Store(REPO, config=self.pcfg) as db:
                db.capture([self.metadata(source)])
                ident = db.reserve_invocation("review")
                db.complete_invocation(ident, True, output_hash=store.candidate_hash(candidate))
                with self.assertRaisesRegex(store.StoreError, "invalid_candidate"):
                    db.review_complete(source, candidate, {"reservation_id": ident, "accepted": True,
                                                           "candidate_hash": store.candidate_hash(candidate)})
        key, _ = self.task_event()
        self.assertEqual("ready", self.event(key)["state"])

    def test_a_checkpoint_on_issue_zero_must_mark_task_zero(self):
        self.sources += 1
        source = "source%d" % self.sources
        for marks in ([], [{"task": 7, "steps": [2]}]):
            candidate = candidate_for("checkpoint", 0, tj.result_text().replace("type=result", "type=checkpoint"),
                                      [source], **({"marks": marks} if marks else {}))
            with store.Store(REPO, config=self.pcfg) as db:
                db.capture([self.metadata(source)]) if db.observation(source) is None else None
                ident = db.reserve_invocation("review")
                db.complete_invocation(ident, True, output_hash=store.candidate_hash(candidate))
                with self.assertRaisesRegex(store.StoreError, "invalid_candidate"):
                    db.review_complete(source, candidate, {"reservation_id": ident, "accepted": True,
                                                           "candidate_hash": store.candidate_hash(candidate)})

    def test_a_1_4_0_candidate_keeps_its_hash(self):
        """Fields that are absent change nothing: a candidate of the old five keys is stored and hashed as before."""
        key, _ = self.result(marks=None)
        event = self.event(key)
        self.assertEqual({"kind", "issue", "body", "sources"}, set(event["candidate"]))
        self.assertEqual(event["body"], publisher.compose(event, None))


class ComposeTests(PublisherBox):
    """compose: a pure function of the reviewed candidate and the index."""

    def composed(self, key):
        event = self.event(key)
        import index
        db = index.connect(REPO, create=False)
        try:
            return publisher.compose(event, db, {"octocat"})
        finally:
            db.close()

    def test_a_mark_is_resolved_to_the_steps_comment_and_its_number(self):
        key, _ = self.result(marks=[{"task": 7, "steps": [2]}])
        body = self.composed(key)
        self.assertEqual("9108:2", self.marker(body)["done"])
        self.assertEqual(record_format.marks(self.marker(body)), [("done", 9108, 2)])
        self.assertTrue(body.startswith("<!-- ghim:event type=result at=2026-03-12T15:30-08:00 eventkey=%s done=9108:2 -->\n"
                                        % key))

    def test_compose_is_deterministic(self):
        key, _ = self.result(marks=[{"task": 7, "steps": [2]}])
        first = self.composed(key)
        self.assertEqual(first, self.composed(key))
        self.assertEqual(hashlib.sha256(first.encode()).hexdigest(), hashlib.sha256(self.composed(key).encode()).hexdigest())

    def test_each_mark_resolves_across_two_steps_memories(self):
        self.second_steps()
        key, _ = self.result(marks=[{"task": 7, "steps": [4, 2, 3]}])
        value = self.marker(self.composed(key))["done"]
        self.assertEqual("9108:2;%d:1,2" % STEPS2, value)
        one, _ = self.result(marks=[{"task": 7, "steps": [4]}])
        self.assertEqual("%d:2" % STEPS2, self.marker(self.composed(one))["done"])

    def test_a_mark_that_points_at_no_open_step_is_mark_binding(self):
        for marks in ([{"task": 7, "steps": [1]}],        # step 1 is checked by a told memory already
                      [{"task": 7, "steps": [3]}],        # the task has two steps
                      [{"task": 99, "steps": [1]}],       # no such task
                      [{"task": 5, "steps": [1]}],        # a topic, not a task
                      [{"task": 0, "steps": [1]}]):       # no task opened in this pass
            key, _ = self.result(marks=marks)
            with self.assertRaises(publisher.PublishError) as caught:
                self.composed(key)
            self.assertEqual("mark_binding", caught.exception.reason, marks)

    def test_a_closed_task_takes_no_mark(self):
        data = self.repo()
        data["issues"]["7"]["state"] = "closed"
        data["issues"]["7"]["closed_at"] = "2026-04-11T10:00:00Z"
        data["issues"]["7"]["updated_at"] = self.tick(data)
        self.save(data)
        self.ready()
        key, _ = self.result(marks=[{"task": 7, "steps": [2]}])
        with self.assertRaisesRegex(publisher.PublishError, "mark_binding"):
            self.composed(key)

    def test_marks_ride_only_on_a_result_a_failure_a_correction_or_a_checkpoint(self):
        key, _ = self.queue("decision", 5, tj.decision_text(), marks=[{"task": 7, "steps": [2]}])
        with self.assertRaisesRegex(publisher.PublishError, "mark_kind"):
            self.composed(key)
        key, _ = self.queue("idea", 5, record_format.parse_marker.__doc__ and tj.steps_text().replace(
            "type=steps", "type=idea"), marks=[{"task": 7, "steps": [2]}])
        with self.assertRaises(publisher.PublishError):
            self.composed(key)

    def test_a_model_never_writes_done_dropped_or_against(self):
        for text in (tj.with_mark(tj.result_text(), " done=9108:2"), tj.with_mark(tj.result_text(), " dropped=9108:2"),
                     tj.with_mark(tj.result_text(), " against=9302")):
            key, _ = self.queue("result", 5, text)
            with self.assertRaises(publisher.PublishError) as caught:
                self.composed(key)
            self.assertIn(caught.exception.reason, ("mark_binding", "part_binding"))

    def test_a_plan_link_is_written_into_the_marker_after_the_ones_a_model_wrote(self):
        key, _ = self.result(links=[{"key": "checks", "part": 9302}, {"key": "against", "part": 9303},
                                    {"key": "checks", "part": 9302}])
        marker = self.marker(self.composed(key))
        self.assertEqual(("9302", "9303"), (marker["checks"], marker["against"]))
        text = tj.with_mark(tj.result_text(), " checks=9104")
        key, _ = self.queue("result", 5, text, links=[{"key": "checks", "part": 9302}])
        self.assertEqual("9104,9302", self.marker(self.composed(key))["checks"])

    def test_a_plan_link_a_kind_may_not_carry_is_part_binding(self):
        key, _ = self.queue("failure", 5, tj.result_text().replace("type=result", "type=failure"),
                            links=[{"key": "against", "part": 9303}])
        with self.assertRaisesRegex(publisher.PublishError, "part_binding"):
            self.composed(key)
        key, _ = self.queue("correction", 5, tj.result_text().replace("type=result", "type=correction"),
                            links=[{"key": "checks", "part": 9302}])
        with self.assertRaisesRegex(publisher.PublishError, "part_binding"):
            self.composed(key)

    def test_more_than_four_ids_in_one_key_is_part_binding(self):
        text = tj.with_mark(tj.result_text(), " checks=9101,9102,9103,9104")
        key, _ = self.queue("result", 5, text, links=[{"key": "checks", "part": 9302}])
        with self.assertRaisesRegex(publisher.PublishError, "part_binding"):
            self.composed(key)

    def test_an_event_with_neither_marks_nor_links_is_its_stored_body(self):
        key, _ = self.result()
        self.assertEqual(self.event(key)["body"], self.composed(key))

    def test_against_joins_the_links_that_change_a_status(self):
        self.assertIn("against", publisher.STATUS_LINKS)
        body = tj.with_mark(tj.result_text(), " against=9302")
        self.assertTrue(publisher.status_change(body))
        self.assertFalse(publisher.status_change(tj.result_text()))


class MarkPublishTests(PublisherBox):
    """The composed body is what is frozen and posted, and what the record then shows as checked."""

    def test_a_mark_is_posted_and_the_step_is_checked_in_the_record(self):
        key, _ = self.result(marks=[{"task": 7, "steps": [2]}])
        out = self.pub(key)
        self.assertEqual("verified", out["state"], out)
        posted = self.comments_on(5)[-1][1]
        self.assertEqual("9108:2", self.marker(posted)["done"])
        self.assertEqual(self.event(key)["body"].replace("\r\n", "\n"), posted.replace("\r\n", "\n"))
        self.assertEqual(hashlib.sha256(self.event(key)["body"].encode()).hexdigest(), self.event(key)["payload_hash"])
        self.ready()
        step = self.task_at(7)["steps"][1]
        self.assertEqual(("done", out["receipt"]["comment_id"]), (step["state"], step["by"]["id"]))

    def test_a_dry_run_shows_the_composed_hash_and_posts_nothing(self):
        key, _ = self.result(marks=[{"task": 7, "steps": [2]}])
        before = len(self.log("issue comment"))
        out = self.pub(key, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertNotEqual(self.event(key)["payload_hash"], out["payload_hash"])
        self.assertEqual("ready", self.event(key)["state"])
        self.assertEqual(before, len(self.log("issue comment")))

    def test_a_mark_that_binds_nothing_defers_the_event_with_its_reason(self):
        key, _ = self.result(marks=[{"task": 7, "steps": [1]}])
        with self.assertRaisesRegex(publisher.PublishError, "mark_binding"):
            self.pub(key)
        self.assertIn("mark_binding", publisher.EVENT_PROBLEMS)
        self.assertEqual([], self.log("issue comment"))

    def test_against_without_status_authority_is_held_for_the_users_answer(self):
        key, _ = self.result(links=[{"key": "against", "part": 9303}])
        with self.assertRaisesRegex(publisher.PublishError, "status_change"):
            self.pub(key)
        self.assertEqual([], self.log("issue comment"))
        self.assertEqual(1, len([e for e in [self.event(key)] if "against=9303" in e["body"]]))

    def test_checks_without_status_authority_is_held_and_with_it_is_posted(self):
        """checks= backs a plan part: the publisher holds it as it holds against=, so the worker must give the authority
        its cited message gives (test_jobs_sort), or every backed claim waits for the user's answer."""
        key, _ = self.result(links=[{"key": "checks", "part": 9302}])
        with self.assertRaisesRegex(publisher.PublishError, "status_change"):
            self.pub(key)
        self.assertEqual([], self.log("issue comment"))
        key, _ = self.queue("result", 5, tj.result_text(), links=[{"key": "checks", "part": 9103}],   # the idea it tried
                            review={"status_authority": "agent"})
        self.assertEqual("verified", self.pub(key)["state"])

    def test_the_composed_body_is_frozen_once_it_is_sending(self):
        key, _ = self.result(marks=[{"task": 7, "steps": [2]}])
        out = self.pub(key)
        self.assertEqual("verified", out["state"])
        before = self.event(key)["body"]
        with store.Store(REPO, config=self.pcfg) as db:
            with self.assertRaisesRegex(store.StoreError, "invalid_transition"):
                db.recompose(key, before.replace("9108:2", "9108:1"))
        self.assertEqual(before, self.event(key)["body"])


class OpenTaskTests(PublisherBox):
    """A steps memory on issue 0 opens a task; marks of task 0 and a checkpoint on it wait for it."""

    def test_the_task_is_opened_with_its_steps_memory_and_the_receipt_names_the_issue(self):
        key, _ = self.task_event()
        out = self.pub(key)
        self.assertEqual("verified", out["state"], out)
        number = self.titled("Task: write the benchmark note")[0]
        self.assertEqual(number, out["receipt"]["issue"])
        issue = self.repo()["issues"][str(number)]
        self.assertEqual(["active"], issue["labels"])
        self.assertEqual(5, issue.get("parent"))
        self.assertIn("- [ ] 1. Run the benchmark", issue["body"].replace("\r\n", "\n"))
        first = self.comments_on(number)
        self.assertEqual(1, len(first))
        self.assertEqual(out["receipt"]["comment_id"], int(first[0][0]))
        self.assertEqual("steps", record_format.parse_event(first[0][1])["type"])
        self.assertIn(" eventkey=%s" % key, first[0][1].split("\n")[0])
        with store.Store(REPO, config=self.pcfg) as db:
            self.assertEqual(["task_opened"], [a["kind"] for a in db.acts()])
            self.assertEqual([2], db.acts()[0]["numbers"])

    def test_a_dry_run_names_the_command_and_opens_nothing(self):
        key, _ = self.task_event()
        out = self.pub(key, dry_run=True)
        self.assertIn("gh issue create", out["command"])
        self.assertEqual([], self.titled("Task: write the benchmark note"))

    def test_an_interrupted_open_is_taken_up_again_and_opens_no_second_task(self):
        key, _ = self.task_event(title="Task: write the second note")
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL_AFTER": "issue create"}):
            out = self.pub(key)
        self.assertEqual("uncertain", out["state"], out)
        self.assertEqual(1, len(self.titled("Task: write the second note")))
        out = self.pub(key)
        self.assertEqual("verified", out["state"], out)
        self.assertEqual(1, len(self.titled("Task: write the second note")))
        self.assertEqual(1, len(self.comments_on(out["receipt"]["issue"])))
        again = publisher.reconcile(REPO, key, self.pcfg)
        self.assertEqual("verified", again["state"])
        self.assertEqual(1, len(self.titled("Task: write the second note")))

    def test_an_interrupted_open_taken_up_again_minutes_later_opens_no_second_task(self):
        """The opening stamp in the body is a clock reading: a rerun in a later minute must keep the body the issue was
        created with, or create_issues.py finds no live issue with that body and opens another."""
        key, _ = self.task_event(title="Task: write the third note")
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL_AFTER": "issue create"}):
            out = self.pub(key)
        self.assertEqual("uncertain", out["state"], out)
        self.assertEqual(1, len(self.titled("Task: write the third note")))
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=7)
        with mock.patch.object(tasks, "now", return_value=later):
            out = self.pub(key)
        self.assertEqual("verified", out["state"], out)
        self.assertEqual(1, len(self.titled("Task: write the third note")))
        self.assertEqual(1, len(self.comments_on(out["receipt"]["issue"])))

    def test_a_task_with_no_topic_is_only_the_standing_to_do_list(self):
        key, _ = self.task_event(title="Task: no topic", topic=0)
        with self.assertRaisesRegex(publisher.PublishError, "invalid_target"):
            self.pub(key)
        self.assertEqual([], self.titled("Task: no topic"))
        key, _ = self.task_event(title="To do", topic=0, steps=("keep the first item",))
        self.assertEqual("verified", self.pub(key)["state"])

    def test_a_topic_that_is_a_pull_request_or_missing_is_refused(self):
        key, _ = self.task_event(topic=999)
        with self.assertRaises(Exception):
            self.pub(key)
        self.assertEqual([], self.titled("Task: write the benchmark note"))

    def test_a_mark_of_task_zero_waits_for_the_task_then_resolves_to_its_steps_memory(self):
        opener, source = self.task_event()
        marked, _ = self.result(marks=[{"task": 0, "steps": [1]}], **{"with": source})
        out = self.pub(marked)
        self.assertEqual("depends", out["waiting"], out)
        self.assertEqual(("ready", []), (self.event(marked)["state"], self.log("issue comment")))
        done = self.pub(opener)
        self.assertEqual("verified", done["state"])
        out = self.pub(marked)
        self.assertEqual("verified", out["state"], out)
        posted = self.comments_on(5)[-1][1]
        self.assertEqual("%d:1" % done["receipt"]["comment_id"], self.marker(posted)["done"])
        with store.Store(REPO, config=self.pcfg) as db:
            self.assertEqual(["step_marked", "task_opened"], sorted(a["kind"] for a in db.acts()))
        self.ready()
        task = self.task_at(done["receipt"]["issue"])
        self.assertEqual(["done", "open"], [step["state"] for step in task["steps"]])

    def test_the_dependent_resolves_in_the_same_order_the_worker_publishes(self):
        opener, source = self.task_event()
        marked, _ = self.result(marks=[{"task": 0, "steps": [2, 1]}], **{"with": source})
        with store.Store(REPO, config=self.pcfg) as db:
            keys = [event["eventkey"] for event in db.events(states=["ready"], limit=4)]
        self.assertEqual([opener, marked], keys)
        for key in keys:
            self.assertEqual("verified", self.pub(key)["state"])

    def test_without_a_with_the_mark_follows_the_task_opened_just_before_it(self):
        opener, _ = self.task_event()
        marked, _ = self.result(marks=[{"task": 0, "steps": [1]}])
        self.assertEqual("depends", self.pub(marked)["waiting"])
        self.pub(opener)
        self.assertEqual("verified", self.pub(marked)["state"])

    def test_a_mark_of_task_zero_beyond_the_steps_the_task_has_is_mark_binding(self):
        opener, source = self.task_event()
        self.pub(opener)
        marked, _ = self.result(marks=[{"task": 0, "steps": [3]}], **{"with": source})
        with self.assertRaisesRegex(publisher.PublishError, "mark_binding"):
            self.pub(marked)

    def test_a_mark_whose_task_was_dropped_is_deferred_with_mark_binding(self):
        opener, source = self.task_event()
        marked, _ = self.result(marks=[{"task": 0, "steps": [1]}], **{"with": source})
        with store.Store(REPO, config=self.pcfg) as db:
            db.transition(opener, "rejected")
        with self.assertRaisesRegex(publisher.PublishError, "mark_binding"):
            self.pub(marked)
        self.assertEqual([], self.log("issue comment"))

    def test_a_checkpoint_on_the_new_task_is_posted_on_it(self):
        opener, source = self.task_event()
        marked, _ = self.queue("checkpoint", 0, ts.CHECKPOINT_060, marks=[{"task": 0, "steps": [1]}],
                               **{"with": source})
        self.assertEqual("depends", self.pub(marked)["waiting"])
        done = self.pub(opener)
        out = self.pub(marked)
        self.assertEqual("verified", out["state"], out)
        number = done["receipt"]["issue"]
        self.assertEqual(2, len(self.comments_on(number)))
        self.assertEqual("%d:1" % done["receipt"]["comment_id"], self.marker(self.comments_on(number)[-1][1])["done"])
        self.assertEqual(out["receipt"]["comment_id"], int(self.comments_on(number)[-1][0]))


class WaitingTests(PublisherBox):
    """What waits for the user's answer: the task the level Assistant would open."""

    def test_the_level_assistant_holds_a_task_for_the_users_answer(self):
        self.use(level="assistant")
        key, _ = self.task_event()
        with self.assertRaisesRegex(publisher.PublishError, "level_ask"):
            self.pub(key)
        self.assertEqual([], self.titled("Task: write the benchmark note"))
        with store.Store(REPO, config=self.pcfg) as db:
            db.transition(key, "deferred", reason="level_ask")      # as the worker does with a problem of the event
            held = db.waiting()
        self.assertEqual(["task_open"], [item["kind"] for item in held])
        self.assertEqual((key[:8], "Task: write the benchmark note", 2), (held[0]["short"], held[0]["title"], held[0]["steps"]))
        question = publisher.task_open_question(held[0])
        self.assertEqual('Task %s: open a task "Task: write the benchmark note" with 2 steps?' % key[:8], question)
        self.assertEqual(("Open it", "Not now", "Drop it"), publisher.TASK_OPEN_OPTIONS)
        self.assertEqual("Task abcd1234: every step of #7 is done. Close it?", publisher.task_close_question("abcd1234", 7))
        self.assertEqual(("Close it", "Not now"), publisher.TASK_CLOSE_OPTIONS)
        self.assertIn("level_ask", observer_holds.HELD_REASONS)
        self.assertEqual(1, self.event(key)["held"])

    def test_the_plugins_task_questions_are_never_sources(self):
        for text in (publisher.task_open_question({"short": "0123abcd", "title": "Task: x", "steps": 3}),
                     publisher.task_close_question("0123abcd", 14)):
            self.assertTrue(observer_sources.OWN_DIALOG.search(text), text)

    def test_the_users_open_it_publishes_it_once_with_the_grant_the_answer_made(self):
        self.use(level="assistant")
        key, _ = self.task_event()
        with self.assertRaisesRegex(publisher.PublishError, "level_ask"):
            self.pub(key)
        with store.Store(REPO, config=self.pcfg) as db:
            db.transition(key, "deferred", reason="level_ask")
        with self.assertRaisesRegex(publisher.PublishError, "not_ready"):
            self.pub(key)                                   # the automatic publisher never opens it
        with self.assertRaisesRegex(publisher.PublishError, "held_unanswered"):
            self.pub(key, main_session=True)               # nor does the main session, without the user's answer
        self.assertEqual([], self.titled("Task: write the benchmark note"))
        with store.Store(REPO, config=self.pcfg) as db:
            self.assertTrue(db.grant_answer(key, "toolu_01open"))
        out = self.pub(key, main_session=True)
        self.assertEqual("verified", out["state"], out)
        self.assertEqual(1, len(self.titled("Task: write the benchmark note")))
        self.assertEqual("verified", self.pub(key, main_session=True)["state"])      # asked again: no second task
        self.assertEqual(1, len(self.titled("Task: write the benchmark note")))

    def test_a_mark_that_waits_for_a_held_task_leaves_the_ready_list_and_goes_on_when_it_opens(self):
        self.use(level="assistant")
        opener, source = self.task_event()
        marked, _ = self.result(marks=[{"task": 0, "steps": [1]}], **{"with": source})
        with self.assertRaises(publisher.PublishError):
            self.pub(opener)
        with store.Store(REPO, config=self.pcfg) as db:
            db.transition(opener, "deferred", reason="level_ask")
        out = self.pub(marked)
        self.assertEqual("depends", out["waiting"])
        self.assertEqual(("deferred", "depends"), (self.event(marked)["state"], self.event(marked)["reason"]))
        self.assertEqual(0, self.event(marked)["held"])
        with store.Store(REPO, config=self.pcfg) as db:
            self.assertEqual([opener], [i["key"] for i in db.waiting()])
            db.grant_answer(opener, "toolu_01open2")
        self.assertEqual("verified", self.pub(opener, main_session=True)["state"])
        self.assertEqual("ready", self.event(marked)["state"])
        self.assertEqual("verified", self.pub(marked)["state"])

    def test_dropping_the_held_task_discards_what_waited_for_it(self):
        self.use(level="assistant")
        opener, source = self.task_event()
        marked, _ = self.result(marks=[{"task": 0, "steps": [1]}], **{"with": source})
        with store.Store(REPO, config=self.pcfg) as db:
            db.transition(opener, "deferred", reason="level_ask")
        self.pub(marked)
        with store.Store(REPO, config=self.pcfg) as db:
            db.transition(opener, "rejected")
            moved = publisher.release_dependents(db, db.event(opener), False)
        self.assertEqual([marked], moved)
        self.assertEqual(("rejected", "mark_binding"), (self.event(marked)["state"], self.event(marked)["reason"]))


class LevelTests(PublisherBox):
    """The level gate, the publisher's own, and the shadow mode."""

    def test_notes_only_publishes_neither_a_task_nor_a_to_do_nor_a_mark(self):
        self.use(level="notes")
        for key in (self.task_event()[0], self.result(marks=[{"task": 7, "steps": [2]}])[0]):
            with self.assertRaisesRegex(publisher.PublishError, "level"):
                self.pub(key)
        todo, _ = self.queue("steps", 7, tj.steps_text(steps=("rerun with the other seed",)))
        with self.assertRaisesRegex(publisher.PublishError, "level"):
            self.pub(todo)
        self.assertEqual([], self.log("issue comment"))
        self.assertEqual([], self.titled("Task: write the benchmark note"))
        plain, _ = self.result()
        self.assertEqual("verified", self.pub(plain)["state"])          # an ordinary memory is the level's own

    def test_assistant_keeps_to_do_items_and_marks_on_its_own(self):
        self.use(level="assistant")
        key, _ = self.result(marks=[{"task": 7, "steps": [2]}])
        self.assertEqual("verified", self.pub(key)["state"])
        todo, _ = self.queue("steps", 7, tj.steps_text(at="2026-03-14T10:00-08:00", steps=("rerun with the other seed",),
                                                       plain="keep one more item on the to-do list"))
        self.assertEqual("verified", self.pub(todo)["state"])
        with store.Store(REPO, config=self.pcfg) as db:
            self.assertIn("steps_added", [a["kind"] for a in db.acts()])

    def test_everything_opens_a_task_on_its_own(self):
        self.use(level="everything")
        self.assertEqual("verified", self.pub(self.task_event()[0])["state"])
        self.assertEqual(1, len(self.titled("Task: write the benchmark note")))

    def test_the_shadow_mode_posts_opens_and_renders_nothing_at_any_level(self):
        for level in ("notes", "assistant", "everything"):
            self.use(level=level, mode="shadow")
            for key in (self.task_event(title="Task: shadow %s" % level)[0],
                        self.result(marks=[{"task": 7, "steps": [2]}])[0]):
                with self.assertRaisesRegex(publisher.PublishError, "not_approved"):
                    self.pub(key)
                with self.assertRaisesRegex(publisher.PublishError, "not_approved"):
                    self.pub(key, main_session=True)
        self.assertEqual([], self.log("issue comment"))
        self.assertEqual([], self.log("issue create"))

    def test_a_paused_publication_opens_no_task(self):
        with store.Store(REPO, config=self.pcfg) as db:
            db.set_control("publication_paused", True)
        with self.assertRaisesRegex(publisher.PublishError, "publication_paused"):
            self.pub(self.task_event()[0])
        self.assertEqual([], self.log("issue create"))


class CapTests(PublisherBox):
    """max_tasks and max_posts, rolling 24 hours."""

    def test_the_fourth_task_of_a_day_waits(self):
        keys = [self.task_event(title="Task: note %d" % n)[0] for n in range(1, 5)]
        outcomes = [self.pub(key) for key in keys]
        self.assertEqual(["verified"] * 3 + [None], [o["state"] if o["state"] == "verified" else None for o in outcomes])
        self.assertEqual("daily_tasks", outcomes[3]["waiting"])
        self.assertEqual("ready", self.event(keys[3])["state"])
        self.assertEqual([], self.titled("Task: note 4"))
        with store.Store(REPO, config=self.pcfg) as db:
            self.assertGreater(db.get_control("task_cap_at", 0), 0)
            stamps = db.get_control("automatic_tasks", [])
            db.set_control("automatic_tasks", [at - 90000 for at in stamps])      # a day later
        self.assertEqual("verified", self.pub(keys[3])["state"])

    def test_the_cap_can_be_lowered_and_never_raised(self):
        self.use(max_tasks=1)
        first, second = self.task_event(title="Task: one")[0], self.task_event(title="Task: two")[0]
        self.assertEqual("verified", self.pub(first)["state"])
        self.assertEqual("daily_tasks", self.pub(second)["waiting"])
        with self.assertRaises(ValueError):
            runtime.settings({"observer": dict(enabled=True, max_tasks=0)})
        self.assertEqual(3, runtime.settings({"observer": {"enabled": True, "max_tasks": 99}})["max_tasks"])

    def test_the_task_and_its_first_comment_count_against_max_posts(self):
        self.use(max_posts=1)
        self.assertEqual("verified", self.pub(self.task_event(title="Task: one")[0])["state"])
        with self.assertRaisesRegex(publisher.PublishError, "daily_cap"):
            self.pub(self.result()[0])

    def test_the_main_sessions_own_publication_is_not_counted_against_the_automatic_caps(self):
        self.use(max_tasks=1)
        first, second = self.task_event(title="Task: one")[0], self.task_event(title="Task: two")[0]
        self.assertEqual("verified", self.pub(first)["state"])
        with store.Store(REPO, config=self.pcfg) as db:
            db.transition(second, "deferred", reason="main_session")
        self.assertEqual("verified", self.pub(second, main_session=True)["state"])


class TitleTests(PublisherBox):
    """The title of a task is the model's text too, and reaches GitHub apart from the body: it gets the body's checks, and
    nothing is created or posted when it fails them."""

    def writes(self):
        """The writes the fake saw: an issue created, edited or commented on, or a GraphQL mutation (a sub-issue link)."""
        return [call for call in self.repo().get("log", [])
                if " ".join(call[:2]) in ("issue create", "issue comment", "issue edit")
                or (call[:2] == ["api", "graphql"] and "mutation" in " ".join(call))]

    def refused(self, title, reason, cap=None, patch=None):
        """Queue a task with this title (the store's own cap lifted when `cap` is given, so that the publisher's guard is
        the one that is tried), publish it and expect `reason`; the event stays ready and nothing was written."""
        with mock.patch.object(store, "TASK_TITLE", cap or store.TASK_TITLE):
            key, _ = self.task_event(title=title)
        before = self.writes()
        with (patch or mock.patch.object(publisher, "TITLE_CAP", publisher.TITLE_CAP)):
            with self.assertRaises(publisher.PublishError) as caught:
                self.pub(key)
        self.assertEqual(reason, caught.exception.reason, title)
        self.assertEqual(before, self.writes(), title)
        self.assertEqual("ready", self.event(key)["state"])
        self.assertEqual([], self.titled(title))

    def test_a_secret_in_the_title_is_refused(self):
        self.refused("Task: rotate ghp_" + "a1B2c3D4e5" * 4, "title_restricted")

    def test_a_restricted_pattern_in_the_title_is_refused(self):
        self.pcfg["restricted_patterns"] = ["PARTICIPANT-[0-9]+"]
        self.refused("Task: recheck PARTICIPANT-0042", "title_restricted")

    def test_a_banned_word_in_the_title_is_refused(self):
        self.pcfg["banned_words"] = ["draftword"]
        self.refused("Task: write the Draftword note", "title_restricted")

    def test_a_local_path_or_this_machines_user_in_the_title_is_refused(self):
        with mock.patch.object(publisher, "local_names", return_value=({"/home/octo-dev"}, {"octo-dev"})):
            self.refused("Task: fix /home/octo-dev/work/reader.py", "outbound_local")
            self.refused("Task: ask octo-dev to rerun", "outbound_local")
        self.refused("Task: mail octo.dev@example.test", "outbound_local")

    def test_markup_a_mention_an_address_and_an_outside_reference_in_the_title_are_refused(self):
        self.refused("Task: ![x](https://collector.example.test/p.png)", "outbound_markup")
        self.refused("Task: ask @hubot to rerun", "outbound_mention")
        self.refused("Task: read https://collector.example.test/p", "outbound_address")
        self.refused("Task: see other-org/other-repo#12", "outbound_reference")

    def test_a_control_character_in_the_title_is_refused(self):
        self.refused("Task: write the\x07 note", "title_form")
        self.refused("Task: write the\x1b[31m note", "title_form")
        self.refused("Task: write the\r note", "title_form")
        self.refused("Task: write the\ttable", "title_form")

    def test_a_bidirectional_or_invisible_character_in_the_title_is_refused(self):
        for char in ("\u202e", "\u2066", "\u200b", "\u200f", "\ufeff", "\u2028", "\u00a0"):
            self.refused("Task: write the %snote" % char, "title_form")

    def test_an_over_long_title_is_refused_by_the_publisher_even_if_the_queue_took_it(self):
        self.refused("Task: " + "note " * 30, "title_form", cap=500)
        self.refused("x" * 121, "title_form", cap=500)

    def test_the_queue_refuses_an_over_long_or_multi_line_title_itself(self):
        for title in ("x" * 121, "Task: a\nb"):
            with self.assertRaisesRegex(store.StoreError, "invalid_candidate"):
                self.task_event(title=title)

    def test_a_title_of_exactly_the_cap_and_an_ordinary_one_pass(self):
        title = "Task: " + "n" * (publisher.TITLE_CAP - 6)
        self.assertEqual(publisher.TITLE_CAP, len(title))
        self.assertIsNone(publisher.title_problem(title, REPO, self.pcfg))
        self.assertIsNone(publisher.title_problem("Task: write the benchmark note (v2)", REPO, self.pcfg))
        key, _ = self.task_event()
        self.assertEqual("verified", self.pub(key)["state"])

    def test_a_dry_run_names_what_is_wrong_with_the_title(self):
        with mock.patch.object(store, "TASK_TITLE", 500):
            key, _ = self.task_event(title="x" * 130)
        self.assertEqual("title_form", self.pub(key, dry_run=True)["title"])
        self.assertEqual([], self.writes())

    def test_the_main_session_cannot_post_a_title_the_gate_refused(self):
        with mock.patch.object(store, "TASK_TITLE", 500):
            key, _ = self.task_event(title="Task: write the\x07 note")
        with store.Store(REPO, config=self.pcfg) as db:
            db.transition(key, "deferred", reason="main_session")
        with self.assertRaisesRegex(publisher.PublishError, "title_form"):
            self.pub(key, main_session=True)
        self.assertEqual([], self.writes())


class OtherFieldsTests(PublisherBox):
    """The other model-written fields the publisher sends: what the marker may hold, and the labels."""

    def test_a_marker_value_that_is_not_a_number_is_refused_before_anything_is_posted(self):
        for mark in (" because=javascript:alert(1)", " because=https://collector.example.test/x", " checks=9104,abc",
                     " because=9104,abc", " because=9104,9105,9106,9107,9108", " because=0", " replaces=9104;9105", " source=file:@hubot", " recorded=2026-03-12T10:00-08:00",
                     " eventkey=" + "0" * 32 + " x=1", " extra=1"):
            try:   # with the status authority a change of status would have, so that the value itself is what is judged
                key, _ = self.queue("result", 5, tj.with_mark(tj.result_text(), mark), review={"status_authority": "agent"})
            except store.StoreError:
                continue   # the queue itself refused the body
            with self.assertRaises(publisher.PublishError, msg=mark):
                self.pub(key)
        self.assertEqual([], self.log("issue comment"))

    def test_the_marks_and_links_the_publisher_composes_are_whole_numbers_only(self):
        key, _ = self.result(marks=[{"task": 7, "steps": [2]}], links=[{"key": "checks", "part": 9104}])
        event = self.event(key)
        import index
        db = index.connect(REPO, create=False)
        try:
            body = publisher.compose(event, db, {"octocat"})
        finally:
            db.close()
        marker = self.marker(body)
        for name in ("checks", "against", "done", "dropped"):
            if name in marker:
                self.assertRegex(marker[name], r"^[0-9,;:]+$")
        self.assertEqual(1, body.split("\n")[0].count("<!--"))

    def test_a_task_is_always_labeled_active_and_nothing_else(self):
        key, _ = self.task_event(title="Task: label check")
        self.assertEqual("verified", self.pub(key)["state"])
        self.assertEqual(["active"], self.repo()["issues"][str(self.titled("Task: label check")[0])]["labels"])

    def test_the_body_of_a_new_task_is_made_by_code_from_the_checked_memory(self):
        key, _ = self.task_event(title="Task: body check")
        out = self.pub(key)
        issue = self.repo()["issues"][str(out["receipt"]["issue"])]
        body = issue["body"].replace("\r\n", "\n")
        self.assertIn("## Plan\n\n- [ ] 1. Run the benchmark\n- [ ] 2. Write the table", body)
        text = "\n".join(line for line in body.split("\n") if not line.startswith("<!-- ghim:list"))
        self.assertIsNone(publisher.outbound_problem(text, REPO, status_ok=True))   # the plugin's own list mark aside


class RegisterMainTests(PublisherBox):
    """The main session's own steps memories are registered like its other memories, so nothing is posted twice."""

    def test_a_steps_memory_the_main_session_posted_sets_the_observers_aside(self):
        key, _ = self.queue("steps", 7, tj.steps_text(at="2026-03-14T10:00-08:00", steps=("rerun with the other seed",),
                                                      plain="keep one more item on the to-do list"))
        moved = publisher.register_main(REPO, self.pcfg, 7, "steps", 9301)
        self.assertEqual([key], moved)
        self.assertEqual(("deferred", "main_session"), (self.event(key)["state"], self.event(key)["reason"]))
        self.assertEqual([], publisher.register_main(REPO, self.pcfg, 5, "steps", 9302))

    def test_post_verified_registers_a_steps_comment_with_the_observer(self):
        key, _ = self.queue("steps", 7, tj.steps_text(at="2026-03-14T10:00-08:00", steps=("rerun with the other seed",),
                                                      plain="keep one more item on the to-do list"))
        body = self.write("todo-steps.md", tj.steps_text(at="2026-03-14T11:00-08:00", steps=("check the other seed",),
                                                         plain="keep one more item on the to-do list"))
        out = self.tool("post_verified.py", "comment", "--issue", "7", "--kind", "steps", "--body-file", body)
        self.assertEqual(0, out.returncode, out.stdout + out.stderr)
        self.assertIn("observer: registered; the observer leaves this steps on #7 to you", out.stdout)
        self.assertEqual("deferred", self.event(key)["state"])


if __name__ == "__main__":
    unittest.main()
