PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 유저 잔액/쿨다운
CREATE TABLE IF NOT EXISTS users (
  guild_id       INTEGER NOT NULL,
  user_id        INTEGER NOT NULL,
  balance        INTEGER NOT NULL DEFAULT 0,
  last_claim_at  INTEGER,
  last_daily_at  INTEGER,
  PRIMARY KEY (guild_id, user_id)
);

-- 원장(이력)
CREATE TABLE IF NOT EXISTS ledger (
  guild_id      INTEGER NOT NULL,
  user_id       INTEGER NOT NULL,
  kind          TEXT    NOT NULL,
  amount        INTEGER NOT NULL,
  balance_after INTEGER NOT NULL,
  meta          TEXT,
  ts            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_gut ON ledger(guild_id, user_id, ts);

-- 길드 설정
CREATE TABLE IF NOT EXISTS guild_settings (
  guild_id     INTEGER PRIMARY KEY,
  min_bet      INTEGER NOT NULL DEFAULT 1000,
  win_min_bps  INTEGER NOT NULL DEFAULT 3000, -- 30.00%
  win_max_bps  INTEGER NOT NULL DEFAULT 6000, -- 60.00%
  mode_name    TEXT    NOT NULL DEFAULT '일반 모드'
);

-- 마켓 아이템(주식/코인) — 관리자 편집 대상
CREATE TABLE IF NOT EXISTS market_items (
  guild_id  INTEGER NOT NULL,
  type      TEXT    NOT NULL,    -- 'stock' | 'coin'
  name      TEXT    NOT NULL,
  range_lo  REAL    NOT NULL,
  range_hi  REAL    NOT NULL,
  enabled   INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (guild_id, type, name)
);

-- [NEW] 무기/강화 상태 저장
CREATE TABLE IF NOT EXISTS user_weapons (
  guild_id   INTEGER NOT NULL,
  user_id    INTEGER NOT NULL,
  level      INTEGER NOT NULL DEFAULT 0,  -- +0 ~ +10
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (guild_id, user_id)
);
