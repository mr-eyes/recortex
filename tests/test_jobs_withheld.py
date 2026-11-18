"""A moment the user's hold or the reviewer kept off the record is not lost (1.5.1). Releasing a hold offers the sources it
withheld to the sort once more, and a candidate the reviewer rejected as withheld waits as a held memory for the user's Post
or Drop. The stretch is the invented one of tests/fixtures/observer-stretches/u01.json (a result and a failure the user
typed, four messages that say nothing); the models' answers are given by the tests; the pass is the real worker and
publisher against the fake gh."""

import contextlib
import io
import json
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_jobs_jobs as tjj  # noqa: E402
import test_jobs_live as tl  # noqa: E402
import test_jobs_publisher as tp  # noqa: E402
import test_jobs_tasks as tj  # noqa: E402
import test_scripts as ts  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import observer  # noqa: E402
import observer_holds  # noqa: E402
import observer_publisher as publisher  # noqa: E402
import observer_store as store  # noqa: E402
import observer_waiting as waiting  # noqa: E402

REPO = ts.REPO
SUPPORTED = {"accepted": True, "reason": "supported"}
WITHHELD = {"accepted": False, "reason": "withheld"}


class Box(tl.LiveBox):
    """The stretch u01 and what a test of holds and of held memories needs around it."""

    def setUp(self):
        super().setUp()
        self.ids = [s["id"] for s in self.data["sources"]]

    # -- holds
    def hold(self, sid="hold-1", session="sess-u01", text="do not post", action="publish"):
        """A hold the user's typed message made: of one session, or, with session None, of the whole repository."""
        scope = {"session_id": session} if session else None
        return observer_holds.make_hold({"source_id": sid, "role": "user", "direct_user": True, "text": text}, REPO,
                                        action, scope, None)

    def keep_holds(self, *holds):
        with store.Store(REPO, config=self.pcfg) as db:
            db.save_holds(list(holds))

    def release(self, hold):
        """Release it as the user's terminal does; the number of sources it offered again."""
        with store.Store(REPO, config=self.pcfg) as db:
            db.update_holds(lambda holds: observer_holds.release(holds, hold["id"]))
            return db.offered_again

    # -- sources
    def other(self, session="sess-other", ids=("other-1", "other-2")):
        """Sources of another session, which the pass never needs to read when a hold covers them."""
        rows = [{"source_id": sid, "session_id": session, "role": "user", "event_type": "Stop",
                 "path": "/example/other.jsonl", "offset": n * 1000, "end": n * 1000 + 900, "sha256": "0" * 64,
                 "content_sha256": "%064x" % (n + 1), "observed_at": time.time() - 90 + n}
                for n, sid in enumerate(ids)]
        with store.Store(REPO, config=self.pcfg) as db:
            db.capture(rows)
        return list(ids)

    def states(self, ids):
        with store.Store(REPO, config=self.pcfg) as db:
            return [(db.observation(sid)["state"], db.observation(sid)["reason"]) for sid in ids]

    def one_pass(self, **models):
        report, purposes, calls = self.run_pass(**models)
        return report, [purpose for purpose in purposes if purpose != "summary"], calls

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = observer.main(list(args))
        return code, out.getvalue()

    def kinds_posted(self):
        return [body.split("type=")[1].split(" ")[0] for _, body in self.posted()]

    def waiting(self):
        with store.Store(REPO, config=self.pcfg) as db:
            return db.waiting()

    def event(self, key):
        with store.Store(REPO, config=self.pcfg) as db:
            return db.event(key)


class ReleaseTests(Box):
    """The sources a hold kept off the record go back to the sort when the user releases it."""

    def held_pass(self, *holds):
        self.keep_holds(*holds)
        report, purposes, calls = self.one_pass()
        self.assertEqual(([], []), (purposes, report["ready"]))   # no call is spent on a session kept off the record
        self.assertEqual([("deferred", "withheld")] * 8, self.states(self.ids))

    def test_releasing_the_hold_offers_its_sources_and_the_next_pass_posts_them_once(self):
        hold = self.hold()
        self.held_pass(hold)
        self.assertEqual(8, self.release(hold))
        self.assertEqual([("observed", "")] * 8, self.states(self.ids))
        report, purposes, calls = self.one_pass()
        self.assertEqual(["sort", "draft", "review", "review"], purposes)
        self.assertEqual(2, len(report["ready"]))
        self.assertEqual(["result", "failure"], self.kinds_posted())
        self.assertEqual({"verified"}, {self.event(key)["state"] for key in report["ready"]})
        again, purposes, calls = self.one_pass()   # a later pass has nothing to offer and posts nothing again
        self.assertEqual(([], []), (purposes, again["ready"]))
        self.assertEqual(["result", "failure"], self.kinds_posted())

    def test_only_the_sources_of_the_released_hold_are_offered(self):
        hold, other_hold = self.hold(), self.hold("hold-2", "sess-other")
        others = self.other()
        self.keep_holds(hold, other_hold)
        self.one_pass()
        self.assertEqual([("deferred", "withheld")] * 10, self.states(self.ids + others))
        legacy = self.other("sess-legacy", ("legacy-1",))   # withheld by a verdict of the reviewer of 1.5.0: no hold covers it
        with store.Store(REPO, config=self.pcfg) as db:
            db.defer(legacy[0], "withheld")
        self.assertEqual(8, self.release(hold))
        self.assertEqual([("observed", "")] * 8 + [("deferred", "withheld")] * 3, self.states(self.ids + others + legacy))
        self.one_pass()
        self.assertEqual(["result", "failure"], self.kinds_posted())
        self.assertEqual([("deferred", "withheld")] * 2, self.states(others))   # the other session stays off the record
        self.assertEqual(2, self.release(other_hold))
        self.assertEqual([("observed", "")] * 2, self.states(others))
        self.assertEqual([("deferred", "withheld")], self.states(legacy))   # not one of the sources of either hold

    def test_a_source_another_hold_still_covers_stays_withheld(self):
        # A second typed phrase of the session, and a hold of the whole repository, each keep the moments off the record
        # until it is released too.
        first, second, wide = self.hold("hold-1"), self.hold("hold-2", text="off the record"), self.hold("hold-3", None)
        self.held_pass(first, second, wide)
        self.assertEqual(0, self.release(first))
        self.assertEqual(0, self.release(second))   # the hold of the whole repository still covers the session
        self.assertEqual([("deferred", "withheld")] * 8, self.states(self.ids))
        self.assertEqual(8, self.release(wide))
        self.assertEqual([("observed", "")] * 8, self.states(self.ids))

    def test_a_session_hold_covered_by_two_session_holds_waits_for_the_second_release(self):
        first, second = self.hold("hold-1"), self.hold("hold-2", text="off the record")
        self.held_pass(first, second)
        self.assertEqual(0, self.release(first))
        self.assertEqual([("deferred", "withheld")] * 8, self.states(self.ids))
        report, purposes, calls = self.one_pass()   # still covered: a pass spends no call on it
        self.assertEqual(([], []), (purposes, report["ready"]))
        self.assertEqual(8, self.release(second))

    def test_only_the_sources_withheld_by_a_hold_of_publication_are_offered(self):
        hold = self.hold()
        push = self.hold("hold-4", session=None, text="do not push", action="git-push")
        self.held_pass(hold, push)
        with store.Store(REPO, config=self.pcfg) as db:
            db.defer(self.ids[0], "privacy")          # deferred for another reason
            db.mark_handled(self.ids[1], reason="")   # sorted out
        self.assertEqual(0, self.release(push))       # a hold of a git action withheld no moment
        self.assertEqual(6, self.release(hold))
        states = self.states(self.ids)
        self.assertEqual([("deferred", "privacy"), ("ignored", "")] + [("observed", "")] * 6, states)

    def test_the_users_release_in_the_terminal_says_how_many_were_offered(self):
        hold = self.hold()
        self.held_pass(hold)

        class Typed:
            def ask(self, question):
                return "release"

        with store.Store(REPO, config=self.pcfg) as db:
            result = observer.release(db, hold["id"], Typed())
        self.assertEqual({"action": "release", "hold_id": hold["id"], "released": True, "offered_again": 8}, result)

    def test_a_memory_the_main_session_recorded_meanwhile_is_not_posted_again(self):
        hold = self.hold()
        self.held_pass(hold)
        with store.Store(REPO, config=self.pcfg) as db:
            db.register_main(6, "result", 9200)   # the main session wrote the result itself while the session was held
        self.release(hold)
        report, purposes, calls = self.one_pass()
        self.assertEqual(["sort", "draft", "review"], purposes)   # one review: the result is not offered to the reviewer
        self.assertEqual(["failure"], self.kinds_posted())
        self.assertEqual([("ignored", "main_session")] * 2, self.states(self.ids[:2]))

    def test_a_source_that_already_has_its_event_posts_no_second_comment(self):
        hold = self.hold()
        self.held_pass(hold)
        self.release(hold)
        self.one_pass()
        self.assertEqual(["result", "failure"], self.kinds_posted())
        with store.Store(REPO, config=self.pcfg) as db:   # offered once more, as if a release came twice
            with db.transaction():
                db.db.execute("UPDATE observations SET state='observed',reason='' WHERE source_id IN (?,?,?,?)",
                              self.ids[:4])
        report, purposes, calls = self.one_pass()
        self.assertEqual(["result", "failure"], self.kinds_posted())   # the event key is the sources': one comment each
        self.assertEqual(2, len([c for c in self.repo()["comments"].values() if "eventkey=" in c["body"]]))


class ReviewTests(Box):
    """A candidate the reviewer rejected as withheld is kept for the user's answer, whatever made the reviewer say so."""

    def withheld_pass(self, verdicts=(WITHHELD, SUPPORTED)):
        report, purposes, calls = self.one_pass(review=list(verdicts))
        self.assertEqual(["sort", "draft", "review", "review"], purposes)
        with store.Store(REPO, config=self.pcfg) as db:
            items = [item for item in db.waiting() if item["kind"] == "held"]
        self.assertEqual(1, len(items), items)
        return report, items[0]["key"]

    def test_the_candidate_waits_as_a_held_memory_and_the_other_one_posts(self):
        report, key = self.withheld_pass()
        self.assertEqual(1, len(report["ready"]))
        self.assertEqual(["failure"], self.kinds_posted())   # the result is not on the record
        event = self.event(key)
        self.assertEqual(("deferred", "withheld_review", 1, "result"),
                         (event["state"], event["reason"], event["held"], event["candidate"]["kind"]))
        self.assertEqual({"accepted": False, "reason": "withheld"},
                         {k: event["review"][k] for k in ("accepted", "reason")})
        self.assertEqual([("deferred", "withheld_review")] * 2, self.states(self.ids[:2]))
        with store.Store(REPO, config=self.pcfg) as db:
            [entry] = [row for row in waiting.listing(db) if row["key"] == key]
        self.assertEqual(["Post", "Drop"], entry["options"])
        self.assertTrue(entry["question"].startswith("Memory %s (result) on #6: " % key[:8]))
        self.assertIn(waiting.KEPT_OFF, entry["question"])
        self.assertTrue(entry["question"].endswith(". Post it, or drop it?"))
        self.assertEqual({"yes": "post_verified.py comment --eventkey %s" % key, "no": "observer.py discard %s" % key},
                         entry["commands"])

    def test_pending_waiting_lists_it_for_the_session(self):
        _, key = self.withheld_pass()
        code, text = self.cli("pending", "--waiting", "--json")
        self.assertEqual(0, code)
        rows = [row for row in json.loads(text)["waiting"] if row["key"] == key]
        self.assertEqual(["held"], [row["kind"] for row in rows])
        code, text = self.cli("pending", "--held")
        self.assertEqual((0, ["withheld_review"]), (code, [row["reason"] for row in json.loads(text)["held"]]))

    def test_nothing_is_offered_to_a_model_again_and_nothing_posts_on_its_own(self):
        _, key = self.withheld_pass()
        report, purposes, calls = self.one_pass()
        self.assertEqual(([], []), (purposes, report["ready"]))
        self.assertEqual(["failure"], self.kinds_posted())
        self.assertEqual("deferred", self.event(key)["state"])

    def test_post_needs_the_users_answer_and_posts_the_memory_once(self):
        _, key = self.withheld_pass()
        with self.assertRaisesRegex(publisher.PublishError, "held_unanswered"):
            publisher.publish(REPO, key, self.pcfg, main_session=True)
        self.assertEqual(["failure"], self.kinds_posted())
        with store.Store(REPO, config=self.pcfg) as db:
            publisher._bound(db.event(key), db)   # post_verified.py checks it before it asks for the post
            self.assertTrue(db.grant_answer(key, "toolu_01post"))   # what the hook records from the user's "Post"
        done = publisher.publish(REPO, key, self.pcfg, main_session=True)
        self.assertEqual("verified", done["state"])
        self.assertEqual(["failure", "result"], sorted(self.kinds_posted()))
        again = publisher.publish(REPO, key, self.pcfg, main_session=True)   # asked twice: still one comment
        self.assertEqual(done["receipt"], again["receipt"])
        self.assertEqual(2, len(self.posted()))
        report, purposes, calls = self.one_pass()   # a later pass neither offers nor posts it
        self.assertEqual(([], []), (purposes, report["ready"]))
        self.assertEqual(2, len(self.posted()))
        self.assertEqual([], [item for item in self.waiting() if item["kind"] == "held"])
        with store.Store(REPO, config=self.pcfg) as db:
            self.assertFalse(db.has_grant(key))   # the answer was used once

    def test_the_automatic_publisher_never_posts_it_even_when_it_is_ready(self):
        _, key = self.withheld_pass()
        with store.Store(REPO, config=self.pcfg) as db:
            self.assertEqual(["withheld_review"], publisher.held_back(db, db.event(key), self.pcfg))
            db.transition(key, "ready")   # a copy of the state a bug or an old queue could leave
        with self.assertRaisesRegex(publisher.PublishError, "withheld_review"):
            publisher.publish(REPO, key, self.pcfg)
        self.assertEqual(["failure"], self.kinds_posted())

    def test_drop_discards_it_on_the_users_answer_and_it_is_not_offered_again(self):
        _, key = self.withheld_pass()
        code, text = self.cli("discard", key)   # a session's own call: refused, the memory still waits
        self.assertEqual((1, "answer_unanswered"), (code, json.loads(text)["code"]))
        self.assertEqual("deferred", self.event(key)["state"])
        with store.Store(REPO, config=self.pcfg) as db:
            item = waiting.find_any(db, key)
            self.assertTrue(db.grant_answer(waiting.answer_key(item, "no"), "toolu_01drop"))   # the user's "Drop"
        self.assertEqual(0, self.cli("discard", key)[0])
        event = self.event(key)
        self.assertEqual(("rejected", 1), (event["state"], event["held"]))   # held for good: no copy posts around it
        self.assertEqual([], [item for item in self.waiting() if item["kind"] == "held"])
        self.assertEqual([("rejected", "discarded")] * 2, self.states(self.ids[:2]))
        report, purposes, calls = self.one_pass()
        self.assertEqual(([], []), (purposes, report["ready"]))
        self.assertEqual(["failure"], self.kinds_posted())

    def test_a_dropped_memory_is_not_offered_again_by_a_release_either(self):
        # The hold that kept the session off the record is released after the reviewer's memory was dropped: the dropped
        # moment (state rejected) is not one of the withheld sources.
        _, key = self.withheld_pass()
        with store.Store(REPO, config=self.pcfg) as db:
            db.transition(key, "rejected", reason="discarded", held=True)
        hold = self.hold()
        self.keep_holds(hold)
        self.assertEqual(0, self.release(hold))
        self.assertEqual([("rejected", "discarded")] * 2, self.states(self.ids[:2]))

    def test_a_memory_the_main_session_already_recorded_is_not_kept_for_an_answer(self):
        with store.Store(REPO, config=self.pcfg) as db:
            db.register_main(6, "result", 9200)
        report, purposes, calls = self.one_pass(review=[WITHHELD])
        self.assertEqual(["sort", "draft", "review"], purposes)   # only the failure is reviewed, and it is withheld
        self.assertEqual([("ignored", "main_session")] * 2, self.states(self.ids[:2]))
        self.assertEqual(["failure"], [row["event"]["candidate"]["kind"] for row in self.waiting()])

    def test_a_hold_of_the_session_still_blocks_even_the_users_post(self):
        _, key = self.withheld_pass()
        hold = self.hold()
        self.keep_holds(hold)
        with store.Store(REPO, config=self.pcfg) as db:
            db.grant_answer(key, "toolu_01post")
        with self.assertRaisesRegex(publisher.PublishError, "withheld"):
            publisher.publish(REPO, key, self.pcfg, main_session=True)
        self.assertEqual(["failure"], self.kinds_posted())
        with store.Store(REPO, config=self.pcfg) as db:
            self.assertTrue(db.has_grant(key))   # the answer was not spent on a refusal
        self.release(hold)
        self.assertEqual("verified", publisher.publish(REPO, key, self.pcfg, main_session=True)["state"])
        self.assertEqual(["failure", "result"], sorted(self.kinds_posted()))

    def test_a_reviewer_that_withholds_a_task_asks_the_question_of_a_task_to_open(self):
        # A steps memory that would open a task waits as "Open it" (the one question that says what an answer does).
        body = tj.steps_text(at="2026-03-13T09:00-08:00", plain="write the benchmark note in two steps",
                             steps=("Run the benchmark", "Write the table"), done="The note is posted.")
        source = "task-source"
        with store.Store(REPO, config=self.pcfg) as db:
            db.capture([{"source_id": source, "session_id": "sess-u01", "role": "user", "event_type": "Stop",
                         "path": "/example/session.jsonl", "offset": 0, "end": 10, "sha256": "0" * 64,
                         "observed_at": time.time() - 100}])
            candidate = tp.candidate_for("steps", 0, body, [source], task_title="Task: write the benchmark note", topic=5)
            ident = db.reserve_invocation("review")
            digest = store.candidate_hash(candidate)
            db.complete_invocation(ident, True, output_hash=digest)
            key = db.review_complete(source, candidate, {"accepted": False, "reason": "withheld", "candidate_hash": digest,
                                                         "reservation_id": ident}, hold="withheld_review")
            [item] = db.waiting()
            self.assertEqual(("task_open", key), (item["kind"], item["key"]))
            self.assertEqual(["Open it", "Not now", "Drop it"], waiting.entry(item)["options"])
            self.use(level="assistant")
            self.assertEqual("withheld_review", db.event(key)["reason"])
            db.grant_answer(key, "toolu_01open")
        done = publisher.publish(REPO, key, self.pcfg, main_session=True)   # the answer lets it open at the level Assistant
        self.assertEqual("verified", done["state"])
        self.assertEqual(["Task: write the benchmark note"], [i["title"] for i in self.repo()["issues"].values()
                                                              if i["title"].startswith("Task: write")])


class QuestionTests(tjj.HookBox):
    """The user's Post to the question about a memory the reviewer kept off the record counts when it is the whole
    question, as for every held memory, and for nothing else."""

    def held(self, n=0):
        key = "%08x" % (0xabc00000 + n) + "0" * 24
        body = ("<!-- ghim:event type=result at=2026-11-18T09:12+00:00 -->\n### Result: 2026-11-18 09:12 UTC - Read time\n\n"
                "**In plain words, we found that:** the reader read the file in 0.12 s\n")
        candidate = json.dumps({"kind": "result", "issue": 3, "body": body, "sources": ["s%d" % n]})
        with self.store() as db:
            with db.transaction():
                db.db.execute("INSERT INTO events(eventkey,source_id,candidate,review,body,payload_hash,state,reason,"
                              "created,updated,held) VALUES (?,?,?,'{}',?,?,'deferred','withheld_review',?,?,1)",
                              (key, "s%d" % n, candidate, body, "0" * 64, n, n))
        return key

    def ask(self, question, answer, ident, labels):
        asked = {"question": question, "header": "Memory", "multiSelect": False,
                 "options": [{"label": label, "description": label} for label in labels]}
        use = {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [
            {"type": "tool_use", "id": ident, "name": "AskUserQuestion", "input": {"questions": [asked]}}]}}
        result = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": ident,
             "content": 'User has answered your questions: "%s"="%s". You can now continue with the user\'s answers '
                        'in mind.' % (question, answer)}]},
                  "toolUseResult": {"questions": [asked], "answers": {question: answer}}}
        with self.path.open("a") as handle:
            handle.write(json.dumps(use) + "\n" + json.dumps(result) + "\n")

    def test_only_the_whole_question_and_post_make_the_grant(self):
        key = self.held()
        with self.store() as db:
            item = waiting.find_any(db, key)
            entry = waiting.entry(item)
        self.assertIn(waiting.KEPT_OFF, entry["question"])
        command = "python3 skills/recortex/scripts/post_verified.py comment --eventkey %s --issue 3 --kind result" % key
        self.ask(entry["question"].replace(waiting.KEPT_OFF, ""), "Post", "toolu_01short", waiting.HELD_OPTIONS)   # the plain template
        self.handle("pre-tool", tool_name="Bash", tool_input={"command": command})
        with self.store() as db:
            self.assertFalse(db.has_grant(key))
        self.ask(entry["question"], "Drop", "toolu_01drop", waiting.HELD_OPTIONS)
        self.handle("pre-tool", tool_name="Bash", tool_input={"command": command})
        with self.store() as db:
            self.assertFalse(db.has_grant(key))
        self.ask(entry["question"], "Post", "toolu_01whole", waiting.HELD_OPTIONS)
        self.handle("pre-tool", tool_name="Bash", tool_input={"command": command})
        with self.store() as db:
            self.assertTrue(db.has_grant(key))

    def test_the_hook_payloads_carry_a_count_and_never_the_memory(self):
        self.held()
        text = json.dumps(self.handle("session-start"))
        self.assertIn("1 item", text)
        for absent in ("the reader read the file", "withheld_review", "kept it off the record"):
            self.assertNotIn(absent, text)


if __name__ == "__main__":
    unittest.main()
