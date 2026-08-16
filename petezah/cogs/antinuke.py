from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any

import discord
from discord.ext import commands

from petezah.checks import is_staff, is_superuser
from petezah.config import ANTINUKE_PACE, INVITE_TIMEOUT_SECONDS, SUPERUSER_ID, TIMEOUT_MAX_SECONDS
from petezah.discord_safe import (
    add_mute_overwrites_for_channel,
    assignable_roles,
    dangerous_roles,
    ensure_quarantine_role,
    lock_channel,
    log_event,
    notify_user,
    timeout_delta,
)
from petezah.sanitize import INVITE_RE, clean

THRESHOLDS = {
    "ban": (3, 12.0),
    "kick": (4, 15.0),
    "channel_create": (4, 18.0),
    "channel_delete": (3, 18.0),
    "role_create": (4, 20.0),
    "role_delete": (3, 18.0),
    "role_update": (5, 20.0),
    "admin_grant": (2, 20.0),
    "webhook_create": (3, 25.0),
    "bot_add": (3, 45.0),
    "emoji_delete": (5, 20.0),
    "guild_update": (3, 25.0),
    "everyone": (2, 20.0),
    "mass_mention": (3, 12.0),
    "msg_spam": (9, 5.0),
    "dup_spam": (6, 10.0),
}

STAFF_MULT = 3


class AntiNukeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._hits: dict[tuple[int, int, str], deque[tuple[float, Any]]] = defaultdict(deque)
        self._joins: dict[int, deque[tuple[float, int]]] = defaultdict(deque)
        self._channel_msgs: dict[int, deque[tuple[float, int]]] = defaultdict(deque)
        self._dups: dict[tuple[int, int], deque[tuple[float, str]]] = defaultdict(deque)
        self._busy: set[tuple[int, int]] = set()
        self._raid_lock: set[int] = set()

    def _cfg(self, guild_id: int) -> dict:
        return self.bot.store.guild(guild_id)

    def _tier(self, member: discord.Member | discord.User | None, guild: discord.Guild, cfg: dict) -> int:
        if member is None:
            return 1
        uid = member.id
        if is_superuser(uid) or uid == guild.owner_id or uid == (guild.me.id if guild.me else 0):
            return 3
        if uid in (cfg.get("whitelist") or []):
            return 3
        if isinstance(member, discord.Member) and is_staff(member, cfg):
            return 2
        return 1

    def _record(self, guild_id: int, user_id: int, kind: str, target: Any = None, *, window: float, limit: int) -> tuple[bool, list[Any]]:
        now = time.monotonic()
        q = self._hits[(guild_id, user_id, kind)]
        q.append((now, target))
        while q and now - q[0][0] > window:
            q.popleft()
        return len(q) >= limit, [item[1] for item in q]

    async def _trip(
        self,
        guild: discord.Guild,
        actor: discord.Member | discord.User | None,
        kind: str,
        *,
        targets: list[Any] | None = None,
        note: str = "",
        strip_all: bool = True,
    ) -> None:
        if actor is None:
            return
        cfg = self._cfg(guild.id)
        if self._tier(actor, guild, cfg) >= 3:
            return
        key = (guild.id, actor.id)
        if key in self._busy:
            return
        self._busy.add(key)
        try:
            await self._punish(guild, actor, kind, targets or [], note, strip_all)
        finally:
            self._busy.discard(key)

    async def _punish(
        self,
        guild: discord.Guild,
        actor: discord.Member | discord.User,
        kind: str,
        targets: list[Any],
        note: str,
        strip_all: bool,
    ) -> None:
        cfg = self._cfg(guild.id)
        member = guild.get_member(actor.id)
        bot_member = guild.me
        details = f"{actor} ({actor.id}) tripped **{kind}**. {clean(note, limit=300)}"
        await log_event(guild, cfg, "Anti-nuke trigger", details, colour=discord.Colour.dark_red())
        if member and bot_member:
            try:
                await member.timeout(timeout_delta(TIMEOUT_MAX_SECONDS), reason="PeteZah anti-nuke")
            except discord.HTTPException:
                pass
            qrole = await ensure_quarantine_role(guild, cfg)
            if qrole:
                try:
                    await member.add_roles(qrole, reason="PeteZah anti-nuke")
                except discord.HTTPException:
                    pass
            roles = assignable_roles(member, bot_member) if strip_all else dangerous_roles(member, bot_member)
            if roles:
                try:
                    await member.remove_roles(*roles, reason="PeteZah anti-nuke")
                except discord.HTTPException:
                    chunk = 5
                    for i in range(0, len(roles), chunk):
                        try:
                            await member.remove_roles(*roles[i : i + chunk], reason="PeteZah anti-nuke")
                        except discord.HTTPException:
                            pass
                        await asyncio.sleep(ANTINUKE_PACE)
            await notify_user(member, "quarantined", f"Anti-nuke: {kind}", guild=guild)
        await self._revert(guild, kind, targets)
        await self.bot.store.save()

    async def _revert(self, guild: discord.Guild, kind: str, targets: list[Any]) -> None:
        for target in targets:
            try:
                if kind == "ban" and target:
                    user = target if isinstance(target, discord.abc.User) else await self.bot.fetch_user(int(target))
                    await guild.unban(user, reason="PeteZah anti-nuke")
                elif kind == "channel_create" and isinstance(target, discord.abc.GuildChannel):
                    await target.delete(reason="PeteZah anti-nuke")
                elif kind == "webhook_create" and isinstance(target, discord.Webhook):
                    await target.delete(reason="PeteZah anti-nuke")
                elif kind == "bot_add":
                    mid = target.id if isinstance(target, discord.Member) else int(target)
                    member = guild.get_member(mid)
                    if member and member.bot:
                        await member.kick(reason="PeteZah anti-nuke")
                elif kind == "role_create" and isinstance(target, discord.Role):
                    await target.delete(reason="PeteZah anti-nuke")
            except (discord.HTTPException, ValueError, TypeError):
                pass
            await asyncio.sleep(ANTINUKE_PACE)

    async def maybe_action(
        self,
        guild: discord.Guild,
        actor: discord.Member | discord.User | None,
        kind: str,
        *,
        target: Any = None,
        immediate: bool = False,
        note: str = "",
        strip_all: bool | None = None,
    ) -> bool:
        cfg = self._cfg(guild.id)
        if not cfg.get("nuke_protection"):
            return False
        if actor is None:
            return False
        if actor.id == (guild.me.id if guild.me else 0):
            return False
        if getattr(actor, "bot", False) and actor.id == (guild.me.id if guild.me else 0):
            return False
        tier = self._tier(actor, guild, cfg)
        if tier >= 3:
            return False
        if immediate:
            await self._trip(guild, actor, kind, targets=[target] if target is not None else [], note=note, strip_all=strip_all if strip_all is not None else tier == 1)
            return True
        window_limit = THRESHOLDS.get(kind)
        if not window_limit:
            return False
        limit, window = window_limit
        if tier == 2:
            if kind in {"msg_spam", "dup_spam", "everyone", "mass_mention"}:
                return False
            limit = limit * STAFF_MULT
        hit, targets = self._record(guild.id, actor.id, kind, target, window=window, limit=limit)
        if hit:
            await self._trip(
                guild,
                actor,
                kind,
                targets=targets,
                note=note,
                strip_all=strip_all if strip_all is not None else tier == 1,
            )
            return True
        return False

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry) -> None:
        guild = entry.guild
        if guild is None:
            return
        cfg = self._cfg(guild.id)
        if not cfg.get("nuke_protection"):
            return
        actor = entry.user
        action = entry.action
        if action is discord.AuditLogAction.ban:
            await self.maybe_action(guild, actor, "ban", target=entry.target)
        elif action is discord.AuditLogAction.kick:
            await self.maybe_action(guild, actor, "kick", target=entry.target)
        elif action is discord.AuditLogAction.channel_create:
            await self.maybe_action(guild, actor, "channel_create", target=entry.target)
        elif action is discord.AuditLogAction.channel_delete:
            await self.maybe_action(guild, actor, "channel_delete", target=getattr(entry.target, "id", None))
        elif action is discord.AuditLogAction.role_create:
            await self.maybe_action(guild, actor, "role_create", target=entry.target)
        elif action is discord.AuditLogAction.role_delete:
            await self.maybe_action(guild, actor, "role_delete")
        elif action is discord.AuditLogAction.role_update:
            dangerous = False
            before = getattr(entry, "before", None)
            after = getattr(entry, "after", None)
            bperms = getattr(before, "permissions", None)
            aperms = getattr(after, "permissions", None)
            if aperms and getattr(aperms, "administrator", False) and not (bperms and getattr(bperms, "administrator", False)):
                dangerous = True
            if dangerous:
                await self.maybe_action(guild, actor, "admin_grant", target=entry.target, note="Administrator granted on a role")
            else:
                await self.maybe_action(guild, actor, "role_update", target=entry.target)
        elif action is discord.AuditLogAction.webhook_create:
            await self.maybe_action(guild, actor, "webhook_create", target=entry.target)
        elif action is discord.AuditLogAction.bot_add:
            await self.maybe_action(guild, actor, "bot_add", target=entry.target)
        elif action is discord.AuditLogAction.emoji_delete:
            await self.maybe_action(guild, actor, "emoji_delete")
        elif action is discord.AuditLogAction.member_prune:
            await self.maybe_action(guild, actor, "kick", immediate=True, note="Member prune", strip_all=True)
        elif action is discord.AuditLogAction.guild_update:
            before = getattr(entry, "before", None)
            after = getattr(entry, "after", None)
            vanity_before = getattr(before, "vanity_url_code", None)
            vanity_after = getattr(after, "vanity_url_code", None)
            if vanity_before != vanity_after and (vanity_before or vanity_after):
                await self.maybe_action(guild, actor, "guild_update", immediate=True, note="Vanity URL change")
            else:
                await self.maybe_action(guild, actor, "guild_update")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = self._cfg(member.guild.id)
        if member.bot or not cfg.get("nuke_protection"):
            return
        now = time.monotonic()
        q = self._joins[member.guild.id]
        q.append((now, member.id))
        while q and now - q[0][0] > 8:
            q.popleft()
        if len(q) >= 12 and member.guild.id not in self._raid_lock:
            self._raid_lock.add(member.guild.id)
            for _, uid in list(q):
                m = member.guild.get_member(uid)
                if not m:
                    continue
                age = discord.utils.utcnow() - m.created_at
                if age.total_seconds() < 86400 * 3:
                    try:
                        await m.timeout(timeout_delta(60 * 30), reason="PeteZah anti-raid")
                    except discord.HTTPException:
                        pass
                    await asyncio.sleep(ANTINUKE_PACE)
            await log_event(member.guild, cfg, "Join raid detected", f"{len(q)} joins in 8 seconds. New accounts were timed out.", colour=discord.Colour.orange())
            await asyncio.sleep(20)
            self._raid_lock.discard(member.guild.id)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        cfg = self._cfg(channel.guild.id)
        await add_mute_overwrites_for_channel(channel, cfg)

    async def screen_message(self, message: discord.Message) -> bool:
        if not message.guild or message.author.bot:
            return False
        cfg = self._cfg(message.guild.id)
        if not cfg.get("nuke_protection"):
            return False
        author = message.author
        if not isinstance(author, discord.Member):
            return False
        if self._tier(author, message.guild, cfg) >= 2:
            return False
        now = time.monotonic()
        cq = self._channel_msgs[message.channel.id]
        cq.append((now, author.id))
        while cq and now - cq[0][0] > 6:
            cq.popleft()
        unique = {u for _, u in cq}
        if len(cq) >= 18 and len(unique) >= 8 and message.channel.id not in self._raid_lock:
            self._raid_lock.add(message.channel.id)
            if str(message.channel.id) not in (cfg.get("locked") or {}):
                try:
                    saved = await lock_channel(message.channel, cfg, reason="PeteZah anti-raid")
                    cfg.setdefault("locked", {})[str(message.channel.id)] = saved
                    await self.bot.store.save()
                    await message.channel.send("Raid detected. Channel locked for staff review.", delete_after=20)
                except discord.HTTPException:
                    pass
            await log_event(message.guild, cfg, "Channel raid lock", f"{message.channel.mention} locked after spam from {len(unique)} users.")
            await asyncio.sleep(15)
            self._raid_lock.discard(message.channel.id)

        if message.mention_everyone:
            if await self.maybe_action(message.guild, author, "everyone", note="everyone/here ping"):
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                return True
        mentions = list(message.mentions) + list(message.role_mentions)
        if len(mentions) >= 6:
            if await self.maybe_action(message.guild, author, "mass_mention", note=f"{len(mentions)} mentions"):
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                return True
        if await self.maybe_action(message.guild, author, "msg_spam", target=message.id, strip_all=False):
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            return True
        if message.content:
            dq = self._dups[(message.guild.id, author.id)]
            dq.append((now, message.content[:180]))
            while dq and now - dq[0][0] > 10:
                dq.popleft()
            texts = [t for _, t in dq]
            if len(texts) >= 6 and len(set(texts)) == 1:
                if await self.maybe_action(message.guild, author, "dup_spam", immediate=True, strip_all=False, note="repeated messages"):
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
                    return True
        return False

    async def screen_invite(self, message: discord.Message) -> bool:
        if not message.guild or message.author.bot:
            return False
        cfg = self._cfg(message.guild.id)
        if not (cfg.get("invite_filter_server") or message.channel.id in (cfg.get("invite_filter_channels") or [])):
            return False
        if not isinstance(message.author, discord.Member):
            return False
        if self._tier(message.author, message.guild, cfg) >= 2:
            return False
        if not INVITE_RE.search(message.content or ""):
            return False
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        try:
            await message.author.timeout(timeout_delta(INVITE_TIMEOUT_SECONDS), reason="Posted a Discord invite")
        except discord.HTTPException:
            pass
        await notify_user(message.author, "timed out", "Posted a Discord invite link", "1 minute", guild=message.guild)
        try:
            await message.channel.send(f"{message.author.mention} timed out for posting an invite.", delete_after=6)
        except discord.HTTPException:
            pass
        await log_event(message.guild, cfg, "Invite filtered", f"{message.author.mention} in {message.channel.mention}")
        return True


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiNukeCog(bot))
