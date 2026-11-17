import random
import unittest
from itertools import combinations

from statecraft import colours, render_image
from statecraft.game import Game


def game(n=4):
    g = Game(rng=random.Random(1))
    for i in range(n):
        g.register(f"Owner{i}", f"State {i}", pid=str(i))
    return g


class PaletteTests(unittest.TestCase):
    def test_twenty_distinct_readable_colors(self):
        self.assertEqual(len(colours.PALETTE), 20)
        closest = min(colours.distance(a, b) for a, b in combinations(colours.PALETTE.values(), 2))
        self.assertGreater(closest, 25, "two palette colors are too similar")
        for name, c in colours.PALETTE.items():
            self.assertGreater(colours.distance(c, (49, 51, 56)), 35, f"{name} blends into the board")
            self.assertGreater(colours.distance(c, (78, 80, 88)), 35, f"{name} looks like a destroyed State")


class ChooseColorTests(unittest.TestCase):
    def test_first_come_first_served(self):
        g = game()
        self.assertTrue(g.set_color("0", "teal").ok)
        self.assertEqual(g.get("0").color, "Teal")
        self.assertFalse(g.set_color("1", "Teal").ok)          # taken
        self.assertNotIn("Teal", g.free_colors("1"))
        self.assertIn("Teal", g.free_colors("0"))               # your own color still counts as yours
        self.assertTrue(g.set_color("0", "Crimson").ok)          # switching frees Teal
        self.assertTrue(g.set_color("1", "Teal").ok)
        self.assertFalse(g.set_color("2", "Plaid").ok)
        self.assertFalse(g.set_color("9", "Pink").ok)            # not registered

    def test_unpicked_get_free_colors_at_start_and_colors_lock(self):
        g = game()
        g.set_color("2", "Crimson")                              # the first palette color
        g.start(force=True)
        picked = [p.color for p in g.state.players.values()]
        self.assertEqual(len(set(picked)), 4)
        self.assertEqual(g.get("2").color, "Crimson")
        self.assertNotIn(None, picked)
        self.assertFalse(g.set_color("0", "Pink").ok)            # locked after the start

    def test_referee_can_change_color(self):
        g = game()
        g.start(force=True)
        taken = g.get("1").color
        self.assertFalse(g.admin_edit("0", "color", taken).ok)
        self.assertTrue(g.admin_edit("0", "color", "silver").ok)
        self.assertEqual(g.get("0").color, "Silver")

    def test_board_uses_chosen_colors(self):
        g = game()
        g.set_color("0", "Magenta")
        g.start(force=True)
        self.assertEqual(render_image.colour_of(g.get("0"), 0), colours.PALETTE["Magenta"])


if __name__ == "__main__":
    unittest.main()
