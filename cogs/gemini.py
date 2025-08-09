# cogs/gemini.py
import os
import google.generativeai as genai
import discord
from discord import app_commands

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
MODEL_NAME = "gemini-1.5-flash"  # 빠른 응답, 필요시 pro로 교체

def _split_chunks(s: str, limit: int = 1900):
    # 디스코드 2000자 제한 대응
    out, buf = [], s
    while buf:
        out.append(buf[:limit]); buf = buf[limit:]
    return out

@app_commands.command(name="mz_gemini", description="Gemini에 질문하기 (텍스트)")
@app_commands.describe(prompt="질문/지시문")
async def mz_gemini(interaction: discord.Interaction, prompt: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    model = genai.GenerativeModel(MODEL_NAME)
    resp = model.generate_content(prompt)
    text = resp.text or "(빈 응답)"
    for chunk in _split_chunks(text):
        await interaction.followup.send(chunk, ephemeral=True)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_gemini)
