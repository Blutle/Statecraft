"""Panel card and layout checks (no network). Views need a running event loop."""
import asyncio
import random
import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from statecraft.discord_format import panel_embed
from statecraft.discord_ui import UI, MoveView
from statecraft.game import Game

CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 17, 15, 0, tzinfo=CT)


def game(phase="active"):
    g = Game(rng=random.Random(3))
    for i, name in enumerate(["Westhaven", "Veyran Empire", "Kesh"]):
        g.register(f"U{i}", name, pid=str(100 + i))
    if phase != "registration":
        g.start(force=True, now=datetime(2026, 11, 17, 10, 0, tzinfo=CT))
        g.state.board_size = 6
        for i, p in enumerate(g.state.players.values()):
            p.x, p.y, p.ap = i * 2, 0, 4
    return g


def fake_ui(g):
    return UI(SimpleNamespace(svc=SimpleNamespace(game=g, now=lambda: NOW)))


def run(coro):
    return asyncio.run(coro)


class PanelEmbedTests(unittest.TestCase):
    def test_unnamed_registration_active_dead_ended(self):
        g = game("registration")
        self.assertIn("Name your state", panel_embed(g, "999", NOW).description)
        self.assertIn("registered", panel_embed(g, "100", NOW).description)
        g = game()
        d = panel_embed(g, "100", NOW).description
        self.assertIn("AP 4", d)
        self.assertIn("open", d)
        self.assertIn("Within your Influence: VE", d)
        g.get("100").alive, g.get("100").hp = False, 0
        self.assertIn("Jury", panel_embed(g, "100", NOW).description)
        g.declare_winners(["101"])
        self.assertIn("You won", panel_embed(g, "101", NOW).description)


class RecentActivityTests(unittest.TestCase):
    def test_recent_field_newest_first_and_capped(self):
        g = game()
        recent = [{"time": NOW.replace(minute=m).isoformat(), "text": f"event {m}"} for m in range(6)]
        emb = panel_embed(g, "100", NOW, recent)
        field = next(f for f in emb.fields if f.name == "Recent activity")
        self.assertLess(field.value.index("event 5"), field.value.index("event 0"))
        long = [{"time": NOW.isoformat(), "text": "x" * 300} for _ in range(6)]
        field = next(f for f in panel_embed(g, "100", NOW, long).fields if f.name == "Recent activity")
        self.assertLessEqual(len(field.value), 1024)
        self.assertEqual(panel_embed(g, "100", NOW, []).fields, [])


class PanelLayoutTests(unittest.TestCase):
    def test_registration_hides_other_states(self):
        async def build():
            g = game("registration")
            ui = fake_ui(g)
            return ui, ui.panel_view("100"), g
        ui, view, g = run(build())
        ids = [getattr(c, "custom_id", None) for c in view.children]
        self.assertNotIn("panel:b:pacts", ids)                 # no pact invite list to browse
        for q in ("Veyran Empire", "Kesh", "Nowhere", "101"):  # every lookup fails the same way
            self.assertEqual(ui.resolve(q), (None, "Other States stay hidden until the game starts."))
        self.assertEqual(ui.resolve("Kesh", admin=True)[0], "102")
        g.start(force=True, now=NOW)
        self.assertEqual(ui.resolve("Kesh")[0], "102")

    def test_views_build_in_every_phase(self):
        async def build():
            out = {}
            g = game("registration")
            out["unnamed"] = fake_ui(g).panel_view("999")
            g = game()
            ui = fake_ui(g)
            out["alive"] = ui.panel_view("100")
            g.get("102").alive = False
            out["jury"] = ui.panel_view("102")
            out["move"] = MoveView(ui, "100")
            return out
        views = run(build())
        ids = [getattr(c, "custom_id", None) for c in views["alive"].children]
        self.assertIn("panel:act", ids)
        self.assertIn("panel:b:dm", ids)
        jury_select = next(c for c in views["jury"].children if getattr(c, "custom_id", "") == "panel:act")
        self.assertEqual([o.value for o in jury_select.item.options] if hasattr(jury_select, "item")
                         else [o.value for o in jury_select.options], ["vote"])
        self.assertEqual(len(views["move"].children), 9)
        # A2 (x=0, y=0): moving west/north is off the board -> disabled
        disabled = [b.label for b in views["move"].children if b.disabled]
        self.assertIn("←", disabled)
        self.assertIn("↑", disabled)


if __name__ == "__main__":
    unittest.main()
