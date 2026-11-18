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


if __name__ == "__main__":
    unittest.main()
