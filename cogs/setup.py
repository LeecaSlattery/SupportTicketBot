"""
cogs/setup.py
Interactive /setup wizard — all ephemeral, all in-place edits.

Ephemeral editing rules used throughout:
  • Button callback that wants to redraw the wizard
      → interaction.response.edit_message(embed=..., view=NewView(oi=interaction))
        (the button's own interaction becomes the new "owner" of the ephemeral)
  • Button callback that opens a modal
      → interaction.response.send_modal(SomeModal(oi=self.oi))
        (the SAME oi is forwarded — send_modal consumes the button interaction)
  • Modal on_submit that wants to redraw the wizard
      → await interaction.response.defer()
        await self.oi.edit_original_response(embed=..., view=NewView(oi=self.oi))
  • Sub-view (confirm / role-select) that wants to redraw the wizard
      → await interaction.response.edit_message(...)   # closes the sub-ephemeral
        await self.oi.edit_original_response(...)       # updates the main wizard
"""

from __future__ import annotations
import re
import discord
from discord import app_commands
from discord.ext import commands

import config
from cogs.tickets import build_panel_view, is_staff
from utils.database import (
    add_topic_question, delete_topic_config, delete_topic_question,
    get_guild_topic, get_guild_topics, get_panel_config,
    remove_category_perm, save_panel_config, set_topic_required_roles,
    upsert_category_perm, upsert_topic,
)


# ══════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════

def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")
    return slug[:32] or "topic"


def _perm_flags(perm: dict) -> str:
    flags = []
    if perm.get("view_channel"):    flags.append("view")
    if perm.get("send_messages"):   flags.append("send")
    if perm.get("read_history"):    flags.append("history")
    if perm.get("attach_files"):    flags.append("files")
    if perm.get("embed_links"):     flags.append("embeds")
    if perm.get("manage_messages"): flags.append("manage")
    return ", ".join(flags) or "none"


STYLE_OPTIONS = [
    discord.SelectOption(label="🔵 Primary (Blue)",   value="primary"),
    discord.SelectOption(label="🟢 Success (Green)",  value="success"),
    discord.SelectOption(label="🔴 Danger (Red)",     value="danger"),
    discord.SelectOption(label="⬜ Secondary (Grey)", value="secondary"),
]

PERM_OPTIONS = [
    discord.SelectOption(label="View Channel",    value="view_channel",    description="Can see the ticket channel"),
    discord.SelectOption(label="Send Messages",   value="send_messages",   description="Can send messages"),
    discord.SelectOption(label="Read History",    value="read_history",    description="Can read message history"),
    discord.SelectOption(label="Attach Files",    value="attach_files",    description="Can attach files/images"),
    discord.SelectOption(label="Embed Links",     value="embed_links",     description="Can embed links"),
    discord.SelectOption(label="Manage Messages", value="manage_messages", description="Can delete/pin messages"),
]


# ══════════════════════════════════════════════════════════════
#  Embeds
# ══════════════════════════════════════════════════════════════

def _main_embed(guild: discord.Guild, note: str = "") -> discord.Embed:
    desc = (
        f"**Server:** {guild.name}\n\n"
        "Use the buttons below to configure the ticket panel, manage topics, "
        "or post the panel to this channel.\n\n"
        "Changes take effect immediately — no restart needed."
    )
    if note:
        desc += f"\n\n{note}"
    return discord.Embed(title="⚙️  Ticket Bot Setup", description=desc, color=config.TICKET_COLOR)


def _topics_embed(topics: list[dict]) -> discord.Embed:
    lines = []
    for i, t in enumerate(topics):
        emoji  = t.get("emoji") or "🎫"
        cat    = f"📁 `{t['category_id']}`" if t.get("category_id") else "📁 *No category*"
        nq     = len(t.get("questions", []))
        rr     = t.get("required_roles", [])
        locked = f"🔒 {len(rr)} role{'s' if len(rr) != 1 else ''}" if rr else "🔒 Open to all"
        lines.append(
            f"**{i+1}.** {emoji} **{t['label']}**  `{t['channel_prefix']}-####`  "
            f"{cat}  ❓{nq}  {locked}"
        )
    body = "\n".join(lines) if lines else "*No topics yet — click Add Topic to create one.*"
    return discord.Embed(
        title="🎫  Manage Topics",
        description=f"**{len(topics)}/25** topics configured.\n\n{body}",
        color=config.TICKET_COLOR,
    )


def _topic_edit_embed(topic: dict) -> discord.Embed:
    emoji  = topic.get("emoji") or "🎫"
    cat_id = topic.get("category_id")
    rr     = topic.get("required_roles", [])
    cp     = topic.get("category_permissions", [])
    qs     = topic.get("questions", [])
    wm     = topic.get("welcome_message", "")

    embed = discord.Embed(
        title=f"{emoji}  Editing: {topic['label']}",
        color=config.TICKET_COLOR,
    )
    embed.add_field(name="Topic ID",       value=f"`{topic['id']}`",                            inline=True)
    embed.add_field(name="Channel Prefix", value=f"`{topic['channel_prefix']}-####`",            inline=True)
    embed.add_field(name="Button Style",   value=topic.get("button_style", "primary").title(),   inline=True)
    embed.add_field(name="Category",       value=f"`{cat_id}`" if cat_id else "*Not set*",        inline=True)
    embed.add_field(name="Required Roles", value=", ".join(f"<@&{r}>" for r in rr) or "*None — open to all*", inline=True)
    embed.add_field(name="Questions",      value=f"{len(qs)}/5",                                 inline=True)
    embed.add_field(
        name="Welcome Message",
        value=(wm[:200] + "…") if len(wm) > 200 else (wm or "*Not set*"),
        inline=False,
    )
    if cp:
        embed.add_field(
            name="Channel Access Roles",
            value="\n".join(f"<@&{p['role_id']}> — {_perm_flags(p)}" for p in cp),
            inline=False,
        )
    embed.set_footer(text="Use the buttons below to edit each section.")
    return embed


def _questions_embed(topic: dict) -> discord.Embed:
    qs = topic.get("questions", [])
    lines = [
        f"**{i+1}.** `{q['label']}` — "
        f"{'required' if q['required'] else 'optional'}, "
        f"{'long' if q.get('long') else 'short'}, max {q['max_length']}"
        for i, q in enumerate(qs)
    ]
    body = "\n".join(lines) if lines else "*No questions — users open tickets directly.*"
    return discord.Embed(
        title=f"❓  Questions — {topic['label']}",
        description=(
            f"**{len(qs)}/5** questions configured.\n"
            "These appear in a form before the ticket channel is created.\n\n"
            + body
        ),
        color=config.TICKET_COLOR,
    )


def _access_embed(topic: dict) -> discord.Embed:
    cp = topic.get("category_permissions", [])
    body = (
        "\n".join(f"<@&{p['role_id']}> — {_perm_flags(p)}" for p in cp)
        if cp else "*No extra roles — only opener and global staff roles get access.*"
    )
    return discord.Embed(
        title=f"👥  Channel Access — {topic['label']}",
        description=(
            "These roles get automatic access to this ticket type's channels "
            "in addition to the opener and global staff roles.\n\n" + body
        ),
        color=config.TICKET_COLOR,
    )


# ══════════════════════════════════════════════════════════════
#  Modals
# ══════════════════════════════════════════════════════════════

class PanelConfigModal(discord.ui.Modal, title="Configure Panel Text"):
    panel_title = discord.ui.TextInput(
        label="Panel Title", max_length=100,
        placeholder="📬  Support Tickets",
    )
    panel_desc = discord.ui.TextInput(
        label="Panel Description", style=discord.TextStyle.paragraph,
        max_length=400, placeholder="Click a button below to open a ticket.",
    )

    def __init__(self, guild_id: int, oi: discord.Interaction) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.oi = oi

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await save_panel_config(
            self.guild_id,
            self.panel_title.value.strip(),
            self.panel_desc.value.strip(),
            config.PANEL_COLOR,
        )
        await interaction.response.defer()
        await self.oi.edit_original_response(
            embed=_main_embed(interaction.guild, "✅ Panel text saved."),
            view=SetupMainView(self.guild_id, self.oi),
        )


class AddTopicModal(discord.ui.Modal, title="Add New Topic"):
    label_input  = discord.ui.TextInput(label="Button Label",         max_length=80,  placeholder="e.g. General Support")
    emoji_input  = discord.ui.TextInput(label="Emoji (optional)",     max_length=100, required=False, placeholder="Unicode: 🎫  or  Custom: <:name:123456789012345678>")
    prefix_input = discord.ui.TextInput(label="Channel Name Prefix",  max_length=30,  placeholder="e.g. support  →  support-0001")

    def __init__(self, guild_id: int, oi: discord.Interaction) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.oi = oi

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        label    = self.label_input.value.strip()
        emoji    = self.emoji_input.value.strip() or None
        prefix   = self.prefix_input.value.strip().lower().replace(" ", "-")
        topic_id = _slugify(label)

        existing    = await get_guild_topics(self.guild_id)
        existing_ids = {t["id"] for t in existing}
        base, n = topic_id, 2
        while topic_id in existing_ids:
            topic_id = f"{base}_{n}"; n += 1

        await upsert_topic(self.guild_id, topic_id, label, emoji, "primary",
                           None, prefix, "", len(existing))
        topic = await get_guild_topic(self.guild_id, topic_id)
        await interaction.response.defer()
        await self.oi.edit_original_response(
            embed=_topic_edit_embed(topic),
            view=TopicEditView(self.guild_id, topic_id, self.oi),
        )


class TopicInfoModal(discord.ui.Modal, title="Edit Basic Info"):
    label_input  = discord.ui.TextInput(label="Button Label",        max_length=80)
    emoji_input  = discord.ui.TextInput(label="Emoji (optional)",    max_length=100, required=False, placeholder="Unicode: 🎫  or  Custom: <:name:123456789012345678>")
    prefix_input = discord.ui.TextInput(label="Channel Name Prefix", max_length=30)

    def __init__(self, topic: dict, oi: discord.Interaction) -> None:
        super().__init__()
        self.topic = topic
        self.oi = oi
        self.label_input.default  = topic["label"]
        self.emoji_input.default  = topic.get("emoji") or ""
        self.prefix_input.default = topic["channel_prefix"]

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        label  = self.label_input.value.strip()
        emoji  = self.emoji_input.value.strip() or None
        prefix = self.prefix_input.value.strip().lower().replace(" ", "-")
        await upsert_topic(
            interaction.guild.id, self.topic["id"], label, emoji,
            self.topic["button_style"], self.topic.get("category_id"),
            prefix, self.topic["welcome_message"], self.topic["sort_order"],
        )
        topic = await get_guild_topic(interaction.guild.id, self.topic["id"])
        await interaction.response.defer()
        await self.oi.edit_original_response(
            embed=_topic_edit_embed(topic),
            view=TopicEditView(interaction.guild.id, self.topic["id"], self.oi),
        )


class TopicCategoryModal(discord.ui.Modal, title="Set Category"):
    category_id = discord.ui.TextInput(
        label="Discord Category ID",
        placeholder="Right-click a category → Copy ID  (enable Developer Mode first)",
        max_length=20, required=False,
    )

    def __init__(self, topic: dict, oi: discord.Interaction) -> None:
        super().__init__()
        self.topic = topic
        self.oi = oi
        if topic.get("category_id"):
            self.category_id.default = str(topic["category_id"])

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        raw = self.category_id.value.strip()
        try:
            cat_id = int(raw) if raw else None
        except ValueError:
            return await interaction.response.send_message(
                "❌ Invalid ID — must be a number.", ephemeral=True
            )
        if cat_id:
            ch = interaction.guild.get_channel(cat_id)
            if not ch or not isinstance(ch, discord.CategoryChannel):
                return await interaction.response.send_message(
                    f"❌ No category with ID `{cat_id}` found in this server.\n"
                    "Make sure you copied the **category** ID, not a channel ID.",
                    ephemeral=True,
                )
        await upsert_topic(
            interaction.guild.id, self.topic["id"], self.topic["label"],
            self.topic.get("emoji"), self.topic["button_style"],
            cat_id, self.topic["channel_prefix"],
            self.topic["welcome_message"], self.topic["sort_order"],
        )
        topic = await get_guild_topic(interaction.guild.id, self.topic["id"])
        await interaction.response.defer()
        await self.oi.edit_original_response(
            embed=_topic_edit_embed(topic),
            view=TopicEditView(interaction.guild.id, self.topic["id"], self.oi),
        )


class TopicWelcomeModal(discord.ui.Modal, title="Welcome Message"):
    message = discord.ui.TextInput(
        label="Welcome Message",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        placeholder="Shown inside the ticket when it opens.",
    )

    def __init__(self, topic: dict, oi: discord.Interaction) -> None:
        super().__init__()
        self.topic = topic
        self.oi = oi
        self.message.default = topic.get("welcome_message", "")

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await upsert_topic(
            interaction.guild.id, self.topic["id"], self.topic["label"],
            self.topic.get("emoji"), self.topic["button_style"],
            self.topic.get("category_id"), self.topic["channel_prefix"],
            self.message.value.strip(), self.topic["sort_order"],
        )
        topic = await get_guild_topic(interaction.guild.id, self.topic["id"])
        await interaction.response.defer()
        await self.oi.edit_original_response(
            embed=_topic_edit_embed(topic),
            view=TopicEditView(interaction.guild.id, self.topic["id"], self.oi),
        )


class AddQuestionModal(discord.ui.Modal, title="Add Question"):
    q_label       = discord.ui.TextInput(label="Question Label",                max_length=45,  placeholder="e.g. What do you need help with?")
    q_placeholder = discord.ui.TextInput(label="Placeholder hint (optional)",   max_length=100, required=False, placeholder="e.g. Describe your issue briefly…")
    q_max_length  = discord.ui.TextInput(label="Max answer length  (100–4000)", max_length=4,   required=False, placeholder="1000")
    q_style       = discord.ui.TextInput(label="Answer style: short or long",   max_length=9,   required=False, placeholder="short")
    q_required    = discord.ui.TextInput(label="Required? yes or no",           max_length=3,   required=False, placeholder="yes")

    def __init__(self, guild_id: int, topic_id: str, oi: discord.Interaction) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.topic_id = topic_id
        self.oi = oi

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        label       = self.q_label.value.strip()
        placeholder = self.q_placeholder.value.strip()
        try:
            max_len = max(100, min(4000, int(self.q_max_length.value.strip() or "1000")))
        except ValueError:
            max_len = 1000
        long_ans = self.q_style.value.strip().lower() in ("long", "paragraph", "l")
        required = self.q_required.value.strip().lower() not in ("no", "n", "false", "0")

        await add_topic_question(self.guild_id, self.topic_id, label, placeholder, required, long_ans, max_len)
        topic = await get_guild_topic(self.guild_id, self.topic_id)
        await interaction.response.defer()
        await self.oi.edit_original_response(
            embed=_questions_embed(topic),
            view=QuestionsView(self.guild_id, self.topic_id, self.oi),
        )


# ══════════════════════════════════════════════════════════════
#  Views — Main Menu
# ══════════════════════════════════════════════════════════════

class SetupMainView(discord.ui.View):
    def __init__(self, guild_id: int, oi: discord.Interaction) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.oi = oi

    @discord.ui.button(label="Configure Panel", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def configure_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        modal = PanelConfigModal(self.guild_id, self.oi)
        cfg = await get_panel_config(self.guild_id)
        modal.panel_title.default = cfg["title"]
        modal.panel_desc.default  = cfg["description"]
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Manage Topics", emoji="🎫", style=discord.ButtonStyle.primary, row=0)
    async def manage_topics(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topics = await get_guild_topics(self.guild_id)
        await interaction.response.edit_message(
            embed=_topics_embed(topics),
            view=TopicListView(self.guild_id, interaction),
        )

    @discord.ui.button(label="Post Panel Here", emoji="📤", style=discord.ButtonStyle.success, row=0)
    async def post_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topics = await get_guild_topics(self.guild_id)
        if not topics:
            return await interaction.response.send_message(
                "❌ You need at least one topic before posting the panel.", ephemeral=True
            )
        cfg = await get_panel_config(self.guild_id)
        panel_embed = discord.Embed(
            title=cfg["title"],
            description=cfg["description"],
            color=cfg["color"],
        )
        panel_embed.set_footer(text="Select the topic that best matches your issue.")
        await interaction.channel.send(embed=panel_embed, view=build_panel_view(topics))
        await interaction.response.edit_message(
            embed=_main_embed(interaction.guild, "✅ **Panel posted to this channel!**"),
            view=SetupMainView(self.guild_id, interaction),
        )


# ══════════════════════════════════════════════════════════════
#  Views — Topic List
# ══════════════════════════════════════════════════════════════

class TopicListView(discord.ui.View):
    def __init__(self, guild_id: int, oi: discord.Interaction) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.oi = oi
        self._selected_id: str | None = None

    async def _refresh_select(self) -> None:
        """Re-query topics and rebuild the select. Called at button time."""
        pass  # select is rebuilt per-interaction below

    @discord.ui.select(
        cls=discord.ui.Select,
        placeholder="Select a topic to edit or delete…",
        min_values=0, max_values=1,
        options=[discord.SelectOption(label="(loading…)", value="__placeholder__")],
        row=0,
    )
    async def topic_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        self._selected_id = interaction.data["values"][0] if interaction.data["values"] else None
        await interaction.response.defer()

    @discord.ui.button(label="Add Topic", emoji="➕", style=discord.ButtonStyle.success, row=1)
    async def add_topic(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topics = await get_guild_topics(self.guild_id)
        if len(topics) >= 25:
            return await interaction.response.send_message("❌ Maximum 25 topics per server.", ephemeral=True)
        await interaction.response.send_modal(AddTopicModal(self.guild_id, self.oi))

    @discord.ui.button(label="Edit Selected", emoji="✏️", style=discord.ButtonStyle.primary, row=1)
    async def edit_topic(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self._selected_id or self._selected_id == "__placeholder__":
            return await interaction.response.send_message("❌ Select a topic from the dropdown first.", ephemeral=True)
        topic = await get_guild_topic(self.guild_id, self._selected_id)
        if not topic:
            return await interaction.response.send_message("❌ Topic not found.", ephemeral=True)
        await interaction.response.edit_message(
            embed=_topic_edit_embed(topic),
            view=TopicEditView(self.guild_id, self._selected_id, interaction),
        )

    @discord.ui.button(label="Delete Selected", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def delete_topic(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self._selected_id or self._selected_id == "__placeholder__":
            return await interaction.response.send_message("❌ Select a topic from the dropdown first.", ephemeral=True)
        topics = await get_guild_topics(self.guild_id)
        topic = next((t for t in topics if t["id"] == self._selected_id), None)
        if not topic:
            return await interaction.response.send_message("❌ Topic not found.", ephemeral=True)
        await interaction.response.send_message(
            f"⚠️ Delete **{topic['label']}**? This cannot be undone.",
            view=_ConfirmDeleteView(self.guild_id, self._selected_id, self.oi),
            ephemeral=True,
        )

    @discord.ui.button(label="← Back", style=discord.ButtonStyle.secondary, row=2)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=_main_embed(interaction.guild),
            view=SetupMainView(self.guild_id, interaction),
        )

    async def on_timeout(self) -> None:
        pass  # ephemeral — nothing to do

    async def _rebuild_select(self, interaction: discord.Interaction) -> None:
        """Utility: send updated topic list embed (called after add/delete)."""
        topics = await get_guild_topics(self.guild_id)
        await self.oi.edit_original_response(
            embed=_topics_embed(topics),
            view=TopicListView(self.guild_id, self.oi),
        )


class _ConfirmDeleteView(discord.ui.View):
    def __init__(self, guild_id: int, topic_id: str, oi: discord.Interaction) -> None:
        super().__init__(timeout=30)
        self.guild_id = guild_id
        self.topic_id = topic_id
        self.oi = oi

    @discord.ui.button(label="Yes, Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await delete_topic_config(self.guild_id, self.topic_id)
        topics = await get_guild_topics(self.guild_id)
        await interaction.response.edit_message(content="✅ Topic deleted.", view=None)
        await self.oi.edit_original_response(
            embed=_topics_embed(topics),
            view=TopicListView(self.guild_id, self.oi),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled.", view=None)


# ══════════════════════════════════════════════════════════════
#  Views — Topic Edit
# ══════════════════════════════════════════════════════════════

class TopicEditView(discord.ui.View):
    def __init__(self, guild_id: int, topic_id: str, oi: discord.Interaction) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.topic_id = topic_id
        self.oi = oi

    async def _topic(self, guild_id: int) -> dict | None:
        return await get_guild_topic(guild_id, self.topic_id)

    @discord.ui.button(label="Basic Info",      emoji="📝", style=discord.ButtonStyle.secondary, row=0)
    async def basic_info(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topic = await self._topic(interaction.guild.id)
        if not topic:
            return await interaction.response.send_message("❌ Topic not found.", ephemeral=True)
        await interaction.response.send_modal(TopicInfoModal(topic, self.oi))

    @discord.ui.button(label="Button Style",    emoji="🎨", style=discord.ButtonStyle.secondary, row=0)
    async def button_style(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topic = await self._topic(interaction.guild.id)
        if not topic:
            return await interaction.response.send_message("❌ Topic not found.", ephemeral=True)
        await interaction.response.send_message(
            f"🎨 Choose button style for **{topic['label']}**:",
            view=ButtonStyleView(self.guild_id, topic, self.oi),
            ephemeral=True,
        )

    @discord.ui.button(label="Set Category",    emoji="📁", style=discord.ButtonStyle.secondary, row=0)
    async def set_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topic = await self._topic(interaction.guild.id)
        if not topic:
            return await interaction.response.send_message("❌ Topic not found.", ephemeral=True)
        await interaction.response.send_modal(TopicCategoryModal(topic, self.oi))

    @discord.ui.button(label="Welcome Message", emoji="💬", style=discord.ButtonStyle.secondary, row=1)
    async def welcome_msg(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topic = await self._topic(interaction.guild.id)
        if not topic:
            return await interaction.response.send_message("❌ Topic not found.", ephemeral=True)
        await interaction.response.send_modal(TopicWelcomeModal(topic, self.oi))

    @discord.ui.button(label="Questions",       emoji="❓", style=discord.ButtonStyle.secondary, row=1)
    async def questions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topic = await self._topic(interaction.guild.id)
        if not topic:
            return await interaction.response.send_message("❌ Topic not found.", ephemeral=True)
        await interaction.response.edit_message(
            embed=_questions_embed(topic),
            view=QuestionsView(self.guild_id, self.topic_id, interaction),
        )

    @discord.ui.button(label="Required Roles",  emoji="🔒", style=discord.ButtonStyle.secondary, row=2)
    async def required_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topic = await self._topic(interaction.guild.id)
        if not topic:
            return await interaction.response.send_message("❌ Topic not found.", ephemeral=True)
        await interaction.response.send_message(
            f"🔒 Select roles required to open **{topic['label']}** tickets.\n"
            "Users need **at least one** of the selected roles. "
            "Select nothing and confirm to allow everyone.",
            view=RequiredRolesView(self.guild_id, topic, self.oi),
            ephemeral=True,
        )

    @discord.ui.button(label="Channel Access",  emoji="👥", style=discord.ButtonStyle.secondary, row=2)
    async def channel_access(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topic = await self._topic(interaction.guild.id)
        if not topic:
            return await interaction.response.send_message("❌ Topic not found.", ephemeral=True)
        await interaction.response.edit_message(
            embed=_access_embed(topic),
            view=ChannelAccessView(self.guild_id, self.topic_id, interaction),
        )

    @discord.ui.button(label="← Back to Topics", style=discord.ButtonStyle.primary, row=3)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topics = await get_guild_topics(self.guild_id)
        await interaction.response.edit_message(
            embed=_topics_embed(topics),
            view=TopicListView(self.guild_id, interaction),
        )


# ══════════════════════════════════════════════════════════════
#  Views — Button Style
# ══════════════════════════════════════════════════════════════

class ButtonStyleView(discord.ui.View):
    def __init__(self, guild_id: int, topic: dict, oi: discord.Interaction) -> None:
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.topic    = topic
        self.oi       = oi
        current = topic.get("button_style", "primary")
        opts = [
            discord.SelectOption(label=o.label, value=o.value, default=(o.value == current))
            for o in STYLE_OPTIONS
        ]
        sel = discord.ui.Select(placeholder="Select button style…", options=opts)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        new_style = interaction.data["values"][0]
        await upsert_topic(
            self.guild_id, self.topic["id"], self.topic["label"],
            self.topic.get("emoji"), new_style, self.topic.get("category_id"),
            self.topic["channel_prefix"], self.topic["welcome_message"],
            self.topic["sort_order"],
        )
        topic = await get_guild_topic(self.guild_id, self.topic["id"])
        await interaction.response.edit_message(
            content=f"✅ Style set to **{new_style.title()}**.", view=None
        )
        await self.oi.edit_original_response(
            embed=_topic_edit_embed(topic),
            view=TopicEditView(self.guild_id, self.topic["id"], self.oi),
        )


# ══════════════════════════════════════════════════════════════
#  Views — Questions
# ══════════════════════════════════════════════════════════════

class QuestionsView(discord.ui.View):
    def __init__(self, guild_id: int, topic_id: str, oi: discord.Interaction) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.topic_id = topic_id
        self.oi = oi

    @discord.ui.button(label="Add Question",    emoji="➕", style=discord.ButtonStyle.success,   row=0)
    async def add_question(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topic = await get_guild_topic(self.guild_id, self.topic_id)
        if len(topic.get("questions", [])) >= 5:
            return await interaction.response.send_message("❌ Maximum 5 questions per topic.", ephemeral=True)
        await interaction.response.send_modal(AddQuestionModal(self.guild_id, self.topic_id, self.oi))

    @discord.ui.button(label="Remove Question", emoji="🗑️", style=discord.ButtonStyle.danger,    row=0)
    async def remove_question(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topic = await get_guild_topic(self.guild_id, self.topic_id)
        qs = topic.get("questions", [])
        if not qs:
            return await interaction.response.send_message("❌ No questions to remove.", ephemeral=True)
        await interaction.response.send_message(
            "Select a question to remove:",
            view=RemoveQuestionView(self.guild_id, self.topic_id, qs, self.oi),
            ephemeral=True,
        )

    @discord.ui.button(label="← Back",          style=discord.ButtonStyle.secondary,              row=1)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topic = await get_guild_topic(self.guild_id, self.topic_id)
        await interaction.response.edit_message(
            embed=_topic_edit_embed(topic),
            view=TopicEditView(self.guild_id, self.topic_id, interaction),
        )


class RemoveQuestionView(discord.ui.View):
    def __init__(self, guild_id: int, topic_id: str, questions: list[dict], oi: discord.Interaction) -> None:
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.topic_id = topic_id
        self.oi = oi
        opts = [
            discord.SelectOption(
                label=q["label"][:100],
                value=str(q["id"]),
                description=f"{'required' if q['required'] else 'optional'} · {'long' if q.get('long') else 'short'}",
            )
            for q in questions
        ]
        sel = discord.ui.Select(placeholder="Select question to remove…", options=opts)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        qid = int(interaction.data["values"][0])
        await delete_topic_question(self.guild_id, qid)
        topic = await get_guild_topic(self.guild_id, self.topic_id)
        await interaction.response.edit_message(content="✅ Question removed.", view=None)
        await self.oi.edit_original_response(
            embed=_questions_embed(topic),
            view=QuestionsView(self.guild_id, self.topic_id, self.oi),
        )


# ══════════════════════════════════════════════════════════════
#  Views — Required Roles
# ══════════════════════════════════════════════════════════════

class RequiredRolesView(discord.ui.View):
    def __init__(self, guild_id: int, topic: dict, oi: discord.Interaction) -> None:
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.topic    = topic
        self.oi       = oi
        sel = discord.ui.RoleSelect(
            placeholder="Select roles (leave empty → everyone can open)…",
            min_values=0, max_values=25,
        )
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        role_ids = [int(v) for v in interaction.data.get("values", [])]
        await set_topic_required_roles(self.guild_id, self.topic["id"], role_ids)
        topic = await get_guild_topic(self.guild_id, self.topic["id"])
        n = len(role_ids)
        await interaction.response.edit_message(
            content=f"✅ Required roles updated — {n} role{'s' if n != 1 else ''} set.",
            view=None,
        )
        await self.oi.edit_original_response(
            embed=_topic_edit_embed(topic),
            view=TopicEditView(self.guild_id, self.topic["id"], self.oi),
        )


# ══════════════════════════════════════════════════════════════
#  Views — Channel Access
# ══════════════════════════════════════════════════════════════

class ChannelAccessView(discord.ui.View):
    def __init__(self, guild_id: int, topic_id: str, oi: discord.Interaction) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.topic_id = topic_id
        self.oi = oi

    @discord.ui.button(label="Add Role",    emoji="➕", style=discord.ButtonStyle.success,   row=0)
    async def add_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "👥 **Step 1 of 2** — Select the role to grant access:",
            view=AddAccessStep1(self.guild_id, self.topic_id, self.oi),
            ephemeral=True,
        )

    @discord.ui.button(label="Remove Role", emoji="➖", style=discord.ButtonStyle.danger,    row=0)
    async def remove_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topic = await get_guild_topic(self.guild_id, self.topic_id)
        cp = topic.get("category_permissions", [])
        if not cp:
            return await interaction.response.send_message("❌ No roles to remove.", ephemeral=True)
        await interaction.response.send_message(
            "Select a role to remove:",
            view=RemoveAccessView(self.guild_id, self.topic_id, cp, self.oi),
            ephemeral=True,
        )

    @discord.ui.button(label="← Back",     style=discord.ButtonStyle.secondary,              row=1)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        topic = await get_guild_topic(self.guild_id, self.topic_id)
        await interaction.response.edit_message(
            embed=_topic_edit_embed(topic),
            view=TopicEditView(self.guild_id, self.topic_id, interaction),
        )


class AddAccessStep1(discord.ui.View):
    def __init__(self, guild_id: int, topic_id: str, oi: discord.Interaction) -> None:
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.topic_id = topic_id
        self.oi = oi
        sel = discord.ui.RoleSelect(placeholder="Select a role…", min_values=1, max_values=1)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        role_id = int(interaction.data["values"][0])
        opts = [
            discord.SelectOption(
                label=o.label, value=o.value,
                description=o.description,
                default=(o.value in ("view_channel", "send_messages", "read_history")),
            )
            for o in PERM_OPTIONS
        ]
        sel = discord.ui.Select(
            placeholder="Select permissions…", options=opts,
            min_values=0, max_values=6,
        )
        step2 = AddAccessStep2(self.guild_id, self.topic_id, role_id, self.oi)
        # Reassign the stored select callback so step2 handles it
        sel.callback = step2._on_select
        step2.add_item(sel)
        await interaction.response.edit_message(
            content=(
                f"👥 **Step 2 of 2** — Permissions for <@&{role_id}>\n"
                "Select all permissions this role should have in this ticket type's channels:"
            ),
            view=step2,
        )


class AddAccessStep2(discord.ui.View):
    def __init__(self, guild_id: int, topic_id: str, role_id: int, oi: discord.Interaction) -> None:
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.topic_id = topic_id
        self.role_id  = role_id
        self.oi       = oi

    async def _on_select(self, interaction: discord.Interaction) -> None:
        selected = set(interaction.data.get("values", []))
        await upsert_category_perm(
            self.guild_id, self.topic_id, self.role_id,
            view_channel=    "view_channel"    in selected,
            send_messages=   "send_messages"   in selected,
            read_history=    "read_history"    in selected,
            attach_files=    "attach_files"    in selected,
            embed_links=     "embed_links"     in selected,
            manage_messages= "manage_messages" in selected,
        )
        topic = await get_guild_topic(self.guild_id, self.topic_id)
        await interaction.response.edit_message(
            content=f"✅ Permissions saved for <@&{self.role_id}>.", view=None
        )
        await self.oi.edit_original_response(
            embed=_access_embed(topic),
            view=ChannelAccessView(self.guild_id, self.topic_id, self.oi),
        )


class RemoveAccessView(discord.ui.View):
    def __init__(self, guild_id: int, topic_id: str, cat_perms: list[dict], oi: discord.Interaction) -> None:
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.topic_id = topic_id
        self.oi = oi
        opts = [
            discord.SelectOption(
                label=f"<@&{p['role_id']}>  (ID: {p['role_id']})",
                value=str(p["role_id"]),
                description=_perm_flags(p),
            )
            for p in cat_perms
        ]
        sel = discord.ui.Select(placeholder="Select role to remove…", options=opts)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        role_id = int(interaction.data["values"][0])
        await remove_category_perm(self.guild_id, self.topic_id, role_id)
        topic = await get_guild_topic(self.guild_id, self.topic_id)
        await interaction.response.edit_message(
            content=f"✅ Role `{role_id}` removed from channel access.", view=None
        )
        await self.oi.edit_original_response(
            embed=_access_embed(topic),
            view=ChannelAccessView(self.guild_id, self.topic_id, self.oi),
        )


# ══════════════════════════════════════════════════════════════
#  Cog
# ══════════════════════════════════════════════════════════════

class SetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="setup",
        description="Open the ticket bot configuration wizard. (Admin / Staff only)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction) -> None:
        if not (interaction.user.guild_permissions.manage_guild or is_staff(interaction.user)):
            return await interaction.response.send_message(
                "❌ You need the **Manage Server** permission or a staff role to use this.",
                ephemeral=True,
            )
        # Ensure topics are seeded for this guild
        await get_guild_topics(interaction.guild.id)
        await interaction.response.send_message(
            embed=_main_embed(interaction.guild),
            view=SetupMainView(interaction.guild.id, interaction),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SetupCog(bot))
