from __future__ import annotations

import discord
from discord.ext import commands

from petezah.checks import staff_only
from petezah.config import PREFIX, SUPERUSER_ID
from petezah.sanitize import clean


class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command()
    async def help(self, ctx: commands.Context) -> None:
        embed = discord.Embed(
            title="PeteZahBot",
            description=f"Prefix `{PREFIX}` · Superuser can use every command.\nStart with `/setup` so mute, lock, and anti-nuke know your roles.",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name="Setup & admin",
            value="`setup` `config` `initiate` `stop` `lock` `unlock` `slowmode` `say` `embed` `pin` `unpin` `role` `autowarn` `whitelist` `log_enable`",
            inline=False,
        )
        embed.add_field(
            name="Moderation",
            value="`ban` `unban` `kick` `mute` `unmute` `timeout` `untimeout` `warn` `warns` `clearwarnings` `purge` `modlogs`",
            inline=False,
        )
        embed.add_field(
            name="Utility",
            value="`ping` `userinfo` `serverinfo` `avatar` `roleinfo` `poll` `invite` `botinvite` `afk` `afkstop`",
            inline=False,
        )
        embed.add_field(
            name="Fun / AI",
            value="`rps` `generateimage` · AI chats in channels where `initiate` was used (rate-limited)",
            inline=False,
        )
        embed.set_footer(text="Staff cannot be warned. Move the bot role to the top after /setup.")
        await ctx.send(embed=embed)

    @commands.command()
    async def ping(self, ctx: commands.Context) -> None:
        await ctx.send(f"Pong! {round(self.bot.latency * 1000)}ms")

    @commands.command()
    async def userinfo(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        member = member or ctx.author
        embed = discord.Embed(title=f"User info — {member}", colour=discord.Colour.blue())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Username", value=clean(member.name, strip_mentions=True, limit=80), inline=True)
        embed.add_field(name="Nickname", value=clean(member.nick or "None", limit=80), inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(member.created_at, "s"), inline=True)
        if member.joined_at:
            embed.add_field(name="Joined", value=discord.utils.format_dt(member.joined_at, "s"), inline=True)
        roles = [r.mention for r in member.roles[1:20]]
        embed.add_field(name="Roles", value=", ".join(roles) or "None", inline=False)
        embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
        if member.id == SUPERUSER_ID:
            embed.add_field(name="PeteZah", value="Superuser", inline=True)
        await ctx.send(embed=embed)

    @commands.command()
    async def serverinfo(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        embed = discord.Embed(title=f"Server info — {guild.name}", colour=discord.Colour.blue())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="ID", value=str(guild.id), inline=True)
        embed.add_field(name="Owner", value=str(guild.owner), inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "s"), inline=True)
        embed.add_field(name="Members", value=str(guild.member_count), inline=True)
        embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles) - 1), inline=True)
        await ctx.send(embed=embed)

    @commands.command()
    async def avatar(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        member = member or ctx.author
        embed = discord.Embed(title=f"{member}'s avatar", colour=discord.Colour.blue())
        embed.set_image(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command()
    async def roleinfo(self, ctx: commands.Context, role: discord.Role) -> None:
        embed = discord.Embed(title=f"Role info — {role.name}", colour=role.colour)
        embed.add_field(name="ID", value=str(role.id), inline=True)
        embed.add_field(name="Members", value=str(len(role.members)), inline=True)
        embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
        embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        await ctx.send(embed=embed)

    @commands.command()
    async def poll(self, ctx: commands.Context, question: str, *options: str) -> None:
        if not options or len(options) > 10:
            await ctx.send("Provide 1-10 options.")
            return
        embed = discord.Embed(title="Poll", description=clean(question, limit=400), colour=discord.Colour.blue())
        for i, option in enumerate(options, 1):
            embed.add_field(name=f"Option {i}", value=clean(option, limit=200), inline=False)
        message = await ctx.send(embed=embed)
        for i in range(len(options)):
            await message.add_reaction(f"{i + 1}\u20e3")

    @commands.command()
    @staff_only("create_instant_invite")
    async def invite(self, ctx: commands.Context) -> None:
        invite = await ctx.channel.create_invite(max_age=86400, max_uses=0, unique=False)
        await ctx.send(f"Invite: {invite.url}")

    @commands.command()
    async def botinvite(self, ctx: commands.Context) -> None:
        await ctx.send(
            discord.utils.oauth_url(
                self.bot.user.id,
                permissions=discord.Permissions(administrator=True),
                scopes=("bot", "applications.commands"),
            )
        )

    @commands.command()
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK") -> None:
        self.bot.store.afk[str(ctx.author.id)] = clean(reason, limit=200)
        await self.bot.store.save()
        await ctx.send(f"{ctx.author.mention} is now AFK: {clean(reason, limit=200)}")

    @commands.command()
    async def afkstop(self, ctx: commands.Context) -> None:
        if str(ctx.author.id) in self.bot.store.afk:
            del self.bot.store.afk[str(ctx.author.id)]
            await self.bot.store.save()
            await ctx.send(f"{ctx.author.mention} is no longer AFK.")
            return
        await ctx.send("You are not AFK.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UtilityCog(bot))
