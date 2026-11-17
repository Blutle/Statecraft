import re
import unittest

from statecraft import geometry, guides
from statecraft.models import DEFAULT_SETTINGS


class GuideTests(unittest.TestCase):
    def test_cards_fit_discord_limits(self):
        for name, sections in (("rules", guides.rules(DEFAULT_SETTINGS)),
                               ("how_to_play", guides.how_to_play(DEFAULT_SETTINGS))):
            self.assertLessEqual(len(sections), 25, name)
            for title, body in sections:
                self.assertLessEqual(len(title), 256, title)
                self.assertLessEqual(len(body), 4096, title)
                self.assertEqual(body.count("```") % 2, 0, title)

    def test_numbers_follow_settings(self):
        s = dict(DEFAULT_SETTINGS, cooldown_minutes=20, window_open="15:00", upgrade_cost=3)
        text = "\n".join(b for _, b in guides.rules(s))
        self.assertIn("20 minutes", text)
        self.assertIn("3:00 PM", text)
        self.assertIn("**Upgrade Influence**: 3 AP", text)

    def test_referee_approved_wording_is_exact(self):
        text = "\n".join(b for _, b in guides.rules(DEFAULT_SETTINGS))
        for phrase in (
            "Any action that targets another State requires that State to be in Range of your State's Influence. "
            "Example Influence ranges are pictured below.",
            "Every State starts with 2 Influence Range",
            "Destruction is not the end of your time in Statecraft. Your votes can grant opportunities to those "
            "who remain.",
            "a top-secret log that shows all gift actions performed for the current day",
            "you must wait",
        ):
            self.assertIn(phrase, text)
        self.assertEqual(guides.rules(DEFAULT_SETTINGS)[0][0], "💰 Meta Rules: No Outside Money")
        for gone in ("Nobody inherits", "Always hits", "HP never regenerates", "three-quarters", "trade"):
            self.assertNotIn(gone, text)

    def test_range_diagram_matches_the_rule(self):
        body = dict(guides.rules(DEFAULT_SETTINGS))["📏 Influence"]
        art = body.split("```")[1].splitlines()[3:]            # skip the opening, the titles, and the blank line
        for rng, col_start, centre_col in ((2, 0, 4), (3, 18, 24)):
            marks = set()
            for row, line in enumerate(art):
                seg = line[col_start:col_start + 13]
                for k, ch in enumerate(seg):
                    if ch in "X@":
                        marks.add(((col_start + k - centre_col) // 2, row))
            centre_row = next(r for r, line in enumerate(art) if "@" in line[col_start:col_start + 13])
            got = {(dx, dy - centre_row) for dx, dy in marks} - {(0, 0)}
            want = {(dx, dy) for dx in range(-5, 6) for dy in range(-5, 6) if geometry.in_range(0, 0, dx, dy, rng)}
            self.assertEqual(got, want, f"range {rng} diagram")


if __name__ == "__main__":
    unittest.main()
