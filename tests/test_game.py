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


def make_game(n=4, seed=1, size=0) -> Game:
    g = Game(rng=random.Random(seed))
    g.state.settings["board_size"] = size          # 0 = automatic
    names = ["Republic of Aldren", "Veyran Empire", "Free Cities", "Westhaven", "Iron Dominion", "Kesh"]
    for i in range(n):
        assert g.register(f"player{i + 1}", names[i]).ok
    assert g.start(force=True).ok
    return g


def place(g: Game, pid: str, x: int, y: int):
    p = g.get(pid)
    p.x, p.y = x, y


class LifecycleTests(unittest.TestCase):
    def test_board_size_and_unique_placement(self):
        g = make_game(4)
        self.assertEqual(g.state.board_size, 3)              # 4 * 3/4
        spots = {(p.x, p.y) for p in g.state.players.values()}
        self.assertEqual(len(spots), 4)
        self.assertTrue(all(p.ap == 0 for p in g.state.players.values()))

    def test_no_cardinal_touching_at_spawn(self):
        for n in range(2, 21):
            for seed in range(25):
                g = Game(rng=random.Random(seed))
                for i in range(n):
                    g.register(f"u{i}", f"State {i}")
                self.assertTrue(g.start(force=True).ok)
                spots = {(p.x, p.y) for p in g.state.players.values()}
                self.assertEqual(len(spots), n)
                for x, y in spots:
                    for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                        self.assertNotIn(nb, spots, f"{n} players, seed {seed}: touching at {(x, y)}")

    def test_spawn_spacing_can_be_disabled(self):
        g = Game(rng=random.Random(0))
        g.state.settings["spawn_no_cardinal_touch"] = 0
        g.state.settings["board_size"] = 2
        g.register("a", "A State")
        g.register("b", "B State")
        self.assertTrue(g.start(force=True).ok)

    def test_auto_board_size(self):
        g = Game()
        sizes = {n: g.auto_board_size(n) for n in (2, 3, 5, 8, 10, 12, 16, 20)}
        self.assertEqual(sizes, {2: 2, 3: 3, 5: 4, 8: 6, 10: 7, 12: 9, 16: 12, 20: 15})

    def test_explicit_board_size_overrides(self):
        g = Game()
        g.state.settings["board_size"] = 10
        for i in range(4):
            g.register(f"u{i}", f"State {i}")
        g.start(force=True)
        self.assertEqual(g.state.board_size, 10)

    def test_duplicate_names_rejected(self):
        g = Game()
        self.assertTrue(g.register("a", "Westhaven").ok)
        self.assertFalse(g.register("b", "westhaven").ok)
        self.assertFalse(g.register("A", "Other").ok)

    def test_discord_id_registration_and_rename(self):
        g = Game()
        self.assertTrue(g.register("Greg", "Westhaven", pid="111").ok)
        self.assertTrue(g.register("Sam", "Kesh", pid="222").ok)
        self.assertFalse(g.register("Greg", "kesh", pid="111").ok)       # taken by someone else
        res = g.register("Greg", "Iron Dominion", pid="111")             # /join again renames
        self.assertTrue(res.ok)
        self.assertEqual(g.get("111").state_name, "Iron Dominion")
        self.assertEqual(g.get("111").marker, "IDO")
        self.assertEqual(len(g.state.players), 2)
        self.assertTrue(g.register("Greg", "Iron Dominion", pid="111").ok)  # same name is fine

    def test_chosen_abbreviations(self):
        g = Game()
        self.assertTrue(g.register("a", "Westhaven", pid="1", abbreviation="wst").ok)
        self.assertEqual(g.get("1").marker, "WST")                       # stored uppercase
        self.assertFalse(g.register("b", "Kesh", pid="2", abbreviation="WST").ok)   # taken
        for bad in ("WS", "WEST", "W5T", "W T", "ÄBC"):
            self.assertFalse(g.register("b", "Kesh", pid="2", abbreviation=bad).ok, bad)
        self.assertNotIn("2", g.state.players)                           # rejected = nothing registered
        self.assertTrue(g.register("b", "Kesh", pid="2").ok)             # blank -> generated
        self.assertEqual(len(g.get("2").marker), 3)
        # Renaming keeps a chosen abbreviation unless a new one is given
        self.assertTrue(g.register("a", "New Westhaven", pid="1").ok)
        self.assertEqual(g.get("1").marker, "NWE")
        self.assertTrue(g.register("a", "New Westhaven", pid="1", abbreviation="NEW").ok)
        self.assertEqual(g.get("1").marker, "NEW")
        self.assertTrue(g.register("a", "New Westhaven", pid="1").ok)    # same name, no abbreviation
        self.assertEqual(g.get("1").marker, "NEW")
        p, _ = g.find("new")
        self.assertEqual(p.id, "1")

    def test_generated_abbreviations_unique_and_three_letters(self):
        g = Game()
        for i, name in enumerate(["The Iron Dominion", "Iron Dominion", "Iron Domain", "Kesh", "K", "Ke",
                                  "Republic of Aldren", "Free Cities of the North", "Ünïcode Land"]):
            self.assertTrue(g.register(f"u{i}", name, pid=str(i)).ok, name)
        marks = [p.marker for p in g.state.players.values()]
        self.assertEqual(len(set(marks)), len(marks))
        self.assertTrue(all(len(m) == 3 and m.isalpha() and m.isascii() for m in marks), marks)

    def test_state_names_reject_discord_markup(self):
        g = Game()
        for bad in ("@everyone", "<@&123>", "`code`", "**Bold**", "#channel"):
            self.assertFalse(g.register("x", bad, pid="1").ok, bad)
        self.assertTrue(g.register("x", "King's Reach", pid="1").ok)

    def test_markers_unique(self):
        g = Game()
        g.register("a", "Veyran Empire")
        g.register("b", "Veyran Elite")
        markers = [p.marker for p in g.state.players.values()]
        self.assertEqual(len(set(markers)), 2)

    def test_minimum_twelve_players(self):
        g = Game()
        for i in range(11):
            g.register(f"u{i}", f"State {i}")
        res = g.start()
        self.assertFalse(res.ok)
        self.assertIn("12", res.message)
        g.register("u11", "State 11")
        self.assertTrue(g.start().ok)

    def test_force_start_for_testing(self):
        g = Game()
        g.register("a", "A State")
        g.register("b", "B State")
        self.assertFalse(g.start().ok)
        self.assertTrue(g.start(force=True).ok)

    def test_need_two_players(self):
        g = Game()
        g.register("a", "Solo")
        self.assertFalse(g.start(force=True).ok)


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.g = make_game(4, size=4)
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
        self.assertEqual(self.g.state.winners, ["p1"])
        self.assertIn("GAME_OVER", [e.kind for e in res.events])

    def test_upgrade_extends_range(self):
        place(self.g, "p3", 2, 1)                            # knight offset from p1
        self.assertFalse(self.g.attack("p1", "p3").ok)
        self.assertTrue(self.g.upgrade("p1").ok)
        self.assertEqual(self.g.get("p1").ap, 3)
        self.assertTrue(self.g.attack("p1", "p3").ok)

    def test_gift(self):
        self.assertTrue(self.g.gift("p1", "p2", 3).ok)
        self.assertEqual(self.g.get("p1").ap, 2)
        self.assertEqual(self.g.get("p2").ap, 8)
        self.assertFalse(self.g.gift("p1", "p2", 3).ok)  # not enough
        self.assertFalse(self.g.gift("p1", "p3", 1).ok)  # out of range
        self.assertFalse(self.g.gift("p1", "p1", 1).ok)

    def test_info_purchase_sees_gifts(self):
        self.g.gift("p1", "p2", 1)
        res = self.g.buy_info("p4")
        self.assertTrue(res.ok)
        self.assertIn("Republic of Aldren gifted Veyran Empire 1 AP", res.message)
        self.assertFalse(self.g.buy_info("p4").ok)           # once per day
        # Later gifts are forwarded to the buyer
        res = self.g.gift("p2", "p1", 1)
        self.assertIn("p4", res.events[0].private)
        # Death ends access immediately
        self.g.get("p4").hp = 1
        place(self.g, "p4", 0, 1)
        self.assertTrue(self.g.attack("p1", "p4").ok)
        self.assertNotIn("p4", self.g.state.info_access)
        res = self.g.gift("p2", "p1", 1)
        self.assertNotIn("p4", res.events[0].private)
        # Access ends with the day
        self.g.buy_info("p3")
        self.g.next_day()
        self.assertEqual(self.g.state.info_access, [])
        self.assertEqual(self.g.state.gifts_today, [])


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
        g = make_game(4, size=4)
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
        self.assertEqual(g.state.winners, ["p1"])
        rows = {r["player"].id: r for r in g.jury_scores()}
        self.assertEqual((rows["p2"]["for_winner"], rows["p2"]["nights"]), (1, 2))
        self.assertEqual((rows["p3"]["for_winner"], rows["p3"]["nights"]), (1, 1))
        self.assertEqual(rows["p4"]["nights"], 0)
        self.assertEqual(g.jury_scores()[0]["player"].id, "p3")   # tie on votes, better rate
        self.assertEqual(len(g.state.jury_history), 4)


class DeclareWinnersTests(unittest.TestCase):
    def make(self, n=16, alive=3):
        g = Game(rng=random.Random(4))
        for i in range(n):
            g.register(f"u{i:02d}", f"State {i:02d}")
        g.start(force=True)
        for p in list(g.state.players.values())[alive:]:
            p.alive, p.hp, p.eliminated_on_day = False, 0, 1
        return g

    def test_admin_declares_joint_winners(self):
        g = self.make(16, 3)
        res = g.declare_winners(["p1", "p3"])
        self.assertTrue(res.ok)
        self.assertEqual(g.state.phase, "ended")
        self.assertEqual(g.winners(), ["p1", "p3"])
        self.assertIn("u00", res.events[0].public)            # winners revealed
        self.assertIn("u02", res.events[0].public)
        self.assertNotIn("u01", res.events[0].public)         # non-winner survivor stays masked
        self.assertFalse(g.move("p2", "n").ok)

    def test_declare_validation(self):
        g = self.make(16, 3)
        self.assertFalse(g.declare_winners([]).ok)
        self.assertFalse(g.declare_winners(["p1", "p1"]).ok)
        self.assertFalse(g.declare_winners(["p1", "p9"]).ok)  # p9 is dead
        self.assertFalse(g.declare_winners(["nobody"]).ok)
        self.assertEqual(g.state.phase, "active")

    def test_jury_scores_count_any_declared_winner(self):
        g = self.make(16, 3)
        g.state.jury_votes = {"p5": "p1", "p6": "p2", "p7": "p3"}
        g.next_day()
        g.declare_winners(["p1", "p3"])
        rows = {r["player"].id: r["for_winner"] for r in g.jury_scores()}
        self.assertEqual((rows["p5"], rows["p6"], rows["p7"]), (1, 0, 1))


class HistoryTests(unittest.TestCase):
    def test_every_call_recorded_including_rejections_and_anon_author(self):
        records = []
        g = Game(rng=random.Random(1), recorder=records.append)
        g.register("greg", "Westhaven")
        g.register("sam", "Kesh")
        g.start(force=True)
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


class JurorSocialTests(unittest.TestCase):
    def test_eliminated_players_keep_social_access(self):
        g = make_game(3)
        g.get("p3").alive, g.get("p3").hp = False, 0
        self.assertTrue(g.say("p3", "The jury remembers.").ok)
        self.assertTrue(g.dm("p3", "p1", "I'm voting for you").ok)
        self.assertTrue(g.dm("p1", "p3", "thanks").ok)       # living can message jurors
        self.assertTrue(g.anon("p3", "psst").ok)
        self.assertFalse(g.dm("p3", "p3", "me").ok)
        self.assertFalse(g.move("p3", "n").ok)               # but still no gameplay


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

    def test_old_save_migrates(self):
        old = make_game(3).state.to_dict()
        old["transfers_today"] = old.pop("gifts_today")
        old["winner"] = old.pop("winners") and None
        old["draw_votes"] = []
        old["settings"]["draw_threshold_pct"] = 25
        st = GameState.from_dict(old)
        self.assertEqual(st.winners, [])
        self.assertEqual(st.gifts_today, [])
        self.assertNotIn("draw_threshold_pct", st.settings)

    def test_bad_schema(self):
        with self.assertRaises(ValueError):
            GameState.from_dict({"schema_version": 999})


if __name__ == "__main__":
    unittest.main()
