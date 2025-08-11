# cogs/help.py
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
def now_kst_str(): return datetime.now(KST).strftime("%Y-%m-%d %H:%M")

HELP_LINES = [
    ("빠른 시작", "`/면진돈줘`, `/면진출첵`, `/면진잔액`"),
    ("주식", "“작은 변동이 많고 안정적. 큰 변동은 드물다.”\n재구식품(가장 안정) · 대이식스(안정) · 성현전자(중간) · 배달의 승기(변동 큼)"),
    ("코인", "“고수익·고변동. 가끔 큰 급등/급락, 원금 이상을 잃을 수 있음.”\n건영(중간 변동) · 면진(높은 변동) · 승철(매우 높은 변동)"),
    ("파산", "“잔액이 음수일 때 10분마다 신청 가능.”"),
    ("AI/게임", "`/면진타로`(3장 풀이, 공개), `/면진지니 질문:<텍스트>`(짧은 답)"),
    ("동기화 팁", "“Ctrl+R로 새로고침.”"),
]

@app_commands.command(name="mz_help", description="면진이 사용법과 주식/코인 가이드")
async def mz_help(interaction: discord.Interaction):
    em = discord.Embed(title="면진도움말", color=0x5865F2)
    for name, val in HELP_LINES:
        em.add_field(name=name, value=val, inline=False)
    if interaction.guild and interaction.guild.icon:
        try: em.set_thumbnail(url=interaction.guild.icon.url)
        except Exception: pass
    em.set_footer(text=f"{interaction.guild.name if interaction.guild else '면진이'} · {now_kst_str()}")
    await interaction.response.send_message(embed=em)  # 항상 공개

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_help)
