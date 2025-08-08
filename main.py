# main.py
import os, aiosqlite
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
INTENTS = discord.Intents.default()
bot = commands.Bot(command_prefix=None, intents=INTENTS)  # 접두사 명령 제거

DB_PATH = "economy.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        with open("models.sql", "r", encoding="utf-8") as f:
            await db.executescript(f.read())
        await db.commit()

bot = commands.Bot(command_prefix="!", intents=INTENTS)

@bot.event
async def on_ready():
    await init_db()
    await bot.tree.sync()
    print(f"✅ {bot.user} 로그인")

async def setup_hook():
    await bot.load_extension("cogs.economy")
    await bot.load_extension("cogs.games")
bot.setup_hook = setup_hook  # for discord.py 2.x

bot.run(TOKEN)
