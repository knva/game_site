-- 0001 基线迁移:现有全部表结构(SQLite)。
-- 来源:app/db.py init_db() / server.py init_db() 的 CREATE TABLE(已含历次 ALTER 补充的列)。
-- 空库执行本基线后,表结构与 init_db() 一致。
-- 注意:本文件为 SQLite 方言;迁移到 PostgreSQL 时按 app/db.py 顶部差异清单逐处适配。

CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    salt TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    last_login REAL,
    steal_open INTEGER NOT NULL DEFAULT 0,
    exp INTEGER NOT NULL DEFAULT 0,
    stamina INTEGER NOT NULL DEFAULT 50,
    stamina_at REAL NOT NULL DEFAULT 0,
    vip_until REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions(
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    ip TEXT
);

CREATE TABLE IF NOT EXISTS game_sessions(
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    game TEXT NOT NULL,
    seed INTEGER NOT NULL,
    chart TEXT,
    max_score INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scores(
    game TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    score INTEGER NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY(game, user_id)
);

CREATE TABLE IF NOT EXISTS farm(
    user_id INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    crop TEXT,
    planted_at REAL,
    waters INTEGER NOT NULL DEFAULT 0,
    stolen INTEGER NOT NULL DEFAULT 0,
    stolen_by TEXT,
    PRIMARY KEY(user_id, slot)
);

CREATE TABLE IF NOT EXISTS farm_plots(
    user_id INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    unlocked INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(user_id, slot)
);

CREATE TABLE IF NOT EXISTS user_buildings(
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    level INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id, name)
);

CREATE TABLE IF NOT EXISTS inventory(
    user_id INTEGER NOT NULL,
    crop TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id, crop)
);

CREATE TABLE IF NOT EXISTS slot_pending(
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    pending INTEGER NOT NULL,
    created_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS checkins(
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    reward INTEGER NOT NULL,
    make_up INTEGER NOT NULL DEFAULT 0,
    at REAL NOT NULL,
    PRIMARY KEY(user_id, day)
);

CREATE TABLE IF NOT EXISTS checkin_stats(
    user_id INTEGER PRIMARY KEY,
    streak INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    amount INTEGER,
    ip TEXT,
    at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mail(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id INTEGER,
    to_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    mtype TEXT NOT NULL DEFAULT 'user',
    created_at REAL NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bottles(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    picked INTEGER NOT NULL DEFAULT 0,
    picked_by TEXT,
    views INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS gomoku_rooms(
    code TEXT PRIMARY KEY,
    player_black INTEGER,
    player_white INTEGER,
    board TEXT NOT NULL,
    turn INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'waiting',
    winner INTEGER,
    reason TEXT,
    mode TEXT NOT NULL DEFAULT 'pvp',
    rewarded INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    last_move_at REAL,
    ip_black TEXT,
    ip_white TEXT,
    moves INTEGER NOT NULL DEFAULT 0,
    started_at REAL
);

CREATE TABLE IF NOT EXISTS gomoku_games(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    player_black INTEGER,
    player_white INTEGER,
    winner INTEGER,
    result TEXT NOT NULL,
    at REAL NOT NULL,
    loser INTEGER,
    reason TEXT,
    moves INTEGER NOT NULL DEFAULT 0,
    risk TEXT,
    ended_at REAL
);

CREATE TABLE IF NOT EXISTS wheel_logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    sector INTEGER NOT NULL,
    name TEXT NOT NULL,
    prize INTEGER NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS wheel_free_tickets(
    ticket_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wheel_spin_requests(
    user_id INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(user_id, request_id)
);

CREATE TABLE IF NOT EXISTS farm_seeds(
    user_id INTEGER NOT NULL,
    crop TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id, crop)
);

CREATE TABLE IF NOT EXISTS point_ledger(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    business TEXT NOT NULL,
    amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    biz_no TEXT UNIQUE,
    request_id TEXT,
    detail TEXT,
    ip TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limits(
    key TEXT PRIMARY KEY,
    count INTEGER NOT NULL,
    window_start REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS game_configs(
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'draft',
    updated_by TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY(name, version)
);

CREATE TABLE IF NOT EXISTS config_version(
    id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS risk_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    rule TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS reports(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_type TEXT NOT NULL,
    content_id INTEGER NOT NULL,
    reporter_id INTEGER NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    handled_by TEXT,
    note TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_audit(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL,
    admin_name TEXT NOT NULL,
    target TEXT,
    action TEXT NOT NULL,
    before_value TEXT,
    after_value TEXT,
    reason TEXT,
    request_id TEXT,
    ip TEXT,
    created_at REAL NOT NULL
);
