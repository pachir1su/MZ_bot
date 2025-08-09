# cogs/markets.py
import asyncio, time, json, secrets, aiosqlite, math
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta
from typing import List, Tuple

DB_PATH = "economy.db"

# ── 시간/표시 유틸 ──────────────────────────────────────
KST = timezone(timedelta(hours=9))
def now_kst_str() -> str: return datetime.now(KST).strftime("%H:%M")
def won(n: int) -> str: return f"{n:,}₩"
def fmt_pct(p: float) -> str: return f"{p:+.1f}%"  # 소수 1자리 고정, 부호 포함

def footer_text(mode_name: str) -> str:
    return f"현재 모드 : {mode_name} · 오늘 {now_kst_str()}"

# ── 고정 파라미터(요청 사양) ────────────────────────────
FIVE_SEC_LOCK = 5
BANKRUPTCY_COOLDOWN = 600  # 10분

# 주식: 균등분포 범위(퍼센트) — 유저 비공개, 관리자 전용으로 수정 가능(초기값)
STOCK_RANGES = {
    "성현전자":   (-20.0, 20.0),
    "배달의 승기": (-30.0, 30.0),
    "대이식스":   (-10.0, 10.0),
    "재구식품":   ( -5.0,  5.0),
}

# 코인: 잭팟 계단형 (퍼센트, 가중치) — 기대값 음수 쪽으로 설계(요청 만족)
COIN_LADDER = {
    "건영코인": [(-80.0, 60), (40.0, 35), (300.0, 5)],                      # ≈ -7%
    "면진코인": [(-80.0, 70), (80.0, 25), (400.0, 4), (1000.0, 1)],        # ≈ -10%
    "승철코인": [(-100.0, 75), (150.0, 22), (600.0, 2.5), (2000.0, 0.5)],  # ≈ -17%
}

# 파산 가중치(%) — 0/50/100 복구(희귀 강화)
BANKRUPTCY_WEIGHTS = {0: 7, 50: 90, 100: 3}

# ── DB 유틸 ─────────────────────────────────────────────
async def get_min_bet_and_mode(gid: int) -> tuple[int, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT min_bet, mode_name FROM guild_settings WHERE guild_id=?",
            (gid,),
        )
        row = await cur.fetchone()
    if row:
        return (row[0] or 1000, row[1] or "일반 모드")
    return (1000, "일반 모드")

async def get_user_row(db: aiosqlite.Connection, gid: int, uid: int) -> dict:
    cur = await db.execute(
        "SELECT balance FROM users WHERE guild_id=? AND user_id=?",
        (gid, uid),
    )
    row = await cur.fetchone()
    if row:
        return {"balance": row[0]}
    await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, uid, 0))
    await db.commit()
    return {"balance": 0}

async def write_ledger(db: aiosqlite.Connection, gid: int, uid: int,
                       kind: str, amount: int, bal_after: int, meta: dict | None = None):
    await db.execute(
        "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) "
        "VALUES(?,?,?,?,?,?,?)",
        (gid, uid, kind, amount, bal_after, json.dumps(meta or {}), int(time.time())),
    )

# ── 락 유틸(5초 락/쿨다운) ──────────────────────────────
async def acquire_lock(gid: int, uid: int, key: str, ttl_sec: int) -> int:
    """락을 획득(성공 시 0, 실패 시 남은 초 반환). 원자적 보장을 위해 BEGIN IMMEDIATE."""
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT until FROM locks WHERE guild_id=? AND user_id=? AND key=?",
            (gid, uid, key),
        )
        row = await cur.fetchone()
        if row and row[0] and row[0] > now:
            remain = row[0] - now
            await db.execute("ROLLBACK")
            return remain
        until = now + ttl_sec
        # UPSERT
        await db.execute(
            "INSERT INTO locks(guild_id,user_id,key,until) VALUES(?,?,?,?) "
            "ON CONFLICT(guild_id,user_id,key) DO UPDATE SET until=excluded.until",
            (gid, uid, key, until),
        )
        await db.commit()
    return 0

async def cooldown_remaining(gid: int, uid: int, key: str) -> int:
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT until FROM locks WHERE guild_id=? AND user_id=? AND key=?",
            (gid, uid, key),
        )
        row = await cur.fetchone()
    if row and row[0] and row[0] > now:
        return row[0] - now
    return 0

async def set_cooldown(gid: int, uid: int, key: str, ttl_sec: int):
    now = int(time.time())
    until = now + ttl_sec
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO locks(guild_id,user_id,key,until) VALUES(?,?,?,?) "
            "ON CONFLICT(guild_id,user_id,key) DO UPDATE SET until=excluded.until",
            (gid, uid, key, until),
        )
        await db.commit()

# ── 난수 유틸 ───────────────────────────────────────────
def uniform_pct(low: float, high: float) -> float:
    # secrets 기반 균등 실수 생성(0<=x<1) → low..high
    r = secrets.randbelow(1_000_000) / 1_000_000.0
    return low + (high - low) * r

def weighted_pick(pairs: List[Tuple[float, float]]) -> float:
    """(value, weight) 리스트에서 weight 비율로 하나 선택."""
    total = sum(float(w) for _, w in pairs)
    # 0 <= roll < total
    roll = secrets.randbelow(int(total * 10_000)) / 10_000.0
    acc = 0.0
    for value, weight in pairs:
        acc += float(weight)
        if roll < acc:
            return float(value)
    return float(pairs[-1][0])

# ── 임베드 유틸 ─────────────────────────────────────────
def base_embed(title: str, color: int, user: discord.abc.User, mode_name: str) -> discord.Embed:
    em = discord.Embed(title=title, color=color)
    try:
        em.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    except Exception:
        pass
    em.set_footer(text=footer_text(mode_name))
    return em

# ── 슬래시 명령: 면진주식 ───────────────────────────────
STOCK_CHOICES = [
    app_commands.Choice(name="성현전자", value="성현전자"),
    app_commands.Choice(name="배달의 승기", value="배달의 승기"),
    app_commands.Choice(name="대이식스", value="대이식스"),
    app_commands.Choice(name="재구식품", value="재구식품"),
]

@app_commands.command(name="mz_stock", description="Virtual stock (5s reveal, percent P/L)")
@app_commands.describe(symbol="종목(성현전자/배달의 승기/대이식스/재구식품)", amount="정수 금액(최소 베팅 이상)")
@app_commands.choices(symbol=STOCK_CHOICES)
async def mz_stock(interaction: discord.Interaction, symbol: app_commands.Choice[str], amount: int):
    gid, uid = interaction.guild.id, interaction.user.id
    if amount <= 0:
        return await interaction.response.send_message("금액은 양의 정수여야 합니다.", ephemeral=False)

    min_bet, mode_name = await get_min_bet_and_mode(gid)
    if amount < min_bet:
        return await interaction.response.send_message(f"최소 베팅은 {won(min_bet)} 입니다.", ephemeral=False)

    # 5초 락 획득
    remain = await acquire_lock(gid, uid, "game", FIVE_SEC_LOCK)
    if remain > 0:
        return await interaction.response.send_message(f"진행 중인 주문이 있습니다. **{remain}초 후** 다시 시도하세요.", ephemeral=False)

    # 접수 알림
    await interaction.response.defer(thinking=True)  # 공개
    recv = base_embed("주문 접수 — 주식", 0x95a5a6, interaction.user, mode_name)
    recv.add_field(name="종목", value=symbol.name)
    recv.add_field(name="베팅", value=won(amount))
    recv.add_field(name="\u200b", value="**5초 후** 결과가 공개됩니다.", inline=False)
    await interaction.followup.send(embed=recv)

    await asyncio.sleep(5)

    low, high = STOCK_RANGES.get(symbol.value, (-10.0, 10.0))
    pct_raw = uniform_pct(low, high)
    pct_disp = float(f"{pct_raw:.1f}")  # 소수 1자리 고정
    delta = round(amount * (pct_disp / 100.0))

    # 정산 트랜잭션
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user_row(db, gid, uid)
        new_bal = u["balance"] + delta
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        kind = "stock_gain" if delta >= 0 else "stock_loss"
        await write_ledger(db, gid, uid, kind, delta, new_bal,
                           {"game": "stock", "symbol": symbol.value, "pct": pct_disp})
        await db.commit()

    color = 0x2ecc71 if delta > 0 else (0xe74c3c if delta < 0 else 0x95a5a6)
    em = base_embed(f"주식 결과 — {symbol.name}", color, interaction.user, mode_name)
    em.add_field(name="변화율", value=fmt_pct(pct_disp))
    em.add_field(name="손익", value=(f"{'+' if delta>=0 else ''}{won(delta)}"))
    em.add_field(name="현재 잔액", value=won(new_bal), inline=False)
    await interaction.followup.send(embed=em)

# ── 슬래시 명령: 면진코인 ───────────────────────────────
COIN_CHOICES = [
    app_commands.Choice(name="건영코인", value="건영코인"),
    app_commands.Choice(name="면진코인", value="면진코인"),
    app_commands.Choice(name="승철코인", value="승철코인"),
]

@app_commands.command(name="mz_coin", description="Virtual coin rush (jackpot ladder, 5s reveal)")
@app_commands.describe(symbol="코인(건영/면진/승철)", amount="정수 금액(최소 베팅 이상)")
@app_commands.choices(symbol=COIN_CHOICES)
async def mz_coin(interaction: discord.Interaction, symbol: app_commands.Choice[str], amount: int):
    gid, uid = interaction.guild.id, interaction.user.id
    if amount <= 0:
        return await interaction.response.send_message("금액은 양의 정수여야 합니다.", ephemeral=False)

    min_bet, mode_name = await get_min_bet_and_mode(gid)
    if amount < min_bet:
        return await interaction.response.send_message(f"최소 베팅은 {won(min_bet)} 입니다.", ephemeral=False)

    # 5초 락
    remain = await acquire_lock(gid, uid, "game", FIVE_SEC_LOCK)
    if remain > 0:
        return await interaction.response.send_message(f"진행 중인 주문이 있습니다. **{remain}초 후** 다시 시도하세요.", ephemeral=False)

    # 접수 알림
    await interaction.response.defer(thinking=True)  # 공개
    recv = base_embed("주문 접수 — 코인", 0x95a5a6, interaction.user, mode_name)
    recv.add_field(name="코인", value=symbol.name)
    recv.add_field(name="베팅", value=won(amount))
    recv.add_field(name="\u200b", value="**5초 후** 결과가 공개됩니다.", inline=False)
    await interaction.followup.send(embed=recv)

    await asyncio.sleep(5)

    ladder = COIN_LADDER.get(symbol.value)
    if not ladder:
        ladder = [(-80.0, 70), (80.0, 25), (400.0, 4), (1000.0, 1)]  # fallback
    pct_disp = float(f"{weighted_pick(ladder):.1f}")
    delta = round(amount * (pct_disp / 100.0))

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user_row(db, gid, uid)
        new_bal = u["balance"] + delta
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        kind = "coin_gain" if delta >= 0 else "coin_loss"
        await write_ledger(
            db, gid, uid, kind, delta, new_bal,
            {"game": "coin", "symbol": symbol.value, "outcome_pct": pct_disp, "ladder": True}
        )
        await db.commit()

    color = 0x8e44ad if pct_disp >= 400.0 else (0x2ecc71 if delta > 0 else 0xe74c3c)
    em = base_embed(f"코인 결과 — {symbol.name}", color, interaction.user, mode_name)
    em.add_field(name="결과 수익률", value=fmt_pct(pct_disp))
    em.add_field(name="손익", value=(f"{'+' if delta>=0 else ''}{won(delta)}"))
    em.add_field(name="현재 잔액", value=won(new_bal), inline=False)
    await interaction.followup.send(embed=em)

# ── 슬래시 명령: 파산신청 ───────────────────────────────
@app_commands.command(name="mz_bankruptcy", description="Bankruptcy relief: balance < 0, 10m cooldown, 5s reveal")
async def mz_bankruptcy(interaction: discord.Interaction):
    gid, uid = interaction.guild.id, interaction.user.id
    min_bet, mode_name = await get_min_bet_and_mode(gid)  # mode_name만 사용

    # 10분 쿨다운 확인
    cd = await cooldown_remaining(gid, uid, "bankruptcy_cd")
    if cd > 0:
        mins, secs = divmod(cd, 60)
        return await interaction.response.send_message(
            f"파산신청 쿨다운 중입니다. **{mins}분 {secs}초 후** 다시 시도하세요.", ephemeral=False
        )

    # 현재 잔액 확인
    async with aiosqlite.connect(DB_PATH) as db:
        u = await get_user_row(db, gid, uid)
    if u["balance"] >= 0:
        return await interaction.response.send_message("잔액이 음수가 아닙니다. 파산신청은 잔액이 음수일 때만 가능합니다.", ephemeral=False)

    # 5초 락(중복 접수 방지)
    remain = await acquire_lock(gid, uid, "bankruptcy", FIVE_SEC_LOCK)
    if remain > 0:
        return await interaction.response.send_message(f"진행 중인 신청이 있습니다. **{remain}초 후** 다시 시도하세요.", ephemeral=False)

    # 접수 알림
    await interaction.response.defer(thinking=True)  # 공개
    recv = base_embed("파산신청 접수", 0x95a5a6, interaction.user, mode_name)
    recv.add_field(name="현재 잔액", value=won(u["balance"]))
    recv.add_field(name="\u200b", value="**5초 후** 심사 결과가 공개됩니다.", inline=False)
    await interaction.followup.send(embed=recv)

    await asyncio.sleep(5)

    # 가중 선택: 0/50/100
    table = [(0, BANKRUPTCY_WEIGHTS.get(0, 7)),
             (50, BANKRUPTCY_WEIGHTS.get(50, 90)),
             (100, BANKRUPTCY_WEIGHTS.get(100, 3))]
    result_pct = int(weighted_pick(table))  # 0/50/100 중 하나

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        u = await get_user_row(db, gid, uid)
        old_bal = u["balance"]
        if old_bal >= 0:
            await db.execute("ROLLBACK")
            return await interaction.followup.send("현재 잔액이 음수가 아닙니다. 파산신청이 취소되었습니다.")
        if result_pct == 0:
            new_bal = old_bal
            delta = 0
            note = "거절(복구 0%)"
        elif result_pct == 50:
            # 절반 상환: 예) -11442 → -5721
            new_bal = math.ceil(old_bal / 2)
            delta = new_bal - old_bal  # 덜 음수 → 양수 변화
            note = "승인(부채 50% 복구)"
        else:  # 100
            new_bal = 0
            delta = -old_bal
            note = "승인(부채 100% 복구)"

        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, "bankruptcy_relief", delta, new_bal,
                           {"mode": "weighted", "result": result_pct, "weights": BANKRUPTCY_WEIGHTS})
        await db.commit()

    # 10분 쿨다운 설정
    await set_cooldown(gid, uid, "bankruptcy_cd", BANKRUPTCY_COOLDOWN)

    color = 0x2ecc71 if result_pct > 0 else 0xe67e22
    em = base_embed("파산신청 결과", color, interaction.user, mode_name)
    em.add_field(name="결과", value=note)
    em.add_field(name="변화", value=(f"{'+' if delta>=0 else ''}{won(delta)}"))
    em.add_field(name="현재 잔액", value=won(new_bal), inline=False)
    await interaction.followup.send(embed=em)

# ── 코그 로드 ───────────────────────────────────────────
async def setup(bot: discord.Client):
    bot.tree.add_command(mz_stock)
    bot.tree.add_command(mz_coin)
    bot.tree.add_command(mz_bankruptcy)
