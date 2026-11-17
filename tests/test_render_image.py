import io
import random
import unittest

from PIL import Image

from statecraft import render_image
from statecraft.game import Game


def game(n=16, seed=4):
    g = Game(rng=random.Random(seed))
    for i in range(n):
        g.register(f"Owner{i}", f"State Number {i}", pid=str(i))
    g.start(force=True)
    return g


class Private:
    """Wraps a Player; reading AP, or a living state's owner, fails the test."""

    def __init__(self, p):
        object.__setattr__(self, "_p", p)

    def __getattr__(self, name):
        p = object.__getattribute__(self, "_p")
        if name == "ap":
            raise AssertionError("the board image must never read AP")
        if name == "owner_name" and p.alive:
            raise AssertionError("the board image must never read a living state's owner")
        return getattr(p, name)


class RenderTests(unittest.TestCase):
    def png(self, data):
        img = Image.open(io.BytesIO(data))
        self.assertEqual(img.format, "PNG")
        return img

    def test_all_board_sizes(self):
        for n in (2, 3, 5, 8, 12, 16, 20):
            img = self.png(render_image.render(game(n)))
            self.assertGreaterEqual(img.width, 800)

    def test_focus_viewer_dead_and_ended(self):
        g = game(12)
        g.get("3").alive, g.get("3").hp, g.get("3").eliminated_on_day = False, 0, 2
        self.png(render_image.render(g, focus="0", viewer="1"))
        self.png(render_image.render(g, focus="3", viewer="3"))    # dead focus / dead viewer
        g.declare_winners(["0", "1"])
        self.png(render_image.render(g))

    def test_image_uses_public_information_only(self):
        g = game(10)
        g.get("4").alive, g.get("4").hp, g.get("4").eliminated_on_day = False, 0, 1
        g.state.players = {pid: Private(p) for pid, p in g.state.players.items()}
        self.png(render_image.render(g, focus="0", viewer="0"))

    def test_colours_distinct(self):
        cols = [render_image.state_colour(i) for i in range(20)]
        self.assertEqual(len(set(cols)), 20)


if __name__ == "__main__":
    unittest.main()
