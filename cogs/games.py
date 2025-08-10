import aiosqlite, secrets, json, time, asyncio
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

DB_PATH = "economy.db"

# ── 표시/시간 유틸 ───────────────────────────────────────
KST = timezone(timedelta(hours=9))
def now_kst() -> datetime: return datetime.now(KST)
def won(n: int) -> str: return f"{n:,}₩"

# ── DB 유틸 ─────────────────────────────────────────────
async def get_settings(db, gid: int):
    cur = await db.execute(
        "SELECT min_bet, win_min_bps, win_max_bps, mode_name FROM guild_settings WHERE guild_id=?",
        (gid,)
    )
    row = await cur.fetchone()
    if row:
        return {"min_bet": row[0], "win_min_bps": row[1], "win_max_bps": row[2], "mode_name": row[3]}
    await db.execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)", (gid,))
    await db.commit()
    return {"min_bet": 1000, "win_min_bps": 3000, "win_max_bps": 6000, "mode_name": "일반 모드"}

async def get_user(db, gid: int, uid: int):
    cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid))
    row = await cur.fetchone()
    if row:
        return {"balance": row[0]}
    await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, uid, 0))
    await db.commit()
    return {"balance": 0}

async def write_ledger(db, gid, uid, kind, amount, bal_after, meta=None):
    await db.execute(
        "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) VALUES(?,?,?,?,?,?,?)",
        (gid, uid, kind, amount, bal_after, json.dumps(meta or {}), int(time.time()))
    )

def footer_text(balance: int | None, mode_name: str) -> str:
    t = now_kst().strftime("%H:%M")
    if balance is None:
        return f"현재 모드 : {mode_name} · 오늘 {t}"
    return f"현재 잔액 : {won(balance)} · 현재 모드 : {mode_name} · 오늘 {t}"

# ── 도박 로직 ────────────────────────────────────────────
async def resolve_bet(interaction: discord.Interaction, amount: int):
    gid, uid = interaction.guild.id, interaction.user.id

    # 1) 파라미터/잔액 확인
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        s = await get_settings(db, gid)
        min_bet = s["min_bet"]
        u = await get_user(db, gid, uid)
        bal = u["balance"]
        if amount < min_bet:
            await db.execute("ROLLBACK")
            em = discord.Embed(title="주문 거절 — 최소 베팅 미만", color=0xe74c3c)
            em.add_field(name="최소 베팅", value=won(min_bet))
            em.add_field(name="요청 베팅", value=won(amount))
            em.set_footer(text=footer_text(bal, s["mode_name"]))
            if interaction.response.is_done():
                return await interaction.followup.send(embed=em, ephemeral=True)
            return await interaction.response.send_message(embed=em)
        if bal <= 0 or amount > bal:
            await db.execute("ROLLBACK")
            em = discord.Embed(title="주문 거절 — 잔액 부족", color=0xe74c3c)
            em.add_field(name="현재 잔액", value=won(bal))
            em.add_field(name="요청 베팅", value=won(amount))
            em.set_footer(text=footer_text(bal, s["mode_name"]))
            if interaction.response.is_done():
                return await interaction.followup.send(embed=em, ephemeral=True)
            return await interaction.response.send_message(embed=em)

    # 2) 접수 (버튼 없음)
    em = discord.Embed(title="주문 접수 — 도박", color=0x95a5a6)
    try:
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        em.set_author(name=interaction.user.display_name)
    em.add_field(name="베팅", value=won(amount))
    em.add_field(name="\u200b", value="**3초 후 결과가 공개됩니다.**", inline=False)
    async with aiosqlite.connect(DB_PATH) as db:
        s = await get_settings(db, gid)
    em.set_footer(text=footer_text(None, s["mode_name"]))
    await interaction.response.send_message(embed=em)

    # 3) 결과
    await asyncio.sleep(3)
    async with aiosqlite.connect(DB_PATH) as db:
        s = await get_settings(db, gid)
        pr_bps = s["win_min_bps"] + secrets.randbelow(max(1, s["win_max_bps"] - s["win_min_bps"] + 1))
        win = secrets.randbelow(10_000) < pr_bps
        delta = (amount if win else -amount)
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        new_bal = u["balance"] + delta
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, ("bet_win" if win else "bet_lose"), delta, new_bal, {"p_bps": pr_bps, "amount": amount})
        await db.commit()

    color = 0x2ecc71 if delta >= 0 else 0xe74c3c
    em = discord.Embed(title="도박 결과", color=color)
    try:
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        em.set_author(name=interaction.user.display_name)
    em.add_field(name="결과", value=("승리" if delta >= 0 else "패배"), inline=True)
    em.add_field(name="손익", value=f"{'+' if delta>=0 else ''}{won(delta)}", inline=True)
    em.add_field(name="현재 잔액", value=won(new_bal), inline=False)
    em.set_footer(text=footer_text(new_bal, s["mode_name"]))
    await interaction.followup.send(embed=em)

# ── 명령 ─────────────────────────────────────────────────
@app_commands.command(name="mz_bet", description="승률 30~60% 랜덤, 결과는 ±베팅액 (최소 1,000₩)")
@app_commands.describe(amount="베팅 금액(정수)", all_in="전액 베팅 여부")
async def mz_bet(interaction: discord.Interaction, amount: int = 0, all_in: bool = False):
    gid, uid = interaction.guild.id, interaction.user.id
    if all_in:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            u = await get_user(db, gid, uid)
            amount = u["balance"]
            if amount <= 0:
                await db.execute("ROLLBACK")
                em = discord.Embed(title="주문 거절 — 잔액 부족", color=0xe74c3c)
                em.add_field(name="현재 잔액", value=won(u["balance"]))
                return await interaction.response.send_message(embed=em, ephemeral=True)
    await resolve_bet(interaction, int(amount))

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_bet)
