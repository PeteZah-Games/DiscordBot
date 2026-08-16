from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from petezah.config import ACTION_PACE, LOCK_PACE, SETUP_PACE, SUPERUSER_ID
from petezah.sanitize import clean


TEXT_LOCK_DENIES = {
    "send_messages": False,
    "add_reactions": False,
    "create_public_threads": False,
    "create_private_threads": False,
    "send_messages_in_threads": False,
    "send_tts_messages": False,
    "embed_links": False,
    "attach_files": False,
}

VOICE_LOCK_DENIES = {
    "speak": False,
    "stream": False,
    "use_voice_activation": False,
    "send_messages": False,
}

MUTE_DENIES = {
    "send_messages": False,
    "add_reactions": False,
    "speak": False,
    "create_public_threads": False,
    "create_private_threads": False,
    "send_messages_in_threads": False,
    "connect": False,
    "use_application_commands": False,
}

QUARANTINE_DENIES = {
    "view_channel": False,
    "send_messages": False,
    "speak": False,
    "connect": False,
    "add_reactions": False,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def pace(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def set_overwrite(
    channel: discord.abc.GuildChannel,
    target: discord.Role | discord.Member,
    *,
    reason: str,
    delay: float = ACTION_PACE,
    overwrite: discord.PermissionOverwrite | None = None,
    **perms: Any,
) -> None:
    kwargs: dict[str, Any] = {"reason": reason}
    if overwrite is not None:
        kwargs["overwrite"] = overwrite
    else:
        kwargs.update(perms)

    async def _run() -> None:
        await channel.set_permissions(target, **kwargs)

    try:
        await _run()
    except discord.HTTPException as exc:
        retry = getattr(exc, "retry_after", None)
        if retry:
            await asyncio.sleep(float(retry) + 0.4)
            try:
                await _run()
            except discord.HTTPException:
                pass
        elif isinstance(exc, discord.Forbidden):
            pass
        else:
            await asyncio.sleep(0.8)
    await asyncio.sleep(delay)


def overwrite_snapshot(ow: discord.PermissionOverwrite) -> dict[str, Any]:
    keys = (
        "send_messages",
        "add_reactions",
        "create_public_threads",
        "create_private_threads",
        "send_messages_in_threads",
        "send_tts_messages",
        "embed_links",
        "attach_files",
        "speak",
        "stream",
        "use_voice_activation",
        "connect",
        "use_application_commands",
        "view_channel",
    )
    return {k: getattr(ow, k) for k in keys}


def apply_snapshot(ow: discord.PermissionOverwrite, snap: dict[str, Any]) -> discord.PermissionOverwrite:
    for key, value in snap.items():
        if hasattr(ow, key):
            setattr(ow, key, value)
    return ow


def lockable_channels(guild: discord.Guild) -> list[discord.abc.GuildChannel]:
    out: list[discord.abc.GuildChannel] = []
    for ch in guild.channels:
        if isinstance(
            ch,
            (
                discord.TextChannel,
                discord.VoiceChannel,
                discord.ForumChannel,
                discord.StageChannel,
                discord.CategoryChannel,
            ),
        ):
            out.append(ch)
    return out


def _lock_denied_roles(guild: discord.Guild, cfg: dict[str, Any]) -> list[discord.Role]:
    roles: list[discord.Role] = [guild.default_role]
    staff_ids = set(cfg.get("staff_role_ids") or [])
    member_ids = [int(x) for x in (cfg.get("member_role_ids") or [])]
    if member_ids:
        for rid in member_ids:
            role = guild.get_role(rid)
            if role and role not in roles:
                roles.append(role)
        return roles
    me = guild.me
    for role in guild.roles:
        if role.is_default() or role.managed:
            continue
        if role.id in staff_ids:
            continue
        if role.permissions.administrator:
            continue
        if me and role >= me.top_role:
            continue
        roles.append(role)
    return roles[:30]


async def lock_channel(
    channel: discord.abc.GuildChannel,
    cfg: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    guild = channel.guild
    saved: dict[str, Any] = {"roles": {}, "staff": {}}
    deny = dict(TEXT_LOCK_DENIES)
    if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        deny.update(VOICE_LOCK_DENIES)
    for role in _lock_denied_roles(guild, cfg):
        ow = channel.overwrites_for(role)
        saved["roles"][str(role.id)] = overwrite_snapshot(ow)
        await set_overwrite(channel, role, reason=reason, delay=LOCK_PACE, **deny)
    for rid in cfg.get("staff_role_ids") or []:
        role = guild.get_role(int(rid))
        if not role:
            continue
        ow = channel.overwrites_for(role)
        saved["staff"][str(role.id)] = overwrite_snapshot(ow)
        await set_overwrite(
            channel,
            role,
            reason=reason,
            delay=LOCK_PACE,
            send_messages=True,
            send_messages_in_threads=True,
            add_reactions=True,
            speak=True,
        )
    member = guild.get_member(SUPERUSER_ID)
    if member:
        ow = channel.overwrites_for(member)
        saved["superuser"] = overwrite_snapshot(ow)
        await set_overwrite(
            channel,
            member,
            reason=reason,
            delay=LOCK_PACE,
            send_messages=True,
            speak=True,
        )
    return saved


async def unlock_channel(
    channel: discord.abc.GuildChannel,
    saved: dict[str, Any],
    *,
    reason: str,
) -> None:
    guild = channel.guild
    for rid, snap in (saved.get("roles") or {}).items():
        role = guild.get_role(int(rid))
        if not role:
            continue
        ow = apply_snapshot(channel.overwrites_for(role), snap)
        await set_overwrite(channel, role, reason=reason, delay=LOCK_PACE, overwrite=ow)
    for rid, snap in (saved.get("staff") or {}).items():
        role = guild.get_role(int(rid))
        if not role:
            continue
        ow = apply_snapshot(channel.overwrites_for(role), snap)
        await set_overwrite(channel, role, reason=reason, delay=LOCK_PACE, overwrite=ow)
    if saved.get("superuser") is not None:
        member = guild.get_member(SUPERUSER_ID)
        if member:
            ow = apply_snapshot(channel.overwrites_for(member), saved["superuser"])
            await set_overwrite(channel, member, reason=reason, delay=LOCK_PACE, overwrite=ow)


async def apply_role_overwrites(
    guild: discord.Guild,
    role: discord.Role,
    perms: dict[str, Any],
    *,
    reason: str,
    delay: float = SETUP_PACE,
    skip_synced: bool = True,
    progress=None,
) -> None:
    channels = lockable_channels(guild)
    total = len(channels)
    done = 0
    for ch in channels:
        if skip_synced and getattr(ch, "permissions_synced", False) and not isinstance(ch, discord.CategoryChannel):
            done += 1
            continue
        await set_overwrite(ch, role, reason=reason, delay=delay, **perms)
        done += 1
        if progress and done % 4 == 0:
            await progress(done, total)


async def ensure_mute_role(guild: discord.Guild, cfg: dict[str, Any]) -> discord.Role | None:
    rid = cfg.get("mute_role_id")
    role = guild.get_role(int(rid)) if rid else None
    if role:
        return role
    me = guild.me
    if not me or not me.guild_permissions.manage_roles:
        return None
    role = await guild.create_role(
        name="Muted",
        permissions=discord.Permissions.none(),
        colour=discord.Colour.dark_grey(),
        reason="PeteZah mute role",
    )
    await asyncio.sleep(0.8)
    try:
        pos = max(me.top_role.position - 1, 1)
        await role.edit(position=pos, reason="PeteZah mute role")
    except discord.HTTPException:
        pass
    cfg["mute_role_id"] = role.id
    return role


async def ensure_quarantine_role(guild: discord.Guild, cfg: dict[str, Any]) -> discord.Role | None:
    rid = cfg.get("quarantine_role_id")
    role = guild.get_role(int(rid)) if rid else None
    if role:
        return role
    me = guild.me
    if not me or not me.guild_permissions.manage_roles:
        return None
    role = await guild.create_role(
        name="Quarantine",
        permissions=discord.Permissions.none(),
        colour=discord.Colour.red(),
        hoist=True,
        reason="PeteZah quarantine role",
    )
    await asyncio.sleep(0.8)
    try:
        pos = max(me.top_role.position - 1, 1)
        await role.edit(position=pos, reason="PeteZah quarantine role")
    except discord.HTTPException:
        pass
    cfg["quarantine_role_id"] = role.id
    return role


async def add_mute_overwrites_for_channel(channel: discord.abc.GuildChannel, cfg: dict[str, Any]) -> None:
    guild = channel.guild
    mute_id = cfg.get("mute_role_id")
    if mute_id:
        role = guild.get_role(int(mute_id))
        if role:
            await set_overwrite(channel, role, reason="PeteZah mute overwrite", delay=0.4, **MUTE_DENIES)
    qid = cfg.get("quarantine_role_id")
    if qid:
        role = guild.get_role(int(qid))
        if role:
            await set_overwrite(channel, role, reason="PeteZah quarantine overwrite", delay=0.4, **QUARANTINE_DENIES)


async def notify_user(
    member: discord.abc.User,
    action: str,
    reason: str | None = None,
    duration: str | None = None,
    guild: discord.Guild | None = None,
) -> bool:
    embed = discord.Embed(title=f"You have been {action}", colour=discord.Colour.red(), timestamp=utcnow())
    if guild:
        embed.add_field(name="Server", value=guild.name, inline=False)
    if reason:
        embed.add_field(name="Reason", value=clean(reason, limit=500), inline=False)
    if duration:
        embed.add_field(name="Duration", value=duration, inline=False)
    try:
        await member.send(embed=embed)
        return True
    except discord.HTTPException:
        return False


async def log_event(guild: discord.Guild, cfg: dict[str, Any], title: str, details: str, colour: discord.Colour | None = None) -> None:
    cid = cfg.get("log_channel_id")
    channel = guild.get_channel(int(cid)) if cid else None
    if channel is None:
        channel = guild.system_channel
    if not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed(
        title=clean(title, strip_mentions=True, limit=250),
        description=clean(details, strip_mentions=True, limit=1800),
        colour=colour or discord.Colour.red(),
        timestamp=utcnow(),
    )
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


def timeout_delta(seconds: int) -> timedelta:
    seconds = max(1, min(int(seconds), 28 * 24 * 3600))
    return timedelta(seconds=seconds)


DANGEROUS_PERMS = (
    "administrator",
    "ban_members",
    "kick_members",
    "manage_channels",
    "manage_roles",
    "manage_guild",
    "manage_webhooks",
    "moderate_members",
    "mention_everyone",
    "manage_nicknames",
)


def assignable_roles(member: discord.Member, bot_member: discord.Member) -> list[discord.Role]:
    out = []
    for role in member.roles:
        if role.is_default() or role.managed:
            continue
        if role >= bot_member.top_role:
            continue
        if not role.is_assignable():
            continue
        out.append(role)
    return out


def dangerous_roles(member: discord.Member, bot_member: discord.Member) -> list[discord.Role]:
    out = []
    for role in assignable_roles(member, bot_member):
        perms = role.permissions
        if any(getattr(perms, name, False) for name in DANGEROUS_PERMS):
            out.append(role)
    return out
