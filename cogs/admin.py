import os, aiosqlite, json, time
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

# ───────── 설정 보기 ─────────
@app_commands.command(name="mz_config_view", description="View guild settings (owner only)")
@owner_only()
async def mz_config_view(interaction: discord.Interaction):
    gid = interaction.guild.id
    async with aiosqlite.connect(DB_PATH) as db:
        s = await get_settings(db, gid)
    embed = discord.Embed(title="서버 설정", color=0x3498db)
    embed.add_field(name="최소 베팅", value=f"{s['min_bet']:,}₩")
    embed.add_field(name="승률 하한", value=f"{s['win_min_bps']/100:.2f}%")
    embed.add_field(name="승률 상한", value=f"{s['win_max_bps']/100:.2f}%")
    embed.add_field(name="모드명", value=s["mode_name"], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ───────── 설정 수정 ─────────
CHOICE_FIELDS = [
    app_commands.Choice(name="최소베팅", value="min_bet"),
    app_commands.Choice(name="승률하한(%)", value="win_min_bps"),
    app_commands.Choice(name="승률상한(%)", value="win_max_bps"),
    app_commands.Choice(name="모드명", value="mode_name"),
]

@app_commands.command(name="mz_config_set", description="Modify guild settings (owner only)")
@app_commands.choices(field=CHOICE_FIELDS)
@app_commands.describe(value="값(정수 또는 문자열). 승률은 % 단위, 모드명은 글자")
@owner_only()
async def mz_config_set(interaction: discord.Interaction, field: app_commands.Choice[str], value: str):
    gid = interaction.guild.id
    key = field.value
    async with aiosqlite.connect(DB_PATH) as db:
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
            return await interaction.response.send_message("알 수 없는 항목입니다.", ephemeral=True)

        await db.commit()
        s = await get_settings(db, gid)

    embed = discord.Embed(title="설정이 반영되었습니다", color=0x2ecc71)
    embed.add_field(name="최소 베팅", value=f"{s['min_bet']:,}₩")
    embed.add_field(name="승률 하한", value=f"{s['win_min_bps']/100:.2f}%")
    embed.add_field(name="승률 상한", value=f"{s['win_max_bps']/100:.2f}%")
    embed.add_field(name="모드명", value=s["mode_name"], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ───────── 잔액 수정(신규) ─────────
BAL_OPS = [
    app_commands.Choice(name="설정(=)", value="set"),
    app_commands.Choice(name="증가(+)", value="add"),
    app_commands.Choice(name="감소(-)", value="sub"),
]

@app_commands.command(name="mz_balance", description="Modify a user's balance (owner only)")
@app_commands.choices(op=BAL_OPS)
@app_commands.describe(user="대상 사용자", op="동작", amount="금액(정수)", reason="사유(선택)")
@owner_only()
async def mz_balance(
    interaction: discord.Interaction,
    user: discord.Member,
    op: app_commands.Choice[str],
    amount: int,
    reason: str | None = None,
):
    gid = interaction.guild.id
    uid = user.id
    actor = interaction.user.id

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        # 유저 레코드 확보
        cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid))
        row = await cur.fetchone()
        if row is None:
            await db.execute("INSERT INTO users(guild_id,user_id,balance) VALUES(?,?,?)", (gid, uid, 0))
            old_bal = 0
        else:
            old_bal = row[0]

        if op.value == "set":
            new_bal = amount
            delta = new_bal - old_bal
            kind = "admin_set"
        elif op.value == "add":
            new_bal = old_bal + amount
            delta = amount
            kind = "admin_add"
        else:  # sub
            new_bal = old_bal - amount
            delta = -amount
            kind = "admin_sub"

        await db.execute("UPDATE users SET balance=? WHERE guild_id=? AND user_id=?", (new_bal, gid, uid))
        await db.execute(
            "INSERT INTO ledger(guild_id,user_id,kind,amount,balance_after,meta,ts) VALUES(?,?,?,?,?,?,?)",
            (gid, uid, kind, delta, new_bal, json.dumps({"by": actor, "reason": reason or ""}), int(time.time()))
        )
        await db.commit()

    color = 0x2ecc71 if delta >= 0 else 0xe74c3c
    sign = "+" if delta >= 0 else ""
    embed = discord.Embed(title="잔액 변경 완료", color=color)
    embed.add_field(name="대상", value=f"{user.mention} (`{uid}`)")
    embed.add_field(name="동작", value=op.name)
    embed.add_field(name="이전 잔액", value=f"{old_bal:,}₩")
    embed.add_field(name="변화량", value=f"{sign}{delta:,}₩")
    embed.add_field(name="현재 잔액", value=f"{new_bal:,}₩", inline=False)
    if reason:
        embed.add_field(name="사유", value=reason, inline=False)
    embed.set_footer(text=f"실행: {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_config_view)
    bot.tree.add_command(mz_config_set)
    bot.tree.add_command(mz_balance)
