import random
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from statecraft import schedule
from statecraft.game import Game
from statecraft.models import DEFAULT_SETTINGS

CT = ZoneInfo("America/Chicago")
S = DEFAULT_SETTINGS


def ct(y, mo, d, h, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=CT)


def clocked_game(start_at, n=3):
    """A small game started at `start_at`, with p1..p3 placed in range of each other."""
    records = []
    g = Game(rng=random.Random(1), recorder=records.append)
    for i in range(n):
        g.register(f"u{i}", f"State {i}")
    g.start(force=True, now=start_at)
    g.state.board_size = 6
    for i, p in enumerate(g.state.players.values()):
        p.x, p.y = i, 0
    g.records = records
    return g


class ScheduleMathTests(unittest.TestCase):
    def test_window_boundaries(self):
        self.assertFalse(schedule.window_open(ct(2026, 11, 17, 13, 59, 59), S))
        self.assertTrue(schedule.window_open(ct(2026, 11, 17, 14, 0), S))
        self.assertTrue(schedule.window_open(ct(2026, 11, 17, 19, 59, 59), S))
        self.assertFalse(schedule.window_open(ct(2026, 11, 17, 20, 0), S))

    def test_voting_boundaries(self):
        self.assertTrue(schedule.voting_open(ct(2026, 11, 17, 20, 0), S))
        self.assertTrue(schedule.voting_open(ct(2026, 11, 18, 3, 0), S))
        self.assertTrue(schedule.voting_open(ct(2026, 11, 18, 13, 44, 59), S))
        self.assertFalse(schedule.voting_open(ct(2026, 11, 18, 13, 45), S))
        self.assertFalse(schedule.voting_open(ct(2026, 11, 18, 16, 0), S))

    def test_utc_input_is_converted(self):
        utc = ct(2026, 11, 17, 15, 0).astimezone(ZoneInfo("UTC"))
        self.assertTrue(schedule.window_open(utc, S))

    def test_due_events_catch_up_in_order(self):
        ev = schedule.due_events(ct(2026, 11, 17, 13, 0), ct(2026, 11, 19, 14, 0), S)
        self.assertEqual([k for _, k in ev],
                         ["daily_tick", "info_expire", "daily_tick", "info_expire", "daily_tick"])

    def test_dst_change_keeps_local_times(self):
        # US DST ends Sun 2026-11-01: ticks stay at local 2:00 PM across it
        ev = schedule.due_events(ct(2026, 10, 31, 15, 0), ct(2026, 11, 2, 15, 0), S)
        ticks = [w for w, k in ev if k == "daily_tick"]
        self.assertEqual([(t.day, t.hour, t.minute) for t in ticks], [(1, 14, 0), (2, 14, 0)])
        self.assertEqual(ticks[1] - ticks[0], timedelta(hours=24))
        self.assertNotEqual(ticks[0].utcoffset(), ct(2026, 10, 31, 14).utcoffset())


class ClockedActionTests(unittest.TestCase):
    def setUp(self):
        self.g = clocked_game(ct(2026, 11, 17, 10, 0))
        for p in self.g.state.players.values():
            p.ap = 10

    def test_actions_only_in_window(self):
        r = self.g.move("p1", "s", now=ct(2026, 11, 17, 13, 59))
        self.assertFalse(r.ok)
        self.assertIn("next window opens", r.message)
        self.assertTrue(self.g.move("p1", "s", now=ct(2026, 11, 17, 14, 0)).ok)
        self.assertFalse(self.g.upgrade("p2", now=ct(2026, 11, 17, 20, 0)).ok)

    def test_cooldown_boundary_and_shared(self):
        self.assertTrue(self.g.attack("p1", "p2", now=ct(2026, 11, 17, 15, 0)).ok)
        r = self.g.upgrade("p1", now=ct(2026, 11, 17, 15, 14, 59))     # any action shares it
        self.assertFalse(r.ok)
        self.assertIn("1s", r.message)
        self.assertTrue(self.g.upgrade("p1", now=ct(2026, 11, 17, 15, 15)).ok)
        self.assertTrue(self.g.attack("p2", "p1", now=ct(2026, 11, 17, 15, 1)).ok)  # per player

    def test_admin_cooldown_applies_to_everyone_at_once(self):
        self.assertTrue(self.g.attack("p1", "p2", now=ct(2026, 11, 17, 15, 0)).ok)
        self.assertFalse(self.g.upgrade("p1", now=ct(2026, 11, 17, 15, 0, 40)).ok)
        res = self.g.set_cooldown(30)
        self.assertTrue(res.ok)
        self.assertIn("30 seconds", res.message)
        self.assertEqual(res.events[0].public, "The referee set the action cooldown to 30 seconds.")
        self.assertEqual(self.g.records[-1]["action"], "admin_set_cooldown")
        # the cooldown already running shrinks too
        self.assertTrue(self.g.upgrade("p1", now=ct(2026, 11, 17, 15, 0, 40)).ok)
        self.assertFalse(self.g.move("p1", "s", now=ct(2026, 11, 17, 15, 1, 9)).ok)
        self.assertTrue(self.g.move("p1", "s", now=ct(2026, 11, 17, 15, 1, 10)).ok)
        self.assertTrue(self.g.set_cooldown(900).ok)
        self.assertEqual(schedule.fmt_cooldown(self.g.s["cooldown_minutes"]), "15 minutes")
        self.assertFalse(self.g.set_cooldown(-1).ok)

    def test_rejected_action_does_not_start_cooldown(self):
        self.assertFalse(self.g.attack("p1", "p1", now=ct(2026, 11, 17, 15, 0)).ok)
        self.assertFalse(self.g.gift("p1", "p1", now=ct(2026, 11, 17, 15, 0)).ok)
        self.assertIsNone(self.g.get("p1").last_action)
        self.assertTrue(self.g.gift("p1", "p2", now=ct(2026, 11, 17, 15, 1)).ok)

    def test_buy_info_triggers_cooldown_and_shows_fade_time(self):
        r = self.g.buy_info("p1", now=ct(2026, 11, 17, 19, 50))
        self.assertTrue(r.ok)
        self.assertIn("8:15 PM", r.message)
        self.assertFalse(self.g.move("p1", "s", now=ct(2026, 11, 17, 19, 55)).ok)

    def test_history_records_game_time(self):
        self.g.move("p1", "s", now=ct(2026, 11, 17, 14, 30))
        rec = self.g.records[-1]
        self.assertEqual(rec["action"], "move")
        self.assertTrue(rec["game_time"].startswith("2026-11-17T14:30:00"))
        self.assertNotIn("now", rec["args"])


class JuryClockTests(unittest.TestCase):
    def test_vote_lock(self):
        g = clocked_game(ct(2026, 11, 17, 10, 0))
        g.get("p3").alive, g.get("p3").hp = False, 0
        self.assertFalse(g.vote("p3", "p1", now=ct(2026, 11, 17, 16, 0)).ok)   # during the window
        self.assertTrue(g.vote("p3", "p1", now=ct(2026, 11, 17, 20, 0)).ok)
        self.assertTrue(g.vote("p3", "p2", now=ct(2026, 11, 18, 13, 44)).ok)   # can change until lock
        r = g.vote("p3", "p1", now=ct(2026, 11, 18, 13, 45))
        self.assertFalse(r.ok)
        self.assertIn("locked", r.message)
        self.assertEqual(g.state.jury_votes, {"p3": "p2"})


class SchedulerTests(unittest.TestCase):
    def test_first_tick_gives_first_ap(self):
        g = clocked_game(ct(2026, 11, 17, 10, 0))
        g.run_due_events(ct(2026, 11, 17, 13, 59))
        self.assertEqual(g.state.day, 0)
        self.assertEqual(g.get("p1").ap, 0)
        g.run_due_events(ct(2026, 11, 17, 14, 0, 30))
        self.assertEqual((g.state.day, g.get("p1").ap), (1, 1))

    def test_starting_inside_window_waits_for_next_day(self):
        g = clocked_game(ct(2026, 11, 17, 15, 0))
        g.run_due_events(ct(2026, 11, 17, 19, 0))
        self.assertEqual((g.state.day, g.get("p1").ap), (0, 0))
        g.run_due_events(ct(2026, 11, 18, 14, 0))
        self.assertEqual((g.state.day, g.get("p1").ap), (1, 1))

    def test_tick_history_records_scheduled_time(self):
        g = clocked_game(ct(2026, 11, 17, 10, 0))
        g.run_due_events(ct(2026, 11, 17, 14, 0, 40))       # bot polls a bit late
        tick = [r for r in g.records if r["action"] == "next_day"][-1]
        self.assertTrue(tick["game_time"].startswith("2026-11-17T14:00:00"))

    def test_tick_runs_once_even_if_called_repeatedly(self):
        g = clocked_game(ct(2026, 11, 17, 10, 0))
        for minute in range(0, 10):
            g.run_due_events(ct(2026, 11, 17, 14, minute))
        self.assertEqual(g.get("p1").ap, 1)

    def test_restart_does_not_repeat_tick(self):
        from statecraft.models import GameState
        g = clocked_game(ct(2026, 11, 17, 10, 0))
        g.run_due_events(ct(2026, 11, 17, 14, 1))
        reloaded = Game(GameState.from_dict(g.state.to_dict()))
        reloaded.run_due_events(ct(2026, 11, 17, 14, 2))
        self.assertEqual(reloaded.get("p1").ap, 1)

    def test_offline_catch_up(self):
        g = clocked_game(ct(2026, 11, 17, 10, 0))
        res = g.run_due_events(ct(2026, 11, 19, 15, 0))      # bot was down ~2 days
        self.assertEqual((g.state.day, g.get("p1").ap), (3, 3))
        self.assertIn("info_expire", res.message)

    def test_info_fades_at_815(self):
        g = clocked_game(ct(2026, 11, 17, 10, 0))
        g.run_due_events(ct(2026, 11, 17, 14, 0))
        g.get("p1").ap = 5
        g.buy_info("p1", now=ct(2026, 11, 17, 19, 30))
        g.run_due_events(ct(2026, 11, 17, 20, 14))
        self.assertIn("p1", g.state.info_access)
        res = g.run_due_events(ct(2026, 11, 17, 20, 15))
        self.assertEqual(g.state.info_access, [])
        self.assertIn("p1", res.events[0].private)

    def test_jury_tally_at_tick_with_votes_cast_overnight(self):
        g = clocked_game(ct(2026, 11, 17, 10, 0), n=6)
        for pid in ("p4", "p5", "p6"):
            g.get(pid).alive, g.get(pid).hp = False, 0
        g.run_due_events(ct(2026, 11, 17, 14, 0))           # day 1: everyone alive gets 1
        for pid in ("p4", "p5", "p6"):
            self.assertTrue(g.vote(pid, "p2", now=ct(2026, 11, 17, 22, 0)).ok)
        g.run_due_events(ct(2026, 11, 18, 14, 0))
        self.assertEqual(g.get("p2").ap, 3)                 # 1 + 1 daily + 1 jury
        self.assertEqual(g.get("p1").ap, 2)

    def test_nothing_runs_after_game_ends(self):
        g = clocked_game(ct(2026, 11, 17, 10, 0))
        g.declare_winners(["p1"])
        g.run_due_events(ct(2026, 11, 20, 14, 0))
        self.assertEqual(g.state.day, 0)


if __name__ == "__main__":
    unittest.main()
