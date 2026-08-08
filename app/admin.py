# -*- coding: utf-8 -*-
"""管理员模块:用户管理 / 日志 / 漂流瓶 / 统计 / 余额调整 / 封禁 / 系统信件 / 删瓶。

管理员判定:登录用户 role=admin(me(admin=True) 返回 False 时一律 403,与原实现一致)。
"""
import time
from datetime import date, timedelta

from fastapi import APIRouter, Request

from . import config
from .auth import get_user_by_name, me
from .db import _lock, db
from .gameconfig import admin_config_list, config_publish, config_rollback, config_set
from .http import json_response, parse_body
from .wallet import change_points, log

router = APIRouter()


def ensure_admins():
    """启动时将 ADMIN_USERS / ADMIN_INIT 中的存量用户提升为 admin(幂等)"""
    names = [n for n in config.ADMIN_USERS + config.ADMIN_INIT if n]
    if not names:
        return
    with _lock, db() as conn:
        for n in names:
            u = get_user_by_name(conn, n)
            if u and u["role"] != "admin":
                conn.execute("UPDATE users SET role='admin' WHERE id=?", (u["id"],))
                conn.commit()
                log(conn, u["id"], n, "admin_op", "预设管理员提升")


def _admin_or_403(request):
    """等价原 Handler 的 admin 检查:未登录或非管理员均返回 403。"""
    user = me(request, admin=True)
    if user is None or user is False:
        return None, json_response(403, {"error": "无权限"})
    return user, None


@router.get("/api/admin/users")
def admin_users(request: Request):
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    search = request.query_params.get("search", "").strip()
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except Exception:
        page = 1
    like = f"%{search}%"
    with _lock, db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE username LIKE ?", (like,)).fetchone()["c"]
        rows = conn.execute(
            """SELECT id, username, points, role, status, created_at, last_login
               FROM users WHERE username LIKE ? ORDER BY id LIMIT 50 OFFSET ?""",
            (like, (page - 1) * 50)).fetchall()
    return json_response(200, {"list": [dict(r) for r in rows], "total": total, "page": page})


@router.get("/api/admin/logs")
def admin_logs(request: Request):
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except Exception:
        page = 1
    cond, args = ["1=1"], []
    if request.query_params.get("username"):
        cond.append("username=?")
        args.append(request.query_params.get("username"))
    if request.query_params.get("action"):
        cond.append("action=?")
        args.append(request.query_params.get("action"))
    with _lock, db() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM logs WHERE {' AND '.join(cond)}", args).fetchone()["c"]
        rows = conn.execute(
            f"""SELECT * FROM logs WHERE {' AND '.join(cond)}
                ORDER BY id DESC LIMIT 50 OFFSET ?""", args + [(page - 1) * 50]).fetchall()
    return json_response(200, {"list": [dict(r) for r in rows], "total": total, "page": page})


@router.get("/api/admin/bottles")
def admin_bottles(request: Request):
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    with _lock, db() as conn:
        rows = conn.execute("SELECT * FROM bottles ORDER BY id DESC LIMIT 100").fetchall()
    return json_response(200, {"list": [dict(r) for r in rows]})


@router.get("/api/admin/stats")
def admin_stats(request: Request):
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    with _lock, db() as conn:
        s = {}
        s["users"] = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        s["points_total"] = conn.execute("SELECT COALESCE(SUM(points),0) s FROM users").fetchone()["s"]
        s["scores"] = conn.execute("SELECT COUNT(*) c FROM scores").fetchone()["c"]
        s["logs"] = conn.execute("SELECT COUNT(*) c FROM logs").fetchone()["c"]
        s["bottles"] = conn.execute("SELECT COUNT(*) c FROM bottles").fetchone()["c"]
        s["mail"] = conn.execute("SELECT COUNT(*) c FROM mail").fetchone()["c"]
        s["earned_today"] = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM logs WHERE action IN ('game_award','farm_harvest') AND at>?",
            (time.time() - 86400,)).fetchone()["s"]
    return json_response(200, s)


# ---------------- Issue #18:运营数据总览仪表盘 ----------------
def _mask_ip(ip):
    """隐私脱敏:IP 只显示前段(IPv4 显示前 2 段,IPv6 显示首个冒号分组)。"""
    if not ip:
        return ""
    ip = str(ip)
    if ":" in ip:  # IPv6
        return ip.split(":")[0] + ":*"
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:2]) + ".*.*"
    return ip[: len(ip) // 2] + "*"


def _avg_score(conn, game):
    r = conn.execute("SELECT AVG(score) s FROM scores WHERE game=?", (game,)).fetchone()
    return round(r["s"], 1) if r["s"] is not None else None


def _balance_dist(conn):
    """余额分布:按档位统计用户数。"""
    tiers = [("0~99", 0, 100), ("100~999", 100, 1000), ("1000~4999", 1000, 5000),
             ("5000~9999", 5000, 10000), ("10000~49999", 10000, 50000), ("50000+", 50000, None)]
    out = []
    for label, lo, hi in tiers:
        if hi is None:
            c = conn.execute("SELECT COUNT(*) c FROM users WHERE points>=?", (lo,)).fetchone()["c"]
        else:
            c = conn.execute("SELECT COUNT(*) c FROM users WHERE points>=? AND points<?",
                             (lo, hi)).fetchone()["c"]
        out.append({"range": label, "users": c})
    return out


@router.get("/api/admin/dashboard")
def admin_dashboard(request: Request):
    """运营仪表盘:DAU / 新增 / 各游戏开局与完成率 / 积分经济 / 余额分布(按本地时区当日)。"""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    day_start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    with _lock, db() as conn:
        dau = conn.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM ("
            " SELECT user_id FROM logs WHERE at>=? AND user_id IS NOT NULL"
            " UNION"
            " SELECT user_id FROM sessions WHERE created_at>=?"
            ") t", (day_start, day_start)).fetchone()["c"]
        new_users = conn.execute("SELECT COUNT(*) c FROM users WHERE created_at>=?",
                                 (day_start,)).fetchone()["c"]
        users_total = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        points_total = conn.execute("SELECT COALESCE(SUM(points),0) s FROM users").fetchone()["s"]

        games = {}
        for r in conn.execute(
                "SELECT game, COUNT(*) starts, COALESCE(SUM(used),0) finishes "
                "FROM game_sessions WHERE created_at>=? GROUP BY game", (day_start,)).fetchall():
            games[r["game"]] = {"starts": r["starts"], "finishes": r["finishes"],
                                "avg_score": _avg_score(conn, r["game"])}
        # 非 game_sessions 游戏(转盘/老虎机/五子棋):开局从日志聚合,完成数按各自结果口径
        for game, (start_action, finish_action) in {"slot": ("slot_spin", "slot_win"),
                                                    "wheel": ("wheel_spin", "wheel_spin"),
                                                    "gomoku": ("gomoku_create", None)}.items():
            starts = conn.execute("SELECT COUNT(*) c FROM logs WHERE action=? AND at>=?",
                                  (start_action, day_start)).fetchone()["c"]
            if finish_action:
                finishes = conn.execute("SELECT COUNT(*) c FROM logs WHERE action=? AND at>=?",
                                        (finish_action, day_start)).fetchone()["c"]
            else:
                finishes = conn.execute("SELECT COUNT(*) c FROM gomoku_games WHERE at>=?",
                                        (day_start,)).fetchone()["c"]
            games[game] = {"starts": starts, "finishes": finishes,
                           "avg_score": _avg_score(conn, game)}

        produced = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM point_ledger WHERE amount>0 AND created_at>=?",
            (day_start,)).fetchone()["s"]
        consumed = conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM point_ledger WHERE amount<0 AND created_at>=?",
            (day_start,)).fetchone()["s"]
        consumed = -consumed
        pl_rows = conn.execute(
            "SELECT business, COUNT(*) n, COALESCE(SUM(amount),0) s FROM point_ledger "
            "WHERE created_at>=? GROUP BY business ORDER BY n DESC", (day_start,)).fetchall()
        balance_dist = _balance_dist(conn)
    return json_response(200, {
        "dau": dau,
        "new_users": new_users,
        "games": games,
        "economy": {
            "produced": produced,
            "consumed": consumed,
            "net": produced - consumed,
            "point_ledger": {
                "today": sum(r["n"] for r in pl_rows),
                "by_business": [{"business": r["business"], "count": r["n"], "amount": r["s"]}
                                for r in pl_rows]},
        },
        "users_total": users_total,
        "points_total": points_total,
        "balance_dist": balance_dist,
    })


# ---------------- Issue #20:积分经济中心 / 通胀告警 ----------------
def _admin_economy(conn):
    """积分经济聚合:业务收支 / 近 14 天净增 / 余额分布 / 通胀告警。
    所有数值直接 SUM point_ledger,与流水总和一致。"""
    day_start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))

    def _sum_col(case):
        return conn.execute(
            f"SELECT COALESCE(SUM({case}),0) s FROM point_ledger WHERE created_at>=?",
            (day_start,)).fetchone()["s"]

    by_business = {}
    for r in conn.execute(
            "SELECT business, "
            "SUM(CASE WHEN amount>0 THEN amount ELSE 0 END) AS amount_in, "
            "SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END) AS amount_out "
            "FROM point_ledger GROUP BY business").fetchall():
        by_business[r["business"]] = {"amount_in": r["amount_in"], "amount_out": r["amount_out"]}

    daily_net = []
    for i in range(13, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        s = time.mktime(time.strptime(d, "%Y-%m-%d"))
        row = conn.execute(
            "SELECT SUM(CASE WHEN amount>0 THEN amount ELSE 0 END) AS produced, "
            "SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END) AS consumed "
            "FROM point_ledger WHERE created_at>=? AND created_at<?", (s, s + 86400)).fetchone()
        produced = row["produced"] or 0
        consumed = row["consumed"] or 0
        daily_net.append({"date": d, "produced": produced, "consumed": consumed, "net": produced - consumed})

    tiers = [("0~100", 0, 100), ("100~1000", 100, 1000), ("1000~10000", 1000, 10000), ("10000+", 10000, None)]
    distribution = []
    for label, lo, hi in tiers:
        if hi is None:
            c = conn.execute("SELECT COUNT(*) c FROM users WHERE points>=?", (lo,)).fetchone()["c"]
        else:
            c = conn.execute("SELECT COUNT(*) c FROM users WHERE points>=? AND points<?",
                             (lo, hi)).fetchone()["c"]
        distribution.append({"range": label, "users": c})

    alerts = []
    for r in conn.execute(
            "SELECT username, SUM(amount) net FROM point_ledger WHERE created_at>=? "
            "GROUP BY user_id, username HAVING SUM(amount)>5000 ORDER BY net DESC",
            (day_start,)).fetchall():
        alerts.append({"type": "user_surge", "level": "high",
                       "msg": f"用户「{r['username']}」今日净增 {r['net']} 积分（阈值 5000）"})
    biz_today = conn.execute(
        "SELECT business, SUM(amount) produced FROM point_ledger WHERE created_at>=? AND amount>0 "
        "GROUP BY business", (day_start,)).fetchall()
    if biz_today:
        avg = sum(r["produced"] for r in biz_today) / len(biz_today)
        for r in biz_today:
            if avg > 0 and r["produced"] > avg * 5:
                alerts.append({"type": "business_surge", "level": "medium",
                               "msg": f"业务「{r['business']}」今日产出 {r['produced']}，超过全站均值 {avg:.0f} 的 5 倍"})
    today_produced = _sum_col("CASE WHEN amount>0 THEN amount ELSE 0 END")
    today_consumed = _sum_col("CASE WHEN amount<0 THEN -amount ELSE 0 END")
    today_net = today_produced - today_consumed
    if today_net > 50000:
        alerts.append({"type": "total_net", "level": "warning",
                       "msg": f"全站今日净增 {today_net} 积分（阈值 50000）"})

    return {
        "by_business": by_business,
        "daily_net": daily_net,
        "distribution": distribution,
        "alerts": alerts,
        "summary": {"produced": today_produced, "consumed": today_consumed, "net": today_net},
    }


@router.get("/api/admin/economy")
def admin_economy(request: Request):
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    with _lock, db() as conn:
        data = _admin_economy(conn)
    return json_response(200, data)


# ---------------- Issue #21:游戏参数配置(draft/publish/rollback) ----------------
@router.get("/api/admin/config/list")
def admin_config_list(request: Request):
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    with _lock, db() as conn:
        data = admin_config_list(conn)
    return json_response(200, data)


@router.post("/api/admin/config/set")
async def admin_config_set(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    name = str(data.get("name", "")).strip()
    value = data.get("value")
    try:
        with _lock, db() as conn:
            ver = config_set(conn, name, value, user["username"])
            log(conn, user["id"], user["username"], "config_set",
                f"修改参数 {name} → {value}（草稿 v{ver}）", ip=ip)
    except ValueError as e:
        return json_response(400, {"error": str(e)})
    return json_response(200, {"ok": True, "name": name, "version": ver})


@router.post("/api/admin/config/publish")
async def admin_config_publish(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    name = str(data.get("name", "")).strip()
    try:
        with _lock, db() as conn:
            ver = config_publish(conn, name, user["username"])
            log(conn, user["id"], user["username"], "config_publish",
                f"发布参数 {name}（v{ver}）", ip=ip)
    except ValueError as e:
        return json_response(400, {"error": str(e)})
    return json_response(200, {"ok": True, "name": name, "version": ver})


@router.post("/api/admin/config/rollback")
async def admin_config_rollback(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    name = str(data.get("name", "")).strip()
    try:
        with _lock, db() as conn:
            res = config_rollback(conn, name, user["username"])
            log(conn, user["id"], user["username"], "config_rollback",
                f"回滚参数 {name} → {res['value']}（v{res['version']}）", ip=ip)
    except ValueError as e:
        return json_response(400, {"error": str(e)})
    return json_response(200, {"ok": True, "name": name, **res})


# ---------------- Issue #19:用户详情 / 封禁理由 / 会话管理 ----------------
@router.get("/api/admin/user-detail")
def admin_user_detail(request: Request):
    """按用户名返回:基本资料 / 积分流水(最近50条) / 游戏记录 / 活跃会话 / 封禁历史。"""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    name = str(request.query_params.get("name", "")).strip()
    if not name:
        return json_response(400, {"error": "缺少用户名"})
    with _lock, db() as conn:
        target = get_user_by_name(conn, name)
        if not target:
            return json_response(400, {"error": "用户不存在"})
        ledger = [dict(r) for r in conn.execute(
            "SELECT id, business, amount, balance_after, detail, ip, created_at "
            "FROM point_ledger WHERE user_id=? ORDER BY id DESC LIMIT 50",
            (target["id"],)).fetchall()]
        for r in ledger:
            r["ip"] = _mask_ip(r["ip"])
        scores = [dict(r) for r in conn.execute(
            "SELECT game, score, at FROM scores WHERE user_id=? ORDER BY score DESC",
            (target["id"],)).fetchall()]
        sessions = [dict(r) for r in conn.execute(
            "SELECT token, created_at, expires_at, ip FROM sessions "
            "WHERE user_id=? AND expires_at>? ORDER BY created_at DESC",
            (target["id"], time.time())).fetchall()]
        for s in sessions:
            s["ip"] = _mask_ip(s["ip"])
        ban_history = [dict(r) for r in conn.execute(
            "SELECT id, action, detail, at FROM logs "
            "WHERE action='admin_op' AND detail LIKE '%封禁/解封 ' || ? || ' →%' "
            "ORDER BY id DESC LIMIT 20", (name,)).fetchall()]
        recent_logs = [dict(r) for r in conn.execute(
            "SELECT id, action, detail, amount, at FROM logs WHERE username=? "
            "ORDER BY id DESC LIMIT 30", (name,)).fetchall()]
    return json_response(200, {
        "user": {k: target[k] for k in ("id", "username", "points", "role", "status",
                                        "created_at", "last_login", "vip_until", "exp", "steal_open")},
        "ledger": ledger,
        "scores": scores,
        "sessions": sessions,
        "ban_history": ban_history,
        "recent_logs": recent_logs,
    })


@router.post("/api/admin/kick-session")
async def admin_kick_session(request: Request):
    """强制注销指定用户的全部会话。"""
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    name = str(data.get("name", "")).strip()
    try:
        uid = int(data.get("user_id", 0))
    except Exception:
        uid = 0
    with _lock, db() as conn:
        target = get_user_by_name(conn, name) if name else None
        if target is None and uid:
            target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            return json_response(400, {"error": "用户不存在"})
        cur = conn.execute("DELETE FROM sessions WHERE user_id=?", (target["id"],))
        kicked = cur.rowcount
        conn.commit()
        log(conn, user["id"], user["username"], "admin_op",
            f"强制下线 {target['username']}(注销 {kicked} 个会话)", ip=ip)
    return json_response(200, {"ok": True, "kicked": kicked})


@router.post("/api/admin/set-balance")
async def admin_set_balance(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    try:
        uid = int(data.get("user_id", 0))
        amount = int(data.get("amount", 0))
    except Exception:
        return json_response(400, {"error": "参数错误"})
    if not uid or abs(amount) > 1000000:
        return json_response(400, {"error": "参数不合法"})
    note = str(data.get("note", ""))[:100]
    with _lock, db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            return json_response(400, {"error": "用户不存在"})
        points = change_points(conn, uid, target["username"], amount,
                               "admin_balance", f"管理员调整余额 {note}".strip(), ip)
        log(conn, user["id"], user["username"], "admin_op", f"给 {target['username']} 调整余额", amount, ip)
    return json_response(200, {"ok": True, "points": points})


@router.post("/api/admin/toggle-status")
async def admin_toggle_status(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    try:
        uid = int(data.get("user_id", 0))
    except Exception:
        return json_response(400, {"error": "参数错误"})
    reason = str(data.get("reason", "")).strip()[:100]
    with _lock, db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            return json_response(400, {"error": "用户不存在"})
        if target["role"] == "admin" and target["id"] != user["id"]:
            return json_response(400, {"error": "不能操作其他管理员"})
        new = "banned" if target["status"] == "active" else "active"
        conn.execute("UPDATE users SET status=? WHERE id=?", (new, uid))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        conn.commit()
        detail = f"封禁/解封 {target['username']} → {new}"
        if reason:
            detail += f"，理由：{reason}"
        log(conn, user["id"], user["username"], "admin_op", detail, ip=ip)
    return json_response(200, {"ok": True, "status": new})


@router.post("/api/admin/mail")
async def admin_mail(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    to = str(data.get("to", "")).strip()
    title = str(data.get("title", "")).strip()[:40]
    content = str(data.get("content", "")).strip()[:500]
    if not to or not title or not content:
        return json_response(400, {"error": "内容不完整"})
    with _lock, db() as conn:
        target = get_user_by_name(conn, to)
        if not target:
            return json_response(400, {"error": "用户不存在"})
        conn.execute("INSERT INTO mail(from_id,to_id,title,content,mtype,created_at) VALUES(?,?,?,?,?,?)",
                     (user["id"], target["id"], title, content, "system", time.time()))
        conn.commit()
        log(conn, user["id"], user["username"], "admin_mail", f"系统信件给 {to}", ip=ip)
    return json_response(200, {"ok": True})


@router.post("/api/admin/del-bottle")
async def admin_del_bottle(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user, err = _admin_or_403(request)
    if err is not None:
        return err
    try:
        bid = int(data.get("id", 0))
    except Exception:
        return json_response(400, {"error": "参数错误"})
    with _lock, db() as conn:
        conn.execute("DELETE FROM bottles WHERE id=?", (bid,))
        conn.commit()
        log(conn, user["id"], user["username"], "admin_op", f"删除漂流瓶 #{bid}", ip=ip)
    return json_response(200, {"ok": True})
