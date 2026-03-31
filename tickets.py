"""
cogs/tickets.py
Core ticket lifecycle: open, close, reopen, transcript, move, delete.
Topics come from the per-guild DB (seeded from config.py on first use).
Panel button interactions are caught via on_interaction so they survive
restarts without requiring all topic IDs to be known at boot time.
"""

from __future__ import annotations
import asyncio
import io
import re

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.database import (
    add_feedback, add_staff_note, create_ticket, delete_ticket_record,
    get_blacklist_entry, get_guild_topic, get_guild_topics,
    get_next_ticket_number, get_staff_note_ids, get_ticket,
    get_user_open_ticket, init_db, update_first_staff_reply,
    update_ticket_priority, update_ticket_status, update_ticket_topic,
    upsert_category_perm, remove_category_perm,
)
from utils.transcript import generate_transcript


# ══════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════

_CUSTOM_EMOJI_RE = re.compile(r"<(a?):([a-zA-Z0-9_]{1,32}):(\d{17,20})>")


def parse_emoji(s: str | None) -> discord.PartialEmoji | str | None:
    """
    Convert a stored emoji string to something discord.py buttons accept.

    Handles:
      "🎫"                          — plain unicode, returned as-is
      "<:name:123456789012345678>"   — custom emoji → PartialEmoji
      "<a:name:123456789012345678>"  — animated custom emoji → PartialEmoji
    """
    if not s:
        return None
    s = s.strip()
    m = _CUSTOM_EMOJI_RE.match(s)
    if m:
        return discord.PartialEmoji(
            animated=bool(m.group(1)),
            name=m.group(2),
            id=int(m.group(3)),
        )
    return s  # plain unicode — discord.py handles it natively


def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(r.id in config.STAFF_ROLE_IDS for r in member.roles)


def has_required_roles(member: discord.Member, required_roles: list[int]) -> bool:
    if not required_roles:
        return True
    return any(r.id in required_roles for r in member.roles)


def str_to_button_style(s: str) -> discord.ButtonStyle:
    return {
        "primary":   discord.ButtonStyle.primary,
        "success":   discord.ButtonStyle.success,
        "danger":    discord.ButtonStyle.danger,
        "secondary": discord.ButtonStyle.secondary,
    }.get(s, discord.ButtonStyle.primary)


def build_overwrites(guild: discord.Guild, opener: discord.Member | None, topic: dict) -> dict:
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            manage_channels=True, manage_messages=True, read_message_history=True,
        ),
    }
    if opener:
        overwrites[opener] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            read_message_history=True, attach_files=True, embed_links=True,
        )
    for role_id in config.STAFF_ROLE_IDS:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True, manage_messages=True,
            )
    for perm in topic.get("category_permissions", []):
        role = guild.get_role(perm["role_id"])
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=perm.get("view_channel", True),
                send_messages=perm.get("send_messages", True),
                read_message_history=perm.get("read_history", True),
                attach_files=perm.get("attach_files", False),
                embed_links=perm.get("embed_links", False),
                manage_messages=perm.get("manage_messages", False),
            )
    return overwrites


def strip_priority_prefix(name: str) -> str:
    for emoji in config.PRIORITY_EMOJI.values():
        if emoji and name.startswith(emoji + "-"):
            return name[len(emoji) + 1:]
    return name


def _priority_meets_threshold(level: str, threshold: str) -> bool:
    order = config.PRIORITY_ORDER
    try:
        return order.index(level) >= order.index(threshold)
    except ValueError:
        return False


async def log_event(guild, title, description, color=None) -> None:
    color = color or config.TICKET_COLOR
    if not config.LOG_CHANNEL_ID:
        return
    ch = guild.get_channel(config.LOG_CHANNEL_ID)
    if not ch:
        return
    embed = discord.Embed(title=title, description=description, color=color)
    embed.timestamp = discord.utils.utcnow()
    await ch.send(embed=embed)


def _transcript_file(html: str, channel_name: str) -> discord.File:
    return discord.File(
        io.BytesIO(html.encode("utf-8")),
        filename=f"transcript-{channel_name}.html",
    )


async def _send_transcript(interaction, ticket, requested_by) -> None:
    topic_data = {"topic_id": ticket[4], "ticket_number": ticket[5]}
    excluded = await get_staff_note_ids(interaction.channel.id)
    html_content = await generate_transcript(interaction.channel, topic_data, excluded)
    t_embed = discord.Embed(
        title=f"📄  Transcript — #{interaction.channel.name}",
        description=f"Generated by {requested_by.mention}.",
        color=config.CLOSED_COLOR,
    )
    t_embed.add_field(name="Opened by", value=f"<@{ticket[3]}>", inline=True)
    topic = await get_guild_topic(interaction.guild.id, ticket[4])
    if topic:
        t_embed.add_field(name="Topic", value=topic["label"], inline=True)
    await interaction.channel.send(
        embed=t_embed,
        file=_transcript_file(html_content, interaction.channel.name),
    )
    if config.TRANSCRIPT_CHANNEL_ID:
        t_ch = interaction.guild.get_channel(config.TRANSCRIPT_CHANNEL_ID)
        if t_ch:
            await t_ch.send(
                embed=t_embed,
                file=_transcript_file(html_content, interaction.channel.name),
            )


# ══════════════════════════════════════════════════════════════
#  Panel builder  (called by /setup wizard when posting panel)
# ══════════════════════════════════════════════════════════════

def build_panel_view(topics: list[dict]) -> discord.ui.View:
    """
    Public panel: one 'Open a Ticket' gateway button.
    Clicking it sends an ephemeral with only the topics that user can see,
    so role-restricted buttons are completely invisible to ineligible users.
    """
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Open a Ticket",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_panel_menu",
    ))
    return view


def build_topic_selector(topics: list[dict]) -> discord.ui.View:
    """Ephemeral per-user view — only topics the user is eligible for."""
    view = discord.ui.View(timeout=None)
    for topic in topics:
        view.add_item(discord.ui.Button(
            label=topic["label"],
            emoji=parse_emoji(topic.get("emoji")),
            style=str_to_button_style(topic["button_style"]),
            custom_id=f"ticket_open:{topic['id']}",
        ))
    return view


# ══════════════════════════════════════════════════════════════
#  Modals
# ══════════════════════════════════════════════════════════════

class TicketQuestionsModal(discord.ui.Modal):
    def __init__(self, topic: dict) -> None:
        super().__init__(title=f"Open — {topic['label']}"[:45])
        self.topic = topic
        self._inputs: list[discord.ui.TextInput] = []
        for q in topic.get("questions", [])[:5]:
            field = discord.ui.TextInput(
                label=q["label"][:45],
                placeholder=q.get("placeholder", "")[:100],
                required=q.get("required", True),
                max_length=q.get("max_length", 1000),
                style=(
                    discord.TextStyle.paragraph if q.get("long", False)
                    else discord.TextStyle.short
                ),
            )
            self._inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True)
        answers = {f.label: f.value for f in self._inputs}
        await _create_ticket_channel(interaction, self.topic, answers)


class AddUserModal(discord.ui.Modal, title="Add User to Ticket"):
    user_input = discord.ui.TextInput(
        label="User ID",
        placeholder="Numeric user ID e.g. 123456789012345678",
        required=True, max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        raw = self.user_input.value.strip().lstrip("<@!").rstrip(">")
        try:
            uid = int(raw)
            member = interaction.guild.get_member(uid) or await interaction.guild.fetch_member(uid)
        except (ValueError, discord.NotFound):
            return await interaction.response.send_message("❌ Member not found.", ephemeral=True)
        await interaction.channel.set_permissions(
            member, view_channel=True, send_messages=True,
            read_message_history=True, attach_files=True, embed_links=True,
        )
        await interaction.response.send_message(
            embed=discord.Embed(description=f"➕ {member.mention} added.", color=discord.Color.green())
        )


# ══════════════════════════════════════════════════════════════
#  Channel creation
# ══════════════════════════════════════════════════════════════

async def _create_ticket_channel(interaction, topic, answers=None) -> None:
    existing = await get_user_open_ticket(interaction.guild.id, interaction.user.id, topic["id"])
    if existing:
        ch = interaction.guild.get_channel(existing[1])
        mention = ch.mention if ch else "your existing ticket"
        await interaction.followup.send(
            f"⚠️ You already have an open **{topic['label']}** ticket: {mention}", ephemeral=True
        )
        return

    ticket_number = await get_next_ticket_number(interaction.guild.id, topic["id"])
    channel_name  = f"{topic['channel_prefix']}-{ticket_number:04d}"
    category: discord.CategoryChannel | None = None
    if topic.get("category_id"):
        category = interaction.guild.get_channel(topic["category_id"])  # type: ignore

    overwrites = build_overwrites(interaction.guild, interaction.user, topic)
    channel = await interaction.guild.create_text_channel(
        name=channel_name, category=category, overwrites=overwrites,
        topic=f"Ticket #{ticket_number:04d} | {topic['label']} | Opened by {interaction.user} ({interaction.user.id})",
    )
    await create_ticket(channel.id, interaction.guild.id, interaction.user.id, topic["id"], ticket_number)

    embed = discord.Embed(
        title=f"{topic.get('emoji') or '🎫'}  {topic['label']} — Ticket #{ticket_number:04d}",
        description=topic["welcome_message"],
        color=config.TICKET_COLOR,
    )
    embed.add_field(name="Opened by", value=interaction.user.mention, inline=True)
    embed.add_field(name="Topic",     value=topic["label"],           inline=True)
    if answers:
        embed.add_field(name="\u200b", value="**— Provided Information —**", inline=False)
        for question, answer in answers.items():
            embed.add_field(name=question, value=answer or "*(no answer)*", inline=False)
    embed.set_footer(text=f"Ticket #{ticket_number:04d} · Click Close Ticket when resolved.")

    await channel.send(content=interaction.user.mention, embed=embed, view=OpenTicketView())
    await interaction.followup.send(f"✅ Ticket created: {channel.mention}", ephemeral=True)
    await log_event(
        interaction.guild, "🎫 Ticket Opened",
        f"**Channel:** {channel.mention}\n**Opened by:** {interaction.user.mention}\n**Topic:** {topic['label']}",
        color=config.TICKET_COLOR,
    )


# ══════════════════════════════════════════════════════════════
#  Views
# ══════════════════════════════════════════════════════════════

class SetPriorityView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=30)
        options = [
            discord.SelectOption(label="🟢 Low",    value="low"),
            discord.SelectOption(label="🟡 Medium", value="medium"),
            discord.SelectOption(label="🟠 High",   value="high"),
            discord.SelectOption(label="🔴 Urgent", value="urgent"),
            discord.SelectOption(label="⬜ Clear",  value="none"),
        ]
        sel = discord.ui.Select(placeholder="Select priority level…", options=options)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message("<:errado:1488163753961984100> Only staff can set priority.", ephemeral=True)
        level  = interaction.data["values"][0]
        ticket = await get_ticket(interaction.channel.id)
        if not ticket:
            return await interaction.response.edit_message(content="<:errado:1488163753961984100> Not a ticket channel.", view=None)
        await update_ticket_priority(interaction.channel.id, level)
        base = strip_priority_prefix(interaction.channel.name)
        emoji = config.PRIORITY_EMOJI.get(level, "")
        new_name = f"{emoji}-{base}" if emoji else base
        await interaction.channel.edit(name=new_name)
        label_map = {"none": "⬜ None", "low": "🟢 Low", "medium": "🟡 Medium", "high": "🟠 High", "urgent": "🔴 Urgent"}
        await interaction.channel.send(
            embed=discord.Embed(
                description=f"Priority set to **{label_map.get(level, level)}** by {interaction.user.mention}.",
                color=config.PRIORITY_COLOR,
            )
        )
        await interaction.response.edit_message(content=f"<:certo:1488163752036667452> Priority set to **{label_map.get(level, level)}**.", view=None)
        if config.PRIORITY_ALERT_CHANNEL_ID and level != "none" and _priority_meets_threshold(level, config.PRIORITY_ALERT_MIN_LEVEL):
            alert_ch = interaction.guild.get_channel(config.PRIORITY_ALERT_CHANNEL_ID)
            if alert_ch:
                topic = await get_guild_topic(interaction.guild.id, ticket[4])
                a_embed = discord.Embed(
                    title=f"{emoji}  Priority Ticket — {label_map[level]}",
                    description=f"**Channel:** {interaction.channel.mention}\n**Topic:** {topic['label'] if topic else ticket[4]}\n**Opener:** <@{ticket[3]}>\n**Set by:** {interaction.user.mention}",
                    color=config.PRIORITY_COLOR,
                )
                await alert_ch.send(embed=a_embed)


class OpenTicketView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="ticket_close", row=0)
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        ticket = await get_ticket(interaction.channel.id)
        if not ticket:
            return await interaction.response.send_message("<:errado:1488163753961984100> Not a tracked ticket channel.", ephemeral=True)
        opener_id = ticket[3]
        if interaction.user.id != opener_id and not is_staff(interaction.user):
            return await interaction.response.send_message("<:errado:1488163753961984100> Only the ticket opener or staff can close this ticket.", ephemeral=True)
        await interaction.response.defer()
        await update_ticket_status(interaction.channel.id, "closed", interaction.user.id)
        opener = interaction.guild.get_member(opener_id)
        if opener:
            await interaction.channel.set_permissions(opener, view_channel=True, send_messages=False)
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.message.edit(view=self)
        close_embed = discord.Embed(
            title="🔒  Ticket Closed",
            description=f"Closed by {interaction.user.mention}.\n\n**Staff:** use the panel below to reopen, transcript, move, or delete.",
            color=config.CLOSED_COLOR,
        )
        await interaction.channel.send(embed=close_embed, view=ClosedTicketView())
        if opener:
            try:
                dm_embed = discord.Embed(
                    title="💬  How was your support experience?",
                    description=f"Your ticket **#{ticket[5]:04d}** in **{interaction.guild.name}** has been closed.\n\nRate your experience below.",
                    color=config.TICKET_COLOR,
                )
                await opener.send(embed=dm_embed, view=_make_feedback_view(interaction.guild.id, interaction.channel.id))
            except discord.Forbidden:
                pass
        await log_event(
            interaction.guild, "🔒 Ticket Closed",
            f"**Channel:** {interaction.channel.mention}\n**Closed by:** {interaction.user.mention}\n**Opener:** <@{opener_id}>",
            color=config.CLOSED_COLOR,
        )

    @discord.ui.button(label="Add User", emoji="➕", style=discord.ButtonStyle.secondary, custom_id="ticket_add_user", row=0)
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message("<:errado:1488163753961984100> Only staff can add users.", ephemeral=True)
        await interaction.response.send_modal(AddUserModal())

    @discord.ui.button(label="Set Priority", emoji="🚦", style=discord.ButtonStyle.secondary, custom_id="ticket_set_priority", row=0)
    async def set_priority(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message("<:errado:1488163753961984100> Only staff can set priority.", ephemeral=True)
        await interaction.response.send_message("🚦 Select priority:", view=SetPriorityView(), ephemeral=True)


def _make_feedback_view(guild_id: int, channel_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for i, label in enumerate(["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"], start=1):
        btn = discord.ui.Button(
            label=label, style=discord.ButtonStyle.secondary,
            custom_id=f"fb:{guild_id}:{channel_id}:{i}",
        )
        view.add_item(btn)
    return view


class MoveCategoryView(discord.ui.View):
    def __init__(self, guild_id: int, current_topic_id: str, topics: list[dict]) -> None:
        super().__init__(timeout=60)
        self.guild_id = guild_id
        options = [
            discord.SelectOption(
                label=t["label"], value=t["id"],
                emoji=t.get("emoji") or None,
                description=f"Move to {t['channel_prefix']} category",
            )
            for t in topics if t["id"] != current_topic_id
        ]
        if not options:
            options = [discord.SelectOption(label="(No other topics)", value="__none__")]
        sel = discord.ui.Select(placeholder="Select new topic / category…", options=options)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        new_topic_id = interaction.data["values"][0]
        if new_topic_id == "__none__":
            return await interaction.response.edit_message(content="<:errado:1488163753961984100> No other topics available.", view=None)
        new_topic = await get_guild_topic(self.guild_id, new_topic_id)
        if not new_topic:
            return await interaction.response.edit_message(content="<:errado:1488163753961984100> Invalid topic.", view=None)
        new_category: discord.CategoryChannel | None = None
        if new_topic.get("category_id"):
            new_category = interaction.guild.get_channel(new_topic["category_id"])  # type: ignore
        if not new_category:
            return await interaction.response.edit_message(
                content="<:errado:1488163753961984100> Target category not found. Set it in `/setup`.", view=None
            )
        ticket = await get_ticket(interaction.channel.id)
        opener_id = ticket[3] if ticket else None
        opener    = interaction.guild.get_member(opener_id) if opener_id else None
        await interaction.channel.edit(
            category=new_category,
            overwrites=build_overwrites(interaction.guild, opener, new_topic),
        )
        await update_ticket_topic(interaction.channel.id, new_topic_id)
        await interaction.channel.send(
            embed=discord.Embed(
                description=f"📂 Moved to **{new_topic['label']}** by {interaction.user.mention}.",
                color=config.MOVED_COLOR,
            )
        )
        await interaction.response.edit_message(content=f"✅ Moved to **{new_topic['label']}**.", view=None)
        await log_event(
            interaction.guild, "📂 Ticket Moved",
            f"**Channel:** {interaction.channel.mention}\n**Moved by:** {interaction.user.mention}\n**New topic:** {new_topic['label']}",
            color=config.MOVED_COLOR,
        )


class ClosedTicketView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Reopen", emoji="🔓", style=discord.ButtonStyle.success, custom_id="ticket_reopen", row=0)
    async def reopen_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message("<:errado:1488163753961984100> Only staff can reopen tickets.", ephemeral=True)
        ticket = await get_ticket(interaction.channel.id)
        if not ticket:
            return await interaction.response.send_message("<:errado:1488163753961984100> Could not find ticket data.", ephemeral=True)
        opener_id = ticket[3]
        opener    = interaction.guild.get_member(opener_id)
        if opener:
            await interaction.channel.set_permissions(
                opener, view_channel=True, send_messages=True,
                read_message_history=True, attach_files=True, embed_links=True,
            )
        await update_ticket_status(interaction.channel.id, "open")
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.message.edit(view=self)
        await interaction.channel.send(
            embed=discord.Embed(
                title="🔓  Ticket Reopened",
                description=f"Reopened by {interaction.user.mention}.",
                color=config.REOPENED_COLOR,
            ),
            view=OpenTicketView(),
        )
        await interaction.response.send_message("✅ Ticket reopened.", ephemeral=True)
        await log_event(
            interaction.guild, "🔓 Ticket Reopened",
            f"**Channel:** {interaction.channel.mention}\n**Reopened by:** {interaction.user.mention}\n**Opener:** <@{opener_id}>",
            color=config.REOPENED_COLOR,
        )

    @discord.ui.button(label="Create Transcript", emoji="📄", style=discord.ButtonStyle.primary, custom_id="ticket_transcript", row=0)
    async def create_transcript(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message("<:errado:1488163753961984100> Only staff can generate transcripts.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        ticket = await get_ticket(interaction.channel.id)
        if not ticket:
            return await interaction.followup.send("<:errado:1488163753961984100> Could not find ticket data.", ephemeral=True)
        await _send_transcript(interaction, ticket, interaction.user)
        await interaction.followup.send("✅ Transcript generated.", ephemeral=True)

    @discord.ui.button(label="Move Category", emoji="📂", style=discord.ButtonStyle.secondary, custom_id="ticket_move_category", row=0)
    async def move_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message("<:errado:1488163753961984100> Only staff can move tickets.", ephemeral=True)
        ticket = await get_ticket(interaction.channel.id)
        current_topic_id = ticket[4] if ticket else ""
        topics = await get_guild_topics(interaction.guild.id)
        await interaction.response.send_message(
            "📂 Select the new category:",
            view=MoveCategoryView(interaction.guild.id, current_topic_id, topics),
            ephemeral=True,
        )

    @discord.ui.button(label="Delete Ticket", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="ticket_delete", row=0)
    async def delete_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message("<:errado:1488163753961984100> Only staff can delete tickets.", ephemeral=True)
        ticket = await get_ticket(interaction.channel.id)
        opener_id = ticket[3] if ticket else None
        await interaction.response.send_message("🗑️ Deleting in **5 seconds**…")
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.message.edit(view=self)
        await asyncio.sleep(5)
        await delete_ticket_record(interaction.channel.id)
        await log_event(
            interaction.guild, "🗑️ Ticket Deleted",
            f"**Channel:** #{interaction.channel.name}\n**Deleted by:** {interaction.user.mention}"
            + (f"\n**Opener:** <@{opener_id}>" if opener_id else ""),
            color=config.CLOSED_COLOR,
        )
        await interaction.channel.delete(reason=f"Deleted by {interaction.user}")


# ══════════════════════════════════════════════════════════════
#  Cog
# ══════════════════════════════════════════════════════════════

class TicketsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── on_interaction: panel buttons + feedback DMs ──────────

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")

        # Feedback star buttons (sent via DM)
        if custom_id.startswith("fb:"):
            try:
                _, guild_id_s, channel_id_s, rating_s = custom_id.split(":")
                guild_id   = int(guild_id_s)
                channel_id = int(channel_id_s)
                rating     = int(rating_s)
            except (ValueError, AttributeError):
                return
            recorded = await add_feedback(guild_id, channel_id, interaction.user.id, rating)
            stars = "⭐" * rating
            if recorded:
                await interaction.response.edit_message(
                    content=f"Thanks for your feedback! You rated this ticket **{stars}** ({rating}/5).",
                    embed=None, view=None,
                )
            else:
                await interaction.response.edit_message(
                    content="You've already submitted feedback for this ticket.", embed=None, view=None,
                )
            return

        # Panel gateway button — show personalised ephemeral topic selector
        if custom_id == "ticket_panel_menu":
            await self._handle_panel_menu(interaction)
            return

        # Ephemeral topic buttons  (ticket_open:{topic_id})
        if custom_id.startswith("ticket_open:"):
            topic_id = custom_id.split(":", 1)[1]
            await self._handle_panel_open(interaction, topic_id)

    async def _handle_panel_menu(self, interaction: discord.Interaction) -> None:
        """Send the user an ephemeral with only the topic buttons they can see."""
        # Blacklist check first
        bl = await get_blacklist_entry(interaction.guild.id, interaction.user.id)
        if bl:
            return await interaction.response.send_message(
                f"🚫 You are restricted from opening tickets.\n**Reason:** {bl[3]}",
                ephemeral=True,
            )

        all_topics = await get_guild_topics(interaction.guild.id)

        # Filter: keep topics that have no role requirement OR user has a required role
        visible = [
            t for t in all_topics
            if has_required_roles(interaction.user, t.get("required_roles", []))
        ]

        if not visible:
            return await interaction.response.send_message(
                "<:errado:1488163753961984100> You don't have the required roles to open any ticket types.",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="<:ticket:1488175544205054192>  Open a Ticket",
            description="Select the option that best matches your issue.",
            color=config.TICKET_COLOR,
        )
        await interaction.response.send_message(
            embed=embed,
            view=build_topic_selector(visible),
            ephemeral=True,
        )

    async def _handle_panel_open(self, interaction: discord.Interaction, topic_id: str) -> None:
        topic = await get_guild_topic(interaction.guild.id, topic_id)
        if not topic:
            return await interaction.response.send_message("<:errado:1488163753961984100> This ticket topic is no longer configured.", ephemeral=True)

        # Blacklist check
        bl = await get_blacklist_entry(interaction.guild.id, interaction.user.id)
        if bl:
            return await interaction.response.send_message(
                f"<:incorrect71:1488163783011733545> You are restricted from opening tickets.\n**Reason:** {bl[3]}", ephemeral=True
            )

        # Role gate
        if not has_required_roles(interaction.user, topic.get("required_roles", [])):
            role_mentions = ", ".join(f"<@&{r}>" for r in topic["required_roles"])
            return await interaction.response.send_message(
                f"<:errado:1488163753961984100> You need one of these roles to open a **{topic['label']}** ticket: {role_mentions}",
                ephemeral=True,
            )

        if topic.get("questions"):
            await interaction.response.send_modal(TicketQuestionsModal(topic))
        else:
            await interaction.response.defer(ephemeral=True)
            await _create_ticket_channel(interaction, topic)

    # ── First staff reply tracking ────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        ticket = await get_ticket(message.channel.id)
        if not ticket or ticket[6] != "open" or ticket[11] is not None:
            return
        member = message.guild.get_member(message.author.id)
        if member and is_staff(member) and message.author.id != ticket[3]:
            await update_first_staff_reply(message.channel.id)

    # ── /adduser ──────────────────────────────────────────────

    @app_commands.command(name="adduser", description="Add a member to this ticket. (Staff only)")
    @app_commands.describe(member="Member to add")
    async def adduser(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message("<:errado:1488163753961984100> Only staff can use this command.", ephemeral=True)
        if not await get_ticket(interaction.channel.id):
            return await interaction.response.send_message("<:errado:1488163753961984100> This is not a ticket channel.", ephemeral=True)
        await interaction.channel.set_permissions(
            member, view_channel=True, send_messages=True,
            read_message_history=True, attach_files=True, embed_links=True,
        )
        await interaction.response.send_message(
            embed=discord.Embed(description=f"➕ {member.mention} added.", color=discord.Color.green())
        )

    # ── /removeuser ───────────────────────────────────────────

    @app_commands.command(name="removeuser", description="Remove a member from this ticket. (Staff only)")
    @app_commands.describe(member="Member to remove")
    async def removeuser(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message("<:errado:1488163753961984100> Only staff can use this command.", ephemeral=True)
        ticket = await get_ticket(interaction.channel.id)
        if not ticket:
            return await interaction.response.send_message("<:errado:1488163753961984100> This is not a ticket channel.", ephemeral=True)
        if ticket[3] == member.id:
            return await interaction.response.send_message("<:errado:1488163753961984100> Cannot remove the ticket opener.", ephemeral=True)
        await interaction.channel.set_permissions(member, overwrite=None)
        await interaction.response.send_message(
            embed=discord.Embed(description=f"➖ {member.mention} removed.", color=discord.Color.red())
        )

    # ── /ticketinfo ───────────────────────────────────────────

    @app_commands.command(name="ticketinfo", description="View details about this ticket.")
    async def ticketinfo(self, interaction: discord.Interaction) -> None:
        ticket = await get_ticket(interaction.channel.id)
        if not ticket:
            return await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
        topic  = await get_guild_topic(interaction.guild.id, ticket[4])
        label  = topic["label"] if topic else ticket[4].replace("_", " ").title()
        color  = config.CLOSED_COLOR if ticket[6] == "closed" else config.TICKET_COLOR
        p_level = ticket[10] if len(ticket) > 10 else "none"
        p_emoji = config.PRIORITY_EMOJI.get(p_level, "")
        embed = discord.Embed(title="📋  Ticket Info", color=color)
        embed.add_field(name="Ticket #",  value=f"`{ticket[5]:04d}`",                  inline=True)
        embed.add_field(name="Topic",     value=label,                                 inline=True)
        embed.add_field(name="Status",    value=ticket[6].title(),                     inline=True)
        embed.add_field(name="Priority",  value=f"{p_emoji} {p_level.title()}",        inline=True)
        embed.add_field(name="Opened by", value=f"<@{ticket[3]}>",                    inline=True)
        embed.add_field(name="Opened at", value=str(ticket[7]),                        inline=True)
        if ticket[8]:
            embed.add_field(name="Closed at", value=str(ticket[8]), inline=True)
        if ticket[9]:
            embed.add_field(name="Closed by", value=f"<@{ticket[9]}>", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════════════════
#  Extension entry point
# ══════════════════════════════════════════════════════════════

async def setup(bot: commands.Bot) -> None:
    await init_db()
    # Only persistent views with stable custom_ids need registration
    bot.add_view(OpenTicketView())
    bot.add_view(ClosedTicketView())
    await bot.add_cog(TicketsCog(bot))
