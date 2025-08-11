# cogs/help.py
import discord
from discord import app_commands
from discord.ext import commands

class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="mz_help", description="면진이 명령어 도움말")
    async def mz_help(self, interaction: discord.Interaction):
        em = discord.Embed(title="면진이 — 도움말", color=0x3498db)

        em.add_field(
            name="기본",
            value=(
                "• **/면진돈줘** — 10분마다 1,000 코인\n"
                "• **/면진출첵** — 자정 초기화 출석 보상\n"
                "• **/면진잔액** — 잔액 확인 / 대상 선택 가능\n"
                "• **/면진송금** — 멤버에게 코인 송금"
            ),
            inline=False
        )

        em.add_field(
            name="투자/게임",
            value=(
                "• **/면진도박** — 승률 30~60%, 결과는 ±베팅액\n"
                "• **/면진주식** — 3초 후 결과, 0=전액\n"
                "• **/면진코인** — 3초 후 결과, 0=전액"
            ),
            inline=False
        )

        em.add_field(
            name="강화/전투",
            value=(
                "• **/면진강화** — 무기 강화 **+30**(1분 무응답 자동 취소). "
                "**+10까지 쉬움**, 이후 난이도 상승\n"
                "• **/면진맞짱** — 강화 무기로 PvP(차기 단계)"
            ),
            inline=False
        )

        em.add_field(
            name="관리자",
            value="• **/면진관리자** — 설정/잔액/쿨타임/마켓 편집(관리자 전용)",
            inline=False
        )

        await interaction.response.send_message(embed=em, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
