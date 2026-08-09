#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版本化数据库迁移执行器(Issue #17)。

按文件名前缀数字升序执行 migrations/000N_*.sql;已应用的版本记录在
schema_migrations(version INTEGER PRIMARY KEY, name, applied_at),重复运行自动跳过。

用法:
    python3 migrations/migrate.py                # 迁移默认 data/game.db
    python3 migrations/migrate.py --db /path/to/db.sqlite
    python3 migrations/migrate.py --dry-run      # 只列出待执行迁移,不落库
    DATABASE_URL=... python3 migrations/migrate.py   # 读取环境变量(见 README)

验收:在空库上执行基线(0001)后,表结构与 app/db.py 的 init_db() 一致。

注意:当前迁移脚本面向 SQLite;DATABASE_URL 指定 postgres:// 时打印提示并
降级到默认 SQLite(与 app/db.py 的降级策略一致)。
"""
import os
import re
import sqlite3
import sys
import time

MIGRATIONS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(MIGRATIONS_DIR)
DEFAULT_DB = os.path.join(BASE_DIR, "data", "game.db")


def resolve_db_path():
    """解析 DATABASE_URL:sqlite:///path 或纯路径;postgres:// 提示后降级默认 SQLite。"""
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        return DEFAULT_DB
    if raw.startswith("postgres://") or raw.startswith("postgresql://"):
        print("⚠️ DATABASE_URL 指定了 PostgreSQL,但迁移脚本尚未适配 psycopg2/SQL。"
              "已降级为默认 SQLite(data/game.db)。", file=sys.stderr)
        return DEFAULT_DB
    if raw.startswith("sqlite:///"):
        path = raw[len("sqlite:///"):]
        if path.startswith("file:"):
            path = path[len("file:"):]
            if "?" in path:
                path = path.split("?", 1)[0]
        return os.path.abspath(os.path.expanduser(path))
    if raw.startswith("sqlite:"):
        return raw[len("sqlite:"):]
    return os.path.abspath(os.path.expanduser(raw))


def connect(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def list_migrations():
    """返回 [(version, filename), ...] 按版本升序。"""
    out = []
    for fn in sorted(os.listdir(MIGRATIONS_DIR)):
        m = re.match(r"^(\d{4})_.+\.sql$", fn)
        if m:
            out.append((int(m.group(1)), fn))
    return out


def applied_versions(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL)")
    conn.commit()
    return {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}


def split_statements(sql):
    """把迁移文件拆成独立语句(每句以分号结尾;先剔除 -- 注释行,
    避免注释里的分号/关键词干扰;本仓库迁移为纯 DDL,字符串内无分号)。"""
    cleaned = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def run_migration(conn, version, filename, dry_run=False):
    path = os.path.join(MIGRATIONS_DIR, filename)
    with open(path, encoding="utf-8") as f:
        sql = f.read()
    if dry_run:
        return
    try:
        conn.execute("BEGIN")
        for stmt in split_statements(sql):
            conn.execute(stmt)
        conn.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                     (version, filename, time.time()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="版本化数据库迁移执行器(Issue #17)")
    ap.add_argument("--db", default=None, help="目标 SQLite 数据库路径(默认 data/game.db 或 DATABASE_URL)")
    ap.add_argument("--dry-run", action="store_true", help="仅列出待执行迁移,不修改数据库")
    args = ap.parse_args(argv)

    db_path = args.db or resolve_db_path()
    migrations = list_migrations()
    conn = connect(db_path)
    done = applied_versions(conn)

    pending = [(v, f) for v, f in migrations if v not in done]
    if not pending:
        print(f"✅ {db_path}: 已是最新,无需迁移(已应用 {len(done)} 个)")
        return 0

    print(f"📦 目标数据库: {db_path}")
    print(f"   待执行迁移: {[f for _, f in pending]}")
    if args.dry_run:
        print("   (dry-run: 不落库)")
        conn.close()
        return 0

    failed = None
    for version, filename in pending:
        try:
            run_migration(conn, version, filename)
            print(f"   ✔ {filename}")
        except Exception as e:
            failed = (filename, e)
            print(f"   ✘ {filename} 失败: {e}")
            break
    conn.close()

    if failed:
        print(f"❌ 迁移未完成,失败于 {failed[0]},事务已回滚")
        return 1
    print(f"✅ 迁移完成,共应用 {len(pending)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
