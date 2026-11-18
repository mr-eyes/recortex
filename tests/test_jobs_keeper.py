"""Tests of the manuscript keeper (1.5.0): when something is notable, stable and allowed, the two strong calls and the
checks, the draft that waits, and the grant before `observer.py drafts apply`. Nothing here reaches GitHub or a model; the
scripts run against tests/fake_gh.py and the calls are a fake `invoke_fn`."""

import hashlib
import json
import os
import re
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_manuscript_write as tw  # noqa: E402
import test_scripts as ts  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import keeper_rules  # noqa: E402
import write_rules  # noqa: E402

HOUR = 3600


# ---------------------------------------------------------------- the pure rules

def row(topic=5, heading="Results", why="claim", parts=(9302,), results=(9140,), decisions=()):
    plain = {"topic": topic, "heading": heading, "why": why, "parts": list(parts), "results": list(results),
             "decisions": list(decisions)}
    return dict(plain, key=write_rules.due_key(plain))


def part(kind="claim", state="backed", topic=5, standing=True, text=None, time_="2026-11-12T15:00:00Z",
         backed_at="2026-11-12T15:00:00Z", replaces=()):
    return {"part": kind, "state": state, "topic": topic, "standing": standing, "text": text, "time": time_,
            "backed_at": backed_at, "replaces": list(replaces)}


def facts(due=(), parts=None, texts=(), display=("figure", "table")):
    return {"due": list(due), "parts": parts if parts is not None else {}, "display": list(display),
            "texts": list(texts)}


def text(issue=5, sections=("Results",), time_="2026-11-10T10:00:00Z", links=()):
    return {"issue": issue, "sections": list(sections), "time": time_, "links": list(links)}


class DueKeyTests(unittest.TestCase):
    def test_the_key_is_the_hash_of_the_row_and_ignores_order_and_why(self):
        one = row(parts=(2, 1), results=(4, 3))
        two = row(parts=(1, 2), results=(3, 4), why="changed")
        self.assertEqual(one["key"], two["key"])
        self.assertRegex(one["key"], r"^[0-9a-f]{64}$")

    def test_a_changed_part_result_decision_topic_or_heading_changes_the_key(self):
        base = row()["key"]
        for other in (row(parts=(9302, 9303)), row(results=(9140, 9141)), row(decisions=(9201,)), row(topic=6),
                      row(heading="Limitations")):
            self.assertNotEqual(base, other["key"])


class NotableTests(unittest.TestCase):
    def test_n1_a_claim_that_became_backed_after_the_newest_text_change_leads_to_a_draft(self):
        found = keeper_rules.notable(facts([row()], {9302: part()}, [text(time_="2026-11-10T10:00:00Z")]))
        self.assertEqual([5], [t["topic"] for t in found["topics"]])
        self.assertEqual(["N1"], found["topics"][0]["tests"])
        self.assertEqual(["Results"], found["topics"][0]["headings"])
        # backed before the newest text change: the text change did not link it, but it is not a new finding
        old = keeper_rules.notable(facts([row()], {9302: part(backed_at="2026-11-09T10:00:00Z")},
                                         [text(time_="2026-11-10T10:00:00Z")]))
        self.assertEqual([], old["topics"])
        # with no text change at all, a backed claim is notable
        self.assertEqual(1, len(keeper_rules.notable(facts([row()], {9302: part()}))["topics"]))
        # a part that is not backed is not
        self.assertEqual([], keeper_rules.notable(facts([row()], {9302: part(state="planned")}))["topics"])

    def test_n2_a_correction_that_touched_the_text_leads_to_a_draft(self):
        found = keeper_rules.notable(facts([row(why="changed")], {9302: part(state="stands")}, [text()]))
        self.assertEqual(["N2"], found["topics"][0]["tests"])

    def test_a_part_due_only_for_methods_is_not_notable(self):
        found = keeper_rules.notable(facts([row(heading="Methods", why="methods", parts=(), decisions=(9201,))],
                                           {9302: part()}))
        self.assertEqual({"topics": [], "figures": 0, "in_doubt": 0, "message_revised": False}, found)

    def test_n3_a_planned_figure_joins_a_draft_of_its_topic_and_alone_is_a_line_only(self):
        figure = {9305: part("figure", "text", text=None, backed_at="")}
        joined = keeper_rules.notable(facts([row()], {9302: part(), **figure}))
        self.assertEqual(["N1", "N3"], joined["topics"][0]["tests"])
        self.assertEqual(0, joined["figures"])          # it goes into the draft, not into a line
        alone = keeper_rules.notable(facts([], figure))
        self.assertEqual([], alone["topics"])
        self.assertEqual(1, alone["figures"])
        # one a text change already links, or one not yet in a body, is not counted
        for fig in (part("figure", "text", text=9400), part("figure", "can be made")):
            self.assertEqual(0, keeper_rules.notable(facts([], {9305: fig}))["figures"])

    def test_n4_a_part_in_doubt_or_a_revised_message_is_a_line_only_never_a_draft(self):
        parts = {9302: part(state="doubt"), 9301: part("message", "stands", replaces=(9300,),
                                                        time_="2026-11-12T10:00:00Z")}
        found = keeper_rules.notable(facts([], parts, [text(links=(9302,), time_="2026-11-10T10:00:00Z")]))
        self.assertEqual([], found["topics"])
        self.assertEqual(1, found["in_doubt"])
        self.assertTrue(found["message_revised"])
        quiet = keeper_rules.notable(facts([], {9301: part("message", "stands", replaces=(), time_="2026-11-12T10:00:00Z")}))
        self.assertFalse(quiet["message_revised"])

    def test_the_line_of_n3_is_fixed_words_and_an_integer(self):
        self.assertEqual("Manuscript: 2 planned figures or tables now exist and are not in the text.",
                         keeper_rules.figures_line(2))
        self.assertEqual("", keeper_rules.figures_line(0))

    def test_the_key_of_a_topic_follows_its_notable_rows_only(self):
        one = keeper_rules.notable(facts([row(), row(heading="Methods", why="methods", parts=(), decisions=(1,))],
                                         {9302: part()}))
        two = keeper_rules.notable(facts([row(), row(heading="Methods", why="methods", parts=(), decisions=(2,))],
                                         {9302: part()}))
        self.assertEqual(one["topics"][0]["key"], two["topics"][0]["key"])
        three = keeper_rules.notable(facts([row(results=(9140, 9150))], {9302: part()}))
        self.assertNotEqual(one["topics"][0]["key"], three["topics"][0]["key"])


class StableAndRareTests(unittest.TestCase):
    def test_settle_keeps_the_time_of_an_unchanged_row_and_restarts_a_changed_one(self):
        seen = keeper_rules.settle({}, {5: "a" * 64}, 1000)
        self.assertEqual({"5": {"key": "a" * 64, "since": 1000}}, seen)
        self.assertEqual(1000, keeper_rules.settle(seen, {5: "a" * 64}, 2000)["5"]["since"])
        self.assertEqual(2000, keeper_rules.settle(seen, {5: "b" * 64}, 2000)["5"]["since"])
        self.assertEqual({}, keeper_rules.settle(seen, {}, 2000))                      # no longer notable: forgotten
        self.assertEqual(3000, keeper_rules.settle({"5": {"key": "a" * 64, "since": 9999}}, {5: "a" * 64}, 3000)["5"]["since"])

    def test_a_due_row_is_stable_after_thirty_minutes(self):
        seen = {"5": {"key": "a" * 64, "since": 0}}
        self.assertFalse(keeper_rules.stable(seen, 5, "a" * 64, 10 * 60))
        self.assertFalse(keeper_rules.stable(seen, 5, "a" * 64, 30 * 60 - 1))
        self.assertTrue(keeper_rules.stable(seen, 5, "a" * 64, 30 * 60))
        self.assertFalse(keeper_rules.stable(seen, 5, "b" * 64, 3 * HOUR))
        self.assertFalse(keeper_rules.stable({}, 5, "a" * 64, 3 * HOUR))
        self.assertFalse(keeper_rules.stable("not a dict", 5, "a" * 64, 3 * HOUR))

    def test_quiet_needs_ten_minutes_without_a_source_unless_a_session_just_started(self):
        self.assertTrue(keeper_rules.quiet(None, 1000))
        self.assertFalse(keeper_rules.quiet(1000 - 5 * 60, 1000))
        self.assertTrue(keeper_rules.quiet(1000 - 10 * 60, 1000))
        self.assertTrue(keeper_rules.quiet(1000 - 5 * 60, 1000, session_started=True))

    def draft(self, topic=5, state="waiting", key="a" * 64, created=0.0):
        return {"topic": topic, "state": state, "due_hash": key, "created": created}

    def test_a_topic_with_a_waiting_or_put_off_draft_is_not_proposed(self):
        for state in ("waiting", "later"):
            self.assertEqual("waiting", keeper_rules.held_off(5, "a" * 64, [self.draft(state=state)], 10, 2))
        self.assertEqual("", keeper_rules.held_off(6, "a" * 64, [self.draft(state="waiting")], 10, 2))

    def test_a_dropped_or_refused_row_holds_the_topic_until_its_due_row_changes(self):
        for state in ("dropped", "refused"):
            drafts = [self.draft(state=state)]
            self.assertEqual(state, keeper_rules.held_off(5, "a" * 64, drafts, 10, 2))
            self.assertEqual("", keeper_rules.held_off(5, "b" * 64, drafts, 10 + 30 * HOUR, 2))

    def test_a_topic_is_proposed_once_in_twenty_four_hours_and_two_a_day(self):
        applied = [self.draft(state="applied", created=0.0)]
        self.assertEqual("topic_day", keeper_rules.held_off(5, "b" * 64, applied, 23 * HOUR, 2))
        self.assertEqual("", keeper_rules.held_off(5, "b" * 64, applied, 25 * HOUR, 2))
        two = [self.draft(topic=6, state="applied", created=10.0), self.draft(topic=7, state="dropped", created=20.0)]
        self.assertEqual("cap", keeper_rules.held_off(5, "b" * 64, two, 1 * HOUR, 2))
        self.assertEqual("", keeper_rules.held_off(5, "b" * 64, two, 25 * HOUR, 2))
        # a refused or stale row is no proposal: the user was not asked
        spent = [self.draft(topic=6, state="refused"), self.draft(topic=7, state="stale")]
        self.assertEqual("", keeper_rules.held_off(5, "b" * 64, spent, 1 * HOUR, 2))

    def test_the_keeper_stays_off_a_topic_the_main_session_started_in_the_last_two_hours(self):
        records = [{"issue": 5, "nonce": "a" * 32, "at": 1000.0}]
        self.assertTrue(keeper_rules.foreign_start(records, 5, [], 1000.0 + 1 * HOUR))
        self.assertFalse(keeper_rules.foreign_start(records, 5, [], 1000.0 + 3 * HOUR))
        self.assertFalse(keeper_rules.foreign_start(records, 6, [], 1000.0 + 1 * HOUR))
        self.assertFalse(keeper_rules.foreign_start(records, 5, ["a" * 32], 1000.0 + 1 * HOUR))   # the keeper's own


class FixedWordsTests(unittest.TestCase):
    def test_the_question_names_the_short_id_and_quotes_the_fixed_line(self):
        self.assertEqual("update the Results of #5", keeper_rules.question_line(["Results"], 5))
        self.assertEqual("update the Results and Limitations of #5", keeper_rules.question_line(["Results", "Limitations"], 5))
        self.assertEqual("update the Results, Limitations and Methods of #5",
                         keeper_rules.question_line(["Results", "Limitations", "Methods"], 5))
        question = keeper_rules.question("abcd1234", ["Results"], 5)
        self.assertEqual("Draft abcd1234: update the Results of #5. Approve it, or not?", question)
        import observer_sources
        self.assertTrue(observer_sources.OWN_DIALOG.search(question))   # the user's answer is never a source

    def test_a_heading_with_markup_or_a_newline_is_not_quoted(self):
        for odd in ("Results\n[click](x)", "<b>Results</b>", "A" * 80, ""):
            self.assertEqual("update the text of #5", keeper_rules.question_line([odd], 5), odd)

    def test_the_waiting_line_is_fixed_words_and_integers(self):
        self.assertEqual("recortex: 1 draft of the paper's text waits for the user's answer (#5). Load the recortex skill, "
                         "run its observer.py drafts, show what would change and ask with the multiple-choice prompt as "
                         "the skill's \"Drafts that wait\" section says.", keeper_rules.draft_line(1, 5))
        self.assertIn("2 drafts of the paper's text wait for the user's answer (#5, #6).", keeper_rules.draft_line(2, [5, 6]))
        self.assertEqual("", keeper_rules.draft_line(0, 5))
        self.assertEqual("", keeper_rules.draft_line(1, ["5; rm"]))

    def test_the_grant_key_is_a_hash_that_cannot_be_an_event_key_of_a_memory(self):
        key = keeper_rules.grant_key("abcd1234")
        self.assertRegex(key, r"^[0-9a-f]{32}$")
        self.assertNotEqual(key, keeper_rules.grant_key("abcd1235"))
        self.assertNotEqual("abcd1234" + "0" * 24, key)


if __name__ == "__main__":
    unittest.main()
