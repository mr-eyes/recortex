"""Tests of tasks, steps and marks (1.5.0): the format of a steps memory and of a mark, the linter's rules, the index's
tables, what a step is (task_rules.assess) and the commands of tasks.py. Nothing here reaches GitHub; the scripts run
against tests/fake_gh.py."""

import json
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_scripts as ts  # noqa: E402

sys.path.insert(0, ts.SCRIPTS)
import lint_drafts  # noqa: E402
import recortex  # noqa: E402
import record_format  # noqa: E402

EXAMPLE = ts.EXAMPLE
CHAIN = os.path.join(EXAMPLE, "chain")


def steps_text(at="2026-03-12T11:00-08:00", plain="let the reader carry a quoted field across lines and prove it on the "
               "conformance set", agreed=None, steps=("Carry the quote state across physical lines",
                                                      "Rerun the conformance set"),
               done="All 12 quoted-newline files in conformance set v2 pass.", marker="", rest=""):
    stamp = at[:16].replace("T", " ") + " PST"
    agreed = agreed if agreed is not None else "By the project lead in the chat, %s:\n\n> Yes, do the two steps." % stamp
    lines = "\n".join("%d. %s" % (n, s) for n, s in enumerate(steps, 1))
    return ("<!-- ghim:event type=steps at=%s%s -->\n### Steps: %s - quoted newlines\n\n"
            "**In plain words, we agreed to:** %s\n\n**Agreed:** %s\n\n**Steps:**\n%s\n\n**Done when:** %s\n%s"
            % (at, marker, stamp, plain, agreed, lines, done, rest))


def result_text(marker="", at="2026-03-12T15:30-08:00", kind="result"):
    """A result (or, with kind, another kind of memory drawn from the examples) with `marker` after its time."""
    text = recortex.read_text(os.path.join(CHAIN, "7-result.md"))
    text = text.replace(" done=9108:1", marker)
    return text


def decision_text(marker=""):
    text = recortex.read_text(os.path.join(EXAMPLE, "chain", "5-decision.md"))
    return text.replace(" replaces=9102", marker)


def with_mark(text, mark):
    """`text` with `mark` (for example " done=9108:1") added to its marker, after the time."""
    return re.sub(r"( at=\S+)", lambda m: m.group(1) + mark, text, count=1)


def lint(text, kind, **kw):
    return lint_drafts.lint_text(text, kind=kind, config=ts.EXAMPLE_CONFIG, **kw)


def codes(findings, level="error"):
    return sorted({f.code for f in findings if f.level == level})


class FormatTests(unittest.TestCase):
    """record_format: the kind steps and the two mark keys."""

    def test_steps_is_an_event_a_headed_a_memory_and_an_observed_kind(self):
        for table in (record_format.EVENT_TYPES, record_format.HEADED_TYPES, record_format.MEMORY_TYPES,
                      record_format.OBSERVED_TYPES):
            self.assertIn("steps", table)
        event = record_format.parse_event(steps_text())
        self.assertEqual(("steps", "2026-03-12T11:00-08:00"), (event["type"], event["at"]))
        self.assertEqual("let the reader carry a quoted field across lines and prove it on the conformance set",
                         record_format.plain(steps_text(), "steps"))

    def test_a_heading_that_does_not_agree_with_the_marker_is_not_a_steps_event(self):
        text = steps_text().replace("type=steps", "type=result")
        self.assertEqual("other", record_format.parse_event(text)["type"])

    def test_steps_list_reads_the_numbered_lines_of_the_steps_field(self):
        self.assertEqual(["Carry the quote state across physical lines", "Rerun the conformance set"],
                         record_format.steps_list(steps_text()))
        self.assertEqual([], record_format.steps_list(result_text()))
        crlf = steps_text().replace("\n", "\r\n")
        self.assertEqual(record_format.steps_list(steps_text()), record_format.steps_list(crlf))

    def test_steps_list_is_lenient_and_capped(self):
        text = steps_text(steps=["step %d" % n for n in range(1, 26)])
        self.assertEqual(20, len(record_format.steps_list(text)))
        stray = steps_text().replace("2. Rerun", "stray words\n2. Rerun")
        self.assertEqual(2, len(record_format.steps_list(stray)))

    def test_marks_read_one_to_four_groups_of_step_numbers(self):
        marker = record_format.parse_marker("<!-- ghim:event type=result at=2026-03-12T15:30-08:00 "
                                            "done=9108:1,2;9110:3 -->")
        self.assertEqual([("done", 9108, 1), ("done", 9108, 2), ("done", 9110, 3)], record_format.marks(marker))
        marker = record_format.parse_marker("<!-- ghim:event type=decision at=2026-03-12T15:30-08:00 "
                                            "dropped=9108:20 -->")
        self.assertEqual([("dropped", 9108, 20)], record_format.marks(marker))
        self.assertEqual([], record_format.marks(None))

    def test_a_bad_mark_value_is_ignored_whole_and_the_others_are_kept(self):
        for value in ("9108:0", "9108:21", "9108", "9108:1,", "9108:1;", ":1", "9108:1;9109:1;9110:1;9111:1;9112:1",
                      "9108:1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1", "0:1", "9108: 1"):
            marker = {"type": "result", "at": "2026-03-12T15:30-08:00", "done": value, "dropped": "9111:2"}
            self.assertEqual([("dropped", 9111, 2)], record_format.marks(marker), value)

    def test_the_same_step_is_listed_once(self):
        marker = {"done": "9108:1,2;9108:2"}
        self.assertEqual([("done", 9108, 1), ("done", 9108, 2)], record_format.marks(marker))

    def test_a_mark_is_not_a_link(self):
        marker = record_format.parse_marker("<!-- ghim:event type=checkpoint at=2026-03-12T15:30-08:00 done=9108:1 -->")
        self.assertEqual([], record_format.links(marker))
        self.assertEqual([("done", 9108, 1)], record_format.marks(marker))
        self.assertIn("done", record_format.MARKER_KEYS)
        self.assertIn("dropped", record_format.MARKER_KEYS)
        self.assertNotIn("done", record_format.LINK_KINDS)

    def test_a_steps_memory_links_only_to_steps_with_replaces(self):
        self.assertTrue(record_format.link_allowed("steps", "replaces", "steps"))
        for key in record_format.LINK_KINDS:
            for source in record_format.MEMORY_TYPES:
                for target in record_format.MEMORY_TYPES:
                    if "steps" in (source, target) and (source, key, target) != ("steps", "replaces", "steps"):
                        self.assertFalse(record_format.link_allowed(source, key, target), (source, key, target))

    def test_agreed_forms(self):
        self.assertEqual("dialog", record_format.agreed_form("Approved in the plan dialog, 2026-03-12 11:00 PST."))
        self.assertEqual("chat", record_format.agreed_form("By the project lead in the chat, 2026-03-12 11:00 PST:\n\n"
                                                            "> go ahead"))
        self.assertIsNone(record_format.agreed_form("By the project lead in the chat, 2026-03-12 11:00 PST:"))
        self.assertIsNone(record_format.agreed_form("Approved in the plan dialog."))
        self.assertIsNone(record_format.agreed_form("Approved in the plan dialog, 2026-03-12 11:00 PST. And more."))
        self.assertIsNone(record_format.agreed_form(""))


class LinterTests(unittest.TestCase):
    """The linter's rules for a steps memory, the mark keys and a task list (each fires, and each stays quiet)."""

    def test_the_example_steps_memory_and_result_pass(self):
        self.assertEqual([], codes(lint(recortex.read_text(os.path.join(CHAIN, "6-steps.md")), "steps")))
        found = lint(recortex.read_text(os.path.join(CHAIN, "7-result.md")), "result")
        self.assertEqual([], codes(found))
        self.assertEqual(["not-checked"], codes(found, "warning"))   # the mark's target needs the record

    def test_the_template_passes_in_template_mode(self):
        text = recortex.read_text(os.path.join(ts.TEMPLATES, "comment-steps.md"))
        self.assertEqual([], [str(f) for f in lint(text, "steps", template=True, allow_placeholders=True)])
        self.assertIn("template-field", codes(lint(text, "steps")))

    def test_a_steps_memory_has_its_four_fields_in_order(self):
        self.assertEqual([], codes(lint(steps_text(), "steps")))
        for label in ("**Agreed:**", "**Steps:**", "**Done when:**"):
            text = steps_text().replace(label, "**Other:**")
            self.assertTrue({"section-missing", "section-order"} & set(codes(lint(text, "steps"))), label)

    def test_agreed_takes_one_of_two_forms(self):
        dialog = steps_text(agreed="Approved in the plan dialog, 2026-03-12 11:00 PST.")
        self.assertEqual([], codes(lint(dialog, "steps")))
        for agreed in ("The user liked it.", "Approved in the plan dialog.", "By the lead in the chat, 2026-03-12 11:00 "
                       "PST:", "Approved in the plan dialog, 2026-03-12 11:00 PST. Fine."):
            self.assertIn("steps-agreed", codes(lint(steps_text(agreed=agreed), "steps")), agreed)

    def test_the_steps_are_a_numbered_list_of_one_to_twenty_one_line_steps(self):
        self.assertEqual([], codes(lint(steps_text(steps=["step %d" % n for n in range(1, 21)]), "steps")))
        self.assertIn("steps-list", codes(lint(steps_text(steps=["step %d" % n for n in range(1, 22)]), "steps")))
        self.assertIn("steps-list", codes(lint(steps_text(steps=[]), "steps")))
        out_of_order = steps_text().replace("2. Rerun", "3. Rerun")
        self.assertIn("steps-list", codes(lint(out_of_order, "steps")))
        stray = steps_text().replace("2. Rerun the conformance set", "2. Rerun the conformance set\nand then more")
        self.assertIn("steps-list", codes(lint(stray, "steps")))
        long_step = steps_text(steps=["x" * 301])
        self.assertIn("steps-list", codes(lint(long_step, "steps")))
        self.assertEqual([], codes(lint(steps_text(steps=["x" * 300]), "steps")))

    def test_done_when_is_one_line(self):
        self.assertEqual([], codes(lint(steps_text(done="The check passes."), "steps")))
        self.assertIn("field-form", codes(lint(steps_text(done="First line.\nSecond line."), "steps")))
        self.assertIn("field-empty", codes(lint(steps_text(done=""), "steps")))

    def test_the_plain_line_of_steps_follows_the_plain_line_rules(self):
        self.assertIn("plain", codes(lint(steps_text(plain="do it"), "steps")))
        repeated = steps_text(plain="we agreed to do the two steps in that order and no others at all")
        self.assertIn("plain", codes(lint(repeated, "steps")))
        period = steps_text(plain="do the two steps in that order and no other steps at all.")
        self.assertIn("plain", codes(lint(period, "steps")))
        number = steps_text(plain="do the 3 steps in that order and no other steps at all")
        self.assertIn("plain", codes(lint(number, "steps")))

    def test_steps_carry_no_mark_and_no_evidence(self):
        self.assertIn("mark-kind", codes(lint(steps_text(marker=" done=9108:1"), "steps")))
        evidence = steps_text(rest="\n**Evidence:**\n- table plots\n")
        self.assertIn("evidence", codes(lint(evidence, "steps")))

    def test_a_steps_memory_may_replace_only_with_replaces(self):
        self.assertEqual([], codes(lint(steps_text(marker=" replaces=9108"), "steps")))
        self.assertIn("link-kind", codes(lint(steps_text(marker=" because=9108"), "steps")))
        self.assertIn("link-kind", codes(lint(steps_text(marker=" checks=9108"), "steps")))

    def test_done_is_for_a_result_a_failure_a_correction_and_a_checkpoint(self):
        self.assertEqual([], codes(lint(result_text(" done=9108:1"), "result")))
        failure = recortex.read_text(os.path.join(CHAIN, "2-failure.md")).replace(" checks=9103", " done=9108:1")
        self.assertEqual([], codes(lint(failure, "failure")))
        for kind, text in (("decision", decision_text(" done=9108:1")),
                           ("idea", with_mark(recortex.read_text(os.path.join(CHAIN, "1-idea.md")), " done=9108:1")),
                           ("question", with_mark(recortex.read_text(os.path.join(CHAIN, "4-question.md")),
                                                  " done=9108:1"))):
            self.assertIn("mark-kind", codes(lint(text, kind)), kind)

    def test_a_checkpoint_may_carry_done_and_still_no_link(self):
        found = lint(with_mark(ts.CHECKPOINT_060, " done=9108:2"), "checkpoint")
        self.assertEqual([], codes(found))
        self.assertIn("link-kind", codes(lint(with_mark(ts.CHECKPOINT_060, " because=9108"), "checkpoint")))

    def test_dropped_is_for_a_decision_only(self):
        self.assertEqual([], codes(lint(decision_text(" dropped=9108:2"), "decision")))
        self.assertIn("mark-kind", codes(lint(result_text(" dropped=9108:1"), "result")))

    def test_a_mark_value_must_have_the_form(self):
        for value in ("9108", "9108:0", "9108:21", "9108:1;", "9108:1,1", "9108:1;9108:1", "abc:1",
                      "9108:1;9109:1;9110:1;9111:1;9112:1"):
            self.assertIn("mark-value", codes(lint(result_text(" done=" + value), "result")), value)
        for value in ("9108:1", "9108:1,2,3", "9108:1;9110:2,4", "9108:20"):
            self.assertEqual([], codes(lint(result_text(" done=" + value), "result")), value)

    def test_a_mark_key_stands_once(self):
        self.assertIn("marker-key", codes(lint(result_text(" done=9108:1 done=9108:2"), "result")))

    def test_a_reconstructed_memory_carries_no_mark(self):
        self.assertEqual([], codes(lint(ts.RECONSTRUCTED_060, "failure")))
        self.assertIn("mark-kind", codes(lint(with_mark(ts.RECONSTRUCTED_060, " done=9108:1"), "failure")))

    def test_a_misspelled_mark_key_is_still_an_unknown_marker_key(self):
        self.assertIn("marker-key", codes(lint(result_text(" finished=9108:1"), "result")))

    def test_a_task_list_under_plan_is_allowed_and_checked(self):
        body = recortex.read_text(os.path.join(EXAMPLE, "task-newlines.md"))
        found = lint(body, "active", allow_placeholders=True)
        self.assertEqual([], codes(found))
        checked = body.replace("- [ ] 1.", "- [x] 1.")
        self.assertEqual([], codes(lint(checked, "active", allow_placeholders=True)))
        for bad in ("- [ ] 1 Carry the quote state", "- [y] 1. Carry the quote state", "- [ ] 2. Carry the quote state",
                    "- [ ]  1. Carry the quote state", "- [ ] 1. "):
            broken = body.replace("- [ ] 1. Carry the quote state across physical lines", bad)
            self.assertIn("plan-list", codes(lint(broken, "active", allow_placeholders=True)), bad)

    def test_an_active_body_with_the_old_plan_table_still_passes(self):
        body = recortex.read_text(os.path.join(EXAMPLE, "task-newlines.md")).replace(
            "- [ ] 1. Carry the quote state across physical lines\n- [ ] 2. Rerun the conformance set",
            "| Step | Owner | State |\n|---|---|---|\n| Carry the quote state | Claude session | running |")
        self.assertEqual([], codes(lint(body, "active", allow_placeholders=True)))

    def test_the_active_template_passes_in_template_mode(self):
        text = recortex.read_text(os.path.join(ts.TEMPLATES, "active-task.md"))
        self.assertEqual([], [str(f) for f in lint(text, "active", template=True, allow_placeholders=True)])


class CompatStepsTests(unittest.TestCase):
    """The frozen 0.5.0 parser reads the new format: a steps comment is a comment of type other at its marker time (as an
    idea is), and a result that carries done= is still a result at its time, with no error."""

    def frozen(self, drafts):
        env = ts.environment_for(os.environ, {"PYTHONDONTWRITEBYTECODE": "1"})
        run = subprocess.run([sys.executable, "-I", "-c", ts.COMPAT_RUNNER, ts.COMPAT], input=json.dumps(drafts),
                             capture_output=True, text=True, env=env, cwd=ts.COMPAT)
        self.assertEqual(0, run.returncode, run.stderr)
        return json.loads(run.stdout)

    def test_steps_and_marks_read_as_events_in_0_5_0(self):
        steps = recortex.read_text(os.path.join(CHAIN, "6-steps.md"))
        result = recortex.read_text(os.path.join(CHAIN, "7-result.md"))
        dropped = decision_text(" dropped=9108:2")
        checkpoint_marked = with_mark(ts.CHECKPOINT_060, " done=9108:2")
        found = self.frozen([("steps", "steps", steps), ("result", "result", result), ("dropped", "decision", dropped),
                             ("checkpoint", "checkpoint", checkpoint_marked)])
        self.assertEqual({"type": "other", "at": "2026-03-12T11:00-08:00", "reconstructed": False, "errors": None},
                         found["steps"])
        self.assertEqual({"type": "result", "at": "2026-03-12T15:30-08:00", "reconstructed": False, "errors": []},
                         found["result"])
        self.assertEqual(("decision", []), (found["dropped"]["type"], found["dropped"]["errors"]))
        self.assertEqual(("checkpoint", []), (found["checkpoint"]["type"], found["checkpoint"]["errors"]))


# ---------------------------------------------------------------- what a step is (pure)

import task_rules  # noqa: E402

T0 = "2026-03-12T10:00:00Z"


def memory(cid, texts, time=T0, replaces=(), state="stands"):
    return {"comment": cid, "time": time, "texts": list(texts), "replaces": list(replaces), "state": state}


def told(kind="result", state="stands", time="2026-03-12T12:00:00Z", reconstructed=False):
    return {"kind": kind, "state": state, "time": time, "reconstructed": reconstructed}


class RulesTests(unittest.TestCase):
    """task_rules: numbering, the four states, and what a mark counts for."""

    def steps(self):
        return task_rules.numbered([memory(10, ["a", "b", "c"])])

    def states(self, marks, items, blocked=False, steps=None):
        return [row["state"] for row in task_rules.assess(steps or self.steps(), marks, items, blocked)]

    def test_every_step_is_open_without_a_mark(self):
        self.assertEqual(["open", "open", "open"], self.states([], {}))

    def test_a_told_memory_marks_a_step_done(self):
        marks = [(20, "done", 10, 2)]
        self.assertEqual(["open", "done", "open"], self.states(marks, {20: told()}))
        for kind in ("failure", "correction", "checkpoint"):
            self.assertEqual(["open", "done", "open"], self.states(marks, {20: told(kind)}), kind)
        found = task_rules.assess(self.steps(), marks, {20: told()})
        self.assertEqual(20, found[1]["by"])
        self.assertIsNone(found[0]["by"])

    def test_a_decision_drops_a_step_and_dropped_beats_done(self):
        marks = [(20, "dropped", 10, 3), (21, "done", 10, 3), (22, "done", 10, 1)]
        items = {20: told("decision"), 21: told(), 22: told()}
        self.assertEqual(["done", "open", "dropped"], self.states(marks, items))
        self.assertEqual(20, task_rules.assess(self.steps(), marks, items)[2]["by"])

    def test_a_mark_on_the_wrong_kind_counts_for_nothing(self):
        self.assertEqual(["open", "open", "open"], self.states([(20, "done", 10, 1)], {20: told("decision")}))
        self.assertEqual(["open", "open", "open"], self.states([(20, "dropped", 10, 1)], {20: told("result")}))
        self.assertEqual(["open", "open", "open"], self.states([(20, "done", 10, 1)], {20: told("idea")}))

    def test_a_mark_by_a_memory_that_is_not_told_counts_for_nothing(self):
        """The told check: items holds only the memories of allowed accounts that nobody else edited."""
        self.assertEqual(["open", "open", "open"], self.states([(20, "done", 10, 1)], {}))
        self.assertEqual(["open", "open", "open"], self.states([(20, "done", 10, 1)], {99: told()}))

    def test_a_mark_on_a_replaced_or_set_aside_or_reconstructed_memory_counts_for_nothing(self):
        for item in (told(state="replaced"), told("decision", state="set aside"), told(reconstructed=True)):
            kind = "dropped" if item["kind"] == "decision" else "done"
            self.assertEqual(["open", "open", "open"], self.states([(20, kind, 10, 1)], {20: item}))

    def test_a_mark_on_a_step_that_does_not_exist_counts_for_nothing(self):
        marks = [(20, "done", 10, 4), (21, "done", 11, 1)]
        self.assertEqual(["open", "open", "open"], self.states(marks, {20: told(), 21: told()}))

    def test_a_mark_on_a_replaced_steps_memory_counts_for_nothing(self):
        both = [memory(10, ["a", "b"], state="replaced"), memory(11, ["a", "b", "c"], time="2026-03-12T11:00:00Z",
                                                                replaces=[10])]
        steps = task_rules.numbered(both)
        self.assertEqual([11, 11, 11], [row["comment"] for row in steps])
        marks = [(20, "done", 10, 1), (21, "done", 11, 2)]
        self.assertEqual(["open", "done", "open"], self.states(marks, {20: told(), 21: told()}, steps=steps))

    def test_a_failure_that_nothing_followed_blocks_the_first_open_step(self):
        marks = [(20, "done", 10, 1)]
        self.assertEqual(["done", "blocked", "open"], self.states(marks, {20: told()}, blocked=True))
        self.assertEqual(["blocked", "open", "open"], self.states([], {}, blocked=True))
        self.assertEqual(["done", "done", "done"], self.states([(20, "done", 10, n) for n in (1, 2, 3)], {20: told()},
                                                              blocked=True))

    def test_the_oldest_mark_decides_who_checked_a_step(self):
        marks = [(21, "done", 10, 1), (20, "done", 10, 1)]
        items = {20: told(time="2026-03-12T12:00:00Z"), 21: told(time="2026-03-12T13:00:00Z")}
        self.assertEqual(20, task_rules.assess(self.steps(), marks, items)[0]["by"])

    def test_steps_are_numbered_across_two_memories_oldest_first(self):
        found = task_rules.numbered([memory(12, ["d", "e"], time="2026-03-13T10:00:00Z"), memory(10, ["a", "b", "c"])])
        self.assertEqual([(1, 10, 1, "a"), (2, 10, 2, "b"), (3, 10, 3, "c"), (4, 12, 1, "d"), (5, 12, 2, "e")],
                         [(r["n"], r["comment"], r["ordinal"], r["text"]) for r in found])

    def test_a_restating_memory_takes_the_place_of_the_one_it_replaces(self):
        first = memory(10, ["a", "b"], state="replaced")
        second = memory(11, ["c"], time="2026-03-12T11:00:00Z")
        third = memory(12, ["a", "b", "x"], time="2026-03-12T12:00:00Z", replaces=[10])
        found = task_rules.numbered([first, second, third])
        self.assertEqual([(1, 12, "a"), (2, 12, "b"), (3, 12, "x"), (4, 11, "c")],
                         [(r["n"], r["comment"], r["text"]) for r in found])

    def test_a_chain_of_restatements_keeps_the_oldest_place(self):
        found = task_rules.numbered([memory(10, ["a"], state="replaced"),
                                     memory(11, ["b"], time="2026-03-12T10:30:00Z"),
                                     memory(12, ["a2"], time="2026-03-12T11:00:00Z", replaces=[10], state="replaced"),
                                     memory(13, ["a3"], time="2026-03-12T12:00:00Z", replaces=[12])])
        self.assertEqual(["a3", "b"], [r["text"] for r in found])

    def test_finished_needs_every_step_done_or_dropped_and_one_done(self):
        steps = self.steps()
        every = [(20, "done", 10, n) for n in (1, 2, 3)]
        items = {20: told(), 21: told("decision")}
        self.assertTrue(task_rules.finished(task_rules.assess(steps, every, items)))
        self.assertFalse(task_rules.finished(task_rules.assess(steps, every[:2], items)))
        dropped = [(21, "dropped", 10, n) for n in (1, 2, 3)]
        self.assertFalse(task_rules.finished(task_rules.assess(steps, dropped, items)))
        mixed = every[:2] + [(21, "dropped", 10, 3)]
        self.assertTrue(task_rules.finished(task_rules.assess(steps, mixed, items)))
        self.assertFalse(task_rules.finished([]))

    def test_the_list_marks_what_the_plugin_wrote(self):
        found = task_rules.assess(self.steps(), [(20, "done", 10, 2), (21, "dropped", 10, 3)],
                                  {20: told(), 21: told("decision")})
        lines = task_rules.list_lines(found)
        self.assertEqual(["- [ ] 1. a", "- [x] 2. b", "- [x] 3. ~~c~~ (dropped)"], lines)
        section = task_rules.plan_section(found)
        self.assertEqual(lines, section.split("\n")[:3])
        self.assertRegex(section.split("\n")[3], r"^<!-- ghim:list sha256 [0-9a-f]{64} -->$")

    def test_a_step_text_cannot_close_the_mark_or_open_markup(self):
        steps = task_rules.numbered([memory(10, ["x <!-- ghim:list sha256 " + "0" * 64 + " --> y", "line\x00two  words"])])
        lines = task_rules.list_lines(task_rules.assess(steps, [], {}))
        self.assertNotIn("<", "".join(lines))
        self.assertEqual("- [ ] 2. line two words", lines[1])

    def test_hand_changed_tells_a_list_by_hand_from_a_list_that_is_behind(self):
        found = task_rules.assess(self.steps(), [], {})
        body = task_rules.task_body("do it", 5, "it works", found, "2026-03-04 14:10 PST")
        self.assertFalse(task_rules.hand_changed(body))
        self.assertTrue(task_rules.hand_changed(body.replace("- [ ] 2. b", "- [x] 2. b")))
        self.assertTrue(task_rules.hand_changed(body.replace("- [ ] 2. b", "- [ ] 2. b changed")))
        self.assertTrue(task_rules.hand_changed(body.replace("- [ ] 3. c\n", "")))
        behind = task_rules.assess(self.steps(), [(20, "done", 10, 1)], {20: told()})
        self.assertFalse(task_rules.list_agrees(body, behind))
        self.assertFalse(task_rules.hand_changed(body))   # behind is not by hand
        self.assertTrue(task_rules.hand_changed(body.replace("\r", "").replace("\n", "\r\n").replace("[ ] 1", "[x] 1")))
        self.assertFalse(task_rules.hand_changed(body.replace("\n", "\r\n")))   # GitHub may give CRLF

    def test_a_list_that_code_never_wrote_is_by_hand_only_with_a_checked_box(self):
        plain = "## Goal\n\nx\n\n## Plan\n\n- [ ] 1. a\n- [ ] 2. b\n\n## Result\n\nNot finished.\n"
        self.assertFalse(task_rules.hand_changed(plain))
        self.assertTrue(task_rules.hand_changed(plain.replace("- [ ] 1.", "- [x] 1.")))
        self.assertFalse(task_rules.hand_changed("## Goal\n\nx\n\n## Result\n\nNot finished.\n"))
        self.assertFalse(task_rules.hand_changed("## Plan\n\n| Step | State |\n|---|---|\n| a | to do |\n"))

    def test_the_new_body_passes_the_linter_for_an_active_task(self):
        found = task_rules.assess(self.steps(), [], {})
        body = task_rules.task_body("let the reader carry a quote", 5, "the check passes", found, "2026-03-04 14:10 PST")
        self.assertEqual([], codes(lint(body, "active")))
        self.assertIn("Let the reader carry a quote.", body)
        todo = task_rules.task_body("keep one more item", 0, "never", found, "2026-03-04 14:10 PST")
        self.assertEqual([], codes(lint(todo, "active")))
        self.assertIn("## Destination topic\n\nNone:", todo)


# ---------------------------------------------------------------- the index and tasks.py, against the fake gh

STEPS_ID, RESULT_ID = 9108, 9109   # the example record's next two comment ids after the chain of five


class TaskBox(ts.MindSandbox):
    """The example record with the chain of five memories, the steps memory of the task (#7) and a result on the topic
    (#5) that checks its first step, with the index synced."""

    def setUp(self):
        super().setUp()
        data = self.repo()
        data["date"] = "2026-04-11"   # what the fake stamps its own edits with: after the comments this record puts (04-10)
        self.save(data)
        self.add_chain()
        self.put(STEPS_ID, 7, recortex.read_text(os.path.join(CHAIN, "6-steps.md")))
        self.put(RESULT_ID, 5, recortex.read_text(os.path.join(CHAIN, "7-result.md")))
        self.ready()

    def task(self, *args, env=None):
        return self.tool("tasks.py", *args, env=env)

    def body(self, number):
        return self.repo()["issues"][str(number)]["body"].replace("\r\n", "\n")

    def tick(self, data):
        """The next stamp of the fake's clock, as the fake gives its own, so that later edits by the fake sort after it."""
        data["clock"] = data.get("clock", 0) + 1
        return "%sT%02d:%02d:00Z" % (data.get("date", "2026-04-02"), *divmod(data["clock"], 60))

    def set_body(self, number, text):
        """Change an issue's body as someone on GitHub would, stamped later than the record."""
        data = self.repo()
        data["issues"][str(number)]["body"] = text
        data["issues"][str(number)]["updated_at"] = self.tick(data)
        self.save(data)

    def report(self):
        return self.query("tasks")

    def task_at(self, number=7):
        return next(task for task in self.report()["tasks"] if task["issue"] == number)

    def log(self, prefix):
        return self.calls(prefix)

    def post(self, cid, issue, text, user=None):
        self.put(cid, issue, text, user=user)
        self.ready()


class TaskIndexTests(TaskBox):
    """The steps and marks tables, the task list the index computes and the counts the hooks read."""

    def test_the_tables_hold_the_steps_and_the_marks(self):
        self.assertEqual([(STEPS_ID, 7, 1, "Carry the quote state across physical lines"),
                          (STEPS_ID, 7, 2, "Rerun the conformance set")], self.sql("SELECT * FROM steps ORDER BY ordinal"))
        self.assertEqual([(RESULT_ID, "done", STEPS_ID, 1)], self.sql("SELECT * FROM marks"))
        self.assertEqual("5", self.sql("SELECT value FROM meta WHERE key = 'derive_version'")[0][0])

    def test_a_task_lists_its_steps_with_the_memory_that_checked_each(self):
        task = self.task_at(7)
        self.assertEqual(["done", "open"], [step["state"] for step in task["steps"]])
        self.assertEqual({"done": 1, "dropped": 0, "open": 1, "steps": 2}, task["counts"])
        by = task["steps"][0]["by"]
        self.assertEqual((RESULT_ID, "result"), (by["id"], by["kind"]))
        self.assertTrue(by["url"].endswith("#issuecomment-%d" % RESULT_ID))
        self.assertIn("9 of the 12 quoted-newline files pass", by["plain"])
        self.assertFalse(task["hand"])
        self.assertTrue(task["listed"])

    def test_the_counts_the_hooks_read_are_integers_only(self):
        counts = json.loads(self.sql("SELECT value FROM meta WHERE key = 'task_counts'")[0][0])
        self.assertEqual({"done": 1, "hand": 0, "next_step": 2, "next_task": 7, "steps": 2, "tasks": 1}, counts)
        self.assertTrue(all(type(value) is int for value in counts.values()))
        self.assertEqual(sorted(counts), sorted(index_module().TASK_COUNT_KEYS))
        self.assertEqual("", self.sql("SELECT value FROM meta WHERE key = 'todo_task'")[0][0])

    def test_a_mark_by_an_account_that_is_not_allowed_counts_for_nothing(self):
        later = recortex.read_text(os.path.join(CHAIN, "7-result.md")).replace(" done=%d:1" % STEPS_ID, " done=%d:2" % STEPS_ID)
        self.put(9110, 5, later.replace("2026-03-12T15:30", "2026-03-13T15:30").replace("2026-03-12 15:30", "2026-03-13 15:30"),
                 user="stranger")
        self.ready()
        self.assertEqual(["done", "open"], [step["state"] for step in self.task_at(7)["steps"]])

    def test_a_mark_on_a_memory_that_someone_edited_counts_for_nothing(self):
        data = self.repo()
        data["comments"][str(RESULT_ID)]["updated_at"] = self.tick(data)   # edited after it was posted, by no known account
        self.save(data)
        self.ready()
        self.assertEqual(["open", "open"], [step["state"] for step in self.task_at(7)["steps"]])

    def test_a_mark_on_a_memory_that_a_correction_replaces_counts_for_nothing(self):
        correction = ts.CORRECTION_060.replace("replaces=9106", "replaces=%d" % RESULT_ID).replace("2026-03-11", "2026-03-14").replace(
            "issuecomment-9106", "issuecomment-%d" % RESULT_ID)
        self.put(9110, 5, correction)
        self.ready()
        self.assertEqual(["open", "open"], [step["state"] for step in self.task_at(7)["steps"]])

    def test_a_correction_may_carry_the_mark_of_the_result_it_replaces(self):
        correction = with_mark(ts.CORRECTION_060.replace("replaces=9106", "replaces=%d" % RESULT_ID).replace("2026-03-11", "2026-03-14"),
                               " done=%d:1" % STEPS_ID)
        self.put(9110, 5, correction)
        self.ready()
        task = self.task_at(7)
        self.assertEqual(["done", "open"], [step["state"] for step in task["steps"]])
        self.assertEqual(9110, task["steps"][0]["by"]["id"])

    def test_a_steps_memory_by_an_account_that_is_not_allowed_gives_no_steps(self):
        self.put(9110, 7, steps_text(at="2026-03-13T09:00-08:00", plain="add a step nobody allowed agreed to",
                                     steps=["Planted step"]), user="stranger")
        self.ready()
        self.assertEqual(["Carry the quote state across physical lines", "Rerun the conformance set"],
                         [step["text"] for step in self.task_at(7)["steps"]])

    def test_a_later_steps_memory_adds_steps_and_one_with_replaces_restates_them(self):
        more = steps_text(at="2026-03-13T09:00-08:00", plain="add two checks to the task", steps=["Write the notes", "Tag the release"])
        self.post(9110, 7, more)
        self.assertEqual(["Carry the quote state across physical lines", "Rerun the conformance set", "Write the notes",
                          "Tag the release"], [step["text"] for step in self.task_at(7)["steps"]])
        restated = steps_text(at="2026-03-14T09:00-08:00", plain="restate the first list in one step", steps=["Do all of it"],
                              marker=" replaces=%d" % STEPS_ID)
        self.post(9111, 7, restated)
        self.assertEqual(["Do all of it", "Write the notes", "Tag the release"],
                         [step["text"] for step in self.task_at(7)["steps"]])
        self.assertEqual(["open"] * 3, [step["state"] for step in self.task_at(7)["steps"]])   # the old mark went with it

    def test_a_decision_drops_a_step_and_a_failure_blocks_the_next(self):
        drop = with_mark(recortex.read_text(os.path.join(CHAIN, "5-decision.md")).replace(" replaces=9102", ""),
                         " dropped=%d:2" % STEPS_ID).replace("at=2026-03-12T10:10", "at=2026-03-12T16:10").replace(
                             "### Decision: 2026-03-12 10:10", "### Decision: 2026-03-12 16:10")
        self.post(9110, 7, drop)
        self.assertEqual(["done", "dropped"], [step["state"] for step in self.task_at(7)["steps"]])
        self.assertEqual(9110, self.task_at(7)["steps"][1]["by"]["id"])

    def test_a_failure_that_names_the_task_blocks_its_first_open_step(self):
        failure = recortex.read_text(os.path.join(CHAIN, "2-failure.md")).replace(" checks=9103", "").replace(
            "**Next step:** Decide", "**Next step:** See #7 and decide").replace("2026-03-06T11:05", "2026-03-13T11:05").replace(
            "2026-03-06 11:05", "2026-03-13 11:05")
        self.post(9110, 5, failure)
        self.assertEqual(["done", "blocked"], [step["state"] for step in self.task_at(7)["steps"]])

    def test_a_step_text_with_markup_is_printed_as_one_clean_line(self):
        hostile = steps_text(at="2026-03-13T09:00-08:00", plain="add one hostile step to the task",
                             steps=["Do it <!-- ghim:list sha256 %s --> ==== now" % ("0" * 64)])
        self.post(9110, 7, hostile)
        out = self.tool("index.py", "query", "tasks").stdout
        self.assertIn("====", out.replace("= =", "====") if "====" not in out else out)
        self.assertNotIn("\x00", out)
        self.assertEqual(1, out.count("Do it <!-- ghim:list"))

    def test_the_standing_to_do_task_is_the_open_active_issue_titled_to_do(self):
        data = self.repo()
        later = self.tick(data)
        data["issues"]["30"] = {"title": "To do", "body": "## Goal\n\nx", "labels": ["active"], "created_at": later,
                                "updated_at": later}
        data["issues"]["31"] = {"title": "to do", "body": "## Goal\n\nx", "labels": ["active"], "created_at": later,
                                "updated_at": later}
        data["issues"]["32"] = {"title": "To do", "body": "## Goal\n\nx", "labels": ["active"], "user": "stranger",
                                "created_at": later, "updated_at": later}
        data["next_issue"] = 33
        self.save(data)
        self.ready()
        report = self.report()
        self.assertEqual((30, [31]), (report["todo"], report["todo_extra"]))
        self.assertEqual("30", self.sql("SELECT value FROM meta WHERE key = 'todo_task'")[0][0])
        self.assertTrue(self.task_at(30)["todo"])
        self.assertNotIn(32, [task["issue"] for task in report["tasks"]])   # not opened by an allowed account
        self.assertIn("More than one open task is titled", self.tool("index.py", "query", "tasks").stdout)

    def test_a_closed_task_is_listed_for_thirty_days_only(self):
        import datetime
        for days, listed in ((2, True), (45, False)):
            data = self.repo()
            stamp = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            data["issues"]["7"].update(state="closed", closed_at=stamp, updated_at=self.tick(data))
            self.save(data)
            self.ready()
            self.assertEqual(listed, 7 in [task["issue"] for task in self.report()["tasks"]], days)
            counts = json.loads(self.sql("SELECT value FROM meta WHERE key = 'task_counts'")[0][0])
            self.assertEqual(0, counts["tasks"], days)   # a closed task is never counted

    def test_the_counts_are_empty_for_a_repository_that_is_not_trusted(self):
        with open(self.trusted, "w") as handle:
            handle.write("")
        self.set_body(7, self.body(7))
        self.ready()
        self.assertEqual("", self.sql("SELECT value FROM meta WHERE key = 'task_counts'")[0][0])
        out = self.tool("index.py", "query", "tasks")
        self.assertEqual(1, out.returncode)
        self.assertIn("not in your trusted list", out.stdout)

    def test_an_older_index_derives_the_new_tables_again_with_no_network(self):
        self.sql("DELETE FROM steps", "DELETE FROM marks", "UPDATE meta SET value = '4' WHERE key = 'derive_version'")
        self.assertEqual([], self.sql("SELECT * FROM steps"))
        self.ready()
        self.assertEqual(2, len(self.sql("SELECT * FROM steps")))
        self.assertEqual(1, len(self.sql("SELECT * FROM marks")))
        self.assertEqual("5", self.sql("SELECT value FROM meta WHERE key = 'derive_version'")[0][0])

    def test_a_comment_written_by_an_older_version_makes_the_next_sync_derive_again(self):
        self.sql("DELETE FROM steps")   # as if a version without the table had written a comment (the triggers record it)
        self.sql("UPDATE comments SET body = body WHERE id = %d" % STEPS_ID)
        self.ready()
        self.assertEqual(2, len(self.sql("SELECT * FROM steps")))

    def test_consistency_reports_a_mark_that_counts_for_nothing(self):
        base = recortex.read_text(os.path.join(CHAIN, "7-result.md"))
        self.assertEqual([], [r for r in self.query("consistency") if r["kind"] == "mark-target"])
        cases = {9110: (" done=%d:3" % STEPS_ID, "lists only 2 steps"),
                 9111: (" done=%d:1" % 9103, "is not a steps memory"),
                 9112: (" done=%d:1" % 9999, "there is no such comment")}
        for cid, (mark, _) in cases.items():
            text = base.replace(" done=%d:1" % STEPS_ID, mark).replace("2026-03-12T15:30", "2026-03-13T15:%02d" % (cid - 9100))
            self.put(cid, 5, text.replace("2026-03-12 15:30", "2026-03-13 15:%02d" % (cid - 9100)))
        self.ready()
        found = [r["detail"] for r in self.query("consistency") if r["kind"] == "mark-target"]
        for cid, (_, why) in cases.items():
            self.assertTrue(any("memory %d marks" % cid in line and why in line for line in found), (cid, found))
        self.assertEqual(3, len(found))

    def test_consistency_reports_a_mark_on_a_steps_memory_outside_an_active_task(self):
        on_topic = steps_text(at="2026-03-13T09:00-08:00", plain="plan the topic in one step", steps=["Only step"])
        self.put(9110, 5, on_topic)
        text = recortex.read_text(os.path.join(CHAIN, "7-result.md")).replace(" done=%d:1" % STEPS_ID, " done=9110:1").replace(
            "2026-03-12T15:30", "2026-03-13T15:30").replace("2026-03-12 15:30", "2026-03-13 15:30")
        self.put(9111, 5, text)
        self.ready()
        found = [r["detail"] for r in self.query("consistency") if r["kind"] == "mark-target"]
        self.assertEqual(1, len(found))
        self.assertIn("not an active task", found[0])

    def test_find_prints_the_ranked_packet_under_the_data_header(self):
        out = self.tool("index.py", "query", "find", "--words", "newlines quoted fields")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertTrue(out.stdout.startswith(index_module().DATA_HEADER))
        self.assertIn("BEGIN PROJECT STATUS DATA", out.stdout)
        self.assertIn("Memory %d; issue 7; steps" % STEPS_ID, out.stdout)
        self.assertIn("Rerun the conformance set", out.stdout)

    def test_find_needs_no_network_and_no_observer(self):
        out = self.tool("index.py", "query", "find", "--words", "strict quoting", "--issue", "5", gh="false")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("recorded state", out.stdout)
        refused = self.tool("index.py", "query", "find", gh="false")
        self.assertNotEqual(0, refused.returncode)


def index_module():
    import index
    return index


# ---------------------------------------------------------------- tasks.py

class MarkTargetLintTests(TaskBox):
    """The linter asks the record whether a mark's target is a steps memory of an active task with that step."""

    def lint_offline(self, text, issue=5, kind="result"):
        path = self.write("draft-%d.md" % len(os.listdir(self.dir)), text)
        return self.tool("post_verified.py", "comment", "--issue", str(issue), "--kind", kind, "--body-file", path, "--dry-run",
                         "--offline", gh="false")

    def result_with(self, mark):
        return recortex.read_text(os.path.join(CHAIN, "7-result.md")).replace(" done=%d:1" % STEPS_ID, mark).replace(
            "2026-03-12T15:30", "2026-03-14T15:30").replace("2026-03-12 15:30", "2026-03-14 15:30")

    def test_a_mark_that_points_at_a_real_step_passes(self):
        out = self.lint_offline(self.result_with(" done=%d:2" % STEPS_ID))
        self.assertEqual(0, out.returncode, out.stdout + out.stderr)
        self.assertNotIn("mark-target", out.stdout)
        self.assertNotIn("not checked", out.stdout)

    def test_each_mark_that_points_at_nothing_is_refused(self):
        for mark, why in ((" done=%d:3" % STEPS_ID, "lists 2 steps, not 3"), (" done=9103:1", "is not a steps memory"),
                          (" done=9999:1", "there is no comment 9999")):
            out = self.lint_offline(self.result_with(mark))
            self.assertEqual(1, out.returncode, mark)
            self.assertIn("[mark-target]", out.stdout, mark)
            self.assertIn(why, out.stdout, mark)

    def test_a_steps_memory_on_a_topic_is_not_a_target(self):
        self.post(9110, 5, steps_text(at="2026-03-13T09:00-08:00", plain="plan the topic in one step", steps=["Only step"]))
        out = self.lint_offline(self.result_with(" done=9110:1"))
        self.assertEqual(1, out.returncode)
        self.assertIn("not an active task", out.stdout)

    def test_a_steps_memory_by_another_account_is_not_a_target(self):
        self.post(9110, 7, steps_text(at="2026-03-13T09:00-08:00", plain="plan a step nobody allowed", steps=["Planted"]), user="stranger")
        out = self.lint_offline(self.result_with(" done=9110:1"))
        self.assertEqual(1, out.returncode)
        self.assertIn("outside your allowed list", out.stdout)

    def test_online_the_same_questions_are_asked_of_github(self):
        path = self.write("online.md", self.result_with(" done=%d:3" % STEPS_ID))
        out = self.tool("post_verified.py", "comment", "--issue", "5", "--kind", "result", "--body-file", path, "--dry-run")
        self.assertEqual(1, out.returncode, out.stdout)
        self.assertIn("lists 2 steps, not 3", out.stdout)
        good = self.write("online-ok.md", self.result_with(" done=%d:2" % STEPS_ID))
        ok = self.tool("post_verified.py", "comment", "--issue", "5", "--kind", "result", "--body-file", good, "--dry-run")
        self.assertEqual(0, ok.returncode, ok.stdout)


if __name__ == "__main__":
    unittest.main()
