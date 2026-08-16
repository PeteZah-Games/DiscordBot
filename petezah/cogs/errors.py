from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from petezah.sanitize import clean


class ErrorsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.tree.on_error = self.on_app_command_error

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        err = getattr(error, "original", error)
        if isinstance(err, commands.CommandNotFound):
            return
        if isinstance(err, commands.MissingPermissions):
            await ctx.send("You need the required permissions to use this command.")
            return
        if isinstance(err, commands.CheckFailure):
            await ctx.send("You can't use that command.")
            return
        if isinstance(err, commands.MissingRequiredArgument):
            await ctx.send("Missing required argument. Check `p!help`.")
            return
        if isinstance(err, commands.MemberNotFound):
            await ctx.send("Member not found.")
            return
        if isinstance(err, commands.BadArgument):
            await ctx.send("Invalid argument.")
            return
        if isinstance(err, commands.NoPrivateMessage):
            await ctx.send("Use this command in a server.")
            return
        if isinstance(err, commands.CommandOnCooldown):
            await ctx.send(f"Slow down. Try again in {err.retry_after:.0f}s.")
            return
        if isinstance(err, discord.Forbidden):
            await ctx.send("I don't have permission to do that. Move my role to the top and grant the needed permissions.")
            return
        self.bot.log.warning("command error: %s", clean(type(err).__name__, strip_mentions=True, limit=80))
        await ctx.send("Something went wrong.")

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        err = getattr(error, "original", error)
        msg = "Something went wrong."
        if isinstance(err, app_commands.CheckFailure):
            msg = "You can't use that command."
        elif isinstance(err, app_commands.CommandOnCooldown):
            msg = "Slow down and try again shortly."
        elif isinstance(err, discord.Forbidden):
            msg = "I don't have permission to do that."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass
        self.bot.log.warning("slash error: %s", type(err).__name__)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ErrorsCog(bot))
