"""Interactive Discord UI: confirmations, DM replies, and the console control panel.

The control panel is a pinned message in each player's console with an
action menu and buttons, built for phones. It is a front end only: every
choice ends in the same engine call (and history record) as the matching
slash command, and anything that spends AP or can't be undone goes through
the same Yes/Cancel confirmation.

Panel components use stable custom ids (DynamicItem), so they keep working
after the bot restarts. Follow-up menus (move grid, target lists) are
short-lived, private (ephemeral) messages.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

import discord

from . import geometry
from .discord_format import ARROWS, DIRECTION_NAMES, HELP, NO_PINGS, board_embed, own_status, panel_embed
from .game import Game
from .models import Result

if TYPE_CHECKING:
    from .discord_bot import StatecraftBot
    from .discord_service import GameService

log = logging.getLogger("statecraft")


# --------------------------------------------------------------- confirmation

class ConfirmView(discord.ui.View):
    """Yes / Cancel. Only the person who started it can answer.
    `on_yes` does the real work and must respond to the interaction."""

    def __init__(self, owner_id: int, on_yes: Callable[[discord.Interaction], Awaitable[None]],
                 timeout: float = 120):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.on_yes = on_yes
        self.expire: Callable[[str], Awaitable] | None = None

    async def _mine(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This isn't your confirmation.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._mine(interaction):
            self.stop()
            await self.on_yes(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._mine(interaction):
            self.stop()
            await interaction.response.edit_message(content="Cancelled. Nothing was spent.", view=None, embed=None)

    async def on_timeout(self):
        if self.expire:
            try:
                await self.expire("Confirmation expired. Nothing was spent.")
            except discord.HTTPException:
                pass


# ------------------------------------------------------------------ DM reply

class ChatModal(discord.ui.Modal):
    """Text box for /say, /dm, /anon, and DM replies."""

    TITLES = {"say": "Post in #public-chat", "anon": "Post anonymously", "dm": "Message"}

    def __init__(self, ui: "UI", kind: str, to_pid: str | None = None, title: str | None = None):
        super().__init__(title=(title or self.TITLES[kind])[:45])
        self.ui, self.kind, self.to_pid = ui, kind, to_pid
        hint = {"say": "Shown under your state name", "anon": "Shown with no name at all",
                "dm": "Only they will see it"}[kind]
        self.body = discord.ui.TextInput(label=hint, style=discord.TextStyle.paragraph, max_length=1500)
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        pid, text, svc = str(interaction.user.id), str(self.body.value), self.ui.svc
        if self.kind == "say":
            fn = lambda g, now: g.say(pid, text)  # noqa: E731
        elif self.kind == "anon":
            fn = lambda g, now: g.anon(pid, text)  # noqa: E731
        else:
            fn = lambda g, now: g.dm(pid, self.to_pid, text)  # noqa: E731
        await self.ui.reply(interaction, await svc.run(fn), ephemeral=True)


class ReplyButton(discord.ui.DynamicItem[discord.ui.Button], template=r"dmreply:(?P<sender>\w+)"):
    """Persistent Reply button on received DMs."""

    def __init__(self, sender: str):
        super().__init__(discord.ui.Button(label="Reply", style=discord.ButtonStyle.primary,
                                           custom_id=f"dmreply:{sender}"))
        self.sender = sender

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["sender"])

    async def callback(self, interaction: discord.Interaction):
        ui: UI = interaction.client.ui  # type: ignore[attr-defined]
        sender = ui.svc.game.get(self.sender)
        if not sender:
            await interaction.response.send_message("That state no longer exists.", ephemeral=True)
            return
        await interaction.response.send_modal(ChatModal(ui, "dm", self.sender, f"Reply to {sender.state_name}"))


class NameModal(discord.ui.Modal, title="Name your state"):
    name = discord.ui.TextInput(label="State name (up to 40 characters)", max_length=40,
                                placeholder="e.g. The Iron Dominion")

    def __init__(self, ui: "UI"):
        super().__init__()
        self.ui = ui

    async def on_submit(self, interaction: discord.Interaction):
        pid, value = str(interaction.user.id), str(self.name.value)
        res = await self.ui.svc.run(lambda g, now: g.register(interaction.user.display_name, value, pid=pid))
        await self.ui.reply(interaction, res, ephemeral=True)


# ---------------------------------------------------------- panel components

class PanelButton(discord.ui.DynamicItem[discord.ui.Button], template=r"panel:b:(?P<what>[a-z_]+)"):
    def __init__(self, what: str, label: str = "", style=discord.ButtonStyle.secondary, row: int | None = None,
                 emoji: str | None = None):
        super().__init__(discord.ui.Button(label=label or what, style=style, row=row, emoji=emoji,
                                           custom_id=f"panel:b:{what}"))
        self.what = what

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["what"])

    async def callback(self, interaction: discord.Interaction):
        await interaction.client.ui.panel_button(interaction, self.what)  # type: ignore[attr-defined]


class PanelSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"panel:act"):
    def __init__(self, options: list[discord.SelectOption] | None = None):
        super().__init__(discord.ui.Select(custom_id="panel:act", placeholder="Choose an action…", row=0,
                                           options=options or [discord.SelectOption(label="(none)", value="none")]))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        values = (interaction.data or {}).get("values") or [None]
        await interaction.client.ui.panel_action(interaction, values[0])  # type: ignore[attr-defined]


ACTIONS_ALIVE = [
    discord.SelectOption(label="Move", value="move", emoji="🧭", description="1 AP · one square"),
    discord.SelectOption(label="Attack", value="attack", emoji="⚔️", description="1 AP · 1 damage to a state in range"),
    discord.SelectOption(label="Gift AP", value="gift", emoji="🎁", description="Give AP to a state in range (private)"),
    discord.SelectOption(label="Upgrade range", value="upgrade", emoji="📡", description="2 AP · range +1"),
    discord.SelectOption(label="Buy info", value="buyinfo", emoji="🔍",
                         description="1 AP · see today's gifts until 8:15 PM"),
]
ACTIONS_JURY = [
    discord.SelectOption(label="Vote", value="vote", emoji="🗳️", description="Secret jury vote (8 PM-1:45 PM)"),
]


# -------------------------------------------------------- follow-up menus

class MoveView(discord.ui.View):
    """3x3 direction grid; blocked or off-board squares are disabled."""

    LAYOUT = [["nw", "n", "ne"], ["w", None, "e"], ["sw", "s", "se"]]

    def __init__(self, ui: "UI", pid: str):
        super().__init__(timeout=120)
        g = ui.svc.game
        p = g.get(pid)
        for r, row in enumerate(self.LAYOUT):
            for d in row:
                if d is None:
                    self.add_item(discord.ui.Button(label=g.pos(p), disabled=True, row=r))
                    continue
                dx, dy = geometry.DIRECTIONS[d]
                x, y = p.x + dx, p.y + dy
                legal = geometry.on_board(x, y, g.state.board_size) and not g.occupant(x, y)
                btn = discord.ui.Button(label=ARROWS[d], row=r, disabled=not legal,
                                        style=discord.ButtonStyle.primary)
                btn.callback = self._make(ui, pid, d, geometry.coord_label(x, y) if legal else "")
                self.add_item(btn)

    @staticmethod
    def _make(ui: "UI", pid: str, d: str, dest: str):
        async def cb(i: discord.Interaction):
            p = ui.svc.game.get(pid)
            await ui.confirm_action(i, f"Move **{DIRECTION_NAMES[d]}** from {ui.svc.game.pos(p)} to **{dest}**? "
                                       + ui.ap_note(pid, ui.svc.game.s["move_cost"]),
                                    lambda g, now: g.move(pid, d, now=now), edit=True)
        return cb


class TargetView(discord.ui.View):
    """Pick a state: for attack, gift, vote, or a DM."""

    def __init__(self, ui: "UI", pid: str, kind: str, targets: list):
        super().__init__(timeout=120)
        self.ui, self.pid, self.kind = ui, pid, kind
        g = ui.svc.game
        me = g.get(pid)
        options = []
        for t in targets[:25]:
            if t.alive and me.alive:
                desc = f"HP {t.hp} · distance {geometry.distance(me.x, me.y, t.x, t.y)}"
            else:
                desc = f"HP {t.hp}" if t.alive else "destroyed"
            options.append(discord.SelectOption(label=f"{t.state_name} [{t.marker}]"[:100], value=t.id,
                                                description=desc))
        select = discord.ui.Select(placeholder="Choose a state…", options=options)
        select.callback = self._picked
        self.select = select
        self.add_item(select)

    async def _picked(self, i: discord.Interaction):
        ui, pid, g = self.ui, self.pid, self.ui.svc.game
        tid = self.select.values[0]
        t = g.get(tid)
        if self.kind == "attack":
            await ui.confirm_action(i, f"Attack **{t.state_name}** [{t.marker}] ({t.hp} HP)? "
                                       + ui.ap_note(pid, g.s["attack_cost"]),
                                    lambda g, now: g.attack(pid, tid, now=now), edit=True)
        elif self.kind == "gift":
            if g.get(pid).ap < 1:   # let the engine reject (and log) it
                await ui.confirm_action(i, "", lambda g, now: g.gift(pid, tid, 1, now=now), edit=True)
                return
            await i.response.edit_message(content=f"How much AP to gift **{t.state_name}**? (You have "
                                                  f"{g.get(pid).ap}.)", view=AmountView(ui, pid, tid))
        elif self.kind == "vote":
            await ui.reply(i, await ui.svc.run(lambda g, now: g.vote(pid, tid, now=now)), edit=True)
        elif self.kind == "dm":
            await i.response.send_modal(ChatModal(ui, "dm", tid, f"Message {t.state_name}"))


class AmountView(discord.ui.View):
    def __init__(self, ui: "UI", pid: str, tid: str):
        super().__init__(timeout=120)
        ap = ui.svc.game.get(pid).ap
        options = [discord.SelectOption(label=f"{n} AP", value=str(n)) for n in range(1, min(ap, 25) + 1)]
        select = discord.ui.Select(placeholder="Amount…", options=options)

        async def picked(i: discord.Interaction):
            n = int(select.values[0])
            t = ui.svc.game.get(tid)
            await ui.confirm_action(i, f"Gift **{n} AP** to **{t.state_name}** [{t.marker}]? " + ui.ap_note(pid, n),
                                    lambda g, now: g.gift(pid, tid, n, now=now), edit=True)

        select.callback = picked
        self.add_item(select)


# ---------------------------------------------------------------- UI helper

class UI:
    """Shared interaction helpers plus the control panel."""

    def __init__(self, bot: "StatecraftBot"):
        self.bot = bot
        self._refresh_task: asyncio.Task | None = None
        self._refresh_again = False

    @property
    def svc(self) -> "GameService":
        return self.bot.svc

    # ---- basics

    def resolve(self, q: str) -> tuple[str | None, str]:
        if q in self.svc.game.state.players:
            return q, ""
        p, err = self.svc.game.find(q)
        return (p.id if p else None), err

    async def console_pid(self, i: discord.Interaction, need_state: bool = True) -> str | None:
        """The caller's id if this is their console; otherwise reply and return None."""
        pid = str(i.user.id)
        cid = self.svc.console_of(pid)
        if not cid:
            await i.response.send_message("You're not in the game. Use `/join` in #register.", ephemeral=True)
            return None
        if i.channel_id != cid:
            await i.response.send_message(f"Use game commands in your console: <#{cid}>", ephemeral=True)
            return None
        if need_state and not self.svc.game.get(pid):
            await i.response.send_message("Name your state first (tap **Name my state** on your panel, or `/name`).",
                                          ephemeral=True)
            return None
        return pid

    async def reply(self, i: discord.Interaction, res: Result, ephemeral: bool = False, edit: bool = False):
        text = (res.message if res.ok else f"Rejected: {res.message}")[:2000]
        if edit:
            await i.response.edit_message(content=text, view=None, embed=None)
        else:
            await i.response.send_message(text, ephemeral=ephemeral, allowed_mentions=NO_PINGS)
        await self.bot.router.deliver(res.events)
        self.schedule_refresh()

    async def ask(self, i: discord.Interaction, prompt: str, on_yes, ephemeral: bool = False, edit: bool = False):
        view = ConfirmView(i.user.id, on_yes)
        if edit:
            await i.response.edit_message(content=prompt, view=view, embed=None)
        else:
            await i.response.send_message(prompt, view=view, ephemeral=ephemeral, allowed_mentions=NO_PINGS)
        view.expire = lambda text: i.edit_original_response(content=text, view=None)

    async def confirm_action(self, i: discord.Interaction, prompt: str, fn, ephemeral: bool = False,
                             edit: bool = False):
        """Ask before an engine call that spends AP or can't be undone.

        The call is first tried on a throwaway copy of the game. If it would
        fail, it's run for real at once: it changes nothing, but the rejected
        attempt is logged. Otherwise the player confirms, and the call runs
        at the moment they click Yes, with the clock and state as of then."""
        svc = self.svc
        async with svc.lock:
            trial = fn(Game(copy.deepcopy(svc.game.state)), svc.now())
        if not trial.ok:
            await self.reply(i, await svc.run(fn), ephemeral=ephemeral, edit=edit)
            return

        async def go(ci: discord.Interaction):
            await self.reply(ci, await svc.run(fn), edit=True)

        await self.ask(i, prompt + "\n**Confirm?**", go, ephemeral=ephemeral, edit=edit)

    def ap_note(self, pid: str, cost: int) -> str:
        p = self.svc.game.get(pid)
        return f"Costs **{cost} AP** (you have {p.ap}) and starts your 15-minute cooldown."

    # ---- panel

    def panel_view(self, pid: str) -> discord.ui.View:
        g = self.svc.game
        p = g.get(pid)
        phase = g.state.phase
        view = discord.ui.View(timeout=None)
        B = discord.ButtonStyle
        if p is None or phase == "registration":
            view.add_item(PanelButton("name", "Rename my state" if p else "Name my state", B.primary, 0, "✏️"))
            view.add_item(PanelButton("help", "Help", B.secondary, 0))
            return view
        if phase == "active":
            view.add_item(PanelSelect(ACTIONS_ALIVE if p.alive else ACTIONS_JURY))
        view.add_item(PanelButton("status", "Status", B.secondary, 1, "📋"))
        view.add_item(PanelButton("board", "Board", B.secondary, 1, "🗺️"))
        view.add_item(PanelButton("refresh", "Refresh", B.secondary, 1, "🔄"))
        if phase == "active":
            view.add_item(PanelButton("say", "Public chat", B.secondary, 2, "📣"))
            view.add_item(PanelButton("dm", "Message a state", B.secondary, 2, "✉️"))
            view.add_item(PanelButton("anon", "Anonymous", B.secondary, 2, "🎭"))
        view.add_item(PanelButton("help", "Help", B.secondary, 3, "❔"))
        return view

    async def post_panel(self, pid: str) -> None:
        """Post a fresh pinned panel (removing the old one)."""
        ch = self.bot.get_channel(self.svc.console_of(pid) or 0)
        if not ch:
            return
        panels = self.svc.server.setdefault("panels", {})
        if old := panels.get(pid):
            try:
                await ch.get_partial_message(old).delete()
            except discord.HTTPException:
                pass
        msg = await ch.send(embed=panel_embed(self.svc.game, pid, self.svc.now()), view=self.panel_view(pid))
        try:
            await msg.pin(reason="Statecraft control panel")
        except discord.HTTPException:
            pass
        panels[pid] = msg.id
        self.svc.save_server()

    async def refresh_panel(self, pid: str) -> None:
        ch = self.bot.get_channel(self.svc.console_of(pid) or 0)
        mid = self.svc.server.get("panels", {}).get(pid)
        if not ch:
            return
        if not mid:
            await self.post_panel(pid)
            return
        try:
            await ch.get_partial_message(mid).edit(embed=panel_embed(self.svc.game, pid, self.svc.now()),
                                                   view=self.panel_view(pid))
        except discord.NotFound:
            await self.post_panel(pid)

    async def refresh_all_panels(self) -> None:
        for pid in list(self.svc.server.get("consoles", {})):
            try:
                await self.refresh_panel(pid)
            except discord.HTTPException:
                log.exception("panel refresh failed for %s", pid)

    def schedule_refresh(self) -> None:
        """Refresh every panel soon, coalescing bursts of changes."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_again = True
            return

        async def run():
            while True:
                self._refresh_again = False
                await asyncio.sleep(1.0)
                await self.refresh_all_panels()
                if not self._refresh_again:
                    break

        self._refresh_task = asyncio.create_task(run())

    # ---- panel callbacks

    async def _panel_owner(self, i: discord.Interaction) -> str | None:
        pid = str(i.user.id)
        if i.channel_id != self.svc.console_of(pid):
            await i.response.send_message("This panel belongs to someone else.", ephemeral=True)
            return None
        return pid

    async def panel_button(self, i: discord.Interaction, what: str) -> None:
        pid = await self._panel_owner(i)
        if not pid:
            return
        g, svc = self.svc.game, self.svc
        if what == "name":
            if g.state.phase != "registration":
                await i.response.send_message("The game has started; names are final.", ephemeral=True)
                return
            await i.response.send_modal(NameModal(self))
            return
        if what == "help":
            await i.response.send_message(HELP, ephemeral=True)
            return
        if not g.get(pid):
            await i.response.send_message("Name your state first.", ephemeral=True)
            return
        if what == "refresh":
            await i.response.edit_message(embed=panel_embed(g, pid, svc.now()), view=self.panel_view(pid))
        elif what == "status":
            await i.response.send_message(own_status(g, pid, svc.now()), ephemeral=True)
        elif what == "board":
            if g.state.board_size == 0:
                await i.response.send_message("The board appears when the game starts.", ephemeral=True)
            else:
                await i.response.send_message(embed=board_embed(g, pid), ephemeral=True)
        elif what in ("say", "anon"):
            await i.response.send_modal(ChatModal(self, what))
        elif what == "dm":
            others = [p for p in g.state.players.values() if p.id != pid]
            await i.response.send_message("Who do you want to message?", view=TargetView(self, pid, "dm", others),
                                          ephemeral=True)
        else:
            await i.response.send_message("Unknown button.", ephemeral=True)

    async def panel_action(self, i: discord.Interaction, action: str | None) -> None:
        pid = await self._panel_owner(i)
        if not pid:
            return
        g = self.svc.game
        me = g.get(pid)
        if not me or g.state.phase != "active":
            await i.response.send_message("No game in progress.", ephemeral=True)
            return
        if action == "move":
            await i.response.send_message("Choose a direction (1 AP):", view=MoveView(self, pid), ephemeral=True)
        elif action in ("attack", "gift"):
            targets = [t for t in g.alive_players() if t.id != pid and g.in_range(me, t)]
            if not targets:
                await i.response.send_message(f"No states are in your range (range {g.range_of(me)}). "
                                              "Move closer or upgrade your range.", ephemeral=True)
            else:
                verb = "attack (1 AP)" if action == "attack" else "gift AP to"
                await i.response.send_message(f"Who do you want to {verb}? Only states in range are listed.",
                                              view=TargetView(self, pid, action, targets), ephemeral=True)
        elif action == "upgrade":
            r = g.range_of(me)
            await self.confirm_action(i, f"Upgrade your range from {r} to **{r + 1}**? "
                                         + self.ap_note(pid, g.s["upgrade_cost"]),
                                      lambda g, now: g.upgrade(pid, now=now), ephemeral=True)
        elif action == "buyinfo":
            await self.confirm_action(i, "Buy information access (today's AP gifts, until 8:15 PM)? The purchase "
                                         "is announced publicly. " + self.ap_note(pid, g.s["info_cost"]),
                                      lambda g, now: g.buy_info(pid, now=now), ephemeral=True)
        elif action == "vote":
            await i.response.send_message("Vote for which surviving state? (secret; you can change it until "
                                          "1:45 PM)", view=TargetView(self, pid, "vote", g.alive_players()),
                                          ephemeral=True)
        else:
            await i.response.send_message("Unknown action.", ephemeral=True)
        # Reset the menu's visible selection
        self.schedule_refresh()
