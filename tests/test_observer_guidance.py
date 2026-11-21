"""Private presentation settings and evidence-bound task guidance, with invented records only."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import test_observer_advisory as receipts
from tests import test_scripts as fixtures
import guidance
import index
import observer_advisory as advisory
import observer_worker as worker
from tests import test_observer_worker as worker_fixtures

from examples.dashboard import preview


class HandoffStyleTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": folder.name}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_one_off_repeats_offer_once_and_never_change_style_implicitly(self):
        repo = "invented/tool"
        self.assertEqual("", guidance.hint(repo))
        for expected in (False, True, False):
            self.assertEqual({"style": "standard", "offer": expected}, guidance.change(repo, "once", "short"))
        self.assertEqual("", guidance.hint(repo))
        guidance.change(repo, "set", "short")
        self.assertIn("three", guidance.hint(repo.upper()))
        self.assertEqual("", guidance.hint("invented/other"))
        self.assertFalse(guidance.change(repo, "once", "short")["offer"])
        guidance.change(repo, "set", "standard")
        self.assertEqual("", guidance.hint(repo))

    def test_assistant_cannot_authorize_replacing_a_standing_user_decision(self):
        candidate = {"kind": "correction", "body": worker_fixtures.CORRECTION.replace(" -->", " replaces=123 -->", 1), "sources": ["a"]}
        assistant = {"role": "assistant", "text": "I switched methods.", "subagent": False, "sidechain": False}
        marker = worker.record_format.parse_marker(worker.record_format.first_lines(candidate["body"])[0])
        target = next(target for rel, target in worker.record_format.links(marker) if rel == "replaces")
        for kind in ("decision", "correction"):
            requires_user = worker.replaces_standing_decision(candidate, {"memories": [{"id": target, "kind": kind}]})
            self.assertTrue(requires_user)
            self.assertEqual("", worker.status_authority(candidate, {"a": assistant}, requires_user))

    def test_same_session_qualification_counts_even_when_draft_omits_it(self):
        def typed(text, session):
            return {"text": text, "session_id": session, "role": "user", "direct_user": True}
        supplied = {"a": typed("We choose batching.", "s"), "b": typed("Actually, for this run only.", "s")}
        messages = worker.decision_messages({"sources": ["a"]}, supplied)
        self.assertFalse(worker.standing_source("choose batching", messages))
        supplied["b"]["session_id"] = "elsewhere"
        self.assertTrue(worker.standing_source("choose batching", worker.decision_messages({"sources": ["a"]}, supplied)))

    def test_scope_is_checked_against_entire_user_message(self):
        quote = "choose batching"
        for msg in ("What if we choose batching?", "I choose batching for this trial", "For now, choose batching"):
            self.assertFalse(worker.standing_source(quote, [msg]))
        self.assertFalse(worker.standing_source(quote, ["We choose batching going forward.", "For this run only."]))
        self.assertFalse(worker.standing_source(quote, ["Let's try to choose batching once."]))
        self.assertFalse(worker.standing_source(quote, ["We choose batching. Do not change the default."]))
        self.assertTrue(worker.standing_source(quote, ["What if we choose batching?", "We choose batching going forward."]))

    def test_mixed_message_still_checks_qualifications_on_the_decision_itself(self):
        quote = "Choose batching."
        for text in (quote + " For this run only.",
                     "For this run only: " + quote + " What if we switch logging?",
                     'What if we switch logging? The example says "' + quote + '"',
                     "What if we choose batching? " + "a" * worker.SOURCE_TEXT + ". " + quote):
            with self.subTest(text=text[:70]):
                self.assertFalse(worker.standing_source(quote, [text]))


class DecisionPromptTests(unittest.TestCase):
    def setUp(self):
        case = fixtures.MindSandbox()
        case.setUp()
        self.addCleanup(case.doCleanups)
        tests = str(Path(__file__).resolve().parent)
        path = mock.patch.object(sys, "path", [tests] + sys.path)
        path.start()
        self.addCleanup(path.stop)
        preview.add_decision_change(case, "software")
        preview.add_project_map(case, "software")
        case.ready()
        case.in_process(extra={"CLAUDE_PROJECT_DIR": case.project})
        self.repo = case.config["repo"]
        self.db = index.connect(self.repo, create=False)
        self.addCleanup(self.db.close)
        self.allowed = guidance.recortex.allowed_users(self.repo)
        self.packet = {"freshness": {"age_seconds": 1, "sync_complete": True}, "scope": None}
        self.query = "Generate the cobalt delimiter summary for each release"

    def pointer(self, query):
        return advisory.change_pointer(self.db, self.repo, self.allowed, self.packet, query)

    def test_specific_task_explains_suggestion_with_both_decisions_and_supports_private_mute(self):
        pointer = self.pointer(self.query)
        self.assertIsNotNone(pointer)
        self.assertEqual("possible_change", pointer["reason"])
        self.assertIn("9701", pointer["text"])
        self.assertIn("9703", pointer["text"])
        self.packet["advisories"] = [pointer]
        store = receipts.Controls()
        self.assertEqual("", advisory.deliver(store, self.packet, self.query, "s0", mode="shadow"))
        self.assertEqual([], advisory.history(store))
        self.assertEqual(pointer["text"], advisory.deliver(store, self.packet, self.query, "s1", mode="deliver"))
        row, = advisory.history(store)
        self.assertEqual([9701, 9703], [source["comment"] for source in row["sources"]])
        advisory.feedback(store, row["id"], True)
        self.assertEqual("", advisory.deliver(store, self.packet, self.query, "s2", mode="deliver"))

    def test_copied_question_resolves_the_current_target_with_observer_off(self):
        import decision_impact
        model = decision_impact.from_index(self.db, self.repo, self.allowed)
        group = next(g for g in model["groups"] if g["old"]["id"] == 9701)
        target = next(i for i in group["items"] if i["kind"] == "section")
        data = guidance.context(self.repo, {}, self.allowed, group["id"], target["id"])
        self.assertTrue(data["available"])
        self.assertEqual(target["revision"], data["target"]["revision"])
        self.assertFalse(guidance.context(self.repo, {}, self.allowed, group["id"], "0" * 64)["available"])
        with self.assertRaises(ValueError):
            guidance.context(self.repo, {}, self.allowed, "../", target["id"])

    def test_return_cites_one_direct_step_and_ignores_inactive_work(self):
        message = guidance.return_pointer(self.db, self.repo, self.allowed)
        self.assertIn("Open judgment:", message)
        self.assertIn("9701", message)
        self.assertIn("9703", message)
        self.db.execute("DELETE FROM labels WHERE label='active'")
        self.assertEqual("", guidance.return_pointer(self.db, self.repo, self.allowed))

    def test_return_picks_up_the_accepted_followup_without_asking_again(self):
        import decision_impact
        data = decision_impact.from_index(self.db, self.repo, self.allowed)
        group = next(g for g in data["groups"] if g["old"]["id"] == 9701)
        step = next(i for i in group["items"] if i["kind"] == "step")
        step["review"] = {"verdict": "needs_update"}
        with mock.patch.object(decision_impact, "from_index", return_value=data):
            text = guidance.return_pointer(self.db, self.repo, self.allowed)
        self.assertIn("saved follow-up", text)
        self.assertNotIn("Open judgment:", text)

    def test_unrelated_or_stale_prompt_stays_quiet(self):
        self.assertIsNone(self.pointer("Check the spelling of translator credits"))
        self.assertIsNone(self.pointer("For issue #42, " + self.query))
        self.assertIsNone(self.pointer("What should we do next?"))
        self.packet["freshness"]["sync_complete"] = False
        self.assertIsNone(self.pointer(self.query))
