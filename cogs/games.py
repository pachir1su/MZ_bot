import aiosqlite, secrets, json, time, asyncio, random
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

DB_PATH = "economy.db"

REVEAL_DELAY = 3
PROGRESS_TICKS = 6
SPINNER = ["◐","◓","◑","◒","◐","◓"]

KST = timezone(timedelta(hours=9))
def now_kst() -> datetime: return datetime.now(KST)
def won(n: int) -> str: return f"{n:,}₩"

def progress_bar(p: float, width: int = 12) -> str:
    p = max(0.0, min(1.0, p))
    filled = int(round(p * width))
    return "▰" * filled + "▱" * (width - filled)

async def get_settings(gid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT min_bet, win_min_bps, win_max_bps, mode_name, COALESCE(force_mode,'off'), COALESCE(force_target_user_id,0) "
            "FROM guild_settings WHERE guild_id=?",
            (gid,)
        )
        row = await cur.fetchone()
        if row:
            return {"min_bet": row[0], "win_min_bps": row[1], "win_max_bps": row[2], "mode_name": row[3], "force_mode": row[4], "force_uid": int(row[5] or 0)}
        await db.execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)", (gid,))
        await db.commit()
        return {"min_bet": 1000, "win_min_bps": 3000, "win_max_bps": 6000, "mode_name": "일반 모드", "force_mode":"off", "force_uid":0}

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

def footer_text(bal: int, mode_name: str) -> str:
    return f"현재 잔액 {won(bal)} · 모드 {mode_name} · {now_kst().strftime('%H:%M')}"

async def send_order_embed(interaction: discord.Interaction, amount: int, all_in: bool, mode_name: str):
    em = discord.Embed(title="주문 접수 — 도박", color=0x95a5a6)
    try:
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        em.set_author(name=interaction.user.display_name)
    em.add_field(name="베팅", value=won(amount), inline=True)
    em.add_field(name="옵션", value=("전액" if all_in else "—"), inline=True)
    em.add_field(name="\u200b", value=f"결과 계산 중… {REVEAL_DELAY}초", inline=False)
    em.set_footer(text=f"현재 모드 : {mode_name} · 오늘 {now_kst().strftime('%H:%M')}")
    await interaction.response.send_message(embed=em)

# ── /면진도박 ────────────────────────────────────────────
@app_commands.command(name="mz_bet", description="승률 30~60%(서버 설정) 랜덤, 결과는 ±베팅액 (최소 1,000₩)")
@app_commands.describe(amount="베팅 금액(정수, 최소 베팅 이상)", all_in="전액 베팅 여부")
async def mz_bet(interaction: discord.Interaction, amount: int, all_in: bool = False):
    gid, uid = interaction.guild.id, interaction.user.id
    s = await get_settings(gid)
    min_bet = int(s["min_bet"])
    p_lo = int(s["win_min_bps"])
    p_hi = int(s["win_max_bps"])
    force_mode, force_uid = s["force_mode"], s["force_uid"]

    # 잔액/금액 검증
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        bal = u["balance"]
        if amount <= 0:
            await db.execute("ROLLBACK")
            return await interaction.response.send_message("베팅 금액은 1원 이상이어야 합니다. (전액은 all_in을 사용)", ephemeral=False)
        if amount < min_bet:
            await db.execute("ROLLBACK")
            return await interaction.response.send_message(f"최소 베팅은 {won(min_bet)} 입니다.", ephemeral=False)
        if amount > bal:
            await db.execute("ROLLBACK")
            return await interaction.response.send_message(f"잔액 부족: {won(bal)}", ephemeral=False)
        # 선차감
        new_bal = bal - amount
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, "bet_place", -amount, new_bal, {"amount": amount})
        await db.commit()

    # 애니메이션
    await send_order_embed(interaction, amount, all_in, s["mode_name"])
    for i, ch in enumerate(SPINNER):
        prog = (i + 1) / len(SPINNER)
        pbar = progress_bar(prog)
        em = discord.Embed(title="도박 진행 중…", color=0xF1C40F)
        try:
            em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        except Exception:
            em.set_author(name=interaction.user.display_name)
        em.add_field(name="베팅", value=won(amount), inline=True)
        em.add_field(name="진행", value=f"{pbar} **{int(prog*100)}%**", inline=False)
        em.set_footer(text=f"오늘 {now_kst().strftime('%H:%M')}")
        await interaction.edit_original_response(embed=em)
        await asyncio.sleep(REVEAL_DELAY / len(SPINNER))

    # 결과
    if force_mode in ("success","fail") and (force_uid == 0 or force_uid == uid):
        win = (force_mode == "success")
        p = random.randint(p_lo, p_hi)
    else:
        p = secrets.randbelow(p_hi - p_lo + 1) + p_lo
        win = (secrets.randbelow(10000) < p * 100)

    delta = amount if win else -amount

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        new_bal = u["balance"] + delta
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, "bet_win" if win else "bet_lose", delta, new_bal, {"amount": amount, "win_prob_bps": p})
        await db.commit()

    color = 0x2ecc71 if win else 0xe74c3c
    em = discord.Embed(title="도박 결과", color=color)
    try: em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception: em.set_author(name=interaction.user.display_name)
    em.add_field(name="결과", value=("승리" if win else "패배"), inline=True)
    em.add_field(name="손익", value=f"{'+' if delta>=0 else ''}{won(delta)}", inline=True)
    em.add_field(name="현재 잔액", value=won(new_bal), inline=False)
    em.set_footer(text=footer_text(new_bal, s["mode_name"]))
    try:
        await interaction.edit_original_response(embed=em)
    except discord.NotFound:
        await interaction.followup.send(embed=em)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_bet)
