import os, aiosqlite, json, time
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

DB_PATH = "economy.db"
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0") or "0")

async def try_send_log(client: discord.Client, embed: discord.Embed):
    if not LOG_CHANNEL_ID:
        return
    try:
        ch = client.get_channel(LOG_CHANNEL_ID) or await client.fetch_channel(LOG_CHANNEL_ID)
        await ch.send(embed=embed)
    except Exception:
        pass

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

# ───────── 쿨타임 초기화 ─────────
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

# ───────── 모달들 ─────────
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
        em = settings_embed(s)
        em.title = f"{self.key_label} 변경 완료"
        await interaction.response.send_message(embed=em, ephemeral=True)

        log = discord.Embed(
            title="설정 변경",
            color=0x8E44AD,
            timestamp=datetime.now(timezone.utc),
            description=f"**{self.key_label}** → `{self.value}`",
        )
        log.set_footer(text=f"by {interaction.user} · Guild {interaction.guild.id}")
        await try_send_log(interaction.client, log)

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
        member = interaction.guild.get_member(self.uid)
        name = member.display_name if member else str(self.uid)
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

        log = discord.Embed(
            title="관리자 잔액 조정",
            color=color,
            timestamp=datetime.now(timezone.utc),
            description=f"**{self.op} {amt:,}₩** → 대상 `{self.uid}`",
        )
        log.add_field(name="변화량", value=f"{sign}{delta:,}₩")
        log.add_field(name="현재 잔액", value=f"{new_bal:,}₩")
        if self.reason:
            log.add_field(name="사유", value=str(self.reason), inline=False)
        log.set_footer(text=f"by {interaction.user} · Guild {interaction.guild.id}")
        await try_send_log(interaction.client, log)

# ───────── Select: 대상 사용자 선택 (행 0 고정) ─────────
class TargetUserSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="대상 선택(미선택 = 전체)", min_values=0, max_values=1, row=0)
    async def callback(self, interaction: discord.Interaction):
        view: AdminMenu = self.view  # type: ignore
        view.target_user_id = self.values[0].id if self.values else None
        picked = interaction.guild.get_member(view.target_user_id).display_name if view.target_user_id else "서버 전체"
        await interaction.response.send_message(f"대상 선택: **{picked}**", ephemeral=True)

# ───────── 관리자 메뉴 View ─────────
class AdminMenu(discord.ui.View):
    def __init__(self, gid: int):
        super().__init__(timeout=300)
        self.gid = gid
        self.target_user_id: int | None = None
        self.add_item(TargetUserSelect())  # row=0

    # row=1: 설정
    @discord.ui.button(label="설정 보기", style=discord.ButtonStyle.primary, row=1)
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

    # row=2: 잔액 조정
    @discord.ui.button(label="잔액 설정", style=discord.ButtonStyle.success, row=2)
    async def bal_set(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.target_user_id is None:
            return await interaction.response.send_message("먼저 **대상 사용자**를 선택하세요.", ephemeral=True)
        m = interaction.guild.get_member(self.target_user_id)
        label = m.display_name if m else str(self.target_user_id)
        await interaction.response.send_modal(BalanceAmountModal(self.gid, self.target_user_id, "set", label))

    @discord.ui.button(label="잔액 증가", style=discord.ButtonStyle.success, row=2)
    async def bal_add(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.target_user_id is None:
            return await interaction.response.send_message("먼저 **대상 사용자**를 선택하세요.", ephemeral=True)
        m = interaction.guild.get_member(self.target_user_id)
        label = m.display_name if m else str(self.target_user_id)
        await interaction.response.send_modal(BalanceAmountModal(self.gid, self.target_user_id, "add", label))

    @discord.ui.button(label="잔액 감소", style=discord.ButtonStyle.danger, row=2)
    async def bal_sub(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.target_user_id is None:
            return await interaction.response.send_message("먼저 **대상 사용자**를 선택하세요.", ephemeral=True)
        m = interaction.guild.get_member(self.target_user_id)
        label = m.display_name if m else str(self.target_user_id)
        await interaction.response.send_modal(BalanceAmountModal(self.gid, self.target_user_id, "sub", label))

    # row=3: 쿨타임 초기화
    @discord.ui.button(label="쿨타임 초기화: 돈줘", style=discord.ButtonStyle.secondary, row=3)
    async def cd_money(self, interaction: discord.Interaction, button: discord.ui.Button):
        await reset_cooldown(self.gid, interaction.user.id, "money", self.target_user_id, None)
        scope = "서버 전체" if self.target_user_id is None else (
            interaction.guild.get_member(self.target_user_id).display_name or str(self.target_user_id)
        )
        await interaction.response.send_message(f"✅ **돈줘** 쿨타임 초기화 완료 · 대상: {scope}", ephemeral=True)
        log = discord.Embed(title="쿨타임 초기화", color=0x16A085, timestamp=datetime.now(timezone.utc),
                            description=f"[money] 대상: {scope}")
        await try_send_log(interaction.client, log)

    @discord.ui.button(label="쿨타임 초기화: 출첵", style=discord.ButtonStyle.secondary, row=3)
    async def cd_attend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await reset_cooldown(self.gid, interaction.user.id, "attend", self.target_user_id, None)
        scope = "서버 전체" if self.target_user_id is None else (
            interaction.guild.get_member(self.target_user_id).display_name or str(self.target_user_id)
        )
        await interaction.response.send_message(f"✅ **출첵** 쿨타임 초기화 완료 · 대상: {scope}", ephemeral=True)
        log = discord.Embed(title="쿨타임 초기화", color=0x16A085, timestamp=datetime.now(timezone.utc),
                            description=f"[attend] 대상: {scope}")
        await try_send_log(interaction.client, log)

    @discord.ui.button(label="쿨타임 초기화: 모두", style=discord.ButtonStyle.secondary, row=3)
    async def cd_both(self, interaction: discord.Interaction, button: discord.ui.Button):
        await reset_cooldown(self.gid, interaction.user.id, "both", self.target_user_id, None)
        scope = "서버 전체" if self.target_user_id is None else (
            interaction.guild.get_member(self.target_user_id).display_name or str(self.target_user_id)
        )
        await interaction.response.send_message(f"✅ **돈줘/출첵** 쿨타임 초기화 완료 · 대상: {scope}", ephemeral=True)
        log = discord.Embed(title="쿨타임 초기화", color=0x16A085, timestamp=datetime.now(timezone.utc),
                            description=f"[both] 대상: {scope}")
        await try_send_log(interaction.client, log)

    # row=4: 싱크/리로드/도구
    @discord.ui.button(label="명령 싱크(이 서버)", style=discord.ButtonStyle.primary, row=4)
    async def sync_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        synced = await interaction.client.tree.sync(guild=interaction.guild)
        names = ", ".join(f"/{c.name}" for c in synced)
        await interaction.followup.send(f"✅ 싱크 완료: {len(synced)}개\n{names or '(목록 없음)'}", ephemeral=True)
        log = discord.Embed(title="명령 싱크", color=0x2980B9, timestamp=datetime.now(timezone.utc),
                            description=f"Guild {interaction.guild.id} : {len(synced)}개")
        await try_send_log(interaction.client, log)

    @discord.ui.button(label="코그 로드·리로드", style=discord.ButtonStyle.secondary, row=4)
    async def reload_cogs(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def load_or_reload(ext: str) -> str:
            try:
                await interaction.client.reload_extension(ext)
                return f"reloaded {ext}"
            except commands.ExtensionNotLoaded:
                await interaction.client.load_extension(ext)
                return f"loaded {ext}"

        results = []
        for ext in ("cogs.economy", "cogs.fun"):
            try:
                results.append(await load_or_reload(ext))
            except Exception as e:
                results.append(f"❌ {ext}: {type(e).__name__}: {e}")

        msg = " / ".join(results)
        await interaction.followup.send(msg, ephemeral=True)

        log = discord.Embed(title="코그 로드·리로드", color=0x34495E, timestamp=datetime.now(timezone.utc),
                            description=msg)
        await try_send_log(interaction.client, log)

    @discord.ui.button(label="명령 목록 보기", style=discord.ButtonStyle.secondary, row=4)
    async def list_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        tree = interaction.client.tree
        globals_ = tree.get_commands()
        guilds_ = tree.get_commands(guild=interaction.guild)
        g_text = "\n".join(f"• /{c.name}" for c in guilds_) or "(없음)"
        G_text = "\n".join(f"• /{c.name}" for c in globals_) or "(없음)"
        em = discord.Embed(title="명령 목록", color=0x95A5A6)
        em.add_field(name="이 서버(Guild) 등록", value=g_text, inline=False)
        em.add_field(name="전역(Global) 등록", value=G_text, inline=False)
        await interaction.response.send_message(embed=em, ephemeral=True)

    @discord.ui.button(label="강제 초기화(전역 비움→리로드→싱크)", style=discord.ButtonStyle.danger, row=4)
    async def hard_reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)

        tree = interaction.client.tree
        # 1) 전역 커맨드 전부 제거
        tree.clear_commands(guild=None)
        await tree.sync()  # 전역에서 삭제 반영

        # 2) 코그 리로드로 커맨드 다시 등록
        for ext in ("cogs.economy", "cogs.fun"):
            try:
                await interaction.client.reload_extension(ext)
            except commands.ExtensionNotLoaded:
                await interaction.client.load_extension(ext)

        # 3) 현재 서버에 길드 싱크
        synced = await tree.sync(guild=interaction.guild)

        await interaction.followup.send(
            f"🧹 전역 비움 → 코그 리로드 → 길드 싱크 완료\n등록된 명령: {len(synced)}개",
            ephemeral=True,
        )
        log = discord.Embed(title="강제 초기화", color=0xE74C3C, timestamp=datetime.now(timezone.utc),
                            description=f"Guild {interaction.guild.id}: synced {len(synced)}")
        await try_send_log(interaction.client, log)

    @discord.ui.button(label="닫기", style=discord.ButtonStyle.danger, row=4)
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
    em.set_footer(text="원하는 항목을 선택하세요 (대상 미선택 = 서버 전체)")
    await interaction.response.send_message(embed=em, view=view, ephemeral=True)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_admin)
