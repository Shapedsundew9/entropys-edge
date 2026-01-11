"""Discord Bot to manage a Minecraft server with auto-sleep functionality."""

import subprocess
from os import getenv
from os.path import exists
from dotenv import load_dotenv
import psutil

import discord
from discord.ext import tasks
from mcstatus import JavaServer

# --- CONFIGURATION ---
dotenv_file = ".env" if exists(".env") else "/home/discordbot/.env"
load_dotenv(dotenv_file)
TOKEN = getenv("DISCORD_TOKEN", "0")
CHANNEL_ID = int(getenv("DISCORD_CHANNEL_ID", "0"))
SERVER_IP = "127.0.0.1"  # Localhost (since bot is on same server)
IDLE_LIMIT_MINUTES = 30  # Sleep after 30 mins of 0 players
CHECK_INTERVAL = 10  # Check every 10 seconds

# Security: Load authorized players
# Expects a comma-separated list like "Steve,Alex,Bob" in .env
auth_str = getenv("AUTHORIZED_PLAYERS", "")
# Create a set of lowercase names for case-insensitive comparison
AUTHORIZED_PLAYERS = {name.strip().lower() for name in auth_str.split(",")} if auth_str else set()
# ---------------------

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Constants
IDLE_MINUTES = 0
SERVER_IS_UP = False
LATEST_CPU_LOAD = 0.0

# Cache of last online players to detect joins/leaves
last_online_players = set()


def run_command(cmd):
    """Runs a system shell command."""
    subprocess.run(cmd, shell=True, check=True)


def is_service_active():
    """Checks if systemd thinks the service is running."""
    try:
        # systemctl is-active returns 0 if active, non-zero if not
        subprocess.check_call("systemctl is-active --quiet minecraft", shell=True)
        return True
    except subprocess.CalledProcessError:
        return False


def get_disk_free_gb():
    """Returns free disk space in GB."""
    shutil = psutil.disk_usage('/')
    return round(shutil.free / (1024 ** 3), 2)


@client.event
async def on_ready():
    """Called when the bot connects only."""
    print(f"Logged in as {client.user}")
    print(f"Authorized Players: {AUTHORIZED_PLAYERS}")
    
    # Initialize CPU counter so the first read in loop is valid
    psutil.cpu_percent(interval=None)
    
    monitor_server.start()


@client.event
async def on_message(message):
    """Handles incoming commands."""
    if message.author == client.user or message.channel.id != CHANNEL_ID:
        return

    msg = message.content.lower()

    if msg == "!help":
        help_text = (
            "**🤖 Entropy's Edge Bot Commands:**\n"
            "`!status`  - Show Server CPU, RAM, Disk & Players\n"
            "`!wake`    - Start the server (alias: !start)\n"
            "`!sleep`   - Stop the server (alias: !stop)\n"
            "`!restart` - Reboot the server process\n"
        )
        await message.channel.send(help_text)

    elif msg == "!start" or msg == "!wake":
        if is_service_active():
            await message.channel.send("⚠️ Server is already running!")
        else:
            await message.channel.send(
                "🚀 **Waking up Entropy's Edge...** (Give it ~30 seconds)"
            )
            run_command("sudo systemctl start minecraft")
            await client.change_presence(activity=discord.Game(name="Booting..."))

    elif msg == "!stop" or msg == "!sleep":
        if is_service_active():
            await message.channel.send("💤 **Putting server to sleep.** Goodnight!")
            run_command("sudo systemctl stop minecraft")
        else:
            await message.channel.send("Server is already sleeping.")

    elif msg == "!restart":
        await message.channel.send("🔄 **Rebooting server process...** (Clearing lag)")
        run_command("sudo systemctl restart minecraft")

    elif msg == "!status":
        # System Stats
        ram_percent = psutil.virtual_memory().percent
        disk_free = get_disk_free_gb()
        
        # CPU is updated every 10s by the background loop
        cpu_msg = f"{LATEST_CPU_LOAD}% (10s avg)"

        if is_service_active():
            try:
                server = JavaServer.lookup(SERVER_IP)
                status = server.status()
                names = (
                    [p.name for p in status.players.sample]
                    if status.players.sample
                    else []
                )
                player_list = ", ".join(names) if names else "None"

                response = (
                    f"🟢 **Online**\n"
                    f"**Players:** {status.players.online}/{status.players.max}\n"
                    f"**Online Now:** {player_list}\n"
                    f"**Latency:** {round(status.latency)}ms\n"
                    f"────────────────\n"
                    f"**CPU:** {cpu_msg} | **RAM:** {ram_percent}%\n"
                    f"**Free Disk:** {disk_free} GB"
                )
                await message.channel.send(response)
            # pylint: disable=broad-except
            except Exception:
                await message.channel.send(
                    f"🟡 **Starting up...** (Process active, Java loading)\n"
                    f"**CPU:** {cpu_msg} | **RAM:** {ram_percent}%"
                )
        else:
            await message.channel.send(
                f"🔴 **Offline** (Sleeping)\n"
                f"**CPU:** {cpu_msg} | **RAM:** {ram_percent}% | **Disk:** {disk_free} GB"
            )


@tasks.loop(seconds=CHECK_INTERVAL)
async def monitor_server():
    """Background task to check player counts, security, and usage."""
    # pylint: disable=global-statement
    global IDLE_MINUTES, last_online_players, SERVER_IS_UP, LATEST_CPU_LOAD
    
    channel = client.get_channel(CHANNEL_ID)
    if not channel:
        return
    assert isinstance(channel, discord.TextChannel), f"Channel {CHANNEL_ID} is not a text channel."

    # 0. Measure CPU (interval=None gives avg since last call 10s ago)
    LATEST_CPU_LOAD = psutil.cpu_percent(interval=None)

    # 1. Check if Process is running
    if not is_service_active():
        if SERVER_IS_UP:  # It just went down
            SERVER_IS_UP = False
            await client.change_presence(
                status=discord.Status.idle, activity=discord.Game(name="Sleeping")
            )
            last_online_players = set()  # Clear cache
        return  # Nothing else to do

    # 2. Process is up, Query Java
    SERVER_IS_UP = True
    try:
        server = JavaServer.lookup(SERVER_IP)
        status = server.status()

        # --- JOIN / LEAVE NOTIFICATIONS ---
        current_players = set()
        if status.players.sample:
            current_players = {p.name for p in status.players.sample}

        # --- SECURITY CHECK ---
        # Only run if we actually have an authorized list configured
        if AUTHORIZED_PLAYERS:
            # Check for any unauthorized players
            unauthorized = [p for p in current_players if p.lower() not in AUTHORIZED_PLAYERS]
            
            if unauthorized:
                print(f"SECURITY ALERT: Unauthorized players detected: {unauthorized}")
                await channel.send(
                    f"🚨 **SECURITY ALERT** 🚨\n"
                    f"Unauthorized player(s) detected: **{', '.join(unauthorized)}**\n"
                    f"🛑 **SHUTTING DOWN SERVER IMMEDIATELY**"
                )
                run_command("sudo systemctl stop minecraft")
                return # Stop processing this loop iteration

        # Calculate difference matches
        new_joins = current_players - last_online_players
        left_players = last_online_players - current_players

        if new_joins:
            await channel.send(f"👋 **{', '.join(new_joins)}** joined the server!")
        if left_players:
            await channel.send(f"🚪 **{', '.join(left_players)}** left the server.")

        last_online_players = current_players

        # --- IDLE CHECK ---
        await client.change_presence(
            status=discord.Status.online,
            activity=discord.Game(name=f"{status.players.online} Players Online"),
        )

        if status.players.online == 0:
            # Add seconds converted to minutes fraction
            IDLE_MINUTES += CHECK_INTERVAL / 60
            if IDLE_MINUTES >= IDLE_LIMIT_MINUTES:
                await channel.send(
                    f"📉 No players for {IDLE_LIMIT_MINUTES} mins. "
                    " **Auto-sleeping to save resources.**"
                )
                run_command("sudo systemctl stop minecraft")
                IDLE_MINUTES = 0
        else:
            IDLE_MINUTES = 0  # Reset timer if someone is playing

    # pylint: disable=broad-except
    except Exception:
        # Server is running but Java isn't responding (Switching levels or booting)
        pass

client.run(TOKEN)