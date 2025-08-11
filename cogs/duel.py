import aiosqlite, asyncio, secrets, time
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta
from typing import Optional

DB_PATH = "economy.db"

# ===== 시간/표시 유틸 =====
KST = timezone(timedelta(hours=9))
def now_kst() -> datetime: return datetime.now(KST)
def won(n: int) -> str: return f"{n:,}₩"

# ===== 공용 DB 유틸 =====
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
    await ensure_weapon_row(db, gid, uid)
    cur = await db.execute("SELECT level FROM user_weapons WHERE guild_id=? AND user_id=?", (gid, uid))
    row = await cur.fetchone()
    return row[0] if row else 0

async def get_min_bet(gid: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT min_bet FROM guild_settings WHERE guild_id=?", (gid,))
        row = await cur.fetchone()
        return row[0] if row and row[0] else 1000

# ===== 승률 모델(로지스틱) =====
import math
def duel_win_prob(att_lv: int, def_lv: int) -> float:
    """
    무기 레벨 영향 대폭 강화: 로지스틱 p = 1/(1+e^{-k*Δ})
    - k = 0.20 (Δ=5 → ~0.73, Δ=10 → ~0.88)
    - 최종 p는 [0.02, 0.985]로 클램프(운 요소 유지)
    """
    k = 0.20
    delta = att_lv - def_lv
    p = 1.0 / (1.0 + math.exp(-k * delta))
    return max(0.02, min(0.985, p))

# ===== 안전한 멤버/유저 해석 =====
class _UserStub:
    def __init__(self, uid: int, display_name: str):
        self.id = uid
        self.mention = f"<@{uid}>"
        self.display_name = display_name

async def resolve_userish(guild: discord.Guild, bot: discord.Client, uid: int):
    m = guild.get_member(uid)
    if m:
        return m
    try:
        u = await bot.fetch_user(uid)
        dn = getattr(u, "global_name", None) or u.name or f"유저 {uid}"
        return _UserStub(uid, dn)
    except Exception:
        return _UserStub(uid, f"유저 {uid}")

def member_label_cached(guild: discord.Guild, uid: int) -> str:
    m = guild.get_member(uid)
    return m.display_name if m else f"유저 {uid}"

# ===== 임베드/프로그레스 =====
def progress_bar(p: float, width: int = 12) -> str:
    p = max(0.0, min(1.0, p))
    filled = int(round(p * width))
    return "▰" * filled + "▱" * (width - filled)

def challenge_embed(challenger: discord.Member, opponent: discord.Member,
                    stake: int, lv_a: int, lv_b: int, p_a: float):
    em = discord.Embed(title="맞짱 요청", color=0x9b59b6,
                       description="상대가 **맞짱**을 요청했습니다. 1분 이내에 수락/거절을 선택하세요.")
    try:
        em.set_author(name=challenger.display_name, icon_url=challenger.display_avatar.url)
    except Exception:
        em.set_author(name=challenger.display_name)
    em.add_field(name="도전자", value=f"{challenger.mention} · +{lv_a}", inline=True)
    em.add_field(name="상대",   value=f"{opponent.mention} · +{lv_b}", inline=True)
    em.add_field(name="베팅",   value=won(stake), inline=False)
    em.add_field(name="도전자 승률(예상)", value=f"{p_a*100:.1f}%", inline=True)
    em.add_field(name="상대 승률(예상)",   value=f"{(1-p_a)*100:.1f}%", inline=True)
    return em

def fight_embed(name_a: str, name_b: str, lv_a: int, lv_b: int, pct: int):
    em = discord.Embed(title="대결 중…", color=0xf1c40f)
    em.add_field(name="도전자", value=f"{name_a} · +{lv_a}", inline=True)
    em.add_field(name="상대",   value=f"{name_b} · +{lv_b}", inline=True)
    em.add_field(name="진행",   value=f"{progress_bar(pct/100)} **{pct}%**", inline=False)
    return em

def result_embed(winner, loser, stake: int, bal_w: int, bal_l: int, lv_w: int, lv_l: int):
    em = discord.Embed(title="맞짱 결과", color=0x2ecc71)
    em.add_field(name="승자", value=f"{winner.mention} · +{lv_w}", inline=True)
    em.add_field(name="패자", value=f"{loser.mention} · +{lv_l}", inline=True)
    em.add_field(name="정산", value=f"{winner.display_name} **+{won(stake)}** / {loser.display_name} **-{won(stake)}**", inline=False)
    em.add_field(name="현재 잔액(승/패)", value=f"{won(bal_w)} / {won(bal_l)}", inline=False)
    return em

# ===== View =====
class AutoCancelView(discord.ui.View):
    def __init__(self, timeout_seconds: int = 60):
        super().__init__(timeout=timeout_seconds)
        self.message: Optional[discord.Message] = None
        self._timeout_seconds = timeout_seconds
        self.finalized = False

    async def on_timeout(self):
        if self.finalized or not self.message: return
        try:
            for c in self.children:
                if hasattr(c, "disabled"): c.disabled = True
            em = discord.Embed(title="취소되었습니다", description=f"{self._timeout_seconds}초가 지나 자동으로 취소되었습니다.", color=0xE74C3C)
            await self.message.edit(embed=em, view=None)
        except Exception:
            pass

class DuelChallengeView(AutoCancelView):
    def __init__(self, gid: int, challenger_id: int, opponent_id: int, stake: int,
                 lv_a: int, lv_b: int, prob_a: float):
        super().__init__(timeout_seconds=60)
        self.gid = gid; self.challenger_id = challenger_id; self.opponent_id = opponent_id
        self.stake = stake; self.lv_a = lv_a; self.lv_b = lv_b; self.prob_a = prob_a
        self.busy = False

    def _only_opponent(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.opponent_id

    @discord.ui.button(label="수락", style=discord.ButtonStyle.success, row=0)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._only_opponent(interaction):
            return await interaction.response.send_message("상대만 수락할 수 있습니다.", ephemeral=True)
        if self.busy:
            return await interaction.response.send_message("이미 처리 중입니다.", ephemeral=True)
        self.busy = True
        await interaction.response.defer()

        gid = self.gid
        uid_a = self.challenger_id
        uid_b = self.opponent_id
        stake = self.stake

        new_bal_w = new_bal_l = 0

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            ua = await get_user(db, gid, uid_a)
            ub = await get_user(db, gid, uid_b)
            bal_a, bal_b = ua["balance"], ub["balance"]
            if bal_a < stake or bal_b < stake:
                await db.execute("ROLLBACK")
                for c in self.children: c.disabled = True
                em = discord.Embed(title="맞짱 취소", description="한쪽 잔액이 부족해 대결이 취소되었습니다.", color=0xE67E22)
                if self.message: await self.message.edit(embed=em, view=None)
                else: await interaction.edit_original_response(embed=em, view=None)
                self.finalized = True; self.stop(); return

            lv_a = await get_level(db, gid, uid_a)
            lv_b = await get_level(db, gid, uid_b)
            p_a = duel_win_prob(lv_a, lv_b)
            roll = secrets.randbelow(10_000) / 10_000.0
            a_wins = (roll < p_a)
            uid_w, uid_l = (uid_a, uid_b) if a_wins else (uid_b, uid_a)
            lv_w, lv_l   = (lv_a, lv_b) if a_wins else (lv_b, lv_a)

            # 애니메이션
            name_a = member_label_cached(interaction.guild, uid_a)
            name_b = member_label_cached(interaction.guild, uid_b)
            if "유저 " in name_a or "유저 " in name_b:
                ua_name = await resolve_userish(interaction.guild, interaction.client, uid_a)
                ub_name = await resolve_userish(interaction.guild, interaction.client, uid_b)
                name_a = ua_name.display_name; name_b = ub_name.display_name
            for t in range(0, 101, 20):
                em = fight_embed(name_a, name_b, lv_a, lv_b, t)
                if self.message: await self.message.edit(embed=em, view=None)
                else: await interaction.edit_original_response(embed=em, view=None)
                await asyncio.sleep(0.4)

            # 정산
            cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid_w))
            bal_w = (await cur.fetchone())[0]
            cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid_l))
            bal_l = (await cur.fetchone())[0]
            if bal_l < stake:
                await db.execute("ROLLBACK")
                em = discord.Embed(title="맞짱 취소", description="정산 중 조건 불일치로 취소되었습니다.", color=0xE67E22)
                if self.message: await self.message.edit(embed=em, view=None)
                else: await interaction.edit_original_response(embed=em, view=None)
                self.finalized = True; self.stop(); return

            new_bal_w = bal_w + stake
            new_bal_l = bal_l - stake
            await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal_w, gid, uid_w))
            await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal_l, gid, uid_l))
            await write_ledger(db, gid, uid_w, "duel_win", +stake, new_bal_w, {"opponent": uid_l, "stake": stake, "p": p_a})
            await write_ledger(db, gid, uid_l, "duel_lose", -stake, new_bal_l, {"opponent": uid_w, "stake": stake, "p": p_a})
            await db.commit()

        winner = await resolve_userish(interaction.guild, interaction.client, uid_w)
        loser  = await resolve_userish(interaction.guild, interaction.client, uid_l)
        em = result_embed(winner, loser, stake, new_bal_w, new_bal_l, lv_w, lv_l)
        if self.message: await self.message.edit(embed=em, view=None)
        else: await interaction.edit_original_response(embed=em, view=None)
        self.finalized = True; self.stop()

    @discord.ui.button(label="거절", style=discord.ButtonStyle.danger, row=0)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._only_opponent(interaction):
            return await interaction.response.send_message("상대만 거절할 수 있습니다.", ephemeral=True)
        for c in self.children: c.disabled = True
        await interaction.response.edit_message(embed=discord.Embed(
            title="맞짱 거절됨", description=f"{interaction.user.mention} 님이 대결을 거절했습니다.", color=0x95a5a6
        ), view=None)
        self.finalized = True; self.stop()

# ===== /면진맞짱 =====
@app_commands.command(name="mz_duel", description="맞짱: 상대와 동일 금액을 베팅해 승부(0=전액)")
@app_commands.describe(opponent="상대 멤버", amount="베팅 금액(정수, 0=전액 · 최소 베팅 적용)")
async def mz_duel(interaction: discord.Interaction, opponent: discord.Member, amount: int = 0):
    gid = interaction.guild.id
    uid_a = interaction.user.id
    uid_b = opponent.id
    if uid_a == uid_b:
        return await interaction.response.send_message("자기 자신과는 대결할 수 없습니다.", ephemeral=True)
    if opponent.bot:
        return await interaction.response.send_message("봇과는 대결할 수 없습니다.", ephemeral=True)

    min_bet = await get_min_bet(gid)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        ua = await get_user(db, gid, uid_a)
        ub = await get_user(db, gid, uid_b)
        bal_a, bal_b = ua["balance"], ub["balance"]
        lv_a = await get_level(db, gid, uid_a)
        lv_b = await get_level(db, gid, uid_b)
        await db.commit()

    stake = min(bal_a, bal_b) if amount == 0 else amount
    if stake < min_bet:
        return await interaction.response.send_message(f"최소 베팅은 {won(min_bet)} 입니다. (입력: {won(stake)})", ephemeral=True)
    if stake > bal_a:
        return await interaction.response.send_message(f"도전자 잔액 부족: {won(bal_a)}", ephemeral=True)
    if stake > bal_b:
        return await interaction.response.send_message(f"상대 잔액 부족: {won(bal_b)}", ephemeral=True)

    p_a = duel_win_prob(lv_a, lv_b)
    view = DuelChallengeView(gid, uid_a, uid_b, stake, lv_a, lv_b, p_a)
    await interaction.response.send_message(embed=challenge_embed(interaction.user, opponent, stake, lv_a, lv_b, p_a), view=view)
    view.message = await interaction.original_response()

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_duel)
