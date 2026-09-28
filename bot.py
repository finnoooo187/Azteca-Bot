import os
import json
import random
import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = 1009507301692014665
TIMEZONE = ZoneInfo("Europe/Berlin")
DATA_FILE = "poll_data.json"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

OPTIONS = {
    "yes": ("🟢", "Verfügbar"),
    "no": ("🔴", "Nicht verfügbar"),
    "maybe": ("🟡", "Vielleicht"),
}

def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

poll_data = load_data()

def today_key():
    return datetime.now(TIMEZONE).date().isoformat()

def make_poll_embed():
    embed = discord.Embed(
        title="📋 Tägliche Abfrage",
        description=(
            "Wer ist heute um **20:30 Uhr** verfügbar?\n\n"
            "🟢 **Verfügbar**\n"
            "🔴 **Nicht verfügbar**\n"
            "🟡 **Vielleicht**\n\n"
            "Die Abstimmung ist bis **20:30 Uhr** geöffnet."
        )
    )
    embed.set_footer(text="Azteca Bot • Tagesabfrage")
    return embed

class PollView(discord.ui.View):
    def __init__(self, key: str, disabled=False):
        super().__init__(timeout=None)
        self.key = key
        for option_key, (emoji, label) in OPTIONS.items():
            button = PollButton(option_key, emoji, label, key)
            button.disabled = disabled
            self.add_item(button)

class PollButton(discord.ui.Button):
    def __init__(self, option_key, emoji, label, key):
        super().__init__(
            style=discord.ButtonStyle.success if option_key == "yes"
            else discord.ButtonStyle.danger if option_key == "no"
            else discord.ButtonStyle.secondary,
            emoji=emoji,
            label=label,
            custom_id=f"poll:{key}:{option_key}"
        )
        self.option_key = option_key
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        if self.key != today_key():
            await interaction.response.send_message(
                "Diese Tagesabfrage ist bereits geschlossen.", ephemeral=True
            )
            return

        data = poll_data.setdefault(self.key, {"message_id": None, "votes": {}})
        user_id = str(interaction.user.id)

        # A user can change their vote. Remove the previous choice first.
        for voters in data["votes"].values():
            if user_id in voters:
                voters.remove(user_id)

        data["votes"].setdefault(self.option_key, []).append(user_id)
        save_data(poll_data)

        await interaction.response.send_message(
            f"{OPTIONS[self.option_key][0]} Deine Stimme wurde als "
            f"**{OPTIONS[self.option_key][1]}** gespeichert.",
            ephemeral=True
        )

@bot.event
async def on_ready():
    if not daily_scheduler.is_running():
        daily_scheduler.start()
    print(f"Angemeldet als {bot.user}.")

@bot.tree.command(name="zahl", description="Erzeugt eine Zufallszahl von 100,0 bis 999,9.")
async def zahl(interaction: discord.Interaction):
    value = random.randint(1000, 9999) / 10
    await interaction.response.send_message(
        f"🎲 **Zufallszahl:** `{value:,.1f}`".replace(",", "X").replace(".", ",").replace("X", ".")
    )

async def post_daily_poll():
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(CHANNEL_ID)
        except discord.DiscordException as e:
            print(f"Kanal konnte nicht geladen werden: {e}")
            return

    key = today_key()
    existing = poll_data.get(key)
    if existing and existing.get("message_id"):
        return

    message = await channel.send(embed=make_poll_embed(), view=PollView(key))
    poll_data[key] = {"message_id": message.id, "votes": {"yes": [], "no": [], "maybe": []}}
    save_data(poll_data)

async def close_poll():
    key = today_key()
    data = poll_data.get(key)
    if not data or not data.get("message_id"):
        return

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(CHANNEL_ID)

    try:
        message = await channel.fetch_message(data["message_id"])
    except discord.DiscordException:
        message = None

    counts = {k: len(v) for k, v in data.get("votes", {}).items()}
    total = sum(counts.values())

    lines = [
        "📊 **Abstimmung beendet**",
        "",
        f"🟢 Verfügbar: **{counts.get('yes', 0)}**",
        f"🔴 Nicht verfügbar: **{counts.get('no', 0)}**",
        f"🟡 Vielleicht: **{counts.get('maybe', 0)}**",
        f"👥 Abgegebene Stimmen: **{total}**",
        "",
    ]

    for key_opt, (emoji, label) in OPTIONS.items():
        ids = data.get("votes", {}).get(key_opt, [])
        mentions = " ".join(f"<@{uid}>" for uid in ids) if ids else "—"
        lines.append(f"{emoji} **{label}:** {mentions}")

    result_embed = discord.Embed(
        title="📊 Tagesabfrage – Ergebnis",
        description="\n".join(lines)
    )
    result_embed.set_footer(text="Azteca Bot")

    if message:
        try:
            await message.edit(view=PollView(key, disabled=True))
        except discord.DiscordException:
            pass

    await channel.send(embed=result_embed)

    # Keep the result data, but mark the poll as closed.
    data["closed"] = True
    save_data(poll_data)

async def wait_until(target: time):
    while True:
        now = datetime.now(TIMEZONE)
        target_dt = datetime.combine(now.date(), target, tzinfo=TIMEZONE)
        if target_dt <= now:
            target_dt += timedelta(days=1)
        await asyncio.sleep((target_dt - now).total_seconds())
        return

@tasks.loop(hours=1)
async def daily_scheduler():
    now = datetime.now(TIMEZONE)
    # Check often enough to handle restarts close to the target times.
    if now.hour == 0 and now.minute == 0:
        await post_daily_poll()
    if now.hour == 20 and now.minute == 30:
        await close_poll()

@daily_scheduler.before_loop
async def before_scheduler():
    await bot.wait_until_ready()

bot.run(TOKEN)
