"""
bot.py
Entry point for the Support Bot.
"""

import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()


class TicketBot(commands.Bot):
    async def setup_hook(self) -> None:
        await self.load_extension("cogs.tickets")  # DB init, persistent views, ticket lifecycle
        await self.load_extension("cogs.staff")     # /snippet /note /priority /blacklist /stats /findticket /listopen
        await self.load_extension("cogs.setup")     # /setup wizard
        await self.load_extension("cogs.help")      # /help
        synced = await self.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user}  (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="for support tickets",
            )
        )


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Add it to your .env file.")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = TicketBot(command_prefix="!", intents=intents)
    bot.run(token)


if __name__ == "__main__":
    main()
