import os
from pathlib import Path
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import aiosqlite

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / "economy.db")

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        with open(BASE_DIR / "models.sql", "r", encoding="utf-8") as f:
            await db.executescript(f.read())
        await db.commit()

class MZTranslator(app_commands.Translator):
    async def translate(self, string: app_commands.locale_str, locale: discord.Locale,
                        context: app_commands.TranslationContext) -> str | None:
        if locale is not discord.Locale.korean:
            return None
        loc = context.location
        data = context.data

        if loc is app_commands.TranslationContextLocation.command_name:
            if isinstance(data, app_commands.Command):
                mapping = {
                    "mz_money":        "면진돈줘",
                    "mz_attend":       "면진출첵",
                    "mz_rank":         "면진순위",
                    "mz_bet":          "면진도박",
                    "mz_balance_show": "면진잔액",
                    "mz_admin":        "면진관리자",
                    "mz_ask":          "면진질문",   # ← 변경: '질문' → '면진질문'
                }
                return mapping.get(data.name)

        if loc is app_commands.TranslationContextLocation.command_description:
            if isinstance(data, app_commands.Command):
                desc_map = {
                    "mz_money":        "10분마다 1,000 코인 지급",
                    "mz_attend":       "자정(00:00 KST)마다 초기화되는 출석 보상",
                    "mz_rank":         "서버 잔액 순위 TOP 10(닉네임만 표시)",
                    "mz_bet":          "승률 30~60% 랜덤, 결과는 ±베팅액 (최소 1,000₩)",
                    "mz_balance_show": "현재 잔액 확인(대상 선택 가능)",
                    "mz_admin":        "관리자 메뉴 열기(관리자 전용)",
                    "mz_ask":          "질문을 보내면 랜덤으로 대답합니다",
                }
                return desc_map.get(data.name)

        if loc is app_commands.TranslationContextLocation.parameter_name:
            if isinstance(data, app_commands.Parameter):
                if data.name == "amount":   return "금액"
                if data.name == "user":     return "대상"
                if data.name == "question": return "질문"

        if loc is app_commands.TranslationContextLocation.parameter_description:
            if isinstance(data, app_commands.Parameter):
                if data.name == "amount":   return "베팅 금액(정수, 최소 1,000₩)"
                if data.name == "user":     return "대상 사용자"
                if data.name == "question": return "질문 내용"
        return None

INTENTS = discord.Intents.default()
INTENTS.members = True
bot = commands.Bot(command_prefix=None, intents=INTENTS)

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Game(name="/면진도박 · /면진돈줘"))
    print(f"✅ {bot.user} 로그인")

async def setup_hook():
    await init_db()
    await bot.tree.set_translator(MZTranslator())

    # 코그 로드
    await bot.load_extension("cogs.economy")
    await bot.load_extension("cogs.games")
    await bot.load_extension("cogs.admin")
    await bot.load_extension("cogs.fun")   # ← 신규

    if DEV_GUILD_ID:
        await bot.tree.sync(guild=discord.Object(id=int(DEV_GUILD_ID)))
    else:
        await bot.tree.sync()

bot.setup_hook = setup_hook

if __name__ == "__main__":
    bot.run(TOKEN)
