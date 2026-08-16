from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from petezah.checks import admin_only, can_moderate, staff_immune, staff_only
from petezah.config import PURGE_MAX, SUPERUSER_ID, TIMEOUT_MAX_SECONDS
from petezah.discord_safe import (
    ensure_mute_role,
    lock_channel,
    log_event,
    notify_user,
    timeout_delta,
    unlock_channel,
    utcnow,
)
from petezah.duration import parse_duration
from petezah.sanitize import clean


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.expire_loop.start()

    async def cog_check(self, ctx: commands.Context) -> bool:
        return ctx.guild is not None

    def cog_unload(self) -> None:
        self.expire_loop.cancel()

    @tasks.loop(seconds=40)
    async def expire_loop(self) -> None:
        now = utcnow()
        changed = False
        for guild in self.bot.guilds:
            cfg = self.bot.store.guild(guild.id)
            remaining = []
            for action in list(cfg.get("temp_actions") or []):
                try:
                    until = datetime.fromisoformat(action["until"])
                except (KeyError, ValueError):
                    continue
                if until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)
                if until > now:
                    remaining.append(action)
                    continue
                changed = True
                kind = action.get("type")
                uid = int(action.get("user_id", 0))
                if kind == "ban":
                    try:
                        user = await self.bot.fetch_user(uid)
                        await guild.unban(user, reason="Temporary ban expired")
                        await notify_user(user, "unbanned", "Temporary ban expired", guild=guild)
                    except discord.HTTPException:
                        pass
                elif kind == "mute":
                    member = guild.get_member(uid)
                    mute = guild.get_role(int(cfg["mute_role_id"])) if cfg.get("mute_role_id") else None
                    if member and mute and mute in member.roles:
                        try:
                            await member.remove_roles(mute, reason="Temporary mute expired")
                            await notify_user(member, "unmuted", "Temporary mute expired", guild=guild)
                        except discord.HTTPException:
                            pass
                await asyncio.sleep(0.4)
            cfg["temp_actions"] = remaining
        if changed:
            await self.bot.store.save()

    @expire_loop.before_loop
    async def _before_expire(self) -> None:
        await self.bot.wait_until_ready()

    async def _guard(self, ctx: commands.Context, member: discord.Member, *, staff_ok: bool = False) -> bool:
        cfg = self.bot.store.guild(ctx.guild.id)
        err = can_moderate(ctx.author, member, cfg, allow_staff_target=staff_ok)
        if err:
            await ctx.send(err)
            return False
        return True

    @commands.command()
    @staff_only("ban_members")
    async def ban(self, ctx: commands.Context, member: discord.Member, duration: str | None = None, *, reason: str | None = None) -> None:
        if not await self._guard(ctx, member):
            return
        seconds, duration_text = parse_duration(duration)
        if duration and seconds is None:
            reason = f"{duration} {reason}".strip() if reason else duration
            seconds, duration_text = None, None
        reason = clean(reason or "None", limit=400)
        await notify_user(member, "banned", reason, duration_text, guild=ctx.guild)
        await member.ban(reason=reason, delete_message_seconds=0)
        extra = f" Duration: {duration_text}." if duration_text else ""
        await ctx.send(clean(f"{member} has been banned.{extra} Reason: {reason}"))
        cfg = self.bot.store.guild(ctx.guild.id)
        await log_event(ctx.guild, cfg, "User banned", f"{member} ({member.id}) by {ctx.author.mention}. {extra} Reason: {reason}")
        if seconds:
            until = utcnow().timestamp() + seconds
            cfg.setdefault("temp_actions", []).append(
                {"type": "ban", "user_id": member.id, "until": datetime.fromtimestamp(until, tz=timezone.utc).isoformat()}
            )
            await self.bot.store.save()

    @commands.command()
    @staff_only("ban_members")
    async def unban(self, ctx: commands.Context, user_id: int, *, reason: str | None = None) -> None:
        reason = clean(reason or "None", limit=400)
        user = await self.bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)
        await ctx.send(f"{user} has been unbanned. Reason: {reason}")
        cfg = self.bot.store.guild(ctx.guild.id)
        await log_event(ctx.guild, cfg, "User unbanned", f"{user} ({user.id}) by {ctx.author.mention}. Reason: {reason}")

    @commands.command()
    @staff_only("kick_members")
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str | None = None) -> None:
        if not await self._guard(ctx, member):
            return
        reason = clean(reason or "None", limit=400)
        await notify_user(member, "kicked", reason, guild=ctx.guild)
        await member.kick(reason=reason)
        await ctx.send(f"{member} has been kicked. Reason: {reason}")
        cfg = self.bot.store.guild(ctx.guild.id)
        await log_event(ctx.guild, cfg, "User kicked", f"{member.mention} by {ctx.author.mention}. Reason: {reason}")

    async def _apply_mute(self, guild: discord.Guild, member: discord.Member, seconds: int | None, reason: str) -> str | None:
        cfg = self.bot.store.guild(guild.id)
        mute = await ensure_mute_role(guild, cfg)
        if mute is None:
            return "I cannot create or find a mute role. Run /setup and put my role at the top."
        try:
            await member.add_roles(mute, reason=reason)
        except discord.Forbidden:
            return "I cannot assign the mute role. Move my role above it and above the member."
        timed = min(seconds or TIMEOUT_MAX_SECONDS, TIMEOUT_MAX_SECONDS)
        try:
            await member.timeout(timeout_delta(timed), reason=reason)
        except discord.Forbidden:
            pass
        if seconds:
            until = utcnow().timestamp() + seconds
            cfg.setdefault("temp_actions", []).append(
                {"type": "mute", "user_id": member.id, "until": datetime.fromtimestamp(until, tz=timezone.utc).isoformat()}
            )
        await self.bot.store.save()
        return None

    @commands.command()
    @staff_only("moderate_members")
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: str | None = None, *, reason: str | None = None) -> None:
        if not await self._guard(ctx, member):
            return
        seconds, duration_text = parse_duration(duration)
        if duration and seconds is None:
            reason = f"{duration} {reason}".strip() if reason else duration
            seconds, duration_text = None, None
        reason = clean(reason or "None", limit=400)
        err = await self._apply_mute(ctx.guild, member, seconds, reason)
        if err:
            await ctx.send(err)
            return
        await notify_user(member, "muted", reason, duration_text, guild=ctx.guild)
        extra = f" Duration: {duration_text}." if duration_text else " (until unmuted)"
        await ctx.send(f"{member.mention} has been muted.{extra} Reason: {reason}")
        cfg = self.bot.store.guild(ctx.guild.id)
        await log_event(ctx.guild, cfg, "User muted", f"{member.mention} by {ctx.author.mention}.{extra} Reason: {reason}")

    @commands.command()
    @staff_only("moderate_members")
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str | None = None) -> None:
        if member.id == SUPERUSER_ID and ctx.author.id != SUPERUSER_ID:
            await ctx.send("No.")
            return
        cfg = self.bot.store.guild(ctx.guild.id)
        mute = ctx.guild.get_role(int(cfg["mute_role_id"])) if cfg.get("mute_role_id") else discord.utils.get(ctx.guild.roles, name="Muted")
        reason = clean(reason or "None", limit=400)
        did = False
        if mute and mute in member.roles:
            await member.remove_roles(mute, reason=reason)
            did = True
        if member.is_timed_out():
            try:
                await member.timeout(None, reason=reason)
                did = True
            except discord.Forbidden:
                pass
        cfg["temp_actions"] = [a for a in cfg.get("temp_actions") or [] if not (a.get("type") == "mute" and int(a.get("user_id", 0)) == member.id)]
        await self.bot.store.save()
        if not did:
            await ctx.send(f"{member.mention} is not muted or timed out.")
            return
        await notify_user(member, "unmuted", reason, guild=ctx.guild)
        await ctx.send(f"{member.mention} has been unmuted. Reason: {reason}")
        await log_event(ctx.guild, cfg, "User unmuted", f"{member.mention} by {ctx.author.mention}. Reason: {reason}")

    @commands.command()
    @staff_only("moderate_members")
    async def timeout(self, ctx: commands.Context, member: discord.Member, duration: str, *, reason: str | None = None) -> None:
        if not await self._guard(ctx, member):
            return
        seconds, duration_text = parse_duration(duration)
        if seconds is None:
            await ctx.send(duration_text or "Provide a duration like 10m or 2h.")
            return
        if seconds > TIMEOUT_MAX_SECONDS:
            await ctx.send("Discord timeouts cannot exceed 28 days. Use mute for longer restrictions.")
            return
        reason = clean(reason or "None", limit=400)
        try:
            await member.timeout(timeout_delta(seconds), reason=reason)
        except discord.Forbidden:
            await ctx.send("I cannot timeout that member. They may have Administrator, or my role is too low.")
            return
        await notify_user(member, "timed out", reason, duration_text, guild=ctx.guild)
        await ctx.send(f"{member.mention} has been timed out for {duration_text}. Reason: {reason}")
        cfg = self.bot.store.guild(ctx.guild.id)
        await log_event(ctx.guild, cfg, "User timed out", f"{member.mention} by {ctx.author.mention} for {duration_text}. Reason: {reason}")

    @commands.command()
    @staff_only("moderate_members")
    async def untimeout(self, ctx: commands.Context, member: discord.Member, *, reason: str | None = None) -> None:
        reason = clean(reason or "None", limit=400)
        if not member.is_timed_out():
            await ctx.send(f"{member.mention} is not timed out.")
            return
        try:
            await member.timeout(None, reason=reason)
        except discord.Forbidden:
            await ctx.send("I cannot remove that timeout.")
            return
        await ctx.send(f"{member.mention} is no longer timed out. Reason: {reason}")
        cfg = self.bot.store.guild(ctx.guild.id)
        await log_event(ctx.guild, cfg, "Timeout removed", f"{member.mention} by {ctx.author.mention}. Reason: {reason}")

    @commands.command()
    @staff_only("manage_messages")
    async def purge(self, ctx: commands.Context, amount: int) -> None:
        if amount < 1 or amount > PURGE_MAX:
            await ctx.send(f"Choose a number between 1 and {PURGE_MAX}.")
            return
        deleted = await ctx.channel.purge(limit=amount + 1)
        await ctx.send(f"Purged {max(len(deleted) - 1, 0)} messages.", delete_after=5)
        cfg = self.bot.store.guild(ctx.guild.id)
        await log_event(ctx.guild, cfg, "Messages purged", f"{amount} messages by {ctx.author.mention} in {ctx.channel.mention}")

    @commands.command()
    @admin_only()
    async def lock(self, ctx: commands.Context, *, reason: str | None = None) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        if str(ctx.channel.id) in (cfg.get("locked") or {}):
            await ctx.send("This channel is already locked.")
            return
        reason = clean(reason or "None", limit=400)
        status = await ctx.send("Locking this channel. Applying role overwrites slowly so Discord does not rate-limit the bot...")
        saved = await lock_channel(ctx.channel, cfg, reason=f"Lock by {ctx.author}")
        cfg.setdefault("locked", {})[str(ctx.channel.id)] = saved
        await self.bot.store.save()
        await status.edit(content=f"Channel locked. Members cannot send messages. Staff can still talk. Reason: {reason}")
        await log_event(ctx.guild, cfg, "Channel locked", f"{ctx.channel.mention} by {ctx.author.mention}. Reason: {reason}")

    @commands.command()
    @admin_only()
    async def unlock(self, ctx: commands.Context, *, reason: str | None = None) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        saved = (cfg.get("locked") or {}).get(str(ctx.channel.id))
        if saved is None:
            await ctx.send("This channel is not locked.")
            return
        reason = clean(reason or "None", limit=400)
        status = await ctx.send("Unlocking this channel...")
        await unlock_channel(ctx.channel, saved, reason=f"Unlock by {ctx.author}")
        cfg["locked"].pop(str(ctx.channel.id), None)
        await self.bot.store.save()
        await status.edit(content=f"Channel unlocked. Reason: {reason}")
        await log_event(ctx.guild, cfg, "Channel unlocked", f"{ctx.channel.mention} by {ctx.author.mention}. Reason: {reason}")

    @commands.command()
    @staff_only("manage_messages")
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str | None = None) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        if staff_immune(member, cfg) and ctx.author.id != SUPERUSER_ID:
            await ctx.send("You cannot warn staff.")
            return
        if not await self._guard(ctx, member):
            return
        reason = clean(reason or "None", limit=400)
        warns = self.bot.store.warnings(ctx.guild.id)
        bucket = warns.setdefault(str(member.id), [])
        bucket.append({"reason": reason, "timestamp": _iso(utcnow()), "moderator_id": ctx.author.id})
        await self.bot.store.save()
        await notify_user(member, "warned", reason, guild=ctx.guild)
        await ctx.send(f"{member.mention} has been warned. Reason: {reason}")
        await log_event(ctx.guild, cfg, "User warned", f"{member.mention} by {ctx.author.mention}. Reason: {reason}")

    @commands.command()
    async def warns(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        member = member or ctx.author
        warns = self.bot.store.warnings(ctx.guild.id).get(str(member.id), [])
        if not warns:
            await ctx.send(f"{member.mention} has no warnings.")
            return
        embed = discord.Embed(title=f"Warnings for {member}", colour=discord.Colour.red())
        for i, warning in enumerate(warns[:15], 1):
            embed.add_field(name=f"Warning {i}", value=clean(f"{warning.get('reason')}\n{warning.get('timestamp')}", limit=300), inline=False)
        await ctx.send(embed=embed)

    @commands.command()
    @staff_only("manage_messages")
    async def clearwarnings(self, ctx: commands.Context, member: discord.Member) -> None:
        warns = self.bot.store.warnings(ctx.guild.id)
        if str(member.id) in warns:
            del warns[str(member.id)]
            await self.bot.store.save()
            await ctx.send(f"Warnings cleared for {member.mention}.")
            cfg = self.bot.store.guild(ctx.guild.id)
            await log_event(ctx.guild, cfg, "Warnings cleared", f"{member.mention} by {ctx.author.mention}")
        else:
            await ctx.send(f"{member.mention} has no warnings.")

    @commands.command()
    @staff_only("manage_messages")
    async def modlogs(self, ctx: commands.Context, member: discord.Member) -> None:
        warns = self.bot.store.warnings(ctx.guild.id).get(str(member.id), [])
        embed = discord.Embed(title=f"Moderation logs for {member}", colour=discord.Colour.red())
        if warns:
            for i, warning in enumerate(warns[:10], 1):
                embed.add_field(name=f"Warn {i}", value=clean(f"{warning.get('reason')}\n{warning.get('timestamp')}", limit=300), inline=False)
        else:
            embed.description = "No stored warnings."
        entries = []
        try:
            async for entry in ctx.guild.audit_logs(limit=12):
                if entry.target and getattr(entry.target, "id", None) == member.id:
                    entries.append(f"{entry.action.name} by {entry.user} — {entry.reason or 'no reason'}")
                if len(entries) >= 8:
                    break
        except discord.Forbidden:
            entries.append("Missing View Audit Log permission.")
        if entries:
            embed.add_field(name="Recent audit", value=clean("\n".join(entries), limit=1000), inline=False)
        await ctx.send(embed=embed)

    @commands.command()
    @admin_only()
    async def whitelist(self, ctx: commands.Context, member: discord.Member) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        ids = cfg.setdefault("whitelist", [])
        if member.id in ids:
            ids.remove(member.id)
            await self.bot.store.save()
            await ctx.send(f"{member.mention} removed from the anti-nuke whitelist.")
            return
        ids.append(member.id)
        await self.bot.store.save()
        await ctx.send(f"{member.mention} added to the anti-nuke whitelist.")
        await log_event(ctx.guild, cfg, "Anti-nuke whitelist", f"{member.mention} updated by {ctx.author.mention}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))
