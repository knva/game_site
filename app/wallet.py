# -*- coding: utf-8 -*-
"""积分钱包 / 不可变流水 / 持久化限流 / 排行榜。

沿用原 server.py 实现:所有积分变动走 change_points(余额 + point_ledger 流水 + 日志),
限流基于 rate_limits 表(重启不清零、可多实例共享)。
"""
import time

from fastapi import APIRouter, Request

from . import config
from .db import _lock, db
from .http import json_response

router = APIRouter()


# ---------------- 日志 ----------------
def log(conn, user_id, username, action, detail="", amount=None, ip=""):
    conn.execute("INSERT INTO logs(user_id,username,action,detail,amount,ip,at) VALUES(?,?,?,?,?,?,?)",
                 (user_id, username, action, detail, amount, ip, time.time()))
    conn.commit()


# ---------------- 频率限制(持久化存储,重启不清零,多实例共享) ----------------
def rate_check(key, limit, window, now=None):
    """窗口限流:DB 持久化。窗口过期自动重置,计数原子递增。

    SQLite `ON CONFLICT(key) DO UPDATE` 语法与 PG 基本一致;
    迁移 PG 时 `excluded.window_start` 需写成 `EXCLUDED.window_start`。
    """
    now = now or time.time()
    with _lock, db() as conn:
        row = conn.execute("SELECT count FROM rate_limits WHERE key=? AND window_start>?",
                           (key, now - window)).fetchone()
        if not row:
            conn.execute("INSERT INTO rate_limits(key,count,window_start) VALUES(?,1,?) "
                         "ON CONFLICT(key) DO UPDATE SET count=1, window_start=excluded.window_start",
                         (key, now))
            conn.commit()
            return True
        if row["count"] >= limit:
            return False
        conn.execute("UPDATE rate_limits SET count=count+1 WHERE key=?", (key,))
        conn.commit()
        return True


def daily_earned(name, today):
    """今日已赚积分:从 logs 聚合(game_award/farm_harvest),持久化可靠"""
    with _lock, db() as conn:
        day_start = time.mktime(time.strptime(today, "%Y-%m-%d"))
        row = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM logs "
                           "WHERE username=? AND action IN ('game_award','farm_harvest') AND at>=?",
                           (name, day_start)).fetchone()
        return row["s"]


def add_daily_earned(name, amount, today):
    """已由 logs/point_ledger 持久化,无需内存计数"""
    pass


def slot_daily_earned(name, today):
    """老虎机今日已赢:从 logs 聚合"""
    with _lock, db() as conn:
        day_start = time.mktime(time.strptime(today, "%Y-%m-%d"))
        row = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM logs "
                           "WHERE username=? AND action='slot_win' AND at>=?",
                           (name, day_start)).fetchone()
        return row["s"]


def add_slot_daily_earned(name, amount, today):
    """老虎机今日已赢由 slot_win 日志持久化,无需内存计数"""
    pass


def _rate_peek(key, window):
    """查看窗口内已记录的次数(不计数,持久化)"""
    now = time.time()
    with _lock, db() as conn:
        row = conn.execute("SELECT count FROM rate_limits WHERE key=? AND window_start>?",
                           (key, now - window)).fetchone()
        return row["count"] if row else 0


# ---------------- 用户余额（所有变动都走这里 + 不可变流水 + 日志） ----------------
def change_points(conn, user_id, username, amount, action, detail="", ip="", idem_key=None):
    """统一积分变动入口:更新余额 + 写 point_ledger 不可变流水 + 写日志。
    idem_key 非空时幂等:同一业务单号只生效一次(防重复发奖/扣款)。"""
    with _lock:
        if idem_key:
            exists = conn.execute("SELECT 1 FROM point_ledger WHERE biz_no=?", (idem_key,)).fetchone()
            if exists:
                conn.commit()
                return conn.execute("SELECT points FROM users WHERE id=?", (user_id,)).fetchone()["points"]
        if amount < 0:
            cur = conn.execute("UPDATE users SET points = points + ? WHERE id=? AND points + ? >= 0",
                               (amount, user_id, amount))
            if cur.rowcount == 0:
                conn.rollback()
                raise ValueError("积分不足")
        else:
            conn.execute("UPDATE users SET points = points + ? WHERE id=?", (amount, user_id))
        balance = conn.execute("SELECT points FROM users WHERE id=?", (user_id,)).fetchone()["points"]
        conn.execute(
            "INSERT INTO point_ledger(user_id,username,business,amount,balance_after,biz_no,request_id,detail,ip,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (user_id, username, action, amount, balance, idem_key, None, detail, ip, time.time()))
        log(conn, user_id, username, action, detail, amount, ip)
        return balance


# ---------------- 排行榜 ----------------
@router.get("/api/leaderboard")
def leaderboard(request: Request):
    kind = request.query_params.get("type", "points")
    with _lock, db() as conn:
        if kind == "score":
            game = request.query_params.get("game", "")
            rows = conn.execute(
                "SELECT name, score FROM scores WHERE game=? ORDER BY score DESC LIMIT 20",
                (game,)).fetchall()
            return json_response(200, {"list": [dict(r) for r in rows]})
        rows = conn.execute(
            "SELECT username, points FROM users WHERE status='active' ORDER BY points DESC LIMIT 20").fetchall()
        return json_response(200, {"list": [{"name": r["username"], "points": r["points"]} for r in rows]})
