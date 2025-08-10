import aiosqlite, asyncio, secrets, time, random
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

DB_PATH = "economy.db"

# === 연출/정책 ===
REVEAL_DELAY = 2.5             # 강화 연출 총 길이(초)
PROGRESS_TICKS = 5             # 애니메이션 프레임 수
BASE_COST = 5_000              # +0 → +1 기본 비용
COST_GROWTH = 1.65             # 단계당 비용 증가 배수
MAX_LEVEL = 10                 # 최대 +10

# 현재 레벨 기준 성공률(%)
SUCCESS_RATE = {
    0: 90, 1: 85, 2: 80, 3: 70, 4: 60,
    5: 45, 6: 35, 7: 25, 8: 15, 9: 10,
    10: 0,  # 이미 최대치
}

# === 공용 유틸 ===
KST = timezone(timedelta(hours=9))
def now_kst() -> datetime: return datetime.now(KST)
def won(n: int) -> str: return f"{n:,}₩"

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
        (gid, uid, kind, amount, bal_after, (meta and str(meta)) or "{}", int(time.time()))
    )

async def get_weapon(db, gid: int, uid: int) -> int:
    cur = await db.execute("SELECT level FROM user_weapons WHERE guild_id=? AND user_id=?", (gid, uid))
    row = await cur.fetchone()
    if row:
        return row[0]
    await db.execute(
        "INSERT OR IGNORE INTO user_weapons(guild_id,user_id,level,updated_at) VALUES(?,?,0,?)",
        (gid, uid, int(time.time()))
    )
    await db.commit()
    return 0

def level_cost(level: int) -> int:
    """현재 레벨에서 다음 단계로 강화 시 드는 비용."""
    raw = BASE_COST * (COST_GROWTH ** level)
    return int(round(raw / 100)) * 100  # 100원 단위 보기 좋게 반올림

def progress_bar(p: float, width: int = 12) -> str:
    p = max(0.0, min(1.0, p))
    filled = int(round(p * width))
    return "▰" * filled + "▱" * (width - filled)

# === /면진강화 ===
@app_commands.command(name="mz_enhance", description="무기 강화(+0 → +10). 비용 지불 후 확률로 성공")
async def mz_enhance(interaction: discord.Interaction):
    gid, uid = interaction.guild.id, interaction.user.id

    # 1) 현재 레벨/비용/성공률 계산
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        level = await get_weapon(db, gid, uid)
        u = await get_user(db, gid, uid)
        bal = u["balance"]

        if level >= MAX_LEVEL:
            await db.execute("ROLLBACK")
            em = discord.Embed(title="강화 불가", description="이미 +10입니다.", color=0x95a5a6)
            em.add_field(name="현재 등급", value=f"+{level}")
            return await interaction.response.send_message(embed=em, ephemeral=True)

        cost = level_cost(level)
        rate = SUCCESS_RATE.get(level, 0)

        if bal < cost:
            await db.execute("ROLLBACK")
            em = discord.Embed(title="잔액 부족", color=0xe74c3c)
            em.add_field(name="필요 비용", value=won(cost), inline=True)
            em.add_field(name="현재 잔액", value=won(bal), inline=True)
            return await interaction.response.send_message(embed=em, ephemeral=True)

        # 2) 비용 즉시 차감(소각)
        new_bal = bal - cost
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, "enhance_fee", -cost, new_bal, {"from": level, "to": level+1, "rate": rate})
        await db.commit()

    # 3) 진행 임베드(애니메이션)
    em = discord.Embed(title="강화 진행 중…", color=0xF1C40F)
    try:
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        em.set_author(name=interaction.user.display_name)
    em.add_field(name="현재 등급", value=f"+{level}", inline=True)
    em.add_field(name="다음 등급", value=f"+{level+1}", inline=True)
    em.add_field(name="성공률", value=f"{rate}%", inline=True)
    em.add_field(name="강화 비용", value=won(level_cost(level)), inline=True)
    em.add_field(name="진행", value=f"{progress_bar(0.0)}  **0%**", inline=False)
    await interaction.response.send_message(embed=em)

    for i in range(1, PROGRESS_TICKS + 1):
        p = i / PROGRESS_TICKS
        em = discord.Embed(title="강화 진행 중…", color=0xF1C40F)
        try:
            em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        except Exception:
            em.set_author(name=interaction.user.display_name)
        em.add_field(name="현재 등급", value=f"+{level}", inline=True)
        em.add_field(name="다음 등급", value=f"+{level+1}", inline=True)
        em.add_field(name="성공률", value=f"{rate}%", inline=True)
        em.add_field(name="강화 비용", value=won(level_cost(level)), inline=True)
        em.add_field(name="진행", value=f"{progress_bar(p)}  **{int(p*100)}%**", inline=False)
        await interaction.edit_original_response(embed=em)
        await asyncio.sleep(REVEAL_DELAY / PROGRESS_TICKS)

    # 4) 결과 판정
    roll = secrets.randbelow(100) + 1  # 1..100
    success = (roll <= rate)

    # 5) DB 반영(+레벨 업 또는 유지)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT level FROM user_weapons WHERE guild_id=? AND user_id=?", (gid, uid))
        row = await cur.fetchone()
        cur_level = row[0] if row else 0
        new_level = min(MAX_LEVEL, cur_level + 1) if success else cur_level

        await db.execute(
            "UPDATE user_weapons SET level=?, updated_at=? WHERE guild_id=? AND user_id=?",
            (new_level, int(time.time()), gid, uid)
        )
        cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid))
        bal_row = await cur.fetchone()
        bal_after = bal_row[0] if bal_row else 0

        await write_ledger(db, gid, uid, "enhance_result", 0, bal_after,
                           {"from": level, "to": new_level, "success": success, "roll": roll, "rate": rate})
        await db.commit()

    # 6) 결과 임베드
    color = 0x2ecc71 if success else 0xe74c3c
    title = "강화 성공!" if success else "강화 실패"
    em = discord.Embed(title=title, color=color)
    try:
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    except Exception:
        em.set_author(name=interaction.user.display_name)
    em.add_field(name="이전 등급", value=f"+{level}", inline=True)
    em.add_field(name="현재 등급", value=f"+{new_level}", inline=True)
    em.add_field(name="잔액", value=won(bal_after), inline=False)
    await interaction.edit_original_response(embed=em)

# === /면진도움말 ===
@app_commands.command(name="mz_help", description="면진이 명령어 도움말")
async def mz_help(interaction: discord.Interaction):
    em = discord.Embed(title="면진이 — 도움말", color=0x3498db, description="주요 명령 요약")
    em.add_field(name="/면진돈줘", value="10분마다 1,000 코인 지급", inline=False)
    em.add_field(name="/면진출첵", value="자정마다 초기화되는 출석 보상", inline=False)
    em.add_field(name="/면진도박", value="승률 30~60% 랜덤, ±베팅액", inline=False)
    em.add_field(name="/면진주식 · /면진코인", value="가상 투자(3초 후 결과 공개)", inline=False)
    em.add_field(name="/면진강화", value="무기 강화(+0→+10), 비용 소각형", inline=False)
    em.add_field(name="/면진순위", value="잔액/무기 순위 — 분화 예정", inline=False)
    em.add_field(name="/면진맞짱", value="강화 무기로 PvP(차기 단계)", inline=False)
    await interaction.response.send_message(embed=em, ephemeral=True)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_enhance)
    bot.tree.add_command(mz_help)
