-- models.sql
CREATE TABLE IF NOT EXISTS users(
  guild_id INTEGER, user_id INTEGER,
  balance INTEGER NOT NULL DEFAULT 0,
  last_claim_at INTEGER, last_daily_at INTEGER,
  PRIMARY KEY(guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id INTEGER, user_id INTEGER,
  kind TEXT, amount INTEGER, balance_after INTEGER,
  meta TEXT, ts INTEGER
);

CREATE TABLE IF NOT EXISTS locks(
  guild_id INTEGER, user_id INTEGER, key TEXT, until INTEGER,
  PRIMARY KEY(guild_id, user_id, key)
);
