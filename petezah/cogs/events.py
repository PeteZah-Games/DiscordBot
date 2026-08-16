from __future__ import annotations

import discord
from discord.ext import commands

from petezah.checks import staff_immune
from petezah.config import PREFIX
from petezah.discord_safe import log_event, notify_user, ensure_mute_role
from petezah.sanitize import clean


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if isinstance(message.content, str) and message.content.lower().startswith(PREFIX):
            return
        cfg = self.bot.store.guild(message.guild.id)
        anti = self.bot.get_cog("AntiNukeCog")
        if anti is not None:
            if await anti.screen_invite(message):
                return
            if await anti.screen_message(message):
                return
        if await self._autowarn(message, cfg):
            return
        await self._afk(message)
        ai = self.bot.get_cog("AICog")
        if ai is not None:
            await ai.reply_to(message)
        await self._pins(message, cfg)

    async def _autowarn(self, message: discord.Message, cfg: dict) -> bool:
        if not isinstance(message.author, discord.Member):
            return False
        if staff_immune(message.author, cfg):
            return False
        rules = self.bot.store.autowarn(message.guild.id)
        content = message.content or ""
        lowered = content.lower()
        for keyword, action in rules.items():
            if keyword and keyword in lowered:
                if action == "warn":
                    warns = self.bot.store.warnings(message.guild.id)
                    bucket = warns.setdefault(str(message.author.id), [])
                    bucket.append({"reason": f"Auto-warn: {keyword}", "timestamp": discord.utils.utcnow().isoformat()})
                    await self.bot.store.save()
                    await notify_user(message.author, "warned", f"Auto-warn for keyword: {keyword}", guild=message.guild)
                    await log_event(message.guild, cfg, "Auto-warn", f"{message.author.mention} keyword `{clean(keyword, limit=40)}`")
                elif action == "mute":
                    mute = await ensure_mute_role(message.guild, cfg)
                    if mute:
                        try:
                            await message.author.add_roles(mute, reason="Auto-mute")
                        except discord.HTTPException:
                            pass
                    await notify_user(message.author, "muted", f"Auto-mute for keyword: {keyword}", guild=message.guild)
                    await log_event(message.guild, cfg, "Auto-mute", f"{message.author.mention} keyword `{clean(keyword, limit=40)}`")
                return True
        return False

    async def _afk(self, message: discord.Message) -> None:
        if str(message.author.id) in self.bot.store.afk:
            del self.bot.store.afk[str(message.author.id)]
            await self.bot.store.save()
            try:
                await message.channel.send(f"Welcome back {message.author.mention}, AFK removed.", delete_after=6)
            except discord.HTTPException:
                pass
        mentioned = []
        for user in message.mentions[:5]:
            reason = self.bot.store.afk.get(str(user.id))
            if reason:
                mentioned.append(f"{user.mention} is AFK: {clean(reason, limit=80)}")
        if mentioned:
            try:
                await message.channel.send("\n".join(mentioned), delete_after=8)
            except discord.HTTPException:
                pass

    async def _pins(self, message: discord.Message, cfg: dict) -> None:
        pin = (cfg.get("pins") or {}).get(str(message.channel.id))
        if not pin:
            return
        last_id = pin.get("last_message_id")
        if last_id:
            try:
                last = await message.channel.fetch_message(int(last_id))
                await last.delete()
            except discord.HTTPException:
                pass
        try:
            sent = await message.channel.send(clean(pin.get("content") or "", limit=1800))
            pin["last_message_id"] = sent.id
            await self.bot.store.save()
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = self.bot.store.guild(member.guild.id)
        cid = cfg.get("welcome_channel_id")
        if not cid:
            return
        channel = member.guild.get_channel(int(cid))
        if not isinstance(channel, discord.TextChannel):
            return
        extra = cfg.get("welcome_message") or ""
        try:
            await channel.send(f"Welcome {member.mention} to {member.guild.name}. {clean(extra, limit=400)}")
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._rr(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._rr(payload, add=False)

    async def _rr(self, payload: discord.RawReactionActionEvent, *, add: bool) -> None:
        if payload.user_id == (self.bot.user.id if self.bot.user else 0):
            return
        for row in self.bot.store.reaction_roles:
            if int(row.get("message_id", 0)) != payload.message_id:
                continue
            if int(row.get("guild_id", 0)) != (payload.guild_id or 0):
                continue
            if str(row.get("emoji")) != str(payload.emoji):
                continue
            guild = self.bot.get_guild(payload.guild_id)
            if not guild:
                return
            member = guild.get_member(payload.user_id)
            role = guild.get_role(int(row.get("role_id", 0)))
            if not member or not role:
                return
            try:
                if add:
                    await member.add_roles(role, reason="Reaction role")
                else:
                    await member.remove_roles(role, reason="Reaction role")
            except discord.HTTPException:
                return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventsCog(bot))
