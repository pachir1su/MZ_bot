import aiosqlite
import discord
from discord import app_commands

DB_PATH = "economy.db"

async def get_user_balance(db, gid: int, uid: int) -> int:
    cur = await db.execute("SELECT balance FROM users WHERE guild_id=? AND user_id=?", (gid, uid))
    row = await cur.fetchone()
    return row[0] if row else 0

async def get_rank(db, gid: int, uid: int) -> tuple[int, int]:
    bal = await get_user_balance(db, gid, uid)
    cur = await db.execute("SELECT COUNT(*) FROM users WHERE guild_id=? AND balance>?", (gid, bal))
    higher = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM users WHERE guild_id=?", (gid,))
    total = (await cur.fetchone())[0]
    return higher + 1, total

async def get_weapon(db, gid: int, uid: int) -> int:
    cur = await db.execute("SELECT level FROM user_weapons WHERE guild_id=? AND user_id=?", (gid, uid))
    row = await cur.fetchone()
    return row[0] if row else 0

async def get_duel_record(db, gid: int, uid: int) -> tuple[int, int]:
    cur = await db.execute("SELECT COUNT(*) FROM ledger WHERE guild_id=? AND user_id=? AND kind='duel_win'", (gid, uid))
    wins = (await cur.fetchone())[0]
    cur = await db.execute("SELECT COUNT(*) FROM ledger WHERE guild_id=? AND user_id=? AND kind='duel_lose'", (gid, uid))
    loses = (await cur.fetchone())[0]
    return wins, loses

def weapon_name(lv: int) -> str:
    if lv <= 0: return "맨손"
    names = [
        None,"샤프심","연필","페트병","파리채","우산","노트북","낡은 단검","쓸만한 단검","견고한 단검","빠따",
        "전기톱","롱소드","화염의 검","냉기의 검","듀얼블레이드","심판자의 검","엑스칼리버","플라즈마 소드",
        "천총운검","사인참사검","뒤랑칼","파멸의 대검","여명의 검","키샨","영원의 창","정의의 저울",
        "사신의 낫","아스트라페","롱기누스의 창","면진검"
    ]
    lv = min(max(0, lv), 30)
    return names[lv] if lv < len(names) else f"+{lv}"

@app_commands.command(name="mz_profile", description="보유 금액, 서버 등수, 무기, 맞짱 전적 등 프로필")
async def mz_profile(interaction: discord.Interaction, user: discord.Member | None = None):
    member = user or interaction.user
    gid, uid = interaction.guild.id, member.id

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        bal = await get_user_balance(db, gid, uid)
        rank, total = await get_rank(db, gid, uid)
        lv = await get_weapon(db, gid, uid)
        w, l = await get_duel_record(db, gid, uid)
        await db.commit()

    em = discord.Embed(title="프로필", color=0x3498db)
    try:
        em.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    except Exception:
        em.set_author(name=member.display_name)
    em.add_field(name="잔액", value=f"{bal:,}₩", inline=True)
    em.add_field(name="서버 등수", value=f"{rank}위 / {total}명", inline=True)
    em.add_field(name="무기", value=f"LV{lv} 「{weapon_name(lv)}」", inline=False)
    em.add_field(name="맞짱 전적", value=f"{w}승 {l}패", inline=True)
    await interaction.response.send_message(embed=em, ephemeral=False)

async def setup(bot: discord.Client):
    bot.tree.add_command(mz_profile)
