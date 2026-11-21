"""Discovery and project-file consent in a local Git project."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "recortex" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import project_map_sources as sources
import setup


class ProjectFiles(unittest.TestCase):
    repo = "octo-org/sample-project"

    def setUp(self):
        base = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        self.root = Path(base.name) / "project"
        self.root.mkdir()
        self.config = Path(base.name) / "config"
        self.config.mkdir()
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config)})
        env.start()
        self.addCleanup(env.stop)
        self.git("init", "-q")
        self.file("docs/outline.md", "# Methods\n## Calibration\nPrivate draft paragraph\n")
        self.file("README.md", "Project\n=======\n")
        self.file("notes.md", "# Notes\n")
        self.git("add", ".")
        self.file("roadmap.md", "# Untracked\n")

    def file(self, name, text):
        path = self.root / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(text, encoding="utf-8")

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root)] + list(args), check=True, capture_output=True)

    def test_only_selected_tracked_files_supply_headings_and_follow_changes(self):
        self.assertEqual(["README.md", "docs/outline.md"], sources.candidates(self.root))
        self.assertEqual([], sources.snapshot(self.root, self.repo))
        sources.approve(self.root, self.repo, ["docs/outline.md"])
        before, = sources.snapshot(self.root, self.repo)
        self.assertEqual([{"level": 1, "title": "Methods"}, {"level": 2, "title": "Calibration"}], before["headings"])
        self.assertNotIn("Private draft", json.dumps(before))
        self.file("docs/outline.md", "New methods\n===========\n~~~md\n# Fenced sample\n~~~\n")
        after, = sources.snapshot(self.root, self.repo)
        self.assertEqual([{"level": 1, "title": "New methods"}], after["headings"])
        self.assertNotEqual(before["digest"], after["digest"])
        self.assertEqual([], sources.snapshot(self.root, "octo-org/another-project"))
        sources.approve(self.root, self.repo, [])
        self.assertEqual([], sources.snapshot(self.root, self.repo))

    def test_replaced_links_or_untracked_files_are_ignored(self):
        sources.approve(self.root, self.repo, ["README.md", "docs/outline.md"])
        (self.root / "docs").rename(self.root / "elsewhere")
        (self.root / "docs").symlink_to(self.root / "elsewhere", target_is_directory=True)
        self.git("rm", "--cached", "-q", "README.md")
        self.assertEqual([], sources.snapshot(self.root, self.repo))
        with self.assertRaises((OSError, ValueError)):
            sources.approve(self.root, self.repo, ["docs/outline.md"])
        with self.assertRaises(ValueError):
            sources.approve(self.root, self.repo, ["roadmap.md"])

    def test_invalid_scope_cannot_replace_saved_selection(self):
        sources.approve(self.root, self.repo, ["README.md"])
        for paths in (["../README.md"], [".git/config"], ["notes.md"] * 9, ["secrets.env"], "README.md"):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                sources.approve(self.root, self.repo, paths)
        self.assertEqual(["README.md"], sources.selection(self.root, self.repo))
        answers = {"repo": self.repo, "deliverables": [{"kind": "software"}], "map_files": ["notes.md"]}
        self.assertEqual(["notes.md"], setup.check_answers(answers)["map_files"])
        with self.assertRaises(setup.AnswerError):
            setup.check_answers(dict(answers, map_files=["/private/notes.md"]))

    def test_private_metadata_comments_and_fenced_examples_are_not_headings(self):
        data = (b"---\ntitle: Private draft\n---\n<!--\n# Hidden remark\n-->\n"
                b"  ## C# tools ###\n~~~\n# Code example\n~~~\n# C#\n")
        self.assertEqual([{"level": 2, "title": "C# tools"}, {"level": 1, "title": "C#"}], sources.headings(data))

    def test_corrupt_private_scope_fails_closed(self):
        sources.approve(self.root, self.repo, ["README.md"])
        Path(sources.scope_path(self.root, self.repo)).write_text("../", encoding="utf-8")
        self.assertEqual([], sources.snapshot(self.root, self.repo))
