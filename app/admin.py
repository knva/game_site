# -*- coding: utf-8 -*-
"""管理员模块:用户管理 / 日志 / 漂流瓶 / 统计 / 余额调整 / 封禁 / 系统信件 / 删瓶。

管理员判定:登录用户 role=admin(me(admin=True) 返回 False 时一律 403,与原实现一致)。
"""
import time

from fastapi import APIRouter, Request

from . import config
from .auth import get_user_by_name, me
from .db import _lock, db
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
        log(conn, user["id"], user["username"], "admin_op",
            f"封禁/解封 {target['username']} → {new}", ip=ip)
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
