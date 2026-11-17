"""Daily schedule math. Pure functions over (now, settings); no I/O.

All times come from settings (local to settings["timezone"], America/Chicago
by default). zoneinfo handles daylight saving: "2:00 PM" is always local
2:00 PM, even on DST change days.

Daily cycle (defaults):
  14:00  daily tick: jury tally, then +1 AP to survivors; action window opens
  20:00  action window closes; jury voting opens
  20:15  purchased information access fades
  13:45  jury votes lock (tallied at 14:00)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def tz(s: dict) -> ZoneInfo:
    return ZoneInfo(s["timezone"])


def clock_time(s: dict, key: str) -> time:
    hh, mm = s[key].split(":")
    return time(int(hh), int(mm))


def local(now: datetime, s: dict) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(tz(s))


def at(day: date, s: dict, key: str) -> datetime:
    """The local datetime of a named schedule time on a given date."""
    return datetime.combine(day, clock_time(s, key), tzinfo=tz(s))


def window_open(now: datetime, s: dict) -> bool:
    t = local(now, s).time()
    return clock_time(s, "window_open") <= t < clock_time(s, "window_close")


def voting_open(now: datetime, s: dict) -> bool:
    """Jury voting runs from window close until the vote lock the next day."""
    t = local(now, s).time()
    return t >= clock_time(s, "window_close") or t < clock_time(s, "vote_lock")


def next_at(now: datetime, s: dict, key: str) -> datetime:
    """The next occurrence (strictly after now) of a named schedule time."""
    loc = local(now, s)
    candidate = at(loc.date(), s, key)
    if candidate <= loc:
        candidate = at(loc.date() + timedelta(days=1), s, key)
    return candidate


def info_expiry(now: datetime, s: dict) -> datetime:
    """When information bought at `now` stops being delivered (today's 20:15)."""
    return at(local(now, s).date(), s, "info_expire")


def due_events(after: datetime, until: datetime, s: dict) -> list[tuple[datetime, str]]:
    """Scheduled events with after < when <= until, oldest first.

    Kinds: "daily_tick" (at window_open) and "info_expire" (at info_expire).
    Missed days (the bot was offline) are all returned, in order.
    """
    start, end = local(after, s), local(until, s)
    events = []
    day = start.date()
    while day <= end.date():
        for key, kind in (("window_open", "daily_tick"), ("info_expire", "info_expire")):
            when = at(day, s, key)
            if start < when <= end:
                events.append((when, kind))
        day += timedelta(days=1)
    events.sort(key=lambda e: e[0])
    return events


def fmt(when: datetime, s: dict) -> str:
    """e.g. 'Tue 2:00 PM'"""
    loc = local(when, s)
    return loc.strftime("%a ") + loc.strftime("%I:%M %p").lstrip("0")


def fmt_duration(td: timedelta) -> str:
    secs = max(0, int(td.total_seconds()))
    m, sec = divmod(secs, 60)
    return f"{m}m {sec:02d}s" if m else f"{sec}s"
