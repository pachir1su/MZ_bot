# main.py
import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import aiosqlite

DB_PATH = "economy.db"

# ───────── 번역기 ─────────
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

        # 명령어 이름 한글화
        if loc is app_commands.TranslationContextLocation.command_name:
            if isinstance(data, app_commands.Command):
                mapping = {
                    "mz_money":        "면진돈줘",
                    "mz_attend":       "면진출첵",
                    "mz_rank":         "면진순위",
                    "mz_bet":          "면진도박",
                    "mz_balance_show": "면진잔액",
                    "mz_transfer":     "면진송금",    # 송금
                    "mz_admin":        "면진관리자",
                    "mz_ask":          "면진질문",    # 질문
                }
                return mapping.get(data.name)

        # 설명 한글화
        if loc is app_commands.TranslationContextLocation.command_description:
            if isinstance(data, app_commands.Command):
                desc_map = {
                    "mz_money":        "10분마다 1,000 코인 지급",
                    "mz_attend":       "자정(00:00 KST)마다 초기화되는 출석 보상",
                    "mz_rank":         "서버 잔액 순위 TOP 10(닉네임만 표시)",
                    "mz_bet":          "승률 30~60% 랜덤, 결과는 ±베팅액 (최소 1,000₩)",
                    "mz_balance_show": "현재 잔액 확인(대상 선택 가능)",
                    "mz_transfer":     "서버 멤버에게 코인을 송금합니다",
                    "mz_admin":        "관리자 메뉴 열기(관리자 전용)",
                    "mz_ask":          "질문을 보내면 랜덤으로 대답합니다",
                }
                return desc_map.get(data.name)

        # 파라미터 이름/설명 한글화
        if loc is app_commands.TranslationContextLocation.parameter_name:
            if isinstance(data, app_commands.Parameter):
                if data.name == "amount":   return "금액"
                if data.name == "user":     return "대상"
                if data.name == "member":   return "받는 사람"
                if data.name == "question": return "질문"

        if loc is app_commands.TranslationContextLocation.parameter_description:
            if isinstance(data, app_commands.Parameter):
                if data.name == "amount":   return "송금/베팅 금액(정수, 최소 1,000₩)"
                if data.name == "user":     return "대상 사용자"
                if data.name == "member":   return "받는 사람 선택"
                if data.name == "question": return "질문 내용"

        return None


# ───────── 기본 설정/봇 생성 ─────────
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID", "").strip()

INTENTS = discord.Intents.default()
INTENTS.message_content = False  # 슬래시 중심

# command_prefix는 None이면 에러가 나므로 안전값 사용
bot = commands.Bot(command_prefix=commands.when_mentioned_or("!"), intents=INTENTS)


# ───────── DB 초기화 ─────────
async def init_db():
    # models.sql을 읽어 테이블 보장
    if not os.path.exists("models.sql"):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        with open("models.sql", "r", encoding="utf-8") as f:
            await db.executescript(f.read())
        await db.commit()


# ───────── 이벤트/셋업 ─────────
@bot.event
async def on_ready():
    print(f"✅ {bot.user} 로그인")

async def setup_hook():
    await init_db()

    # 코그 로드
    await bot.load_extension("cogs.economy")
    # games 코그가 있다면 유지
    try:
        await bot.load_extension("cogs.games")
    except Exception:
        pass
    await bot.load_extension("cogs.admin")
    await bot.load_extension("cogs.fun")  # 면진질문

    # 번역기 등록
    await bot.tree.set_translator(MZTranslator())

    # 개발 길드 우선 싱크(즉시 반영)
    gids = [g.strip() for g in DEV_GUILD_ID.split(",") if g.strip()]
    if gids:
        for gid in gids:
            await bot.tree.sync(guild=discord.Object(id=int(gid)))
        print(f"[sync] guild-synced to {gids}")
    else:
        await bot.tree.sync()
        print("[sync] globally synced")

bot.setup_hook = setup_hook  # discord.py 2.3~ 스타일 대응

# ───────── 실행 ─────────
bot.run(TOKEN)
