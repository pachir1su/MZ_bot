import time
import discord
from discord import app_commands

@app_commands.command(name="mz_ping", description="봇의 핑 확인")
async def mz_ping(interaction: discord.Interaction):
    started = time.perf_counter()
    await interaction.response.send_message("핑 측정 중…")
    msg = await interaction.original_response()
    api_ms = (time.perf_counter() - started) * 1000.0
    ws_ms = getattr(interaction.client, "latency", 0.0) * 1000.0

    em = discord.Embed(title="핑", color=0x3498db)
    em.add_field(name="WebSocket", value=f"{ws_ms:.0f} ms", inline=True)
    em.add_field(name="REST(API)", value=f"{api_ms:.0f} ms", inline=True)

    try:
        await msg.edit(content=None, embed=em)
    except Exception:
        await interaction.followup.send(embed=em)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_ping)
