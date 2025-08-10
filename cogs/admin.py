import os, aiosqlite, json, time
import discord
from discord import app_commands

DB_PATH = "economy.db"

# ───────── 권한 유틸 ─────────
def owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        owner_id = int(os.getenv("OWNER_ID", "0"))
        dev_mode = os.getenv("DEV_MODE", "0") == "1"
        return dev_mode or (interaction.user.id == owner_id)
    return app_commands.check(predicate)

# ───────── 설정/DB 유틸 ─────────
async def get_settings(db, gid: int):
    cur = await db.execute(
        "SELECT min_bet, win_min_bps, win_max_bps, mode_name FROM guild_settings WHERE guild_id=?",
        (gid,)
    )
    row = await cur.fetchone()
    if row:
        return {"min_bet": row[0], "win_min_bps": row[1], "win_max_bps": row[2], "mode_name": row[3]}
    await db.execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)", (gid,))
    await db.commit()
    return {"min_bet": 1000, "win_min_bps": 3000, "win_max_bps": 6000, "mode_name": "일반 모드"}

async def set_setting_field(db, gid: int, key: str, value: str):
    await db.execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)", (gid,))
    if key == "mode_name":
        await db.execute("UPDATE guild_settings SET mode_name=? WHERE guild_id=?", (value, gid))
    elif key in ("min_bet", "win_min_bps", "win_max_bps"):
        raw = value.strip().replace("%", "")
        if key.startswith("win_"):
            num = int(round(float(raw) * 100))
        else:
            num = int(raw)
        await db.execute(f"UPDATE guild_settings SET {key}=? WHERE guild_id=?", (num, gid))
    else:
        raise ValueError("unknown key")
    await db.commit()

async def apply_balance_change(gid: int, uid: int, op: str, amount: int, actor: int, reason: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid))
        row = await cur.fetchone()
        if row is None:
            await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, uid, 0))
            old_bal = 0
        else:
            old_bal = row[0]

        if op == "set":
            new_bal = amount
            delta = new_bal - old_bal
            kind = "admin_set"
        elif op == "add":
            new_bal = old_bal + amount
            delta = amount
            kind = "admin_add"
        else:
            new_bal = old_bal - amount
            delta = -amount
            kind = "admin_sub"

        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await db.execute(
            "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) VALUES(?,?,?,?,?,?,?)",
            (gid, uid, kind, delta, new_bal, json.dumps({"by": actor, "reason": reason or ""}), int(time.time()))
        )
        await db.commit()
    return old_bal, new_bal, delta

def settings_embed(s: dict) -> discord.Embed:
    em = discord.Embed(title="서버 설정", color=0x3498db)
    em.add_field(name="최소 베팅", value=f"{s['min_bet']:,}₩")
    em.add_field(name="승률 하한", value=f"{s['win_min_bps']/100:.2f}%")
    em.add_field(name="승률 상한", value=f"{s['win_max_bps']/100:.2f}%")
    em.add_field(name="모드명", value=s["mode_name"], inline=False)
    return em

# 쿨타임 초기화
async def reset_cooldown(gid: int, actor_id: int, which: str, target_uid: int | None, reason: str | None = None):
    which = which.lower().strip()
    if which not in ("money", "attend", "both"):
        raise ValueError("which must be money/attend/both")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        if which in ("money", "both"):
            if target_uid is None:
                await db.execute("UPDATE users SET last_claim_at=NULL WHERE guild_id=?", (gid,))
            else:
                await db.execute("UPDATE users SET last_claim_at=NULL WHERE guild_id=? AND user_id=?", (gid, target_uid))
        if which in ("attend", "both"):
            if target_uid is None:
                await db.execute("UPDATE users SET last_daily_at=NULL WHERE guild_id=?", (gid,))
            else:
                await db.execute("UPDATE users SET last_daily_at=NULL WHERE guild_id=? AND user_id=?", (gid, target_uid))
        meta = {"by": actor_id, "which": which, "scope": ("all" if target_uid is None else "user"), "reason": reason or ""}
        log_uid = (target_uid if target_uid is not None else 0)
        await db.execute(
            "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) VALUES(?,?,?,?,?,?,?)",
            (gid, log_uid, "admin_reset_cd", 0, 0, json.dumps(meta), int(time.time()))
        )
        await db.commit()

# ───────── 도움말 임베드 ─────────
def admin_help_embed() -> discord.Embed:
    em = discord.Embed(title="도움말 — 슬래시 명령이 안 보일 때", color=0x95a5a6)
    em.description = (
        "• **데스크톱(PC)**: Discord 창에서 **Ctrl+R**(하드 리로드)로 명령 목록을 새로 고침하세요.\n"
        "• **모바일**: 앱을 완전히 종료 후 다시 실행하면 목록이 갱신됩니다.\n"
        "• **전파 지연**: **전역(Global)** 명령은 반영까지 시간이 걸릴 수 있습니다(보통 최대 1시간). "
        "**길드(Guild)** 명령은 즉시 반영됩니다.\n"
        "• **개발 시 권장**: `.env`에 `DEV_GUILD_ID`를 설정하고, 봇이 **길드 싱크**를 수행하도록 하세요.\n"
        "• **권한 확인**: 서버/채널 권한에서 **애플리케이션 명령어 사용(Use Application Commands)** 이 허용되어 있는지 확인하세요."
    )
    return em

# ───────── 닉네임 안전화 ─────────
async def safe_name(guild: discord.Guild, user_id: int) -> str:
    m = guild.get_member(user_id)
    if m:
        return m.display_name
    try:
        m = await guild.fetch_member(user_id)
        return m.display_name
    except Exception:
        return f"<@{user_id}>"

# ---- 마켓 시드/보증 ----
SEED_STOCKS = [
    ("성현전자", -20.0,  20.0),
    ("배달의 승기", -30.0, 30.0),
    ("대이식스",  -10.0,  10.0),
    ("재구식품",   -5.0,   5.0),
]
SEED_COINS = [
    ("건영코인",  -60.0, 120.0),
    ("면진코인", -120.0, 240.0),
    ("승철코인", -200.0, 400.0),
]

async def ensure_seed_markets_admin(gid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM market_items WHERE guild_id=?", (gid,))
        cnt = (await cur.fetchone())[0]
        if cnt == 0:
            await db.executemany(
                "INSERT INTO market_items(guild_id,type,name,range_lo,range_hi,enabled) VALUES(?,?,?,?,?,1)",
                [(gid,"stock",n,lo,hi) for (n,lo,hi) in SEED_STOCKS] +
                [(gid,"coin", n,lo,hi) for (n,lo,hi) in SEED_COINS]
            )
            await db.commit()

# ───────── 모달 ─────────
class ConfigValueModal(discord.ui.Modal, title="설정 값 입력"):
    value = discord.ui.TextInput(label="값", placeholder="예) 2000 / 35(%) / 이벤트 모드", required=True)

    def __init__(self, key: str, key_label: str, gid: int):
        super().__init__(timeout=180)
        self.key = key
        self.key_label = key_label
        self.gid = gid
        self.title = f"{self.key_label} 변경"

    async def on_submit(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await set_setting_field(db, self.gid, self.key, str(self.value))
            s = await get_settings(db, self.gid)
        em = settings_embed(s); em.title = f"{self.key_label} 변경 완료"
        await interaction.response.send_message(embed=em, ephemeral=True)

class BalanceAmountModal(discord.ui.Modal, title="잔액 입력"):
    amount = discord.ui.TextInput(label="금액(정수)", placeholder="예) 10000", required=True)
    reason = discord.ui.TextInput(label="사유(선택)", style=discord.TextStyle.paragraph, required=False)

    def __init__(self, gid: int, uid: int, op: str, user_label: str):
        super().__init__(timeout=180)
        self.gid, self.uid, self.op, self.user_label = gid, uid, op, user_label
        self.title = f"{user_label} · {op.upper()}"

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amt = int(str(self.amount).replace(",", "").strip())
        except ValueError:
            return await interaction.response.send_message("금액은 정수여야 합니다.", ephemeral=True)
        old_bal, new_bal, delta = await apply_balance_change(
            self.gid, self.uid, self.op, amt, interaction.user.id, str(self.reason) or None
        )
        color = 0x2ecc71 if delta >= 0 else 0xe74c3c
        sign = "+" if delta >= 0 else ""
        name = await safe_name(interaction.guild, self.uid)
        em = discord.Embed(title="잔액 변경 완료", color=color)
        em.add_field(name="대상", value=f"{name} (`{self.uid}`)")
        em.add_field(name="동작", value=self.op)
        em.add_field(name="이전 잔액", value=f"{old_bal:,}₩")
        em.add_field(name="변화량", value=f"{sign}{delta:,}₩")
        em.add_field(name="현재 잔액", value=f"{new_bal:,}₩", inline=False)
        if self.reason:
            em.add_field(name="사유", value=str(self.reason), inline=False)
        em.set_footer(text=f"실행: {interaction.user.display_name}")
        await interaction.response.send_message(embed=em, ephemeral=True)

# ───────── Select: 대상 사용자 선택 (row=0 전용) ─────────
class TargetUserSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="대상 선택(미선택 = 전체)", min_values=0, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        view = self.view  # type: ignore
        assert isinstance(view, (BalanceView, CooldownView))
        view.target_user_id = self.values[0].id if self.values else None
        picked = (await safe_name(interaction.guild, view.target_user_id) if view.target_user_id
                  else "서버 전체")
        await interaction.response.send_message(f"대상 선택: **{picked}**", ephemeral=True)

# ───────── 서브 뷰: 설정 ─────────
class SettingsView(discord.ui.View):
    def __init__(self, gid: int):
        super().__init__(timeout=300)
        self.gid = gid

    @discord.ui.button(label="설정 보기", style=discord.ButtonStyle.primary, row=1)
    async def view_settings(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            s = await get_settings(db, self.gid)
        await interaction.edit_original_response(embed=settings_embed(s), view=self, content=None)

    @discord.ui.button(label="최소베팅 수정", style=discord.ButtonStyle.secondary, row=1)
    async def edit_min_bet(self, interaction, _):
        await interaction.response.send_modal(ConfigValueModal("min_bet", "최소베팅", self.gid))

    @discord.ui.button(label="승률하한(%) 수정", style=discord.ButtonStyle.secondary, row=1)
    async def edit_win_min(self, interaction, _):
        await interaction.response.send_modal(ConfigValueModal("win_min_bps", "승률 하한(%)", self.gid))

    @discord.ui.button(label="승률상한(%) 수정", style=discord.ButtonStyle.secondary, row=1)
    async def edit_win_max(self, interaction, _):
        await interaction.response.send_modal(ConfigValueModal("win_max_bps", "승률 상한(%)", self.gid))

    @discord.ui.button(label="모드명 수정", style=discord.ButtonStyle.secondary, row=1)
    async def edit_mode_name(self, interaction, _):
        await interaction.response.send_modal(ConfigValueModal("mode_name", "모드명", self.gid))

    @discord.ui.button(label="← 메인으로", style=discord.ButtonStyle.danger, row=4)
    async def back(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(embed=admin_main_embed(), view=AdminMainView(self.gid), content=None)

# ───────── 서브 뷰: 잔액 ─────────
class BalanceView(discord.ui.View):
    def __init__(self, gid: int):
        super().__init__(timeout=300)
        self.gid = gid
        self.target_user_id: int | None = None
        self.add_item(TargetUserSelect())  # row=0 전용

    @discord.ui.button(label="잔액 설정", style=discord.ButtonStyle.success, row=1)
    async def bal_set(self, interaction, _):
        if self.target_user_id is None:
            return await interaction.response.send_message("먼저 **대상 사용자**를 선택하세요.", ephemeral=True)
        label = await safe_name(interaction.guild, self.target_user_id)
        await interaction.response.send_modal(BalanceAmountModal(self.gid, self.target_user_id, "set", label))

    @discord.ui.button(label="잔액 증가", style=discord.ButtonStyle.success, row=1)
    async def bal_add(self, interaction, _):
        if self.target_user_id is None:
            return await interaction.response.send_message("먼저 **대상 사용자**를 선택하세요.", ephemeral=True)
        label = await safe_name(interaction.guild, self.target_user_id)
        await interaction.response.send_modal(BalanceAmountModal(self.gid, self.target_user_id, "add", label))

    @discord.ui.button(label="잔액 감소", style=discord.ButtonStyle.danger, row=1)
    async def bal_sub(self, interaction, _):
        if self.target_user_id is None:
            return await interaction.response.send_message("먼저 **대상 사용자**를 선택하세요.", ephemeral=True)
        label = await safe_name(interaction.guild, self.target_user_id)
        await interaction.response.send_modal(BalanceAmountModal(self.gid, self.target_user_id, "sub", label))

    @discord.ui.button(label="← 메인으로", style=discord.ButtonStyle.danger, row=4)
    async def back(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(embed=admin_main_embed(), view=AdminMainView(self.gid), content=None)

# ───────── 서브 뷰: 쿨타임 ─────────
class CooldownView(discord.ui.View):
    def __init__(self, gid: int):
        super().__init__(timeout=300)
        self.gid = gid
        self.target_user_id: int | None = None
        self.add_item(TargetUserSelect())  # row=0 전용

    @discord.ui.button(label="돈줘 초기화", style=discord.ButtonStyle.secondary, row=1)
    async def cd_money(self, interaction, _):
        await reset_cooldown(self.gid, interaction.user.id, "money", self.target_user_id, None)
        picked = await safe_name(interaction.guild, self.target_user_id) if self.target_user_id else "서버 전체"
        await interaction.response.send_message(f"✅ **돈줘** 쿨타임 초기화 완료 · 대상: {picked}", ephemeral=True)

    @discord.ui.button(label="출첵 초기화", style=discord.ButtonStyle.secondary, row=1)
    async def cd_attend(self, interaction, _):
        await reset_cooldown(self.gid, interaction.user.id, "attend", self.target_user_id, None)
        picked = await safe_name(interaction.guild, self.target_user_id) if self.target_user_id else "서버 전체"
        await interaction.response.send_message(f"✅ **출첵** 쿨타임 초기화 완료 · 대상: {picked}", ephemeral=True)

    @discord.ui.button(label="모두 초기화", style=discord.ButtonStyle.secondary, row=1)
    async def cd_both(self, interaction, _):
        await reset_cooldown(self.gid, interaction.user.id, "both", self.target_user_id, None)
        picked = await safe_name(interaction.guild, self.target_user_id) if self.target_user_id else "서버 전체"
        await interaction.response.send_message(f"✅ **돈줘/출첵** 쿨타임 초기화 완료 · 대상: {picked}", ephemeral=True)

    @discord.ui.button(label="← 메인으로", style=discord.ButtonStyle.danger, row=4)
    async def back(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(embed=admin_main_embed(), view=AdminMainView(self.gid), content=None)

# ───────── 서브 뷰: 도구 ─────────
class ToolsView(discord.ui.View):
    def __init__(self, gid: int):
        super().__init__(timeout=300)
        self.gid = gid

    @discord.ui.button(label="도움말", style=discord.ButtonStyle.secondary, row=1)
    async def help_btn(self, interaction, _):
        await interaction.response.send_message(embed=admin_help_embed(), ephemeral=True)

    @discord.ui.button(label="명령 재동기화", style=discord.ButtonStyle.primary, row=1)
    async def resync_btn(self, interaction, _):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            synced = await interaction.client.tree.sync(guild=discord.Object(id=interaction.guild.id))
            await interaction.followup.send(f"✅ 이 길드 재동기화 완료 · 현재 등록 {len(synced)}개", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"재동기화 중 문제 발생: {type(e).__name__}: {e}", ephemeral=True)

    @discord.ui.button(label="← 메인으로", style=discord.ButtonStyle.danger, row=4)
    async def back(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(embed=admin_main_embed(), view=AdminMainView(self.gid), content=None)

# ───────── 서브 뷰: 마켓(주식/코인) 편집 ─────────
class MarketModal(discord.ui.Modal, title="마켓 항목"):
    t_type  = discord.ui.TextInput(label="종류(stock/coin)")
    t_name  = discord.ui.TextInput(label="이름(예: 성현전자)")
    t_lo    = discord.ui.TextInput(label="최소 변화율(예: -20.0)")
    t_hi    = discord.ui.TextInput(label="최대 변화율(예: 20.0)")
    t_enable= discord.ui.TextInput(label="활성화(1/0)", default="1")

    def __init__(self, gid: int, mode: str):
        super().__init__(timeout=180)
        self.gid = gid
        self.mode = mode  # 'add' | 'del'
        self.title = f"마켓 {mode.upper()}"

    async def on_submit(self, interaction: discord.Interaction):
        typ = str(self.t_type).strip().lower()
        name = str(self.t_name).strip()
        if typ not in ("stock","coin"):
            return await interaction.response.send_message("type은 stock/coin만 허용됩니다.", ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            if self.mode == "del":
                await db.execute("DELETE FROM market_items WHERE guild_id=? AND type=? AND name=?",
                                 (self.gid, typ, name))
            else:
                lo = float(str(self.t_lo))
                hi = float(str(self.t_hi))
                enable = 1 if str(self.t_enable).strip() == "1" else 0
                await db.execute("""
                    INSERT INTO market_items(guild_id,type,name,range_lo,range_hi,enabled)
                    VALUES(?,?,?,?,?,?)
                    ON CONFLICT(guild_id,type,name) DO UPDATE SET
                      range_lo=excluded.range_lo, range_hi=excluded.range_hi, enabled=excluded.enabled
                """, (self.gid, typ, name, lo, hi, enable))
            await db.commit()
        await interaction.response.send_message(f"✅ {self.mode.upper()} 완료: {typ} · {name}", ephemeral=True)

class MarketView(discord.ui.View):
    def __init__(self, gid: int):
        super().__init__(timeout=300)
        self.gid = gid

    @discord.ui.button(label="목록 보기", style=discord.ButtonStyle.secondary, row=1)
    async def list_items(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        await ensure_seed_markets_admin(self.gid)
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT type,name,range_lo,range_hi,enabled FROM market_items WHERE guild_id=? ORDER BY type,name",
                (self.gid,)
            )
            rows = await cur.fetchall()
        if not rows:
            txt = "등록된 항목이 없습니다."
        else:
            lines = [f"• [{t}] {n}  {lo:+.1f}% ~ {hi:+.1f}%  ({'on' if en else 'off'})"
                     for (t,n,lo,hi,en) in rows]
            txt = "\n".join(lines[:50])
        em = discord.Embed(title="마켓 항목", description=txt or " ", color=0x95a5a6)
        await interaction.edit_original_response(embed=em, view=self, content=None)

    @discord.ui.button(label="추가/수정", style=discord.ButtonStyle.success, row=1)
    async def add_edit(self, interaction, _):
        await interaction.response.send_modal(MarketModal(self.gid, "add"))

    @discord.ui.button(label="삭제", style=discord.ButtonStyle.danger, row=1)
    async def delete(self, interaction, _):
        await interaction.response.send_modal(MarketModal(self.gid, "del"))

    @discord.ui.button(label="← 메인으로", style=discord.ButtonStyle.danger, row=4)
    async def back(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(embed=admin_main_embed(), view=AdminMainView(self.gid), content=None)

# ───────── 메인 뷰/임베드 ─────────
def admin_main_embed() -> discord.Embed:
    em = discord.Embed(title="관리자 메뉴", color=0x3498db)
    em.description = "원하는 카테고리를 선택하세요."
    return em

class AdminMainView(discord.ui.View):
    def __init__(self, gid: int):
        super().__init__(timeout=300)
        self.gid = gid

    @discord.ui.button(label="설정", style=discord.ButtonStyle.primary, row=0)
    async def to_settings(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            s = await get_settings(db, self.gid)
        await interaction.edit_original_response(embed=settings_embed(s), view=SettingsView(self.gid), content=None)

    @discord.ui.button(label="잔액", style=discord.ButtonStyle.secondary, row=0)
    async def to_balance(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            s = await get_settings(db, self.gid)
        await interaction.edit_original_response(embed=settings_embed(s), view=BalanceView(self.gid), content=None)

    @discord.ui.button(label="쿨타임", style=discord.ButtonStyle.secondary, row=0)
    async def to_cooldown(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(content="쿨타임 메뉴", embed=None, view=CooldownView(self.gid))

    @discord.ui.button(label="도구", style=discord.ButtonStyle.secondary, row=0)
    async def to_tools(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(content="도구 메뉴", embed=None, view=ToolsView(self.gid))

    @discord.ui.button(label="마켓 편집", style=discord.ButtonStyle.success, row=0)
    async def to_market(self, interaction, _):
        await interaction.response.defer(ephemeral=True)
        await ensure_seed_markets_admin(self.gid)
        await interaction.edit_original_response(content="마켓 편집", embed=None, view=MarketView(self.gid))

# ───────── 슬래시 명령 ─────────
@app_commands.command(name="mz_admin", description="Open admin menu (owner only)")
@owner_only()
async def mz_admin(interaction: discord.Interaction):
    gid = interaction.guild.id
    await ensure_seed_markets_admin(gid)
    await interaction.response.send_message(embed=admin_main_embed(), view=AdminMainView(gid), ephemeral=True)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_admin)
