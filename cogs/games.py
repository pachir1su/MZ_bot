import aiosqlite, secrets, json, time
from pathlib import Path
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

DB_PATH = str(Path(__file__).resolve().parent.parent / "economy.db")

# ── 표시 유틸 ───────────────────────────────────────────
KST = timezone(timedelta(hours=9))
def won(n: int) -> str: return f"{n:,}₩"
def footer_text(balance: int, mode_name: str) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    return f"잔액 : {won(balance)} | 현재 모드 : {mode_name} · 오늘 {now}"

# DB 유틸
async def get_user(db, gid, uid):
    cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid))
    row = await cur.fetchone()
    if row: return {"balance": row[0]}
    await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, uid, 0))
    await db.commit()
    return {"balance": 0}

async def write_ledger(db, gid, uid, kind, amount, bal_after, meta=None):
    await db.execute(
        "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) VALUES(?,?,?,?,?,?,?)",
        (gid, uid, kind, amount, bal_after, json.dumps(meta or {}), int(time.time()))
    )

async def get_settings(db, gid: int):
    cur = await db.execute("SELECT min_bet, win_min_bps, win_max_bps, mode_name FROM guild_settings WHERE guild_id=?", (gid,))
    row = await cur.fetchone()
    if row: return {"min_bet": row[0], "win_min_bps": row[1], "win_max_bps": row[2], "mode_name": row[3]}
    return {"min_bet": 1000, "win_min_bps": 3000, "win_max_bps": 6000, "mode_name": "일반 모드"}

# /면진도박 : 승률 30~60% 랜덤(길드 설정 반영), 결과는 ±베팅액 — 임베드 출력
@app_commands.command(
    name="mz_bet",
    description="Random win chance; net result is ±bet amount"
)
@app_commands.describe(amount="bet amount (integer, min by server setting)")
async def mz_bet(interaction: discord.Interaction, amount: int):
    gid, uid = interaction.guild.id, interaction.user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        s = await get_settings(db, gid)
        min_bet = s["min_bet"]
        if amount < min_bet:
            await db.execute("ROLLBACK")
            return await interaction.response.send_message(f"베팅 최소 금액은 {won(min_bet)} 입니다.")
        u = await get_user(db, gid, uid)
        if u["balance"] < amount:
            await db.execute("ROLLBACK")
            return await interaction.response.send_message("잔액 부족입니다.")

        low, high = s["win_min_bps"], s["win_max_bps"]
        if high < low: low, high = high, low
        prob_bps = low + secrets.randbelow(high - low + 1)  # ex) 3000~6000 → 30~60%
        roll = secrets.randbelow(10_000)
        win = roll < prob_bps

        if win:
            new_bal = u["balance"] + amount
            delta, kind, color, title = amount, "bet_win", 0x2ecc71, "도박에 성공했어요"
            result_line = f"**결과 :** +{won(amount)}"
        else:
            new_bal = u["balance"] - amount
            delta, kind, color, title = -amount, "bet_lose", 0xe74c3c, "도박에 실패했어요"
            result_line = f"**결과 :** -{won(amount)}"

        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, kind, delta, new_bal,
                           {"game": "random_bet", "bet": amount, "prob_bps": prob_bps, "roll": roll})
        await db.commit()

    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="승리 확률", value=f"**{prob_bps/100:.0f}%**", inline=False)
    embed.add_field(name="\u200b", value=result_line, inline=False)
    embed.set_footer(text=footer_text(new_bal, s["mode_name"]))
    try:
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        pass
    await interaction.response.send_message(embed=embed)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_bet)
