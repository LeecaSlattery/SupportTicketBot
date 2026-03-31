"""
cogs/staff.py
Staff-only commands: /snippet, /note, /priority,
/blacklist, /stats, /findticket, /listopen.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.database import (
    add_staff_note,
    add_to_blacklist,
    find_ticket_by_number,
    find_tickets_by_user,
    get_blacklist_entry,
    get_full_blacklist,
    get_guild_topic,
    get_open_tickets,
    get_ticket,
    get_ticket_stats,
    remove_from_blacklist,
    update_ticket_priority,
)
from cogs.tickets import (
    SetPriorityView,
    is_staff,
    strip_priority_prefix,
    _priority_meets_threshold,
)


# ══════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════

_DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday",
         "Thursday", "Friday", "Saturday"]


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d}d {h}h"


# ══════════════════════════════════════════════════════════════
#  Snippet select view  (ephemeral, per-interaction)
# ══════════════════════════════════════════════════════════════

class SnippetSelectView(discord.ui.View):
    def __init__(self, channel: discord.TextChannel) -> None:
        super().__init__(timeout=30)
        self.channel = channel
        options = [
            discord.SelectOption(
                label=s["name"][:100],
                description=(s["content"][:97] + "…") if len(s["content"]) > 100 else s["content"],
                value=str(i),
            )
            for i, s in enumerate(config.SNIPPETS)
        ]
        sel = discord.ui.Select(
            placeholder="Choose a snippet to send…",
            options=options,
        )
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        idx     = int(interaction.data["values"][0])
        snippet = config.SNIPPETS[idx]

        embed = discord.Embed(
            title=snippet["name"],
            description=snippet["content"],
            color=config.TICKET_COLOR,
        )
        embed.set_footer(text=f"Sent by {interaction.user.display_name}")

        await self.channel.send(embed=embed)
        await interaction.response.edit_message(
            content=f"✅ **{snippet['name']}** sent.", view=None
        )


# ══════════════════════════════════════════════════════════════
#  Staff Cog
# ══════════════════════════════════════════════════════════════

class StaffCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /snippet ──────────────────────────────────────────────

    @app_commands.command(
        name="snippet",
        description="Send a pre-written canned response. (Staff only)",
    )
    async def snippet(self, interaction: discord.Interaction) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ Only staff can use snippets.", ephemeral=True
            )
        if not await get_ticket(interaction.channel.id):
            return await interaction.response.send_message(
                "❌ This command can only be used inside a ticket channel.", ephemeral=True
            )
        if not config.SNIPPETS:
            return await interaction.response.send_message(
                "❌ No snippets are configured in `config.py`.", ephemeral=True
            )
        await interaction.response.send_message(
            "📋 Select a snippet to send to this ticket:",
            view=SnippetSelectView(interaction.channel),
            ephemeral=True,
        )

    # ── /note ─────────────────────────────────────────────────

    @app_commands.command(
        name="note",
        description="Post a staff-only note (excluded from transcripts). (Staff only)",
    )
    @app_commands.describe(content="The note content visible to staff in this channel")
    async def note(self, interaction: discord.Interaction, content: str) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ Only staff can post notes.", ephemeral=True
            )
        if not await get_ticket(interaction.channel.id):
            return await interaction.response.send_message(
                "❌ This command can only be used inside a ticket channel.", ephemeral=True
            )

        embed = discord.Embed(
            title="📝  Staff Note",
            description=content,
            color=config.NOTE_COLOR,
        )
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=str(interaction.user.display_avatar.url),
        )
        embed.set_footer(text="⚠️  Staff only — this message is excluded from transcripts")
        embed.timestamp = discord.utils.utcnow()

        await interaction.response.defer(ephemeral=True)
        msg = await interaction.channel.send(embed=embed)

        # Store message ID so transcript generator skips it
        await add_staff_note(
            interaction.channel.id, msg.id, interaction.user.id, content
        )
        await interaction.followup.send("✅ Note posted.", ephemeral=True)

    # ── /priority ─────────────────────────────────────────────

    @app_commands.command(
        name="priority",
        description="Set the priority level of this ticket. (Staff only)",
    )
    @app_commands.describe(level="Priority level to assign")
    @app_commands.choices(level=[
        app_commands.Choice(name="🟢 Low",    value="low"),
        app_commands.Choice(name="🟡 Medium", value="medium"),
        app_commands.Choice(name="🟠 High",   value="high"),
        app_commands.Choice(name="🔴 Urgent", value="urgent"),
        app_commands.Choice(name="⬜ Clear",  value="none"),
    ])
    async def priority(
        self, interaction: discord.Interaction, level: str
    ) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ Only staff can set priority.", ephemeral=True
            )
        ticket = await get_ticket(interaction.channel.id)
        if not ticket:
            return await interaction.response.send_message(
                "❌ This is not a ticket channel.", ephemeral=True
            )

        await update_ticket_priority(interaction.channel.id, level)

        base    = strip_priority_prefix(interaction.channel.name)
        emoji   = config.PRIORITY_EMOJI.get(level, "")
        new_name = f"{emoji}-{base}" if emoji else base
        await interaction.channel.edit(name=new_name)

        label_map = {
            "none": "⬜ None", "low": "🟢 Low",
            "medium": "🟡 Medium", "high": "🟠 High", "urgent": "🔴 Urgent",
        }
        p_embed = discord.Embed(
            description=(
                f"Priority set to **{label_map.get(level, level)}** "
                f"by {interaction.user.mention}."
            ),
            color=config.PRIORITY_COLOR,
        )
        await interaction.channel.send(embed=p_embed)
        await interaction.response.send_message(
            f"✅ Priority set to **{label_map.get(level, level)}**.", ephemeral=True
        )

        if (
            config.PRIORITY_ALERT_CHANNEL_ID
            and level != "none"
            and _priority_meets_threshold(level, config.PRIORITY_ALERT_MIN_LEVEL)
        ):
            alert_ch = interaction.guild.get_channel(config.PRIORITY_ALERT_CHANNEL_ID)
            if alert_ch:
                topic = await get_guild_topic(interaction.guild.id, ticket[4])
                a_embed = discord.Embed(
                    title=f"{emoji}  Priority Ticket — {label_map[level]}",
                    description=(
                        f"**Channel:** {interaction.channel.mention}\n"
                        f"**Topic:** {topic['label'] if topic else ticket[4]}\n"
                        f"**Opener:** <@{ticket[3]}>\n"
                        f"**Set by:** {interaction.user.mention}"
                    ),
                    color=config.PRIORITY_COLOR,
                )
                await alert_ch.send(embed=a_embed)

    # ══════════════════════════════════════════════════════════
    #  /blacklist  (command group)
    # ══════════════════════════════════════════════════════════

    blacklist_group = app_commands.Group(
        name="blacklist",
        description="Manage the ticket blacklist. (Staff only)",
    )

    @blacklist_group.command(name="add", description="Prevent a user from opening tickets.")
    @app_commands.describe(user="User to blacklist", reason="Reason for the restriction")
    async def blacklist_add(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "No reason provided",
    ) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ Only staff can manage the blacklist.", ephemeral=True
            )
        if is_staff(user):
            return await interaction.response.send_message(
                "❌ You cannot blacklist a staff member.", ephemeral=True
            )
        await add_to_blacklist(
            interaction.guild.id, user.id, reason, interaction.user.id
        )
        embed = discord.Embed(
            title="🚫  User Blacklisted",
            color=config.CLOSED_COLOR,
        )
        embed.add_field(name="User",      value=user.mention,                inline=True)
        embed.add_field(name="Added by",  value=interaction.user.mention,    inline=True)
        embed.add_field(name="Reason",    value=reason,                      inline=False)
        await interaction.response.send_message(embed=embed)

    @blacklist_group.command(name="remove", description="Restore a user's ability to open tickets.")
    @app_commands.describe(user="User to remove from the blacklist")
    async def blacklist_remove(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ Only staff can manage the blacklist.", ephemeral=True
            )
        removed = await remove_from_blacklist(interaction.guild.id, user.id)
        if removed:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=f"✅ {user.mention} has been removed from the blacklist.",
                    color=discord.Color.green(),
                )
            )
        else:
            await interaction.response.send_message(
                f"⚠️ {user.mention} is not on the blacklist.", ephemeral=True
            )

    @blacklist_group.command(name="list", description="View all blacklisted users.")
    async def blacklist_list(self, interaction: discord.Interaction) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ Only staff can view the blacklist.", ephemeral=True
            )
        entries = await get_full_blacklist(interaction.guild.id)
        if not entries:
            return await interaction.response.send_message(
                "✅ The blacklist is empty.", ephemeral=True
            )
        embed = discord.Embed(
            title=f"🚫  Ticket Blacklist ({len(entries)} users)",
            color=config.CLOSED_COLOR,
        )
        lines = []
        for entry in entries[:20]:   # cap at 20 to stay within embed limits
            # entry: id(0) guild_id(1) user_id(2) reason(3) added_by(4) added_at(5)
            lines.append(
                f"<@{entry[2]}> — {entry[3]}\n"
                f"  Added by <@{entry[4]}> on {str(entry[5])[:10]}"
            )
        embed.description = "\n\n".join(lines)
        if len(entries) > 20:
            embed.set_footer(text=f"Showing 20 of {len(entries)} entries.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @blacklist_group.command(name="check", description="Check if a user is blacklisted.")
    @app_commands.describe(user="User to check")
    async def blacklist_check(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ Only staff can use this command.", ephemeral=True
            )
        entry = await get_blacklist_entry(interaction.guild.id, user.id)
        if entry:
            embed = discord.Embed(
                title="🚫  User is Blacklisted",
                color=config.CLOSED_COLOR,
            )
            embed.add_field(name="User",     value=user.mention,         inline=True)
            embed.add_field(name="Added by", value=f"<@{entry[4]}>",    inline=True)
            embed.add_field(name="Added at", value=str(entry[5])[:16],  inline=True)
            embed.add_field(name="Reason",   value=entry[3],            inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(
                f"✅ {user.mention} is **not** on the blacklist.", ephemeral=True
            )

    # ══════════════════════════════════════════════════════════
    #  /stats
    # ══════════════════════════════════════════════════════════

    @app_commands.command(
        name="stats",
        description="View ticket statistics for this server. (Staff only)",
    )
    async def stats(self, interaction: discord.Interaction) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ Only staff can view stats.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)

        data = await get_ticket_stats(interaction.guild.id)

        by_status = data["by_status"]
        total  = sum(by_status.values())
        open_  = by_status.get("open",   0)
        closed = by_status.get("closed", 0)

        embed = discord.Embed(
            title=f"📊  Ticket Statistics — {interaction.guild.name}",
            color=config.TICKET_COLOR,
        )

        # Overview
        embed.add_field(
            name="📋 Overview",
            value=(
                f"**Total:** {total}\n"
                f"**Open:** {open_}\n"
                f"**Closed:** {closed}"
            ),
            inline=True,
        )

        # Response time
        avg_s = data["avg_response_secs"]
        embed.add_field(
            name="⏱️ Avg First Response",
            value=_fmt_duration(avg_s) if avg_s else "No data yet",
            inline=True,
        )

        # Feedback
        fb_avg   = data["feedback_avg"]
        fb_count = data["feedback_count"]
        if fb_avg and fb_count:
            stars = "⭐" * round(fb_avg)
            embed.add_field(
                name="💬 Feedback",
                value=f"{stars}\n**{fb_avg:.1f}/5** ({fb_count} ratings)",
                inline=True,
            )
        else:
            embed.add_field(name="💬 Feedback", value="No ratings yet", inline=True)

        # Topic breakdown
        if data["by_topic"]:
            lines = []
            for topic_id, count in data["by_topic"][:8]:
                topic = await get_guild_topic(interaction.guild.id, topic_id)
                label = topic["label"] if topic else topic_id.replace("_", " ").title()
                emoji = topic.get("emoji", "🎫") if topic else "🎫"
                pct   = (count / total * 100) if total else 0
                lines.append(f"{emoji} **{label}:** {count} ({pct:.1f}%)")
            embed.add_field(
                name="📂 By Topic",
                value="\n".join(lines),
                inline=False,
            )

        # Activity patterns
        activity_parts = []
        if data["busiest_dow"] is not None:
            day_name = _DAYS[int(data["busiest_dow"])]
            activity_parts.append(f"**Busiest day:** {day_name}")
        if data["busiest_hour"] is not None:
            hr = int(data["busiest_hour"])
            ampm = f"{hr % 12 or 12} {'AM' if hr < 12 else 'PM'} UTC"
            activity_parts.append(f"**Busiest hour:** {ampm}")
        if activity_parts:
            embed.add_field(
                name="📈 Activity",
                value="\n".join(activity_parts),
                inline=False,
            )

        embed.set_footer(text="Stats are based on all recorded tickets in this server.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ══════════════════════════════════════════════════════════
    #  /findticket
    # ══════════════════════════════════════════════════════════

    @app_commands.command(
        name="findticket",
        description="Look up tickets by user or ticket number. (Staff only)",
    )
    @app_commands.describe(
        user="Find tickets opened by this user",
        number="Find a specific ticket number",
    )
    async def findticket(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
        number: int | None = None,
    ) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ Only staff can search tickets.", ephemeral=True
            )
        if not user and number is None:
            return await interaction.response.send_message(
                "❌ Provide at least one search parameter: `user` or `number`.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        if user:
            tickets = await find_tickets_by_user(interaction.guild.id, user.id)
        else:
            tickets = await find_ticket_by_number(interaction.guild.id, number)

        if not tickets:
            return await interaction.followup.send(
                "🔍 No tickets found matching that search.", ephemeral=True
            )

        embed = discord.Embed(
            title=f"🔍  Search Results ({len(tickets)} ticket{'s' if len(tickets) != 1 else ''})",
            color=config.TICKET_COLOR,
        )

        for t in tickets[:10]:
            topic  = await get_guild_topic(interaction.guild.id, t[4])
            label  = topic["label"] if topic else t[4].replace("_", " ").title()
            emoji  = topic.get("emoji", "🎫") if topic else "🎫"
            status = t[6]
            p_level = t[10] if len(t) > 10 else "none"
            p_emoji = config.PRIORITY_EMOJI.get(p_level, "")

            channel = interaction.guild.get_channel(t[1])
            ch_text = channel.mention if channel else f"*(#{t[1]} — deleted)*"

            status_icon = "🔒" if status == "closed" else "🟢"
            field_name  = f"{status_icon} Ticket #{t[5]:04d} — {emoji} {label}"
            field_value = (
                f"**Channel:** {ch_text}\n"
                f"**Opened by:** <@{t[3]}>\n"
                f"**Status:** {status.title()}"
                + (f"  {p_emoji} {p_level.title()}" if p_level != "none" else "")
                + f"\n**Opened:** {str(t[7])[:16]}"
                + (f"\n**Closed:** {str(t[8])[:16]}" if t[8] else "")
            )
            embed.add_field(name=field_name, value=field_value, inline=False)

        if len(tickets) > 10:
            embed.set_footer(text=f"Showing 10 of {len(tickets)} results.")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ══════════════════════════════════════════════════════════
    #  /listopen
    # ══════════════════════════════════════════════════════════

    @app_commands.command(
        name="listopen",
        description="List all currently open tickets. (Staff only)",
    )
    async def listopen(self, interaction: discord.Interaction) -> None:
        if not is_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ Only staff can list open tickets.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)

        tickets = await get_open_tickets(interaction.guild.id)
        if not tickets:
            return await interaction.followup.send(
                "✅ There are no open tickets right now.", ephemeral=True
            )

        embed = discord.Embed(
            title=f"🟢  Open Tickets — {len(tickets)} active",
            color=config.TICKET_COLOR,
        )

        lines = []
        for t in tickets[:25]:
            topic   = await get_guild_topic(interaction.guild.id, t[4])
            emoji   = topic.get("emoji", "🎫") if topic else "🎫"
            label   = topic["label"] if topic else t[4].replace("_", " ").title()
            channel = interaction.guild.get_channel(t[1])
            ch_text = channel.mention if channel else f"*(#{t[1]})*"
            p_level = t[10] if len(t) > 10 else "none"
            p_emoji = config.PRIORITY_EMOJI.get(p_level, "")

            line = (
                f"{p_emoji} {emoji} **#{t[5]:04d}** {ch_text} "
                f"— {label} — <@{t[3]}>"
                + (f"  `{p_level}`" if p_level != "none" else "")
            )
            lines.append(line)

        embed.description = "\n".join(lines)

        if len(tickets) > 25:
            embed.set_footer(text=f"Showing 25 of {len(tickets)} open tickets.")
        else:
            embed.set_footer(text=f"{len(tickets)} open ticket{'s' if len(tickets) != 1 else ''}.")

        await interaction.followup.send(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════════════════
#  Extension entry point
# ══════════════════════════════════════════════════════════════

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffCog(bot))
