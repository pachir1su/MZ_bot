# cogs/tarot.py
# /면진타로 (mz_tarot)
# - spread: 1장(조언) 또는 3장(과거/현재/미래)
# - question: 질문(선택)
# - public: 채널 공개 여부 (기본 비공개)
import os, secrets
from typing import List, Tuple, Optional

import discord
from discord import app_commands
import google.generativeai as genai

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
MODEL_NAME = "gemini-1.5-pro"   # 설명력 우선, 속도 필요시 "gemini-1.5-flash"

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

    # 포지션 라벨
    positions = ["Advice"] if n == 1 else ["Past", "Present", "Future"]

    # 카드 텍스트(예: "The Sun (Reversed)")
    labeled = []
    for (name, rev), pos in zip(picks, positions):
        labeled.append(f"{pos}: {name}{' (Reversed)' if rev else ''}")

    # Gemini 프롬프트
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
            "1) 카드 의미(정방향/역방향 반영, 3~5문장)\n"
            "2) 핵심 조언 3가지(불릿)\n"
            "3) 한 줄 요약(이모지 없이)"
        )
    else:
        instr = (
            f"질문: {question or '일반 운세'}\n"
            f"카드(3장):\n- {labeled[0]}\n- {labeled[1]}\n- {labeled[2]}\n\n"
            "구성:\n"
            "1) 각 카드가 의미하는 바(각 2~4문장)\n"
            "2) 종합 해석(3~6문장)\n"
            "3) 실행 가능한 조언 3가지(불릿)\n"
            "4) 한 줄 요약(이모지 없이)"
        )

    model = genai.GenerativeModel(MODEL_NAME)
    resp = model.generate_content([system, instr])
    text = (resp.text or "결과가 없습니다.").strip()

    # 카드 표시 임베드
    title = "타로 리딩 결과"
    em = discord.Embed(title=title, color=0x9b59b6)
    em.add_field(name="스프레드", value=("1장 조언" if n == 1 else "3장(과거/현재/미래)"), inline=True)
    em.add_field(name="질문", value=(question or "일반 운세"), inline=False)
    em.add_field(name="카드", value="\n".join(labeled), inline=False)
    try:
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        pass

    await interaction.followup.send(embed=em, ephemeral=not public)
    for part in chunks(text):
        await interaction.followup.send(part, ephemeral=not public)

# 에러 메시지(쿨다운 등) 깔끔히 처리
@mz_tarot.error
async def _tarot_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        return await interaction.response.send_message(
            f"잠시 후 다시 시도해 주세요. 남은 시간: {error.retry_after:.1f}s", ephemeral=True
        )
    raise error

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_tarot)
