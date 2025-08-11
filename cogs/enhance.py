# cogs/enhance.py
import aiosqlite, asyncio, secrets, time
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta
from typing import Optional

DB_PATH = "economy.db"

# === 정책/연출 ===
REVEAL_DELAY   = 2.5
PROGRESS_TICKS = 5

# 비용 곡선( +10까지 완만, 이후 구간별 상승 )
BASE_COST          = 5_000
GROWTH_BELOW_10    = 1.35    # 0~9
GROWTH_10_TO_19    = 1.55    # 10~19
GROWTH_20_TO_29    = 1.75    # 20~29

MAX_LEVEL      = 30         # +30 캡
DOWNGRADE_FROM = 10         # +10 이상 실패 시 -1

# === 성공률( +10까지 쉽게 ) ===
def success_rate(level: int) -> int:
    if level >= MAX_LEVEL:
        return 0
    if level <= 9:
        return max(65, 95 - level * 3)         # 0:95 → 9:68(최저 65)
    if level <= 19:
        return max(10, 60 - (level - 10) * 5)  # 10:60 → 19:15(최저 10)
    return max(3, 12 - (level - 20) * 1)       # 20:12 → 29:3

# 레벨별 무기 스킨
WEAPON_SKINS = {
    0: {"name": "나무막대",       "art": None},
    1: {"name": "돌몽둥이",       "art": None},
    2: {"name": "연필",          "art": None},
    3: {"name": "몽당연필",       "art": None},
    4: {"name": "샤프",          "art": None},
    5: {"name": "강철볼펜",       "art": None},
    6: {"name": "스테인리스 자",  "art": None},
    7: {"name": "해머",          "art": None},
    8: {"name": "광석검",         "art": None},
    9: {"name": "플라즈마 커터",  "art": None},
    10:{"name": "우주망치",       "art": None},
}

# === 공용 유틸 ===
KST = timezone(timedelta(hours=9))
def now_kst() -> datetime: return datetime.now(KST)
def won(n: int) -> str: return f"{n:,}₩"

def level_cost(level: int) -> int:
    if level <= 9:   growth = GROWTH_BELOW_10
    elif level <= 19:growth = GROWTH_10_TO_19
    else:            growth = GROWTH_20_TO_29
    raw = BASE_COST * (growth ** level)
    return int(round(raw / 100)) * 100

def weapon_name(level: int) -> str:
    meta = WEAPON_SKINS.get(level)
    if meta: return f"LV{level} 「{meta['name']}」"
    return f"강화무기 Lv{level}"

def progress_bar(p: float, width: int = 12) -> str:
    p = max(0.0, min(1.0, p))
    filled = int(round(p * width))
    return "▰" * filled + "▱" * (width - filled)

async def get_user(db, gid: int, uid: int):
    cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid))
    row = await cur.fetchone()
    if row: return {"balance": row[0]}
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

# === 공용: 자동 취소 베이스 ===
class AutoCancelView(discord.ui.View):
    """버튼이 1분간 상호작용 없으면 자동 취소(관리자 메뉴에는 사용하지 않음)."""
    def __init__(self, timeout_seconds: int = 60):
        super().__init__(timeout=timeout_seconds)
        self.message: Optional[discord.Message] = None
        self.owner_id: Optional[int] = None
        self._timeout_seconds = timeout_seconds

    async def on_timeout(self):
        try:
            for c in self.children:
                if hasattr(c, "disabled"):
                    c.disabled = True
            if self.message:
                em = discord.Embed(
                    title="취소되었습니다",
                    description=f"{self._timeout_seconds}초가 지나 자동으로 취소되었습니다.",
                    color=0xE74C3C
                )
                await self.message.edit(embed=em, view=None)
        except Exception:
            pass

# === 패널 임베드 ===
def build_panel_embed(member: discord.Member, level: int, bal: int):
    rate = success_rate(level)
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

    if level >= MAX_LEVEL:
        em.add_field(name="상태", value=f"최대 등급 도달(+{MAX_LEVEL})", inline=False)
    else:
        em.add_field(name="강화비용", value=won(level_cost(level)), inline=True)
        em.add_field(name="성공률", value=f"{rate}%", inline=True)
        em.add_field(name="실패률", value=f"{fail}%", inline=True)
        if DOWNGRADE_FROM is not None and level >= DOWNGRADE_FROM:
            em.add_field(name="\u200b", value=f"※ **실패 시 1단계 하락** (+{DOWNGRADE_FROM} 이상)", inline=False)

    em.add_field(name="잔액", value=won(bal), inline=False)
    art = WEAPON_SKINS.get(level, {}).get("art")
    if art: em.set_image(url=art)
    return em

# === View: 버튼/흐름(자동 취소 상속) ===
class EnhanceView(AutoCancelView):
    def __init__(self, gid: int, uid: int):
        super().__init__(timeout_seconds=60)
        self.gid = gid
        self.uid = uid
        self.busy = False

    async def _refresh(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await ensure_weapon_row(db, self.gid, self.uid)
            level = await get_level(db, self.gid, self.uid)
            u = await get_user(db, self.gid, self.uid)
            bal = u["balance"]

        can_enhance = (level < MAX_LEVEL) and (bal >= level_cost(level))
        new_view = EnhanceView(self.gid, self.uid)
        new_view.owner_id = self.uid
        em = build_panel_embed(interaction.user, level, bal)
        for c in new_view.children:
            if isinstance(c, discord.ui.Button) and c.label == "강화하기":
                c.disabled = not can_enhance

        if self.message:
            await self.message.edit(embed=em, view=new_view)
            new_view.message = self.message
        else:
            await interaction.edit_original_response(embed=em, view=new_view)
            new_view.message = await interaction.original_response()

    @discord.ui.button(label="강화하기", style=discord.ButtonStyle.primary, row=0)
    async def do_enhance(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.uid:
            return await interaction.response.send_message("본인만 사용할 수 있습니다.", ephemeral=True)
        if self.busy:
            return await interaction.response.send_message("진행 중입니다.", ephemeral=True)
        self.busy = True
        await interaction.response.defer()

        # 비용 차감
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            await ensure_weapon_row(db, self.gid, self.uid)
            level = await get_level(db, self.gid, self.uid)
            u = await get_user(db, self.gid, self.uid)
            bal = u["balance"]

            if level >= MAX_LEVEL:
                await db.execute("ROLLBACK")
                self.busy = False
                return await interaction.followup.send(f"이미 최대 레벨(+{MAX_LEVEL})입니다.", ephemeral=True)

            cost = level_cost(level)
            rate = success_rate(level)
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

        # 애니메이션
        for i in range(1, PROGRESS_TICKS + 1):
            p = i / PROGRESS_TICKS
            em = discord.Embed(title="강화 진행 중…", color=0xF1C40F)
            try:
                em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            except Exception:
                em.set_author(name=interaction.user.display_name)
            em.add_field(name="무기", value=weapon_name(level), inline=True)
            em.add_field(name="다음 등급", value=f"+{level+1}", inline=True)
            em.add_field(name="성공률", value=f"{success_rate(level)}%", inline=True)
            em.add_field(name="진행", value=f"{progress_bar(p)} **{int(p*100)}%**", inline=False)
            art = WEAPON_SKINS.get(level, {}).get("art")
            if art: em.set_image(url=art)
            if self.message:
                await self.message.edit(embed=em, view=self)
            else:
                await interaction.edit_original_response(embed=em, view=self)
            await asyncio.sleep(REVEAL_DELAY / PROGRESS_TICKS)

        # 판정 & 반영
        roll = secrets.randbelow(100) + 1
        success = (roll <= success_rate(level))
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
                                "roll": roll, "rate": success_rate(level)})
            await db.commit()

        # 결과 표시
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
        art2 = WEAPON_SKINS.get(new_level, {}).get("art")
        if art2: em.set_image(url=art2)

        if self.message:
            await self.message.edit(embed=em, view=self)
        else:
            await interaction.edit_original_response(embed=em, view=self)

        await asyncio.sleep(1.0)
        await self._refresh(interaction)
        self.busy = False

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary, row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.uid:
            return await interaction.response.send_message("본인만 사용할 수 있습니다.", ephemeral=True)
        for c in self.children: c.disabled = True
        if self.message:
            await interaction.response.edit_message(content="강화를 종료했습니다.", view=None)
        else:
            await interaction.response.edit_message(content="강화를 종료했습니다.", view=None)

# === /면진강화 ===
@app_commands.command(name="mz_enhance", description="무기 강화(+30) — 1분 무응답 시 자동 취소")
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
    view.owner_id = uid
    em = build_panel_embed(interaction.user, level, bal)

    can_enhance = (level < MAX_LEVEL) and (bal >= level_cost(level))
    for c in view.children:
        if isinstance(c, discord.ui.Button) and c.label == "강화하기":
            c.disabled = not can_enhance

    await interaction.response.send_message(embed=em, view=view)
    view.message = await interaction.original_response()

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_enhance)
