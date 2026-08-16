from __future__ import annotations

import discord
from discord.ext import commands

from petezah.checks import admin_only
from petezah.config import SETUP_PACE
from petezah.discord_safe import (
    MUTE_DENIES,
    QUARANTINE_DENIES,
    apply_role_overwrites,
    log_event,
    pace,
)
from petezah.sanitize import clean


class UserBoundView(discord.ui.View):
    def __init__(self, user_id: int, timeout: float = 240) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.cancelled = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This prompt is not for you.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        self.cancelled = True


class ContinueView(UserBoundView):
    def __init__(self, user_id: int) -> None:
        super().__init__(user_id)
        self.ok = False

    @discord.ui.button(label="Start setup", style=discord.ButtonStyle.green)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.ok = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.cancelled = True
        await interaction.response.defer()
        self.stop()


class RoleStepView(UserBoundView):
    def __init__(self, user_id: int, placeholder: str, min_values: int, max_values: int, allow_skip: bool = False) -> None:
        super().__init__(user_id)
        self.picked: list[discord.Role] = []
        self.allow_skip = allow_skip
        self.skipped = False
        self.select = discord.ui.RoleSelect(
            placeholder=placeholder,
            min_values=max(min_values, 0 if allow_skip else min_values),
            max_values=max_values,
        )
        self.select.callback = self._picked
        self.add_item(self.select)

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.picked = list(self.select.values)
        await interaction.response.defer()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def nxt(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.select.values:
            self.picked = list(self.select.values)
        if not self.picked and not self.allow_skip:
            await interaction.response.send_message("Select at least one role, then press Next.", ephemeral=True)
            return
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, row=1)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.allow_skip:
            await interaction.response.send_message("This step cannot be skipped.", ephemeral=True)
            return
        self.skipped = True
        await interaction.response.defer()
        self.stop()


class MuteRoleView(UserBoundView):
    def __init__(self, user_id: int) -> None:
        super().__init__(user_id)
        self.role: discord.Role | None = None
        self.create = False
        self.select = discord.ui.RoleSelect(placeholder="Existing mute role (optional)", min_values=0, max_values=1)
        self.select.callback = self._picked
        self.add_item(self.select)

    async def _picked(self, interaction: discord.Interaction) -> None:
        if self.select.values:
            self.role = self.select.values[0]
        await interaction.response.defer()

    @discord.ui.button(label="Create Muted role", style=discord.ButtonStyle.green, row=1)
    async def make(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.create = True
        self.role = None
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Use selected role", style=discord.ButtonStyle.primary, row=1)
    async def use(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.select.values:
            self.role = self.select.values[0]
        if self.role is None:
            await interaction.response.send_message("Select a role or create a new one.", ephemeral=True)
            return
        await interaction.response.defer()
        self.stop()


class ChannelStepView(UserBoundView):
    def __init__(self, user_id: int) -> None:
        super().__init__(user_id)
        self.channel: discord.abc.GuildChannel | None = None
        self.select = discord.ui.ChannelSelect(
            placeholder="Moderation log channel",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
        )
        self.select.callback = self._picked
        self.add_item(self.select)

    async def _picked(self, interaction: discord.Interaction) -> None:
        if self.select.values:
            self.channel = self.select.values[0]
        await interaction.response.defer()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, row=1)
    async def nxt(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.select.values:
            self.channel = self.select.values[0]
        if self.channel is None:
            await interaction.response.send_message("Select a log channel, then press Next.", ephemeral=True)
            return
        await interaction.response.defer()
        self.stop()


class ToggleView(UserBoundView):
    def __init__(self, user_id: int, yes_label: str, no_label: str) -> None:
        super().__init__(user_id)
        self.value: bool | None = None
        self._yes.label = yes_label
        self._no.label = no_label

    @discord.ui.button(label="Enable", style=discord.ButtonStyle.green)
    async def _yes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Disable", style=discord.ButtonStyle.red)
    async def _no(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = False
        await interaction.response.defer()
        self.stop()


class SetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._busy: set[int] = set()

    async def cog_check(self, ctx: commands.Context) -> bool:
        return ctx.guild is not None

    async def _begin(self, guild: discord.Guild, actor: discord.Member, send, edit_base) -> None:
        if guild.id in self._busy:
            await send("Setup is already running in this server. Finish that one first.")
            return
        self._busy.add(guild.id)
        try:
            await self._wizard(guild, actor, send, edit_base)
        finally:
            self._busy.discard(guild.id)

    async def _wizard(self, guild: discord.Guild, actor: discord.Member, send, edit_base) -> None:
        intro = discord.Embed(
            title="PeteZahBot setup",
            description=(
                "We will configure this server one step at a time so Discord does not rate-limit the bot.\n\n"
                "You will choose member roles, staff roles, a mute role, a log channel, and protection options.\n"
                "Keep the bot's role at the **top** of the role list after setup."
            ),
            colour=discord.Colour.blurple(),
        )
        view = ContinueView(actor.id)
        message = await send(embed=intro, view=view)
        await view.wait()
        if not view.ok or view.cancelled:
            await edit_base(message, embed=discord.Embed(title="Setup cancelled", colour=discord.Colour.dark_grey()), view=None)
            return

        step = discord.Embed(
            title="Step 1 — Member roles",
            description="Select the general member / verified roles that regular users have. These are the roles `p!lock` and mute will actually restrict.",
            colour=discord.Colour.blurple(),
        )
        roles_view = RoleStepView(actor.id, "Select member roles", 1, 15)
        await edit_base(message, embed=step, view=roles_view)
        await roles_view.wait()
        if roles_view.cancelled or not roles_view.picked:
            await edit_base(message, embed=discord.Embed(title="Setup cancelled", colour=discord.Colour.dark_grey()), view=None)
            return
        member_roles = roles_view.picked

        step = discord.Embed(
            title="Step 2 — Staff roles",
            description="Select moderator and staff roles. Staff cannot be warned, muted, kicked, or banned by other staff. PeteZah can still use every command.",
            colour=discord.Colour.blurple(),
        )
        staff_view = RoleStepView(actor.id, "Select staff roles", 1, 15)
        await edit_base(message, embed=step, view=staff_view)
        await staff_view.wait()
        if staff_view.cancelled or not staff_view.picked:
            await edit_base(message, embed=discord.Embed(title="Setup cancelled", colour=discord.Colour.dark_grey()), view=None)
            return
        staff_roles = staff_view.picked

        step = discord.Embed(
            title="Step 3 — Mute role",
            description="Create a dedicated Muted role, or pick an existing one. It will be denied send/speak in every channel, slowly, to stay within Discord's limits.",
            colour=discord.Colour.blurple(),
        )
        mute_view = MuteRoleView(actor.id)
        await edit_base(message, embed=step, view=mute_view)
        await mute_view.wait()
        if mute_view.cancelled:
            await edit_base(message, embed=discord.Embed(title="Setup cancelled", colour=discord.Colour.dark_grey()), view=None)
            return

        step = discord.Embed(
            title="Step 4 — Log channel",
            description="Choose a private staff channel for moderation and anti-nuke alerts.",
            colour=discord.Colour.blurple(),
        )
        log_view = ChannelStepView(actor.id)
        await edit_base(message, embed=step, view=log_view)
        await log_view.wait()
        if log_view.cancelled or log_view.channel is None:
            await edit_base(message, embed=discord.Embed(title="Setup cancelled", colour=discord.Colour.dark_grey()), view=None)
            return
        log_channel = log_view.channel

        step = discord.Embed(
            title="Step 5 — Anti-nuke",
            description="Enable strong nuke protection: mass ban/kick, channel/role wipes, webhook spam, vanity theft, raid joining, message spam, and mass pings. Staff have much higher thresholds so normal moderation is not blocked. Owner and PeteZah are never punished.",
            colour=discord.Colour.blurple(),
        )
        nuke_view = ToggleView(actor.id, "Enable anti-nuke", "Leave off")
        await edit_base(message, embed=step, view=nuke_view)
        await nuke_view.wait()
        if nuke_view.cancelled or nuke_view.value is None:
            await edit_base(message, embed=discord.Embed(title="Setup cancelled", colour=discord.Colour.dark_grey()), view=None)
            return

        step = discord.Embed(
            title="Step 6 — Invite filter",
            description="Automatically delete Discord invite links and timeout the sender.",
            colour=discord.Colour.blurple(),
        )
        inv_view = ToggleView(actor.id, "Filter invites server-wide", "Skip invite filter")
        await edit_base(message, embed=step, view=inv_view)
        await inv_view.wait()
        if inv_view.cancelled or inv_view.value is None:
            await edit_base(message, embed=discord.Embed(title="Setup cancelled", colour=discord.Colour.dark_grey()), view=None)
            return

        cfg = self.bot.store.guild(guild.id)
        me = guild.me
        mute_role = mute_view.role
        if mute_view.create or mute_role is None:
            mute_role = await guild.create_role(
                name="Muted",
                permissions=discord.Permissions.none(),
                colour=discord.Colour.dark_grey(),
                reason="PeteZah setup",
            )
            await pace(1.0)
        if me:
            try:
                await mute_role.edit(position=max(me.top_role.position - 1, 1), reason="PeteZah setup")
            except discord.HTTPException:
                pass
            await pace(0.8)

        quarantine = None
        qid = cfg.get("quarantine_role_id")
        if qid:
            quarantine = guild.get_role(int(qid))
        if quarantine is None:
            quarantine = await guild.create_role(
                name="Quarantine",
                permissions=discord.Permissions.none(),
                colour=discord.Colour.red(),
                hoist=True,
                reason="PeteZah setup",
            )
            await pace(1.0)
            if me:
                try:
                    await quarantine.edit(position=max(me.top_role.position - 1, 1), reason="PeteZah setup")
                except discord.HTTPException:
                    pass
                await pace(0.8)

        cfg["member_role_ids"] = [r.id for r in member_roles]
        cfg["staff_role_ids"] = [r.id for r in staff_roles]
        cfg["mute_role_id"] = mute_role.id
        cfg["quarantine_role_id"] = quarantine.id
        cfg["log_channel_id"] = log_channel.id
        cfg["nuke_protection"] = bool(nuke_view.value)
        cfg["invite_filter_server"] = bool(inv_view.value)
        cfg["configured"] = True
        await self.bot.store.save()

        progress = discord.Embed(
            title="Applying channel permissions",
            description="Updating channels one at a time. This is slow on purpose so the bot stays within Discord's rate limits. Leave this running.",
            colour=discord.Colour.orange(),
        )
        await edit_base(message, embed=progress, view=None)

        async def report(done: int, total: int) -> None:
            progress.description = f"Mute/quarantine overwrites: {done}/{total} channels."
            try:
                await message.edit(embed=progress)
            except discord.HTTPException:
                pass

        await apply_role_overwrites(
            guild, mute_role, MUTE_DENIES, reason="PeteZah setup mute", delay=SETUP_PACE, progress=report
        )
        await apply_role_overwrites(
            guild, quarantine, QUARANTINE_DENIES, reason="PeteZah setup quarantine", delay=SETUP_PACE, progress=report
        )

        done = discord.Embed(
            title="Setup complete",
            description=(
                f"Member roles: {', '.join(r.mention for r in member_roles)}\n"
                f"Staff roles: {', '.join(r.mention for r in staff_roles)}\n"
                f"Mute role: {mute_role.mention}\n"
                f"Quarantine: {quarantine.mention}\n"
                f"Logs: {log_channel.mention}\n"
                f"Anti-nuke: {'on' if cfg['nuke_protection'] else 'off'}\n"
                f"Invite filter: {'on' if cfg['invite_filter_server'] else 'off'}\n\n"
                "Move **PeteZahBot** to the top of the role list so mute, timeout, and anti-nuke can manage members."
            ),
            colour=discord.Colour.green(),
        )
        await edit_base(message, embed=done, view=None)
        await log_event(guild, cfg, "Setup completed", f"Configured by {actor.mention}.", colour=discord.Colour.green())

    @commands.hybrid_command(name="setup", description="Configure member roles, staff roles, mute, logs, and protection")
    @commands.guild_only()
    @admin_only()
    async def setup_cmd(self, ctx: commands.Context) -> None:
        async def send(**kwargs):
            return await ctx.send(**kwargs)

        async def edit_base(message, **kwargs):
            return await message.edit(**kwargs)

        await self._begin(ctx.guild, ctx.author, send, edit_base)

    @commands.hybrid_command(name="config", description="Show this server's PeteZahBot configuration")
    @admin_only()
    async def show_config(self, ctx: commands.Context) -> None:
        cfg = self.bot.store.guild(ctx.guild.id)
        def names(ids):
            parts = []
            for rid in ids or []:
                role = ctx.guild.get_role(int(rid))
                parts.append(role.mention if role else str(rid))
            return ", ".join(parts) or "None"
        log_ch = ctx.guild.get_channel(int(cfg["log_channel_id"])) if cfg.get("log_channel_id") else None
        embed = discord.Embed(title="PeteZahBot config", colour=discord.Colour.blurple())
        embed.add_field(name="Configured", value="Yes" if cfg.get("configured") else "No — run /setup", inline=True)
        embed.add_field(name="Anti-nuke", value="On" if cfg.get("nuke_protection") else "Off", inline=True)
        embed.add_field(name="Invite filter", value="On" if cfg.get("invite_filter_server") else "Off", inline=True)
        embed.add_field(name="Member roles", value=clean(names(cfg.get("member_role_ids")), strip_mentions=False, limit=800), inline=False)
        embed.add_field(name="Staff roles", value=clean(names(cfg.get("staff_role_ids")), strip_mentions=False, limit=800), inline=False)
        mute = ctx.guild.get_role(int(cfg["mute_role_id"])) if cfg.get("mute_role_id") else None
        embed.add_field(name="Mute role", value=mute.mention if mute else "None", inline=True)
        embed.add_field(name="Log channel", value=log_ch.mention if log_ch else "None", inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SetupCog(bot))
