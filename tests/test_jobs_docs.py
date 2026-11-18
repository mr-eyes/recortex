"""The 1.5.0 docs say what the code does (1.5.0): the new reference is linked, the five commands are named where users
look, the numbers and question templates in the docs are the ones in the code, the upgrade entry is above 1.4.0's, the
README stays under its word cap, and the words the project bans are not in the files this release wrote. No network."""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "recortex")
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import observer_publisher  # noqa: E402
import observer_runtime  # noqa: E402

NAMES = ("record", "ask", "todo", "status", "usage")
BANNED = ("compre" + "hensive", "ro" + "bust", "cru" + "cial")   # spelled in pieces: a test scans this file for them too


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def references():
    return sorted(name for name in os.listdir(os.path.join(SKILL, "references")) if name.endswith(".md"))


class LinkTests(unittest.TestCase):
    def test_every_reference_is_linked_from_skill_md_and_tasks_md_is_new(self):
        skill = read("skills", "recortex", "SKILL.md")
        self.assertIn("tasks.md", references())
        for name in references():
            self.assertIn("references/" + name, skill, name)
        self.assertIn("(references/tasks.md)", skill)

    def test_the_observer_page_and_the_task_page_link_each_other(self):
        self.assertIn("(tasks.md)", read("skills", "recortex", "references", "observer.md"))
        self.assertIn("(observer.md)", read("skills", "recortex", "references", "tasks.md"))

    def test_the_upgrade_entry_is_above_1_4_0_and_says_the_four_things(self):
        text = read("skills", "recortex", "references", "upgrading.md")
        self.assertLess(text.index("## Version 1.5.0: an observer with several jobs"), text.index("## Version 1.4.0"))
        entry = text.split("## Version 1.5.0", 1)[1].split("## Version 1.4.0", 1)[0]
        for needed in ("paused until", "level", "ordinary comment", "ignores `done=`", "Update every machine of a team",
                       "usage.db", "SubagentStop", "derives the index again"):
            self.assertIn(needed, entry, needed)


class CommandTests(unittest.TestCase):
    def test_the_five_commands_and_the_agent_are_named_in_the_skill_the_reference_and_the_readme(self):
        for path in (("skills", "recortex", "SKILL.md"), ("docs", "reference.md"), ("README.md",)):
            text = read(*path)
            for name in NAMES:
                self.assertIn("/recortex:" + name, text, "%s: %s" % (path[-1], name))
            self.assertIn("@agent-recortex:ask", text, path[-1])

    def test_no_doc_promises_a_bare_record_command(self):
        for path in (("README.md",), ("docs", "reference.md"), ("skills", "recortex", "SKILL.md")):
            text = read(*path).replace("bare `/record`", "")   # a page may say that there is none
            self.assertEqual([], re.findall(r"(?<![:\w])/(?:record|ask|todo|status|usage)\b", text), path[-1])

    def test_the_readme_stays_under_its_cap_and_has_the_new_words_once(self):
        text = read("README.md")
        self.assertLess(len(text.split()), 1000)
        self.assertIn("See. Record. Recall. Act.", text)
        self.assertEqual(1, text.count("record + cortex"))
        self.assertNotIn("**", re.sub(r"```.*?```", "", text, flags=re.S))


class NumbersTests(unittest.TestCase):
    def test_the_caps_in_the_observer_page_are_the_defaults_of_the_code(self):
        text = read("skills", "recortex", "references", "observer.md")
        defaults = observer_runtime.DEFAULTS
        row = {"sort calls": "max_sort", "middle calls": "max_invocations", "strong calls": "max_strong",
               "tasks opened": "max_tasks", "paper proposals": "max_proposals", "status summaries": "max_summaries",
               "task lists rendered by code": "max_renders", "candidates per draft": "max_candidates"}
        for label, key in row.items():
            found = re.search(r"^\| %s \| (?:\S+ )?([0-9,]+)" % re.escape(label), text, re.M)
            self.assertIsNotNone(found, label)
            self.assertEqual(defaults[key], int(found.group(1).replace(",", "")), label)
        budgets = "%s, %s, %s" % tuple("{:,}".format(defaults[key]) for key in ("sort_tokens", "middle_tokens", "strong_tokens"))
        self.assertIn(budgets, text)
        self.assertIn("25 a day", text)
        self.assertEqual(25, defaults["max_posts"])

    def test_the_input_limits_and_models_in_the_observer_page(self):
        text = read("skills", "recortex", "references", "observer.md")
        for number in (observer_runtime.SORT_INPUT, observer_runtime.MAX_INPUT, observer_runtime.STRONG_INPUT):
            self.assertIn("{:,} characters".format(number), text)
        for model in ("`haiku`", "`sonnet`", "`opus`"):
            self.assertIn(model, text)

    def test_the_question_templates_in_the_docs_are_the_ones_the_code_asks(self):
        asked = {
            "task open": observer_publisher.TASK_OPEN_QUESTION % ("<short>", "<title>", 0),
            "task close": observer_publisher.TASK_CLOSE_QUESTION % ("<short>", 0),
        }
        asked["task open"] = asked["task open"].replace("with 0 steps", "with <n> steps")
        asked["task close"] = asked["task close"].replace("#0", "#<N>")
        for name in ("SKILL.md",):
            text = read("skills", "recortex", name)
            for label, line in asked.items():
                self.assertIn(line, text, label)
        for path in (("skills", "recortex", "references", "observer.md"), ("skills", "recortex", "references", "session-protocol.md")):
            text = read(*path)
            for label, line in asked.items():
                self.assertIn(line, text, "%s: %s" % (path[-1], label))
        self.assertEqual(("Open it", "Not now", "Drop it"), observer_publisher.TASK_OPEN_OPTIONS)
        self.assertEqual(("Close it", "Not now"), observer_publisher.TASK_CLOSE_OPTIONS)

    def test_the_draft_question_and_the_paused_line_are_quoted_as_the_code_has_them(self):
        import keeper_rules
        draft = (keeper_rules.DRAFT_QUESTION % ("<short>", keeper_rules.DRAFT_LINE % ("<heading>", 0))).replace("#0", "#<N>")
        for path in (("skills", "recortex", "SKILL.md"), ("skills", "recortex", "references", "observer.md")):
            self.assertIn(draft, read(*path), path[-1])
        self.assertEqual(("Approve", "Not now", "Drop it"), keeper_rules.DRAFT_OPTIONS)
        paused = observer_runtime.PAUSED.replace("recortex observer: ", "").rstrip(".")
        self.assertIn(paused, read("skills", "recortex", "references", "observer.md"))
        self.assertIn(paused, read("skills", "recortex", "references", "upgrading.md"))


class ContributingTests(unittest.TestCase):
    def test_the_sixteenth_command_and_the_layout(self):
        text = read("CONTRIBUTING.md")
        self.assertIn("python3 -m unittest discover -s tests -p 'test_jobs_*.py'", text)
        self.assertIn("The sixteen commands", text)
        self.assertNotIn("fifteen commands", text)
        self.assertEqual(16, len(re.findall(r"^python3 -m unittest ", text.split("```bash", 1)[1].split("```", 1)[0], re.M)))
        self.assertIn("commands/", text)
        self.assertIn("SubagentStop", text)

    def test_each_new_boundary_has_its_rule(self):
        text = read("CONTRIBUTING.md")
        for needed in ("The observer's levels", "The plan dialog and the question dialog", "The manuscript keeper",
                       "Usage is tokens and calls, never money", "A step is checked only by a mark"):
            self.assertIn(needed, text, needed)


class WordsTests(unittest.TestCase):
    def test_the_banned_words_are_not_in_the_files_this_release_wrote(self):
        paths = [("README.md",), ("CONTRIBUTING.md",), ("skills", "recortex", "SKILL.md")]
        paths += [("skills", "recortex", "references", name) for name in references()]
        paths += [("docs", name) for name in ("reference.md", "how-it-works.md", "security.md")]
        paths += [("commands", name + ".md") for name in NAMES] + [("agents", "ask.md")]
        for path in paths:
            text = read(*path).lower()
            for word in BANNED:
                self.assertNotIn(word, text, "%s: %s" % ("/".join(path), word))


if __name__ == "__main__":
    unittest.main()
