# cogs/markets.py
import aiosqlite, secrets, json, time, asyncio, random
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

DB_PATH = "economy.db"

REVEAL_DELAY = 3
PROGRESS_TICKS = 6

MIN_STOCK_BET = 5_000
MIN_COIN_BET  = 20_000

KST = timezone(timedelta(hours=9))
def now_kst() -> datetime: return datetime.now(KST)
def won(n: int) -> str: return f"{n:,}₩"

STOCKS = {
    "성현전자": (-20.0, 20.0),
    "배달의 승기": (-30.0, 30.0),
    "대이식스": (-10.0, 10.0),
    "재구식품": (-5.0, 5.0),
}
COINS = {
    "건영코인": (-60.0, 120.0),
    "면진코인": (-120.0, 240.0),
    "승철코인": (-200.0, 400.0),
}
STOCK_CHOICES = [app_commands.Choice(name=k, value=k) for k in STOCKS.keys()]
COIN_CHOICES  = [app_commands.Choice(name=k, value=k) for k in COINS.keys()]

async def get_mode_and_force(gid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COALESCE(mode_name,'일반 모드'), COALESCE(force_mode,'off'), COALESCE(force_target_user_id,0) FROM guild_settings WHERE guild_id=?", (gid,))
        row = await cur.fetchone()
        if row: return row[0], row[1], int(row[2] or 0)
        await db.execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)", (gid,))
        await db.commit()
        return "일반 모드", "off", 0

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

def ease_out_cubic(t: float) -> float:
    return max(0.0, min(1.0, 1 - (1 - t) ** 3))

def make_previews(lo: float, hi: float, final_value: float, ticks: int) -> list[float]:
    seq = []
    start = random.uniform(lo, hi)
    for i in range(1, ticks + 1):
        t = ease_out_cubic(i / ticks)
        val = start * (1 - t) + final_value * t
        jitter = (hi - lo) * 0.02 * (1 - t)
        if jitter:
            val += random.uniform(-jitter, jitter)
        seq.append(round(val, 1))
    seq[-1] = round(final_value, 1)
    return seq

async def animate_preview_embed(interaction: discord.Interaction, title: str,
                                header_fields: list[tuple[str, str]],
                                preview_label: str, previews: list[float]):
    for i, v in enumerate(previews, start=1):
        p = i / len(previews)
        em = discord.Embed(title=title, color=0xF1C40F)
        try:
            em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        except Exception:
            em.set_author(name=interaction.user.display_name)
        for name, value in header_fields:
            em.add_field(name=name, value=value, inline=True)
        em.add_field(name=preview_label, value=f"{v:+.1f}%", inline=True)
        em.set_footer(text=f"오늘 {now_kst().strftime('%H:%M')} · {int(p*100)}%")
        try:
            await interaction.edit_original_response(embed=em)
        except discord.NotFound:
            await interaction.followup.send(embed=em)
        await asyncio.sleep(REVEAL_DELAY / PROGRESS_TICKS)

async def get_mode_name(gid: int) -> str:
    name, _, _ = await get_mode_and_force(gid)
    return name

async def send_min_bet_violation(interaction: discord.Interaction, kind: str, min_bet: int, amount: int):
    em = discord.Embed(title=f"주문 거절 — 최소 {kind} 베팅 미만", color=0xf1c40f)
    em.add_field(name="최소 베팅", value=won(min_bet), inline=True)
    em.add_field(name="요청 베팅", value=won(amount), inline=True)
    em.set_footer(text=f"현재 모드 : {await get_mode_name(interaction.guild.id)} · 오늘 {now_kst().strftime('%H:%M')}")
    await interaction.response.send_message(embed=em)

def forced_final_change(lo: float, hi: float, force_to_win: bool) -> float:
    if force_to_win:
        if hi <= 0: return 0.1
        return random.uniform(max(0.1, hi*0.6), hi)
    else:
        if lo >= 0: return -0.1
        return random.uniform(lo, min(-0.1, lo*0.6))

# ── /면진주식 ────────────────────────────────────────────
@app_commands.command(name="mz_stock", description="가상 주식 투자(0=전액, 애니메이션 공개)")
@app_commands.describe(symbol="종목", amount=f"베팅 금액(정수, 0=전액, 최소 {MIN_STOCK_BET:,}₩)")
@app_commands.choices(symbol=STOCK_CHOICES)
async def mz_stock(interaction: discord.Interaction, symbol: str, amount: int):
    gid, uid = interaction.guild.id, interaction.user.id
    if symbol not in STOCKS:
        return await interaction.response.send_message("알 수 없는 종목입니다.", ephemeral=False)
    if amount < 0:
        return await interaction.response.send_message("베팅 금액은 음수가 될 수 없습니다.", ephemeral=False)

    lo, hi = STOCKS[symbol]
    min_bet = MIN_STOCK_BET

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        bal = u["balance"]
        all_in = False
        if amount == 0:
            amount = bal
            all_in = True
        if amount < min_bet:
            await db.execute("ROLLBACK")
            return await send_min_bet_violation(interaction, "주식", min_bet, amount)
        if amount > bal:
            await db.execute("ROLLBACK")
            return await interaction.response.send_message(f"잔액 부족: {won(bal)}", ephemeral=False)
        new_bal = bal - amount
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, "stock_place", -amount, new_bal, {"symbol": symbol, "amount": amount, "all_in": all_in})
        await db.commit()

    mode_name, force_mode, force_uid = await get_mode_and_force(gid)
    if force_mode in ("success","fail") and (force_uid == 0 or force_uid == uid):
        final = forced_final_change(lo, hi, force_to_win=(force_mode=="success"))
    else:
        final = round(random.uniform(lo, hi), 1)

    previews = make_previews(lo, hi, final, PROGRESS_TICKS)
    header = [("종목", symbol), ("베팅", won(amount))]
    await interaction.response.send_message(embed=discord.Embed(title="주문 접수 — 주식", color=0x95a5a6))
    await animate_preview_embed(interaction, "주식 체결 중…", header, "예상 등락", previews)

    delta = int(round(amount * (final / 100.0)))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        new_bal = u["balance"] + delta
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, "stock_settle", delta, new_bal, {"symbol": symbol, "pct": final, "amount": amount})
        await db.commit()

    title = "주식 결과"
    color = 0x2ecc71 if delta >= 0 else 0xe74c3c
    em = discord.Embed(title=title, color=color)
    em.add_field(name="종목", value=symbol, inline=True)
    em.add_field(name="등락", value=f"{final:+.1f}%", inline=True)
    em.add_field(name="손익", value=f"{'+' if delta>=0 else ''}{won(delta)}", inline=True)
    em.add_field(name="현재 잔액", value=won(new_bal), inline=False)
    em.set_footer(text=footer_text(new_bal, mode_name))
    try:
        await interaction.edit_original_response(embed=em)
    except discord.NotFound:
        await interaction.followup.send(embed=em)

# ── /면진코인 ────────────────────────────────────────────
@app_commands.command(name="mz_coin", description="가상 코인 러시(0=전액, 애니메이션 공개)")
@app_commands.describe(symbol="코인", amount=f"베팅 금액(정수, 0=전액, 최소 {MIN_COIN_BET:,}₩)")
@app_commands.choices(symbol=COIN_CHOICES)
async def mz_coin(interaction: discord.Interaction, symbol: str, amount: int):
    gid, uid = interaction.guild.id, interaction.user.id
    if symbol not in COINS:
        return await interaction.response.send_message("알 수 없는 코인입니다.", ephemeral=False)
    if amount < 0:
        return await interaction.response.send_message("베팅 금액은 음수가 될 수 없습니다.", ephemeral=False)

    lo, hi = COINS[symbol]
    min_bet = MIN_COIN_BET

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        bal = u["balance"]
        all_in = False
        if amount == 0:
            amount = bal
            all_in = True
        if amount < min_bet:
            await db.execute("ROLLBACK")
            return await send_min_bet_violation(interaction, "코인", min_bet, amount)
        if amount > bal:
            await db.execute("ROLLBACK")
            return await interaction.response.send_message(f"잔액 부족: {won(bal)}", ephemeral=False)
        new_bal = bal - amount
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, "coin_place", -amount, new_bal, {"symbol": symbol, "amount": amount, "all_in": all_in})
        await db.commit()

    mode_name, force_mode, force_uid = await get_mode_and_force(gid)
    if force_mode in ("success","fail") and (force_uid == 0 or force_uid == uid):
        final = forced_final_change(lo, hi, force_to_win=(force_mode=="success"))
    else:
        final = round(random.uniform(lo, hi), 1)

    previews = make_previews(lo, hi, final, PROGRESS_TICKS)
    header = [("코인", symbol), ("베팅", won(amount))]
    await interaction.response.send_message(embed=discord.Embed(title="주문 접수 — 코인", color=0x95a5a6))
    await animate_preview_embed(interaction, "코인 체결 중…", header, "예상 등락", previews)

    delta = int(round(amount * (final / 100.0)))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        new_bal = u["balance"] + delta
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, "coin_settle", delta, new_bal, {"symbol": symbol, "pct": final, "amount": amount})
        await db.commit()

    title = "코인 결과"
    color = 0x2ecc71 if delta >= 0 else 0xe74c3c
    em = discord.Embed(title=title, color=color)
    em.add_field(name="코인", value=symbol, inline=True)
    em.add_field(name="등락", value=f"{final:+.1f}%", inline=True)
    em.add_field(name="손익", value=f"{'+' if delta>=0 else ''}{won(delta)}", inline=True)
    em.add_field(name="현재 잔액", value=won(new_bal), inline=False)
    em.set_footer(text=footer_text(new_bal, mode_name))
    try:
        await interaction.edit_original_response(embed=em)
    except discord.NotFound:
        await interaction.followup.send(embed=em)

# ── /면진파산 ─ 기존 코드 유지 (강제결과 비적용)
@app_commands.command(name="mz_bankruptcy", description="잔액이 음수일 때 10분마다 부채 복구 시도")
async def mz_bankruptcy(interaction: discord.Interaction):
    gid, uid = interaction.guild.id, interaction.user.id
    mode_name, _, _ = await get_mode_and_force(gid)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid))
        r = await cur.fetchone()
        bal = r[0] if r else 0
        if bal >= 0:
            await db.execute("ROLLBACK")
            em = discord.Embed(title="면진파산 조건 불충족", description="잔액이 음수일 때만 신청할 수 있습니다.", color=0xf1c40f)
            em.add_field(name="현재 잔액", value=won(bal))
            em.set_footer(text=footer_text(bal, mode_name))
            return await interaction.response.send_message(embed=em)

        roll = secrets.randbelow(1000)
        if roll < 30:       recover_ratio = 1.0
        elif roll < 80:     recover_ratio = 0.0
        else:               recover_ratio = 0.5

        recovered = int(round(abs(bal) * recover_ratio))
        new_bal = bal + recovered
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, "bankruptcy", recovered, new_bal, {"ratio": recover_ratio})
        await db.commit()

    result = "전액 복구" if recover_ratio == 1.0 else ("부분 복구" if recover_ratio == 0.5 else "복구 실패")
    color = 0x2ecc71 if recovered > 0 else 0xf1c40f
    em = discord.Embed(title="면진파산 결과", color=color)
    em.add_field(name="결과", value=result, inline=True)
    em.add_field(name="변화", value=f"{'+' if recovered>0 else ''}{won(recovered)}", inline=True)
    em.add_field(name="현재 잔액", value=won(new_bal), inline=False)
    em.set_footer(text=footer_text(new_bal, mode_name))
    await interaction.response.send_message(embed=em)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_stock)
    bot.tree.add_command(mz_coin)
    bot.tree.add_command(mz_bankruptcy)
