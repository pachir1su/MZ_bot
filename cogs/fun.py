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

@app_commands.command(name="mz_ask", description="Ask a question and get a random answer")
@app_commands.describe(question="Your question")
async def mz_ask(interaction: discord.Interaction, question: str):
    reply = random.choice(ANSWERS)
    # 임베드 대신 간단한 텍스트 응답
    await interaction.response.send_message(f"질문: {question}\n면진이: {reply}")

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_ask)
