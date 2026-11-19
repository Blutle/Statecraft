"""Engine rules for AI-controlled states (AI_PLAYERS_PLAN.md §0-1)."""

import copy
import random
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from statecraft.game import Game

CT = ZoneInfo("America/Chicago")
IN_WINDOW = datetime(2026, 11, 19, 15, 0, tzinfo=CT)


def make_game() -> Game:
    """Two humans (h1, h2) and two AI states (npc-bal, npc-zog) on a 6x6 board."""
    g = Game(rng=random.Random(1))
    g.state.settings["board_size"] = 6
    assert g.register("alice", "Republic of Aldren", pid="h1").ok
    assert g.register("bob", "Veyran Empire", pid="h2").ok
    assert g.register("Jarl Balderdash (AI)", "Kingdom of Balderheim", pid="npc-bal").ok
    assert g.register("Zog the Unwise (AI)", "Zogland", pid="npc-zog").ok
    assert g.start(force=True).ok
    for pid, (x, y) in {"h1": (0, 0), "h2": (5, 5), "npc-bal": (1, 1), "npc-zog": (5, 0)}.items():
        p = g.get(pid)
        p.x, p.y = x, y
    return g


def kill(g: Game, pid: str, by: str = "h1"):
    g.get(pid).hp = 1
    g.get(by).ap = 1
    g.get(by).range_upgrades = 20
    res = g.attack(by, pid)
    assert res.ok, res.message
    return res


class LegalActionTests(unittest.TestCase):
    def test_every_listed_action_succeeds(self):
        g = make_game()
        g.get("npc-bal").ap = 5
        actions = g.legal_actions("npc-bal", IN_WINDOW)
        kinds = {a["action"] for a in actions}
        self.assertEqual(kinds, {"pass", "upgrade", "spy", "attack", "gift", "move"})
        for a in actions:
            trial = copy.deepcopy(g)
            res = trial.perform("npc-bal", a, IN_WINDOW)
            self.assertTrue(res.ok, f"{a}: {res.message}")

    def test_targets_are_only_alive_states_in_range(self):
        g = make_game()
        g.get("npc-bal").ap = 5
        targets = {a["target"] for a in g.legal_actions("npc-bal") if a["action"] == "attack"}
        self.assertEqual(targets, {"h1"})          # h2 and Zogland are out of range 2

    def test_moves_skip_edges_and_occupied_squares(self):
        g = make_game()
        g.get("h1").ap = 5                          # h1 sits in the corner A1, Balderheim at B2
        moves = {a["direction"] for a in g.legal_actions("h1") if a["action"] == "move"}
        self.assertEqual(moves, {"e", "s"})

    def test_ap_gates_each_action(self):
        g = make_game()
        self.assertEqual(g.legal_actions("npc-bal", IN_WINDOW), [{"action": "pass"}])     # 0 AP
        g.get("npc-bal").ap = 1
        kinds = {a["action"] for a in g.legal_actions("npc-bal", IN_WINDOW)}
        self.assertNotIn("upgrade", kinds)                                                # costs 2
        self.assertIn("attack", kinds)

    def test_window_and_cooldown_leave_only_pass(self):
        g = make_game()
        g.get("npc-bal").ap = 5
        night = IN_WINDOW.replace(hour=21)
        self.assertEqual(g.legal_actions("npc-bal", night), [{"action": "pass"}])
        self.assertTrue(g.perform("npc-bal", {"action": "upgrade"}, IN_WINDOW).ok)
        self.assertEqual(g.legal_actions("npc-bal", IN_WINDOW + timedelta(minutes=5)), [{"action": "pass"}])
        self.assertGreater(len(g.legal_actions("npc-bal", IN_WINDOW + timedelta(minutes=16))), 1)

    def test_no_spy_twice(self):
        g = make_game()
        g.get("npc-bal").ap = 5
        self.assertTrue(g.perform("npc-bal", {"action": "spy"}).ok)
        self.assertNotIn({"action": "spy"}, g.legal_actions("npc-bal"))

    def test_perform_rechecks_and_rejects_nonsense(self):
        g = make_game()
        g.get("npc-bal").ap = 5
        self.assertFalse(g.perform("npc-bal", {"action": "attack", "target": "h2"}).ok)   # out of range
        self.assertFalse(g.perform("npc-bal", {"action": "nuke"}).ok)
        self.assertTrue(g.perform("npc-bal", {"action": "pass"}).ok)

    def test_destroyed_states_only_pass(self):
        g = make_game()
        kill(g, "npc-bal")
        self.assertEqual(g.legal_actions("npc-bal"), [{"action": "pass"}])


class EliminationTests(unittest.TestCase):
    def test_destroyed_ai_leaves_instead_of_joining_jury(self):
        g = make_game()
        res = kill(g, "npc-bal")
        elim = next(e for e in res.events if e.kind == "ELIMINATION")
        self.assertIn("AI state ruled by Jarl Balderdash (AI)", elim.public)
        self.assertNotIn("Jury", elim.public)
        self.assertEqual(elim.private, {})
        self.assertFalse(g.is_juror(g.get("npc-bal")))

    def test_destroyed_human_still_joins_jury(self):
        g = make_game()
        res = kill(g, "h2")
        elim = next(e for e in res.events if e.kind == "ELIMINATION")
        self.assertIn("joins the Jury", elim.public)
        self.assertTrue(g.is_juror(g.get("h2")))

    def test_referee_destroying_an_ai(self):
        g = make_game()
        res = g.admin_edit("npc-zog", "hp", "0")
        self.assertTrue(res.ok, res.message)
        elim = next(e for e in res.events if e.kind == "ELIMINATION")
        self.assertIn("leaves the game", elim.public)

    def test_ai_cannot_vote(self):
        g = make_game()
        kill(g, "npc-bal")
        res = g.vote("npc-bal", "h2")
        self.assertFalse(res.ok)
        self.assertIn("AI", res.message)

    def test_jury_scores_leave_out_ai(self):
        g = make_game()
        kill(g, "npc-bal")
        kill(g, "npc-zog")
        kill(g, "h2")                               # h1 wins
        self.assertEqual(g.state.phase, "ended")
        self.assertEqual([r["player"].id for r in g.jury_scores()], ["h2"])


class MessagingTests(unittest.TestCase):
    def test_humans_and_ai_can_talk(self):
        g = make_game()
        self.assertTrue(g.dm("h1", "npc-bal", "Hail, Jarl!").ok)
        self.assertTrue(g.dm("npc-bal", "h1", "Hail, friend!").ok)

    def test_ai_never_message_each_other(self):
        g = make_game()
        self.assertFalse(g.dm("npc-bal", "npc-zog", "hi").ok)
        self.assertTrue(g.dm_problem("npc-zog", "npc-bal"))

    def test_fallen_ai_takes_no_messages(self):
        g = make_game()
        kill(g, "npc-bal")
        kill(g, "h2")                               # a juror
        for sender in ("h1", "h2"):
            res = g.dm(sender, "npc-bal", "hello?")
            self.assertFalse(res.ok)
            self.assertIn("answers no more", res.message)

    def test_a_fallen_ai_is_not_a_juror_to_message(self):
        g = make_game()
        kill(g, "npc-bal")
        kill(g, "h2")                               # h2's only fellow casualty is an AI
        self.assertEqual([p.id for p in g.state.players.values() if not g.dm_problem("h2", p.id)], [])


class PactTests(unittest.TestCase):
    def test_ai_cannot_be_invited_or_found_pacts(self):
        g = make_game()
        res = g.create_pact("h1", "Northern League")
        self.assertTrue(res.ok)
        pact_id = next(iter(g.state.pacts))
        res = g.invite_to_pact("h1", pact_id, "npc-bal")
        self.assertFalse(res.ok)
        self.assertIn("AI state", res.message)
        self.assertFalse(g.create_pact("npc-bal", "Balderdash Bros").ok)



class ReservedTagTests(unittest.TestCase):
    """AI characters' state names and abbreviations are kept for them."""

    def game(self):
        return Game(rng=random.Random(1), reserved=lambda: ({"church of the purge"}, {"PUR"}))

    def test_players_cant_take_reserved_names_or_tags(self):
        g = self.game()
        res = g.register("alice", "Church of the Purge", pid="h1")
        self.assertFalse(res.ok)
        self.assertIn("reserved for an AI state", res.message)
        res = g.register("alice", "Purity League", pid="h1", abbreviation="pur")
        self.assertFalse(res.ok)
        self.assertIn("reserved", res.message)

    def test_generated_markers_avoid_reserved_tags(self):
        g = self.game()
        self.assertTrue(g.register("alice", "Purple Unicorn Republic", pid="h1").ok)   # would be PUR
        self.assertNotEqual(g.get("h1").marker, "PUR")

    def test_the_ai_itself_can_use_them(self):
        g = self.game()
        self.assertTrue(g.register("Sister Veronica (AI)", "Church of the Purge", pid="npc-veronica",
                                   abbreviation="PUR").ok)

    def test_shipped_characters_are_reserved(self):
        from statecraft import npc
        names, tags = npc.reserved_tags()
        for key in npc.character_keys():
            c = npc.load_character(key)
            self.assertIn(c.state_name.lower(), names)
            if c.abbreviation:
                self.assertIn(c.abbreviation, tags)

if __name__ == "__main__":
    unittest.main()
