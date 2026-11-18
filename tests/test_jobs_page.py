"""The mind page's fixed style (1.5.1): what a closed part of the page leaves on the window. No network."""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(ROOT, "skills", "recortex", "page", "mind.css")


def rules(text):
    """{selector: body} of the flat rules of the style (media blocks are not entered)."""
    return {match.group(1).strip(): match.group(2) for match in re.finditer(r"(?m)^([^@{}\n][^{}]*)\{([^{}]*)\}", text)}


class PanelShadowTests(unittest.TestCase):
    def setUp(self):
        with open(CSS, encoding="utf-8") as handle:
            self.rules = rules(handle.read())

    def test_a_closed_panel_casts_no_shadow_on_the_window(self):
        closed = self.rules[".js .panel"]
        self.assertIn("translateX(102%)", closed)   # it waits just outside the window
        self.assertNotIn("box-shadow", closed)      # so its shadow would show at the window's right edge

    def test_an_open_panel_has_its_shadow(self):
        opened = self.rules[".js .panel.on"]
        self.assertIn("transform:none", opened)
        self.assertIn("box-shadow", opened)


if __name__ == "__main__":
    unittest.main()
