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

# ─────────────────────────────────────────────────────────────────────
# DB 초기화
# ─────────────────────────────────────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        with open(BASE_DIR / "models.sql", "r", encoding="utf-8") as f:
            await db.executescript(f.read())
        await db.commit()

# ─────────────────────────────────────────────────────────────────────
# 번역기: 한국어 클라이언트에 한글 명령/설명/파라미터 노출
# ─────────────────────────────────────────────────────────────────────
class MZTranslator(app_commands.Translator):
    async def translate(self, string: app_commands.locale_str,
                        locale: discord.Locale,
                        context: app_commands.TranslationContext) -> str | None:
        if locale is not discord.Locale.korean:
            return None

        loc = context.location  # command_name / command_description / parameter_name / parameter_description
        data = context.data     # Command | Parameter | Choice 등

        # 명령어 이름
        if loc is app_commands.TranslationContextLocation.command_name:
            if isinstance(data, app_commands.Command):
                mapping = {
                    "mz_money":  "면진돈줘",
                    "mz_attend": "면진출첵",
                    "mz_rank":   "면진순위",
                    "mz_bet":    "면진도박",
                }
                return mapping.get(data.name)

        # 명령어 설명
        if loc is app_commands.TranslationContextLocation.command_description:
            if isinstance(data, app_commands.Command):
                desc_map = {
                    "mz_money":  "10분마다 1,000 코인 지급",
                    "mz_attend": "하루에 한 번 10,000 코인 지급",
                    "mz_rank":   "서버 잔액 순위 TOP 10",
                    "mz_bet":    "코인플립 베팅(앞/뒤, 금액)",
                }
                return desc_map.get(data.name)

        # 파라미터 이름
        if loc is app_commands.TranslationContextLocation.parameter_name:
            if isinstance(data, app_commands.Parameter):
                if data.name == "side":   return "면"
                if data.name == "amount": return "금액"

        # 파라미터 설명
        if loc is app_commands.TranslationContextLocation.parameter_description:
            if isinstance(data, app_commands.Parameter):
                if data.name == "side":   return "앞 또는 뒤"
                if data.name == "amount": return "베팅 금액(정수)"

        return None

# ─────────────────────────────────────────────────────────────────────
# Bot
# ─────────────────────────────────────────────────────────────────────
INTENTS = discord.Intents.default()
# 접두사 명령 안 쓸 거면 prefix=None → 메시지 콘텐츠 인텐트 경고 제거
bot = commands.Bot(command_prefix=None, intents=INTENTS)

@bot.event
async def on_ready():
    print(f"✅ {bot.user} 로그인")

async def setup_hook():
    await init_db()
    await bot.tree.set_translator(MZTranslator())
    await bot.load_extension("cogs.economy")
    await bot.load_extension("cogs.games")

    # 개발 중 즉시 반영 원하면 아래 줄의 주석 해제 후 서버 ID 입력
    # await bot.tree.sync(guild=discord.Object(id=123456789012345678))
    await bot.tree.sync()

bot.setup_hook = setup_hook

if __name__ == "__main__":
    bot.run(TOKEN)
