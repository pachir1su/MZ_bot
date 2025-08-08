import aiosqlite, secrets, json, time
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

DB_PATH = "economy.db"
MAX_BET = 10_000

# 매 라운드 승률 범위(30%~60%)
WIN_PROB_MIN_BPS = 3000   # 30.00%
WIN_PROB_MAX_BPS = 6000   # 60.00%

# ── 표시 유틸 ───────────────────────────────────────────
KST = timezone(timedelta(hours=9))
MODE_NAME = "일반 모드"

def won(n: int) -> str:
    return f"{n:,}₩"

def footer_text(balance: int) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    return f"잔액 : {won(balance)} | 현재 모드 : {MODE_NAME} · 오늘 {now}"

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

# /면진도박 (mz_bet): 승률 30~60% 랜덤, 결과는 ±베팅액 — 임베드 출력
@app_commands.command(
    name="mz_bet",
    description="Random win chance 30–60%; net result is ±bet amount"
)
@app_commands.describe(amount="bet amount (integer)")
async def mz_bet(interaction: discord.Interaction, amount: int):
    if amount <= 0 or amount > MAX_BET:
        return await interaction.response.send_message(f"베팅 금액은 1~{MAX_BET:,} 범위입니다.")

    gid, uid = interaction.guild.id, interaction.user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        if u["balance"] < amount:
            await db.execute("ROLLBACK")
            return await interaction.response.send_message("잔액 부족입니다.")

        # 이번 라운드 승률(30~60%) 결정
        prob_bps = WIN_PROB_MIN_BPS + secrets.randbelow(WIN_PROB_MAX_BPS - WIN_PROB_MIN_BPS + 1)
        roll = secrets.randbelow(10_000)  # 0~9999
        win = roll < prob_bps

        if win:
            new_bal = u["balance"] + amount
            delta = amount
            kind = "bet_win"
            color = 0x2ecc71  # green
            title = "도박에 성공했어요"
            result_line = f"**결과 :** +{won(amount)}"
        else:
            new_bal = u["balance"] - amount
            delta = -amount
            kind = "bet_lose"
            color = 0xe74c3c  # red
            title = "도박에 실패했어요"
            result_line = f"**결과 :** -{won(amount)}"

        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(
            db, gid, uid, kind, delta, new_bal,
            {"game": "random_bet", "bet": amount, "prob_bps": prob_bps, "roll": roll}
        )
        await db.commit()

    # 임베드 구성
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="승리 확률", value=f"**{prob_bps/100:.0f}%**", inline=False)
    embed.add_field(name="\u200b", value=result_line, inline=False)
    embed.set_footer(text=footer_text(new_bal))
    try:
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        pass

    await interaction.response.send_message(embed=embed)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_bet)
