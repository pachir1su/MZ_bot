# cogs/games.py
import aiosqlite, secrets, json, time
import discord
from discord import app_commands
from discord.ext import commands
from settings import HOUSE_EDGE, MAX_BET

DB_PATH = "economy.db"

async def get_user(db, gid, uid):
    cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid))
    row = await cur.fetchone()
    if row: return {"balance":row[0]}
    await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, uid, 0))
    await db.commit()
    return {"balance":0}

async def write_ledger(db, gid, uid, kind, amount, bal_after, meta=None):
    await db.execute(
        "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) VALUES(?,?,?,?,?,?,?)",
        (gid, uid, kind, amount, bal_after, json.dumps(meta or {}), int(time.time()))
    )

class Games(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="mz_coinflip", description="코인플립 베팅")
    @app_commands.describe(side="앞 또는 뒤", amount="베팅 금액")
    async def mz_coinflip(self, interaction: discord.Interaction, side: str, amount: int):
        side = side.strip()
        if side not in ("앞","뒤"): return await interaction.response.send_message("앞/뒤 중 선택")
        if amount <= 0 or amount > MAX_BET:
            return await interaction.response.send_message(f"베팅 금액은 1~{MAX_BET:,} 범위")
        gid, uid = interaction.guild.id, interaction.user.id
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            u = await get_user(db, gid, uid)
            if u["balance"] < amount:
                await db.execute("ROLLBACK"); return await interaction.response.send_message("잔액 부족")
            roll = secrets.randbelow(2)   # 0 or 1
            win = (roll == 0 and side=="앞") or (roll == 1 and side=="뒤")
            if win:
                payout = int(amount * (2 - HOUSE_EDGE))
                new_bal = u["balance"] - amount + payout
                await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
                await write_ledger(db, gid, uid, "bet_win", payout-amount, new_bal,
                                   {"game":"coinflip","bet":amount,"roll":roll,"side":side})
                await db.commit()
                return await interaction.response.send_message(f"승리! +{payout-amount:,} → 잔액 {new_bal:,}")
            else:
                new_bal = u["balance"] - amount
                await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
                await write_ledger(db, gid, uid, "bet_lose", -amount, new_bal,
                                   {"game":"coinflip","bet":amount,"roll":roll,"side":side})
                await db.commit()
                return await interaction.response.send_message(f"패배… -{amount:,} → 잔액 {new_bal:,}")

async def setup(bot): await bot.add_cog(Games(bot))
