# -*- coding: utf-8 -*-
"""社交模块:站内信 / 漂流瓶。

SQLite 差异点:捡漂流瓶用 `ORDER BY RANDOM()`,PG 对应 `ORDER BY random()`。
"""
import time

from fastapi import APIRouter, Request

from .auth import get_user_by_name, is_vip, me
from .db import _lock, db
from .http import json_response, parse_body
from .wallet import change_points, log, rate_check

router = APIRouter()

BOTTLE_COST = 2
BOTTLE_PICK_DAILY = 2     # 每天最多捡 2 个
BOTTLE_THROW_DAILY = 5    # 每天最多扔 5 个（VIP +1）


@router.get("/api/mail")
def mail_list(request: Request):
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    with _lock, db() as conn:
        rows = conn.execute(
            """SELECT m.*, COALESCE(u.username,'系统') AS from_name
               FROM mail m LEFT JOIN users u ON u.id=m.from_id
               WHERE m.to_id=? ORDER BY m.id DESC LIMIT 100""", (user["id"],)).fetchall()
    return json_response(200, {"list": [dict(r) for r in rows]})


@router.get("/api/bottle/feed")
def bottle_feed(request: Request):
    with _lock, db() as conn:
        rows = conn.execute(
            "SELECT id, username, content, created_at, views FROM bottles "
            "WHERE hidden=0 ORDER BY id DESC LIMIT 15").fetchall()
    return json_response(200, {"list": [dict(r) for r in rows], "cost": BOTTLE_COST})


# Issue #52:捡漂流瓶是有副作用的操作(GET 会消耗次数/改变状态),只允许 POST。
# GET 保留但不产生任何副作用,统一提示改用 POST。
@router.get("/api/bottle/pick")
def bottle_pick_get(request: Request):
    return json_response(400, {"error": "请使用 POST /api/bottle/pick 捞瓶子"})


@router.post("/api/bottle/pick")
async def bottle_pick_post(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    if not rate_check(f"bottlepick:{user['username']}", BOTTLE_PICK_DAILY, 86400):
        return json_response(429, {"error": f"每天最多捡 {BOTTLE_PICK_DAILY} 个漂流瓶，明天再来吧"})
    with _lock, db() as conn:
        # SQLite `ORDER BY RANDOM()` → PG 用 `ORDER BY random()`
        row = conn.execute(
            """SELECT * FROM bottles WHERE picked=0 AND user_id<>? AND hidden=0
               ORDER BY RANDOM() LIMIT 1""", (user["id"],)).fetchone()
        if not row:
            return json_response(200, {"bottle": None})
        conn.execute("UPDATE bottles SET picked=1, picked_by=?, views=views+1 WHERE id=?",
                     (user["username"], row["id"]))
        log(conn, user["id"], user["username"], "bottle_pick", f"捡起第{row['id']}号漂流瓶")
        conn.commit()
        return json_response(200, {"bottle": dict(row)})


@router.post("/api/mail/send")
async def mail_send(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    to = str(data.get("to", "")).strip()
    title = str(data.get("title", "")).strip()[:40]
    content = str(data.get("content", "")).strip()[:500]
    if not to or not title or not content:
        return json_response(400, {"error": "内容不完整"})
    with _lock, db() as conn:
        target = get_user_by_name(conn, to)
        if not target:
            return json_response(400, {"error": "收件人不存在"})
        conn.execute("INSERT INTO mail(from_id,to_id,title,content,mtype,created_at) VALUES(?,?,?,?,?,?)",
                     (user["id"], target["id"], title, content, "user", time.time()))
        log(conn, user["id"], user["username"], "mail_send", f"发信给 {to}", ip=ip)
        conn.commit()
    return json_response(200, {"ok": True})


@router.post("/api/mail/read")
async def mail_read(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    try:
        mid = int(data.get("id", 0))
    except Exception:
        return json_response(400, {"error": "参数错误"})
    with _lock, db() as conn:
        conn.execute("UPDATE mail SET is_read=1 WHERE id=? AND to_id=?", (mid, user["id"]))
        conn.commit()
    return json_response(200, {"ok": True})


@router.post("/api/bottle/throw")
async def bottle_throw(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    content = str(data.get("content", "")).strip()[:200]
    if not content:
        return json_response(400, {"error": "瓶子是空的"})
    daily = BOTTLE_THROW_DAILY + (1 if is_vip(user) else 0)
    if not rate_check(f"bottle:{user['username']}", daily, 86400):
        return json_response(429, {"error": f"每天最多扔 {daily} 个漂流瓶（VIP +1）"})
    with _lock, db() as conn:
        if user["points"] < BOTTLE_COST:
            return json_response(400, {"error": f"扔漂流瓶需要 {BOTTLE_COST} 积分"})
        conn.execute("INSERT INTO bottles(user_id,username,content,created_at) VALUES(?,?,?,?)",
                     (user["id"], user["username"], content, time.time()))
        points = change_points(conn, user["id"], user["username"], -BOTTLE_COST,
                               "bottle_throw", "投放漂流瓶", ip)
    return json_response(200, {"ok": True, "points": points})


# ============ Issue #23:用户举报(漂流瓶 / 站内信) ============
@router.post("/api/report")
async def report(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    ctype = str(data.get("type", ""))
    try:
        cid = int(data.get("id", 0))
    except Exception:
        return json_response(400, {"error": "参数错误"})
    reason = str(data.get("reason", "")).strip()[:200]
    if ctype not in ("bottle", "mail"):
        return json_response(400, {"error": "举报类型错误"})
    if not cid or not reason:
        return json_response(400, {"error": "参数不完整"})
    with _lock, db() as conn:
        table = "bottles" if ctype == "bottle" else "mail"
        row = conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (cid,)).fetchone()
        if not row:
            return json_response(404, {"error": "内容不存在或已被删除"})
        dup = conn.execute(
            "SELECT 1 FROM reports WHERE content_type=? AND content_id=? AND reporter_id=?",
            (ctype, cid, user["id"])).fetchone()
        if dup:
            return json_response(400, {"error": "你已举报过该内容"})
        conn.execute(
            "INSERT INTO reports(content_type,content_id,reporter_id,reason,status,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (ctype, cid, user["id"], reason, "pending", time.time()))
        log(conn, user["id"], user["username"], "report", f"举报{ctype}#{cid}：{reason}", ip=ip)
        conn.commit()
    return json_response(200, {"ok": True})
