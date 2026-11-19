"""Runs the AI states: replies to messages, scheduled gameplay polls, and the
OpenAI calls. The logic lives in npc.py; this is the async glue.

Two independent paths (AI_PLAYERS_PLAN.md §4):
  on_dm()  -> wait for the player to stop typing -> one API call -> Game.dm
  tick()   -> due poll -> one API call -> Game.perform (+ Game.say)
Conversations can't reach gameplay: _reply() never calls an action.

API calls never hold GameService.lock. Each answer is applied by a fresh
svc.run(), so the engine checks everything again at that moment. Memory
updates are synchronous (no await between reading and writing an entry), so
two answers landing together can't overwrite each other.

Every call, answer, decision and memory change goes into history.jsonl as an
ai_* record, and is mirrored to the referee's AI log threads.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from . import npc, schedule
from .discord_service import GameService
from .models import Event, Result

log = logging.getLogger("statecraft.npc")

DEBOUNCE_SECONDS = 6.0         # wait this long after a player's last message before replying
LIMIT_REASONS = {"player_day": "a player reached the daily message allowance",
                 "per_minute": "a player hit the per-minute message limit",
                 "budget": "the daily chat budget (AI_DAILY_BUDGET_USD) is used up"}


@dataclass
class Completion:
    text: str
    tokens_in: int
    tokens_out: int


class Brain(Protocol):
    async def complete(self, req: npc.Request) -> Completion: ...


class OpenAIBrain:
    """Chat Completions with strict JSON-schema output."""

    def __init__(self, settings: npc.AISettings):
        from openai import AsyncOpenAI           # imported here so tests and AI-less setups don't need it
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.api_key, timeout=45, max_retries=1)

    async def complete(self, req: npc.Request) -> Completion:
        extra = {"reasoning_effort": self.settings.reasoning} if self.settings.reasoning else {}
        r = await self.client.chat.completions.create(
            model=self.settings.model,
            messages=[{"role": "system", "content": req.system}, {"role": "user", "content": req.user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": req.schema_name, "schema": req.schema, "strict": True}},
            max_completion_tokens=req.max_tokens, **extra)
        u = r.usage
        return Completion(r.choices[0].message.content or "", u.prompt_tokens if u else 0,
                          u.completion_tokens if u else 0)


class NPCService:
    def __init__(self, svc: GameService, settings: npc.AISettings, brain: Brain | None,
                 deliver: Callable[[list[Event]], Awaitable[None]],
                 admin_log: Callable[[str], Awaitable[None]],
                 ai_log: Callable[[str, str], Awaitable[None]],
                 characters=npc.CHARACTER_DIR, rng: random.Random | None = None):
        self.svc, self.settings, self.brain = svc, settings, brain
        self.deliver, self.admin_log, self.ai_log = deliver, admin_log, ai_log
        self.characters = characters
        self.rng = rng or random.Random()
        self.memory = npc.Memory(npc.memory_path(svc.cfg.game_file))
        self.wake: dict[tuple[str, str], float] = {}          # (npc, human) -> loop time to reply at
        self.tasks: set[asyncio.Task] = set()
        self.replying: dict[tuple[str, str], asyncio.Task] = {}
        self.debounce = DEBOUNCE_SECONDS
        self.sandbox: Sandbox | None = None              # the referee's practice game (/admin ai try)

    # ------------------------------------------------------------ helpers

    @property
    def enabled(self) -> bool:
        return self.brain is not None and self.settings.enabled

    def character(self, pid: str) -> npc.Character | None:
        entry = self.memory.entry(pid)
        if not entry:
            return None
        try:
            return npc.load_character(entry["character"], self.characters)
        except npc.CharacterError:
            log.exception("character file problem")
            return None

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    def _finished(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("AI task failed", exc_info=task.exception())
            self._spawn(self.admin_log(f"AI task failed: {task.exception()!r}"))

    def _record(self, action: str, pid: str, **data) -> None:
        g = self.svc.game
        self.svc.history.append({"game_time": self.svc.now().isoformat(), "day": g.state.day,
                                 "phase": g.state.phase, "action": action, "ok": True,
                                 "actor": g.who(pid), "args": data})

    async def _send(self, fn) -> Result:
        res = await self.svc.run(fn)
        if res.ok and res.events:
            await self.deliver(res.events)
        return res

    async def _ask(self, pid: str, req: npc.Request) -> tuple[dict | None, Completion | None, str]:
        """One API call, retried once. (parsed JSON, completion, error)."""
        error = ""
        for _ in range(2):
            try:
                done = await self.brain.complete(req)
            except Exception as e:                          # network, rate limit, bad key...
                error = f"{type(e).__name__}: {e}"[:300]
                continue
            npc.record_cost(self.memory, self.svc.game.state.day, req.kind, done.tokens_in, done.tokens_out,
                            self.settings)
            data = npc.parse_json(done.text)
            if data is not None:
                return data, done, ""
            error = "The answer wasn't valid JSON."
        return None, None, error

    # ------------------------------------------------------------ admin

    async def add(self, key: str) -> Result:
        """Register a character as a state (registration only)."""
        try:
            c = npc.load_character(key, self.characters)
        except npc.CharacterError as e:
            return Result.fail(str(e))
        if self.svc.game.get(c.pid):
            return Result.fail(f"{c.character_name} is already in this game as {self.svc.game.get(c.pid).state_name}.")
        res = await self.svc.run(lambda g, now: g.register(c.owner_name, c.state_name, pid=c.pid,
                                                           abbreviation=c.abbreviation or None))
        if not res.ok:
            return res
        self.memory.add(c)
        self.memory.save()
        p = self.svc.game.get(c.pid)
        return Result(True, f"Added **{c.character_name}** as **{p.state_name}** [{p.marker}].")

    async def remove(self, pid: str) -> Result:
        res = await self.svc.run(lambda g, now: g.unregister(pid))
        if res.ok:
            self.memory.remove(pid)
            self.memory.save()
        return res

    def set_paused(self, pid: str, paused: bool) -> Result:
        entry = self.memory.entry(pid)
        if not entry:
            return Result.fail("That isn't an AI state.")
        entry["paused"] = paused
        self.memory.save()
        self._record("ai_pause" if paused else "ai_resume", pid)
        return Result(True, "Paused: it answers with its unavailable message and skips its polls."
                      if paused else "Resumed.")

    def reset(self) -> None:
        """The game was wiped: set the memory aside with it."""
        for task in list(self.tasks):
            task.cancel()
        self.wake.clear()
        self.replying.clear()
        self.memory.archive()

    def status_text(self) -> str:
        g, s = self.svc.game, self.settings
        u = npc.usage(self.memory, g.state.day)
        lines = [f"**AI states** · {'on' if self.enabled else 'OFF (missing ' + ', '.join(s.missing()) + ')'}"
                 f" · model `{s.model or '-'}`",
                 f"Today (day {g.state.day}): chat ${u['chat_cost']:.4f} of ${s.daily_budget:.2f} "
                 f"({u['chat_calls']} calls) · polls ${u['poll_cost']:.4f} of ${s.poll_budget:.2f} "
                 f"({u['poll_calls']} calls)"]
        for pid in self.memory.pids():
            p, entry = g.get(pid), self.memory.entry(pid)
            if not p:
                lines.append(f"- `{pid}`: not in the game")
                continue
            nxt = npc.next_poll(entry)
            state = ("destroyed" if not p.alive else "paused" if entry["paused"]
                     else f"next poll {npc.schedule.fmt(nxt, g.s)}" if nxt else "no polls left today")
            lines.append(f"- **{p.state_name}** [{p.marker}] ({p.owner_name}): {p.ap} AP, {state}, "
                         f"{len(entry['conversations'])} conversations")
        if u["chat"]:
            top = sorted(u["chat"].items(), key=lambda kv: -kv[1])[:5]
            lines.append("Messages to AI states today: " + ", ".join(
                f"{g.get(h).state_name if g.get(h) else h} {n}" for h, n in top))
        return "\n".join(lines)

    def memory_text(self, pid: str) -> str:
        g, entry = self.svc.game, self.memory.entry(pid)
        if not entry:
            return "That isn't an AI state."
        rels = []
        for other, rel in sorted(entry["relationships"].items(), key=lambda kv: -kv[1]["likability"]):
            o = g.get(other)
            rels.append(f"**{o.state_name if o else other}** {rel['likability']}/10: "
                        + ("; ".join(rel["notes"]) or "-"))
        return (f"**{g.get(pid).state_name if g.get(pid) else pid}**\n" + ("\n".join(rels) or "No opinions yet.")
                + "\n**Personal notes:** " + ("; ".join(entry["personal_notes"]) or "-"))

    # ------------------------------------------------------------ System A: conversation

    async def on_dm(self, sender: str, recipient: str, text: str) -> None:
        """A message was delivered to an AI state (already in the history via Game.dm)."""
        g = self.svc.game
        entry = self.memory.entry(recipient)
        if not entry or g.is_npc(sender):
            return
        c = self.character(recipient)
        now = self.svc.now()
        await self.ai_log(recipient, f"📨 **{g.get(sender).state_name}**: {text}")
        block = ("paused" if entry["paused"] else "off" if not self.enabled
                 else npc.chat_block(self.memory, g.state.day, sender, now, self.settings))
        if block:
            if block in LIMIT_REASONS and npc.first_notice(self.memory, g.state.day, block):
                await self.admin_log(f"AI chat limit: {LIMIT_REASONS[block]} "
                                     f"({g.get(sender).state_name}). Unavailable replies are being sent.")
            self.memory.save()
            self._record("ai_unavailable", recipient, to=sender, reason=block)
            await self._send(lambda g, now: g.dm(recipient, sender, c.unavailable_message if c else
                                                 "This state is not receiving messages right now."))
            return
        npc.count_chat_message(self.memory, g.state.day, sender, now)
        npc.add_message(entry, sender, "them", text, now, self.settings.history_messages)
        self.memory.save()
        key = (recipient, sender)
        self.wake[key] = asyncio.get_running_loop().time() + self.debounce
        if key not in self.replying or self.replying[key].done():
            self.replying[key] = self._spawn(self._reply_when_quiet(recipient, sender))

    async def _reply_when_quiet(self, pid: str, other: str) -> None:
        key, loop = (pid, other), asyncio.get_running_loop()
        while True:
            while (delay := self.wake.get(key, 0) - loop.time()) > 0:
                await asyncio.sleep(delay)
            entry = self.memory.entry(pid)
            if not entry or not npc.unanswered(entry, other):
                return
            await self._reply(pid, other)
            entry = self.memory.entry(pid)
            if not entry or not npc.unanswered(entry, other):      # more arrived mid-call? go round again
                return

    async def _reply(self, pid: str, other: str) -> None:
        g, entry, c = self.svc.game, self.memory.entry(pid), self.character(pid)
        if not c or g.dm_problem(pid, other):
            return
        now = self.svc.now()
        if npc.chat_block(self.memory, g.state.day, other, now, self.settings) == "budget":
            self._record("ai_unavailable", pid, to=other, reason="budget")
            npc.add_message(entry, other, "me", c.unavailable_message, now, self.settings.history_messages)
            self.memory.save()
            await self._send(lambda g, now: g.dm(pid, other, c.unavailable_message))
            return
        records = self.svc.history.records(lambda r: r.get("action") in ("say", "anon"), last=60)
        req = npc.chat_request(g, c, entry, other, records, now, self.settings)
        data, done, error = await self._ask(pid, req)
        reply, check = npc.check_chat(data) if data else ("", error)
        entry = self.memory.entry(pid)
        if not entry:                                   # reset while we waited
            return
        if not reply:
            await self.admin_log(f"AI reply failed for {c.state_name} -> {g.get(other).state_name}: {check}")
            self._record("ai_chat_failed", pid, to=other, error=check)
            reply = c.unavailable_message
        changes = npc.apply_memory(g, entry, pid, data)
        npc.add_message(entry, other, "me", reply, self.svc.now(), self.settings.history_messages)
        self.memory.save()
        self._record("ai_chat", pid, to=other, memory=changes,
                     tokens_in=done.tokens_in if done else 0, tokens_out=done.tokens_out if done else 0)
        res = await self._send(lambda g, now: g.dm(pid, other, reply))
        await self.ai_log(pid, f"💬 to **{g.get(other).state_name}**: {reply}"
                          + ("" if res.ok else f"\n-# not delivered: {res.message}")
                          + (f"\n-# 🧠 {'; '.join(changes)}" if changes else ""))

    def on_notice(self, pid: str, text: str) -> None:
        """A private notice to an AI state (attacked, gifted...)."""
        entry = self.memory.entry(pid)
        if entry and not text.startswith("[Info]"):    # spy lines come from the spy report instead
            npc.add_notice(entry, self.svc.game.state.day, text)
            self.memory.save()

    # ------------------------------------------------------------ System B: gameplay polls

    async def tick(self) -> None:
        """Called by the bot's 30-second scheduler loop."""
        g = self.svc.game
        if g.state.phase != "active" or not self.enabled:
            return
        now, changed = self.svc.now(), False
        for pid in self.memory.pids():
            p, entry = g.get(pid), self.memory.entry(pid)
            if not p or not p.alive or entry["paused"]:
                continue
            changed |= npc.ensure_polls(entry, g, now, self.rng)
            due = npc.take_due_poll(entry, now)
            if due:
                changed = True
            if due == "run":
                self._spawn(self.poll(pid))
            elif due == "late":
                self._record("ai_poll_skipped", pid, reason="late (the bot was offline)")
        if changed:
            self.memory.save()

    async def poll(self, pid: str) -> str:
        """One gameplay decision. Returns a one-line summary (for /admin ai poll)."""
        g, entry, c = self.svc.game, self.memory.entry(pid), self.character(pid)
        p = g.get(pid)
        if not entry or not c or not p:
            return "That isn't an AI state in this game."
        now = self.svc.now()
        menu = npc.action_menu(g, pid, now)
        if len(menu) == 1:
            why = g._actor(pid, 0, now)[1] or "no AP to spend"
            self._record("ai_poll", pid, choice="pass", reason=f"nothing to do ({why})", model=False)
            return f"{p.state_name} passed without asking the model: {why}"
        if not self.enabled:
            return "AI states are off (see /admin ai list)."
        if npc.poll_blocked(self.memory, g.state.day, self.settings):
            if npc.first_notice(self.memory, g.state.day, "poll_budget"):
                await self.admin_log("AI poll budget (AI_POLL_BUDGET_USD) is used up for today: AI states pass.")
            self._record("ai_poll", pid, choice="pass", reason="poll budget used up", model=False)
            return f"{p.state_name} passed: the poll budget is used up."
        records = self.svc.history.records(lambda r: r.get("action") in ("say", "anon"), last=60)
        req = npc.poll_request(g, c, entry, menu, records, now, self.settings)
        data, done, error = await self._ask(pid, req)
        action, reason, public, error = npc.check_poll(data, menu) if data else (None, "", "", error)
        entry = self.memory.entry(pid)
        if not entry:
            return "Reset while deciding."
        if action is None:
            self.memory.save()
            self._record("ai_poll_failed", pid, error=error)
            await self.admin_log(f"AI poll failed for {c.state_name}: {error}")
            return f"{c.state_name}: poll failed ({error})."
        changes = npc.apply_memory(g, entry, pid, data)
        self.memory.save()
        desc = next(d for _, a, d in menu if a == action)
        res = await self._send(lambda g, now: g.perform(pid, action, now))
        outcome = res.message if res.ok else f"rejected: {res.message}"
        self._record("ai_poll", pid, choice=action, described=desc, reason=reason, outcome=outcome,
                     memory=changes, tokens_in=done.tokens_in, tokens_out=done.tokens_out)
        posted = ""
        if public and g.get(pid).alive:
            said = await self._send(lambda g, now: g.say(pid, public))
            posted = f"\n📣 {public}" if said.ok else f"\n-# public message not posted: {said.message}"
        await self.ai_log(pid, f"🎲 **{desc}** ({outcome})\n-# {reason}" + posted
                          + (f"\n-# 🧠 {'; '.join(changes)}" if changes else ""))
        return f"{c.state_name} chose: {desc} ({outcome})."


class Sandbox:
    """The referee's private practice game for /admin ai try: the real
    character files and model on a throwaway board, with a test state for the
    referee and its own temporary history and memory. Nothing in it touches
    the live game, its history log, or npcs.json. Lost on restart."""

    TESTER = "sandbox-you"
    TESTER_NAME = "Your Test State"
    FILLERS = ("Republic of Aldren", "Veyran Empire", "Free Cities")
    AI_AP = 3                                  # so polls have choices

    def __init__(self, live: NPCService):
        self.dir = Path(tempfile.mkdtemp(prefix="statecraft-sandbox-"))
        cfg = dataclasses.replace(live.svc.cfg, game_file=self.dir / "game.json",
                                  server_file=self.dir / "server.json")
        self.live = live
        self.svc = GameService(cfg)
        self.svc.now = self.now
        self.out: list[str] = []

        async def note(text: str) -> None:
            self.out.append(text)

        async def ai_note(pid: str, text: str) -> None:
            self.out.append(text)

        async def deliver(events: list[Event]) -> None:
            for e in events:
                if e.public and e.kind not in ("PUBLIC_CHAT", "PRIVATE_MESSAGE"):
                    self.out.append(f"📰 {e.public}")

        self.npcs = NPCService(self.svc, live.settings, live.brain, deliver, note, ai_note,
                               characters=live.characters, rng=live.rng)
        self.npcs.debounce = 0

    def now(self) -> datetime:
        """Real time if the action window is open, otherwise 3:00 PM today, so
        polls always run."""
        real, s = self.live.svc.now(), self.svc.game.s
        if schedule.window_open(real, s):
            return real
        return schedule.at(schedule.local(real, s).date(), s, "window_open").replace(hour=15)

    @classmethod
    async def create(cls, live: NPCService, keys: list[str]) -> tuple["Sandbox", str]:
        sb = cls(live)
        added = []
        for key in keys:
            res = await sb.npcs.add(key)
            if res.ok:
                added.append(key)
        names = {p.state_name.lower() for p in sb.svc.game.state.players.values()}
        await sb.svc.run(lambda g, now: g.register("You", cls.TESTER_NAME, pid=cls.TESTER))
        for n, name in enumerate(cls.FILLERS, start=1):
            if name.lower() not in names:
                await sb.svc.run(lambda g, now, n=n, name=name: g.register(f"Filler {n}", name, pid=f"filler{n}"))
        res = await sb.svc.run(lambda g, now: g.start(force=True, now=now))
        if not res.ok:
            sb.close()
            raise RuntimeError(res.message)
        for pid in sb.npcs.memory.pids():
            sb.svc.game.get(pid).ap = cls.AI_AP
        g = sb.svc.game
        return sb, (f"🧪 New sandbox: {g.state.board_size}x{g.state.board_size} board, AI states start with "
                    f"{cls.AI_AP} AP.\n" + npc.board_text(g, cls.TESTER).replace("YOU, ", "YOU (referee), "))

    def pid_of(self, key: str) -> str | None:
        pid = npc.NPC_PREFIX + key
        return pid if self.npcs.memory.entry(pid) else None

    async def _drain(self) -> None:
        while self.npcs.tasks:
            await asyncio.gather(*list(self.npcs.tasks), return_exceptions=True)

    async def chat(self, pid: str, text: str) -> str:
        self.out = []
        res = await self.svc.run(lambda g, now: g.dm(self.TESTER, pid, text))
        if not res.ok:
            return f"Not delivered: {res.message}"
        await self.npcs.on_dm(self.TESTER, pid, text)
        await self._drain()
        return "\n".join(self.out)

    async def poll(self, pid: str) -> str:
        self.out = []
        p = self.svc.game.get(pid)
        if not p.alive:
            return f"{p.state_name} was destroyed in the sandbox. Use reset:True for a fresh one."
        p.last_action = None                   # no cooldown between practice polls
        topped = p.ap < 2
        if topped:
            p.ap = 2
        summary = await self.npcs.poll(pid)
        lines = self.out or [summary]
        return ("-# (topped up to 2 AP)\n" if topped else "") + "\n".join(lines)

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)
