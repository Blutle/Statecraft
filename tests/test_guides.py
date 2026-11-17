import re
import unittest

from statecraft import geometry, guides
from statecraft.models import DEFAULT_SETTINGS


class GuideTests(unittest.TestCase):
    def test_cards_fit_discord_limits(self):
        for name, sections in (("rules", guides.rules(DEFAULT_SETTINGS)),
                               ("how_to_play", guides.how_to_play(DEFAULT_SETTINGS)),
                               ("register", guides.register(DEFAULT_SETTINGS))):
            self.assertLessEqual(len(sections), 25, name)
            for title, body, *_ in sections:
                self.assertLessEqual(len(title), 256, title)
                self.assertLessEqual(len(body), 4096, title)
                self.assertEqual(body.count("```") % 2, 0, title)

    def test_numbers_follow_settings(self):
        s = dict(DEFAULT_SETTINGS, cooldown_minutes=20, window_open="15:00", upgrade_cost=3)
        text = "\n".join(sec[1] for sec in guides.rules(s))
        self.assertIn("20 minutes", text)
        self.assertIn("3:00 PM", text)
        self.assertIn("**Upgrade Influence**: 3 AP", text)

    def test_referee_approved_wording_is_exact(self):
        text = "\n".join("\n".join(x for x in sec[1:] if x) for sec in guides.rules(DEFAULT_SETTINGS))
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

    def test_influence_card_has_the_picture(self):
        card = next(sec for sec in guides.rules(DEFAULT_SETTINGS) if sec[0] == "📏 Influence")
        self.assertEqual(card[2], "influence")
        self.assertEqual(card[3], "Every State starts with 2 Influence Range")
        from statecraft import render_image
        self.assertTrue(render_image.influence_png().startswith(b"\x89PNG"))

    def test_upgrade_is_the_last_action(self):
        body = {sec[0]: sec[1] for sec in guides.rules(DEFAULT_SETTINGS)}["🎯 Actions"]
        self.assertTrue(body.rstrip().splitlines()[-1].startswith("**Upgrade Influence**"))

    def test_pact_founding_wording(self):
        body = {sec[0]: sec[1] for sec in guides.how_to_play(DEFAULT_SETTINGS)}["🤝 Pacts (group chats)"]
        self.assertIn("(you can found up to 3)", body)

    def test_register_welcome(self):
        (title, body), = guides.register(DEFAULT_SETTINGS, "<#1>", "<#2>")
        self.assertIn("`/join`", body)
        self.assertIn("<#1>", body)
        self.assertIn("<#2>", body)
        self.assertIn("12 States", body)


if __name__ == "__main__":
    unittest.main()
