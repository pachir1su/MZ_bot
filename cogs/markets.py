# cogs/markets.py
import aiosqlite, secrets, json, time, asyncio
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

DB_PATH = "economy.db"

# ── 표시/시간 유틸 ───────────────────────────────────────
KST = timezone(timedelta(hours=9))
def now_kst() -> datetime: return datetime.now(KST)
def won(n: int) -> str: return f"{n:,}₩"
def footer_text(balance: int | None, mode_name: str) -> str:
    t = now_kst().strftime("%H:%M")
    if balance is None:
        return f"현재 모드 : {mode_name} · 오늘 {t}"
    return f"현재 잔액 : {won(balance)} · 현재 모드 : {mode_name} · 오늘 {t}"

# ── DB 유틸 ─────────────────────────────────────────────
async def get_mode_name(gid: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT mode_name FROM guild_settings WHERE guild_id=?", (gid,))
        row = await cur.fetchone()
        return (row[0] if row else "일반 모드")

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

# ── 상품 정의 ────────────────────────────────────────────
STOCKS = {
    "성현전자":  (-20.0, +20.0),
    "배달의 승기":(-30.0, +30.0),
    "대이식스":   (-10.0, +10.0),
    "재구식품":   ( -5.0,  +5.0),
}
COINS = {
    "건영코인":  (-100.0, +200.0),
    "면진코인":  (-200.0, +500.0),
    "승철코인":  (-300.0, +1000.0),
}

# ✅ symbol 드롭다운 선택지
STOCK_CHOICES = [app_commands.Choice(name=n, value=n) for n in STOCKS.keys()]
COIN_CHOICES  = [app_commands.Choice(name=n, value=n) for n in COINS.keys()]

def pick_rate(lo: float, hi: float) -> float:
    """한 자리 소수 고정. 극단값은 희귀하게만 나오도록 균등 추출."""
    if hi < lo: lo, hi = hi, lo
    raw = lo + (hi - lo) * (secrets.randbelow(10_000) / 10_000.0)  # 균등
    return round(raw, 1)  # 소수 1자리

# ── 공용: 주문 접수 임베드 ───────────────────────────────
async def send_order_embed(interaction: discord.Interaction, title: str, fields: list[tuple[str, str]]):
    em = discord.Embed(title=title, color=0x95a5a6)
    for name, value in fields:
        em.add_field(name=name, value=value)
    em.add_field(name="\u200b", value="**5초 후 결과가 공개됩니다.**", inline=False)
    try:
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        em.set_author(name=interaction.user.display_name)
    em.set_footer(text=f"현재 모드 : {await get_mode_name(interaction.guild.id)} · 오늘 {now_kst().strftime('%H:%M')}")
    await interaction.response.send_message(embed=em)

# ── 잔액 부족 안내(공개) ─────────────────────────────────
async def send_insufficient(interaction: discord.Interaction, balance: int, amount: int):
    em = discord.Embed(title="주문 거절 — 잔액 부족", color=0xe74c3c)
    em.add_field(name="현재 잔액", value=won(balance), inline=True)
    em.add_field(name="요청 베팅", value=won(amount), inline=True)
    em.add_field(name="\u200b", value="잔액이 0 이하이거나 잔액보다 큰 금액으로는 베팅할 수 없습니다.", inline=False)
    em.set_footer(text=f"현재 모드 : {await get_mode_name(interaction.guild.id)} · 오늘 {now_kst().strftime('%H:%M')}")
    await interaction.response.send_message(embed=em)

# ── /면진주식 ────────────────────────────────────────────
@app_commands.command(name="mz_stock", description="가상 주식 투자(5초 후 결과 공개, 퍼센트 손익)")
@app_commands.describe(symbol="종목(성현전자/배달의 승기/대이식스/재구식품)", amount="베팅 금액(정수)")
@app_commands.choices(symbol=STOCK_CHOICES)   # ✅ 드롭다운
async def mz_stock(interaction: discord.Interaction, symbol: str, amount: int):
    gid, uid = interaction.guild.id, interaction.user.id
    symbol = symbol.strip()
    if symbol not in STOCKS:
        return await interaction.response.send_message("알 수 없는 종목입니다.", ephemeral=False)
    if amount <= 0:
        return await interaction.response.send_message("베팅 금액은 1 이상이어야 합니다.", ephemeral=False)

    # 동시성 안전: 트랜잭션에서 잔액 확인
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        bal = u["balance"]
        if bal <= 0 or amount > bal:
            await db.execute("ROLLBACK")
            return await send_insufficient(interaction, bal, amount)

    # 주문 접수 공지(공개)
    await send_order_embed(
        interaction,
        "주문 접수 — 주식",
        [("종목", symbol), ("베팅", won(amount))]
    )

    # 결과 계산
    await asyncio.sleep(5)
    lo, hi = STOCKS[symbol]
    rate = pick_rate(lo, hi)                   # % (소수1자리)
    delta = int(round(amount * rate / 100.0))  # 손익
    kind = "stock_win" if delta >= 0 else "stock_lose"

    # 반영
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        new_bal = u["balance"] + delta         # 손익만 반영(원금 차감 없음)
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, kind, delta, new_bal, {"symbol": symbol, "amount": amount, "rate_pct": rate})
        await db.commit()

    # 결과 임베드
    color = 0x2ecc71 if delta >= 0 else 0xe74c3c
    em = discord.Embed(title=f"주식 결과 — {symbol}", color=color)
    try:
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        em.set_author(name=interaction.user.display_name)
    em.add_field(name="변화율", value=f"{rate:+.1f}%", inline=True)
    em.add_field(name="손익",   value=f"{'+' if delta>=0 else ''}{won(delta)}", inline=True)
    em.add_field(name="현재 잔액", value=won(new_bal), inline=False)
    em.set_footer(text=footer_text(new_bal, await get_mode_name(gid)))
    await interaction.followup.send(embed=em)

# ── /면진코인 ────────────────────────────────────────────
@app_commands.command(name="mz_coin", description="가상 코인 러시(잭팟 계단형, 5초 후 공개)")
@app_commands.describe(symbol="코인(건영코인/면진코인/승철코인)", amount="베팅 금액(정수)")
@app_commands.choices(symbol=COIN_CHOICES)    # ✅ 드롭다운
async def mz_coin(interaction: discord.Interaction, symbol: str, amount: int):
    gid, uid = interaction.guild.id, interaction.user.id
    symbol = symbol.strip()
    if symbol not in COINS:
        return await interaction.response.send_message("알 수 없는 코인입니다.", ephemeral=False)
    if amount <= 0:
        return await interaction.response.send_message("베팅 금액은 1 이상이어야 합니다.", ephemeral=False)

    # 동시성 안전: 잔액 확인
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        bal = u["balance"]
        if bal <= 0 or amount > bal:
            await db.execute("ROLLBACK")
            return await send_insufficient(interaction, bal, amount)

    # 주문 접수 공지
    await send_order_embed(
        interaction,
        "주문 접수 — 코인",
        [("코인", symbol), ("베팅", won(amount))]
    )

    # 결과
    await asyncio.sleep(5)
    lo, hi = COINS[symbol]
    rate = pick_rate(lo, hi)                   # % (소수1자리)
    delta = int(round(amount * rate / 100.0))
    kind = "coin_win" if delta >= 0 else "coin_lose"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        new_bal = u["balance"] + delta         # 손익만 반영
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, kind, delta, new_bal, {"coin": symbol, "amount": amount, "rate_pct": rate})
        await db.commit()

    color = 0x2ecc71 if delta >= 0 else 0xe74c3c
    em = discord.Embed(title=f"코인 결과 — {symbol}", color=color)
    try:
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        em.set_author(name=interaction.user.display_name)
    em.add_field(name="결과 수익률", value=f"{rate:+.1f}%", inline=True)
    em.add_field(name="손익",       value=f"{'+' if delta>=0 else ''}{won(delta)}", inline=True)
    em.add_field(name="현재 잔액",   value=won(new_bal), inline=False)
    em.set_footer(text=footer_text(new_bal, await get_mode_name(gid)))
    await interaction.followup.send(embed=em)

# ── /면진파산 ────────────────────────────────────────────
@app_commands.command(name="mz_bankruptcy", description="잔액이 음수일 때 10분마다 부채 복구 시도")
async def mz_bankruptcy(interaction: discord.Interaction):
    """부채 복구: 0% / 50% / 100% (희귀하게 극단값). 하루 한도 없음, 10분 쿨다운은 다른 로직에서 통제."""
    gid, uid = interaction.guild.id, interaction.user.id
    mode = await get_mode_name(gid)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user(db, gid, uid)
        bal = u["balance"]
        if bal >= 0:
            await db.execute("ROLLBACK")
            em = discord.Embed(title="면진파산 조건 불충족", description="잔액이 음수일 때만 신청할 수 있습니다.", color=0xf1c40f)
            em.add_field(name="현재 잔액", value=won(bal))
            em.set_footer(text=footer_text(bal, mode))
            return await interaction.response.send_message(embed=em)

        # 확률: 100%/0%는 희귀
        roll = secrets.randbelow(1000)  # 0..999
        if roll < 30:
            recover_ratio = 1.0
        elif roll < 80:
            recover_ratio = 0.0
        else:
            recover_ratio = 0.5

        recovered = int(round(abs(bal) * recover_ratio))
        new_bal = bal + recovered

        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, "bankruptcy", recovered, new_bal, {"roll": roll, "ratio": recover_ratio})
        await db.commit()

    title = "면진파산 결과"
    color = 0x2ecc71 if recovered > 0 else 0xe74c3c
    result = ("승인(부채 100% 복구)" if recover_ratio == 1.0
              else "승인(부채 50% 복구)" if recover_ratio == 0.5
              else "거절")
    em = discord.Embed(title=title, color=color)
    try:
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        em.set_author(name=interaction.user.display_name)
    em.add_field(name="결과", value=result, inline=True)
    em.add_field(name="변화", value=f"{'+' if recovered>0 else ''}{won(recovered)}", inline=True)
    em.add_field(name="현재 잔액", value=won(new_bal), inline=False)
    em.set_footer(text=footer_text(new_bal, mode))
    await interaction.response.send_message(embed=em)

# ── 코그 등록 ────────────────────────────────────────────
async def setup(bot: discord.Client):
    bot.tree.add_command(mz_stock)
    bot.tree.add_command(mz_coin)
    bot.tree.add_command(mz_bankruptcy)
