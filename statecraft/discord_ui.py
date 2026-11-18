"""Interactive Discord UI: confirmations, DM replies, and the console control panel.

The control panel is a message kept at the bottom of each player's console with an
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
import io
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

import discord

from . import geometry, render_image
from .discord_format import ARROWS, DIRECTION_NAMES, HELP, NO_PINGS, board_message, ping_only, own_status, panel_embed
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
    name = discord.ui.TextInput(label="State name (up to 24 characters)", max_length=24,
                                placeholder="e.g. The Iron Dominion")
    abbreviation = discord.ui.TextInput(label="3-letter abbreviation (shown on the board)", min_length=3,
                                        max_length=3, required=False, placeholder="e.g. IRN (blank = automatic)")

    def __init__(self, ui: "UI", current=None):
        super().__init__()
        self.ui = ui
        if current:                                   # renaming: start from the current values
            self.name.default = current.state_name
            self.abbreviation.default = current.marker

    async def on_submit(self, interaction: discord.Interaction):
        pid, value = str(interaction.user.id), str(self.name.value)
        abbr = str(self.abbreviation.value or "").strip() or None
        res = await self.ui.svc.run(lambda g, now: g.register(interaction.user.display_name, value, pid=pid,
                                                              abbreviation=abbr))
        await self.ui.reply(interaction, res, ephemeral=True)


# --------------------------------------------------------------------- pacts

class PactInviteButton(discord.ui.DynamicItem[discord.ui.Button], template=r"pactinv:(?P<pact>k\d+):(?P<ans>[yn])"):
    """Accept / Decline on a pact invitation (persistent)."""

    def __init__(self, pact: str, ans: str):
        super().__init__(discord.ui.Button(label="Accept" if ans == "y" else "Decline",
                                           style=discord.ButtonStyle.success if ans == "y"
                                           else discord.ButtonStyle.secondary,
                                           custom_id=f"pactinv:{pact}:{ans}"))
        self.pact, self.ans = pact, ans

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["pact"], match["ans"])

    async def callback(self, interaction: discord.Interaction):
        ui: UI = interaction.client.ui  # type: ignore[attr-defined]
        pid = str(interaction.user.id)
        res = await ui.svc.run(lambda g, now: g.respond_to_pact(pid, self.pact, self.ans == "y"))
        await ui.reply(interaction, res)
        if res.ok and interaction.message:
            try:
                await interaction.message.delete()
            except discord.HTTPException:
                pass


class PactCreateModal(discord.ui.Modal, title="Found a pact"):
    name = discord.ui.TextInput(label="Pact name (up to 40 characters)", max_length=40,
                                placeholder="e.g. The Iron Accord")

    def __init__(self, ui: "UI"):
        super().__init__()
        self.ui = ui

    async def on_submit(self, interaction: discord.Interaction):
        pid, value = str(interaction.user.id), str(self.name.value)
        await self.ui.reply(interaction, await self.ui.svc.run(lambda g, now: g.create_pact(pid, value)),
                            ephemeral=True)


def pact_summary(game: Game, pid: str) -> str:
    mine = game.pacts_of(pid)
    lines = [f"**Your pacts** ({len(mine)}/{game.s['pact_member_limit']}; founded "
             f"{sum(1 for pc in game.state.pacts.values() if pc['original_founder'] == pid)}"
             f"/{game.s['pact_found_limit']}):"]
    for pc in mine:
        members = ", ".join(game.get(m).state_name + (" ★" if m == pc["founder"] else "") for m in pc["members"])
        lines.append(f"• **{pc['name']}**: {members}")
    if not mine:
        lines.append("• none yet")
    invites = [pc for pc in game.state.pacts.values() if pc["active"] and pid in pc["invites"]]
    if invites:
        lines.append("**Pending invitations** (accept them from the invitation message in your console):")
        lines += [f"• {pc['name']} (from {game.get(pc['founder']).state_name})" for pc in invites]
    lines.append("★ = founder. Only the founder can invite or remove members.")
    return "\n".join(lines)


class PactMenuView(discord.ui.View):
    """Private pact menu opened from the panel."""

    def __init__(self, ui: "UI", pid: str):
        super().__init__(timeout=180)
        self.ui, self.pid = ui, pid
        g = ui.svc.game
        mine = g.pacts_of(pid)
        founded = [pc for pc in mine if pc["founder"] == pid]
        self._button("Found a pact", discord.ButtonStyle.primary, self.create, "🤝")
        self._button("Invite a state", discord.ButtonStyle.secondary, self.invite, "➕", disabled=not founded)
        self._button("Remove a member", discord.ButtonStyle.secondary, self.kick, "➖",
                     disabled=not any(len(pc["members"]) > 1 for pc in founded))
        self._button("Leave a pact", discord.ButtonStyle.danger, self.leave, "🚪", disabled=not mine)

    def _button(self, label, style, cb, emoji, disabled=False):
        b = discord.ui.Button(label=label, style=style, emoji=emoji, disabled=disabled)
        b.callback = cb
        self.add_item(b)

    async def create(self, i: discord.Interaction):
        await i.response.send_modal(PactCreateModal(self.ui))

    def _pick(self, placeholder: str, options: list[discord.SelectOption], then) -> discord.ui.View:
        view = discord.ui.View(timeout=120)
        sel = discord.ui.Select(placeholder=placeholder, options=options[:25])

        async def cb(i: discord.Interaction):
            await then(i, sel.values[0])

        sel.callback = cb
        view.add_item(sel)
        return view

    def _pact_options(self, pacts) -> list[discord.SelectOption]:
        return [discord.SelectOption(label=pc["name"][:100], value=pc["id"],
                                     description=f"{len(pc['members'])} member(s)") for pc in pacts]

    async def invite(self, i: discord.Interaction):
        ui, pid, g = self.ui, self.pid, self.ui.svc.game
        founded = [pc for pc in g.pacts_of(pid) if pc["founder"] == pid]

        async def chose_pact(ci: discord.Interaction, pact_id: str):
            pc = g.pact(pact_id)
            candidates = [p for p in g.state.players.values()
                          if p.id not in pc["members"] and p.id not in pc["invites"]]
            if not candidates:
                await ci.response.edit_message(content="Everyone is already a member or invited.", view=None)
                return
            opts = [discord.SelectOption(label=f"{p.state_name} [{p.marker}]"[:100], value=p.id,
                                         description="alive" if p.alive else "destroyed (jury)")
                    for p in candidates]

            async def chose_state(si: discord.Interaction, tid: str):
                await ui.reply(si, await ui.svc.run(lambda g, now: g.invite_to_pact(pid, pact_id, tid)), edit=True)

            await ci.response.edit_message(content=f"Invite which state to **{pc['name']}**?",
                                           view=self._pick("Choose a state…", opts, chose_state))

        await i.response.edit_message(content="Invite to which pact?",
                                      view=self._pick("Choose a pact…", self._pact_options(founded), chose_pact))

    async def kick(self, i: discord.Interaction):
        ui, pid, g = self.ui, self.pid, self.ui.svc.game
        founded = [pc for pc in g.pacts_of(pid) if pc["founder"] == pid and len(pc["members"]) > 1]

        async def chose_pact(ci: discord.Interaction, pact_id: str):
            pc = g.pact(pact_id)
            opts = [discord.SelectOption(label=g.get(m).state_name[:100], value=m)
                    for m in pc["members"] if m != pid]

            async def chose_member(si: discord.Interaction, tid: str):
                await ui.confirm_action(si, f"Remove **{g.get(tid).state_name}** from **{pc['name']}**? Their "
                                            "copy of the pact channel will be deleted.",
                                        lambda g, now: g.kick_from_pact(pid, pact_id, tid), edit=True)

            await ci.response.edit_message(content=f"Remove whom from **{pc['name']}**?",
                                           view=self._pick("Choose a member…", opts, chose_member))

        await i.response.edit_message(content="Remove a member from which pact?",
                                      view=self._pick("Choose a pact…", self._pact_options(founded), chose_pact))

    async def leave(self, i: discord.Interaction):
        ui, pid, g = self.ui, self.pid, self.ui.svc.game

        async def chose_pact(ci: discord.Interaction, pact_id: str):
            pc = g.pact(pact_id)
            await ui.confirm_action(ci, f"Leave **{pc['name']}**? Your copy of its channel will be deleted, and "
                                        "you'd need a new invitation to rejoin.",
                                    lambda g, now: g.leave_pact(pid, pact_id), edit=True)

        await i.response.edit_message(content="Leave which pact?",
                                      view=self._pick("Choose a pact…", self._pact_options(g.pacts_of(pid)),
                                                      chose_pact))


# -------------------------------------------------------------------- colors

class ColorView(discord.ui.View):
    """Menu of the colors still free (first come, first served)."""

    def __init__(self, ui: "UI", pid: str):
        super().__init__(timeout=180)
        g = ui.svc.game
        mine = g.get(pid).color
        options = [discord.SelectOption(label=c, value=c, default=(c == mine)) for c in g.free_colors(pid)]
        select = discord.ui.Select(placeholder="Choose your State's color…", options=options[:25])

        async def picked(i: discord.Interaction):
            choice = select.values[0]
            res = await ui.svc.run(lambda g, now: g.set_color(pid, choice))
            await ui.reply(i, res, edit=True)

        select.callback = picked
        self.add_item(select)


async def send_color_picker(ui: "UI", i: discord.Interaction, pid: str) -> None:
    g = ui.svc.game
    if g.state.phase != "registration":
        await i.response.send_message("Colors are locked once the game has started.", ephemeral=True)
        return
    me = g.get(pid)
    taken = {p.color for p in g.state.players.values() if p.color}
    png = await asyncio.to_thread(render_image.palette_png, taken, me.color)
    await i.response.send_message(
        "Pick a color for your State. It's first come, first served; dimmed colors are taken. You can change it "
        "until the game starts.", file=discord.File(io.BytesIO(png), filename="colors.png"),
        view=ColorView(ui, pid), ephemeral=True)


# --------------------------------------------------------------------- board

class BoardRangeSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"board:range"):
    """'Show Influence for...' under every board image (public and private).
    The answer is always private to whoever picked."""

    def __init__(self, options: list[discord.SelectOption] | None = None):
        super().__init__(discord.ui.Select(custom_id="board:range", placeholder="Show Influence for…",
                                           options=options or [discord.SelectOption(label="(none)", value="none")]))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        ui: UI = interaction.client.ui  # type: ignore[attr-defined]
        values = (interaction.data or {}).get("values") or [None]
        await ui.send_board(interaction, focus=values[0])


def board_view(game: Game) -> discord.ui.View | None:
    alive = sorted(game.alive_players(), key=lambda p: p.state_name.lower())
    if not alive:
        return None
    options = [discord.SelectOption(label=f"{p.state_name}"[:100], value=p.id, description=f"{p.marker} · Influence "
                                    f"{game.range_of(p)} · {game.pos(p)}") for p in alive[:25]]
    view = discord.ui.View(timeout=None)
    view.add_item(BoardRangeSelect(options))
    return view


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
    discord.SelectOption(label="Attack", value="attack", emoji="⚔️", description="1 AP · 1 damage to a State within your Influence"),
    discord.SelectOption(label="Gift", value="gift", emoji="🎁", description="1 AP · give it to a State within your Influence (private)"),
    discord.SelectOption(label="Upgrade Influence", value="upgrade", emoji="📡", description="2 AP · Influence +1"),
    discord.SelectOption(label="Spy", value="buyinfo", emoji="🕵️",
                         description="1 AP · see today's top-secret Gift log until 8:15 PM"),
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
            n = g.s["gift_amount"]
            await ui.confirm_action(i, f"Gift **{n} AP** to **{t.state_name}** [{t.marker}]? Gifts are private. "
                                       + ui.ap_note(pid, n),
                                    lambda g, now: g.gift(pid, tid, now=now), edit=True)
        elif self.kind == "vote":
            await ui.reply(i, await ui.svc.run(lambda g, now: g.vote(pid, tid, now=now)), edit=True)
        elif self.kind == "dm":
            if not (g.get(pid).alive and t.alive):          # a State was destroyed since the menu opened
                await i.response.edit_message(content="Destroyed States can't send or receive private messages.",
                                              view=None)
                return
            await i.response.defer(ephemeral=True, thinking=True)
            thread = await ui.bot.router.open_dm_thread(pid, tid)
            text = (f"Your conversation with **{t.state_name}**: {thread.mention}. Type there; it's relayed to them "
                    "under your state name.") if thread else f"Couldn't open a conversation with {t.state_name}."
            await i.edit_original_response(content=text, view=None)


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

    def resolve(self, q: str, admin: bool = False) -> tuple[str | None, str]:
        """A State id from a typed name/abbreviation. Before the start every
        lookup fails the same way, so typing names can't probe who joined."""
        if self.svc.game.state.phase == "registration" and not admin:
            return None, "Other States stay hidden until the game starts."
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

    async def reply(self, i: discord.Interaction, res: Result, ephemeral: bool = True, edit: bool = False):
        text = (res.message if res.ok else f"Rejected: {res.message}")[:2000]
        if edit:
            await i.response.edit_message(content=text, view=None, embed=None)
        else:
            await i.response.send_message(text, ephemeral=ephemeral, allowed_mentions=NO_PINGS)
        await self.bot.router.deliver(res.events)
        self.schedule_refresh()

    async def ask(self, i: discord.Interaction, prompt: str, on_yes, ephemeral: bool = True, edit: bool = False):
        view = ConfirmView(i.user.id, on_yes)
        if edit:
            await i.response.edit_message(content=prompt, view=view, embed=None)
        else:
            await i.response.send_message(prompt, view=view, ephemeral=ephemeral, allowed_mentions=NO_PINGS)
        view.expire = lambda text: i.edit_original_response(content=text, view=None)

    async def confirm_action(self, i: discord.Interaction, prompt: str, fn, ephemeral: bool = True,
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

    async def send_board(self, i: discord.Interaction, focus: str | None = None) -> None:
        """Private board image for whoever asked: `focus` state's range tinted,
        their own tile outlined, and red rings on states that can reach them."""
        g = self.svc.game
        if g.state.board_size == 0:
            await i.response.send_message("The board appears when the game starts.", ephemeral=True)
            return
        viewer = str(i.user.id) if g.get(str(i.user.id)) else None
        if focus is not None and not g.get(focus):
            focus = None
        await i.response.defer(ephemeral=True, thinking=True)
        emb, file = await board_message(g, focus=focus, viewer=viewer)
        kwargs = {"embed": emb, "file": file, "ephemeral": True}
        if view := board_view(g):
            kwargs["view"] = view
        await i.followup.send(**kwargs)

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
            if p:
                view.add_item(PanelButton("color", "Pick a color", B.secondary, 0, "🎨"))
            view.add_item(PanelButton("help", "Help", B.secondary, 0))
            return view
        if phase == "active":
            view.add_item(PanelSelect(ACTIONS_ALIVE if p.alive else ACTIONS_JURY))
        view.add_item(PanelButton("status", "Status", B.secondary, 1, "📋"))
        view.add_item(PanelButton("board", "Board", B.secondary, 1, "🗺️"))
        view.add_item(PanelButton("refresh", "Refresh", B.secondary, 1, "🔄"))
        if phase == "active":
            view.add_item(PanelButton("say", "Public chat", B.secondary, 2, "📣"))
            if p.alive:                                 # jurors lose private correspondence
                view.add_item(PanelButton("dm", "Message a state", B.secondary, 2, "✉️"))
            view.add_item(PanelButton("anon", "Anonymous", B.secondary, 2, "🎭"))
        if phase == "active" and p.alive:
            view.add_item(PanelButton("pacts", "Pacts", B.secondary, 3, "🤝"))
        view.add_item(PanelButton("help", "Help", B.secondary, 3, "❔"))
        return view

    def _card(self, pid: str) -> discord.Embed:
        return panel_embed(self.svc.game, pid, self.svc.now(), self.svc.recent(pid))

    async def post_panel(self, pid: str, ping: bool = False) -> None:
        """Post a fresh panel at the bottom of the console, removing the old one.

        Not pinned: phones open a channel scrolled to the bottom, so the
        panel stays under the player's thumb, and pinning would add a
        'pinned a message' notice each time.

        `ping` @mentions the player (a small line above the card) so Discord
        notifies them of new Recent activity. Only they and the referee can
        see their console, so the mention reveals nothing."""
        ch = self.bot.get_channel(self.svc.console_of(pid) or 0)
        if not ch:
            return
        panels = self.svc.server.setdefault("panels", {})
        if old := panels.get(pid):
            try:
                await ch.get_partial_message(old).delete()
            except discord.HTTPException:
                pass
        if ping:
            msg = await ch.send(f"-# <@{pid}> new activity", embed=self._card(pid), view=self.panel_view(pid),
                                allowed_mentions=ping_only(pid))
        else:
            msg = await ch.send(embed=self._card(pid), view=self.panel_view(pid), allowed_mentions=NO_PINGS)
        panels[pid] = msg.id
        self.svc.save_server()

    def schedule_repost(self, pid: str, ping: bool = False) -> None:
        """Move a player's panel back below a new console message (coalesced).
        A burst of notices gives one repost, and one ping if any asked for it."""
        tasks = self.__dict__.setdefault("_repost_tasks", {})
        pings = self.__dict__.setdefault("_repost_pings", set())
        if ping:
            pings.add(pid)
        if (t := tasks.get(pid)) and not t.done():
            return

        async def run():
            await asyncio.sleep(1.5)
            wanted = pid in pings
            pings.discard(pid)
            await self.post_panel(pid, ping=wanted)

        tasks[pid] = asyncio.create_task(run())

    async def refresh_panel(self, pid: str) -> None:
        ch = self.bot.get_channel(self.svc.console_of(pid) or 0)
        mid = self.svc.server.get("panels", {}).get(pid)
        if not ch:
            return
        if not mid:
            await self.post_panel(pid)
            return
        try:
            await ch.get_partial_message(mid).edit(embed=self._card(pid), view=self.panel_view(pid))
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
            await i.response.send_modal(NameModal(self, g.get(pid)))
            return
        if what == "help":
            await i.response.send_message(HELP, ephemeral=True)
            return
        if not g.get(pid):
            await i.response.send_message("Name your state first.", ephemeral=True)
            return
        if what == "refresh":
            await i.response.edit_message(embed=self._card(pid), view=self.panel_view(pid))
        elif what == "status":
            await i.response.send_message(own_status(g, pid, svc.now()), ephemeral=True)
        elif what == "board":
            await self.send_board(i, focus=pid)
        elif what in ("say", "anon"):
            await i.response.send_modal(ChatModal(self, what))
        elif what == "color":
            await send_color_picker(self, i, pid)
        elif what == "pacts":
            await i.response.send_message(pact_summary(g, pid), view=PactMenuView(self, pid), ephemeral=True,
                                          allowed_mentions=NO_PINGS)
        elif what == "dm":
            if not g.get(pid).alive:
                await i.response.send_message("Destroyed States can't send private messages.", ephemeral=True)
                return
            others = [p for p in g.alive_players() if p.id != pid]
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
                await i.response.send_message(f"No States are within your Influence ({g.range_of(me)}). "
                                              "Move closer or upgrade your Influence.", ephemeral=True)
            else:
                verb = "attack (1 AP)" if action == "attack" else "Gift to"
                await i.response.send_message(f"Who do you want to {verb}? Only States within your Influence are listed.",
                                              view=TargetView(self, pid, action, targets), ephemeral=True)
        elif action == "upgrade":
            r = g.range_of(me)
            await self.confirm_action(i, f"Upgrade your Influence from {r} to **{r + 1}**? "
                                         + self.ap_note(pid, g.s["upgrade_cost"]),
                                      lambda g, now: g.upgrade(pid, now=now), ephemeral=True)
        elif action == "buyinfo":
            await self.confirm_action(i, "**Spy**: see the top-secret log of every Gift made today (until 8:15 PM)? "
                                         "Spying is announced publicly. " + self.ap_note(pid, g.s["info_cost"]),
                                      lambda g, now: g.buy_info(pid, now=now), ephemeral=True)
        elif action == "vote":
            await i.response.send_message("Vote for which surviving state? (secret; you can change it until "
                                          "1:45 PM)", view=TargetView(self, pid, "vote", g.alive_players()),
                                          ephemeral=True)
        else:
            await i.response.send_message("Unknown action.", ephemeral=True)
        # Reset the menu's visible selection
        self.schedule_refresh()
