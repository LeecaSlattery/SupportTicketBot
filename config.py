"""
config.py
Bot-wide defaults and seed data.

IMPORTANT: Topic settings here are only used the FIRST time /setup is run
on a server (to seed the database). After that, all changes are made through
the /setup wizard and stored per-guild in tickets.db.

To reset a guild's topics back to these defaults, delete its rows from the
topic_configs table in tickets.db.
"""

import discord

# ══════════════════════════════════════════════════════════════
#  BOT-WIDE SETTINGS
# ══════════════════════════════════════════════════════════════

# Role IDs whose members count as "staff".
# Administrators always count as staff regardless.
STAFF_ROLE_IDS: list[int] = [
    1488175869020078121,
]

# Ticket lifecycle log channel
LOG_CHANNEL_ID: int | None = 927689571603456011

# Channel that receives a copy of every generated transcript
TRANSCRIPT_CHANNEL_ID: int | None = 927689571603456011

# Channel that receives an alert when a ticket is set to a high priority
PRIORITY_ALERT_CHANNEL_ID: int | None = 1485263612766326925

# Minimum level that triggers a priority alert: "low" | "medium" | "high" | "urgent"
PRIORITY_ALERT_MIN_LEVEL: str = "high"

# ══════════════════════════════════════════════════════════════
#  PANEL DEFAULTS
# ══════════════════════════════════════════════════════════════

PANEL_TITLE       = "📬  Support Tickets"
PANEL_DESCRIPTION = (
    "Need help? Click the button that best matches your issue below.\n"
    "A private ticket will be created for you."
)
PANEL_COLOR = 0x5865F2

# ══════════════════════════════════════════════════════════════
#  CANNED RESPONSES / SNIPPETS
# ══════════════════════════════════════════════════════════════

SNIPPETS: list[dict] = [
    {
        "name":    "Need More Information",
        "content": (
            "Thank you for reaching out! To help you as quickly as possible, "
            "could you please provide some additional details about your issue?\n\n"
            "The more context you give us, the faster we can resolve this for you."
        ),
    },
    {
        "name":    "Under Investigation",
        "content": (
            "We've received your ticket and are currently investigating the issue. "
            "We'll update you here as soon as we have more information.\n\n"
            "Thank you for your patience!"
        ),
    },
    {
        "name":    "Resolved — Please Confirm",
        "content": (
            "We believe your issue has been resolved. Could you please confirm "
            "that everything is working as expected?\n\n"
            "If you're satisfied, feel free to close this ticket using the "
            "**Close Ticket** button above. If the problem persists, just let us know!"
        ),
    },
    {
        "name":    "Escalating to Senior Staff",
        "content": (
            "Your ticket has been escalated to a senior staff member for further review. "
            "Please allow some additional time for a thorough response.\n\n"
            "We appreciate your patience."
        ),
    },
    {
        "name":    "Duplicate Ticket",
        "content": (
            "It looks like this issue is already being tracked in another ticket. "
            "We'll be closing this one to keep things organised, "
            "but your concern is on our radar!"
        ),
    },
    {
        "name":    "No Response — Closing Soon",
        "content": (
            "We haven't heard back from you in a while. "
            "If you still need assistance, please reply here within **24 hours** "
            "or this ticket will be automatically closed.\n\n"
            "You can always open a new ticket if you need help in the future!"
        ),
    },
]

# ══════════════════════════════════════════════════════════════
#  DEFAULT TICKET TOPICS  (seed data — see note at top of file)
# ══════════════════════════════════════════════════════════════

TICKET_TOPICS: list[dict] = [
    {
        "id":             "support",
        "label":          "General Support",
        "emoji":          "<:ticket:1488175544205054192>",
        "button_style":   discord.ButtonStyle.primary,
        "category_id":    0,
        "channel_prefix": "support",
        "required_roles": [],
        "welcome_message": (
            "Thanks for opening a support ticket!\n"
            "A staff member will be with you shortly."
        ),
        "questions": [
            {
                "label":       "What do you need help with?",
                "placeholder": "Briefly describe your issue...",
                "required":    True,
                "long":        False,
                "max_length":  200,
            },
            {
                "label":       "Additional details",
                "placeholder": "Any extra context, screenshots, steps to reproduce...",
                "required":    False,
                "long":        True,
                "max_length":  1000,
            },
        ],
        "category_permissions": [],
    },
    {
        "id":             "missing transaction",
        "label":          "Missing Transaction",
        "emoji":          "<:a_button_caution:1488163736085860403>",
        "button_style":   discord.ButtonStyle.success,
        "category_id":    0,
        "channel_prefix": "missing transaction",
        "required_roles": [],
        "welcome_message": (
            "Thanks for reaching out about a billing issue!\n"
            "Please give us a moment to review your information."
        ),
        "questions": [
            {
                "label":       "Transaction Hash",
                "placeholder": "e.g. TXN-123456",
                "required":    True,
                "long":        False,
                "max_length":  100,
            },
            {
                "label":       "What Cryptocurrency was sent?",
                "placeholder": "i.e. BTC",
                "required":    True,
                "long":        True,
                "max_length":  25,
            },
            {
                "label":       "What was the amount sent?",
                "placeholder": "Please use currency amount, not USD value",
                "required":    True,
                "long":        True,
                "max_length":  25,
            },
            {
                "label":       "Date and Time of Transaction, including timezone",
                "placeholder": "11/13/25 3:45 PM PST, February 13th 2025 4:43AM CST",
                "required":    True,
                "long":        True,
                "max_length":  100,
            },
        ],
        "category_permissions": [],
    },
    {
        "id":             "listing",
        "label":          "Token Listing Request",
        "emoji":          "<:Gold_coins:1488163770894254160>",
        "button_style":   discord.ButtonStyle.danger,
        "category_id":    0,
        "channel_prefix": "listing request",
        "required_roles": [],
        "welcome_message": (
            "Thank you for you interest in listing a token with dwallet.\n"
            "Please provide as much information as you are able."
        ),
        "questions": [
            {
                "label":       "What token would you like to see listed?",
                "placeholder": "e.g. Litecoin (LTC)`, `Dogecoin (DOGE)`, etc.",
                "required":    True,
                "long":        False,
                "max_length":  200,
            },
            {
                "label":       "What network is the token on?",
                "placeholder": "What did this user do?",
                "required":    True,
                "long":        True,
                "max_length":  800,
            },
            {
                "label":       "Please provide information about the token and one of the owners will be with you whenever they are available.",
                "placeholder": "Links, listings on other exchanges, descriptions, etc.",
                "required":    False,
                "long":        True,
                "max_length":  800,
            },
        ],
        "category_permissions": [],
    },
    {
        "id":             "cluster",
        "label":          "Cluster B Request",
        "emoji":          "⚖️",
        "button_style":   discord.ButtonStyle.secondary,
        "category_id":    0,
        "channel_prefix": "waitlist",
        "required_roles": [
            1488175994870169680,  # Cluster B Access
        ],
        "welcome_message": (
            "One of our team members will be with you shortly.\n"
            "Please be patient, as it may take some time before new requests can be accepted."
        ),
        "category_permissions": [],
    },
]

# ══════════════════════════════════════════════════════════════
#  PRIORITY
# ══════════════════════════════════════════════════════════════

PRIORITY_EMOJI: dict[str, str] = {
    "none":   "",
    "low":    "🟢",
    "medium": "🟡",
    "high":   "🟠",
    "urgent": "🔴",
}

PRIORITY_ORDER: list[str] = ["none", "low", "medium", "high", "urgent"]

# ══════════════════════════════════════════════════════════════
#  COLOURS
# ══════════════════════════════════════════════════════════════

TICKET_COLOR   = 0x5865F2
CLOSED_COLOR   = 0xED4245
REOPENED_COLOR = 0x57F287
MOVED_COLOR    = 0xFEE75C
NOTE_COLOR     = 0xEB459E
PRIORITY_COLOR = 0xFEE75C
