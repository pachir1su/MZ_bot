# cogs/enhance.py
import aiosqlite, asyncio, secrets, time, random
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta

DB_PATH = "economy.db"

# === 정책/연출 ===
REVEAL_DELAY   = 2.5       # 강화 연출 총 길이(초)
PROGRESS_TICKS = 5         # 프레임 수
BASE_COST      = 5_000     # +0→+1 기본 비용
COST_GROWTH    = 1.65      # 단계당 비용 증가 배수
MAX_LEVEL      = 10        # +10 캡
DOWNGRADE_FROM = 4         # 이 레벨 이상에서 실패 시 -1 단계 하락(없애려면 None)

# 현재 레벨 기준 성공률(%)
SUCCESS_RATE = {
    0: 90, 1: 85, 2: 80, 3: 70, 4: 60,
    5: 45, 6: 35, 7: 25, 8: 15, 9: 10, 10: 0
}

# 레벨별 무기 스킨(이름/이미지). art가 None이면 이미지 생략.
WEAPON_SKINS = {
    0: {"name": "나무막대",    "art": None},
    1: {"name": "돌몽둥이",    "art": None},
    2: {"name": "연필",       "art": None},
    3: {"name": "몽당연필",    "art": None},
    4: {"name": "샤프",       "art": None},
    5: {"name": "강철볼펜",    "art": None},
    6: {"name": "스테인리스 자", "art": None},
    7: {"name": "해머",       "art": None},
    8: {"name": "광석검",      "art": None},
    9: {"name": "플라즈마 커터","art": None},
    10:{"name": "우주망치",    "art": None},
}
# ↑ 이미지를 쓰고 싶다면 각 레벨의 "art"에 URL을 넣어주세요(예: GitHub raw/CDN).

# === 공용 유틸 ===
KST = timezone(timedelta(hours=9))
def now_kst() -> datetime: return datetime.now(KST)
def won(n: int) -> str: return f"{n:,}₩"

def level_cost(level: int) -> int:
    raw = BASE_COST * (COST_GROWTH ** level)
    return int(round(raw / 100)) * 100  # 100원 단위 반올림

def weapon_name(level: int) -> str:
    meta = WEAPON_SKINS.get(level, {"name":"미상"})
    return f"LV{level} 「{meta['name']}」"

def progress_bar(p: float, width: int = 12) -> str:
    p = max(0.0, min(1.0, p))
    filled = int(round(p * width))
    return "▰" * filled + "▱" * (width - filled)

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

async def ensure_weapon_row(db, gid: int, uid: int):
    await db.execute(
        "INSERT OR IGNORE INTO user_weapons(guild_id,user_id,level,updated_at) VALUES(?,?,0,?)",
        (gid, uid, int(time.time()))
    )

async def get_level(db, gid: int, uid: int) -> int:
    cur = await db.execute("SELECT level FROM user_weapons WHERE guild_id=? AND user_id=?", (gid, uid))
    row = await cur.fetchone()
    return row[0] if row else 0

# === 패널 임베드 ===
def build_panel_embed(member: discord.Member, level: int, bal: int):
    rate = SUCCESS_RATE.get(level, 0)
    fail = max(0, 100 - rate)
    em = discord.Embed(
        title="무기 강화",
        description=f"당신이 보유한 무기는 **{weapon_name(level)}** 입니다.",
        color=0x2D9CDB
    )
    try:
        em.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    except Exception:
        em.set_author(name=member.display_name)

    em.add_field(name="강화비용", value=won(level_cost(level)), inline=True)
    em.add_field(name="성공률", value=f"{rate}%", inline=True)
    em.add_field(name="실패률", value=f"{fail}%", inline=True)

    if DOWNGRADE_FROM is not None and level >= DOWNGRADE_FROM:
        em.add_field(name="\u200b", value="※ **실패 시 1단계 하락**합니다.", inline=False)

    em.add_field(name="잔액", value=won(bal), inline=False)

    art = WEAPON_SKINS.get(level, {}).get("art")
    if art:
        em.set_image(url=art)

    return em

# === View: 버튼/흐름 ===
class EnhanceView(discord.ui.View):
    def __init__(self, gid: int, uid: int):
        super().__init__(timeout=180)
        self.gid = gid
        self.uid = uid
        self.busy = False

    async def _refresh(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await ensure_weapon_row(db, self.gid, self.uid)
            level = await get_level(db, self.gid, self.uid)
            u = await get_user(db, self.gid, self.uid)
            bal = u["balance"]

        # 버튼 활성/비활성 갱신
        can_enhance = (level < MAX_LEVEL) and (bal >= level_cost(level))
        for c in self.children:
            if isinstance(c, discord.ui.Button) and c.label == "강화하기":
                c.disabled = not can_enhance
            if isinstance(c, discord.ui.Button) and c.label == "취소":
                c.disabled = False

        em = build_panel_embed(interaction.user, level, bal)
        await interaction.edit_original_response(embed=em, view=self)

    @discord.ui.button(label="강화하기", style=discord.ButtonStyle.primary, row=0)
    async def do_enhance(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.uid:
            return await interaction.response.send_message("본인만 사용할 수 있습니다.", ephemeral=True)
        if self.busy:
            return await interaction.response.send_message("진행 중입니다.", ephemeral=True)
        self.busy = True
        await interaction.response.defer()

        # 1) 비용 차감/전처리
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            await ensure_weapon_row(db, self.gid, self.uid)
            level = await get_level(db, self.gid, self.uid)
            u = await get_user(db, self.gid, self.uid)
            bal = u["balance"]

            if level >= MAX_LEVEL:
                await db.execute("ROLLBACK")
                self.busy = False
                return await interaction.followup.send("이미 최대 레벨입니다.", ephemeral=True)

            cost = level_cost(level)
            rate = SUCCESS_RATE.get(level, 0)

            if bal < cost:
                await db.execute("ROLLBACK")
                self.busy = False
                return await interaction.followup.send(f"잔액 부족: 필요 {won(cost)}, 현재 {won(bal)}", ephemeral=True)

            new_bal = bal - cost
            await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?",
                             (new_bal, self.gid, self.uid))
            await write_ledger(db, self.gid, self.uid, "enhance_fee", -cost, new_bal,
                               {"from": level, "to": level+1, "rate": rate})
            await db.commit()

        # 2) 진행 애니메이션(메시지 수정)
        for i in range(1, PROGRESS_TICKS + 1):
            p = i / PROGRESS_TICKS
            em = discord.Embed(title="강화 진행 중…", color=0xF1C40F)
            try:
                em.set_author(name=interaction.user.display_name,
                              icon_url=interaction.user.display_avatar.url)
            except Exception:
                em.set_author(name=interaction.user.display_name)
            em.add_field(name="무기", value=weapon_name(level), inline=True)
            em.add_field(name="다음 등급", value=f"+{level+1}", inline=True)
            em.add_field(name="성공률", value=f"{SUCCESS_RATE.get(level,0)}%", inline=True)
            em.add_field(name="진행", value=f"{progress_bar(p)} **{int(p*100)}%**", inline=False)
            art = WEAPON_SKINS.get(level, {}).get("art")
            if art: em.set_image(url=art)
            await interaction.edit_original_response(embed=em, view=self)
            await asyncio.sleep(REVEAL_DELAY / PROGRESS_TICKS)

        # 3) 판정 & 반영
        roll = secrets.randbelow(100) + 1
        success = (roll <= SUCCESS_RATE.get(level, 0))
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT level FROM user_weapons WHERE guild_id=? AND user_id=?",
                                   (self.gid, self.uid))
            row = await cur.fetchone()
            cur_level = row[0] if row else 0
            new_level = cur_level
            if success:
                new_level = min(MAX_LEVEL, cur_level + 1)
            else:
                if DOWNGRADE_FROM is not None and cur_level >= DOWNGRADE_FROM:
                    new_level = max(0, cur_level - 1)

            await db.execute("UPDATE user_weapons SET level=?, updated_at=? WHERE guild_id=? AND user_id=?",
                             (new_level, int(time.time()), self.gid, self.uid))
            cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?",
                                   (self.gid, self.uid))
            bal_row = await cur.fetchone()
            bal_after = bal_row[0] if bal_row else 0

            await write_ledger(db, self.gid, self.uid, "enhance_result", 0, bal_after,
                               {"from": cur_level, "to": new_level, "success": success,
                                "roll": roll, "rate": SUCCESS_RATE.get(level,0)})
            await db.commit()

        # 4) 결과 + 다음 시도 패널로 갱신
        color = 0x2ecc71 if success else 0xe74c3c
        title = "강화 성공!" if success else "강화 실패"
        em = discord.Embed(title=title, color=color)
        try:
            em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        except Exception:
            em.set_author(name=interaction.user.display_name)
        em.add_field(name="이전 등급", value=f"+{cur_level}", inline=True)
        em.add_field(name="현재 등급", value=f"+{new_level}", inline=True)
        em.add_field(name="잔액", value=won(bal_after), inline=False)

        # 결과 이미지: 현재 등급 이미지
        art2 = WEAPON_SKINS.get(new_level, {}).get("art")
        if art2: em.set_image(url=art2)

        await interaction.edit_original_response(embed=em, view=self)
        # 짧은 지연 후 패널 재구성
        await asyncio.sleep(1.0)
        await self._refresh(interaction)
        self.busy = False

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary, row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.uid:
            return await interaction.response.send_message("본인만 사용할 수 있습니다.", ephemeral=True)
        for c in self.children: c.disabled = True
        await interaction.response.edit_message(content="강화를 종료했습니다.", view=self)

# === /면진강화 ===
@app_commands.command(name="mz_enhance", description="무기 강화(+0→+10) — 패널에서 버튼으로 진행")
async def mz_enhance(interaction: discord.Interaction):
    gid, uid = interaction.guild.id, interaction.user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        await ensure_weapon_row(db, gid, uid)
        level = await get_level(db, gid, uid)
        u = await get_user(db, gid, uid)
        bal = u["balance"]
        await db.commit()

    view = EnhanceView(gid, uid)
    em = build_panel_embed(interaction.user, level, bal)

    # 버튼 초기 활성화 상태 반영
    can_enhance = (level < MAX_LEVEL) and (bal >= level_cost(level))
    for c in view.children:
        if isinstance(c, discord.ui.Button) and c.label == "강화하기":
            c.disabled = not can_enhance

    await interaction.response.send_message(embed=em, view=view)

# === /면진도움말(강화 안내 포함) ===
@app_commands.command(name="mz_help", description="면진이 명령어 도움말")
async def mz_help(interaction: discord.Interaction):
    em = discord.Embed(title="면진이 — 도움말", color=0x3498db, description="주요 명령 요약")
    em.add_field(name="/면진돈줘", value="10분마다 1,000 코인 지급", inline=False)
    em.add_field(name="/면진출첵", value="자정 초기화 출석 보상", inline=False)
    em.add_field(name="/면진도박", value="승률 30~60% 랜덤, ±베팅액", inline=False)
    em.add_field(name="/면진주식 · /면진코인", value="가상 투자(3초 후 결과 공개)", inline=False)
    em.add_field(name="/면진강화", value="무기 강화(+0→+10). 패널에서 버튼으로 진행", inline=False)
    em.add_field(name="/면진맞짱", value="강화 무기로 PvP(차기 단계)", inline=False)
    await interaction.response.send_message(embed=em, ephemeral=True)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_enhance)
    bot.tree.add_command(mz_help)
