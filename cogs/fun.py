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

    embed = discord.Embed(title="면진의 대답", color=0x5865F2)
    embed.add_field(name="질문", value=question, inline=False)
    embed.add_field(name="답변", value=f"**{reply}**", inline=False)
    try:
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        pass

    await interaction.response.send_message(embed=embed)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_ask)
