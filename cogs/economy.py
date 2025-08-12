# cogs/economy.py
# Slash commands:
#   /면진돈줘 (mz_money)      : 10분 쿨타임, +1,000₩
#   /면진출첵 (mz_attend)     : KST 자정 기준 하루 1회, +10,000₩  [트랜잭션 일원화]
#   /면진순위 (mz_rank)       : 서버 상위 10명 잔액
#   /면진잔액 (mz_balance_show): 대상/본인 잔액 조회
#   /면진송금 (mz_transfer)    : 멤버 간 송금 [신규]

import time
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiosqlite
import discord
from discord import app_commands

DB_PATH = "economy.db"

# ==== 금액/쿨타임 설정 ====
MONEY_COOLDOWN = 600        # 10분
MONEY_AMOUNT   = 1_000
DAILY_AMOUNT   = 10_000

# 송금 정책 (필요시 조정)
MIN_TRANSFER = 1_000
MAX_TRANSFER = 10_000_000
FEE_BPS      = 0            # 100 => 1% 수수료, 현재 0%

# ==== 시간/표시 유틸 ====
KST = timezone(timedelta(hours=9))

def won(n: int) -> str:
    return f"{n:,}₩"

async def get_mode_name(gid: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT mode_name FROM guild_settings WHERE guild_id=?", (gid,))
        row = await cur.fetchone()
    return (row[0] if row else "일반 모드")

def footer_text(mode_name: str) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    return f"현재 모드 : {mode_name} · 오늘 {now}"

def seconds_until_kst_midnight(now: Optional[datetime] = None) -> int:
    now = (now.astimezone(KST) if now else datetime.now(KST))
    next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return int((next_midnight - now).total_seconds())

# ==== DB 유틸 ====
async def get_user(db: aiosqlite.Connection, gid: int, uid: int):
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

async def write_ledger(
    db: aiosqlite.Connection,
    gid: int, uid: int, kind: str,
    amount: int, bal_after: int, meta: Optional[dict] = None
):
    await db.execute(
        "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) VALUES(?,?,?,?,?,?,?)",
        (gid, uid, kind, amount, bal_after, json.dumps(meta or {}), int(time.time()))
    )

# ==== /면진돈줘 ====
@app_commands.command(name="mz_money", description="Claim periodic money (10 min CD, +1000)")
async def mz_money(interaction: discord.Interaction):
    gid, uid = interaction.guild.id, interaction.user.id
    mode_name = await get_mode_name(gid)
    now = int(time.time())

    async with aiosqlite.connect(DB_PATH) as db:
        # 경쟁 방지
        await db.execute("BEGIN IMMEDIATE")

        # 직접 조회(트랜잭션 내)
        cur = await db.execute(
            "SELECT balance, last_claim_at FROM users WHERE guild_id=? AND user_id=?",
            (gid, uid)
        )
        row = await cur.fetchone()
        if row is None:
            balance, last = 0, 0
            await db.execute(
                "INSERT INTO users(guild_id,user_id,balance,last_claim_at) VALUES(?,?,?,?)",
                (gid, uid, 0, 0)
            )
        else:
            balance, last = row[0], (row[1] or 0)

        if last > now:
            last = now

        elapsed = now - last
        if elapsed < MONEY_COOLDOWN:
            remain = MONEY_COOLDOWN - elapsed
            await db.execute("ROLLBACK")
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

# ==== /면진출첵  [트랜잭션 일원화] ====
@app_commands.command(name="mz_attend", description="Daily attendance (+10000, resets 00:00 KST)")
async def mz_attend(interaction: discord.Interaction):
    gid, uid = interaction.guild.id, interaction.user.id
    mode_name = await get_mode_name(gid)
    now_ts = int(time.time())
    now_kst = datetime.now(KST)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")

        # 직접 조회(트랜잭션 내)
        cur = await db.execute(
            "SELECT balance,last_daily_at FROM users WHERE guild_id=? AND user_id=?",
            (gid, uid)
        )
        row = await cur.fetchone()
        if row is None:
            balance, last_daily = 0, None
            await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, uid, 0))
        else:
            balance, last_daily = row[0], row[1]

        last_date = (datetime.fromtimestamp(last_daily, tz=KST).date() if last_daily else None)
        if last_date == now_kst.date():
            await db.execute("ROLLBACK")
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

        new_bal = balance + DAILY_AMOUNT
        await db.execute(
            "UPDATE users SET balance=?, last_daily_at=? WHERE guild_id=? AND user_id=?",
            (new_bal, now_ts, gid, uid)
        )
        await write_ledger(db, gid, uid, "deposit", DAILY_AMOUNT, new_bal, {"reason": "attend"})
        await db.commit()

    embed = discord.Embed(title="돈 지급 (하루에 한 번 가능)", color=0x2ecc71)
    embed.add_field(name="\u200b", value=f"**{won(DAILY_AMOUNT)}**을 드렸어요", inline=False)
    embed.add_field(name="잔액", value=won(new_bal), inline=False)
    embed.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=embed)

# ==== /면진순위 ====
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

    embed = discord.Embed(title="서버 순위", color=0x2ecc71 if rows else 0x95a5a6)
    if not rows:
        embed.description = "데이터가 없습니다."
    else:
        lines = []
        for i, (uid, bal) in enumerate(rows, start=1):
            # 강화 레벨 조회
            async with aiosqlite.connect(DB_PATH) as _db_lv:
                cur_lv = await _db_lv.execute("SELECT level FROM user_weapons WHERE guild_id=? AND user_id=?", (gid, uid))
                r_lv = await cur_lv.fetchone()
                lv = (r_lv[0] if r_lv else 0)
            m = interaction.guild.get_member(uid)
            if m:
                name = m.display_name
            else:
                try:
                    u = await interaction.client.fetch_user(uid)
                    name = u.global_name or u.name
                except Exception:
                    name = f"유저 {uid}"
            lines.append(f"{i}. {name}  **+{lv}**\n{won(bal)}")
        embed.description = "\n".join(lines)
        if interaction.guild.icon:
            try:
                embed.set_thumbnail(url=interaction.guild.icon.url)
            except Exception:
                pass

    embed.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=embed)

# ==== /면진잔액 ====
@app_commands.command(name="mz_balance_show", description="Show user's current balance")
@app_commands.describe(user="대상 사용자(선택)")
async def mz_balance_show(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    target = user or interaction.user
    gid = interaction.guild.id
    mode_name = await get_mode_name(gid)
    async with aiosqlite.connect(DB_PATH) as db:
        u = await get_user(db, gid, target.id)

    embed = discord.Embed(title="현재 잔액", color=0x3498db)
    embed.add_field(name=target.display_name, value=won(u["balance"]), inline=False)
    embed.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=embed)

# ==== /면진송금  [신규] ====
@app_commands.command(
    name="mz_transfer",
    description="Send coins to a member (min 1,000₩)"
)
@app_commands.describe(member="받는 사람", amount="송금 금액(정수, 최소 1,000₩)")
async def mz_transfer(interaction: discord.Interaction, member: discord.Member, amount: int):
    gid, sender_id = interaction.guild.id, interaction.user.id
    receiver_id = member.id

    # 1) 입력 검증
    if receiver_id == sender_id:
        return await interaction.response.send_message("자기 자신에게는 송금할 수 없습니다.", ephemeral=True)
    if member.bot:
        return await interaction.response.send_message("봇 계정으로는 송금할 수 없습니다.", ephemeral=True)
    if amount < MIN_TRANSFER:
        return await interaction.response.send_message(f"최소 송금 금액은 {won(MIN_TRANSFER)} 입니다.", ephemeral=True)
    if amount > MAX_TRANSFER:
        return await interaction.response.send_message(f"한 번에 보낼 수 있는 최대 금액은 {won(MAX_TRANSFER)} 입니다.", ephemeral=True)

    fee = (amount * FEE_BPS) // 10_000
    net = amount - fee
    if net <= 0:
        return await interaction.response.send_message("수수료 설정이 과도합니다. 관리자 설정을 확인해 주세요.", ephemeral=True)

    mode_name = await get_mode_name(gid)

    # 2) 트랜잭션
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")

        # 보낸 사람
        cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, sender_id))
        row = await cur.fetchone()
        if row is None:
            await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, sender_id, 0))
            sender_bal = 0
        else:
            sender_bal = row[0]

        if sender_bal < amount:
            await db.execute("ROLLBACK")
            return await interaction.response.send_message("잔액이 부족합니다.", ephemeral=True)

        # 받는 사람
        cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, receiver_id))
        row = await cur.fetchone()
        if row is None:
            await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, receiver_id, 0))
            receiver_bal = 0
        else:
            receiver_bal = row[0]

        new_sender_bal   = sender_bal - amount
        new_receiver_bal = receiver_bal + net

        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_sender_bal, gid, sender_id))
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_receiver_bal, gid, receiver_id))

        await write_ledger(db, gid, sender_id,  "transfer_out", -amount, new_sender_bal,   {"to": receiver_id, "fee": fee})
        await write_ledger(db, gid, receiver_id, "transfer_in",   net,     new_receiver_bal, {"from": sender_id, "fee": fee})

        await db.commit()

    # 3) 피드백 (보낸 사람)
    em = discord.Embed(title="송금 완료", color=0x2ecc71)
    em.add_field(name="받는 사람", value=f"{member.display_name}", inline=True)
    em.add_field(name="보낸 금액", value=won(amount), inline=True)
    if fee:
        em.add_field(name="수수료", value=won(fee), inline=True)
        em.add_field(name="실수령액", value=won(net), inline=True)
    em.add_field(name="내 잔액", value=won(new_sender_bal), inline=False)
    em.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=em, ephemeral=True)

    # 4) 받는 사람 DM 알림 (가능할 때만)
    try:
        dm = discord.Embed(title="코인 도착", color=0x3498db)
        dm.add_field(name="보낸 사람", value=interaction.user.display_name, inline=True)
        dm.add_field(name="받은 금액", value=won(net), inline=True)
        if fee:
            dm.add_field(name="수수료(보낸 측)", value=won(fee), inline=True)
        dm.set_footer(text=f"서버: {interaction.guild.name}")
        await member.send(embed=dm)
    except Exception:
        pass  # DM 차단 등은 무시

# ==== setup ====
async def setup(bot: discord.Client):
    bot.tree.add_command(mz_money)
    bot.tree.add_command(mz_attend)
    bot.tree.add_command(mz_rank)
    bot.tree.add_command(mz_balance_show)
    bot.tree.add_command(mz_transfer)
