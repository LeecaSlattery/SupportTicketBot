"""
cogs/help.py
/help command — paginated help embed with section navigation.

Everyone sees:
  - Overview
  - Opening a Ticket
  - Button Interactions

Staff also see:
  - Staff Commands
  - Blacklist Commands
  - Bot Configuration
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from cogs.tickets import is_staff

# ══════════════════════════════════════════════════════════════
#  Section definitions
# ══════════════════════════════════════════════════════════════

def _build_sections(is_staff_user: bool) -> list[dict]:
    """
    Returns a list of section dicts.
    Each dict has: title, emoji, description, fields (list of (name, value)).
    """
    sections = [

        # ── Overview ──────────────────────────────────────────
        {
            "id":          "overview",
            "label":       "Overview",
            "emoji":       "📬",
            "description": (
                "Welcome to **Ticket Bot** — a fully-featured support ticket "
                "system for Discord.\n\n"
                "Users can open private ticket channels by topic, answer "
                "intake questions, and communicate directly with staff. "
                "Staff have a full suite of tools to manage, prioritise, "
                "and track every ticket."
            ),
            "fields": [
                (
                    "How it works",
                    "1. A staff member runs `/setup` in a channel to post the ticket panel.\n"
                    "2. Users click the button for their issue to open a private channel.\n"
                    "3. Staff are notified and respond inside the channel.\n"
                    "4. Either side can close the ticket when resolved.",
                ),
                (
                    "Quick links",
                    "• `/setup` — Post the ticket panel *(admin only)*\n"
                    "• `/help` — Show this help menu\n"
                    "• `/ticketinfo` — View details about the current ticket",
                ),
            ],
        },

        # ── Opening a Ticket ──────────────────────────────────
        {
            "id":          "opening",
            "label":       "Opening a Ticket",
            "emoji":       "🎫",
            "description": (
                "To open a ticket, go to the support panel channel and click "
                "the button that best matches your issue."
            ),
            "fields": [
                (
                    "Available ticket types",
                    "\n".join(
                        f"{t.get('emoji', '🎫')} **{t['label']}** — "
                        f"Creates a `{t['channel_prefix']}-####` channel"
                        + (
                            f"\n  *Requires one of: "
                            + ", ".join(f"<@&{r}>" for r in t["required_roles"])
                            + "*"
                            if t.get("required_roles") else ""
                        )
                        for t in config.TICKET_TOPICS
                    ),
                ),
                (
                    "Intake questions",
                    "Some ticket types show a short form before the channel is created. "
                    "Fill in as much detail as possible — your answers appear in the "
                    "opening message so staff can start helping immediately.",
                ),
                (
                    "Limits",
                    "You can only have **one open ticket per topic** at a time. "
                    "If you already have an open ticket of that type, the bot will "
                    "send you a link to it instead of creating a new one.",
                ),
            ],
        },

        # ── Button Interactions ───────────────────────────────
        {
            "id":          "buttons",
            "label":       "Button Interactions",
            "emoji":       "🔘",
            "description": (
                "Ticket channels are managed through buttons rather than commands. "
                "Here's what each button does and who can use it."
            ),
            "fields": [
                (
                    "🟢 Inside an open ticket",
                    "**🔒 Close Ticket** — Available to the ticket opener *and* staff. "
                    "Locks the channel and posts the staff control panel.\n\n"
                    "**➕ Add User** *(staff only)* — Opens a prompt to add a member "
                    "to the ticket by their user ID.\n\n"
                    "**🚦 Set Priority** *(staff only)* — Opens a dropdown to mark the "
                    "ticket Low / Medium / High / Urgent. Renames the channel with a "
                    "colour emoji prefix and can trigger an alert channel.",
                ),
                (
                    "🔒 After a ticket is closed",
                    "**🔓 Reopen** *(staff only)* — Restores the opener's ability to "
                    "type and posts a fresh control panel.\n\n"
                    "**📄 Create Transcript** *(staff only)* — Generates a full HTML "
                    "transcript of the conversation (staff notes excluded) and attaches "
                    "it to the channel. Optionally also posted to a log channel.\n\n"
                    "**📂 Move Category** *(staff only)* — Moves the channel to a "
                    "different topic category and updates permissions accordingly.\n\n"
                    "**🗑️ Delete Ticket** *(staff only)* — Permanently deletes the "
                    "channel after a 5-second countdown.",
                ),
                (
                    "💬 Feedback (via DM)",
                    "After a ticket closes, the opener receives a DM with a "
                    "⭐–⭐⭐⭐⭐⭐ rating prompt. Ratings are stored and shown in `/stats`.",
                ),
            ],
        },
    ]

    # ── Staff-only sections ───────────────────────────────────
    if is_staff_user:

        sections.append({
            "id":          "staff_commands",
            "label":       "Staff Commands",
            "emoji":       "🛠️",
            "description": "Commands available to staff members inside or outside ticket channels.",
            "fields": [
                (
                    "Ticket management",
                    "**`/ticketinfo`** — View ticket number, topic, status, priority, "
                    "opener, and timestamps for the current channel.\n\n"
                    "**`/adduser member:`** — Grant a member access to the current ticket.\n\n"
                    "**`/removeuser member:`** — Revoke a member's access "
                    "(cannot remove the opener).\n\n"
                    "**`/priority level:`** — Set priority to Low / Medium / High / "
                    "Urgent / Clear. Same as the in-channel button.",
                ),
                (
                    "Snippets & notes",
                    "**`/snippet`** — Choose from a list of pre-written replies and "
                    "send them as a formatted embed into the current ticket.\n\n"
                    "**`/note content:`** — Post a staff-only note embed. "
                    "Visible to staff in the channel but **excluded from transcripts** "
                    "so the opener never sees it.",
                ),
                (
                    "Visibility",
                    "**`/listopen`** — Ephemeral list of every open ticket across all "
                    "topics with channel mentions, openers, and priority.\n\n"
                    "**`/findticket user:`** — Find all tickets opened by a specific member "
                    "(works even for deleted channels).\n\n"
                    "**`/findticket number:`** — Look up a specific ticket by its number.\n\n"
                    "**`/stats`** — Server-wide dashboard: totals, avg first response time, "
                    "feedback ratings, topic breakdown, and busiest day/hour.",
                ),
                (
                    "Admin only",
                    "**`/setup`** — Post the ticket panel embed + buttons to the current "
                    "channel. Run this once in your dedicated support channel.",
                ),
            ],
        })

        sections.append({
            "id":          "blacklist",
            "label":       "Blacklist Commands",
            "emoji":       "🚫",
            "description": (
                "The blacklist prevents specific users from opening any tickets. "
                "Blacklisted users see an ephemeral message with the reason when "
                "they click a panel button."
            ),
            "fields": [
                (
                    "Commands",
                    "**`/blacklist add user: reason:`** — Block a user. "
                    "Reason is shown to them if they try to open a ticket.\n\n"
                    "**`/blacklist remove user:`** — Lift the restriction.\n\n"
                    "**`/blacklist list`** — View all blacklisted users, their reasons, "
                    "who added them, and when.\n\n"
                    "**`/blacklist check user:`** — Check if a specific user is blocked.",
                ),
                (
                    "Notes",
                    "• Staff members cannot be blacklisted.\n"
                    "• Adding a user who is already blacklisted overwrites the old reason.\n"
                    "• Blacklist entries persist across bot restarts (stored in `tickets.db`).",
                ),
            ],
        })

        sections.append({
            "id":          "configuration",
            "label":       "Bot Configuration",
            "emoji":       "⚙️",
            "description": (
                "All bot settings live in **`config.py`** — no code changes are needed "
                "for day-to-day customisation."
            ),
            "fields": [
                (
                    "Core settings",
                    "**`STAFF_ROLE_IDS`** — List of role IDs that count as staff. "
                    "Admins always count as staff regardless.\n\n"
                    "**`LOG_CHANNEL_ID`** — Channel that receives open / close / reopen / "
                    "move / delete events. Set to `None` to disable.\n\n"
                    "**`TRANSCRIPT_CHANNEL_ID`** — Channel that receives a copy of every "
                    "transcript generated. Set to `None` to skip the secondary copy.\n\n"
                    "**`PRIORITY_ALERT_CHANNEL_ID`** — Channel that receives an alert "
                    "when a ticket reaches the alert threshold.\n\n"
                    "**`PRIORITY_ALERT_MIN_LEVEL`** — Minimum level that triggers an alert "
                    "(`\"low\"` / `\"medium\"` / `\"high\"` / `\"urgent\"`).",
                ),
                (
                    "Ticket topics",
                    "Each entry in **`TICKET_TOPICS`** creates one panel button. Fields:\n"
                    "• `id` — Unique snake_case key used in channel names and the DB.\n"
                    "• `label` / `emoji` / `button_style` — Panel button appearance.\n"
                    "• `category_id` — Discord category the channel is created in.\n"
                    "• `channel_prefix` — Channel name prefix (e.g. `billing-0003`).\n"
                    "• `welcome_message` — First message posted in the ticket.\n"
                    "• `required_roles` — Role IDs needed to open this type. Empty = everyone.\n"
                    "• `questions` — Up to 5 modal fields shown before the channel is created.\n"
                    "• `category_permissions` — Extra roles granted access beyond global staff.",
                ),
                (
                    "Snippets",
                    "Add entries to **`SNIPPETS`** in `config.py` to make them available "
                    "via `/snippet`. Each entry needs a `name` and `content` field. "
                    "No restart required if you hot-reload the cog.",
                ),
            ],
        })

    return sections


# ══════════════════════════════════════════════════════════════
#  Select menu for navigation
# ══════════════════════════════════════════════════════════════

def _build_embed(section: dict, is_staff_user: bool, page: int, total: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"{section['emoji']}  {section['label']}",
        description=section["description"],
        color=config.TICKET_COLOR,
    )
    for name, value in section.get("fields", []):
        embed.add_field(name=name, value=value, inline=False)

    viewer = "Staff" if is_staff_user else "User"
    embed.set_footer(
        text=f"Page {page}/{total}  ·  Viewing as: {viewer}  ·  Use the menu below to navigate"
    )
    return embed


class HelpView(discord.ui.View):
    """Persistent-ish view (60 s timeout, not re-registered on restart — ephemeral is fine)."""

    def __init__(self, sections: list[dict], current_index: int, is_staff_user: bool) -> None:
        super().__init__(timeout=120)
        self.sections      = sections
        self.current_index = current_index
        self.is_staff_user = is_staff_user
        self._build_select()

    def _build_select(self) -> None:
        self.clear_items()
        options = [
            discord.SelectOption(
                label=s["label"],
                value=str(i),
                emoji=s["emoji"],
                default=(i == self.current_index),
            )
            for i, s in enumerate(self.sections)
        ]
        sel = discord.ui.Select(
            placeholder="Jump to a section…",
            options=options,
        )
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self.current_index = int(interaction.data["values"][0])
        self._build_select()
        embed = _build_embed(
            self.sections[self.current_index],
            self.is_staff_user,
            self.current_index + 1,
            len(self.sections),
        )
        await interaction.response.edit_message(embed=embed, view=self)

    def current_embed(self) -> discord.Embed:
        return _build_embed(
            self.sections[self.current_index],
            self.is_staff_user,
            self.current_index + 1,
            len(self.sections),
        )


# ══════════════════════════════════════════════════════════════
#  Cog
# ══════════════════════════════════════════════════════════════

class HelpCog(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="help",
        description="Show the bot help guide.",
    )
    @app_commands.describe(
        section="Which section to open first (optional)"
    )
    @app_commands.choices(section=[
        app_commands.Choice(name="📬 Overview",             value="overview"),
        app_commands.Choice(name="🎫 Opening a Ticket",     value="opening"),
        app_commands.Choice(name="🔘 Button Interactions",  value="buttons"),
        app_commands.Choice(name="🛠️ Staff Commands",       value="staff_commands"),
        app_commands.Choice(name="🚫 Blacklist Commands",   value="blacklist"),
        app_commands.Choice(name="⚙️ Bot Configuration",    value="configuration"),
    ])
    async def help(
        self,
        interaction: discord.Interaction,
        section: str = "overview",
    ) -> None:
        staff = is_staff(interaction.user)
        sections = _build_sections(staff)

        # Resolve starting index; fall back to 0 if section is staff-only and user isn't staff
        index = next(
            (i for i, s in enumerate(sections) if s["id"] == section), 0
        )

        view = HelpView(sections, index, staff)
        await interaction.response.send_message(
            embed=view.current_embed(),
            view=view,
            ephemeral=True,
        )


# ══════════════════════════════════════════════════════════════
#  Extension entry point
# ══════════════════════════════════════════════════════════════

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
