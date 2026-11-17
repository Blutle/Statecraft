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
        self.assertTrue(run_text(g, "fake2", "gift WE", WINDOW).ok)
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


class AdminLogTests(unittest.TestCase):
    def test_history_filter_and_lines(self):
        import tempfile
        from pathlib import Path
        from statecraft import store
        from statecraft.discord_format import history_line
        with tempfile.TemporaryDirectory() as d:
            hist = store.History(Path(d) / "g.history.jsonl")
            g = Game(rng=random.Random(2), recorder=hist.append)
            for i, name in enumerate(["Westhaven", "Veyran Empire", "Kesh"]):
                g.register(f"User{i}", name, pid=f"fake{i + 1}")
            g.start(force=True)
            g.anon("fake2", "Westhaven is lying")
            g.dm("fake1", "fake3", "secret deal?")
            g.move("fake3", "n")                                  # rejected: no AP
            pact = g.create_pact("fake1", "Iron Accord").events[0].data["pact"]
            g.pact_message("fake1", pact, "first pact message")
            mine = hist.records(lambda r: "fake1" in {(r.get("actor") or {}).get("id"),
                                                      (r.get("target") or {}).get("id")})
            self.assertEqual([r["action"] for r in mine], ["register", "dm", "pact_create", "pact_message"])
            lines = [history_line(r, g) for r in hist.records()]
            anon = next(l for l in lines if " anon" in l)
            self.assertIn("**Veyran Empire**", anon)              # the referee sees the real author
            self.assertIn("✗", next(l for l in lines if " move" in l))
            self.assertIn("in Iron Accord", next(l for l in lines if "pact_message" in l))
            self.assertEqual(len(hist.records(last=2)), 2)


class ActionCardTests(unittest.TestCase):
    def test_every_public_event_makes_a_clean_card(self):
        from statecraft.discord_format import action_card
        seen = {}
        g = Game(rng=random.Random(2), recorder=None)
        for i, name in enumerate(["Westhaven", "Veyran Empire", "Kesh", "Norland"]):
            g.register(f"User{i}", name, pid=f"fake{i + 1}")
        events = list(g.start(force=True).events)
        for i, p in enumerate(g.state.players.values()):
            p.x, p.y, p.ap = i, 0, 9
        g.state.board_size = 6
        events += g.move("fake1", "s").events
        events += g.upgrade("fake2").events
        events += g.buy_info("fake3").events
        events += g.attack("fake2", "fake3").events
        g.get("fake3").hp = 1
        events += g.attack("fake2", "fake3").events                 # elimination
        g.vote("fake3", "fake1")
        events += g.next_day().events                                 # jury result + daily AP
        events += g.admin_edit("fake4", "range", "4").events
        events += g.admin_edit("fake3", "hp", "2").events              # revive
        events += g.declare_winners(["fake1", "fake2"]).events
        for ev in events:
            if ev.public and ev.kind not in ("PUBLIC_CHAT", "ANONYMOUS"):
                card = action_card(ev, g, WINDOW)
                text = (card.description or "") + "".join(f.value for f in card.fields)
                self.assertTrue(text, ev.kind)
                self.assertLess(len(text), 1500, ev.kind)
                self.assertNotIn("None", text, ev.kind)
                seen[ev.kind] = text
        for kind in ("GAME_START", "MOVE", "UPGRADE", "INFO_PURCHASE", "ATTACK", "ELIMINATION",
                     "JURY_RESULT", "DAILY_AP", "ADMIN_EDIT", "REVIVED", "GAME_OVER"):
            self.assertIn(kind, seen)
        self.assertIn("`WES` Westhaven", seen["GAME_START"])
        self.assertIn("joint winners", seen["GAME_OVER"])
        self.assertIn("User2", seen["ELIMINATION"])                   # owner revealed


class DurationTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_duration("+2h"), 7200)
        self.assertEqual(parse_duration("30m"), 1800)
        self.assertEqual(parse_duration("-1d"), -86400)
        self.assertEqual(parse_duration("0"), 0)
        self.assertEqual(parse_duration("15"), 900)          # bare number = minutes
        self.assertIsNone(parse_duration("soon"))


class GuideCardDiffTests(unittest.TestCase):
    """/admin guides only edits cards whose content changed (avoids rate limits)."""

    def test_unchanged_cards_are_skipped(self):
        import discord
        from types import SimpleNamespace
        from statecraft.discord_bot import _same_card
        card = discord.Embed(title="Influence", description="Body", colour=0x1ABC9C)
        card.set_image(url="attachment://influence.png")
        card.set_footer(text="Every State starts with 2 Influence Range")
        # Once posted, Discord rewrites the image URL to its CDN
        live = discord.Embed.from_dict({**card.to_dict(), "image": {"url": "https://cdn.example/influence.png"}})
        self.assertTrue(_same_card(SimpleNamespace(embeds=[live]), card))
        edited = card.copy()
        edited.description = "New body"
        self.assertFalse(_same_card(SimpleNamespace(embeds=[live]), edited))


class ChatMuteTests(unittest.TestCase):
    def test_mute_and_unmute_one_chat(self):
        from types import SimpleNamespace
        from statecraft.discord_service import GameService
        svc = SimpleNamespace(server={}, save_server=lambda: None)
        muted = lambda pid, key: GameService.muted(svc, pid, key)
        self.assertFalse(muted("1", "pact:k1"))                  # pings by default
        GameService.set_muted(svc, "1", "pact:k1", True)
        GameService.set_muted(svc, "1", "pact:k1", True)         # idempotent
        self.assertTrue(muted("1", "pact:k1"))
        self.assertFalse(muted("1", "dm:2"))
        self.assertFalse(muted("2", "pact:k1"))                  # per player
        GameService.set_muted(svc, "1", "pact:k1", False)
        self.assertEqual(svc.server["mutes"]["1"], [])


class ReactionPermissionTests(unittest.TestCase):
    def test_reactions_off_outside_the_jury(self):
        from statecraft import discord_setup as ds
        self.assertFalse(ds.READ_ONLY["add_reactions"])
        self.assertFalse(ds.MEMBER["add_reactions"])
        self.assertFalse(ds.PLAYER_PRIVATE["add_reactions"])
        for perms in (ds.READ_ONLY, ds.MEMBER, ds.PLAYER_PRIVATE):   # no files, images, or link embeds
            self.assertFalse(perms["attach_files"])
            self.assertFalse(perms["embed_links"])
        self.assertTrue(all(ds.JURY_EXTRAS.values()))       # #jury keeps them
        for k in ("add_reactions", "attach_files", "embed_links"):
            self.assertTrue(ds.BOT_PERMS[k])                 # the bot must hold what it sets


if __name__ == "__main__":
    unittest.main()
