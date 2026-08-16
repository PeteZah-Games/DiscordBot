from __future__ import annotations

import io
from urllib.parse import quote

import aiohttp
import discord
from discord.ext import commands

from petezah.config import IMAGE_USER_LIMIT, IMAGE_USER_WINDOW
from petezah.rate import SlidingWindow
from petezah.sanitize import clean_prompt


class FunCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.image_limit = SlidingWindow(IMAGE_USER_LIMIT, IMAGE_USER_WINDOW)

    @commands.command()
    async def generateimage(self, ctx: commands.Context, *, prompt: str) -> None:
        allowed, retry = self.image_limit.allow(ctx.author.id)
        if not allowed:
            await ctx.send(f"Image generation is rate-limited. Try again in {int(retry) + 1}s.")
            return
        prompt = clean_prompt(prompt)[:400]
        session = self.bot.http_session
        if session is None:
            await ctx.send("Not ready.")
            return
        encoded = quote(prompt)
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with session.get(f"https://image.pollinations.ai/prompt/{encoded}", timeout=timeout) as resp:
                if resp.status != 200:
                    await ctx.send("Failed to generate image.")
                    return
                data = io.BytesIO(await resp.read())
        except aiohttp.ClientError:
            await ctx.send("Failed to generate image.")
            return
        await ctx.send(file=discord.File(data, "generated_image.png"))

    @commands.command()
    async def rps(self, ctx: commands.Context, opponent: discord.Member) -> None:
        if opponent.id == ctx.author.id:
            await ctx.send("You can't challenge yourself.")
            return
        if opponent.bot:
            await ctx.send("You can't challenge a bot.")
            return
        options = ["🪨", "📜", "✂️"]
        embed = discord.Embed(
            title="Rock-Paper-Scissors",
            description=f"{ctx.author.mention} vs {opponent.mention}\nReact with 🪨, 📜, or ✂️",
            colour=discord.Colour.blue(),
        )
        message = await ctx.send(embed=embed)
        for emoji in options:
            await message.add_reaction(emoji)
        choices: dict[int, str] = {}

        def check(reaction: discord.Reaction, user: discord.User) -> bool:
            return (
                user.id in {ctx.author.id, opponent.id}
                and str(reaction.emoji) in options
                and reaction.message.id == message.id
            )

        try:
            while len(choices) < 2:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
                choices[user.id] = str(reaction.emoji)
                try:
                    await message.remove_reaction(reaction, user)
                except discord.HTTPException:
                    pass
        except TimeoutError:
            await message.edit(embed=discord.Embed(title="Rock-Paper-Scissors", description="Timed out.", colour=discord.Colour.red()))
            return
        player = choices.get(ctx.author.id)
        other = choices.get(opponent.id)
        wins = {("🪨", "✂️"), ("📜", "🪨"), ("✂️", "📜")}
        if player == other:
            result = "It's a tie!"
        elif (player, other) in wins:
            result = f"{ctx.author.mention} wins!"
        else:
            result = f"{opponent.mention} wins!"
        out = discord.Embed(title="Rock-Paper-Scissors result", colour=discord.Colour.blue())
        out.add_field(name=ctx.author.name, value=player, inline=True)
        out.add_field(name=opponent.name, value=other, inline=True)
        out.add_field(name="Result", value=result, inline=False)
        await message.edit(embed=out)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FunCog(bot))
