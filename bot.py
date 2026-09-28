import os
import json
import random
from datetime import datetime, time
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

# =========================
# KONFIGURATION
# =========================
TOKEN = os.getenv("DISCORD_TOKEN")

# Dein Discord-Kanal für die tägliche Abstimmung
POLL_CHANNEL_ID = 1009507301692014665

# Deutsche Zeitzone (Sommer-/Winterzeit wird automatisch berücksichtigt)
TIMEZONE = ZoneInfo("Europe/Berlin")

# Persistente Datei. /data wird auf vielen Hostern für App-Daten verwendet.
DATA_DIR = "/data" if os.path.isdir("/data") else "."
STATE_FILE = os.path.join(DATA_DIR, "poll_state.json")

OPTIONS = {
    "available": {
        "label": "Verfügbar",
        "emoji": "🟢",
    },
    "unavailable": {
        "label": "Nicht verfügbar",
        "emoji": "🔴",
    },
    "maybe": {
        "label": "Vielleicht",
        "emoji": "🟡",
    },
}


# =========================
# BOT
# =========================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# =========================
# HILFSFUNKTIONEN
# =========================
def today_key():
    return datetime.now(TIMEZONE).date().isoformat()


def now_local():
    return datetime.now(TIMEZONE)


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state():
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(poll_state, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"[WARNUNG] Konnte Status nicht speichern: {exc}")


poll_state = load_state()


def current_poll():
    return poll_state.get(today_key())


def is_poll_open():
    poll = current_poll()
    if not poll:
        return False

    if poll.get("closed", False):
        return False

    current_time = now_local().time()
    return current_time < time(20, 30)


async def get_poll_channel():
    channel = bot.get_channel(POLL_CHANNEL_ID)

    if channel is None:
        try:
            channel = await bot.fetch_channel(POLL_CHANNEL_ID)
        except discord.DiscordException as exc:
            print(f"[FEHLER] Poll-Kanal konnte nicht geladen werden: {exc}")
            return None

    return channel


async def member_name(guild, user_id):
    member = guild.get_member(int(user_id))

    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except discord.DiscordException:
            return f"Unbekannt ({user_id})"

    return discord.utils.escape_markdown(member.display_name)


async def build_poll_embed(guild, closed=False):
    poll = current_poll()

    embed = discord.Embed(
        title="📋 Aufstellung um 20:30 Uhr",
        description=(
            "Wer ist heute um **20:30 Uhr** verfügbar?\n\n"
            "Klicke auf einen Button, um deine Auswahl abzugeben. "
            "Du kannst deine Auswahl jederzeit ändern."
        ),
        color=discord.Color.from_rgb(0, 216, 224),
        timestamp=now_local(),
    )

    votes = poll.get("votes", {}) if poll else {}

    grouped = {
        "available": [],
        "unavailable": [],
        "maybe": [],
    }

    for user_id, choice in votes.items():
        if choice in grouped:
            grouped[choice].append(user_id)

    for choice in grouped:
        grouped[choice].sort(
            key=lambda user_id: str(user_id)
        )

    for choice, info in OPTIONS.items():
        names = []
        for user_id in grouped[choice]:
            names.append(await member_name(guild, user_id))

        if names:
            value = "\n".join(f"• {name}" for name in names)
        else:
            value = "— Niemand —"

        embed.add_field(
            name=f"{info['emoji']} {info['label']} ({len(names)})",
            value=value,
            inline=True,
        )

    if closed:
        embed.set_footer(text="🔒 Abstimmung geschlossen • 20:30 Uhr")
    else:
        embed.set_footer(text="⏰ Abstimmung läuft bis 20:30 Uhr")

    return embed


# =========================
# ABSTIMMUNGS-BUTTONS
# =========================
class PollView(discord.ui.View):
    def __init__(self, poll_date: str, disabled=False):
        super().__init__(timeout=None)

        self.poll_date = poll_date

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = disabled

    async def handle_vote(self, interaction: discord.Interaction, choice: str):
        if self.poll_date != today_key():
            await interaction.response.send_message(
                "Diese Abstimmung gehört zu einem vergangenen Tag.",
                ephemeral=True,
            )
            return

        if not is_poll_open():
            await interaction.response.send_message(
                "Die Abstimmung ist bereits geschlossen.",
                ephemeral=True,
            )
            return

        poll = poll_state.get(self.poll_date)

        if not poll:
            await interaction.response.send_message(
                "Für heute wurde keine Abstimmung gefunden.",
                ephemeral=True,
            )
            return

        user_id = str(interaction.user.id)
        poll.setdefault("votes", {})[user_id] = choice
        save_state()

        guild = interaction.guild
        embed = await build_poll_embed(guild, closed=False)

        try:
            await interaction.response.edit_message(
                embed=embed,
                view=PollView(self.poll_date),
            )
        except discord.DiscordException as exc:
            print(f"[FEHLER] Abstimmung konnte nicht aktualisiert werden: {exc}")

    @discord.ui.button(
        label="Verfügbar",
        emoji="🟢",
        style=discord.ButtonStyle.success,
        custom_id="azteca_poll_available",
    )
    async def available(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self.handle_vote(interaction, "available")

    @discord.ui.button(
        label="Nicht verfügbar",
        emoji="🔴",
        style=discord.ButtonStyle.danger,
        custom_id="azteca_poll_unavailable",
    )
    async def unavailable(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self.handle_vote(interaction, "unavailable")

    @discord.ui.button(
        label="Vielleicht",
        emoji="🟡",
        style=discord.ButtonStyle.secondary,
        custom_id="azteca_poll_maybe",
    )
    async def maybe(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self.handle_vote(interaction, "maybe")


# =========================
# ABSTIMMUNG ERSTELLEN
# =========================
async def create_daily_poll():
    key = today_key()

    # Bereits vorhanden? Dann keine zweite Abstimmung erstellen.
    if key in poll_state and poll_state[key].get("message_id"):
        return

    channel = await get_poll_channel()

    if channel is None:
        return

    if not isinstance(channel, discord.TextChannel):
        print("[FEHLER] Die angegebene Kanal-ID ist kein Textkanal.")
        return

    poll_state[key] = {
        "message_id": None,
        "votes": {},
        "closed": False,
    }
    save_state()

    embed = await build_poll_embed(channel.guild, closed=False)

    try:
        message = await channel.send(
            embed=embed,
            view=PollView(key),
            allowed_mentions=discord.AllowedMentions.none(),
        )

        poll_state[key]["message_id"] = message.id
        save_state()

        print(f"[INFO] Tagesabstimmung erstellt: {message.id}")

    except discord.DiscordException as exc:
        print(f"[FEHLER] Tagesabstimmung konnte nicht erstellt werden: {exc}")


# =========================
# ABSTIMMUNG SCHLIESSEN
# =========================
async def close_daily_poll():
    key = today_key()
    poll = poll_state.get(key)

    if not poll or poll.get("closed", False):
        return

    channel = await get_poll_channel()
    if channel is None:
        return

    message_id = poll.get("message_id")

    try:
        message = await channel.fetch_message(message_id)
    except discord.DiscordException as exc:
        print(f"[FEHLER] Abstimmungsnachricht konnte nicht geladen werden: {exc}")
        poll["closed"] = True
        save_state()
        return

    poll["closed"] = True
    save_state()

    embed = await build_poll_embed(channel.guild, closed=True)

    try:
        await message.edit(
            embed=embed,
            view=PollView(key, disabled=True),
        )
        print("[INFO] Tagesabstimmung geschlossen.")

    except discord.DiscordException as exc:
        print(f"[FEHLER] Abstimmung konnte nicht geschlossen werden: {exc}")


# =========================
# AUTOMATISCHE ZEITSTEUERUNG
# =========================
@tasks.loop(seconds=20)
async def scheduler():
    now = now_local()
    current_time = now.time()
    key = now.date().isoformat()

    # Zwischen 00:00 und 20:29: heutige Abstimmung sicherstellen.
    # Dadurch wird sie auch erstellt, wenn der Bot nach Mitternacht neu startet.
    if time(0, 0) <= current_time < time(20, 30):
        if key not in poll_state or not poll_state[key].get("message_id"):
            await create_daily_poll()

    # Ab 20:30 schließen.
    if current_time >= time(20, 30):
        poll = poll_state.get(key)
        if poll and not poll.get("closed", False):
            await close_daily_poll()


@scheduler.before_loop
async def before_scheduler():
    await bot.wait_until_ready()


# =========================
# SLASH COMMAND /zahl
# =========================
@bot.tree.command(
    name="zahl",
    description="Gibt eine zufällige Zahl von 100,0 bis 999,9 aus.",
)
async def zahl(interaction: discord.Interaction):
    value = random.randint(1000, 9999) / 10
    formatted = f"{value:05.1f}".replace(".", ",")

    await interaction.response.send_message(
        f"🎲 **{formatted}**",
        allowed_mentions=discord.AllowedMentions.none(),
    )


# =========================
# BOT START
# =========================
@bot.event
async def setup_hook():
    # Slash-Commands bei Discord registrieren
    try:
        await bot.tree.sync()
        print("[INFO] Slash-Commands synchronisiert.")
    except discord.DiscordException as exc:
        print(f"[FEHLER] Slash-Commands konnten nicht synchronisiert werden: {exc}")

    # Aktive heutige Abstimmung nach Neustart wieder mit Buttons versehen.
    poll = current_poll()

    if poll and poll.get("message_id") and not poll.get("closed", False):
        bot.add_view(PollView(today_key()))

    scheduler.start()


@bot.event
async def on_ready():
    print(f"[INFO] Eingeloggt als {bot.user} (ID: {bot.user.id})")


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN wurde nicht gefunden. "
        "Bitte den Discord-Bot-Token beim Hoster als Umgebungsvariable "
        "'DISCORD_TOKEN' hinterlegen."
    )

bot.run(TOKEN)
