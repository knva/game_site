# -*- coding: utf-8 -*-
"""认证 / 用户模块:注册、登录、登出、签到、VIP、等级。

- 会话:gs_session Cookie + X-Token 头双通道(token 优先取 X-Token)。
- 密码:PBKDF2-HMAC-SHA256,随机盐。
"""
import hashlib
import math
import re
import secrets
import time
from datetime import date, timedelta

from fastapi import APIRouter, Request

from . import config
from .db import _lock, db
from .gameconfig import config_get
from .http import (clear_session_cookie, cookie_token, json_response, parse_body,
                   set_session_cookie)
from .wallet import change_points, daily_earned, log, rate_check

router = APIRouter()

# 签到 / VIP
CHECKIN_BASE = 10
CHECKIN_BONUS_START = 7       # 连续 7 天起开始加成
CHECKIN_BONUS_PER_DAY = 15    # 每多一天 +15
CHECKIN_MAX = 120             # 单日签到最高 120
MAKEUP_COST = 150             # 补签一次费用
MAKEUP_WINDOW = 7             # 只可补签最近 7 天
VIP_BONUS = 0.2               # VIP 签到奖励 +20%
VIP_PLANS = {
    30: {"days": 30, "name": "月度 VIP", "cost": 3000},
    15: {"days": 15, "name": "半月 VIP", "cost": 1500},
}


# ---------------- 密码 / 会话 ----------------
def hash_pw(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()


def new_session(conn, user_id, ip):
    token = secrets.token_hex(24)
    conn.execute("INSERT INTO sessions(token,user_id,created_at,expires_at,ip) VALUES(?,?,?,?,?)",
                 (token, user_id, time.time(), time.time() + config.LOGIN_SESSION_DAYS * 86400, ip))
    conn.commit()
    return token


def auth_user(conn, token):
    row = conn.execute("""SELECT u.*, s.expires_at AS s_exp FROM sessions s
                          JOIN users u ON u.id = s.user_id
                          WHERE s.token=? AND s.expires_at>?""",
                       (token, time.time())).fetchone()
    if not row or row["status"] != "active":
        return None
    return row


def get_user_by_name(conn, name):
    return conn.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()


def me(request: Request, admin=False):
    """等价原 Handler._me:返回 dict(user) / None(未登录) / False(登录但非管理员)。"""
    token = (request.headers.get("X-Token") or "").strip()
    if not token:
        token = cookie_token(request)
    with _lock, db() as conn:
        user = auth_user(conn, token)
        if not user:
            return None
        if admin and user["role"] != "admin":
            return False
        return dict(user)


# ---------------- 签到 / VIP / 等级 ----------------
def is_vip(user_row):
    return bool(user_row["vip_until"] and user_row["vip_until"] > time.time())


def user_level(user_row):
    """用户等级 = floor(sqrt(exp/100)) + 1(100→2级,400→3级,900→4级,1600→5级…)"""
    return int(math.sqrt(max(0, user_row["exp"]) / 100)) + 1


def compute_streak(conn, user_id):
    rows = conn.execute("SELECT day FROM checkins WHERE user_id=?", (user_id,)).fetchall()
    days = {r["day"] for r in rows}
    streak = 0
    d = date.today()
    if d.isoformat() in days:
        d -= timedelta(days=1)
    while d.isoformat() in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


def checkin_reward(streak_day, vip=False):
    if streak_day < CHECKIN_BONUS_START:
        r = CHECKIN_BASE
    else:
        r = min(CHECKIN_BASE + CHECKIN_BONUS_PER_DAY * (streak_day - (CHECKIN_BONUS_START - 1)),
                CHECKIN_MAX)
    if vip:
        r = round(r * (1 + VIP_BONUS))
    return min(r, CHECKIN_MAX)


def vip_remaining_days(user_row):
    return max(0, int((user_row["vip_until"] - time.time()) // 86400)) if user_row["vip_until"] else 0


# ================= 路由 =================
@router.get("/api/me")
def api_me(request: Request):
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    with _lock, db() as conn:
        unread = conn.execute("SELECT COUNT(*) c FROM mail WHERE to_id=? AND is_read=0",
                              (user["id"],)).fetchone()["c"]
        earned = daily_earned(user["username"], time.strftime("%Y-%m-%d"))
    return json_response(200, {"user": {k: user[k] for k in
                             ("id", "username", "points", "role", "status", "created_at", "last_login")} | {
                             "vip_until": user["vip_until"], "vip": is_vip(user),
                             "vip_days_left": vip_remaining_days(user)},
                             "unread": unread, "today_earned": earned,
                             "daily_cap": config_get("daily_earned_cap", config.DAILY_EARNED_CAP)})


@router.get("/api/checkin/status")
def checkin_status(request: Request):
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    with _lock, db() as conn:
        today = date.today().isoformat()
        row = conn.execute("SELECT * FROM checkins WHERE user_id=? AND day=?",
                           (user["id"], today)).fetchone()
        streak = compute_streak(conn, user["id"])
        vip = is_vip(user)
        # 未来 7 天签到收益预览
        future = []
        pos = streak if row else streak + 1
        for i in range(7):
            d = (date.today() + timedelta(days=i)).isoformat()
            has = conn.execute("SELECT 1 FROM checkins WHERE user_id=? AND day=?",
                               (user["id"], d)).fetchone()
            future.append({"day": d, "reward": 0 if has else checkin_reward(pos + i, vip),
                           "checked": bool(has)})
        # 可补签的日期（最近 7 天、未签）
        makeup = []
        for i in range(1, MAKEUP_WINDOW):
            d = (date.today() - timedelta(days=i)).isoformat()
            has = conn.execute("SELECT 1 FROM checkins WHERE user_id=? AND day=?",
                               (user["id"], d)).fetchone()
            if not has:
                makeup.append({"day": d, "reward": checkin_reward(streak + 1 if i == 1 else streak, vip),
                               "cost": MAKEUP_COST})
        today_reward = 0 if row else checkin_reward(streak + 1, vip)
    return json_response(200, {
        "today_checked": bool(row), "today_reward": today_reward,
        "streak": streak, "future": future, "makeup": makeup,
        "makeup_cost": MAKEUP_COST, "makeup_window": MAKEUP_WINDOW,
        "max_reward": CHECKIN_MAX, "is_vip": vip,
        "vip_plans": {str(d): p for d, p in VIP_PLANS.items()},
        "vip_remaining_days": vip_remaining_days(user),
    })


@router.post("/api/register")
async def register(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if not re.fullmatch(r"[\w\u4e00-\u9fa5]{2,16}", username):
        return json_response(400, {"error": "昵称需为2-16位中英文或数字"})
    if not (4 <= len(password) <= 64):
        return json_response(400, {"error": "密码需为4-64位"})
    if not rate_check(f"reg:{ip}", 10, 3600):
        return json_response(429, {"error": "注册过于频繁"})
    salt = secrets.token_hex(8)
    with _lock, db() as conn:
        if get_user_by_name(conn, username):
            return json_response(400, {"error": "该昵称已被注册"})
        # 注册永远创建普通用户;仅当用户名命中预设管理员名单(ADMIN_USERS / ADMIN_INIT)时设为 admin
        role = "admin" if username in config.ADMIN_USERS or username in config.ADMIN_INIT else "user"
        cur = conn.execute("INSERT INTO users(username,password,salt,points,role,created_at) VALUES(?,?,?,?,?,?)",
                           (username, hash_pw(password, salt), salt, config.WELCOME_POINTS, role, time.time()))
        uid = cur.lastrowid
        token = new_session(conn, uid, ip)
        log(conn, uid, username, "register", "新用户注册", ip=ip)
        conn.commit()
    resp = json_response(200, {"ok": True, "token": token, "user": {
        "id": uid, "username": username, "points": config.WELCOME_POINTS, "role": role},
        "msg": f"注册成功，赠送 {config.WELCOME_POINTS} 积分！"})
    set_session_cookie(resp, token)
    return resp


@router.post("/api/login")
async def login(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if not rate_check(f"login:{username}", 10, 300) or not rate_check(f"loginip:{ip}", 30, 300):
        return json_response(429, {"error": "登录尝试过于频繁，请稍后再试"})
    with _lock, db() as conn:
        row = get_user_by_name(conn, username)
        if not row or row["password"] != hash_pw(password, row["salt"]):
            return json_response(400, {"error": "用户名或密码错误"})
        if row["status"] != "active":
            return json_response(403, {"error": "账号已被封禁"})
        token = new_session(conn, row["id"], ip)
        conn.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), row["id"]))
        log(conn, row["id"], username, "login", "登录成功", ip=ip)
        conn.commit()
    resp = json_response(200, {"ok": True, "token": token, "user": {
        "id": row["id"], "username": username, "points": row["points"], "role": row["role"]}})
    set_session_cookie(resp, token)
    return resp


@router.post("/api/logout")
async def logout(request: Request):
    ip = request.client.host if request.client else ""
    token = (request.headers.get("X-Token") or "").strip()
    if not token:
        token = cookie_token(request)
    with _lock, db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
        if row:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            u = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
            log(conn, row["user_id"], u["username"] if u else "?", "logout", "退出登录", ip=ip)
            conn.commit()
    resp = json_response(200, {"ok": True})
    clear_session_cookie(resp)
    return resp


@router.post("/api/checkin")
async def checkin(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    today = date.today().isoformat()
    with _lock, db() as conn:
        if conn.execute("SELECT 1 FROM checkins WHERE user_id=? AND day=?",
                        (user["id"], today)).fetchone():
            return json_response(400, {"error": "今天已经签过到了"})
        streak = compute_streak(conn, user["id"])
        reward = checkin_reward(streak + 1, is_vip(user))
        today_str = time.strftime("%Y-%m-%d")
        if daily_earned(user["username"], today_str) + reward > config_get("daily_earned_cap", config.DAILY_EARNED_CAP):
            return json_response(400, {"error": "今日积分已达上限"})
        conn.execute("INSERT INTO checkins(user_id,day,reward,make_up,at) VALUES(?,?,?,0,?)",
                     (user["id"], today, reward, time.time()))
        conn.execute("UPDATE users SET exp=exp+10 WHERE id=?", (user["id"],))   # 签到经验
        points = change_points(conn, user["id"], user["username"], reward,
                               "checkin", f"签到第 {streak + 1} 天", ip)
    return json_response(200, {"ok": True, "reward": reward, "streak": streak + 1, "points": points})


@router.post("/api/checkin/makeup")
async def checkin_makeup(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    day = str(data.get("day", ""))
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return json_response(400, {"error": "日期格式错误"})
    delta = (date.today() - d).days
    if not (1 <= delta < MAKEUP_WINDOW):
        return json_response(400, {"error": f"只能补签最近 {MAKEUP_WINDOW - 1} 天内的签到"})
    with _lock, db() as conn:
        if not is_vip(user):
            return json_response(400, {"error": "只有 VIP 才可以补签，快去开通吧！"})
        if user["points"] < MAKEUP_COST:
            return json_response(400, {"error": f"补签需要 {MAKEUP_COST} 积分"})
        if conn.execute("SELECT 1 FROM checkins WHERE user_id=? AND day=?",
                        (user["id"], day)).fetchone():
            return json_response(400, {"error": "该日期已签到"})
        streak = compute_streak(conn, user["id"])
        conn.execute("INSERT INTO checkins(user_id,day,reward,make_up,at) VALUES(?,?,?,1,?)",
                     (user["id"], day, 0, time.time()))
        points = change_points(conn, user["id"], user["username"], -MAKEUP_COST,
                               "checkin_makeup", f"补签 {day}", ip)
    return json_response(200, {"ok": True, "points": points, "streak": compute_streak(conn, user["id"])})


@router.post("/api/vip/buy")
async def vip_buy(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    try:
        days = int(data.get("days", 0))
    except Exception:
        return json_response(400, {"error": "参数错误"})
    plan = VIP_PLANS.get(days)
    if not plan:
        return json_response(400, {"error": "仅支持 15 天 / 30 天 VIP"})
    with _lock, db() as conn:
        if user["points"] < plan["cost"]:
            return json_response(400, {"error": f"积分不足，需要 {plan['cost']} 积分"})
        base = max(time.time(), user["vip_until"] or 0)
        conn.execute("UPDATE users SET vip_until=? WHERE id=?",
                     (base + plan["days"] * 86400, user["id"]))
        points = change_points(conn, user["id"], user["username"], -plan["cost"],
                               "vip_buy", f"开通{plan['name']}", ip)
    return json_response(200, {"ok": True, "points": points, "days": plan["days"],
                               "until": base + plan["days"] * 86400})
