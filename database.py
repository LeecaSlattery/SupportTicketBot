"""
utils/database.py
Async SQLite helpers — tickets, counters, staff notes, blacklist,
feedback, priority, response-time tracking, and per-guild topic config.
"""

from __future__ import annotations
import aiosqlite
import config as _cfg

DB_PATH = "tickets.db"


# ══════════════════════════════════════════════════════════════
#  Schema
# ══════════════════════════════════════════════════════════════

async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id           INTEGER UNIQUE NOT NULL,
                guild_id             INTEGER NOT NULL,
                user_id              INTEGER NOT NULL,
                topic_id             TEXT    NOT NULL,
                ticket_number        INTEGER NOT NULL,
                status               TEXT    NOT NULL DEFAULT 'open',
                created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at            TIMESTAMP,
                closed_by            INTEGER,
                priority             TEXT    DEFAULT 'none',
                first_staff_reply_at TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ticket_counters (
                guild_id  INTEGER NOT NULL,
                topic_id  TEXT    NOT NULL,
                count     INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, topic_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS staff_notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL UNIQUE,
                author_id  INTEGER NOT NULL,
                content    TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                reason   TEXT    DEFAULT 'No reason provided',
                added_by INTEGER NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                rating     INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS panel_configs (
                guild_id    INTEGER PRIMARY KEY,
                title       TEXT    NOT NULL DEFAULT '📬  Support Tickets',
                description TEXT    NOT NULL DEFAULT 'Click a button below to open a ticket.',
                color       INTEGER NOT NULL DEFAULT 5793266
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS topic_configs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id        INTEGER NOT NULL,
                topic_id        TEXT    NOT NULL,
                label           TEXT    NOT NULL,
                emoji           TEXT,
                button_style    TEXT    NOT NULL DEFAULT 'primary',
                category_id     INTEGER,
                channel_prefix  TEXT    NOT NULL,
                welcome_message TEXT,
                sort_order      INTEGER NOT NULL DEFAULT 0,
                UNIQUE(guild_id, topic_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS topic_questions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                topic_id    TEXT    NOT NULL,
                sort_order  INTEGER NOT NULL DEFAULT 0,
                label       TEXT    NOT NULL,
                placeholder TEXT,
                required    INTEGER NOT NULL DEFAULT 1,
                long_answer INTEGER NOT NULL DEFAULT 0,
                max_length  INTEGER NOT NULL DEFAULT 1000
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS topic_required_roles (
                guild_id INTEGER NOT NULL,
                topic_id TEXT    NOT NULL,
                role_id  INTEGER NOT NULL,
                PRIMARY KEY (guild_id, topic_id, role_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS topic_category_perms (
                guild_id         INTEGER NOT NULL,
                topic_id         TEXT    NOT NULL,
                role_id          INTEGER NOT NULL,
                view_channel     INTEGER NOT NULL DEFAULT 1,
                send_messages    INTEGER NOT NULL DEFAULT 1,
                read_history     INTEGER NOT NULL DEFAULT 1,
                attach_files     INTEGER NOT NULL DEFAULT 0,
                embed_links      INTEGER NOT NULL DEFAULT 0,
                manage_messages  INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, topic_id, role_id)
            )
        """)

        # Migrate existing installs
        for col_def in [
            "priority TEXT DEFAULT 'none'",
            "first_staff_reply_at TIMESTAMP",
        ]:
            try:
                await db.execute(f"ALTER TABLE tickets ADD COLUMN {col_def}")
            except Exception:
                pass

        await db.commit()


# ══════════════════════════════════════════════════════════════
#  Counters
# ══════════════════════════════════════════════════════════════

async def get_next_ticket_number(guild_id: int, topic_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO ticket_counters (guild_id, topic_id, count) VALUES (?, ?, 1)
            ON CONFLICT (guild_id, topic_id) DO UPDATE SET count = count + 1
            """,
            (guild_id, topic_id),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT count FROM ticket_counters WHERE guild_id = ? AND topic_id = ?",
            (guild_id, topic_id),
        )
        row = await cursor.fetchone()
        return row[0]


# ══════════════════════════════════════════════════════════════
#  Tickets — CRUD
# ══════════════════════════════════════════════════════════════

async def create_ticket(channel_id, guild_id, user_id, topic_id, ticket_number) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tickets (channel_id,guild_id,user_id,topic_id,ticket_number,status) VALUES (?,?,?,?,?,'open')",
            (channel_id, guild_id, user_id, topic_id, ticket_number),
        )
        await db.commit()


async def get_ticket(channel_id: int) -> tuple | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,))
        return await cursor.fetchone()


async def get_user_open_ticket(guild_id, user_id, topic_id) -> tuple | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT * FROM tickets WHERE guild_id=? AND user_id=? AND topic_id=? AND status='open'",
            (guild_id, user_id, topic_id),
        )
        return await cursor.fetchone()


async def update_ticket_status(channel_id, status, closed_by=None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        if status == "closed":
            await db.execute(
                "UPDATE tickets SET status=?,closed_at=CURRENT_TIMESTAMP,closed_by=? WHERE channel_id=?",
                (status, closed_by, channel_id),
            )
        else:
            await db.execute(
                "UPDATE tickets SET status=?,closed_at=NULL,closed_by=NULL WHERE channel_id=?",
                (status, channel_id),
            )
        await db.commit()


async def update_ticket_topic(channel_id: int, topic_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tickets SET topic_id=? WHERE channel_id=?", (topic_id, channel_id))
        await db.commit()


async def update_ticket_priority(channel_id: int, priority: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tickets SET priority=? WHERE channel_id=?", (priority, channel_id))
        await db.commit()


async def update_first_staff_reply(channel_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tickets SET first_staff_reply_at=CURRENT_TIMESTAMP WHERE channel_id=? AND first_staff_reply_at IS NULL",
            (channel_id,),
        )
        await db.commit()


async def delete_ticket_record(channel_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tickets WHERE channel_id=?", (channel_id,))
        await db.commit()


# ══════════════════════════════════════════════════════════════
#  Tickets — Queries
# ══════════════════════════════════════════════════════════════

async def get_open_tickets(guild_id: int) -> list[tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT * FROM tickets WHERE guild_id=? AND status='open' ORDER BY created_at ASC",
            (guild_id,),
        )
        return await cursor.fetchall()


async def find_tickets_by_user(guild_id, user_id) -> list[tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT * FROM tickets WHERE guild_id=? AND user_id=? ORDER BY created_at DESC LIMIT 10",
            (guild_id, user_id),
        )
        return await cursor.fetchall()


async def find_ticket_by_number(guild_id, number) -> list[tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT * FROM tickets WHERE guild_id=? AND ticket_number=? ORDER BY created_at DESC",
            (guild_id, number),
        )
        return await cursor.fetchall()


# ══════════════════════════════════════════════════════════════
#  Staff Notes
# ══════════════════════════════════════════════════════════════

async def add_staff_note(channel_id, message_id, author_id, content) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO staff_notes (channel_id,message_id,author_id,content) VALUES (?,?,?,?)",
            (channel_id, message_id, author_id, content),
        )
        await db.commit()


async def get_staff_note_ids(channel_id: int) -> set[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT message_id FROM staff_notes WHERE channel_id=?", (channel_id,)
        )
        return {r[0] for r in await cursor.fetchall()}


# ══════════════════════════════════════════════════════════════
#  Blacklist
# ══════════════════════════════════════════════════════════════

async def add_to_blacklist(guild_id, user_id, reason, added_by) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO blacklist (guild_id,user_id,reason,added_by,added_at) VALUES (?,?,?,?,CURRENT_TIMESTAMP)",
            (guild_id, user_id, reason, added_by),
        )
        await db.commit()


async def remove_from_blacklist(guild_id, user_id) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM blacklist WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_blacklist_entry(guild_id, user_id) -> tuple | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT * FROM blacklist WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
        return await cursor.fetchone()


async def get_full_blacklist(guild_id) -> list[tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT * FROM blacklist WHERE guild_id=? ORDER BY added_at DESC", (guild_id,)
        )
        return await cursor.fetchall()


# ══════════════════════════════════════════════════════════════
#  Feedback
# ══════════════════════════════════════════════════════════════

async def add_feedback(guild_id, channel_id, user_id, rating) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM feedback WHERE channel_id=? AND user_id=?", (channel_id, user_id)
        )
        if await cursor.fetchone():
            return False
        await db.execute(
            "INSERT INTO feedback (guild_id,channel_id,user_id,rating) VALUES (?,?,?,?)",
            (guild_id, channel_id, user_id, rating),
        )
        await db.commit()
        return True


# ══════════════════════════════════════════════════════════════
#  Stats
# ══════════════════════════════════════════════════════════════

async def get_ticket_stats(guild_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status,COUNT(*) FROM tickets WHERE guild_id=? GROUP BY status", (guild_id,)
        )
        by_status = {r[0]: r[1] for r in await cursor.fetchall()}
        cursor = await db.execute(
            "SELECT topic_id,COUNT(*) as c FROM tickets WHERE guild_id=? GROUP BY topic_id ORDER BY c DESC",
            (guild_id,),
        )
        by_topic = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT AVG((julianday(first_staff_reply_at)-julianday(created_at))*86400) FROM tickets WHERE guild_id=? AND first_staff_reply_at IS NOT NULL",
            (guild_id,),
        )
        row = await cursor.fetchone()
        avg_resp = row[0] if row and row[0] else None
        cursor = await db.execute(
            "SELECT AVG(f.rating),COUNT(f.id) FROM feedback f JOIN tickets t ON f.channel_id=t.channel_id WHERE t.guild_id=?",
            (guild_id,),
        )
        fb = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT strftime('%w',created_at),COUNT(*) as c FROM tickets WHERE guild_id=? GROUP BY 1 ORDER BY c DESC LIMIT 1",
            (guild_id,),
        )
        dow = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT strftime('%H',created_at),COUNT(*) as c FROM tickets WHERE guild_id=? GROUP BY 1 ORDER BY c DESC LIMIT 1",
            (guild_id,),
        )
        hr = await cursor.fetchone()
    return {
        "by_status": by_status, "by_topic": by_topic,
        "avg_response_secs": avg_resp,
        "feedback_avg": fb[0] if fb and fb[0] else None,
        "feedback_count": fb[1] if fb else 0,
        "busiest_dow": dow[0] if dow else None,
        "busiest_hour": hr[0] if hr else None,
    }


# ══════════════════════════════════════════════════════════════
#  Panel Config
# ══════════════════════════════════════════════════════════════

async def get_panel_config(guild_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT title,description,color FROM panel_configs WHERE guild_id=?", (guild_id,)
        )
        row = await cursor.fetchone()
    if row:
        return {"title": row[0], "description": row[1], "color": row[2]}
    return {"title": _cfg.PANEL_TITLE, "description": _cfg.PANEL_DESCRIPTION, "color": _cfg.PANEL_COLOR}


async def save_panel_config(guild_id: int, title: str, description: str, color: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO panel_configs (guild_id,title,description,color) VALUES (?,?,?,?) ON CONFLICT (guild_id) DO UPDATE SET title=excluded.title,description=excluded.description,color=excluded.color",
            (guild_id, title, description, color),
        )
        await db.commit()


# ══════════════════════════════════════════════════════════════
#  Topic Configs (per-guild)
# ══════════════════════════════════════════════════════════════

def _style_to_str(style) -> str:
    import discord
    m = {
        discord.ButtonStyle.primary:   "primary",
        discord.ButtonStyle.success:   "success",
        discord.ButtonStyle.danger:    "danger",
        discord.ButtonStyle.secondary: "secondary",
    }
    return m.get(style, "primary") if not isinstance(style, str) else style


async def _load_topic_extras(db, guild_id: int, topic_id: str) -> tuple:
    cursor = await db.execute(
        "SELECT id,label,placeholder,required,long_answer,max_length FROM topic_questions WHERE guild_id=? AND topic_id=? ORDER BY sort_order ASC",
        (guild_id, topic_id),
    )
    questions = [
        {"id": r[0], "label": r[1], "placeholder": r[2] or "",
         "required": bool(r[3]), "long": bool(r[4]), "max_length": r[5]}
        for r in await cursor.fetchall()
    ]
    cursor = await db.execute(
        "SELECT role_id FROM topic_required_roles WHERE guild_id=? AND topic_id=?",
        (guild_id, topic_id),
    )
    required_roles = [r[0] for r in await cursor.fetchall()]
    cursor = await db.execute(
        "SELECT role_id,view_channel,send_messages,read_history,attach_files,embed_links,manage_messages FROM topic_category_perms WHERE guild_id=? AND topic_id=?",
        (guild_id, topic_id),
    )
    cat_perms = [
        {"role_id": r[0], "view_channel": bool(r[1]), "send_messages": bool(r[2]),
         "read_history": bool(r[3]), "attach_files": bool(r[4]),
         "embed_links": bool(r[5]), "manage_messages": bool(r[6])}
        for r in await cursor.fetchall()
    ]
    return questions, required_roles, cat_perms


async def get_guild_topics(guild_id: int) -> list[dict]:
    """Return all topics for a guild. Auto-seeds from config.py on first call."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT topic_id,label,emoji,button_style,category_id,channel_prefix,welcome_message,sort_order FROM topic_configs WHERE guild_id=? ORDER BY sort_order ASC,id ASC",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        if not rows:
            await _seed_topics(db, guild_id)
            await db.commit()
            cursor = await db.execute(
                "SELECT topic_id,label,emoji,button_style,category_id,channel_prefix,welcome_message,sort_order FROM topic_configs WHERE guild_id=? ORDER BY sort_order ASC,id ASC",
                (guild_id,),
            )
            rows = await cursor.fetchall()
        topics = []
        for row in rows:
            tid = row[0]
            questions, req_roles, cat_perms = await _load_topic_extras(db, guild_id, tid)
            topics.append({
                "id": tid, "label": row[1], "emoji": row[2], "button_style": row[3],
                "category_id": row[4], "channel_prefix": row[5],
                "welcome_message": row[6] or "", "sort_order": row[7],
                "questions": questions, "required_roles": req_roles,
                "category_permissions": cat_perms,
            })
    return topics


async def get_guild_topic(guild_id: int, topic_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT topic_id,label,emoji,button_style,category_id,channel_prefix,welcome_message,sort_order FROM topic_configs WHERE guild_id=? AND topic_id=?",
            (guild_id, topic_id),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        questions, req_roles, cat_perms = await _load_topic_extras(db, guild_id, topic_id)
    return {
        "id": row[0], "label": row[1], "emoji": row[2], "button_style": row[3],
        "category_id": row[4], "channel_prefix": row[5],
        "welcome_message": row[6] or "", "sort_order": row[7],
        "questions": questions, "required_roles": req_roles,
        "category_permissions": cat_perms,
    }


async def upsert_topic(guild_id, topic_id, label, emoji, button_style,
                       category_id, channel_prefix, welcome_message, sort_order=0) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO topic_configs
                (guild_id,topic_id,label,emoji,button_style,category_id,channel_prefix,welcome_message,sort_order)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT (guild_id,topic_id) DO UPDATE SET
                label=excluded.label,emoji=excluded.emoji,button_style=excluded.button_style,
                category_id=excluded.category_id,channel_prefix=excluded.channel_prefix,
                welcome_message=excluded.welcome_message,sort_order=excluded.sort_order
            """,
            (guild_id, topic_id, label, emoji, button_style, category_id,
             channel_prefix, welcome_message, sort_order),
        )
        await db.commit()


async def delete_topic_config(guild_id: int, topic_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        for tbl in ("topic_configs", "topic_questions", "topic_required_roles", "topic_category_perms"):
            await db.execute(f"DELETE FROM {tbl} WHERE guild_id=? AND topic_id=?", (guild_id, topic_id))
        await db.commit()


async def _seed_topics(db, guild_id: int) -> None:
    for i, t in enumerate(_cfg.TICKET_TOPICS):
        style_str = _style_to_str(t.get("button_style", "primary"))
        cat_id = t.get("category_id") or None
        if cat_id == 0:
            cat_id = None
        await db.execute(
            "INSERT OR IGNORE INTO topic_configs (guild_id,topic_id,label,emoji,button_style,category_id,channel_prefix,welcome_message,sort_order) VALUES (?,?,?,?,?,?,?,?,?)",
            (guild_id, t["id"], t["label"], t.get("emoji"), style_str, cat_id,
             t.get("channel_prefix", t["id"]), t.get("welcome_message", ""), i),
        )
        for j, q in enumerate(t.get("questions", [])[:5]):
            await db.execute(
                "INSERT OR IGNORE INTO topic_questions (guild_id,topic_id,sort_order,label,placeholder,required,long_answer,max_length) VALUES (?,?,?,?,?,?,?,?)",
                (guild_id, t["id"], j, q["label"], q.get("placeholder", ""),
                 1 if q.get("required", True) else 0,
                 1 if q.get("long", False) else 0,
                 q.get("max_length", 1000)),
            )
        for role_id in t.get("required_roles", []):
            await db.execute(
                "INSERT OR IGNORE INTO topic_required_roles (guild_id,topic_id,role_id) VALUES (?,?,?)",
                (guild_id, t["id"], role_id),
            )
        for perm in t.get("category_permissions", []):
            await db.execute(
                "INSERT OR IGNORE INTO topic_category_perms (guild_id,topic_id,role_id,view_channel,send_messages,read_history,attach_files,embed_links,manage_messages) VALUES (?,?,?,?,?,?,?,?,?)",
                (guild_id, t["id"], perm["role_id"],
                 1 if perm.get("view_channel", True) else 0,
                 1 if perm.get("send_messages", True) else 0,
                 1 if perm.get("read_history", True) else 0,
                 1 if perm.get("attach_files", False) else 0,
                 1 if perm.get("embed_links", False) else 0,
                 1 if perm.get("manage_messages", False) else 0),
            )


# ══════════════════════════════════════════════════════════════
#  Questions
# ══════════════════════════════════════════════════════════════

async def add_topic_question(guild_id, topic_id, label, placeholder,
                              required, long_answer, max_length) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COALESCE(MAX(sort_order),-1)+1 FROM topic_questions WHERE guild_id=? AND topic_id=?",
            (guild_id, topic_id),
        )
        row = await cursor.fetchone()
        next_order = row[0] if row else 0
        await db.execute(
            "INSERT INTO topic_questions (guild_id,topic_id,sort_order,label,placeholder,required,long_answer,max_length) VALUES (?,?,?,?,?,?,?,?)",
            (guild_id, topic_id, next_order, label, placeholder,
             1 if required else 0, 1 if long_answer else 0, max_length),
        )
        await db.commit()


async def delete_topic_question(guild_id: int, question_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM topic_questions WHERE id=? AND guild_id=?", (question_id, guild_id)
        )
        await db.commit()


# ══════════════════════════════════════════════════════════════
#  Required Roles
# ══════════════════════════════════════════════════════════════

async def set_topic_required_roles(guild_id, topic_id, role_ids: list[int]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM topic_required_roles WHERE guild_id=? AND topic_id=?",
            (guild_id, topic_id),
        )
        for rid in role_ids:
            await db.execute(
                "INSERT OR IGNORE INTO topic_required_roles (guild_id,topic_id,role_id) VALUES (?,?,?)",
                (guild_id, topic_id, rid),
            )
        await db.commit()


# ══════════════════════════════════════════════════════════════
#  Category Permissions
# ══════════════════════════════════════════════════════════════

async def upsert_category_perm(guild_id, topic_id, role_id,
                                view_channel=True, send_messages=True, read_history=True,
                                attach_files=False, embed_links=False, manage_messages=False) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO topic_category_perms
                (guild_id,topic_id,role_id,view_channel,send_messages,read_history,attach_files,embed_links,manage_messages)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT (guild_id,topic_id,role_id) DO UPDATE SET
                view_channel=excluded.view_channel,send_messages=excluded.send_messages,
                read_history=excluded.read_history,attach_files=excluded.attach_files,
                embed_links=excluded.embed_links,manage_messages=excluded.manage_messages
            """,
            (guild_id, topic_id, role_id,
             1 if view_channel else 0, 1 if send_messages else 0, 1 if read_history else 0,
             1 if attach_files else 0, 1 if embed_links else 0, 1 if manage_messages else 0),
        )
        await db.commit()


async def remove_category_perm(guild_id, topic_id, role_id) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM topic_category_perms WHERE guild_id=? AND topic_id=? AND role_id=?",
            (guild_id, topic_id, role_id),
        )
        await db.commit()
