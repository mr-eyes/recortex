"""Regression tests for the documentation search index."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build


class SearchIndexTests(unittest.TestCase):
    def test_disclosure_answers_stay_with_their_own_search_result(self):
        sections = build.plain(
            '<h2 id="intro">Intro</h2><details><summary>A source</summary>Source text</details>'
            '<details id="first"><summary>Why <em>now</em>?</summary><p>First answer</p></details>'
            '<details id="second"><summary>What next?</summary><p>Second answer</p></details>'
            '<p>Closing words</p>'
        )
        self.assertEqual([(s["id"], s["heading"]) for s in sections], [
            ("", ""), ("intro", "Intro"), ("first", "Why now?"), ("second", "What next?"),
        ])
        texts = [" ".join("".join(s["text"]).split()) for s in sections]
        self.assertEqual(texts, ["", "Intro A source Source text Closing words",
                                 "Why now? First answer", "What next? Second answer"])
