"""Discord-free tests for the bot's helpers (text commands, formatting, parsing)."""
import random
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from statecraft.discord_bot import own_status, parse_duration, public_status, run_text
from statecraft.game import Game

CT = ZoneInfo("America/Chicago")
WINDOW = datetime(2026, 11, 17, 15, 0, tzinfo=CT)


def game():
    g = Game(rng=random.Random(2))
    for i, name in enumerate(["Westhaven", "Veyran Empire", "Kesh"]):
        g.register(f"User{i}", name, pid=f"fake{i + 1}")
    g.start(force=True, now=datetime(2026, 11, 17, 10, 0, tzinfo=CT))
    g.state.board_size = 6
    for i, p in enumerate(g.state.players.values()):
        p.x, p.y, p.ap = i, 0, 5
    return g


class RunTextTests(unittest.TestCase):
    def test_commands_dispatch(self):
        g = game()
        self.assertTrue(run_text(g, "fake1", "attack VE", WINDOW).ok)
        self.assertEqual(g.get("fake2").hp, 2)
        self.assertTrue(run_text(g, "fake2", "gift WE 2", WINDOW).ok)
        self.assertTrue(run_text(g, "fake3", "say hello all", WINDOW).ok)
        self.assertTrue(run_text(g, "fake3", "dm WE secret plan", WINDOW).ok)
        self.assertFalse(run_text(g, "fake1", "move ne", WINDOW).ok)      # cooldown from the attack
        self.assertFalse(run_text(g, "fake1", "fly away", WINDOW).ok)

    def test_pact_text_commands(self):
        g = game()
        self.assertTrue(run_text(g, "fake1", 'pact create "Iron Accord"', WINDOW).ok)
        self.assertTrue(run_text(g, "fake1", 'pact invite "Iron Accord" VE', WINDOW).ok)
        self.assertTrue(run_text(g, "fake2", 'pact accept "iron accord"', WINDOW).ok)
        res = run_text(g, "fake2", 'pact say "Iron Accord" hello there', WINDOW)
        self.assertEqual(res.events[0].data["recipients"], ["fake1"])
        self.assertFalse(run_text(g, "fake3", 'pact say "Iron Accord" let me in', WINDOW).ok)
        self.assertTrue(run_text(g, "fake1", 'pact kick "Iron Accord" VE', WINDOW).ok)

    def test_actions_respect_clock(self):
        g = game()
        res = run_text(g, "fake1", "upgrade", datetime(2026, 11, 17, 21, 0, tzinfo=CT))
        self.assertFalse(res.ok)
        self.assertIn("window", res.message)


class FormattingTests(unittest.TestCase):
    def test_public_status_hides_ap(self):
        g = game()
        text = public_status(g, "fake1", "fake2")
        self.assertIn("Veyran Empire", text)
        self.assertNotIn("AP 5", text)
        self.assertIn("you can reach it", text)

    def test_own_status_shows_ap_and_cooldown(self):
        g = game()
        run_text(g, "fake1", "upgrade", WINDOW)
        text = own_status(g, "fake1", WINDOW.replace(minute=5))
        self.assertIn("AP:       3", text)
        self.assertIn("Cooldown: 10m", text)


class DurationTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_duration("+2h"), 7200)
        self.assertEqual(parse_duration("30m"), 1800)
        self.assertEqual(parse_duration("-1d"), -86400)
        self.assertEqual(parse_duration("0"), 0)
        self.assertEqual(parse_duration("15"), 900)          # bare number = minutes
        self.assertIsNone(parse_duration("soon"))


if __name__ == "__main__":
    unittest.main()
