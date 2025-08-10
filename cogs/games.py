import aiosqlite, secrets, json, time, asyncio
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

DB_PATH = "economy.db"

# 애니메이션/지연
REVEAL_DELAY = 3
PROGRESS_TICKS = 6
SPINNER = ["◐","◓","◑","◒","◐","◓"]

# ── 표시/시간 유틸 ───────────────────────────────────────
KST = timezone(timedelta(hours=9))
def now_kst() -> datetime: return datetime.now(KST)
def won(n: int) -> str: return f"{n:,}₩"
def footer_text(balance: int | None, mode_name: str) -> str:
    t = now_kst().strftime("%H:%M")
    if balance is None:
        return f"현재 모드 : {mode_name} · 오늘 {t}"
    return f"현재 잔액 : {won(balance)} · 현재 모드 : {mode_name} · 오늘 {t}"

def progress_bar(p: float, width: int = 12) -> str:
    p = max(0.0, min(1.0, p))
    filled = int(round(p * width))
    return "▰" * filled + "▱" * (width - filled)

# ── 설정/DB 유틸 ─────────────────────────────────────────
async def get_settings(gid: int):
    async with aiosqlite.connect(DB_PATH) as db:
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

# ── 주문 접수 임베드 ─────────────────────────────────────
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

    # 잔액/금액 검증
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        bal = u["balance"]
        if all_in:
            amount = bal

        if bal <= 0 or amount <= 0 or amount > bal:
            await db.execute("ROLLBACK")
            em = discord.Embed(title="주문 거절 — 잔액/금액 오류", color=0xe74c3c)
            em.add_field(name="현재 잔액", value=won(bal), inline=True)
            em.add_field(name="요청 베팅", value=won(amount), inline=True)
            return await interaction.response.send_message(embed=em)

        if amount < min_bet:
            await db.execute("ROLLBACK")
            em = discord.Embed(title="주문 거절 — 최소 베팅 미만", color=0xf1c40f)
            em.add_field(name="최소 베팅", value=won(min_bet))
            em.add_field(name="요청 베팅", value=won(amount))
            return await interaction.response.send_message(embed=em)

    # 주문 접수(초기 메시지)
    await send_order_embed(interaction, amount, all_in, s["mode_name"])

    # 결과 미리 결정(승/패) — DB 반영은 나중에
    if p_hi < p_lo:
        p_lo, p_hi = p_hi, p_lo
    p = secrets.randbelow((p_hi - p_lo + 1)) + p_lo  # [p_lo, p_hi]
    win = secrets.randbelow(10_000) < p
    delta = amount if win else -amount

    # 스피너 애니메이션
    for i, ch in enumerate(SPINNER, start=1):
        prog = i / len(SPINNER)
        pbar = progress_bar(prog)
        em = discord.Embed(title=f"도박 진행 중 {ch}", color=0xF1C40F)
        try:
            em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        except Exception:
            em.set_author(name=interaction.user.display_name)
        em.add_field(name="베팅", value=won(amount), inline=True)
        em.add_field(name="진행", value=f"{pbar} **{int(prog*100)}%**", inline=False)
        em.set_footer(text=f"오늘 {now_kst().strftime('%H:%M')}")
        await interaction.edit_original_response(embed=em)
        await asyncio.sleep(REVEAL_DELAY / len(SPINNER))

    # 반영
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        new_bal = u["balance"] + delta
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, "bet_win" if win else "bet_lose", delta, new_bal, {"amount": amount, "win_prob_bps": p})
        await db.commit()

    # 결과 임베드(같은 메시지로 확정)
    color = 0x2ecc71 if win else 0xe74c3c
    em = discord.Embed(title="도박 결과", color=color)
    try:
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        em.set_author(name=interaction.user.display_name)
    em.add_field(name="결과", value=("승리" if win else "패배"), inline=True)
    em.add_field(name="손익", value=f"{'+' if delta>=0 else ''}{won(delta)}", inline=True)
    em.add_field(name="현재 잔액", value=won(new_bal), inline=False)
    em.set_footer(text=footer_text(new_bal, s["mode_name"]))
    try:
        await interaction.edit_original_response(embed=em)
    except discord.NotFound:
        await interaction.followup.send(embed=em)

# ── 코그 등록 ────────────────────────────────────────────
async def setup(bot: discord.Client):
    bot.tree.add_command(mz_bet)
