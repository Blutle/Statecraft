import random
import unittest

from statecraft.game import Game


def game(n=8):
    records = []
    g = Game(rng=random.Random(5), recorder=records.append)
    for i in range(n):
        g.register(f"U{i}", f"State {chr(65 + i)}", pid=str(i))
    g.start(force=True)
    g.records = records
    return g


def kinds(res):
    return [e.kind for e in res.events]


class PactTests(unittest.TestCase):
    def test_create_invite_accept_message(self):
        g = game()
        res = g.create_pact("0", "Iron Accord")
        self.assertTrue(res.ok)
        pact = res.events[0].data["pact"]
        self.assertEqual(kinds(res), ["PACT_JOINED"])
        res = g.invite_to_pact("0", pact, "1")
        self.assertTrue(res.ok)
        self.assertIn("1", res.events[0].private)
        self.assertIn("State A", res.events[0].private["1"])           # invitee sees the founder's state name
        self.assertTrue(g.respond_to_pact("1", pact, True).ok)
        self.assertEqual(g.pact(pact)["members"], ["0", "1"])
        res = g.pact_message("1", pact, "hello allies")
        self.assertEqual(res.events[0].data["recipients"], ["0"])
        self.assertEqual(res.events[0].data["state"], "State B")
        self.assertEqual(g.records[-1]["action"], "pact_message")       # every message is logged
        self.assertEqual(g.records[-1]["args"]["text"], "hello allies")

    def test_only_founder_invites_and_kicks(self):
        g = game()
        pact = g.create_pact("0", "Accord").events[0].data["pact"]
        g.invite_to_pact("0", pact, "1")
        g.respond_to_pact("1", pact, True)
        self.assertFalse(g.invite_to_pact("1", pact, "2").ok)
        self.assertFalse(g.kick_from_pact("1", pact, "0").ok)
        self.assertTrue(g.kick_from_pact("0", pact, "1").ok)
        self.assertEqual(g.pact(pact)["members"], ["0"])

    def test_decline_and_stale_invites(self):
        g = game()
        pact = g.create_pact("0", "Accord").events[0].data["pact"]
        g.invite_to_pact("0", pact, "1")
        self.assertFalse(g.invite_to_pact("0", pact, "1").ok)            # already pending
        res = g.respond_to_pact("1", pact, False)
        self.assertIn("0", res.events[0].private)
        self.assertFalse(g.respond_to_pact("1", pact, True).ok)          # no longer open
        self.assertFalse(g.respond_to_pact("2", pact, True).ok)          # never invited

    def test_founder_leaving_passes_founding_and_last_leaver_dissolves(self):
        g = game()
        pact = g.create_pact("0", "Accord").events[0].data["pact"]
        for m in ("1", "2"):
            g.invite_to_pact("0", pact, m)
            g.respond_to_pact(m, pact, True)
        res = g.leave_pact("0", pact)
        self.assertEqual(res.events[0].data["new_founder"], "1")
        self.assertEqual(g.pact(pact)["founder"], "1")
        self.assertTrue(g.invite_to_pact("1", pact, "3").ok)
        g.leave_pact("1", pact)
        res = g.leave_pact("2", pact)
        self.assertTrue(res.events[0].data["dissolved"])
        self.assertIsNone(g.pact(pact))
        self.assertFalse(g.respond_to_pact("3", pact, True).ok)          # invite died with the pact

    def test_limits(self):
        g = game(10)
        for k in range(3):
            self.assertTrue(g.create_pact("0", f"P{k}").ok)
        self.assertFalse(g.create_pact("0", "P4").ok)                    # founded 3
        # Leaving doesn't refund the founding allowance
        g.leave_pact("0", "k1")
        self.assertFalse(g.create_pact("0", "P5").ok)
        # Membership cap of 6: player 9 joins six pacts founded by others
        for f in range(1, 7):
            pact = g.create_pact(str(f), f"Q{f}").events[0].data["pact"]
            g.invite_to_pact(str(f), pact, "9")
            self.assertTrue(g.respond_to_pact("9", pact, True).ok)
        pact = g.create_pact("7", "Q7").events[0].data["pact"]
        g.invite_to_pact("7", pact, "9")
        self.assertFalse(g.respond_to_pact("9", pact, True).ok)
        self.assertFalse(g.create_pact("9", "Mine").ok)

    def test_game_end_closes_pacts(self):
        g = game()
        pact = g.create_pact("0", "Accord").events[0].data["pact"]
        self.assertTrue(g.pact_message("0", pact, "still going").ok)
        g.declare_winners(["1"])
        self.assertFalse(g.pact_message("0", pact, "too late").ok)

    def test_destruction_removes_a_state_from_its_pacts(self):
        g = game()
        accord = g.create_pact("0", "Accord").events[0].data["pact"]
        solo = g.create_pact("2", "Solo").events[0].data["pact"]
        for m in ("1", "2"):
            g.invite_to_pact("0", accord, m)
        g.respond_to_pact("1", accord, True)                           # "2" still has a pending invite
        for p in g.state.players.values():
            p.ap = 9
        a, b = g.get("1"), g.get("0")                                  # "1" attacks founder "0" to death
        a.x, a.y, b.x, b.y = 0, 0, 1, 0
        b.hp = 1
        res = g.attack("1", "0")
        self.assertTrue(res.ok, res.message)
        self.assertEqual(g.pact(accord)["invites"], ["2"])              # a living invitee keeps theirs
        left =[e for e in res.events if e.kind == "PACT_LEFT"]
        self.assertEqual([e.data["member"] for e in left], ["0"])
        self.assertTrue(left[0].data["destroyed"])
        self.assertEqual(left[0].data["new_founder"], "1")             # founding passes on
        self.assertEqual(g.pact(accord)["members"], ["1"])
        self.assertEqual(g.pacts_of("0"), [])
        self.assertTrue(g.pact(solo))                                   # other pacts untouched
        self.assertFalse(g.create_pact("0", "Ghosts").ok)               # jurors can't use pacts
        self.assertFalse(g.invite_to_pact("1", accord, "0").ok)         # or be invited back

    def test_destruction_withdraws_pending_invites(self):
        g = game()
        pact = g.create_pact("0", "Accord").events[0].data["pact"]
        g.invite_to_pact("0", pact, "1")
        g.admin_edit("1", "hp", "0")                                    # destroyed by the referee
        self.assertEqual(g.pact(pact)["invites"], [])
        self.assertFalse(g.respond_to_pact("1", pact, True).ok)

    def test_names_and_text_validated(self):
        g = game()
        self.assertFalse(g.create_pact("0", "@everyone").ok)
        self.assertFalse(g.create_pact("0", "").ok)
        pact = g.create_pact("0", "Accord").events[0].data["pact"]
        self.assertFalse(g.pact_message("0", pact, "   ").ok)
        self.assertFalse(g.pact_message("0", pact, "x" * 2001).ok)
        self.assertFalse(g.pact_message("1", pact, "not a member").ok)


if __name__ == "__main__":
    unittest.main()
