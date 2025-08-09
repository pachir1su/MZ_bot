# cogs/tarot.py
# /면진타로 (mz_tarot)
# - spread: 1장(조언) 또는 3장(과거/현재/미래)
# - question: 질문(선택)
# - public: 채널 공개 여부 (기본 비공개)
import os, secrets, asyncio
from typing import List, Tuple, Optional

import discord
from discord import app_commands
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
# 안정성 우선: 기본은 flash, 필요시 관리자에서 pro로 바꿔도 됨
PRIMARY_MODEL = "gemini-1.5-flash"
FALLBACK_MODEL = "gemini-1.5-flash"  # 동일 유지(원하면 pro→flash 대체 시 사용)

# ── 덱 구성 ──────────────────────────────────────────────
MAJOR = [
    "The Fool","The Magician","The High Priestess","The Empress","The Emperor",
    "The Hierophant","The Lovers","The Chariot","Strength","The Hermit",
    "Wheel of Fortune","Justice","The Hanged Man","Death","Temperance",
    "The Devil","The Tower","The Star","The Moon","The Sun","Judgement","The World"
]
SUITS = ["Wands","Cups","Swords","Pentacles"]
RANKS = ["Ace","Two","Three","Four","Five","Six","Seven","Eight","Nine","Ten","Page","Knight","Queen","King"]

def build_deck() -> List[str]:
    deck = MAJOR[:]
    for s in SUITS:
        for r in RANKS:
            deck.append(f"{r} of {s}")
    return deck  # 78장

def draw_cards(n: int) -> List[Tuple[str, bool]]:
    """n장을 중복 없이 뽑고, 각 카드에 역방향 여부(50%)를 부여한다. (True=역방향)"""
    rng = secrets.SystemRandom()
    deck = build_deck()
    rng.shuffle(deck)
    picks = deck[:n]
    return [(c, rng.choice([False, True])) for c in picks]

def chunks(s: str, limit: int = 1900):
    for i in range(0, len(s), limit):
        yield s[i:i+limit]

SEM = asyncio.Semaphore(2)  # API 동시 호출 2개로 제한(폭주 완화)

async def _gemini_call(prompt: str, model_name: str) -> str:
    model = genai.GenerativeModel(model_name)
    resp = model.generate_content(prompt)
    return (resp.text or "").strip()

def _error_embed(title: str, desc: str) -> discord.Embed:
    em = discord.Embed(title=title, description=desc, color=0xe67e22)
    em.set_footer(text="참고: Gemini API rate limits / troubleshooting")
    return em

# ── 슬래시 명령 ─────────────────────────────────────────
SpreadChoice = app_commands.Choice[str]

@app_commands.command(name="mz_tarot", description="타로 카드로 상황을 해석합니다 (Gemini)")
@app_commands.describe(
    question="질문/상황(선택, 비우면 일반 운세)",
    public="채널에 공개할지 여부(기본 비공개)"
)
@app_commands.choices(
    spread=[
        app_commands.Choice(name="1장: 조언", value="1"),
        app_commands.Choice(name="3장: 과거/현재/미래", value="3"),
    ]
)
@app_commands.checks.cooldown(1, 8.0)  # 유저당 8초 쿨다운
async def mz_tarot(
    interaction: discord.Interaction,
    spread: SpreadChoice,
    question: Optional[str] = None,
    public: Optional[bool] = False,
):
    await interaction.response.defer(ephemeral=not public, thinking=True)

    n = 1 if spread.value == "1" else 3
    picks = draw_cards(n)
    positions = ["Advice"] if n == 1 else ["Past", "Present", "Future"]
    labeled = [f"{pos}: {name}{' (Reversed)' if rev else ''}" for (name, rev), pos in zip(picks, positions)]

    system = (
        "You are a professional tarot reader. "
        "Explain clearly in Korean for a general audience. "
        "Be practical and constructive. Avoid superstition claims."
    )
    if n == 1:
        instr = (
            f"질문: {question or '일반 운세'}\n"
            f"카드(1장): {labeled[0]}\n\n"
            "구성:\n"
            "1) 카드 의미(정/역방향 반영, 3~5문장)\n"
            "2) 핵심 조언 3가지(불릿)\n"
            "3) 한 줄 요약(이모지 없이)"
        )
    else:
        instr = (
            f"질문: {question or '일반 운세'}\n"
            f"카드(3장):\n- {labeled[0]}\n- {labeled[1]}\n- {labeled[2]}\n\n"
            "구성:\n"
            "1) 각 카드 의미(각 2~4문장)\n"
            "2) 종합 해석(3~6문장)\n"
            "3) 실행 가능한 조언 3가지(불릿)\n"
            "4) 한 줄 요약(이모지 없이)"
        )
    prompt = f"{system}\n\n{instr}"

    # 카드 정보 임베드(콘텐츠는 뒤에서 전송)
    em = discord.Embed(title="타로 리딩 결과", color=0x9b59b6)
    em.add_field(name="스프레드", value=("1장 조언" if n == 1 else "3장(과거/현재/미래)"), inline=True)
    em.add_field(name="질문", value=(question or "일반 운세"), inline=False)
    em.add_field(name="카드", value="\n".join(labeled), inline=False)
    try:
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        pass

    # 호출부: 429 처리, 재시도, 폴백
    async with SEM:
        try:
            text = await _gemini_call(prompt, PRIMARY_MODEL)
        except ResourceExhausted as e:
            # google가 내려주는 retry_delay가 있으면 그만큼 대기 후 1회 재시도
            retry_seconds = getattr(getattr(e, "retry_delay", None), "seconds", None)
            if retry_seconds is None:
                retry_seconds = 20  # 안전 기본값
            wait = min(int(retry_seconds), 60)  # 상호작용 제한 내에서만 대기
            note = f"사용량 한도에 도달하여 {wait}초 대기 후 재시도합니다."
            await interaction.followup.send(embed=_error_embed("잠시 대기 중", note), ephemeral=not public)
            await asyncio.sleep(wait)
            # 1회 재시도(동일 모델), 실패 시 폴백
            try:
                text = await _gemini_call(prompt, PRIMARY_MODEL)
            except ResourceExhausted:
                # 폴백 모델로 한 번 더 시도
                if FALLBACK_MODEL != PRIMARY_MODEL:
                    try:
                        text = await _gemini_call(prompt, FALLBACK_MODEL)
                    except Exception as e2:
                        msg = (
                            "현재 Gemini 쿼터가 가득 찼습니다. 잠시 후 다시 시도해 주세요.\n"
                            "자세한 한도 정책은 공식 문서를 참고해 주세요."
                        )
                        await interaction.followup.send(embed=_error_embed("요청 한도 초과(429)", msg), ephemeral=not public)
                        return
                else:
                    msg = (
                        "현재 Gemini 쿼터가 가득 찼습니다. 잠시 후 다시 시도해 주세요.\n"
                        "자세한 한도 정책은 공식 문서를 참고해 주세요."
                    )
                    await interaction.followup.send(embed=_error_embed("요청 한도 초과(429)", msg), ephemeral=not public)
                    return
        except GoogleAPIError as e:
            msg = f"외부 서비스 응답이 원활하지 않습니다. 잠시 후 다시 시도해 주세요. ({type(e).__name__})"
            await interaction.followup.send(embed=_error_embed("서비스 지연", msg), ephemeral=not public)
            return
        except Exception as e:
            msg = f"처리 중 문제가 발생했습니다. ({type(e).__name__})"
            await interaction.followup.send(embed=_error_embed("처리 실패", msg), ephemeral=not public)
            return

    await interaction.followup.send(embed=em, ephemeral=not public)
    if not text:
        text = "결과가 없습니다."
    for part in chunks(text):
        await interaction.followup.send(part, ephemeral=not public)

# 에러 핸들러: 여기서는 추가 재전파하지 않음(무한로딩 방지)
@mz_tarot.error
async def _tarot_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    try:
        await interaction.response.send_message(
            embed=_error_embed("명령 처리 중 문제가 발생했습니다.", "잠시 후 다시 시도해 주세요."),
            ephemeral=True
        )
    except discord.InteractionResponded:
        await interaction.followup.send(
            embed=_error_embed("명령 처리 중 문제가 발생했습니다.", "잠시 후 다시 시도해 주세요."),
            ephemeral=True
        )

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_tarot)
