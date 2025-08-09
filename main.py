# main.py
import os
import importlib.util
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import aiosqlite

DB_PATH = "economy.db"

def module_exists(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None

# ───────── 번역기 ─────────
class MZTranslator(app_commands.Translator):
    async def translate(self, string: app_commands.locale_str,
                        locale: discord.Locale,
                        context: app_commands.TranslationContext) -> str | None:
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
                    "mz_transfer":     "면진송금",
                    "mz_admin":        "면진관리자",
                    "mz_ask":          "면진질문",
                    "mz_tarot":        "면진타로",
                    "mz_genie":        "면진지니",
                    "mz_stock":        "면진주식",
                    "mz_coin":         "면진코인",
                    "mz_bankruptcy":   "면진파산",   # 표시 이름
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
                    "mz_tarot":        "타로 3장 해석(5초 후 공개, 채널에 표시)",
                    "mz_genie":        "면진지니: Gemini로 짧은 답변 생성",
                    "mz_stock":        "가상 주식 투자(5초 후 결과 공개, 퍼센트 손익)",
                    "mz_coin":         "가상 코인 러시(잭팟 계단형, 5초 후 공개)",
                    "mz_bankruptcy":   "잔액이 음수일 때 10분마다 부채 복구 시도",
                }
                return desc_map.get(data.name)

        # 파라미터 이름 로컬라이즈(한글화)는 비활성화 — Discord 검증 충돌 방지

        if loc is app_commands.TranslationContextLocation.parameter_description:
            if isinstance(data, app_commands.Parameter):
                if data.name == "amount":   return "정수 금액(최소 베팅 이상)"
                if data.name == "symbol":   return "종목(주식)/코인(가상 자산)"
                if data.name == "question": return "질문 내용"
                if data.name == "member":   return "받는 사람 선택"
                if data.name == "user":     return "대상 사용자"
        return None


# ───────── 기본 설정/봇 생성 ─────────
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID", "").strip()

INTENTS = discord.Intents.default()
INTENTS.message_content = False
bot = commands.Bot(command_prefix=commands.when_mentioned_or("!"), intents=INTENTS)

# ───────── DB 초기화 ─────────
async def init_db():
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
    try:
        await bot.load_extension("cogs.games")
    except Exception:
        pass
    await bot.load_extension("cogs.admin")
    await bot.load_extension("cogs.fun")
    await bot.load_extension("cogs.tarot")
    if module_exists("cogs.genie"):
        await bot.load_extension("cogs.genie")
    else:
        print("[load] cogs.genie not found — skipping")
    await bot.load_extension("cogs.markets")  # 주식/코인/면진파산

    # 번역기 등록
    await bot.tree.set_translator(MZTranslator())

    # 길드 우선 싱크 → 그 다음 전역 정리(중복 방지)
    gids = [g.strip() for g in DEV_GUILD_ID.split(",") if g.strip()]
    if gids:
        for gid in gids:
            gobj = discord.Object(id=int(gid))
            bot.tree.copy_global_to(guild=gobj)           # ① 글로벌→길드 복사
            synced = await bot.tree.sync(guild=gobj)       # ② 길드 싱크
            print(f"[sync] guild {gid} -> {len(synced)} cmds (copied global)")

        # ③ 마지막에 전역 비우고 싱크(길드에는 이미 반영돼 유지)
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        print("[sync] cleared global commands")
    else:
        synced = await bot.tree.sync()
        print(f"[sync] global -> {len(synced)} cmds")

bot.setup_hook = setup_hook

# ───────── 실행 ─────────
bot.run(TOKEN)
