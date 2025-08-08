-- 기본 테이블
CREATE TABLE IF NOT EXISTS users(
  guild_id INTEGER,
  user_id  INTEGER,
  balance  INTEGER NOT NULL DEFAULT 0,
  last_claim_at INTEGER,
  last_daily_at INTEGER,
  PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER,
  user_id  INTEGER,
  kind TEXT,               -- deposit/withdraw/bet_win/bet_lose/attend/money ...
  amount INTEGER,          -- +유입 / -유출
  balance_after INTEGER,
  meta TEXT,               -- JSON
  ts   INTEGER             -- epoch seconds
);

-- 중복 처리/레이트리밋 등에 사용 가능
CREATE TABLE IF NOT EXISTS locks(
  guild_id INTEGER,
  user_id  INTEGER,
  key TEXT,
  until INTEGER,
  PRIMARY KEY (guild_id, user_id, key)
);

-- 길드별 설정
CREATE TABLE IF NOT EXISTS guild_settings(
  guild_id INTEGER PRIMARY KEY,
  min_bet      INTEGER DEFAULT 1000,  -- 최소 베팅(₩)
  win_min_bps  INTEGER DEFAULT 3000,  -- 승률 하한 30.00% (basis points)
  win_max_bps  INTEGER DEFAULT 6000,  -- 승률 상한 60.00%
  mode_name    TEXT    DEFAULT '일반 모드'
);

-- 권장 PRAGMA/인덱스
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE INDEX IF NOT EXISTS idx_users_gb ON users(guild_id, balance DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_gut ON ledger(guild_id, user_id, ts DESC);
