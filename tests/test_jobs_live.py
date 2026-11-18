"""A stretch the first 1.5.0 wording of the models left unposted: the user types a result and a failure of their own runs
and asks the agent to just acknowledge them, the agent says it posted nothing as the user asked earlier, and nothing
else of the stretch was recorded by the main session. The answers of the models are the ones real Haiku (sort) and
Sonnet (draft, review) calls gave to this invented text with the corrected wording (tests/fixtures/observer-stretches/
u01.json); the pass is replayed through the real worker and publisher against the fake gh. The models are not run here:
the tests hold what the pass does with answers like theirs, and that the prompts keep the lines the answers rest on."""

import copy
import json
import os
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_jobs_publisher as tp  # noqa: E402
import test_scripts as ts  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import observer_holds  # noqa: E402
import observer_runtime as runtime  # noqa: E402
import observer_store as store  # noqa: E402
import observer_worker as worker  # noqa: E402

REPO = ts.REPO
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "observer-stretches", "u01.json")


class LiveBox(tp.PublisherBox):
    """The example record with the observer approved at the level Everything in automatic mode, and the stretch
    captured as the hooks leave it: eight messages, in the order they happened."""

    def setUp(self):
        super().setUp()
        with open(FIXTURE, encoding="utf-8") as handle:
            self.data = json.load(handle)
        self.by_id = {s["id"]: dict(s, subagent=False, sidechain=False, control_user=False, message_id=None)
                      for s in self.data["sources"]}

    def capture(self, session="sess-u01"):
        now = time.time() - 60
        rows = [{"source_id": s["id"], "session_id": session, "role": s["role"], "event_type": "Stop",
                 "path": "/example/session.jsonl", "offset": i * 1000, "end": i * 1000 + 900, "sha256": "0" * 64,
                 "content_sha256": "1" * 64, "observed_at": now + i} for i, s in enumerate(self.data["sources"])]
        with store.Store(REPO, config=self.pcfg) as db:
            db.capture(rows)

    def models(self, sort=None, draft=None, review=None, calls=None):
        """The calls of the pass, answered with what the real models answered (or what a test gives)."""
        answers = {"sort": sort or self.data["sort"], "draft": {"candidates": draft if draft is not None else self.data["draft"]}}
        verdicts = list(self.data["reviews"] if review is None else review)

        def invoke(payload, schema, purpose, config):
            if calls is not None:
                calls.append((purpose, payload))
            if purpose in answers:
                return {"ok": True, "structured": answers[purpose]}
            if purpose == "review":
                return {"ok": True, "structured": dict(verdicts.pop(0), candidate_hash=payload["candidate_hash"])}
            return {"ok": False, "code": "runtime-failed", "structured": None}   # the status summary is not part of this
        return invoke

    def run_pass(self, **models):
        self.ready()
        self.capture()
        calls = models.setdefault("calls", [])
        with mock.patch.object(worker.sources, "resolve", side_effect=lambda meta, root: self.by_id[meta["source_id"]]):
            report = worker.run_once(REPO, self.pcfg, self.project, self.models(**models))
        return report, [purpose for purpose, _ in calls], calls

    def posted(self):
        """The comments the pass wrote to the record, with the observer's event key in their marker."""
        return [(c["issue"], c["body"]) for _, c in sorted(self.repo()["comments"].items(), key=lambda row: int(row[0]))
                if "eventkey=" in c["body"]]


class UserFindingTests(LiveBox):
    def test_a_result_and_a_failure_the_user_typed_are_posted_at_everything(self):
        report, purposes, calls = self.run_pass()
        self.assertEqual(["sort", "draft", "review", "review"], [p for p in purposes if p != "summary"][:4])
        self.assertEqual((2, 2, 4), (report["labels"], report["reviewed"], report["ignored"]))
        self.assertEqual(2, len(report["ready"]))
        posted = self.posted()
        self.assertEqual([(6, "result"), (6, "failure")], [(issue, body.split("type=")[1].split(" ")[0]) for issue, body in posted])
        for _, body in posted:
            self.assertIn("the agent did not run it", body)   # the user's run is told as the user's
        with store.Store(REPO, config=self.pcfg) as db:
            self.assertEqual(["verified", "verified"], sorted(db.event(key)["state"] for key in report["ready"]))
            first, second = (db.event(key)["candidate"] for key in report["ready"])
        ids = [s["id"] for s in self.data["sources"]]
        self.assertEqual([[ids[0], ids[1]], [ids[2], ids[3]]], [sorted(c["sources"], key=ids.index) for c in (first, second)])
        self.assertEqual(["ignored"] * 4, [db_state for db_state in self.states(ids[4:])])   # the rest was set aside

    def states(self, ids):
        with store.Store(REPO, config=self.pcfg) as db:
            return [db.observation(sid)["state"] for sid in ids]

    def test_both_memories_cite_the_users_message_and_the_agents_message(self):
        report, purposes, calls = self.run_pass()
        draft = next(payload for purpose, payload in calls if purpose == "draft")
        self.assertEqual([[1, 2], [3, 4]], [label["sources"] for label in draft["labels"]])
        self.assertEqual([True, False, True, False], [view["direct_user"] for view in draft["sources"]][:4])
        with store.Store(REPO, config=self.pcfg) as db:
            for key in report["ready"]:
                roles = {self.by_id[sid]["role"] for sid in db.event(key)["candidate"]["sources"]}
                self.assertEqual({"user", "assistant"}, roles)   # a result needs the agent's message, which the user's backs

    def test_a_finding_the_user_typed_needs_the_agents_message_before_it_is_posted(self):
        # The user's own words alone are not a result: code asks for an agent message among the cited ones.
        cut = copy.deepcopy(self.data["draft"])
        for candidate in cut:
            candidate["sources"] = [candidate["sources"][0]]
        report, purposes, calls = self.run_pass(draft=cut)
        self.assertEqual((["sort", "draft"], []), (purposes[:2], report["ready"]))
        self.assertEqual([], self.posted())

    def at_level(self, level):
        self.use(level=level)
        report, purposes, calls = self.run_pass()
        self.assertEqual(2, len(report["ready"]), level)
        self.assertEqual(2, len(self.posted()), level)

    def test_the_level_notes_only_posts_the_two_memories_too(self):
        self.at_level("notes")

    def test_the_level_assistant_posts_the_two_memories_too_and_asks_about_nothing(self):
        self.at_level("assistant")
        with store.Store(REPO, config=self.pcfg) as db:
            self.assertEqual([], db.waiting())

    def test_the_session_is_not_posted_when_the_user_typed_that_it_is_off_the_record(self):
        # The same stretch with a typed request in it: the hold the hook records for the phrase withholds the session
        # before any call is made, as before.
        text = "For this session do not post anything to the record."
        first = self.data["sources"][0]
        self.by_id[first["id"]]["text"] = text
        self.ready()
        self.capture()
        hold = observer_holds.make_hold({"source_id": first["id"], "role": "user", "direct_user": True, "text": "do not post"},
                                        REPO, "publish", {"session_id": "sess-u01"}, None)
        with store.Store(REPO, config=self.pcfg) as db:
            db.save_holds([hold])
        calls = []
        with mock.patch.object(worker.sources, "resolve", side_effect=lambda meta, root: self.by_id[meta["source_id"]]):
            report = worker.run_once(REPO, self.pcfg, self.project, self.models(calls=calls))
        self.assertEqual(([], []), ([p for p, _ in calls if p in ("sort", "draft", "review")], report["ready"]))
        self.assertEqual([], self.posted())
        with store.Store(REPO, config=self.pcfg) as db:
            self.assertEqual({"withheld"}, {db.observation(s["id"])["reason"] for s in self.data["sources"]})


class PromptTests(unittest.TestCase):
    """The lines the recorded answers rest on, in each of the three wordings: without one of them the models went back to
    ignoring, drafting nothing or withholding the stretch."""

    def test_the_sorter_counts_the_users_own_finding_and_is_not_put_off_by_what_the_user_asks_of_the_agent(self):
        text = runtime.PROMPTS["sort"]
        self.assertIn("A finding the user typed about their own work", text)
        self.assertIn("\"just acknowledge it\"", text)
        self.assertIn("does not make it routine", text)

    def test_the_drafter_writes_the_users_finding_as_the_users_report(self):
        text = runtime.PROMPTS["draft"]
        self.assertIn("a finding the user typed about their own work", text)
        self.assertIn("say in What ran that the user reported the run", text)

    def test_the_reviewer_accepts_the_users_finding_told_as_the_users_report_and_rejects_it_as_the_agents_own(self):
        text = runtime.PROMPTS["review"]
        self.assertIn("a finding the user typed about their own work", text)
        self.assertIn("reject it with attribution when it presents the run as the agent's own", text)

    def test_only_the_users_own_typed_words_keep_a_moment_off_the_record_in_all_three_wordings(self):
        for purpose in ("sort", "draft", "review"):
            text = runtime.PROMPTS[purpose]
            self.assertIn("not to post or record" if purpose != "review" else "not to post or record", text, purpose)
            self.assertIn("leave the recording to recortex or the observer", text, purpose)
        for purpose in ("sort", "draft"):
            self.assertIn("only the user's own typed words can say so", runtime.PROMPTS[purpose], purpose)
            self.assertIn("the agent's account of one", runtime.PROMPTS[purpose], purpose)
        self.assertIn("the agent's account of such a request withholds nothing", runtime.PROMPTS["review"])
        self.assertIn("the agent saying that it posted nothing", runtime.PROMPTS["review"])

    def test_the_sorter_leaves_a_moment_the_agent_says_it_just_recorded(self):
        self.assertIn("a moment that an agent message in the stretch says it has just recorded, with the address of its comment",
                      runtime.PROMPTS["sort"])


if __name__ == "__main__":
    unittest.main()
