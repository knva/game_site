# -*- coding: utf-8 -*-
"""数据库层(默认 SQLite)。

Issue #17:建立 SQLite → PostgreSQL 的可迁移部署方案。
- sqlite3.connect 统一在此创建,启用 WAL / busy_timeout / foreign_keys(在 db() 中设置)。
- 数据库地址从环境变量 DATABASE_URL 读取;未设置时默认 SQLite(data/game.db)。
- 当 DATABASE_URL 以 postgres:// 或 postgresql:// 开头时,打印明确提示
  "需要安装 psycopg2 并适配 SQL",并优雅降级为 SQLite(不崩溃)。

SQLite 特有语法与 PostgreSQL 差异点(后续迁移 PG 时需逐处适配):
- `INSERT OR REPLACE` → PG 用 `INSERT ... ON CONFLICT (...) DO UPDATE SET ...`。
- `... ON CONFLICT(...) DO UPDATE` → 两者语法接近,但 SQLite 用 `excluded.col`,
  PG 用 `EXCLUDED.col`(保留字需加双引号)。
- `ORDER BY RANDOM()` → PG 用 `ORDER BY random()`(函数名小写;SQLite 的 random()
  返回有符号整数,RANDOM() 也可用,用法一致)。
- `PRAGMA` 系列(SQLite 专有)→ PG 由连接池/事务隔离级别替代,WAL 无对应概念。
- `AUTOINCREMENT` → PG 用 `BIGSERIAL` 或 `IDENTITY`。
- `REAL` 时间戳 → PG 建议 `DOUBLE PRECISION` 或 `TIMESTAMPTZ`。
- 参数占位符 `?` → PG 用 `%s`。
- `LIKE` 默认大小写:SQLite 仅 ASCII 不敏感,PG 按 collation,行为可能不同。
"""
import os
import sqlite3
import threading

from . import config

# 可重入锁:所有数据库操作串行化(与原 server.py 行为一致,迁移期间保持)
_lock = threading.RLock()

# Issue #17:数据库地址从环境变量读取;PostgreSQL 未适配时打印提示并降级 SQLite。
_DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_POSTGRES_REQUESTED = _DATABASE_URL.startswith("postgres://") or _DATABASE_URL.startswith("postgresql://")
if _POSTGRES_REQUESTED:
    print("⚠️ DATABASE_URL 指定了 PostgreSQL,但本项目尚未适配 psycopg2 与 SQL。"
          "需要安装 psycopg2 并适配 SQL 后重启;当前自动降级为 SQLite(data/game.db)。", flush=True)


def db():
    """打开一个 SQLite 连接(每请求/每事务),启用 WAL、busy_timeout、外键。"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL").fetchall()   # SQLite 专有;PG 无需(连接池替代)
    conn.execute("PRAGMA busy_timeout=5000")             # SQLite 专有;PG 由连接池/语句超时控制
    conn.execute("PRAGMA foreign_keys=ON")               # SQLite 需显式开启;PG 默认开启
    return conn


def init_db():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,        -- PG: id BIGSERIAL PRIMARY KEY
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            salt TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            role TEXT NOT NULL DEFAULT 'user',
            status TEXT NOT NULL DEFAULT 'active',
            created_at REAL NOT NULL,
            last_login REAL,
            steal_open INTEGER NOT NULL DEFAULT 0,
            exp INTEGER NOT NULL DEFAULT 0)""")
        # 迁移:老库补字段
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "steal_open" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN steal_open INTEGER NOT NULL DEFAULT 0")
        elif "steal_open" in cols:
            pass
        if "exp" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN exp INTEGER NOT NULL DEFAULT 0")
        if "steal_open" in cols:
            conn.execute("UPDATE users SET steal_open=0 WHERE steal_open=1")
        conn.execute("""CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            ip TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS game_sessions(
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            game TEXT NOT NULL,
            seed INTEGER NOT NULL,
            chart TEXT,
            max_score INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            used INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS scores(
            game TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            score INTEGER NOT NULL,
            at REAL NOT NULL,
            PRIMARY KEY(game, user_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS farm(
            user_id INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            crop TEXT,
            planted_at REAL,
            waters INTEGER NOT NULL DEFAULT 0,
            stolen INTEGER NOT NULL DEFAULT 0,
            stolen_by TEXT,
            PRIMARY KEY(user_id, slot))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS farm_plots(
            user_id INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            unlocked INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(user_id, slot))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS user_buildings(
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            level INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, name))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS inventory(
            user_id INTEGER NOT NULL,
            crop TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, crop))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS slot_pending(
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            pending INTEGER NOT NULL,
            created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending')""")
        # 老库迁移:slot_pending 补 status 状态机列
        _sp_cols = [r["name"] for r in conn.execute("PRAGMA table_info(slot_pending)").fetchall()]
        if "status" not in _sp_cols:
            conn.execute("ALTER TABLE slot_pending ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        conn.execute("""CREATE TABLE IF NOT EXISTS checkins(
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            reward INTEGER NOT NULL,
            make_up INTEGER NOT NULL DEFAULT 0,
            at REAL NOT NULL,
            PRIMARY KEY(user_id, day))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS checkin_stats(
            user_id INTEGER PRIMARY KEY,
            streak INTEGER NOT NULL DEFAULT 0)""")
        # 老库迁移：补列（已存在则忽略）
        for table, col, decl in [("users", "stamina", "INTEGER NOT NULL DEFAULT 50"),
                                 ("users", "stamina_at", "REAL NOT NULL DEFAULT 0"),
                                 ("users", "vip_until", "REAL NOT NULL DEFAULT 0"),
                                 ("farm", "stolen", "INTEGER NOT NULL DEFAULT 0"),
                                 ("farm", "stolen_by", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass
        conn.execute("""CREATE TABLE IF NOT EXISTS logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,        -- PG: id BIGSERIAL PRIMARY KEY
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            detail TEXT,
            amount INTEGER,
            ip TEXT,
            at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS mail(
            id INTEGER PRIMARY KEY AUTOINCREMENT,        -- PG: id BIGSERIAL PRIMARY KEY
            from_id INTEGER,
            to_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            mtype TEXT NOT NULL DEFAULT 'user',
            created_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS bottles(
            id INTEGER PRIMARY KEY AUTOINCREMENT,        -- PG: id BIGSERIAL PRIMARY KEY
            user_id INTEGER,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            picked INTEGER NOT NULL DEFAULT 0,
            picked_by TEXT,
            views INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS gomoku_rooms(
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
            last_move_at REAL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS gomoku_games(
            id INTEGER PRIMARY KEY AUTOINCREMENT,        -- PG: id BIGSERIAL PRIMARY KEY
            code TEXT NOT NULL,
            player_black INTEGER,
            player_white INTEGER,
            winner INTEGER,
            result TEXT NOT NULL,
            at REAL NOT NULL)""")
        # 五子棋迁移：补列（房间 TTL/超时/风控/结算原因）
        _gr_cols = [r["name"] for r in conn.execute("PRAGMA table_info(gomoku_rooms)").fetchall()]
        for _col, _decl in [("ip_black", "TEXT"), ("ip_white", "TEXT"),
                            ("moves", "INTEGER NOT NULL DEFAULT 0"), ("started_at", "REAL")]:
            if _col not in _gr_cols:
                conn.execute(f"ALTER TABLE gomoku_rooms ADD COLUMN {_col} {_decl}")
        _gg_cols = [r["name"] for r in conn.execute("PRAGMA table_info(gomoku_games)").fetchall()]
        for _col, _decl in [("loser", "INTEGER"), ("reason", "TEXT"),
                            ("moves", "INTEGER NOT NULL DEFAULT 0"), ("risk", "TEXT"), ("ended_at", "REAL")]:
            if _col not in _gg_cols:
                conn.execute(f"ALTER TABLE gomoku_games ADD COLUMN {_col} {_decl}")
        conn.execute("""CREATE TABLE IF NOT EXISTS wheel_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,        -- PG: id BIGSERIAL PRIMARY KEY
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            sector INTEGER NOT NULL,
            name TEXT NOT NULL,
            prize INTEGER NOT NULL,
            created_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS wheel_free_tickets(
            ticket_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            used INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS wheel_spin_requests(
            user_id INTEGER NOT NULL,
            request_id TEXT NOT NULL,
            result TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(user_id, request_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS farm_seeds(
            user_id INTEGER NOT NULL,
            crop TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, crop))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS point_ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT,        -- PG: id BIGSERIAL PRIMARY KEY
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            business TEXT NOT NULL,
            amount INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            biz_no TEXT UNIQUE,
            request_id TEXT,
            detail TEXT,
            ip TEXT,
            created_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS rate_limits(
            key TEXT PRIMARY KEY,
            count INTEGER NOT NULL,
            window_start REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS game_configs(
            name TEXT NOT NULL,
            value TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'draft',
            updated_by TEXT,
            created_at REAL NOT NULL,
            PRIMARY KEY(name, version))""")
        conn.commit()
