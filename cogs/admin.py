import os, aiosqlite, json, time, re
import discord
from discord import app_commands

DB_PATH = "economy.db"

# ───────── 공통 유틸 ─────────
def owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        owner_id = int(os.getenv("OWNER_ID", "0"))
        return interaction.user.id == owner_id
    return app_commands.check(predicate)

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
    em = discord.Embed(title="서버 설정", color=0x3498db)
    em.add_field(name="최소 베팅", value=f"{s['min_bet']:,}₩")
    em.add_field(name="승률 하한", value=f"{s['win_min_bps']/100:.2f}%")
    em.add_field(name="승률 상한", value=f"{s['win_max_bps']/100:.2f}%")
    em.add_field(name="모드명", value=s["mode_name"], inline=False)
    return em

# ───────── 쿨타임 초기화 로직 ─────────
async def reset_cooldown(
    gid: int,
    actor_id: int,
    which: str,            # "money" | "attend" | "both"
    target_uid: int | None # None이면 길드 전체
):
    which = which.lower().strip()
    if which not in ("money", "attend", "both"):
        raise ValueError("which must be money/attend/both")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")

        # 영향받는 수 계산(로그 출력용)
        async def count_rows(col: str) -> int:
            if target_uid is None:
                cur = await db.execute(f"SELECT COUNT(*) FROM users WHERE guild_id=? AND {col} IS NOT NULL", (gid,))
            else:
                cur = await db.execute(f"SELECT COUNT(*) FROM users WHERE guild_id=? AND user_id=? AND {col} IS NOT NULL", (gid, target_uid))
            (c,) = await cur.fetchone()
            return int(c or 0)

        affected_money = affected_attend = 0
        if which in ("money", "both"):
            affected_money = await count_rows("last_claim_at")
            if target_uid is None:
                await db.execute("UPDATE users SET last_claim_at=NULL WHERE guild_id=?", (gid,))
            else:
                await db.execute("UPDATE users SET last_claim_at=NULL WHERE guild_id=? AND user_id=?", (gid, target_uid))

        if which in ("attend", "both"):
            affected_attend = await count_rows("last_daily_at")
            if target_uid is None:
                await db.execute("UPDATE users SET last_daily_at=NULL WHERE guild_id=?", (gid,))
            else:
                await db.execute("UPDATE users SET last_daily_at=NULL WHERE guild_id=? AND user_id=?", (gid, target_uid))

        # 감사 로그: 범위가 크면 개별 유저 대신 요약 로그 한 건
        meta = {"by": actor_id, "which": which, "scope": ("all" if target_uid is None else "user")}
        # user_id는 대상이 특정되면 해당 ID, 아니면 0으로 요약
        log_uid = (target_uid if target_uid is not None else 0)
        await db.execute(
            "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) VALUES(?,?,?,?,?,?,?)",
            (gid, log_uid, "admin_reset_cd", 0, 0, json.dumps(meta), int(time.time()))
        )

        await db.commit()

    return affected_money, affected_attend

# ───────── 모달들 ─────────
class ConfigValueModal(discord.ui.Modal, title="설정 값 입력"):
    value = discord.ui.TextInput(label="값", placeholder="예) 2000 / 35(%) / 이벤트 모드", required=True)

    def __init__(self, key: str, key_label: str, gid: int):
        super().__init__(timeout=180)
        self.key = key
        self.key_label = key_label
        self.gid = gid
        self.title = f"{key_label} 변경"

    async def on_submit(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB_PATH) as db:
            await set_setting_field(db, self.gid, self.key, str(self.value))
            s = await get_settings(db, self.gid)
        em = settings_embed(s)
        em.title = f"{self.key_label} 변경 완료"
        await interaction.response.send_message(embed=em, ephemeral=True)

class BalanceModal(discord.ui.Modal, title="잔액 수정"):
    target = discord.ui.TextInput(label="대상(사용자 ID 또는 @멘션)", placeholder="@닉네임 또는 123456789012345678", required=True)
    op     = discord.ui.TextInput(label="동작(set/add/sub)", default="set", required=True, max_length=3)
    amount = discord.ui.TextInput(label="금액(정수)", placeholder="예) 10000", required=True)
    reason = discord.ui.TextInput(label="사유(선택)", style=discord.TextStyle.paragraph, required=False)

    def __init__(self, gid: int):
        super().__init__(timeout=180)
        self.gid = gid

    async def on_submit(self, interaction: discord.Interaction):
        gid = self.gid
        m = re.search(r"\d{5,20}", str(self.target))
        if not m:
            return await interaction.response.send_message("대상을 인식하지 못했습니다. @멘션 또는 숫자 ID를 입력하세요.", ephemeral=True)
        uid = int(m.group(0))
        op = str(self.op).strip().lower()
        if op not in ("set", "add", "sub"):
            return await interaction.response.send_message("동작은 set/add/sub 중 하나여야 합니다.", ephemeral=True)
        try:
            amt = int(str(self.amount).replace(",", "").strip())
        except ValueError:
            return await interaction.response.send_message("금액은 정수여야 합니다.", ephemeral=True)

        old_bal, new_bal, delta = await apply_balance_change(gid, uid, op, amt, interaction.user.id, str(self.reason) or None)
        color = 0x2ecc71 if delta >= 0 else 0xe74c3c
        sign = "+" if delta >= 0 else ""
        user_name = interaction.guild.get_member(uid).display_name if interaction.guild.get_member(uid) else f"{uid}"
        em = discord.Embed(title="잔액 변경 완료", color=color)
        em.add_field(name="대상", value=f"{user_name} (`{uid}`)")
        em.add_field(name="동작", value=op)
        em.add_field(name="이전 잔액", value=f"{old_bal:,}₩")
        em.add_field(name="변화량", value=f"{sign}{delta:,}₩")
        em.add_field(name="현재 잔액", value=f"{new_bal:,}₩", inline=False)
        if self.reason:
            em.add_field(name="사유", value=str(self.reason), inline=False)
        em.set_footer(text=f"실행: {interaction.user.display_name}")
        await interaction.response.send_message(embed=em, ephemeral=True)

class CooldownResetModal(discord.ui.Modal, title="쿨타임 초기화"):
    target = discord.ui.TextInput(
        label="대상(사용자 ID/@멘션 또는 all)",
        placeholder="@닉네임 또는 123456789012345678 또는 all",
        required=True
    )
    which  = discord.ui.TextInput(
        label="항목(money/attend/both)",
        default="both",
        required=True,
        max_length=6
    )
    reason = discord.ui.TextInput(
        label="사유(선택)",
        style=discord.TextStyle.paragraph,
        required=False
    )

    def __init__(self, gid: int):
        super().__init__(timeout=180)
        self.gid = gid

    async def on_submit(self, interaction: discord.Interaction):
        gid = self.gid
        raw_target = str(self.target).strip().lower()
        if raw_target in ("all", "everyone", "@everyone"):
            target_uid = None
        else:
            m = re.search(r"\d{5,20}", str(self.target))
            if not m:
                return await interaction.response.send_message("대상을 인식하지 못했습니다. @멘션 또는 숫자 ID, 혹은 all 을 입력하세요.", ephemeral=True)
            target_uid = int(m.group(0))

        which = str(self.which).strip().lower()
        if which not in ("money", "attend", "both"):
            return await interaction.response.send_message("항목은 money/attend/both 중 하나여야 합니다.", ephemeral=True)

        affected_money, affected_attend = await reset_cooldown(
            gid=gid,
            actor_id=interaction.user.id,
            which=which,
            target_uid=target_uid
        )

        scope_txt = "서버 전체" if target_uid is None else (interaction.guild.get_member(target_uid).display_name if interaction.guild.get_member(target_uid) else f"{target_uid}")
        em = discord.Embed(title="쿨타임 초기화 완료", color=0x2ecc71)
        em.add_field(name="대상", value=scope_txt, inline=False)
        em.add_field(name="항목", value=which, inline=True)
        em.add_field(name="초기화 수", value=f"money: {affected_money} · attend: {affected_attend}", inline=True)
        if self.reason:
            em.add_field(name="사유", value=str(self.reason), inline=False)
        em.set_footer(text=f"실행: {interaction.user.display_name}")
        await interaction.response.send_message(embed=em, ephemeral=True)

# ───────── 관리자 메뉴 View ─────────
class AdminMenu(discord.ui.View):
    def __init__(self, gid: int):
        super().__init__(timeout=300)
        self.gid = gid

    @discord.ui.button(label="설정 보기", style=discord.ButtonStyle.primary, row=0)
    async def view_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with aiosqlite.connect(DB_PATH) as db:
            s = await get_settings(db, self.gid)
        await interaction.response.edit_message(embed=settings_embed(s), view=self)

    @discord.ui.button(label="최소베팅 수정", style=discord.ButtonStyle.secondary, row=1)
    async def edit_min_bet(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ConfigValueModal("min_bet", "최소베팅", self.gid))

    @discord.ui.button(label="승률하한(%) 수정", style=discord.ButtonStyle.secondary, row=1)
    async def edit_win_min(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ConfigValueModal("win_min_bps", "승률 하한(%)", self.gid))

    @discord.ui.button(label="승률상한(%) 수정", style=discord.ButtonStyle.secondary, row=1)
    async def edit_win_max(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ConfigValueModal("win_max_bps", "승률 상한(%)", self.gid))

    @discord.ui.button(label="모드명 수정", style=discord.ButtonStyle.secondary, row=1)
    async def edit_mode_name(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ConfigValueModal("mode_name", "모드명", self.gid))

    @discord.ui.button(label="잔액 수정", style=discord.ButtonStyle.success, row=2)
    async def balance_edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BalanceModal(self.gid))

    @discord.ui.button(label="쿨타임 초기화", style=discord.ButtonStyle.success, row=2)
    async def cooldown_reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CooldownResetModal(self.gid))

    @discord.ui.button(label="닫기", style=discord.ButtonStyle.danger, row=2)
    async def close_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="메뉴를 닫았습니다.", view=self)

# ───────── 통합 관리자 슬래시 ─────────
@app_commands.command(name="mz_admin", description="Open admin menu (owner only)")
@owner_only()
async def mz_admin(interaction: discord.Interaction):
    gid = interaction.guild.id
    view = AdminMenu(gid)
    async with aiosqlite.connect(DB_PATH) as db:
        s = await get_settings(db, gid)
    em = settings_embed(s)
    em.title = "관리자 메뉴"
    em.set_footer(text="원하는 항목을 선택하세요")
    await interaction.response.send_message(embed=em, view=view, ephemeral=True)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_admin)
