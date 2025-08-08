import random
import discord
from discord import app_commands

ANSWERS = [
    "말도 안돼!!",
    "아마도",
    "그런 거 같아",
    "그럴 수도?",
    "나도 그렇게 생각해",
    "물론이지",
    "절대 아니야",
]

@app_commands.command(name="mz_ask", description="Ask and get a random answer")
@app_commands.describe(question="질문 내용")
async def mz_ask(interaction: discord.Interaction, question: str):
    pick = random.choice(ANSWERS)
    await interaction.response.send_message(f"질문: {question}\n면진이: {pick}")

# 한글 표기(Localization)
mz_ask.name_localizations = {"ko": "면진질문"}
mz_ask.description_localizations = {"ko": "질문을 보내면 랜덤으로 대답합니다"}

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_ask)
