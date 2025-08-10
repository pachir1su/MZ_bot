# cogs/admin.py
import os, aiosqlite, json, time, math
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
            num = int(round(float(raw) * 100))  # 66.5% -> 6650 bps
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
    em = discord.Embed(title="도박 메뉴 — 현재 설정", color=0x3498db)
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
        "• **권한 확인**: 서버/채널 권한에서 **애플리케이션 명령어 사용**이 허용되어 있는지 확인하세요."
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

# ───────── 서브 뷰: 도박 설정 ─────────
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
        await interaction.edit_original_response(
            embed=admin_main_embed(), view=AdminMainView(self.gid), content=None
        )

# ═══════════════════════════════════════════════════════════════════════════
#                               MarketView v2
#   - 탭(주식/코인) · 검색 · 페이지네이션 · 다중선택
#   - 단건/일괄: 편집/삭제/활성토글/복제/프리셋
#   - 저장 전 미리보기(확인) · 30초 Undo
# ═══════════════════════════════════════════════════════════════════════════

PAGE_SIZE = 25  # Discord StringSelect 옵션 최대치
UNDO_TTL = 30.0 # 초

# 간단 Undo: SQL 역연산들 저장
class UndoView(discord.ui.View):
    def __init__(self, sql_ops: list[tuple[str, tuple]], title: str):
        super().__init__(timeout=UNDO_TTL)
        self.sql_ops = sql_ops
        self.title = title

    @discord.ui.button(label="실행 취소", style=discord.ButtonStyle.secondary)
    async def do_undo(self, interaction: discord.Interaction, _):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("BEGIN IMMEDIATE")
                for sql, args in reversed(self.sql_ops):
                    await db.execute(sql, args)
                await db.commit()
            await interaction.response.edit_message(content=f"↩️ Undo 완료: {self.title}", view=None)
        except Exception as e:
            await interaction.response.edit_message(content=f"Undo 중 오류: {type(e).__name__}: {e}", view=None)

# 공통 쿼리
async def count_items(gid: int, typ: str, keyword: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        like = f"%{keyword.strip()}%" if keyword else "%"
        cur = await db.execute(
            "SELECT COUNT(*) FROM market_items WHERE guild_id=? AND type=? AND name LIKE ?",
            (gid, typ, like)
        )
        return (await cur.fetchone())[0]

async def list_items(gid: int, typ: str, keyword: str, page: int, limit: int):
    async with aiosqlite.connect(DB_PATH) as db:
        like = f"%{keyword.strip()}%" if keyword else "%"
        offset = page * limit
        cur = await db.execute(
            "SELECT name,range_lo,range_hi,enabled FROM market_items "
            "WHERE guild_id=? AND type=? AND name LIKE ? "
            "ORDER BY name LIMIT ? OFFSET ?",
            (gid, typ, like, limit, offset)
        )
        rows = await cur.fetchall()
        return [{"name": n, "lo": lo, "hi": hi, "en": en} for (n,lo,hi,en) in rows]

# 프리셋 조정 함수들
def normalize_percent(txt: str) -> float:
    s = txt.strip().replace("%","")
    return float(s)

def preset_apply(lo: float, hi: float, kind: str) -> tuple[float,float]:
    width = hi - lo
    if kind == "widen10":
        lo2 = lo - width*0.1; hi2 = hi + width*0.1
    elif kind == "narrow10":
        lo2 = lo + width*0.1; hi2 = hi - width*0.1
        if lo2 >= hi2: lo2, hi2 = lo, hi
    elif kind == "center0":
        lo2 = -width/2; hi2 = width/2
    elif kind == "tilt_pos":
        shift = width*0.1; lo2 = lo + shift; hi2 = hi + shift
    elif kind == "tilt_neg":
        shift = width*0.1; lo2 = lo - shift; hi2 = hi - shift
    else:
        lo2, hi2 = lo, hi
    return (round(lo2,1), round(hi2,1))

def ev_of(lo: float, hi: float) -> float:
    return round((lo + hi)/2.0, 3)

# 편집 미리보기 → 확인 단계
class EditConfirmView(discord.ui.View):
    def __init__(self, gid: int, typ: str, name: str, lo: float, hi: float, en: int):
        super().__init__(timeout=120)
        self.gid, self.typ, self.name, self.lo, self.hi, self.en = gid, typ, name, lo, hi, en

    @discord.ui.button(label="저장", style=discord.ButtonStyle.success, row=0)
    async def save(self, interaction: discord.Interaction, _):
        sql_ops: list[tuple[str, tuple]] = []
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT name,range_lo,range_hi,enabled FROM market_items "
                "WHERE guild_id=? AND type=? AND name=?",
                (self.gid, self.typ, self.name)
            )
            old = await cur.fetchone()
            if old:
                sql_ops.append((
                    "UPDATE market_items SET range_lo=?, range_hi=?, enabled=? "
                    "WHERE guild_id=? AND type=? AND name=?",
                    (old[1], old[2], old[3], self.gid, self.typ, self.name)
                ))
                await db.execute(
                    "UPDATE market_items SET range_lo=?, range_hi=?, enabled=? "
                    "WHERE guild_id=? AND type=? AND name=?",
                    (self.lo, self.hi, self.en, self.gid, self.typ, self.name)
                )
            else:
                sql_ops.append((
                    "DELETE FROM market_items WHERE guild_id=? AND type=? AND name=?",
                    (self.gid, self.typ, self.name)
                ))
                await db.execute(
                    "INSERT INTO market_items(guild_id,type,name,range_lo,range_hi,enabled) VALUES(?,?,?,?,?,?)",
                    (self.gid, self.typ, self.name, self.lo, self.hi, self.en)
                )
            await db.commit()

        em = discord.Embed(title="마켓 저장 완료", color=0x2ecc71)
        em.add_field(name="종류", value=self.typ)
        em.add_field(name="이름", value=self.name)
        em.add_field(name="범위", value=f"{self.lo:+.1f}% ~ {self.hi:+.1f}%")
        em.add_field(name="EV(추정)", value=f"{ev_of(self.lo,self.hi):+.3f}%")
        em.add_field(name="상태", value="on" if self.en else "off", inline=True)
        await interaction.response.edit_message(embed=em, view=UndoView(sql_ops, f"{self.name} 편집"))

class MarketEditModal(discord.ui.Modal, title="마켓 편집/추가"):
    t_type  = discord.ui.TextInput(label="종류(stock/coin)", placeholder="stock 또는 coin", required=True)
    t_name  = discord.ui.TextInput(label="이름", placeholder="예: 성현전자", required=True)
    t_lo    = discord.ui.TextInput(label="최소 변화율(예: -10 / -10.0 / -10%)", required=True)
    t_hi    = discord.ui.TextInput(label="최대 변화율(예: 10 / 10.0 / 10%)", required=True)
    t_en    = discord.ui.TextInput(label="활성화(1/0)", default="1", required=True)

    def __init__(self, gid: int, preset: dict | None = None):
        super().__init__(timeout=180)
        self.gid = gid
        if preset:
            self.t_type.default = preset.get("type","")
            self.t_name.default = preset.get("name","")
            self.t_lo.default = str(preset.get("lo",""))
            self.t_hi.default = str(preset.get("hi",""))
            self.t_en.default = "1" if preset.get("en",1) else "0"

    async def on_submit(self, interaction: discord.Interaction):
        typ = str(self.t_type).strip().lower()
        name = str(self.t_name).strip()
        try:
            lo = normalize_percent(str(self.t_lo)); hi = normalize_percent(str(self.t_hi))
            if lo >= hi: raise ValueError("lo<hi 위반")
            if abs(lo) > 1000 or abs(hi) > 1000: raise ValueError("범위 초과")
            en = 1 if str(self.t_en).strip() == "1" else 0
        except Exception as e:
            return await interaction.response.send_message(f"입력 검증 실패: {type(e).__name__}: {e}", ephemeral=True)

        em = discord.Embed(title="저장 전 미리보기", color=0x95a5a6)
        em.add_field(name="종류", value=typ)
        em.add_field(name="이름", value=name)
        em.add_field(name="범위", value=f"{lo:+.1f}% ~ {hi:+.1f}%")
        em.add_field(name="EV(추정)", value=f"{ev_of(lo,hi):+.3f}%")
        em.set_footer(text="확인을 누르면 저장됩니다.")
        await interaction.response.send_message(embed=em, ephemeral=True, view=EditConfirmView(self.gid, typ, name, lo, hi, en))

# 검색 입력
class QueryModal(discord.ui.Modal, title="검색어 입력"):
    q = discord.ui.TextInput(label="키워드", placeholder="부분 일치, 공백이면 전체", required=False)
    def __init__(self, view: "MarketViewV2"):
        super().__init__(timeout=120)
        self.mv = view
    async def on_submit(self, interaction: discord.Interaction):
        self.mv.keyword = str(self.q).strip()
        self.mv.page = 0
        await self.mv.refresh(interaction)

# 선택 컴포넌트
class MarketSelect(discord.ui.Select):
    def __init__(self, options: list[discord.SelectOption], multi: bool):
        super().__init__(
            placeholder="항목을 선택하세요",
            options=options,
            min_values=0,
            max_values=(len(options) if multi else 1),
            row=0,  # row=0 고정
        )
    async def callback(self, interaction: discord.Interaction):
        view: "MarketViewV2" = self.view  # type: ignore
        view.selected = list(self.values)
        await interaction.response.send_message(
            f"선택: {', '.join(view.selected) if view.selected else '없음'}",
            ephemeral=True
        )

# 프리셋 단일 뷰
class PresetQuickView(discord.ui.View):
    def __init__(self, mv: "MarketViewV2", names: list[str]):
        super().__init__(timeout=60)
        self.mv = mv
        self.names = names

    async def _apply(self, interaction: discord.Interaction, kind: str):
        sql_ops: list[tuple[str, tuple]] = []
        count = 0
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            for name in self.names:
                cur = await db.execute(
                    "SELECT range_lo,range_hi FROM market_items WHERE guild_id=? AND type=? AND name=?",
                    (self.mv.gid, self.mv.tab, name)
                )
                row = await cur.fetchone()
                if not row: continue
                lo2, hi2 = preset_apply(row[0], row[1], kind)
                sql_ops.append((
                    "UPDATE market_items SET range_lo=?, range_hi=? WHERE guild_id=? AND type=? AND name=?",
                    (row[0], row[1], self.mv.gid, self.mv.tab, name)
                ))
                await db.execute(
                    "UPDATE market_items SET range_lo=?, range_hi=? WHERE guild_id=? AND type=? AND name=?",
                    (lo2, hi2, self.mv.gid, self.mv.tab, name)
                )
                count += 1
            await db.commit()
        await interaction.response.edit_message(content=f"프리셋 적용({kind}) 완료: {count}개", view=UndoView(sql_ops, f"{self.mv.tab} 프리셋"))

    @discord.ui.button(label="폭 +10%", style=discord.ButtonStyle.secondary, row=0)
    async def widen(self, i, _): await self._apply(i, "widen10")

    @discord.ui.button(label="폭 -10%", style=discord.ButtonStyle.secondary, row=0)
    async def narrow(self, i, _): await self._apply(i, "narrow10")

    @discord.ui.button(label="대칭화(중심=0)", style=discord.ButtonStyle.secondary, row=0)
    async def center(self, i, _): await self._apply(i, "center0")

    @discord.ui.button(label="닫기", style=discord.ButtonStyle.danger, row=1)
    async def close(self, interaction, _): await interaction.response.edit_message(content="프리셋 취소", view=None)

# MarketView v2
class MarketViewV2(discord.ui.View):
    def __init__(self, gid: int):
        super().__init__(timeout=420)
        self.gid = gid
        self.tab = "stock"      # 'stock' | 'coin'
        self.keyword = ""       # 검색어
        self.page = 0           # 페이지
        self.selected: list[str] = []  # 현재 페이지에서 선택된 name
        # ▼ 직전 Select 추적(중복 추가 방지)
        self.select_item: MarketSelect | None = None

    # 내부: 리스트 갱신
    async def refresh(self, interaction: discord.Interaction):
        total = await count_items(self.gid, self.tab, self.keyword)
        pages = max(1, math.ceil(total / PAGE_SIZE))
        if self.page >= pages: self.page = pages - 1

        rows = await list_items(self.gid, self.tab, self.keyword, self.page, PAGE_SIZE)
        self.selected = []

        options: list[discord.SelectOption] = []
        for r in rows:
            label = f"{r['name']}"
            desc = f"{r['lo']:+.1f}% ~ {r['hi']:+.1f}% ({'on' if r['en'] else 'off'})"
            options.append(discord.SelectOption(label=label, value=r['name'], description=desc))

        # ▼ 기존 Select가 있으면 정확히 제거
        if self.select_item is not None:
            try:
                self.remove_item(self.select_item)
            except Exception:
                pass
            self.select_item = None

        # ▼ 새 Select 추가(항상 row=0)
        select = MarketSelect(options, multi=True)
        self.add_item(select)
        self.select_item = select

        # 상단 임베드
        em = discord.Embed(title="마켓 항목", color=0x95a5a6)
        em.description = f"탭: **{('주식' if self.tab=='stock' else '코인')}** · 검색어: **{self.keyword or '없음'}**\n" \
                         f"페이지: **{self.page+1}/{pages}** · 항목 수: **{total}**"
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=em, view=self)
        else:
            await interaction.response.edit_message(embed=em, view=self)

    # ── 컨트롤(탭/검색/페이지) — row=1 ──
    @discord.ui.button(label="주식", style=discord.ButtonStyle.primary, row=1)
    async def tab_stock(self, interaction, _):
        self.tab = "stock"; self.page = 0
        await self.refresh(interaction)

    @discord.ui.button(label="코인", style=discord.ButtonStyle.success, row=1)
    async def tab_coin(self, interaction, _):
        self.tab = "coin"; self.page = 0
        await self.refresh(interaction)

    @discord.ui.button(label="검색", style=discord.ButtonStyle.secondary, row=1)
    async def do_search(self, interaction, _):
        await interaction.response.send_modal(QueryModal(self))

    @discord.ui.button(label="« 이전", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(self, interaction, _):
        if self.page > 0: self.page -= 1
        await self.refresh(interaction)

    @discord.ui.button(label="다음 »", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction, _):
        self.page += 1
        await self.refresh(interaction)

    # ── 단건/일괄 액션 — row=2 ──
    async def _require_selection(self, interaction: discord.Interaction, single=False) -> list[str] | None:
        if not self.selected:
            await interaction.response.send_message("먼저 목록에서 항목을 선택하세요.", ephemeral=True)
            return None
        if single and len(self.selected) != 1:
            await interaction.response.send_message("이 동작은 **하나의 항목만** 선택해야 합니다.", ephemeral=True)
            return None
        return self.selected

    @discord.ui.button(label="편집", style=discord.ButtonStyle.primary, row=2)
    async def edit_item(self, interaction, _):
        sel = await self._require_selection(interaction, single=True); 
        if not sel: return
        name = sel[0]
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT range_lo,range_hi,enabled FROM market_items WHERE guild_id=? AND type=? AND name=?",
                                   (self.gid, self.tab, name))
            row = await cur.fetchone()
        preset = {"type": self.tab, "name": name, "lo": row[0] if row else "", "hi": row[1] if row else "", "en": (row[2] if row else 1)}
        await interaction.response.send_modal(MarketEditModal(self.gid, preset=preset))

    @discord.ui.button(label="활성 토글(일괄)", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_enable(self, interaction, _):
        sel = await self._require_selection(interaction); 
        if not sel: return
        sql_ops: list[tuple[str, tuple]] = []
        changed = 0
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            for name in sel:
                cur = await db.execute("SELECT enabled FROM market_items WHERE guild_id=? AND type=? AND name=?",
                                       (self.gid, self.tab, name))
                row = await cur.fetchone()
                if not row: continue
                old_en = row[0]; new_en = 0 if old_en else 1
                sql_ops.append(("UPDATE market_items SET enabled=? WHERE guild_id=? AND type=? AND name=?",
                                (old_en, self.gid, self.tab, name)))
                await db.execute("UPDATE market_items SET enabled=? WHERE guild_id=? AND type=? AND name=?",
                                 (new_en, self.gid, self.tab, name))
                changed += 1
            await db.commit()
        await interaction.response.edit_message(content=f"활성 토글 완료: {changed}개", view=UndoView(sql_ops, "활성 토글"))

    @discord.ui.button(label="삭제(일괄)", style=discord.ButtonStyle.danger, row=2)
    async def delete_items(self, interaction, _):
        sel = await self._require_selection(interaction); 
        if not sel: return
        sql_ops: list[tuple[str, tuple]] = []
        deleted = 0
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            for name in sel:
                cur = await db.execute("SELECT name,range_lo,range_hi,enabled FROM market_items WHERE guild_id=? AND type=? AND name=?",
                                       (self.gid, self.tab, name))
                row = await cur.fetchone()
                if not row: continue
                sql_ops.append(("INSERT INTO market_items(guild_id,type,name,range_lo,range_hi,enabled) VALUES(?,?,?,?,?,?)",
                                (self.gid, self.tab, row[0], row[1], row[2], row[3])))
                await db.execute("DELETE FROM market_items WHERE guild_id=? AND type=? AND name=?",
                                 (self.gid, self.tab, name))
                deleted += 1
            await db.commit()
        await interaction.response.edit_message(content=f"삭제 완료: {deleted}개", view=UndoView(sql_ops, "삭제"))

    @discord.ui.button(label="복제", style=discord.ButtonStyle.success, row=2)
    async def duplicate(self, interaction, _):
        sel = await self._require_selection(interaction, single=True); 
        if not sel: return
        name = sel[0]
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT range_lo,range_hi,enabled FROM market_items WHERE guild_id=? AND type=? AND name=?",
                                   (self.gid, self.tab, name))
            row = await cur.fetchone()
            if not row:
                return await interaction.response.send_message("원본을 찾을 수 없습니다.", ephemeral=True)
            base = f"{name} 복사본"
            new_name = base
            idx = 2
            while True:
                cur = await db.execute("SELECT 1 FROM market_items WHERE guild_id=? AND type=? AND name=?",
                                       (self.gid, self.tab, new_name))
                if not await cur.fetchone(): break
                new_name = f"{base} {idx}"; idx += 1

            await db.execute(
                "INSERT INTO market_items(guild_id,type,name,range_lo,range_hi,enabled) VALUES(?,?,?,?,?,?)",
                (self.gid, self.tab, new_name, row[0], row[1], row[2])
            )
            await db.commit()
        undo = UndoView([("DELETE FROM market_items WHERE guild_id=? AND type=? AND name=?",
                          (self.gid, self.tab, new_name))], "복제")
        await interaction.response.edit_message(content=f"복제 완료 → **{new_name}**", view=undo)

    @discord.ui.button(label="프리셋", style=discord.ButtonStyle.secondary, row=2)
    async def open_preset(self, interaction, _):
        sel = await self._require_selection(interaction); 
        if not sel: return
        await interaction.response.edit_message(content=f"프리셋 적용 대상: {len(sel)}개", view=PresetQuickView(self, sel))

    # ── 그 외 컨트롤 — row=3 ──
    @discord.ui.button(label="새 항목 추가", style=discord.ButtonStyle.success, row=3)
    async def add_new(self, interaction, _):
        await interaction.response.send_modal(MarketEditModal(self.gid, preset={"type": self.tab}))

    @discord.ui.button(label="새로 고침", style=discord.ButtonStyle.secondary, row=3)
    async def refresh_btn(self, interaction, _):
        await self.refresh(interaction)

    @discord.ui.button(label="← 메인으로", style=discord.ButtonStyle.danger, row=3)
    async def back(self, interaction, _):
        await interaction.response.edit_message(embed=admin_main_embed(), view=AdminMainView(self.gid), content=None)

# ───────── 메인 뷰/임베드 ─────────
def admin_main_embed() -> discord.Embed:
    em = discord.Embed(title="관리자 메뉴", color=0x3498db)
    em.description = "원하는 카테고리를 선택하세요."
    return em

class AdminMainView(discord.ui.View):
    def __init__(self, gid: int):
        super().__init__(timeout=300)
        self.gid = gid

    @discord.ui.button(label="도박 메뉴", style=discord.ButtonStyle.primary, row=0)
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
        mv = MarketViewV2(self.gid)
        await interaction.edit_original_response(content=None, embed=discord.Embed(title="마켓 항목", color=0x95a5a6), view=mv)
        await mv.refresh(interaction)

# ───────── 슬래시 명령 ─────────
@app_commands.command(name="mz_admin", description="Open admin menu (owner only)")
@owner_only()
async def mz_admin(interaction: discord.Interaction):
    gid = interaction.guild.id
    await ensure_seed_markets_admin(gid)
    await interaction.response.send_message(embed=admin_main_embed(), view=AdminMainView(gid), ephemeral=True)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_admin)
