from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import aiohttp
import discord
from discord.ext import commands

from petezah.config import DISCORD_TOKEN, LOG_FILE, PREFIX, SUPERUSER_ID
from petezah.cogs import EXTENSIONS
from petezah.sanitize import clean
from petezah.store import Store


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("petezah")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s:%(levelname)s:%(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


class PeteZahBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.moderation = True
        intents.guilds = True
        super().__init__(
            command_prefix=PREFIX,
            intents=intents,
            owner_ids={SUPERUSER_ID},
            help_command=None,
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False),
        )
        self.store = Store()
        self.http_session: aiohttp.ClientSession | None = None
        self.log = _build_logger()
        self._adopted = False

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession()
        await self.store.load()
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        try:
            await self.tree.sync()
        except discord.HTTPException as exc:
            self.log.warning(clean(str(exc), strip_mentions=True, limit=300))

    async def _adopt_orphans(self) -> None:
        changed = False
        mapping: dict[int, int] = {}
        for guild in self.guilds:
            for ch in guild.channels:
                mapping[ch.id] = guild.id
        for cid in self.store.orphan_ai:
            gid = mapping.get(cid)
            if gid:
                g = self.store.guild(gid)
                if cid not in g["ai_channels"]:
                    g["ai_channels"].append(cid)
                    changed = True
        for cid in self.store.orphan_security:
            gid = mapping.get(cid)
            if gid:
                g = self.store.guild(gid)
                if cid not in g["invite_filter_channels"]:
                    g["invite_filter_channels"].append(cid)
                    changed = True
        for cid, msg in self.store.orphan_welcome:
            gid = mapping.get(cid)
            if gid:
                g = self.store.guild(gid)
                g["welcome_channel_id"] = cid
                g["welcome_message"] = msg
                changed = True
        for cid in self.store.orphan_locked:
            gid = mapping.get(cid)
            if gid:
                g = self.store.guild(gid)
                g["locked"].setdefault(str(cid), {"roles": {}, "staff": {}})
                changed = True
        self.store.orphan_ai = []
        self.store.orphan_security = []
        self.store.orphan_welcome = []
        self.store.orphan_locked = []
        if changed:
            await self.store.save()

    async def on_ready(self) -> None:
        if not self._adopted:
            await self._adopt_orphans()
            self._adopted = True
        await self.change_presence(activity=discord.Game(name="PeteZahBot | p!help"))
        self.log.info("ready")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        await self.process_commands(message)

    async def close(self) -> None:
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()


def main() -> None:
    bot = PeteZahBot()
    bot.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
