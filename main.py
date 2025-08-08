import os
from pathlib import Path
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import aiosqlite

# 프로젝트 경로 / DB 파일 경로
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / "economy.db")

# 환경변수 읽기
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID")  # 개발용 길드 즉시 동기화(선택)

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
    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContext,
    ) -> str | None:
        if locale is not discord.Locale.korean:
            return None

        loc = context.location
        data = context.data

        # 명령어 이름
        if loc is app_commands.TranslationContextLocation.command_name:
            if isinstance(data, app_commands.Command):
                mapping = {
                    "mz_money":        "면진돈줘",
                    "mz_attend":       "면진출첵",
                    "mz_rank":         "면진순위",
                    "mz_bet":          "면진도박",
                    "mz_config_view":  "면진설정보기",
                    "mz_config_set":   "면진설정수정",
                    "mz_balance":      "면진잔액수정",
                }
                return mapping.get(data.name)

        # 명령어 설명
        if loc is app_commands.TranslationContextLocation.command_description:
            if isinstance(data, app_commands.Command):
                desc_map = {
                    "mz_money":       "10분마다 1,000 코인 지급",
                    "mz_attend":      "하루에 한 번 10,000 코인 지급",
                    "mz_rank":        "서버 잔액 순위 TOP 10",
                    "mz_bet":         "승률 30~60% 랜덤, 결과는 ±베팅액 (최소 1,000₩)",
                    "mz_config_view": "서버 설정 보기(관리자 전용)",
                    "mz_config_set":  "서버 설정 수정(관리자 전용)",
                    "mz_balance":     "특정 사용자의 잔액을 설정/증가/감소 (관리자 전용)",
                }
                return desc_map.get(data.name)

        # 파라미터 이름
        if loc is app_commands.TranslationContextLocation.parameter_name:
            if isinstance(data, app_commands.Parameter):
                if data.name == "amount": return "금액"
                if data.name == "field":  return "항목"
                if data.name == "value":  return "값"
                if data.name == "user":   return "대상"
                if data.name == "op":     return "동작"
                if data.name == "reason": return "사유"

        # 파라미터 설명
        if loc is app_commands.TranslationContextLocation.parameter_description:
            if isinstance(data, app_commands.Parameter):
                if data.name == "amount": return "베팅 금액(정수, 최소 1,000₩)"
                if data.name == "field":  return "수정할 항목을 선택"
                if data.name == "value":  return "값(정수 또는 문자열). 승률은 % 단위"
                if data.name == "user":   return "잔액을 변경할 대상 사용자"
                if data.name == "op":     return "설정(=) / 증가(+) / 감소(-) 중 선택"
                if data.name == "reason": return "변경 사유(선택)"

        return None

# ─────────────────────────────────────────────────────────────────────
# Bot
# ─────────────────────────────────────────────────────────────────────
INTENTS = discord.Intents.default()
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

    # 개발 중에는 길드 한정 동기화가 빠릅니다.
    if DEV_GUILD_ID:
        await bot.tree.sync(guild=discord.Object(id=int(DEV_GUILD_ID)))
    else:
        await bot.tree.sync()

bot.setup_hook = setup_hook

if __name__ == "__main__":
    bot.run(TOKEN)
