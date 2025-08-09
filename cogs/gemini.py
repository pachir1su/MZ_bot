# cogs/gemini.py
# /mz_gemini (텍스트 Q&A)
# - prompt : 질문/지시문 (필수)
# - model  : gemini-1.5-flash | gemini-1.5-pro (선택, 기본 flash)
# - public : 채널에 공개할지 여부(기본 False=비공개)
#
# 필요 패키지:
#   pip install google-generativeai
#
# 환경변수:
#   GOOGLE_API_KEY=콘솔에서 발급한 키

from __future__ import annotations
import os
import traceback
from typing import Optional, Iterable

import discord
from discord import app_commands
import google.generativeai as genai

# ─────────────────────────────────────────────────────────
# 설정
API_KEY = os.getenv("GOOGLE_API_KEY", "")
DEFAULT_MODEL = "gemini-1.5-flash"  # 속도 우선; 정확도는 pro
MODEL_CHOICES = ["gemini-1.5-flash", "gemini-1.5-pro"]

# GenAI 클라이언트 초기화(키가 없으면 명령 실행 시 안내)
if API_KEY:
    genai.configure(api_key=API_KEY)

# ─────────────────────────────────────────────────────────
# 유틸
def _chunks(s: str, limit: int = 1900) -> Iterable[str]:
    """디스코드 2000자 제한 대비 분할."""
    s = s or ""
    for i in range(0, len(s), limit):
        yield s[i:i + limit]

def _ensure_client_ready() -> Optional[str]:
    """키 미설정 시 에러 메시지 반환."""
    if not API_KEY:
        return (
            "Gemini API 키가 설정되어 있지 않습니다.\n"
            "서버 환경변수 `GOOGLE_API_KEY`를 설정한 뒤 다시 시도해 주세요."
        )
    return None

# ─────────────────────────────────────────────────────────
# 슬래시 명령
@app_commands.command(name="mz_gemini", description="Gemini에 질문하기 (텍스트)")
@app_commands.describe(
    prompt="질문/지시문",
    model="사용할 모델(기본: gemini-1.5-flash)",
    public="채널에 공개할지 여부(기본 비공개)",
)
@app_commands.choices(
    model=[app_commands.Choice(name=m, value=m) for m in MODEL_CHOICES]
)
@app_commands.checks.cooldown(1, 6.0)  # 유저당 6초 쿨다운
async def mz_gemini(
    interaction: discord.Interaction,
    prompt: str,
    model: Optional[app_commands.Choice[str]] = None,
    public: Optional[bool] = False,
):
    # 키 유효성
    err = _ensure_client_ready()
    if err:
        return await interaction.response.send_message(err, ephemeral=True)

    # 응답 지연 처리
    await interaction.response.defer(ephemeral=not public, thinking=True)

    model_name = model.value if isinstance(model, app_commands.Choice) else DEFAULT_MODEL

    try:
        gm = genai.GenerativeModel(model_name)
        # 필요 시 system 지침을 앞에 붙여도 됨
        resp = gm.generate_content(prompt)
        text = (resp.text or "").strip() or "(응답이 비어 있습니다.)"

        # 길이 분할 전송
        for part in _chunks(text):
            await interaction.followup.send(part, ephemeral=not public)

    except Exception as e:
        # 간단한 오류 보고
        msg = f"요청 처리 중 문제가 발생했습니다.\n`{type(e).__name__}: {e}`"
        await interaction.followup.send(msg, ephemeral=True)
        # 서버 로그용(필요 시 로깅 시스템으로 교체)
        traceback.print_exc()

# 공통 에러 처리(쿨다운 등)
@mz_gemini.error
async def _gemini_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        return await interaction.response.send_message(
            f"잠시 후 다시 시도해 주세요. 남은 시간: {error.retry_after:.1f}s",
            ephemeral=True,
        )
    # 이미 응답되었는지에 따라 분기
    try:
        await interaction.response.send_message("명령 실행 중 문제가 발생했습니다.", ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send("명령 실행 중 문제가 발생했습니다.", ephemeral=True)
    raise error  # 상위 로거로 전달(옵션)

# ─────────────────────────────────────────────────────────
# setup
async def setup(bot: discord.Client):
    bot.tree.add_command(mz_gemini)
