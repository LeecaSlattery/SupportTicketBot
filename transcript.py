"""
utils/transcript.py
Generates a self-contained Discord-styled HTML transcript.
Staff note message IDs passed via excluded_ids are silently skipped.
"""

import html
from datetime import datetime, timezone

import discord

_AVATAR_COLORS = [
    "#5865F2", "#EB459E", "#ED4245",
    "#FEE75C", "#57F287", "#00B0F4",
]


def _color_for(user_id: int, cache: dict) -> str:
    if user_id not in cache:
        cache[user_id] = _AVATAR_COLORS[len(cache) % len(_AVATAR_COLORS)]
    return cache[user_id]


def _initials(name: str) -> str:
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper()


async def generate_transcript(
    channel: discord.TextChannel,
    ticket_data: dict,
    excluded_ids: set[int] | None = None,
) -> str:
    """
    Fetch all messages from *channel* and return a polished HTML string.

    ticket_data keys used: topic_id (str), ticket_number (int)
    excluded_ids: message IDs to skip (staff notes)
    """
    excluded_ids = excluded_ids or set()
    messages: list[discord.Message] = []
    async for msg in channel.history(limit=None, oldest_first=True):
        if msg.id not in excluded_ids:
            messages.append(msg)

    topic_id      = ticket_data.get("topic_id", "ticket")
    ticket_number = ticket_data.get("ticket_number", 0)
    generated_at  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Transcript · #{html.escape(channel.name)}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#36393f;color:#dcddde;font-family:'Whitney','Helvetica Neue',Helvetica,Arial,sans-serif;font-size:16px;line-height:1.375}}
a{{color:#00b0f4;text-decoration:none}}a:hover{{text-decoration:underline}}
.hdr{{background:#2f3136;padding:18px 28px;border-bottom:2px solid #202225;display:flex;align-items:center;gap:14px}}
.hdr-icon{{width:48px;height:48px;background:#5865f2;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0}}
.hdr h1{{font-size:19px;font-weight:700;color:#fff}}
.hdr p{{font-size:13px;color:#72767d;margin-top:2px}}
.meta{{background:#2f3136;padding:12px 28px;display:flex;flex-wrap:wrap;gap:24px;border-bottom:1px solid #202225}}
.meta-item{{display:flex;flex-direction:column;gap:2px}}
.meta-label{{font-size:11px;font-weight:700;text-transform:uppercase;color:#72767d;letter-spacing:.6px}}
.meta-value{{font-size:13px;color:#dcddde}}
.messages{{padding:16px 28px}}
.day-sep{{display:flex;align-items:center;gap:10px;margin:20px 0;color:#72767d;font-size:12px;font-weight:600}}
.day-sep::before,.day-sep::after{{content:'';flex:1;height:1px;background:#3f4147}}
.msg{{display:flex;gap:14px;padding:2px 8px;border-radius:4px;transition:background .1s}}
.msg:hover{{background:#32353b}}
.msg.grouped .avatar-wrap{{visibility:hidden}}
.msg.grouped .msg-meta{{display:none}}
.avatar-wrap{{flex-shrink:0;margin-top:4px}}
.avatar-wrap img{{width:40px;height:40px;border-radius:50%;object-fit:cover}}
.avatar-fallback{{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:15px;color:#fff}}
.msg-body{{flex:1;min-width:0}}
.msg-meta{{display:flex;align-items:baseline;gap:8px;margin-bottom:3px}}
.username{{font-weight:600;font-size:15px}}
.bot-tag{{background:#5865f2;color:#fff;font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;text-transform:uppercase;vertical-align:middle;margin-left:3px}}
.ts{{font-size:11px;color:#72767d}}
.msg-text{{font-size:15px;word-break:break-word;white-space:pre-wrap}}
.embed{{border-left:4px solid #5865f2;background:#2f3136;padding:10px 14px;border-radius:0 4px 4px 0;margin-top:6px;max-width:520px}}
.embed-title{{font-weight:700;font-size:14px;color:#fff;margin-bottom:5px}}
.embed-desc{{font-size:13px;color:#dcddde;line-height:1.4}}
.embed-field{{margin-top:8px}}
.embed-field-name{{font-size:12px;font-weight:700;color:#fff;margin-bottom:2px}}
.embed-field-value{{font-size:13px;color:#dcddde}}
.attachment{{margin-top:6px}}
.attachment img{{max-width:400px;max-height:280px;border-radius:4px;display:block}}
.attachment a{{font-size:13px}}
.footer{{background:#2f3136;padding:12px 28px;text-align:center;font-size:12px;color:#72767d;border-top:1px solid #202225}}
</style>
</head>
<body>
<div class="hdr">
  <div class="hdr-icon">🎫</div>
  <div>
    <h1>#{html.escape(channel.name)}</h1>
    <p>Ticket Transcript &nbsp;·&nbsp; {html.escape(channel.guild.name)}</p>
  </div>
</div>
<div class="meta">
  <div class="meta-item"><span class="meta-label">Ticket #</span><span class="meta-value">{ticket_number:04d}</span></div>
  <div class="meta-item"><span class="meta-label">Topic</span><span class="meta-value">{html.escape(topic_id.replace('_',' ').title())}</span></div>
  <div class="meta-item"><span class="meta-label">Channel</span><span class="meta-value">#{html.escape(channel.name)}</span></div>
  <div class="meta-item"><span class="meta-label">Server</span><span class="meta-value">{html.escape(channel.guild.name)}</span></div>
  <div class="meta-item"><span class="meta-label">Messages</span><span class="meta-value">{len(messages)}</span></div>
  <div class="meta-item"><span class="meta-label">Generated</span><span class="meta-value">{generated_at}</span></div>
</div>
<div class="messages">
"""

    color_cache: dict[int, str] = {}
    last_author_id: int | None  = None
    last_msg_time: datetime | None = None
    current_date: str | None    = None

    for msg in messages:
        msg_date = msg.created_at.strftime("%B %d, %Y")
        if msg_date != current_date:
            current_date = msg_date
            doc += f'<div class="day-sep">{html.escape(msg_date)}</div>\n'
            last_author_id = None

        is_grouped = (
            last_author_id == msg.author.id
            and last_msg_time is not None
            and (msg.created_at - last_msg_time).total_seconds() < 300
        )
        grouped_cls = " grouped" if is_grouped else ""

        color    = _color_for(msg.author.id, color_cache)
        initials = _initials(msg.author.display_name)
        timestamp = msg.created_at.strftime("%H:%M")
        bot_tag   = '<span class="bot-tag">BOT</span>' if msg.author.bot else ""

        avatar_url = str(msg.author.display_avatar.url) if msg.author.display_avatar else ""
        if avatar_url:
            avatar_html = (
                f'<img src="{html.escape(avatar_url)}" '
                f'alt="{html.escape(msg.author.display_name)}" '
                f'onerror="this.parentElement.innerHTML=\'<div class=&quot;avatar-fallback&quot; '
                f'style=&quot;background:{color}&quot;>{html.escape(initials)}</div>\'">'
            )
        else:
            avatar_html = (
                f'<div class="avatar-fallback" style="background:{color}">'
                f'{html.escape(initials)}</div>'
            )

        doc += f'<div class="msg{grouped_cls}">\n'
        doc += f'  <div class="avatar-wrap">{avatar_html}</div>\n'
        doc += f'  <div class="msg-body">\n'
        doc += (
            f'    <div class="msg-meta">'
            f'<span class="username" style="color:{color}">{html.escape(msg.author.display_name)}</span>'
            f'{bot_tag}'
            f'<span class="ts">{timestamp}</span>'
            f'</div>\n'
        )

        if msg.content:
            doc += f'    <div class="msg-text">{html.escape(msg.content)}</div>\n'

        for embed in msg.embeds:
            e_color = f"#{embed.color.value:06x}" if embed.color else "#5865f2"
            doc += f'    <div class="embed" style="border-left-color:{e_color}">\n'
            if embed.title:
                doc += f'      <div class="embed-title">{html.escape(embed.title)}</div>\n'
            if embed.description:
                doc += f'      <div class="embed-desc">{html.escape(embed.description)}</div>\n'
            for field in embed.fields:
                doc += (
                    f'      <div class="embed-field">'
                    f'<div class="embed-field-name">{html.escape(field.name)}</div>'
                    f'<div class="embed-field-value">{html.escape(field.value)}</div>'
                    f'</div>\n'
                )
            doc += "    </div>\n"

        for att in msg.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                doc += (
                    f'    <div class="attachment">'
                    f'<img src="{html.escape(att.url)}" alt="{html.escape(att.filename)}">'
                    f'</div>\n'
                )
            else:
                doc += (
                    f'    <div class="attachment">📎 '
                    f'<a href="{html.escape(att.url)}">{html.escape(att.filename)}</a>'
                    f'</div>\n'
                )

        doc += "  </div>\n</div>\n"
        last_author_id = msg.author.id
        last_msg_time  = msg.created_at

    doc += f"""</div>
<div class="footer">
  Transcript generated by Ticket Bot &nbsp;·&nbsp; {generated_at} &nbsp;·&nbsp; {len(messages)} messages (staff notes excluded)
</div>
</body>
</html>"""

    return doc
