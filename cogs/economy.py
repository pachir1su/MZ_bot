import os
import time
import json
import aiosqlite
from pathlib import Path
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

DB_PATH = str(Path(__file__).resolve().parent.parent / "economy.db")

# 지급/쿨타임 기본값
MONEY_COOLDOWN = 600       # 10분
MONEY_AMOUNT   = 1_000
DAILY_AMOUNT   = 10_000

# 운영 로그 채널
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0") or "0")

# ── 표시/시간 유틸 ───────────────────────────────────────
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

# ✅ 다음 KST 자정까지 남은 초
def seconds_until_kst_midnight(now: datetime | None = None) -> int:
    now = (now.astimezone(KST) if now else datetime.now(KST))
    next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return int((next_midnight - now).total_seconds())

# 운영 로그 헬퍼
async def try_send_log(client: discord.Client, embed: discord.Embed):
    if not LOG_CHANNEL_ID:
        return
    try:
        ch = client.get_channel(LOG_CHANNEL_ID) or await client.fetch_channel(LOG_CHANNEL_ID)
        await ch.send(embed=embed)
    except Exception:
        pass

# ── DB 유틸 ───────────────────────────────────────────────
async def get_user(db: aiosqlite.Connection, gid: int, uid: int):
    cur = await db.execute(
        "SELECT balance,last_claim_at,last_daily_at FROM users WHERE guild_id=? AND user_id=?",
        (gid, uid),
    )
    row = await cur.fetchone()
    if row:
        return {"balance": row[0], "last_claim_at": row[1], "last_daily_at": row[2]}
    await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, uid, 0))
    await db.commit()
    return {"balance": 0, "last_claim_at": None, "last_daily_at": None}

async def write_ledger(
    db: aiosqlite.Connection, gid: int, uid: int, kind: str, amount: int, bal_after: int, meta: dict | None = None
):
    await db.execute(
        "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) VALUES(?,?,?,?,?,?,?)",
        (gid, uid, kind, amount, bal_after, json.dumps(meta or {}), int(time.time())),
    )

# 서버 설정에서 최소 베팅(= 최소 송금액) 읽기
async def get_min_transfer(gid: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT min_bet FROM guild_settings WHERE guild_id=?", (gid,))
        row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 1_000

# ── /면진돈줘 ─────────────────────────────────────────────
@app_commands.command(name="mz_money", description="Claim periodic money (10 min CD, +1000)")
async def mz_money(interaction: discord.Interaction):
    gid, uid = interaction.guild.id, interaction.user.id
    mode_name = await get_mode_name(gid)
    now = int(time.time())

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")

        cur = await db.execute(
            "SELECT balance, last_claim_at FROM users WHERE guild_id=? AND user_id=?", (gid, uid)
        )
        row = await cur.fetchone()
        if row is None:
            balance, last = 0, 0
            await db.execute(
                "INSERT INTO users(guild_id,user_id,balance,last_claim_at) VALUES(?,?,?,?)", (gid, uid, 0, 0)
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
                title="잠시 후 이용 가능", description=f"{mins}분 {secs}초 후 이용 가능", color=0xF1C40F
            )
            embed.set_footer(text=footer_text(mode_name))
            return await interaction.response.send_message(embed=embed)

        new_bal = balance + MONEY_AMOUNT
        await db.execute(
            "UPDATE users SET balance=?, last_claim_at=? WHERE guild_id=? AND user_id=?",
            (new_bal, now, gid, uid),
        )
        await write_ledger(db, gid, uid, "deposit", MONEY_AMOUNT, new_bal, {"reason": "money"})
        await db.commit()

    embed = discord.Embed(title="돈 지급 (10분에 한 번 가능)", color=0x2ECC71)
    embed.add_field(name="\u200b", value=f"**{won(MONEY_AMOUNT)}**을 드렸어요", inline=False)
    embed.add_field(name="잔액", value=won(new_bal), inline=False)
    embed.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=embed)

# 한글 표기(Localization)
mz_money.name_localizations = {"ko": "면진돈줘"}
mz_money.description_localizations = {"ko": "10분마다 1,000 코인 지급"}

# ── /면진출첵 ─────────────────────────────────────────────
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
                color=0xF1C40F,
            )
            embed.set_footer(text=footer_text(mode_name))
            return await interaction.response.send_message(embed=embed)

        new_bal = u["balance"] + DAILY_AMOUNT
        await db.execute(
            "UPDATE users SET balance=?, last_daily_at=? WHERE guild_id=? AND user_id=?",
            (new_bal, now_ts, gid, uid),
        )
        await write_ledger(db, gid, uid, "deposit", DAILY_AMOUNT, new_bal, {"reason": "attend"})
        await db.commit()

    embed = discord.Embed(title="돈 지급 (하루에 한 번 가능)", color=0x2ECC71)
    embed.add_field(name="\u200b", value=f"**{won(DAILY_AMOUNT)}**을 드렸어요", inline=False)
    embed.add_field(name="잔액", value=won(new_bal), inline=False)
    embed.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=embed)

mz_attend.name_localizations = {"ko": "면진출첵"}
mz_attend.description_localizations = {"ko": "자정(00:00 KST)마다 초기화되는 출석 보상"}

# ── /면진순위 ─────────────────────────────────────────────
@app_commands.command(name="mz_rank", description="Show top balances in this server")
async def mz_rank(interaction: discord.Interaction):
    gid = interaction.guild.id
    mode_name = await get_mode_name(gid)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, balance FROM users WHERE guild_id=? "
            "ORDER BY balance DESC, user_id ASC LIMIT 10",
            (gid,),
        )
        rows = await cur.fetchall()

    embed = discord.Embed(title="서버 게임 잔액 순위", color=0x2ECC71 if rows else 0x95A5A6)
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
            try:
                embed.set_thumbnail(url=interaction.guild.icon.url)
            except Exception:
                pass

    embed.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=embed)

mz_rank.name_localizations = {"ko": "면진순위"}
mz_rank.description_localizations = {"ko": "서버 잔액 순위 TOP 10(닉네임만 표시)"}

# ── /면진잔액 ─────────────────────────────────────────────
@app_commands.command(name="mz_balance_show", description="Show user's current balance")
@app_commands.describe(user="대상 사용자(선택)")
async def mz_balance_show(interaction: discord.Interaction, user: discord.Member | None = None):
    target = user or interaction.user
    gid = interaction.guild.id
    mode_name = await get_mode_name(gid)
    async with aiosqlite.connect(DB_PATH) as db:
        u = await get_user(db, gid, target.id)

    embed = discord.Embed(title="현재 잔액", color=0x3498DB)
    embed.add_field(name=target.display_name, value=won(u["balance"]), inline=False)
    embed.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=embed)

mz_balance_show.name_localizations = {"ko": "면진잔액"}
mz_balance_show.description_localizations = {"ko": "현재 잔액 확인(대상 선택 가능)"}

# ── /면진송금 ─────────────────────────────────────────────
@app_commands.command(name="mz_transfer", description="Transfer coins to another member in this server")
@app_commands.describe(member="받는 사람", amount="송금 금액(정수)")
async def mz_transfer(interaction: discord.Interaction, member: discord.Member, amount: int):
    sender = interaction.user
    receiver = member
    gid = interaction.guild.id
    mode_name = await get_mode_name(gid)

    if receiver.id == sender.id:
        return await interaction.response.send_message("자기 자신에게는 송금할 수 없습니다.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("송금 금액은 1 이상이어야 합니다.", ephemeral=True)

    min_amt = await get_min_transfer(gid)
    if amount < min_amt:
        return await interaction.response.send_message(
            f"최소 송금 금액은 **{won(min_amt)}** 입니다.", ephemeral=True
        )

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")

        cur = await db.execute(
            "SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, sender.id)
        )
        row = await cur.fetchone()
        if row is None:
            await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, sender.id, 0))
            sender_bal = 0
        else:
            sender_bal = int(row[0])

        if sender_bal < amount:
            await db.execute("ROLLBACK")
            return await interaction.response.send_message("잔액이 부족합니다.", ephemeral=True)

        cur = await db.execute(
            "SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, receiver.id)
        )
        row = await cur.fetchone()
        if row is None:
            await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, receiver.id, 0))
            receiver_bal = 0
        else:
            receiver_bal = int(row[0])

        new_sender_bal = sender_bal - amount
        new_receiver_bal = receiver_bal + amount

        await db.execute(
            "UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_sender_bal, gid, sender.id)
        )
        await db.execute(
            "UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_receiver_bal, gid, receiver.id)
        )

        await write_ledger(
            db, gid, sender.id, "transfer_out", -amount, new_sender_bal, {"to": receiver.id}
        )
        await write_ledger(
            db, gid, receiver.id, "transfer_in", amount, new_receiver_bal, {"from": sender.id}
        )

        await db.commit()

    embed = discord.Embed(title="송금 완료", color=0x3498DB)
    embed.add_field(name="보낸 사람", value=sender.mention, inline=True)
    embed.add_field(name="받는 사람", value=receiver.mention, inline=True)
    embed.add_field(name="금액", value=won(amount), inline=False)
    embed.add_field(name="보낸 사람 잔액", value=won(new_sender_bal), inline=True)
    embed.add_field(name="받는 사람 잔액", value=won(new_receiver_bal), inline=True)
    embed.set_footer(text=footer_text(mode_name))
    await interaction.response.send_message(embed=embed)

    # 운영 로그
    log = discord.Embed(
        title="송금",
        color=0x2980B9,
        timestamp=datetime.now(timezone.utc),
        description=f"{sender.mention} → {receiver.mention} : **{won(amount)}**",
    )
    log.add_field(name="보낸 잔액", value=won(new_sender_bal))
    log.add_field(name="받은 잔액", value=won(new_receiver_bal))
    log.set_footer(text=f"Guild {interaction.guild.id}")
    await try_send_log(interaction.client, log)

mz_transfer.name_localizations = {"ko": "면진송금"}
mz_transfer.description_localizations = {"ko": "서버 멤버에게 코인을 송금합니다"}

# ── setup ─────────────────────────────────────────────────
async def setup(bot: discord.Client):
    bot.tree.add_command(mz_money)
    bot.tree.add_command(mz_attend)
    bot.tree.add_command(mz_rank)
    bot.tree.add_command(mz_balance_show)
    bot.tree.add_command(mz_transfer)
