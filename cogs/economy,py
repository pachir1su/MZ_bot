import time, json, aiosqlite
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

DB_PATH = "economy.db"

MONEY_COOLDOWN = 600       # 10분
MONEY_AMOUNT   = 1_000
DAILY_AMOUNT   = 10_000

# ── 표시/시간 유틸 ───────────────────────────────────────
KST = timezone(timedelta(hours=9))
def won(n: int) -> str: return f"{n:,}₩"

async def get_mode_name(gid: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT mode_name FROM guild_settings WHERE guild_id=?", (gid,))
        row = await cur.fetchone()
    return (row[0] if row else "일반 모드")

def footer_text(mode_name: str) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    return f"현재 모드 : {mode_name} · 오늘 {now}"

# ✅ 다음 KST 자정까지 남은 초 (항상 '내일 00:00 KST')
def seconds_until_kst_midnight(now: datetime | None = None) -> int:
    now = (now.astimezone(KST) if now else datetime.now(KST))
    next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return int((next_midnight - now).total_seconds())

# DB 유틸
async def get_user(db, gid, uid):
    cur = await db.execute(
        "SELECT balance,last_claim_at,last_daily_at FROM users WHERE guild_id=? AND user_id=?",
        (gid, uid)
    )
    row = await cur.fetchone()
    if row:
        return {"balance": row[0], "last_claim_at": row[1], "last_daily_at": row[2]}
    await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, uid, 0))
    await db.commit()
    return {"balance": 0, "last_claim_at": None, "last_daily_at": None}

async def write_ledger(db, gid, uid, kind, amount, bal_after, meta=None):
    await db.execute(
        "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) VALUES(?,?,?,?,?,?,?)",
        (gid, uid, kind, amount, bal_after, json.dumps(meta or {}), int(time.time()))
    )

# /면진돈줘 — 트랜잭션으로 동시호출 안정화, 남은 시간 정확 표기, 고정 1,000 지급
@app_commands.command(name="mz_money", description="Claim periodic money (10 min CD, +1000)")
async def mz_money(interaction: discord.Interaction):
    gid, uid = interaction.guild.id, interaction.user.id
    mode_name = await get_mode_name(gid)
    now = int(time.time())

    async with aiosqlite.connect(DB_PATH) as db:
        # 동시호출 경쟁 방지
        await db.execute("BEGIN IMMEDIATE")

        # 사용자 행 직접 조회 (get_user는 commit을 수행하므로 여기선 직접 처리)
        cur = await db.execute(
            "SELECT balance, last_claim_at FROM users WHERE guild_id=? AND user_id=?",
            (gid, uid)
        )
        row = await cur.fetchone()
        if row is None:
            balance, last = 0, 0
            await db.execute("INSERT INTO users(guild_id,user_id,balance,last_claim_at) VALUES(?,?,?,?)",
                             (gid, uid, 0, 0))
        else:
            balance, last = row[0], (row[1] or 0)

        # 시스템 시간이 꼬여 last가 미래라면 보정
        if last > now:
            last = now

        elapsed = now - last
        if elapsed < MONEY_COOLDOWN:
            remain = MONEY_COOLDOWN - elapsed
            await db.execute("ROLLBACK")  # 변경 없음, 잠금 즉시 해제
            mins, secs = divmod(remain, 60)
            embed = discord.Embed(
                title="잠시 후 이용 가능",
                description=f"{mins}분 {secs}초 후 이용 가능",
                color=0xf1c40f
            )
            embed.set_footer(text=footer_text(mode_name))
            return await interaction.response.send_message(embed=embed)

        # 지급
        new_bal = balance + MONEY_AMOUNT
        await db.execute(
            "UPDATE users SET balance=?, last_claim_at=? WHERE guild_id=? AND user_id=?",
            (new_bal, now, gid, uid)
        )
        await write_ledger(db, gid, uid, "deposit", MONEY_AMOUNT, new_bal, {"reason": "money"})
        await db.commit()

    embed = discord.Embed(title="돈 지급 (10분에 한 번 가능)", color=0x2ecc71)
    embed.add_field(name="\u200b", value=f"**{won(MONEY_AMOUNT)}**을 드렸어요", inline=False)
    embed.add_field(name="잔액", value=won(new_bal), inline=False)
    embed.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=embed)

# /면진출첵 — **KST 자정 기준** 하루 1회 (기존 동일)
@app_commands.command(name="mz_attend", description="Daily attendance (+10000, resets 00:00 KST)")
async def mz_attend(interaction: discord.Interaction):
    gid, uid = interaction.guild.id, interaction.user.id
    mode_name = await get_mode_name(gid)
    now_ts = int(time.time())
    now_kst = datetime.now(KST)

    async with aiosqlite.connect(DB_PATH) as db:
        u = await get_user(db, gid, uid)
        last_ts = u["last_daily_at"]
        last_date = (datetime.fromtimestamp(last_ts, tz=KST).date() if last_ts else None)

        if last_date == now_kst.date():
            remain = seconds_until_kst_midnight(now_kst)
            hrs, rem = divmod(remain, 3600)
            mins, secs = divmod(rem, 60)
            reset_dt = now_kst.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            reset_str = reset_dt.strftime("%m월 %d일 00:00 (KST)")
            embed = discord.Embed(
                title="이미 오늘 출석했습니다",
                description=f"{int(hrs)}시간 {int(mins)}분 {int(secs)}초 후 다시 가능\n리셋: {reset_str}",
                color=0xf1c40f
            )
            embed.set_footer(text=footer_text(mode_name))
            return await interaction.response.send_message(embed=embed)

        new_bal = u["balance"] + DAILY_AMOUNT
        async with aiosqlite.connect(DB_PATH) as db2:
            await db2.execute(
                "UPDATE users SET balance=?, last_daily_at=? WHERE guild_id=? AND user_id=?",
                (new_bal, now_ts, gid, uid)
            )
            await write_ledger(db2, gid, uid, "deposit", DAILY_AMOUNT, new_bal, {"reason": "attend"})
            await db2.commit()

    embed = discord.Embed(title="돈 지급 (하루에 한 번 가능)", color=0x2ecc71)
    embed.add_field(name="\u200b", value=f"**{won(DAILY_AMOUNT)}**을 드렸어요", inline=False)
    embed.add_field(name="잔액", value=won(new_bal), inline=False)
    embed.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=embed)

# /면진순위 & /면진잔액 (변경 없음)
@app_commands.command(name="mz_rank", description="Show top balances in this server")
async def mz_rank(interaction: discord.Interaction):
    gid = interaction.guild.id
    mode_name = await get_mode_name(gid)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, balance FROM users WHERE guild_id=? ORDER BY balance DESC, user_id ASC LIMIT 10",
            (gid,)
        )
        rows = await cur.fetchall()

    embed = discord.Embed(title="서버 게임 잔액 순위", color=0x2ecc71 if rows else 0x95a5a6)
    if not rows:
        embed.description = "데이터가 없습니다."
    else:
        lines = []
        for i, (uid, bal) in enumerate(rows, start=1):
            m = interaction.guild.get_member(uid)
            if m:
                name = m.display_name
            else:
                try:
                    u = await interaction.client.fetch_user(uid)
                    name = u.global_name or u.name
                except Exception:
                    name = f"유저 {uid}"
            lines.append(f"{i}. {name}\n{won(bal)}")
        embed.description = "\n".join(lines)
        if interaction.guild.icon:
            try: embed.set_thumbnail(url=interaction.guild.icon.url)
            except Exception: pass

    embed.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=embed)

@app_commands.command(name="mz_balance_show", description="Show user's current balance")
@app_commands.describe(user="대상 사용자(선택)")
async def mz_balance_show(interaction: discord.Interaction, user: discord.Member | None = None):
    target = user or interaction.user
    gid = interaction.guild.id
    mode_name = await get_mode_name(gid)
    async with aiosqlite.connect(DB_PATH) as db:
        u = await get_user(db, gid, target.id)

    embed = discord.Embed(title="현재 잔액", color=0x3498db)
    embed.add_field(name=target.display_name, value=won(u["balance"]), inline=False)
    embed.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=embed)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_money)
    bot.tree.add_command(mz_attend)
    bot.tree.add_command(mz_rank)
    bot.tree.add_command(mz_balance_show)
