import aiosqlite, secrets, json, time
import discord
from discord import app_commands

DB_PATH = "economy.db"
MAX_BET = 10_000

# 매 라운드 승률 범위(기본 30%~60%)
WIN_PROB_MIN_BPS = 3000   # 30.00%
WIN_PROB_MAX_BPS = 6000   # 60.00%

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

# /면진도박 (mz_bet): 승률 30~60% 랜덤, 결과는 ±베팅액
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

        # 이번 라운드 승률을 30~60%에서 균등 난수로 결정
        prob_bps = WIN_PROB_MIN_BPS + secrets.randbelow(WIN_PROB_MAX_BPS - WIN_PROB_MIN_BPS + 1)
        roll = secrets.randbelow(10_000)  # 0~9999
        win = roll < prob_bps

        if win:
            new_bal = u["balance"] + amount          # 순증가 = +베팅액
            delta = amount
            kind = "bet_win"
            msg = f"승리! +{delta:,} → 잔액 {new_bal:,}"
        else:
            new_bal = u["balance"] - amount          # 순감소 = -베팅액
            delta = -amount
            kind = "bet_lose"
            msg = f"패배… {delta:,} → 잔액 {new_bal:,}"

        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(
            db, gid, uid, kind, delta, new_bal,
            {"game": "random_bet", "bet": amount, "prob_bps": prob_bps, "roll": roll}
        )
        await db.commit()

    detail = f"이번 라운드 승률 {prob_bps/100:.2f}% · 결과는 ±베팅액"
    await interaction.response.send_message(f"{msg}\n`{detail}`")

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_bet)
