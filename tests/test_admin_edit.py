import random
import unittest

from statecraft.game import Game


def game():
    records = []
    g = Game(rng=random.Random(3), recorder=records.append)
    for i, name in enumerate(["Westhaven", "Veyran Empire", "Kesh", "Norland"]):
        g.register(f"Owner{i}", name, pid=str(i))
    g.start(force=True)
    for i, p in enumerate(g.state.players.values()):
        p.x, p.y = i, 0
    g.records = records
    return g


def kinds(res):
    return [e.kind for e in res.events]


class AdminEditTests(unittest.TestCase):
    def test_public_fields_announced_hidden_fields_not(self):
        g = game()
        res = g.admin_edit("0", "range", "4")
        self.assertTrue(res.ok)
        self.assertEqual(g.range_of(g.get("0")), 4)
        self.assertIn("Referee adjustment", res.events[0].public)
        self.assertIn("0", res.events[0].private)                    # the player is told privately
        res = g.admin_edit("0", "ap", "7")
        self.assertEqual(g.get("0").ap, 7)
        self.assertIsNone(res.events[0].public)                      # AP stays secret
        res = g.admin_edit("0", "hp", "2", announce=False)
        self.assertIsNone(res.events[0].public)                      # quiet edit
        self.assertEqual(g.records[-1]["action"], "admin_edit")      # always logged

    def test_name_and_abbreviation_rules(self):
        g = game()
        self.assertFalse(g.admin_edit("0", "name", "Kesh").ok)      # taken
        self.assertFalse(g.admin_edit("0", "name", "X" * 25).ok)
        self.assertFalse(g.admin_edit("0", "name", "@everyone").ok)
        self.assertTrue(g.admin_edit("0", "name", "New Westhaven").ok)
        self.assertEqual(g.get("0").state_name, "New Westhaven")
        self.assertFalse(g.admin_edit("0", "abbreviation", "AB").ok)
        self.assertFalse(g.admin_edit("0", "abbreviation", g.get("1").marker).ok)
        self.assertTrue(g.admin_edit("0", "abbreviation", "nwh").ok)
        self.assertEqual(g.get("0").marker, "NWH")

    def test_position(self):
        g = game()
        self.assertFalse(g.admin_edit("0", "position", "B1").ok)    # occupied by state 1
        self.assertFalse(g.admin_edit("0", "position", "Z9").ok)    # off the board
        self.assertFalse(g.admin_edit("0", "position", "middle").ok)
        self.assertTrue(g.admin_edit("0", "position", "c3").ok)
        self.assertEqual(g.pos(g.get("0")), "C3")

    def test_hp_zero_destroys_and_revive(self):
        g = game()
        res = g.admin_edit("1", "hp", "0")
        self.assertIn("ELIMINATION", kinds(res))
        self.assertFalse(g.get("1").alive)
        self.assertIn("Owner1", next(e for e in res.events if e.kind == "ELIMINATION").public)
        res = g.admin_edit("1", "hp", "2")
        self.assertIn("REVIVED", kinds(res))
        self.assertTrue(g.get("1").alive)
        self.assertIsNone(g.get("1").eliminated_on_day)

    def test_destroying_the_second_last_state_ends_the_game(self):
        g = game()
        g.admin_edit("1", "hp", "0")
        g.admin_edit("2", "hp", "0")
        res = g.admin_edit("3", "hp", "0")
        self.assertIn("GAME_OVER", kinds(res))
        self.assertEqual(g.winners(), ["0"])

    def test_cooldown_owner_and_bad_input(self):
        g = game()
        g.get("0").last_action = "2026-11-17T15:00:00-06:00"
        self.assertTrue(g.admin_edit("0", "cooldown", "reset").ok)
        self.assertIsNone(g.get("0").last_action)
        self.assertTrue(g.admin_edit("0", "owner", "Greg").ok)
        self.assertEqual(g.get("0").owner_name, "Greg")
        self.assertFalse(g.admin_edit("0", "ap", "lots").ok)
        self.assertFalse(g.admin_edit("0", "ap", "-1").ok)
        self.assertFalse(g.admin_edit("0", "range", "0").ok)
        self.assertFalse(g.admin_edit("0", "colour", "red").ok)
        self.assertFalse(g.admin_edit("9", "ap", "1").ok)
        self.assertFalse(g.admin_edit("0", "ap", str(g.get("0").ap)).ok)   # no change


if __name__ == "__main__":
    unittest.main()
