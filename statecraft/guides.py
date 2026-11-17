"""Content for #rules (the game) and #how-to-play (using the bot).

Numbers come from the game settings, so the guides stay correct if a
setting changes. Each guide is a list of (title, body) sections; the bot
posts each section as one card.
"""

from __future__ import annotations


def _t(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


def rules(s: dict) -> list[tuple[str, str]]:
    open_, close, lock, fade = _t(s["window_open"]), _t(s["window_close"]), _t(s["vote_lock"]), _t(s["info_expire"])
    tz = "Central" if s["timezone"] == "America/Chicago" else s["timezone"]
    r = s["base_range"]
    return [
        ("📜 Statecraft: the rules",
         "Every player secretly runs one **state** on a shared map. Spend **Action Points (AP)** to move, attack, and "
         "trade, make (and break) alliances, and be the **last state standing**.\n\n"
         "Everyone can see the states, but **not who controls them**. A player's identity is revealed only when "
         "their state is destroyed, or when the game ends."),

        ("🗺️ The board",
         f"• The map is a square grid about **three-quarters as wide as the number of players** "
         f"(16 players → 12×12).\n"
         f"• States start on random squares. No two start directly side by side (diagonal neighbours are possible).\n"
         f"• Each state starts with **{s['starting_hp']} HP**, **0 AP**, and **range {r}**.\n"
         f"• A destroyed state stays on the map as a permanent **obstacle**."),

        ("⚡ Action Points (AP)",
         f"• Every surviving state gets **{s['daily_ap']} AP each day at {open_} {tz}**. Your first AP arrives at the "
         f"first {open_} after the game starts.\n"
         "• AP never expires. Save it up or spend it straight away.\n"
         "• **Your AP is secret.** Only you can see it.\n"
         "• The Jury can award bonus AP (see below).\n"
         "• When a state is destroyed, its unspent AP is lost. Nobody inherits it."),

        ("🎯 Actions",
         f"Actions can only be taken **{open_}–{close} {tz}**. After **any** action you wait "
         f"**{s['cooldown_minutes']} minutes** before your next one.\n\n"
         f"**Move**: {s['move_cost']} AP. One square in any of the 8 directions. You can't move off the board or "
         "onto any state, living or destroyed.\n"
         f"**Attack**: {s['attack_cost']} AP. Deal 1 damage to a state in your range. Always hits.\n"
         f"**Upgrade range**: {s['upgrade_cost']} AP. Your range grows by 1, permanently.\n"
         "**Gift AP**: any amount. Give AP to a state in your range. **Gifts are private**: only the giver, the "
         "receiver, and anyone who has bought information that day know.\n"
         f"**Buy information**: {s['info_cost']} AP. See every AP gift made today, and any made later, until "
         f"{fade}. The *purchase* is announced publicly."),

        ("📏 Range",
         "Count the steps to a target: a **straight** step (up, down, left, right) counts **1**, and a "
         "**diagonal** step counts **2**. If the total is no more than your range, the target is in range.\n"
         "```\n"
         "Range 2           Range 3\n"
         "\n"
         "    X                   X\n"
         "  X X X               X X X\n"
         "X X @ X X           X X X X X\n"
         "  X X X           X X X @ X X X\n"
         "    X               X X X X X\n"
         "                      X X X\n"
         "                        X\n"
         "```"
         "Nothing blocks range: you can attack or gift over any state, living or destroyed. "
         "Only movement is blocked."),

        ("💀 Destruction",
         "A state at **0 HP** is destroyed:\n"
         "• Its controller is **revealed** to everyone.\n"
         "• They join the **Jury**.\n"
         "• Their state stays on the map as an obstacle, and its AP is lost.\n"
         "• HP never regenerates."),

        ("🗳️ The Jury",
         f"Destroyed players form the Jury. Each night they help decide who gets ahead.\n"
         f"• Each juror gets **one secret vote** for a surviving state, from **{close} until {lock}** the next day. "
         f"You can change it until the lock.\n"
         f"• At **{open_}**, every full **{s['votes_per_ap']} votes** a state received gives it **+1 AP**.\n"
         "• The vote **totals** are announced. Who voted for whom is never revealed.\n"
         "• Jurors keep full access to chat, private messages, and pacts, and they get their own #jury channel."),

        ("🏆 Winning",
         "• The **last state standing wins**.\n"
         "• Survivors who want to end the game in a **draw** can agree to it among themselves and ask the "
         "**referee**, who can declare joint winners.\n"
         "• Prize details are announced by the referee."),

        ("🕑 The daily schedule",
         f"**{open_}**: jury votes are counted, everyone gets their daily AP, and **actions open**\n"
         f"**{close}**: **actions close** and jury voting opens\n"
         f"**{fade}**: purchased information access ends\n"
         f"**{lock}**: jury votes lock\n"
         f"All times are {tz}. Chatting, private messages, and pacts are open at **all hours**."),

        ("🎭 Secrecy and fair play",
         "• Talk through the bot as your **state**, never as yourself, if you want to stay hidden. How much you "
         "reveal is up to you.\n"
         "• **Everything you send through the bot is recorded**, including anonymous posts and private messages, "
         "for the post-game summary. Nothing sent through the bot stays private forever.\n"
         "• The referee can see everything and settles anything the rules don't cover."),
    ]


def how_to_play(s: dict) -> list[tuple[str, str]]:
    close, fade = _t(s["window_close"]), _t(s["info_expire"])
    return [
        ("👋 How to play on Discord",
         "Everything happens in your **private console**, a channel only you (and the referee) can see. Nobody "
         "else can tell which console, or which state, is yours.\n\n"
         "This guide covers using the bot. For the game itself, read the rules channel."),

        ("1️⃣ Joining",
         "• In **#register**, type `/join`. You'll get a private console channel.\n"
         "• In your console, tap **✏️ Name my state** and pick a name (up to 24 characters) and a 3-letter "
         "**abbreviation** for the map (or leave it blank and one is made for you). You can change both until the "
         "game starts.\n"
         "• Changed your mind? `/leave` before the game starts."),

        ("🎛️ Your control panel",
         "The **control panel** always sits at the **bottom of your console**. On a phone, everything is a tap or "
         "two away.\n"
         "• **The status card**: your AP, HP, range, position, when your cooldown is ready, whether actions are "
         "open, who's in your range, and who can reach you.\n"
         "• **Recent activity**: gifts you received, info alerts, and other notices.\n"
         "• **🔄 Refresh** updates it. If it's ever missing, type `/panel`."),

        ("🎯 Taking actions",
         "1. Open **Choose an action…** on your panel.\n"
         "2. Pick **Move** (tap an arrow), **Attack** or **Gift AP** (only states in your range are listed), "
         "**Upgrade range**, or **Buy info**.\n"
         "3. Check the summary and tap **Yes**. **Nothing happens until you confirm.**\n"
         "Every reply to you is private and disappears when dismissed. If an action isn't possible (not enough AP, "
         "out of range, cooldown, outside hours), it's rejected and costs nothing."),

        ("🗺️ The board",
         "• **#game-board** always shows the current map.\n"
         "• Tap **🗺️ Board** on your panel for **your own view**: your range is shaded, your state has a white "
         "outline, and any state that can reach you has a **red ring**.\n"
         "• Use **Show range for…** under any board to see any state's reach (only you see the result).\n"
         "• On the map, each tile shows a state's **3-letter abbreviation**, its **HP as dots**, and its **range** "
         "in the corner. Destroyed states are grey with an ✕."),

        ("💬 Talking",
         "• **📣 Public chat**: posts in #public-chat under your **state's name**.\n"
         "• **🎭 Anonymous**: posts in #anonymous-chat with **no name at all**.\n"
         "• **✉️ Message a state**: opens a **private conversation thread** with that state inside your console. "
         "Type there like a normal chat. They see your messages under your state name, and you see theirs.\n"
         "• You keep all of these after your state is destroyed."),

        ("🤝 Pacts (group chats)",
         f"Pacts are private group chats for alliances (or plots).\n"
         f"• Tap **🤝 Pacts** to **found** one (up to {s['pact_found_limit']} each). Only the **founder** can invite "
         "or remove members.\n"
         "• Invitations arrive in your console with **Accept** / **Decline** buttons.\n"
         "• Each member gets their own copy of the pact channel. Just type there: your messages appear to the "
         "others under your state name.\n"
         "• You only see messages sent **after you joined**.\n"
         f"• You can be in up to {s['pact_member_limit']} pacts at once. Leave from the same menu."),

        ("🔔 Notifications",
         "• **Attacks, destructions, and big announcements** always ping everyone playing.\n"
         "• Moves, upgrades, and info purchases only ping you if you opt in: `/notify on` (or `/notify off`).\n"
         "• **#action-log** shows everything that happens, in order."),

        ("⌨️ Slash commands (backup)",
         "Everything on the panel also works as a command in your console:\n"
         "`/move` `/attack` `/gift` `/upgrade` `/buyinfo` · `/status` (you, or any state) `/board` `/info` · "
         "`/say` `/anon` `/dm` · `/pact create|invite|leave|kick|list` · `/vote` (jurors) · `/notify` · "
         "`/panel` `/help`\n"
         "Use them **only in your console**. A command typed anywhere else is rejected so it can't give you away.\n"
         f"Tip: actions close at {close}, and purchased information ends at {fade}."),

        ("🔒 Staying hidden",
         "• Don't use other bots or apps in the game server. Their replies could show your real name.\n"
         "• Your console, threads, and pact channels are visible only to you and the referee.\n"
         "• Remember: **everything sent through the bot is recorded** for the post-game summary."),
    ]
