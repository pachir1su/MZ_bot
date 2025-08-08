import aiosqlite, secrets, json, time
import discord
from discord import app_commands

DB_PATH = "economy.db"
HOUSE_EDGE = 0.02
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

# /면진도박 (기본명 mz_bet) — 코인플립
@app_commands.command(
    name="mz_bet",
    description="Bet on coin flip (front/back, amount)"
)
@app_commands.describe(side="front or back", amount="bet amount (integer)")
async def mz_bet(interaction: discord.Interaction, side: str, amount: int):
    side = side.strip()
    if side not in ("앞", "뒤", "front", "back"):
        return await interaction.response.send_message("side는 '앞' 또는 '뒤'만 가능합니다.")
    if side in ("front", "back"):
        side = "앞" if side == "front" else "뒤"

    if amount <= 0 or amount > MAX_BET:
        return await interaction.response.send_message(f"베팅 금액은 1~{MAX_BET:,} 범위입니다.")

    gid, uid = interaction.guild.id, interaction.user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")  # 간단한 동시성 제어
        u = await get_user(db, gid, uid)
        if u["balance"] < amount:
            await db.execute("ROLLBACK")
            return await interaction.response.send_message("잔액 부족입니다.")

        roll = secrets.randbelow(2)  # 0 or 1
        win = (roll == 0 and side == "앞") or (roll == 1 and side == "뒤")

        if win:
            payout = int(amount * (2 - HOUSE_EDGE))  # 1.98배(2% 하우스엣지)
            new_bal = u["balance"] - amount + payout
            await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
            await write_ledger(
                db, gid, uid, "bet_win", payout - amount, new_bal,
                {"game": "coinflip", "bet": amount, "roll": roll, "side": side}
            )
            await db.commit()
            return await interaction.response.send_message(f"승리! +{payout - amount:,} → 잔액 {new_bal:,}")
        else:
            new_bal = u["balance"] - amount
            await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
            await write_ledger(
                db, gid, uid, "bet_lose", -amount, new_bal,
                {"game": "coinflip", "bet": amount, "roll": roll, "side": side}
            )
            await db.commit()
            return await interaction.response.send_message(f"패배… -{amount:,} → 잔액 {new_bal:,}")

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_bet)
