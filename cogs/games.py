
import aiosqlite, secrets, time, asyncio, random
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
    return "█" * filled + "▁" * (width - filled)

def footer_text(balance: int, mode_name: str) -> str:
    t = now_kst().strftime("%H:%M")
    return f"현재 잔액 {balance:,}₩ · 모드 {mode_name} · {t}"

async def get_settings(db, gid: int):
    cur = await db.execute("SELECT min_bet, win_min_bps, win_max_bps, mode_name, force_mode, force_target_user_id FROM guild_settings WHERE guild_id=?", (gid,))
    row = await cur.fetchone()
    if not row:
        # defaults
        return {"min_bet": 1000, "win_min_bps": 3000, "win_max_bps": 6000, "mode_name": "일반 모드", "force_mode":"off", "force_uid":0}
    return {
        "min_bet": row[0], "win_min_bps": row[1], "win_max_bps": row[2],
        "mode_name": row[3], "force_mode": row[4], "force_uid": row[5],
    }

async def get_user(db, gid: int, uid: int):
    cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid))
    r = await cur.fetchone()
    if not r:
        await db.execute("INSERT INTO users(guild_id,user_id,balance,last_claim_at,last_daily_at) VALUES(?,?,?,?,?)", (gid, uid, 0, None, None))
        return {"balance": 0}
    return {"balance": r[0]}

async def write_ledger(db, gid:int, uid:int, kind:str, amount:int, bal_after:int, meta=None):
    await db.execute(
        "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) VALUES(?,?,?,?,?,?,?)",
        (gid, uid, kind, amount, bal_after, (meta and str(meta)) or "{}", int(time.time()))
    )

class _DisabledView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        # no interactive items

@app_commands.command(name="mz_bet", description="면진도박 — 승률 30~60% 랜덤, 결과는 ±베팅액 (최소 1,000₩)")
@app_commands.describe(amount="베팅 금액(정수). 최소 베팅 이상이어야 합니다.")
async def mz_bet(interaction: discord.Interaction, amount: int):
    gid, uid = interaction.guild.id, interaction.user.id

    # 초기 임베드(스피너/프로그레스, 버튼 없는 뷰로 잠그기)
    em = discord.Embed(title="도박 준비 중...", color=0x95a5a6)
    em.add_field(name="진행", value=progress_bar(0.0, 16), inline=False)
    await interaction.response.send_message(embed=em, view=_DisabledView())

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        s = await get_settings(db, gid)
        min_bet = s["min_bet"]
        if amount < min_bet:
            await db.execute("ROLLBACK")
            return await interaction.edit_original_response(embed=discord.Embed(description=f"최소 베팅은 {won(min_bet)} 입니다.", color=0xe67e22), view=None)

        u = await get_user(db, gid, uid)
        bal = u["balance"]
        if amount > bal:
            await db.execute("ROLLBACK")
            return await interaction.edit_original_response(embed=discord.Embed(description="잔액이 부족합니다.", color=0xe67e22), view=None)

        # 애니메이션 표시
        for i in range(PROGRESS_TICKS):
            p = (i+1) / PROGRESS_TICKS
            em = discord.Embed(title="도박 준비 중...", color=0x95a5a6)
            em.add_field(name="현재", value=won(bal), inline=True)
            em.add_field(name="베팅", value=won(amount), inline=True)
            em.add_field(name="진행", value=progress_bar(p, 16), inline=False)
            try:
                await interaction.edit_original_response(embed=em, view=_DisabledView())
            except discord.NotFound:
                pass
            await asyncio.sleep(REVEAL_DELAY / PROGRESS_TICKS)

        # 강제 결과 적용
        forced = s.get("force_mode","off")
        if forced == "success":   win = True
        elif forced == "fail":    win = False
        else:
            # 확률 범위 내에서 랜덤
            lo = max(0, min(10000, int(s["win_min_bps"])))
            hi = max(lo, min(10000, int(s["win_max_bps"])))
            p_win = random.randint(lo, hi) / 10000.0
            win = (random.random() < p_win)

        delta = amount if win else -amount
        new_bal = bal + delta

        # 정산
        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await write_ledger(db, gid, uid, ("bet_win" if win else "bet_lose"), delta, new_bal, {"bet": amount, "p_forced": forced})
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
        await interaction.edit_original_response(embed=em, view=None)
    except discord.NotFound:
        await interaction.followup.send(embed=em)
