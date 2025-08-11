import os, aiosqlite, asyncio, secrets, time, random
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta
from typing import Optional

DB_PATH = "economy.db"

# ───────── 시간/표시 유틸 ─────────
KST = timezone(timedelta(hours=9))
def now_kst() -> datetime: return datetime.now(KST)
def won(n: int) -> str: return f"{n:,}₩"

# ───────── 강화 테이블: 1~30 ─────────
ENH_TABLE = [
    None,
    {"name":"샤프심","s":100,"f":0,"d":0,"b":0,"cost":2000},
    {"name":"연필","s":98,"f":2,"d":0,"b":0,"cost":2200},
    {"name":"페트병","s":96,"f":4,"d":0,"b":0,"cost":2500},
    {"name":"파리채","s":94,"f":6,"d":0,"b":0,"cost":2900},
    {"name":"우산","s":90,"f":10,"d":0,"b":0,"cost":3400},
    {"name":"노트북","s":87,"f":13,"d":0,"b":0,"cost":4000},
    {"name":"낡은 단검","s":84,"f":16,"d":0,"b":0,"cost":5000},
    {"name":"쓸만한 단검","s":80,"f":20,"d":0,"b":0,"cost":6500},
    {"name":"견고한 단검","s":76,"f":24,"d":0,"b":0,"cost":8500},
    {"name":"빠따","s":73,"f":27,"d":0,"b":0,"cost":11000},
    {"name":"전기톱","s":65,"f":25,"d":10,"b":0,"cost":15000},
    {"name":"롱소드","s":62,"f":24,"d":12,"b":2,"cost":20000},
    {"name":"화염의 검","s":59,"f":24,"d":14,"b":3,"cost":28000},
    {"name":"냉기의 검","s":56,"f":23,"d":16,"b":5,"cost":40000},
    {"name":"듀얼블레이드","s":53,"f":22,"d":18,"b":7,"cost":60000},
    {"name":"심판자의 검","s":50,"f":21,"d":20,"b":9,"cost":85000},
    {"name":"엑스칼리버","s":47,"f":20,"d":22,"b":11,"cost":120000},
    {"name":"플라즈마 소드","s":44,"f":19,"d":24,"b":13,"cost":170000},
    {"name":"천총운검","s":41,"f":18,"d":26,"b":15,"cost":240000},
    {"name":"사인참사검","s":38,"f":17,"d":28,"b":17,"cost":330000},
    {"name":"뒤랑칼","s":35,"f":17,"d":30,"b":18,"cost":450000},
    {"name":"파멸의 대검","s":32,"f":16,"d":33,"b":19,"cost":600000},
    {"name":"여명의 검","s":29,"f":15,"d":35,"b":21,"cost":800000},
    {"name":"키샨","s":26,"f":14,"d":38,"b":22,"cost":1100000},
    {"name":"영원의 창","s":23,"f":14,"d":40,"b":23,"cost":1500000},
    {"name":"정의의 저울","s":21,"f":13,"d":42,"b":24,"cost":2000000},
    {"name":"사신의 낫","s":19,"f":12,"d":43,"b":26,"cost":2700000},
    {"name":"아스트라페","s":17,"f":11,"d":45,"b":27,"cost":3600000},
    {"name":"롱기누스의 창","s":16,"f":10,"d":46,"b":28,"cost":4800000},
    {"name":"면진검","s":15,"f":10,"d":47,"b":28,"cost":6500000},
]
MAX_LV = 30

# ───────── DB 유틸 ─────────
async def get_user(db, gid: int, uid: int):
    cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid))
    row = await cur.fetchone()
    if row:
        return {"balance": row[0]}
    await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, uid, 0))
    await db.commit()
    return {"balance": 0}

async def ensure_weapon_row(db, gid: int, uid: int):
    await db.execute(
        "INSERT OR IGNORE INTO user_weapons(guild_id,user_id,level,updated_at) VALUES(?,?,0,?)",
        (gid, uid, int(time.time()))
    )

async def get_level(db, gid: int, uid: int) -> int:
    await ensure_weapon_row(db, gid, uid)
    cur = await db.execute("SELECT level FROM user_weapons WHERE guild_id=? AND user_id=?", (gid, uid))
    row = await cur.fetchone()
    return row[0] if row else 0

async def set_level(db, gid: int, uid: int, lv: int):
    await db.execute("UPDATE user_weapons SET level=?, updated_at=? WHERE guild_id=? AND user_id=?",
                     (lv, int(time.time()), gid, uid))

async def write_ledger(db, gid, uid, kind, amount, bal_after, meta=None):
    await db.execute(
        "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) VALUES(?,?,?,?,?,?,?)",
        (gid, uid, kind, amount, bal_after, (meta and str(meta)) or "{}", int(time.time()))
    )

async def get_enh_cost_mult(gid: int) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COALESCE(enh_cost_mult,1.0) FROM guild_settings WHERE guild_id=?", (gid,))
        row = await cur.fetchone()
        return float(row[0] if row else 1.0)

async def get_force_mode(gid: int) -> tuple[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COALESCE(force_mode,'off'), COALESCE(force_target_user_id,0) FROM guild_settings WHERE guild_id=?", (gid,))
        row = await cur.fetchone()
        return (row[0], int(row[1] or 0)) if row else ("off", 0)

# ───────── 진행바/임베드 ─────────
def _progress_bar(p: float, width: int = 12) -> str:
    p = max(0.0, min(1.0, p))
    fill = int(round(p * width))
    return "▰" * fill + "▱" * (width - fill)

def lv_name(lv: int) -> str:
    if lv <= 0: return "맨손"
    if lv > MAX_LV: lv = MAX_LV
    return ENH_TABLE[lv]["name"]

def next_row(curr_lv: int):
    nxt = min(curr_lv + 1, MAX_LV)
    return ENH_TABLE[nxt], nxt

def _progress_embed(user: discord.Member, curr_lv: int, next_name: str, pct: int):
    em = discord.Embed(title="강화 준비 중…", color=0xF1C40F)
    try: em.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    except Exception: em.set_author(name=user.display_name)
    em.add_field(name="현재", value=f"LV{curr_lv}", inline=True)
    em.add_field(name="다음", value=next_name, inline=True)
    em.add_field(name="진행", value=f"{_progress_bar(pct/100)} **{pct}%**", inline=False)
    return em

def enhance_embed(user: discord.Member, gid: int, curr_lv: int, bal: int):
    row, nxt = next_row(curr_lv)
    title = f"당신이 보유한 무기는\nLV{curr_lv}『{lv_name(curr_lv)}』" if curr_lv>0 else "당신은 아직 무기가 없습니다"
    em = discord.Embed(title=title, color=0x3498db)
    try: em.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    except Exception: em.set_author(name=user.display_name)
    if curr_lv >= MAX_LV:
        em.add_field(name="강화 상태", value=f"이미 **최대 레벨(LV{MAX_LV})** 입니다.", inline=False)
    else:
        # 비용은 표시만, 계산은 실행 시 get_enh_cost_mult 사용
        em.add_field(name="다음 단계", value=f"LV{nxt} 『{row['name']}』", inline=False)
        em.add_field(name="강화비용(기본)", value=won(row["cost"]), inline=True)
        em.add_field(name="성공/실패/하락/파괴", value=f"{row['s']}% / {row['f']}% / {row['d']}% / {row['b']}%", inline=True)
    em.add_field(name="잔액", value=won(bal), inline=False)
    em.set_footer(text=f"오늘 {now_kst().strftime('%H:%M')}")
    return em

# ───────── UI View(자동 취소) ─────────
class AutoCancelView(discord.ui.View):
    def __init__(self, timeout_seconds: int = 60):
        super().__init__(timeout=timeout_seconds)
        self.message: Optional[discord.Message] = None
        self._timeout_seconds = timeout_seconds
        self.finalized = False

    async def on_timeout(self):
        if self.finalized or not self.message:
            return
        try:
            for c in self.children:
                if hasattr(c, "disabled"):
                    c.disabled = True
            em = discord.Embed(title="취소되었습니다", description=f"{self._timeout_seconds}초가 지나 자동으로 취소되었습니다.", color=0xE74C3C)
            await self.message.edit(embed=em, view=None)
        except Exception:
            pass

# ───────── 강화 View ─────────
class EnhanceView(AutoCancelView):
    def __init__(self, gid: int, uid: int, curr_lv: int, bal: int):
        super().__init__(timeout_seconds=60)
        self.gid, self.uid = gid, uid
        self.curr_lv, self.bal = curr_lv, bal
        self.btn_enh = discord.ui.Button(label="강화하기", style=discord.ButtonStyle.primary, row=0, disabled=(curr_lv>=MAX_LV))
        self.btn_cancel = discord.ui.Button(label="취소", style=discord.ButtonStyle.secondary, row=0)
        self.btn_enh.callback = self._do_enhance
        self.btn_cancel.callback = self._do_cancel
        self.add_item(self.btn_enh); self.add_item(self.btn_cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.uid:
            await interaction.response.send_message("이 메뉴는 생성한 사용자만 조작할 수 있습니다.", ephemeral=True)
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (self.gid, self.uid))
            row = await cur.fetchone()
            self.bal = row[0] if row else 0
            cur = await db.execute("SELECT level FROM user_weapons WHERE guild_id=? AND user_id=?", (self.gid, self.uid))
            row = await cur.fetchone()
            self.curr_lv = row[0] if row else 0
            await db.commit()
        self.btn_enh.disabled = (self.curr_lv >= MAX_LV)
        if self.message:
            await self.message.edit(embed=enhance_embed(interaction.user, self.gid, self.curr_lv, self.bal), view=self)

    async def _do_cancel(self, interaction: discord.Interaction):
        self.finalized = True
        for c in self.children: c.disabled = True
        await interaction.response.edit_message(embed=discord.Embed(title="취소되었습니다", color=0x95a5a6), view=None)
        self.stop()

    async def _do_enhance(self, interaction: discord.Interaction):
        if self.curr_lv >= MAX_LV:
            return await interaction.response.send_message("이미 최대 레벨입니다.", ephemeral=True)

        row, nxt = next_row(self.curr_lv)
        mult = await get_enh_cost_mult(self.gid)
        cost = int(round(row["cost"] * float(mult)))

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (self.gid, self.uid))
            r = await cur.fetchone()
            bal = r[0] if r else 0
            if bal < cost:
                await db.execute("ROLLBACK")
                return await interaction.response.send_message(f"잔액 부족: 필요 {won(cost)} / 현재 {won(bal)}", ephemeral=True)
            new_bal = bal - cost
            await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, self.gid, self.uid))
            await write_ledger(db, self.gid, self.uid, "enhance_cost", -cost, new_bal, {"to": nxt})
            await db.commit()

        await interaction.response.defer()
        for pct in (0, 20, 40, 60, 80, 100):
            em_prog = _progress_embed(interaction.user, self.curr_lv, f"LV{nxt} 『{row['name']}』", pct)
            if self.message: await self.message.edit(embed=em_prog, view=self)
            else: await interaction.edit_original_response(embed=em_prog, view=self)
            await asyncio.sleep(0.5)

        # 결과 계산(강제 모드 반영)
        force_mode, force_uid = await get_force_mode(self.gid)
        forced = (force_mode in ("success","fail") and (force_uid == 0 or force_uid == self.uid))
        if forced:
            outcome = "success" if force_mode == "success" else "fail"
        else:
            roll = secrets.randbelow(10000) / 100.0
            s, f, d, b = row["s"], row["f"], row["d"], row["b"]
            if roll < s: outcome = "success"
            elif roll < s + f: outcome = "fail"
            elif roll < s + f + d: outcome = "down"
            else: outcome = "break"

        if outcome == "success":
            new_lv = min(self.curr_lv + 1, MAX_LV)
        elif outcome == "down":
            new_lv = max(0, self.curr_lv - 1)
        elif outcome == "break":
            new_lv = 0
        else:  # fail
            new_lv = self.curr_lv

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            await set_level(db, self.gid, self.uid, new_lv)
            await write_ledger(
                db, self.gid, self.uid, "enhance_result", 0, new_bal,
                {"from": self.curr_lv, "to": new_lv, "nxt": nxt, "outcome": outcome, "forced": forced}
            )
            await db.commit()

        self.curr_lv, self.bal = new_lv, new_bal
        result_txt = {
            "success": f"강화 **성공**! → LV{self.curr_lv} 『{lv_name(self.curr_lv)}』",
            "fail":    "강화 **실패**(등급 유지)",
            "down":    f"강화 **실패**(등급 하락) → LV{self.curr_lv}",
            "break":   "강화 **실패**(무기 **파괴**) → LV0",
        }[outcome]

        em = enhance_embed(interaction.user, self.gid, self.curr_lv, self.bal)
        em.insert_field_at(0, name="결과", value=result_txt, inline=False)
        self.btn_enh.disabled = (self.curr_lv >= MAX_LV)
        self.finalized = True
        if self.message: await self.message.edit(embed=em, view=self)
        else: await interaction.edit_original_response(embed=em, view=self)

# ───────── Slash 명령 ─────────
@app_commands.command(name="mz_enhance", description="무기 강화 메뉴를 엽니다")
async def mz_enhance(interaction: discord.Interaction):
    gid, uid = interaction.guild.id, interaction.user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        await ensure_weapon_row(db, gid, uid)
        cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid))
        r = await cur.fetchone()
        bal = r[0] if r else 0
        cur = await db.execute("SELECT level FROM user_weapons WHERE guild_id=? AND user_id=?", (gid, uid))
        r = await cur.fetchone()
        lv = r[0] if r else 0
        await db.commit()

    view = EnhanceView(gid, uid, lv, bal)
    await interaction.response.send_message(embed=enhance_embed(interaction.user, gid, lv, bal), view=view)
    view.message = await interaction.original_response()

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_enhance)
