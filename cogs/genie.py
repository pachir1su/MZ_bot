# cogs/genie.py
"""
면진지니(/mz_genie): Gemini 기반 짧은 Q&A
- 항상 공개 메시지로 응답
- 퍼포먼스 안정화를 위한 타임아웃/예외 처리 포함
- gemini.py가 있으면 그 설정을 재사용, 없으면 flash 모델로 바로 구동
"""

import os
import asyncio
import aiosqlite
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

DB_PATH = "economy.db"

# ── 시간/표시 유틸 ──────────────────────────────────────
KST = timezone(timedelta(hours=9))
def now_kst_str() -> str: return datetime.now(KST).strftime("%H:%M")
def footer_text(mode_name: str) -> str:
    return f"현재 모드 : {mode_name} · 오늘 {now_kst_str()}"

# ── 모드명 조회(푸터용) ─────────────────────────────────
async def get_mode_name(gid: int) -> str:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT mode_name FROM guild_settings WHERE guild_id=?", (gid,))
            row = await cur.fetchone()
            return (row[0] if row and row[0] else "일반 모드")
    except Exception:
        return "일반 모드"

# ── Gemini 클라이언트 준비 ─────────────────────────────
def _get_gemini_model():
    """
    1) 프로젝트의 gemini.py에 정의된 get_model()을 우선 사용
    2) 없으면 로컬에서 바로 flash 모델 생성
    """
    try:
        # 프로젝트 내 gemini.py 재사용
        from gemini import get_model  # type: ignore
        return get_model()  # 기본 모델은 gemini.py에서 flash로 설정되어 있음
    except Exception:
        # 직접 초기화
        import google.generativeai as genai  # type: ignore
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY 환경변수가 필요합니다.")
        genai.configure(api_key=api_key)
        return genai.GenerativeModel("gemini-1.5-flash")

# 전역 모델(프로세스 당 1회 초기화)
_MODEL = None
def _model():
    global _MODEL
    if _MODEL is None:
        _MODEL = _get_gemini_model()
    return _MODEL

# ── 슬래시 명령: 면진지니 ───────────────────────────────
@app_commands.command(name="mz_genie", description="면진지니: Gemini로 짧은 답변 생성(항상 공개)")
@app_commands.describe(question="질문 텍스트")
async def mz_genie(interaction: discord.Interaction, question: str):
    gid = interaction.guild.id
    mode_name = await get_mode_name(gid)

    q = (question or "").strip()
    if not q:
        return await interaction.response.send_message("질문 내용을 입력해 주세요.", ephemeral=False)
    if len(q) > 600:
        q = q[:600]

    # 즉시 디퍼(항상 공개)
    await interaction.response.defer(thinking=True)

    # 프롬프트(간결/정확/금지어 가드)
    system = (
        "너는 '면진지니'라는 이름의 조수다. 한국어로 간결하고 명확하게 답한다. "
        "핵심만 3~6문장으로 정리하고, 필요하면 번호나 불릿을 사용한다. "
        "사과/면책/장황한 수사는 쓰지 않는다. 코드나 표는 사용자가 요청할 때만 제공한다."
    )
    instr = f"질문: {q}"

    try:
        model = _model()
        # 라이브러리 호환: generate_content는 동기 함수(google-generativeai)
        # 블로킹을 줄이기 위해 스레드 실행
        def _infer():
            return model.generate_content(
                [system, instr],
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_output_tokens": 300,
                },
            )
        resp = await asyncio.to_thread(_infer)
        text = (resp.text or "").strip()
        if not text:
            text = "응답을 구성하지 못했어요. 문장을 조금 더 구체적으로 적어 주세요."

        # 임베드로 깔끔하게 출력
        em = discord.Embed(title="면진지니", color=0x1abc9c)
        em.add_field(name="질문", value=q, inline=False)
        # Discord 임베드 필드 최대 길이 대비
        if len(text) > 1024:
            text = text[:1010] + "…"
        em.add_field(name="답변", value=text, inline=False)
        em.set_footer(text=footer_text(mode_name))
        await interaction.followup.send(embed=em)

    except Exception as e:
        # 과금/레이트리밋/네트워크 등 일반 예외 처리
        msg = "지금은 요청이 몰려 있어 응답 생성이 지연됐습니다. 잠시 후 다시 시도해 주세요."
        try:
            await interaction.followup.send(msg)
        except Exception:
            # followup 실패 시(이미 응답 등) 대비
            pass

# ── 코그 로드 ───────────────────────────────────────────
async def setup(bot: discord.Client):
    bot.tree.add_command(mz_genie)
