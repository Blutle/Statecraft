import random
import tempfile
import unittest
from pathlib import Path

from statecraft import geometry, store
from statecraft.game import Game
from statecraft.models import GameState


def in_range_offsets(rng: int, radius: int = 5) -> set[tuple[int, int]]:
    c = radius
    return {
        (x - c, y - c)
        for x in range(2 * radius + 1)
        for y in range(2 * radius + 1)
        if geometry.in_range(c, c, x, y, rng)
    }


class GeometryTests(unittest.TestCase):
    def test_range_2_is_diamond(self):
        expected = {(0, 1), (0, 2), (1, 0), (2, 0), (0, -1), (0, -2), (-1, 0), (-2, 0),
                    (1, 1), (1, -1), (-1, 1), (-1, -1)}
        self.assertEqual(in_range_offsets(2), expected)

    def test_knight_offset_needs_range_3(self):
        self.assertFalse(geometry.in_range(0, 0, 2, 1, 2))
        self.assertTrue(geometry.in_range(0, 0, 2, 1, 3))

    def test_two_diagonal_steps_need_range_4(self):
        self.assertFalse(geometry.in_range(0, 0, 2, 2, 3))
        self.assertTrue(geometry.in_range(0, 0, 2, 2, 4))

    def test_self_not_in_range(self):
        self.assertFalse(geometry.in_range(3, 3, 3, 3, 5))

    def test_labels(self):
        self.assertEqual(geometry.coord_label(0, 0), "A1")
        self.assertEqual(geometry.coord_label(15, 15), "P16")


def make_game(n=4, seed=1) -> Game:
    g = Game(rng=random.Random(seed))
    names = ["Republic of Aldren", "Veyran Empire", "Free Cities", "Westhaven", "Iron Dominion", "Kesh"]
    for i in range(n):
        assert g.register(f"player{i + 1}", names[i]).ok
    assert g.start().ok
    return g


def place(g: Game, pid: str, x: int, y: int):
    p = g.get(pid)
    p.x, p.y = x, y


class LifecycleTests(unittest.TestCase):
    def test_board_size_and_unique_placement(self):
        g = make_game(4)
        self.assertEqual(g.state.board_size, 4)
        spots = {(p.x, p.y) for p in g.state.players.values()}
        self.assertEqual(len(spots), 4)
        self.assertTrue(all(p.ap == 0 for p in g.state.players.values()))

    def test_duplicate_names_rejected(self):
        g = Game()
        self.assertTrue(g.register("a", "Westhaven").ok)
        self.assertFalse(g.register("b", "westhaven").ok)
        self.assertFalse(g.register("A", "Other").ok)

    def test_markers_unique(self):
        g = Game()
        g.register("a", "Veyran Empire")
        g.register("b", "Veyran Elite")
        markers = [p.marker for p in g.state.players.values()]
        self.assertEqual(len(set(markers)), 2)

    def test_need_two_players(self):
        g = Game()
        g.register("a", "Solo")
        self.assertFalse(g.start().ok)


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.g = make_game(4)
        # Spread everyone out on the 4x4 board, then give AP.
        place(self.g, "p1", 0, 0)
        place(self.g, "p2", 2, 0)
        place(self.g, "p3", 3, 3)
        place(self.g, "p4", 0, 3)
        for p in self.g.state.players.values():
            p.ap = 5

    def test_move_and_blocking(self):
        self.assertTrue(self.g.move("p1", "e").ok)          # to B1
        self.assertFalse(self.g.move("p1", "e").ok)         # C1 occupied by p2
        self.assertFalse(self.g.move("p1", "n").ok)         # off board
        self.assertEqual(self.g.get("p1").ap, 4)

    def test_dead_states_block_movement(self):
        self.g.get("p2").alive = False
        place(self.g, "p1", 1, 0)
        self.assertFalse(self.g.move("p1", "e").ok)

    def test_rejected_action_changes_nothing(self):
        before = self.g.state.to_dict()
        self.assertFalse(self.g.attack("p1", "p3").ok)      # out of range
        self.assertFalse(self.g.move("p1", "nw").ok)
        self.g.get("p1").ap = 0
        before["players"]["p1"]["ap"] = 0
        self.assertFalse(self.g.upgrade("p1").ok)
        self.assertEqual(self.g.state.to_dict(), before)

    def test_attack_eliminate_and_win(self):
        # p1 at A1, p2 at C1 (distance 2, in range)
        for _ in range(3):
            self.assertTrue(self.g.attack("p1", "p2").ok)
        p2 = self.g.get("p2")
        self.assertFalse(p2.alive)
        self.assertFalse(self.g.attack("p1", "p2").ok)       # already dead
        self.assertFalse(self.g.move("p2", "s").ok)          # dead can't act
        # Finish the others off
        for pid in ("p3", "p4"):
            self.g.get(pid).hp = 1
            place(self.g, pid, *{"p3": (0, 1), "p4": (1, 1)}[pid])
        self.assertTrue(self.g.attack("p1", "p3").ok)
        res = self.g.attack("p1", "p4")
        self.assertTrue(res.ok)
        self.assertEqual(self.g.state.phase, "ended")
        self.assertEqual(self.g.state.winner, "p1")
        self.assertIn("GAME_OVER", [e.kind for e in res.events])

    def test_upgrade_extends_range(self):
        place(self.g, "p3", 2, 1)                            # knight offset from p1
        self.assertFalse(self.g.attack("p1", "p3").ok)
        self.assertTrue(self.g.upgrade("p1").ok)
        self.assertEqual(self.g.get("p1").ap, 3)
        self.assertTrue(self.g.attack("p1", "p3").ok)

    def test_transfer(self):
        self.assertTrue(self.g.transfer("p1", "p2", 3).ok)
        self.assertEqual(self.g.get("p1").ap, 2)
        self.assertEqual(self.g.get("p2").ap, 8)
        self.assertFalse(self.g.transfer("p1", "p2", 3).ok)  # not enough
        self.assertFalse(self.g.transfer("p1", "p3", 1).ok)  # out of range
        self.assertFalse(self.g.transfer("p1", "p1", 1).ok)

    def test_info_purchase_sees_transfers(self):
        self.g.transfer("p1", "p2", 1)
        res = self.g.buy_info("p4")
        self.assertTrue(res.ok)
        self.assertIn("Republic of Aldren -> Veyran Empire: 1 AP", res.message)
        self.assertFalse(self.g.buy_info("p4").ok)           # once per day
        # Later transfers are forwarded to the buyer
        res = self.g.transfer("p2", "p1", 1)
        self.assertIn("p4", res.events[0].private)
        # Death ends access immediately
        self.g.get("p4").hp = 1
        place(self.g, "p4", 0, 1)
        self.assertTrue(self.g.attack("p1", "p4").ok)
        self.assertNotIn("p4", self.g.state.info_access)
        res = self.g.transfer("p2", "p1", 1)
        self.assertNotIn("p4", res.events[0].private)
        # Access ends with the day
        self.g.buy_info("p3")
        self.g.next_day()
        self.assertEqual(self.g.state.info_access, [])
        self.assertEqual(self.g.state.transfers_today, [])


class DayAndJuryTests(unittest.TestCase):
    def test_next_day_awards_ap_to_living_only(self):
        g = make_game(3)
        g.get("p3").alive = False
        g.next_day()
        self.assertEqual(g.state.day, 1)
        self.assertEqual([g.get(p).ap for p in ("p1", "p2", "p3")], [1, 1, 0])

    def test_jury_votes(self):
        g = make_game(6)
        for pid in ("p3", "p4", "p5"):
            g.get(pid).alive = False
        self.assertFalse(g.vote("p1", "p2").ok)              # living can't vote
        self.assertFalse(g.vote("p3", "p4").ok)              # can't vote for dead
        self.assertTrue(g.vote("p3", "p1").ok)
        self.assertTrue(g.vote("p3", "p2").ok)               # changed vote
        self.assertTrue(g.vote("p4", "p2").ok)
        self.assertTrue(g.vote("p5", "p2").ok)
        g.next_day()
        self.assertEqual(g.get("p2").ap, 2)                  # 3 votes -> +1, plus daily
        self.assertEqual(g.get("p1").ap, 1)
        self.assertEqual(g.state.jury_votes, {})


class JuryScoreTests(unittest.TestCase):
    def test_scores_rank_votes_for_winner(self):
        g = make_game(4)
        place(g, "p1", 0, 0); place(g, "p2", 1, 0); place(g, "p3", 2, 0); place(g, "p4", 3, 3)
        g.get("p1").ap = 20
        g.state.day = 1
        for _ in range(3):
            g.attack("p1", "p2")                     # p2 dies day 1
        g.vote("p2", "p1")
        g.next_day()                                 # night 1 tallied -> day 2
        for _ in range(3):
            g.attack("p1", "p3")                     # p3 dies day 2
        g.vote("p2", "p4")
        g.vote("p3", "p1")
        g.next_day()                                 # night 2 tallied -> day 3
        g.vote("p2", "p1")                           # cast but never tallied
        g.get("p4").hp = 1
        place(g, "p4", 0, 1)
        g.attack("p1", "p4")                         # p1 wins on day 3
        self.assertEqual(g.state.winner, "p1")
        rows = {r["player"].id: r for r in g.jury_scores()}
        self.assertEqual((rows["p2"]["for_winner"], rows["p2"]["nights"]), (1, 2))
        self.assertEqual((rows["p3"]["for_winner"], rows["p3"]["nights"]), (1, 1))
        self.assertEqual(rows["p4"]["nights"], 0)
        self.assertEqual(g.jury_scores()[0]["player"].id, "p3")   # tie on votes, better rate
        self.assertEqual(len(g.state.jury_history), 4)


class HistoryTests(unittest.TestCase):
    def test_every_call_recorded_including_rejections_and_anon_author(self):
        records = []
        g = Game(rng=random.Random(1), recorder=records.append)
        g.register("greg", "Westhaven")
        g.register("sam", "Kesh")
        g.start()
        g.move("p1", "n")                            # rejected: 0 AP
        g.anon("p2", "trust no one")
        g.dm("p1", "p2", "ally?")
        g.say("p2", "Kesh is peaceful")
        self.assertEqual([r["action"] for r in records],
                         ["register", "register", "start", "move", "anon", "dm", "say"])
        self.assertFalse(records[3]["ok"])
        self.assertIn("snapshot", records[2])
        anon = records[4]
        self.assertEqual(anon["actor"]["owner"], "sam")            # true author kept
        self.assertEqual(anon["args"]["text"], "trust no one")
        self.assertNotIn("Kesh", anon["events"][0]["public"])     # but not shown publicly
        self.assertEqual(records[5]["target"]["state"], "Kesh")

    def test_history_file_appends_with_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            h = store.History(store.history_path(Path(d) / "game.json"))
            h.append({"action": "a"})
            h.append({"action": "b"})
            h2 = store.History(h.path)                # reopen continues numbering
            h2.append({"action": "c"})
            self.assertEqual([r["seq"] for r in h2.tail(10)], [1, 2, 3])
            self.assertTrue(h.path.name.endswith(".history.jsonl"))


class StoreTests(unittest.TestCase):
    def test_round_trip(self):
        g = make_game(3)
        g.next_day()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "game.json"
            store.save(g.state, path)
            loaded = store.load(path)
        self.assertEqual(loaded.to_dict(), g.state.to_dict())

    def test_missing_file(self):
        self.assertIsNone(store.load(Path(tempfile.gettempdir()) / "definitely-missing-statecraft.json"))

    def test_bad_schema(self):
        with self.assertRaises(ValueError):
            GameState.from_dict({"schema_version": 999})


if __name__ == "__main__":
    unittest.main()
