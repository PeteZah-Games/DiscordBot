from __future__ import annotations

from typing import Any, Callable

import discord
from discord import app_commands
from discord.ext import commands

from petezah.config import SUPERUSER_ID


def is_superuser(user_id: int) -> bool:
    return user_id == SUPERUSER_ID


def is_guild_admin(member: discord.Member) -> bool:
    if is_superuser(member.id):
        return True
    if member.guild.owner_id == member.id:
        return True
    return bool(member.guild_permissions.administrator)


def is_staff(member: discord.Member, cfg: dict[str, Any] | None = None) -> bool:
    if is_superuser(member.id):
        return True
    if member.guild.owner_id == member.id:
        return True
    if member.guild_permissions.administrator:
        return True
    if cfg is None:
        return False
    staff_ids = set(cfg.get("staff_role_ids") or [])
    return any(role.id in staff_ids for role in member.roles)


def has_perm_or_staff(member: discord.Member, cfg: dict[str, Any], perm: str) -> bool:
    if is_superuser(member.id):
        return True
    if member.guild.owner_id == member.id:
        return True
    if getattr(member.guild_permissions, perm, False):
        return True
    return is_staff(member, cfg)


def staff_immune(member: discord.Member, cfg: dict[str, Any]) -> bool:
    if is_superuser(member.id):
        return True
    if member.guild.owner_id == member.id:
        return True
    staff_ids = set(cfg.get("staff_role_ids") or [])
    if any(role.id in staff_ids for role in member.roles):
        return True
    return bool(member.guild_permissions.administrator)


def can_moderate(
    actor: discord.Member,
    target: discord.Member,
    cfg: dict[str, Any],
    *,
    allow_staff_target: bool = False,
) -> str | None:
    me = target.guild.me
    if target.id == SUPERUSER_ID:
        return "PeteZah is immune."
    if target.id == target.guild.owner_id and not is_superuser(actor.id):
        return "You cannot moderate the server owner."
    if target.id == actor.id:
        return "You cannot moderate yourself."
    if me and target.id == me.id:
        return "You cannot moderate the bot."
    if target.bot and not is_superuser(actor.id):
        if not allow_staff_target:
            pass
    if staff_immune(target, cfg) and not is_superuser(actor.id):
        return "You cannot moderate staff."
    if not is_superuser(actor.id) and actor.id != actor.guild.owner_id:
        if target.top_role >= actor.top_role:
            return "You cannot moderate someone with an equal or higher role."
    if me and target.top_role >= me.top_role and target.id != target.guild.owner_id:
        return "My role is too low to moderate that member. Move my role to the top."
    if target.guild_permissions.administrator and not is_superuser(actor.id) and not allow_staff_target:
        if not actor.guild_permissions.administrator and actor.id != actor.guild.owner_id:
            return "That member has Administrator and cannot be moderated that way."
    return None


def admin_only() -> Callable:
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            raise commands.NoPrivateMessage()
        if is_guild_admin(ctx.author):
            return True
        raise commands.MissingPermissions(["administrator"])

    return commands.check(predicate)


def staff_only(*perms: str) -> Callable:
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            raise commands.NoPrivateMessage()
        cfg = ctx.bot.store.guild(ctx.guild.id)
        if not perms:
            if is_staff(ctx.author, cfg) or is_guild_admin(ctx.author):
                return True
            raise commands.MissingPermissions(["moderate_members"])
        for perm in perms:
            if has_perm_or_staff(ctx.author, cfg, perm):
                return True
        raise commands.MissingPermissions(list(perms))

    return commands.check(predicate)


def superuser_only() -> Callable:
    async def predicate(ctx: commands.Context) -> bool:
        if is_superuser(ctx.author.id):
            return True
        raise commands.CheckFailure("PeteZah only.")

    return commands.check(predicate)


def app_admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        return is_guild_admin(interaction.user)

    return app_commands.check(predicate)


def app_staff_only(*perms: str):
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        cfg = interaction.client.store.guild(interaction.guild.id)
        if not perms:
            return is_staff(interaction.user, cfg) or is_guild_admin(interaction.user)
        return any(has_perm_or_staff(interaction.user, cfg, p) for p in perms)

    return app_commands.check(predicate)
