"""Discord Bot to manage a Minecraft server with auto-sleep functionality."""

import subprocess
from os import getenv

import discord
from discord.ext import tasks
from mcstatus import JavaServer

# --- CONFIGURATION ---
TOKEN = getenv("DISCORD_TOKEN", "0")
CHANNEL_ID = int(getenv("DISCORD_CHANNEL_ID", "0"))
SERVER_IP = "127.0.0.1"  # Localhost (since bot is on same server)
IDLE_LIMIT_MINUTES = 30  # Sleep after 30 mins of 0 players
CHECK_INTERVAL = 30  # Check every 30 seconds
# ---------------------

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Constants
IDLE_MINUTES = 0
SERVER_IS_UP = False

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


@client.event
async def on_ready():
    """
    Docstring for on_ready
    """
    print(f"Logged in as {client.user}")
    monitor_server.start()


@client.event
async def on_message(message):
    """
    Docstring for on_message

    :param message: Description
    """
    if message.author == client.user or message.channel.id != CHANNEL_ID:
        return

    msg = message.content.lower()

    if msg == "!start" or msg == "!wake":
        if is_service_active():
            await message.channel.send("⚠️ Server is already running!")
        else:
            await message.channel.send(
                "🚀 **Waking up entropy's Edge...** (Give it ~30 seconds)"
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
                    f"**Latency:** {round(status.latency)}ms"
                )
                await message.channel.send(response)
            # pylint: disable=broad-except
            except Exception:
                await message.channel.send(
                    "🟡 **Starting up...** (Process active, Java loading)"
                )
        else:
            await message.channel.send("🔴 **Offline** (Sleeping)")


@tasks.loop(seconds=CHECK_INTERVAL)
async def monitor_server():
    """
    Docstring for monitor_server
    """
    # pylint: disable=global-statement
    global IDLE_MINUTES, last_online_players, SERVER_IS_UP
    channel = client.get_channel(CHANNEL_ID)
    assert isinstance(
        channel, discord.TextChannel
    ), "Channel not found or invalid type."

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

        # Calculate difference
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
