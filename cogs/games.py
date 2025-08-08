import aiosqlite, secrets, json, time
import discord
from discord import app_commands

DB_PATH = "economy.db"
HOUSE_EDGE = 0.02                # 하우스 엣지(= 기대 손실률) 2%
WIN_PROB_BPS = 5000              # 승률 50.00% (basis points)
PAYOUT_MULTIPLE = 2 * (1 - HOUSE_EDGE)  # 승리 시 총지급 배수(예: 1.96x)
MAX_BET    = 10_000

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

# /면진도박 (기본명 mz_bet) — 앞/뒤 선택 없이 50% 승률 고정
@app_commands.command(
    name="mz_bet",
    description="Bet with 50% win chance; payout adjusted by house edge"
)
@app_commands.describe(amount="bet amount (integer)")
async def mz_bet(interaction: discord.Interaction, amount: int):
    if amount <= 0 or amount > MAX_BET:
        return await interaction.response.send_message(f"베팅 금액은 1~{MAX_BET:,} 범위입니다.")

    gid, uid = interaction.guild.id, interaction.user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")  # 간단한 동시성 제어
        u = await get_user(db, gid, uid)
        if u["balance"] < amount:
            await db.execute("ROLLBACK")
            return await interaction.response.send_message("잔액 부족입니다.")

        # 0~9999 중 추첨 → 0~4999면 승리(50.00%)
        roll = secrets.randbelow(10_000)
        win = roll < WIN_PROB_BPS

        detail = f"승률 {WIN_PROB_BPS/100:.2f}% · 배당 {PAYOUT_MULTIPLE:.2f}x · 하우스 엣지 {HOUSE_EDGE*100:.2f}%"

        if win:
            payout = int(round(amount * PAYOUT_MULTIPLE))   # 총지급액(원금 포함)
            profit = payout - amount                        # 순이익
            new_bal = u["balance"] - amount + payout
            await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
            await write_ledger(
                db, gid, uid, "bet_win", profit, new_bal,
                {"game": "coinflip", "bet": amount, "roll": roll, "win_prob_bps": WIN_PROB_BPS,
                 "payout_multiple": PAYOUT_MULTIPLE, "house_edge": HOUSE_EDGE}
            )
            await db.commit()
            return await interaction.response.send_message(
                f"승리! +{profit:,} → 잔액 {new_bal:,}\n`{detail}`"
            )
        else:
            new_bal = u["balance"] - amount
            await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
            await write_ledger(
                db, gid, uid, "bet_lose", -amount, new_bal,
                {"game": "coinflip", "bet": amount, "roll": roll, "win_prob_bps": WIN_PROB_BPS,
                 "payout_multiple": PAYOUT_MULTIPLE, "house_edge": HOUSE_EDGE}
            )
            await db.commit()
            return await interaction.response.send_message(
                f"패배… -{amount:,} → 잔액 {new_bal:,}\n`{detail}`"
            )

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_bet)
