#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小游戏乐园服务器（Python 标准库，零依赖）

功能：注册/登录/积分余额/日志/管理员/站内信/漂流瓶/4个小游戏
防作弊：一次性游戏令牌、服务器生成谱面、分数上限、频率限制、每日积分上限
"""
import hashlib
import json
import math
import os
import random
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
import email.utils
from collections import deque
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "game.db")
PORT = int(os.environ.get("PORT", "8000"))
ADMIN_USERS = [u.strip() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()]

WELCOME_POINTS = 100
LOGIN_SESSION_DAYS = 7
GAME_SESSION_MINUTES = 30

# 游戏防作弊参数
GAMES = {
    "goldminer": {"name": "黄金矿工", "max_score": 6000, "duration": 60},
    "rhythm": {"name": "音乐游戏", "max_score": None, "duration": 80},
}
SUBMIT_PER_HOUR = 6
SUBMIT_PER_DAY = 40
DAILY_EARNED_CAP = 30000
RHYTHM_BPM = 132
RHYTHM_SONG_SEC = 80

CROPS = {
    "carrot": {"name": "萝卜", "emoji": "🥕", "cost": 5, "sell": 9, "grow": 25},
    "tomato": {"name": "番茄", "emoji": "🍅", "cost": 10, "sell": 19, "grow": 45},
    "corn": {"name": "玉米", "emoji": "🌽", "cost": 25, "sell": 49, "grow": 90},
    "watermelon": {"name": "西瓜", "emoji": "🍉", "cost": 60, "sell": 120, "grow": 180},
    "potato": {"name": "土豆", "emoji": "🥔", "cost": 40, "sell": 78, "grow": 120},
    "eggplant": {"name": "茄子", "emoji": "🍆", "cost": 45, "sell": 88, "grow": 150},
    "pumpkin": {"name": "南瓜", "emoji": "🎃", "cost": 55, "sell": 108, "grow": 165},
    "pepper": {"name": "辣椒", "emoji": "🌶", "cost": 50, "sell": 98, "grow": 140},
    "strawberry": {"name": "草莓", "emoji": "🍓", "cost": 70, "sell": 138, "grow": 200},
    # VIP 专属(产量高、售价高)
    "grape": {"name": "葡萄", "emoji": "🍇", "cost": 100, "sell": 210, "grow": 240, "vip": 1},
    "peach": {"name": "蟠桃", "emoji": "🍑", "cost": 120, "sell": 250, "grow": 260, "vip": 1},
    "melon": {"name": "蜜瓜", "emoji": "🍈", "cost": 150, "sell": 320, "grow": 300, "vip": 1},
}
PLOT_COUNT = 36
DEFAULT_PLOTS = 2
PLOT_MAX_LEVEL = 5
PLOT_UPGRADE_BASE = 100       # 升级费用 = 等级 * base
PLOT_GROW_CUT = 0.05          # 每级 -5% 生长时间
# 第 0~35 块地的开地费用(前 2 块免费,之后二次方递增,非常消耗积分,全开约 274 万)
PLOT_UNLOCK_COSTS = [0, 0, 200, 800, 1800, 3200, 5000, 7200, 9800, 12800, 16200, 20000, 24200, 28800,
                     33800, 39200, 45000, 51200, 57800, 64800, 72200, 80000, 88200, 96800, 105800,
                     115200, 125000, 135200, 145800, 156800, 168200, 180000, 192200, 204800, 217800, 231200]
# 第 0~35 块地的开地等级要求(等级 + 积分 双重门槛,越靠后等级越高)
PLOT_UNLOCK_LEVELS = [1, 1] + [2 + (s - 2) // 5 for s in range(2, 36)]
CROP_SIZE = {c: 1 for c in CROPS}   # 果实统一占 1 格(不再按大小占地)
WATER_SECONDS = 12          # 每次浇水加速的秒数
WATER_LIMIT = 3             # 基础浇水次数（水井每级 +1）
BUILDINGS = {
    "storehouse": {"name": "仓库", "emoji": "🏚️", "desc": "容量指数增长、每级 +6% 售价", "cost_base": 150},
    "well": {"name": "水井", "emoji": "⛲", "desc": "每级 +1 次浇水", "cost_base": 100},
    "greenhouse": {"name": "温室", "emoji": "🏡", "desc": "每级 -5% 生长时间", "cost_base": 150},
}
BUILDING_MAX_LEVEL = 5
STORE_CAPACITY_BASE = 100     # 仓库基础容量(单位)
STORE_CAPACITY_GROW = 1.45    # 每级容量 ×1.45(指数扩容)
STORE_SELL_BONUS = 0.06       # 每级 +6% 售价
BUILD_COST_GROW = 1.75        # 建筑升级费用指数基数(每级 ×1.75)

STAMINA_MAX = 50
STAMINA_REGEN_SECONDS = 300   # 每 5 分钟恢复 1 点
STEAL_STAMINA_COST = 5
STEAL_RATE = 0.4              # 偷菜获得作物售价的 40%
STEAL_DAILY_MAX = 15

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

WHEEL_COST = 10
WHEEL_SECTORS = [
    {"name": "+5分", "prize": 5}, {"name": "+10分", "prize": 10},
    {"name": "0分", "prize": 0}, {"name": "+50分", "prize": 50},
    {"name": "+2分", "prize": 2}, {"name": "再转一次", "prize": -1},
    {"name": "+100分", "prize": 100}, {"name": "-5分", "prize": -5},
]
WHEEL_WEIGHTS = [25, 20, 18, 4, 15, 6, 2, 10]

BOTTLE_COST = 2
BOTTLE_PICK_DAILY = 2     # 每天最多捡 2 个
BOTTLE_THROW_DAILY = 5    # 每天最多扔 5 个（VIP +1）

# 黄金矿工门票与经济
GOLDMINER_DAILY_LIMIT = 10
GOLDMINER_TICKET = 80
GOLDMINER_PAY_MIN = 100
GOLDMINER_PAY_MAX = 200

# 水果老虎机
SLOT_COST = 5
SLOT_PENDING_MAX = 5000     # 翻倍上限，防止无界放大
SLOT_PENDING_TTL = 600      # 待结算奖励有效期（秒），过期自动入账
SLOT_DAILY_MAX = 300        # 老虎机每日最多赢 300 金币
SLOT_SYMBOLS = {
    "cherry": {"name": "樱桃", "w": 20, "x3": 15},
    "lemon": {"name": "柠檬", "w": 16, "x3": 30},
    "watermelon": {"name": "西瓜", "w": 14, "x3": 50},
    "grapes": {"name": "葡萄", "w": 12, "x3": 100},
    "strawberry": {"name": "草莓", "w": 12, "x3": 40},
    "orange": {"name": "橙子", "w": 11, "x3": 25},
    "apple": {"name": "苹果", "w": 10, "x3": 150},
    "gem": {"name": "宝石", "w": 5, "x3": 300},
}
SLOT_PAIR_PAY = 10

# 五子棋
GOMOKU_SIZE = 15
GOMOKU_WIN_POINTS = 30
GOMOKU_LOSE_POINTS = 10
GOMOKU_DRAW_POINTS = 15

_lock = threading.RLock()  # 可重入锁：嵌套调用不会死锁

# 五子棋房间事件订阅（SSE 广播）
_room_subscribers = {}
_sub_lock = threading.Lock()


def _broadcast(code, state):
    with _sub_lock:
        for q in list(_room_subscribers.get(code, ())):
            try:
                q.put(state)
            except Exception:
                pass


def _subscribe(code):
    import queue
    q = queue.Queue(maxsize=50)
    with _sub_lock:
        _room_subscribers.setdefault(code, []).append(q)
    return q


def _unsubscribe(code, q):
    with _sub_lock:
        subs = _room_subscribers.get(code)
        if subs and q in subs:
            subs.remove(q)
            if not subs:
                _room_subscribers.pop(code, None)


# ---------------- 数据库 ----------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users(
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
            created_at REAL NOT NULL)""")
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            detail TEXT,
            amount INTEGER,
            ip TEXT,
            at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS mail(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id INTEGER,
            to_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            mtype TEXT NOT NULL DEFAULT 'user',
            created_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS bottles(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            player_black INTEGER,
            player_white INTEGER,
            winner INTEGER,
            result TEXT NOT NULL,
            at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS wheel_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            sector INTEGER NOT NULL,
            name TEXT NOT NULL,
            prize INTEGER NOT NULL,
            created_at REAL NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS farm_seeds(
            user_id INTEGER NOT NULL,
            crop TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, crop))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS point_ledger(
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
            created_at REAL NOT NULL)""")
        conn.commit()


# ---------------- 密码 / 会话 ----------------
def hash_pw(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()


def new_session(conn, user_id, ip):
    token = secrets.token_hex(24)
    conn.execute("INSERT INTO sessions(token,user_id,created_at,expires_at,ip) VALUES(?,?,?,?,?)",
                 (token, user_id, time.time(), time.time() + LOGIN_SESSION_DAYS * 86400, ip))
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


# ---------------- 日志 ----------------
def log(conn, user_id, username, action, detail="", amount=None, ip=""):
    conn.execute("INSERT INTO logs(user_id,username,action,detail,amount,ip,at) VALUES(?,?,?,?,?,?,?)",
                 (user_id, username, action, detail, amount, ip, time.time()))
    conn.commit()


# ---------------- 频率限制 ----------------
_rates = {}


def rate_check(key, limit, window, now=None):
    now = now or time.time()
    with _lock:
        q = _rates.setdefault(key, deque())
        while q and q[0] < now - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def daily_earned(name, today):
    with _lock:
        key = f"earn:{today}:{name}"
        return _rates.setdefault(key, [0, time.time()])[0]


def add_daily_earned(name, amount, today):
    with _lock:
        key = f"earn:{today}:{name}"
        v = _rates.setdefault(key, [0, time.time()])
        v[0] += amount


def slot_daily_earned(name, today):
    with _lock:
        return _rates.setdefault(f"slotearn:{today}:{name}", [0, time.time()])[0]


def add_slot_daily_earned(name, amount, today):
    with _lock:
        v = _rates.setdefault(f"slotearn:{today}:{name}", [0, time.time()])
        v[0] += amount


def _rate_peek(key, window):
    """查看窗口内已记录的次数（不计数）"""
    now = time.time()
    with _lock:
        q = _rates.get(key, deque())
        while q and q[0] < now - window:
            q.popleft()
        return len(q)


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


def get_user_by_name(conn, name):
    return conn.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()


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


# ---------------- 谱面（服务器生成，客户端无法篡改） ----------------
def gen_chart(seed):
    rng = random.Random(seed)
    step_sec = 60 / RHYTHM_BPM / 4
    steps = int(RHYTHM_SONG_SEC / step_sec)
    chart = []
    for s in range(steps):
        t = s / steps
        intensity = 0.25 + 0.6 * math.sin(math.pi * t * 1.3) ** 2 + (0.2 if t > 0.86 else 0)
        if t > 0.9:
            intensity = max(intensity, 0.85)
        if rng.random() < intensity:
            lane = rng.randrange(8)
            chart.append({"lane": lane, "t": round(s * step_sec, 3)})
            if rng.random() < 0.18 and s > 2:
                chart.append({"lane": (lane + 3 + rng.randrange(3)) % 8, "t": round(s * step_sec, 3)})
    chart.sort(key=lambda n: n["t"])
    return chart


# ---------------- 农场（经济核心） ----------------
def building_level(conn, user_id, name):
    row = conn.execute("SELECT level FROM user_buildings WHERE user_id=? AND name=?",
                       (user_id, name)).fetchone()
    return row["level"] if row else 0


def farm_capacity(conn, user_id):
    """仓库容量(指数扩容):100 × 1.45^等级"""
    lv = building_level(conn, user_id, "storehouse")
    return round(STORE_CAPACITY_BASE * (STORE_CAPACITY_GROW ** lv))


def building_upgrade_cost(name, lv):
    """建筑升级费用(指数上涨):cost_base × 1.75^等级"""
    return round(BUILDINGS[name]["cost_base"] * (BUILD_COST_GROW ** lv))


def farm_sell_value(crop, storehouse_lv):
    """作物出售单价（仓库等级加成）"""
    return round(CROPS[crop]["sell"] * (1 + STORE_SELL_BONUS * storehouse_lv))


def farm_grow_seconds(crop, plot_level, greenhouse_lv):
    """生长时长（地块 + 温室加成）"""
    return max(5, round(CROPS[crop]["grow"] * (1 - PLOT_GROW_CUT * (plot_level - 1))
                        * (1 - PLOT_GROW_CUT * greenhouse_lv)))


def farm_inventory(conn, user_id):
    rows = conn.execute("SELECT * FROM inventory WHERE user_id=?", (user_id,)).fetchall()
    inv = {r["crop"]: r["count"] for r in rows if r["count"] > 0}
    units = sum(CROP_SIZE.get(c, 1) * n for c, n in inv.items())
    return inv, units


def stamina_state(conn, user_row):
    """返回当前体力并结算在线恢复"""
    now = time.time()
    cur = user_row["stamina"]
    if user_row["stamina_at"] > 0:
        gained = int((now - user_row["stamina_at"]) // STAMINA_REGEN_SECONDS)
        if gained > 0:
            cur = min(STAMINA_MAX, cur + gained)
            conn.execute("UPDATE users SET stamina=?, stamina_at=? WHERE id=?",
                         (cur, now, user_row["id"]))
            conn.commit()
    if cur >= STAMINA_MAX:
        next_in = 0
        conn.execute("UPDATE users SET stamina_at=0 WHERE id=?", (user_row["id"],))
        conn.commit()
    else:
        stamp = user_row["stamina_at"] or now
        next_in = STAMINA_REGEN_SECONDS - int((now - stamp) % STAMINA_REGEN_SECONDS)
    return {"current": cur, "max": STAMINA_MAX,
            "next_in": next_in, "steal_cost": STEAL_STAMINA_COST}


def farm_state(conn, user_id, viewer_id=None, viewer_name=""):
    """玩家农场的完整状态（含目标农场视角）"""
    is_me = (viewer_id is None) or (viewer_id == user_id)
    user_row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user_row:
        return None
    me_row = conn.execute("SELECT * FROM users WHERE id=?", (viewer_id or user_id,)).fetchone() or user_row

    plots_row = {r["slot"]: r for r in conn.execute(
        "SELECT * FROM farm_plots WHERE user_id=?", (user_id,)).fetchall()}
    farm_row = {r["slot"]: r for r in conn.execute(
        "SELECT * FROM farm WHERE user_id=?", (user_id,)).fetchall()}
    sh = building_level(conn, user_id, "storehouse")
    gh = building_level(conn, user_id, "greenhouse")
    wl = building_level(conn, user_id, "well")

    plots = []
    for i in range(PLOT_COUNT):
        pr = plots_row.get(i)
        unlocked = bool(pr and pr["unlocked"]) or i < DEFAULT_PLOTS
        lv = pr["level"] if pr else 1
        fr = farm_row.get(i)
        base = {"slot": i, "unlocked": unlocked, "level": lv,
                "unlock_cost": PLOT_UNLOCK_COSTS[i] if not unlocked else 0,
                "unlock_level": PLOT_UNLOCK_LEVELS[i]}
        if not unlocked:
            plots.append({**base, "crop": None})
            continue
        if fr is None or fr["crop"] is None:
            plots.append({**base, "crop": None})
            continue
        crop = CROPS[fr["crop"]]
        grow = farm_grow_seconds(fr["crop"], lv, gh)
        elapsed = time.time() - fr["planted_at"]
        grown = min(1.0, elapsed / grow)
        plots.append({
            **base,
            "crop": fr["crop"], "crop_name": crop["name"], "emoji": crop["emoji"],
            "grow_seconds": grow, "elapsed": int(elapsed), "progress": round(grown, 3),
            "ready": grown >= 1.0,
            "waters": fr["waters"], "waters_limit": WATER_LIMIT + wl,
            "sell_value": farm_sell_value(fr["crop"], sh),
            "stolen": fr["stolen"], "stolen_by": fr["stolen_by"],
            "steal_reward": max(1, round(farm_sell_value(fr["crop"], sh) * STEAL_RATE)),
        })

    buildings = {name: building_level(conn, user_id, name) for name in BUILDINGS}
    inv, units = farm_inventory(conn, user_id)
    capacity = farm_capacity(conn, user_id)

    result = {
        "owner": user_row["username"],
        "level": user_level(me_row if is_me else user_row),
        "exp": me_row["exp"] if is_me else None,
        "exp_next": None if not is_me else round((((user_level(me_row)) ** 2) * 100) - me_row["exp"]),
        "is_me": is_me,
        "plots": plots,
        "buildings": buildings,
        "buildings_info": {k: {**v, "max_level": BUILDING_MAX_LEVEL,
                               "upgrade_cost": building_upgrade_cost(k, building_level(conn, user_id, k)) if
                                   building_level(conn, user_id, k) < BUILDING_MAX_LEVEL else None}
                           for k, v in BUILDINGS.items()},
        "inventory": inv,
        "seeds": {r["crop"]: r["count"] for r in conn.execute(
            "SELECT crop, count FROM farm_seeds WHERE user_id=? AND count>0", (user_id,)).fetchall()},
        "capacity": capacity,
        "capacity_used": units,
        "sell_prices": {c: farm_sell_value(c, sh) for c in CROPS},
        "plot_upgrade_cost_dict": {lv: lv * PLOT_UPGRADE_BASE for lv in range(1, PLOT_MAX_LEVEL)},
        "crops": {k: {**v, "size": CROP_SIZE[k]} for k, v in CROPS.items()},
        "stamina": stamina_state(conn, me_row) if is_me else {"current": None},
        "water_seconds": WATER_SECONDS,
        "steal_rate": STEAL_RATE,
        "steal_open": bool(user_row["steal_open"]),
        "stamina_max": STAMINA_MAX,
    }
    return result


def farm_steal_random_state(conn, user_id, username):
    """随机偷菜目标:steal_open=1 且非自己的玩家,优先有成熟作物的;返回状态或 None"""
    rows = conn.execute(
        """SELECT DISTINCT f.user_id AS uid FROM farm f
           JOIN users u ON u.id=f.user_id
           WHERE u.steal_open=1 AND f.user_id!=? AND f.crop IS NOT NULL AND f.stolen=0""",
        (user_id,)).fetchall()
    ready_ids, all_ids = [], []
    for r in rows:
        all_ids.append(r["uid"])
        st = farm_state(conn, r["uid"], user_id, username)
        if st and any(p.get("ready") for p in st["plots"]):
            ready_ids.append(r["uid"])
    pool = ready_ids or all_ids
    if not pool:
        return None
    tid = random.choice(pool)
    state = farm_state(conn, tid, user_id, username)
    state["steal_daily_left"] = STEAL_DAILY_MAX - _rate_peek(f"steal:{username}", 86400)
    return state


# ---------------- 五子棋逻辑 ----------------
def gomoku_new_board():
    return [0] * (GOMOKU_SIZE * GOMOKU_SIZE)


def gomoku_win(board, x, y, color):
    dirs = [(1, 0), (0, 1), (1, 1), (1, -1)]
    for dx, dy in dirs:
        cnt = 1
        for sign in (1, -1):
            nx, ny = x + dx * sign, y + dy * sign
            while 0 <= nx < GOMOKU_SIZE and 0 <= ny < GOMOKU_SIZE and board[ny * GOMOKU_SIZE + nx] == color:
                cnt += 1
                nx += dx * sign
                ny += dy * sign
        if cnt >= 5:
            return True
    return False


def gomoku_full(board):
    return all(board)


def _line_power(board, x, y, color):
    """评估在 (x,y) 落子后形成棋型的价值"""
    dirs = [(1, 0), (0, 1), (1, 1), (1, -1)]
    score = 0
    for dx, dy in dirs:
        cnt = 1
        open_ends = 0
        for sign in (1, -1):
            nx, ny = x + dx * sign, y + dy * sign
            while 0 <= nx < GOMOKU_SIZE and 0 <= ny < GOMOKU_SIZE and board[ny * GOMOKU_SIZE + nx] == color:
                cnt += 1
                nx += dx * sign
                ny += dy * sign
            if 0 <= nx < GOMOKU_SIZE and 0 <= ny < GOMOKU_SIZE and board[ny * GOMOKU_SIZE + nx] == 0:
                open_ends += 1
        if cnt >= 5:
            score += 100000
        elif cnt == 4 and open_ends == 2:
            score += 10000
        elif cnt == 4 and open_ends == 1:
            score += 1000
        elif cnt == 3 and open_ends == 2:
            score += 500
        elif cnt == 3 and open_ends == 1:
            score += 100
        elif cnt == 2 and open_ends == 2:
            score += 50
        elif cnt == 2 and open_ends == 1:
            score += 10
        elif cnt == 1:
            score += 1
    return score


def gomoku_bot_move(board):
    """简单AI：进攻(1.0) + 防守(1.2)，优先中心附近"""
    best, best_score = None, -1
    for y in range(GOMOKU_SIZE):
        for x in range(GOMOKU_SIZE):
            if board[y * GOMOKU_SIZE + x]:
                continue
            attack = _line_power(board, x, y, 2)
            defend = _line_power(board, x, y, 1)
            center = 14 - (abs(x - 7) + abs(y - 7))
            s = attack + defend * 1.2 + center
            if s > best_score:
                best_score = s
                best = (x, y)
    return best


def gomoku_award(conn, user_id, username, amount, detail, ip):
    points = change_points(conn, user_id, username, amount, "gomoku_award", detail, ip)
    return points


def gomoku_state(row, my_id):
    try:
        board = json.loads(row["board"])
    except Exception:
        board = gomoku_new_board()
    mine = "black" if my_id == row["player_black"] else ("white" if my_id == row["player_white"] else None)
    return {
        "code": row["code"],
        "mode": row["mode"],
        "status": row["status"],
        "board": board,
        "turn": row["turn"],
        "winner": row["winner"],
        "reason": row["reason"],
        "my_color": mine,
        "my_id": my_id,
        "black_id": row["player_black"],
        "white_id": row["player_white"],
        "black_online": row["player_black"] is not None,
        "white_online": bool(row["player_white"]),
        "can_join": row["status"] == "waiting" and row["player_black"] != my_id,
        "can_move": row["status"] == "playing" and mine is not None
                    and ((row["turn"] == 1 and mine == "black") or (row["turn"] == 2 and mine == "white")),
    }


def _finish_gomoku(conn, code, winner, reason, ip=""):
    """结算五子棋：发积分（仅一次）、写历史、广播。
    winner: 玩家 id / 0=AI 获胜 / None=平局"""
    row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
    if not row or row["status"] == "over":
        return None
    conn.execute("UPDATE gomoku_rooms SET status='over', winner=?, reason=?, last_move_at=? WHERE code=?",
                 (winner, reason, time.time(), code))
    conn.commit()
    pv = row["player_white"]
    is_bot = pv == 0
    result = "black" if winner == row["player_black"] else ("white" if not is_bot and winner == pv else "draw")
    conn.execute("INSERT INTO gomoku_games(code,player_black,player_white,winner,result,at) VALUES(?,?,?,?,?,?)",
                 (code, row["player_black"], row["player_white"], winner, result, time.time()))
    if not row["rewarded"]:
        conn.execute("UPDATE gomoku_rooms SET rewarded=1 WHERE code=?", (code,))
        pb = row["player_black"]
        if winner is None:
            if not is_bot:
                for uid in (pb, pv):
                    u = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
                    if u:
                        gomoku_award(conn, uid, u["username"], GOMOKU_DRAW_POINTS, f"五子棋平局 ({code})", ip)
        elif winner == 0:
            u = conn.execute("SELECT username FROM users WHERE id=?", (pb,)).fetchone()
            if u:
                gomoku_award(conn, pb, u["username"], GOMOKU_LOSE_POINTS, f"五子棋输给AI ({code})", ip)
        else:
            u = conn.execute("SELECT username FROM users WHERE id=?", (winner,)).fetchone()
            if u:
                gomoku_award(conn, winner, u["username"], GOMOKU_WIN_POINTS, f"五子棋获胜 ({code})", ip)
            loser = pv if winner == pb else pb
            if not is_bot and loser is not None:
                u = conn.execute("SELECT username FROM users WHERE id=?", (loser,)).fetchone()
                if u:
                    gomoku_award(conn, loser, u["username"], GOMOKU_LOSE_POINTS, f"五子棋参与 ({code})", ip)
        conn.commit()
    return row


def _gomoku_bot_turn(code):
    """AI 落子（延迟一点更像真人）"""
    time.sleep(0.55)
    with _lock, db() as conn:
        row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
        if not row or row["status"] != "playing" or row["turn"] != 2 or row["player_white"]:
            return  # 非 bot 局或有真人白方则不行动
        board = json.loads(row["board"])
        mv = gomoku_bot_move(board)
        if mv is None:
            _finish_gomoku(conn, code, None, "和棋", "")
            return
        x, y = mv
        board[y * GOMOKU_SIZE + x] = 2
        won = gomoku_win(board, x, y, 2)
        full = gomoku_full(board)
        if won or full:
            conn.execute("UPDATE gomoku_rooms SET board=?, turn=1 WHERE code=?", (json.dumps(board), code))
            conn.commit()
            _finish_gomoku(conn, code, 0 if won else None, "白棋(AI)连五" if won else "和棋", "")
        else:
            conn.execute("UPDATE gomoku_rooms SET board=?, turn=1, last_move_at=? WHERE code=?",
                         (json.dumps(board), time.time(), code))
            conn.commit()
    _broadcast(code, None)


# ---------------- 水果老虎机 ----------------
def slot_spin(conn, uid, username, ip):
    """抽奖：立即扣本金，奖励进入待结算（可翻倍或领取）"""
    syms = list(SLOT_SYMBOLS)
    weights = [SLOT_SYMBOLS[s]["w"] for s in syms]
    reel = [random.choices(syms, weights=weights, k=1)[0] for _ in range(3)]
    mid = reel[1]
    pay = 0
    if reel[0] == mid == reel[2]:
        pay = SLOT_SYMBOLS[mid]["x3"]
    elif reel[0] == mid or reel[2] == mid:
        pay = SLOT_PAIR_PAY
    change_points(conn, uid, username, -SLOT_COST, "slot_spin", f"拉杆 {'中奖'+str(pay) if pay else '未中奖'}", ip)
    token = None
    if pay > 0:
        token = secrets.token_hex(16)
        conn.execute("INSERT INTO slot_pending(token,user_id,pending,created_at) VALUES(?,?,?,?)",
                     (token, uid, min(pay, SLOT_PENDING_MAX), time.time()))
        conn.commit()
    return reel, pay, token


def slot_collect(conn, uid, username, token, ip):
    """领取待结算奖励（含翻倍后），受每日 300 上限约束"""
    row = conn.execute("SELECT * FROM slot_pending WHERE token=? AND user_id=?", (token, uid)).fetchone()
    if not row:
        return None, None
    today = time.strftime("%Y-%m-%d")
    remain = SLOT_DAILY_MAX - slot_daily_earned(username, today)
    pending = min(row["pending"], SLOT_PENDING_MAX)
    if pending > remain:
        pending = max(0, remain)
    conn.execute("DELETE FROM slot_pending WHERE token=?", (token,))
    conn.commit()
    if pending > 0:
        points = change_points(conn, uid, username, pending, "slot_win", f"老虎机领取奖励 {pending}", ip)
        add_slot_daily_earned(username, pending, today)
        add_daily_earned(username, pending, today)
        return points, pending
    return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"], 0


def slot_cleanup(conn):
    """清理过期待结算：自动入账，避免用户损失"""
    rows = conn.execute("SELECT * FROM slot_pending WHERE created_at<?",
                        (time.time() - SLOT_PENDING_TTL,)).fetchall()
    for r in rows:
        u = conn.execute("SELECT * FROM users WHERE id=?", (r["user_id"],)).fetchone()
        if u:
            conn.execute("DELETE FROM slot_pending WHERE token=?", (r["token"],))
            conn.commit()
            slot_collect(conn, r["user_id"], u["username"], r["token"], "")
    if rows:
        conn.execute("DELETE FROM slot_pending WHERE created_at<?", (time.time() - SLOT_PENDING_TTL,))
        conn.commit()


# ---------------- HTTP 处理 ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "GameSite/2.0"

    def log_message(self, fmt, *args):
        pass

    def _ip(self):
        return self.client_address[0]

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def _body(self):
        try:
            return self._read_json()
        except Exception:
            self._send(400, {"error": "请求格式错误"})
            return None

    def _me(self, admin=False):
        token = (self.headers.get("X-Token") or "").strip()
        with _lock, db() as conn:
            user = auth_user(conn, token)
            if not user:
                return None
            if admin and user["role"] != "admin":
                return False
            return dict(user)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = self._query()

        if path == "/api/me":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            with _lock, db() as conn:
                unread = conn.execute("SELECT COUNT(*) c FROM mail WHERE to_id=? AND is_read=0",
                                      (user["id"],)).fetchone()["c"]
                earned = daily_earned(user["username"], time.strftime("%Y-%m-%d"))
            return self._send(200, {"user": {k: user[k] for k in
                                             ("id", "username", "points", "role", "status", "created_at", "last_login")} | {
                                    "vip_until": user["vip_until"], "vip": is_vip(user),
                                    "vip_days_left": vip_remaining_days(user)},
                                    "unread": unread, "today_earned": earned,
                                    "daily_cap": DAILY_EARNED_CAP})

        if path == "/api/leaderboard":
            kind = (q.get("type") or ["points"])[0]
            with _lock, db() as conn:
                if kind == "score":
                    game = (q.get("game") or [""])[0]
                    rows = conn.execute(
                        "SELECT name, score FROM scores WHERE game=? ORDER BY score DESC LIMIT 20",
                        (game,)).fetchall()
                    return self._send(200, {"list": [dict(r) for r in rows]})
                rows = conn.execute(
                    "SELECT username, points FROM users WHERE status='active' ORDER BY points DESC LIMIT 20").fetchall()
                return self._send(200, {"list": [{"name": r["username"], "points": r["points"]} for r in rows]})

        if path == "/api/wheel/stats":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            with _lock, db() as conn:
                total = conn.execute("SELECT COUNT(*) c FROM wheel_logs").fetchone()["c"]
                my = conn.execute("SELECT COUNT(*) c FROM wheel_logs WHERE user_id=?",
                                  (user["id"],)).fetchone()["c"]
                win = conn.execute("SELECT COUNT(*) c FROM wheel_logs WHERE prize > 0 OR prize = -1").fetchone()["c"]
                jackpots = [dict(r) for r in conn.execute(
                    "SELECT username, name, prize, created_at FROM wheel_logs "
                    "WHERE prize >= 50 ORDER BY id DESC LIMIT 30").fetchall()]
                my_recent = [dict(r) for r in conn.execute(
                    "SELECT name, prize, created_at FROM wheel_logs WHERE user_id=? "
                    "ORDER BY id DESC LIMIT 8", (user["id"],)).fetchall()]
            return self._send(200, {
                "total": total,
                "my_spins": my,
                "win_rate": round(win / total * 100, 1) if total else 0,
                "jackpots": jackpots,
                "my_recent": my_recent,
            })

        if path == "/api/mail":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            with _lock, db() as conn:
                rows = conn.execute(
                    """SELECT m.*, COALESCE(u.username,'系统') AS from_name
                       FROM mail m LEFT JOIN users u ON u.id=m.from_id
                       WHERE m.to_id=? ORDER BY m.id DESC LIMIT 100""", (user["id"],)).fetchall()
            return self._send(200, {"list": [dict(r) for r in rows]})

        if path == "/api/farm":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            target_name = (q.get("target") or [""])[0].strip()
            with _lock, db() as conn:
                if target_name:
                    t = get_user_by_name(conn, target_name)
                    if not t:
                        return self._send(404, {"error": "该用户不存在"})
                    if t["id"] == user["id"]:
                        return self._send(200, farm_state(conn, t["id"], user["id"], user["username"]))
                    state = farm_state(conn, t["id"], user["id"], user["username"])
                    state["steal_daily_left"] = STEAL_DAILY_MAX - _rate_peek(f"steal:{user['username']}", 86400)
                    return self._send(200, state)
                return self._send(200, farm_state(conn, user["id"]))

        if path == "/api/farm/steal-random":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            with _lock, db() as conn:
                state = farm_steal_random_state(conn, user["id"], user["username"])
            if state is None:
                return self._send(404, {"error": "暂时没有可以偷的目标，稍后再来试试"})
            return self._send(200, state)

        if path == "/api/bottle/feed":
            with _lock, db() as conn:
                rows = conn.execute(
                    "SELECT id, username, content, created_at, views FROM bottles ORDER BY id DESC LIMIT 15").fetchall()
            return self._send(200, {"list": [dict(r) for r in rows], "cost": BOTTLE_COST})

        if path == "/api/bottle/pick":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            if not rate_check(f"bottlepick:{user['username']}", BOTTLE_PICK_DAILY, 86400):
                return self._send(429, {"error": f"每天最多捡 {BOTTLE_PICK_DAILY} 个漂流瓶，明天再来吧"})
            with _lock, db() as conn:
                row = conn.execute(
                    """SELECT * FROM bottles WHERE picked=0 AND user_id<>?
                       ORDER BY RANDOM() LIMIT 1""", (user["id"],)).fetchone()
                if not row:
                    return self._send(200, {"bottle": None})
                conn.execute("UPDATE bottles SET picked=1, picked_by=?, views=views+1 WHERE id=?",
                             (user["username"], row["id"]))
                conn.commit()
                log(conn, user["id"], user["username"], "bottle_pick", f"捡起第{row['id']}号漂流瓶")
                return self._send(200, {"bottle": dict(row)})

        if path == "/api/admin/users":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            search = (q.get("search") or [""])[0].strip()
            try:
                page = max(1, int((q.get("page") or ["1"])[0]))
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
            return self._send(200, {"list": [dict(r) for r in rows], "total": total, "page": page})

        if path == "/api/admin/logs":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            try:
                page = max(1, int((q.get("page") or ["1"])[0]))
            except Exception:
                page = 1
            cond, args = ["1=1"], []
            if (q.get("username") or [""])[0]:
                cond.append("username=?")
                args.append(q["username"][0])
            if (q.get("action") or [""])[0]:
                cond.append("action=?")
                args.append(q["action"][0])
            with _lock, db() as conn:
                total = conn.execute(f"SELECT COUNT(*) c FROM logs WHERE {' AND '.join(cond)}", args).fetchone()["c"]
                rows = conn.execute(
                    f"""SELECT * FROM logs WHERE {' AND '.join(cond)}
                        ORDER BY id DESC LIMIT 50 OFFSET ?""", args + [(page - 1) * 50]).fetchall()
            return self._send(200, {"list": [dict(r) for r in rows], "total": total, "page": page})

        if path == "/api/admin/bottles":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            with _lock, db() as conn:
                rows = conn.execute("SELECT * FROM bottles ORDER BY id DESC LIMIT 100").fetchall()
            return self._send(200, {"list": [dict(r) for r in rows]})

        if path == "/api/admin/stats":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
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
            return self._send(200, s)

        if path == "/api/gomoku/rank":
            with _lock, db() as conn:
                rows = conn.execute(
                    """SELECT u.username, COUNT(*) wins FROM gomoku_games g
                       JOIN users u ON u.id=g.winner
                       WHERE g.winner IS NOT NULL GROUP BY g.winner ORDER BY wins DESC LIMIT 20""").fetchall()
            return self._send(200, {"list": [dict(r) for r in rows]})

        if path == "/api/gomoku/room":
            code = (q.get("code") or [""])[0].strip().upper()
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            with _lock, db() as conn:
                row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
                if not row:
                    return self._send(404, {"error": "房间不存在"})
            return self._send(200, gomoku_state(row, user["id"]))

        if path == "/api/gomoku/stream":
            code = (q.get("code") or [""])[0].strip().upper()
            token = (q.get("token") or [""])[0].strip()
            with _lock, db() as conn:
                user = auth_user(conn, token)
                if not user:
                    return self._send(401, {"error": "未登录"})
                row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
                if not row:
                    return self._send(404, {"error": "房间不存在"})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = _subscribe(code)
            try:
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                while True:
                    try:
                        ev = q.get(timeout=15)
                    except Exception:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    if ev is None:
                        ev = "refresh"
                    with _lock, db() as conn:
                        row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
                        state = gomoku_state(row, user["id"]) if row else None
                    payload = json.dumps(state, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                _unsubscribe(code, q)
            return

        if path == "/api/checkin/status":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            with _lock, db() as conn:
                today = date.today().isoformat()
                row = conn.execute("SELECT * FROM checkins WHERE user_id=? AND day=?", (user["id"], today)).fetchone()
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
            return self._send(200, {
                "today_checked": bool(row), "today_reward": today_reward,
                "streak": streak, "future": future, "makeup": makeup,
                "makeup_cost": MAKEUP_COST, "makeup_window": MAKEUP_WINDOW,
                "max_reward": CHECKIN_MAX, "is_vip": vip,
                "vip_plans": {str(d): p for d, p in VIP_PLANS.items()},
                "vip_remaining_days": vip_remaining_days(user),
            })

        self._serve_static(path)

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        if "/../" in path or path.startswith("/data") or path.startswith("/server"):
            return self._send(404, {"error": "not found"})
        fpath = os.path.normpath(os.path.join(PUBLIC_DIR, path.lstrip("/")))
        if not fpath.startswith(PUBLIC_DIR) or not os.path.isfile(fpath):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(fpath)[1].lower()
        mime = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8", ".png": "image/png",
                ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon",
                ".json": "application/json", ".mp3": "audio/mpeg"}.get(ext, "application/octet-stream")
        body = open(fpath, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Last-Modified", email.utils.formatdate(os.path.getmtime(fpath), usegmt=True))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        data = self._body()
        if data is None:
            return
        ip = self._ip()

        # ============ 注册 / 登录 ============
        if path == "/api/register":
            username = str(data.get("username", "")).strip()
            password = str(data.get("password", ""))
            if not re.fullmatch(r"[\w\u4e00-\u9fa5]{2,16}", username):
                return self._send(400, {"error": "昵称需为2-16位中英文或数字"})
            if not (4 <= len(password) <= 64):
                return self._send(400, {"error": "密码需为4-64位"})
            if not rate_check(f"reg:{ip}", 10, 3600):
                return self._send(429, {"error": "注册过于频繁"})
            salt = secrets.token_hex(8)
            with _lock, db() as conn:
                if get_user_by_name(conn, username):
                    return self._send(400, {"error": "该昵称已被注册"})
                admin_count = conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin'").fetchone()["c"]
                role = "admin" if (admin_count == 0 or username in ADMIN_USERS) else "user"
                cur = conn.execute("INSERT INTO users(username,password,salt,points,role,created_at) VALUES(?,?,?,?,?,?)",
                                   (username, hash_pw(password, salt), salt, WELCOME_POINTS, role, time.time()))
                uid = cur.lastrowid
                token = new_session(conn, uid, ip)
                log(conn, uid, username, "register", "新用户注册", ip=ip)
            return self._send(200, {"ok": True, "token": token, "user": {
                "id": uid, "username": username, "points": WELCOME_POINTS, "role": role},
                "msg": f"注册成功，赠送 {WELCOME_POINTS} 积分！"})

        if path == "/api/login":
            username = str(data.get("username", "")).strip()
            password = str(data.get("password", ""))
            if not rate_check(f"login:{username}", 10, 300) or not rate_check(f"loginip:{ip}", 30, 300):
                return self._send(429, {"error": "登录尝试过于频繁，请稍后再试"})
            with _lock, db() as conn:
                row = get_user_by_name(conn, username)
                if not row or row["password"] != hash_pw(password, row["salt"]):
                    return self._send(400, {"error": "用户名或密码错误"})
                if row["status"] != "active":
                    return self._send(403, {"error": "账号已被封禁"})
                token = new_session(conn, row["id"], ip)
                conn.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), row["id"]))
                conn.commit()
                log(conn, row["id"], username, "login", "登录成功", ip=ip)
            return self._send(200, {"ok": True, "token": token, "user": {
                "id": row["id"], "username": username, "points": row["points"], "role": row["role"]}})

        if path == "/api/logout":
            token = (self.headers.get("X-Token") or "").strip()
            with _lock, db() as conn:
                row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
                if row:
                    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
                    conn.commit()
                    u = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
                    log(conn, row["user_id"], u["username"] if u else "?", "logout", "退出登录", ip=ip)
            return self._send(200, {"ok": True})

        # ============ 签到 / VIP ============
        if path == "/api/checkin":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            today = date.today().isoformat()
            with _lock, db() as conn:
                if conn.execute("SELECT 1 FROM checkins WHERE user_id=? AND day=?",
                                (user["id"], today)).fetchone():
                    return self._send(400, {"error": "今天已经签过到了"})
                streak = compute_streak(conn, user["id"])
                reward = checkin_reward(streak + 1, is_vip(user))
                today_str = time.strftime("%Y-%m-%d")
                if daily_earned(user["username"], today_str) + reward > DAILY_EARNED_CAP:
                    return self._send(400, {"error": "今日积分已达上限"})
                conn.execute("INSERT INTO checkins(user_id,day,reward,make_up,at) VALUES(?,?,?,0,?)",
                             (user["id"], today, reward, time.time()))
                conn.execute("UPDATE users SET exp=exp+10 WHERE id=?", (user["id"],))   # 签到经验
                points = change_points(conn, user["id"], user["username"], reward,
                                       "checkin", f"签到第 {streak + 1} 天", ip)
                add_daily_earned(user["username"], reward, today_str)
            return self._send(200, {"ok": True, "reward": reward, "streak": streak + 1, "points": points})

        if path == "/api/checkin/makeup":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            day = str(data.get("day", ""))
            try:
                d = date.fromisoformat(day)
            except ValueError:
                return self._send(400, {"error": "日期格式错误"})
            delta = (date.today() - d).days
            if not (1 <= delta < MAKEUP_WINDOW):
                return self._send(400, {"error": f"只能补签最近 {MAKEUP_WINDOW - 1} 天内的签到"})
            with _lock, db() as conn:
                if not is_vip(user):
                    return self._send(400, {"error": "只有 VIP 才可以补签，快去开通吧！"})
                if user["points"] < MAKEUP_COST:
                    return self._send(400, {"error": f"补签需要 {MAKEUP_COST} 积分"})
                if conn.execute("SELECT 1 FROM checkins WHERE user_id=? AND day=?",
                                (user["id"], day)).fetchone():
                    return self._send(400, {"error": "该日期已签到"})
                streak = compute_streak(conn, user["id"])
                conn.execute("INSERT INTO checkins(user_id,day,reward,make_up,at) VALUES(?,?,?,1,?)",
                             (user["id"], day, 0, time.time()))
                points = change_points(conn, user["id"], user["username"], -MAKEUP_COST,
                                       "checkin_makeup", f"补签 {day}", ip)
            return self._send(200, {"ok": True, "points": points, "streak": compute_streak(conn, user["id"])})

        if path == "/api/vip/buy":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            try:
                days = int(data.get("days", 0))
            except Exception:
                return self._send(400, {"error": "参数错误"})
            plan = VIP_PLANS.get(days)
            if not plan:
                return self._send(400, {"error": "仅支持 15 天 / 30 天 VIP"})
            with _lock, db() as conn:
                if user["points"] < plan["cost"]:
                    return self._send(400, {"error": f"积分不足，需要 {plan['cost']} 积分"})
                base = max(time.time(), user["vip_until"] or 0)
                conn.execute("UPDATE users SET vip_until=? WHERE id=?",
                             (base + plan["days"] * 86400, user["id"]))
                points = change_points(conn, user["id"], user["username"], -plan["cost"],
                                       "vip_buy", f"开通{plan['name']}", ip)
            return self._send(200, {"ok": True, "points": points, "days": plan["days"],
                                    "until": base + plan["days"] * 86400})

        # ============ 游戏（防作弊核心） ============
        if path == "/api/game/start":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            game = str(data.get("game", ""))
            if game not in GAMES:
                return self._send(400, {"error": "未知游戏"})
            if not rate_check(f"gstart:{user['username']}:{game}", 30, 3600):
                return self._send(429, {"error": "开局过于频繁"})
            played = 0
            if game == "goldminer":
                day_start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
                with _lock, db() as conn:
                    played = conn.execute(
                        "SELECT COUNT(*) c FROM game_sessions WHERE user_id=? AND game='goldminer' AND created_at>=?",
                        (user["id"], day_start)).fetchone()["c"]
                if played >= GOLDMINER_DAILY_LIMIT:
                    return self._send(429, {"error": f"黄金矿工每天限玩 {GOLDMINER_DAILY_LIMIT} 次，明天再来！"})
                if user["points"] < GOLDMINER_TICKET:
                    return self._send(400, {"error": f"门票需要 {GOLDMINER_TICKET} 积分"})
                with _lock, db() as conn:
                    change_points(conn, user["id"], user["username"], -GOLDMINER_TICKET,
                                  "goldminer_ticket", f"购买门票（第{played + 1}/{GOLDMINER_DAILY_LIMIT}次）", ip)
            seed = secrets.randbits(31)
            max_score = GAMES[game]["max_score"]
            chart = None
            if game == "rhythm":
                chart = gen_chart(seed)
                max_score = len(chart) * 200
            token = secrets.token_hex(24)
            with _lock, db() as conn:
                conn.execute("DELETE FROM game_sessions WHERE expires_at<?",
                             (time.time() - 3600,))
                conn.execute("""INSERT INTO game_sessions(token,user_id,game,seed,chart,max_score,created_at,expires_at)
                                VALUES(?,?,?,?,?,?,?,?)""",
                             (token, user["id"], game, seed,
                              json.dumps(chart) if chart else None, max_score,
                              time.time(), time.time() + GAME_SESSION_MINUTES * 60))
                conn.commit()
                log(conn, user["id"], user["username"], "game_start", f"开始游戏 {game}", ip=ip)
            return self._send(200, {"ok": True, "token": token, "game": game,
                                    "chart": chart, "max_score": max_score,
                                    "duration": GAMES[game]["duration"],
                                    "ticket": GOLDMINER_TICKET if game == "goldminer" else 0,
                                    "daily_left": played if game == "goldminer" else None,
                                    "limits": {"hour": SUBMIT_PER_HOUR, "day": SUBMIT_PER_DAY,
                                               "daily_cap": DAILY_EARNED_CAP}})

        if path == "/api/game/end":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            token = str(data.get("token", ""))
            game = str(data.get("game", ""))
            try:
                score = int(data.get("score", -1))
            except Exception:
                return self._send(400, {"error": "分数格式错误"})
            stats = data.get("stats") or {}
            if game not in GAMES:
                return self._send(400, {"error": "未知游戏"})
            today = time.strftime("%Y-%m-%d")
            if not rate_check(f"gend:{user['username']}:{game}", SUBMIT_PER_HOUR, 3600):
                return self._send(429, {"error": "本小时提交次数已达上限"})
            if not rate_check(f"gend:{user['username']}:{game}", SUBMIT_PER_DAY, 86400):
                return self._send(429, {"error": "今日提交次数已达上限"})
            if not rate_check(f"gendip:{ip}", 30, 3600):
                return self._send(429, {"error": "提交过于频繁"})
            with _lock, db() as conn:
                sess = conn.execute("SELECT * FROM game_sessions WHERE token=? AND user_id=?",
                                    (token, user["id"])).fetchone()
                if not sess:
                    return self._send(400, {"error": "会话无效或已过期"})
                if sess["used"]:
                    return self._send(400, {"error": "该局已结算，禁止重放"})
                if sess["game"] != game or sess["expires_at"] < time.time():
                    return self._send(400, {"error": "会话无效或已过期"})
                max_score = sess["max_score"]
                if score < 0 or score > max_score:
                    return self._send(400, {"error": "分数异常，提交被拒绝"})
                if game == "rhythm":
                    chart = json.loads(sess["chart"] or "[]")
                    p, g, m = int(stats.get("perfect", 0)), int(stats.get("good", 0)), int(stats.get("miss", 0))
                    if p + g + m < len(chart) - 1 or p + g + m > len(chart) + 1:
                        return self._send(400, {"error": "判定数据异常，提交被拒绝"})
                earned = min(score, max_score)
                if game == "goldminer":
                    earned = random.randint(GOLDMINER_PAY_MIN, GOLDMINER_PAY_MAX)
                if daily_earned(user["username"], today) + earned > DAILY_EARNED_CAP:
                    return self._send(400, {"error": f"今日可赚积分已达上限（{DAILY_EARNED_CAP}）"})
                conn.execute("UPDATE game_sessions SET used=1 WHERE token=?", (token,))
                conn.execute("UPDATE users SET exp=exp+? WHERE id=?", (max(1, earned // 20), user["id"]))  # 结算经验
                points = change_points(conn, user["id"], user["username"], earned,
                                       "game_award", f"{GAMES[game]['name']} 得分 {score}", ip,
                                       idem_key=f"settle:{token}")
                prev = conn.execute("SELECT score FROM scores WHERE game=? AND user_id=?",
                                    (game, user["id"])).fetchone()
                is_best = prev is None or score > prev["score"]
                if is_best:
                    conn.execute("INSERT OR REPLACE INTO scores(game,user_id,name,score,at) VALUES(?,?,?,?,?)",
                                 (game, user["id"], user["username"], score, time.time()))
                    conn.commit()
                add_daily_earned(user["username"], earned, today)
                log(conn, user["id"], user["username"], "game_end", f"结算 {GAMES[game]['name']}", earned, ip)
            return self._send(200, {"ok": True, "earned": earned, "points": points,
                                    "is_best": is_best, "today_earned": daily_earned(user["username"], today),
                                    "ticket": GOLDMINER_TICKET if game == "goldminer" else 0,
                                    "pay_range": [GOLDMINER_PAY_MIN, GOLDMINER_PAY_MAX] if game == "goldminer" else None})

        # ============ 农场（开地/升级/建筑/种植/浇水/收获/出售/偷菜） ============
        if path == "/api/farm/plant":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            try:
                slot = int(data.get("slot", -1))
            except Exception:
                return self._send(400, {"error": "槽位错误"})
            crop = str(data.get("crop", ""))
            if crop not in CROPS or not (0 <= slot < PLOT_COUNT):
                return self._send(400, {"error": "参数不合法"})
            if CROPS[crop].get("vip") and not is_vip(user):
                return self._send(400, {"error": "这是 VIP 专属作物，开通 VIP 后才能种植"})
            with _lock, db() as conn:
                uid = user["id"]
                seed = conn.execute("SELECT count FROM farm_seeds WHERE user_id=? AND crop=?",
                                    (uid, crop)).fetchone()
                if not seed or seed["count"] < 1:
                    return self._send(400, {"error": "没有种子，先到种子商店购买"})
                pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                                  (uid, slot)).fetchone()
                if not (pr and pr["unlocked"]) and slot >= DEFAULT_PLOTS:
                    return self._send(400, {"error": "该地块还未开垦"})
                cur = conn.execute("SELECT * FROM farm WHERE user_id=? AND slot=?", (uid, slot)).fetchone()
                if cur and cur["crop"] is not None:
                    gh = building_level(conn, uid, "greenhouse")
                    lv = pr["level"] if pr else 1
                    if time.time() < cur["planted_at"] + farm_grow_seconds(cur["crop"], lv, gh):
                        return self._send(400, {"error": "该地块还没成熟"})
                conn.execute("UPDATE farm_seeds SET count=count-1 WHERE user_id=? AND crop=?", (uid, crop))
                conn.execute("INSERT OR REPLACE INTO farm(user_id,slot,crop,planted_at,waters,stolen,stolen_by) VALUES(?,?,?,?,0,0,NULL)",
                             (uid, slot, crop, time.time()))
                conn.execute("DELETE FROM farm_seeds WHERE user_id=? AND crop=? AND count<=0", (uid, crop))
                conn.commit()
            return self._send(200, {"ok": True,
                                    "farm": farm_state(conn, uid, uid, user["username"])})

        # 购买种子(道具,不可出售)
        if path == "/api/farm/buy-seed":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            crop = str(data.get("crop", ""))
            try:
                count = max(1, min(99, int(data.get("count", 1))))
            except Exception:
                return self._send(400, {"error": "数量错误"})
            if crop not in CROPS:
                return self._send(400, {"error": "作物不存在"})
            if CROPS[crop].get("vip") and not is_vip(user):
                return self._send(400, {"error": "这是 VIP 专属作物，开通 VIP 后才能购买"})
            cost = CROPS[crop]["cost"] * count
            with _lock, db() as conn:
                if user["points"] < cost:
                    return self._send(400, {"error": f"积分不足，需要 {cost} 积分"})
                points = change_points(conn, user["id"], user["username"], -cost,
                                       "farm_buy_seed", f"购买{count}个{CROPS[crop]['name']}种子", ip)
                conn.execute("INSERT INTO farm_seeds(user_id,crop,count) VALUES(?,?,?) "
                             "ON CONFLICT(user_id,crop) DO UPDATE SET count=count+?",
                             (user["id"], crop, count, count))
                conn.commit()
            return self._send(200, {"ok": True, "points": points,
                                    "farm": farm_state(conn, user["id"], user["id"], user["username"])})

        if path == "/api/farm/water":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            try:
                slot = int(data.get("slot", -1))
            except Exception:
                return self._send(400, {"error": "槽位错误"})
            with _lock, db() as conn:
                row = conn.execute("SELECT * FROM farm WHERE user_id=? AND slot=?", (user["id"], slot)).fetchone()
                if not row or not row["crop"]:
                    return self._send(400, {"error": "没有作物"})
                limit = WATER_LIMIT + building_level(conn, user["id"], "well")
                gh = building_level(conn, user["id"], "greenhouse")
                pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                                  (user["id"], slot)).fetchone()
                lv = pr["level"] if pr else 1
                grow = farm_grow_seconds(row["crop"], lv, gh)
                if row["planted_at"] + grow - time.time() <= 0:
                    return self._send(400, {"error": "已成熟，无需浇水"})
                if row["waters"] >= limit:
                    return self._send(400, {"error": f"浇水次数已用完（{limit} 次）"})
                conn.execute("UPDATE farm SET waters=waters+1, planted_at=planted_at-? WHERE user_id=? AND slot=?",
                             (WATER_SECONDS, user["id"], slot))
                conn.commit()
            return self._send(200, {"ok": True,
                                    "farm": farm_state(conn, user["id"], user["id"], user["username"])})

        if path == "/api/farm/harvest":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            try:
                slot = int(data.get("slot", -1))
            except Exception:
                return self._send(400, {"error": "槽位错误"})
            with _lock, db() as conn:
                row = conn.execute("SELECT * FROM farm WHERE user_id=? AND slot=?", (user["id"], slot)).fetchone()
                if not row or not row["crop"]:
                    return self._send(400, {"error": "没有作物"})
                gh = building_level(conn, user["id"], "greenhouse")
                pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                                  (user["id"], slot)).fetchone()
                lv = pr["level"] if pr else 1
                grow = farm_grow_seconds(row["crop"], lv, gh)
                if time.time() < row["planted_at"] + grow:
                    return self._send(400, {"error": "还没成熟"})
                if row["stolen"]:
                    return self._send(400, {"error": f"作物已被 {row['stolen_by']} 偷走了！"})
                size = CROP_SIZE.get(row["crop"], 1)
                sh = building_level(conn, user["id"], "storehouse")
                inv, units = farm_inventory(conn, user["id"])
                capacity = farm_capacity(conn, user["id"])
                if units + size > capacity:
                    return self._send(400, {"error": f"仓库已满（{units}/{capacity}），请先出售作物"})
                conn.execute("INSERT INTO inventory(user_id,crop,count) VALUES(?,?,1) "
                             "ON CONFLICT(user_id,crop) DO UPDATE SET count=count+1",
                             (user["id"], row["crop"]))
                conn.execute("DELETE FROM farm WHERE user_id=? AND slot=?", (user["id"], slot))
                conn.execute("UPDATE users SET exp=exp+5 WHERE id=?", (user["id"],))   # 收获经验
                conn.commit()
                log(conn, user["id"], user["username"], "farm_harvest", f"收获{CROPS[row['crop']]['name']}入仓", ip=ip)
            return self._send(200, {"ok": True,
                                    "farm": farm_state(conn, user["id"], user["id"], user["username"])})

        if path == "/api/farm/sell":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            crop = str(data.get("crop", ""))
            if crop not in CROPS:
                return self._send(400, {"error": "作物不存在"})
            today = time.strftime("%Y-%m-%d")
            with _lock, db() as conn:
                inv = conn.execute("SELECT * FROM inventory WHERE user_id=? AND crop=?",
                                   (user["id"], crop)).fetchone()
                if not inv or inv["count"] <= 0:
                    return self._send(400, {"error": "仓库里没有这种作物"})
                sh = building_level(conn, user["id"], "storehouse")
                unit = farm_sell_value(crop, sh)
                earned = inv["count"] * unit
                remain = DAILY_EARNED_CAP - daily_earned(user["username"], today)
                if earned > remain:
                    sell_count = remain // unit
                    if sell_count <= 0:
                        return self._send(400, {"error": f"今日可赚积分已达上限（{DAILY_EARNED_CAP}）"})
                    conn.execute("UPDATE inventory SET count=count-? WHERE user_id=? AND crop=?",
                                 (sell_count, user["id"], crop))
                    earned = sell_count * unit
                else:
                    conn.execute("DELETE FROM inventory WHERE user_id=? AND crop=?",
                                 (user["id"], crop))
                points = change_points(conn, user["id"], user["username"], earned,
                                       "farm_sell", f"出售{CROPS[crop]['name']}", ip)
                add_daily_earned(user["username"], earned, today)
            return self._send(200, {"ok": True, "points": points, "earned": earned,
                                    "farm": farm_state(conn, user["id"], user["id"], user["username"])})

        if path == "/api/farm/unlock":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            try:
                slot = int(data.get("slot", -1))
            except Exception:
                return self._send(400, {"error": "槽位错误"})
            if not (0 <= slot < PLOT_COUNT) or slot < DEFAULT_PLOTS:
                return self._send(400, {"error": "该地块无需开垦"})
            if user_level(user) < PLOT_UNLOCK_LEVELS[slot]:
                return self._send(400, {"error": f"需要达到 {PLOT_UNLOCK_LEVELS[slot]} 级才能开垦"})
            cost = PLOT_UNLOCK_COSTS[slot]
            with _lock, db() as conn:
                pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                                  (user["id"], slot)).fetchone()
                if pr and pr["unlocked"]:
                    return self._send(400, {"error": "已开垦"})
                if user["points"] < cost:
                    return self._send(400, {"error": f"开地需要 {cost} 积分"})
                conn.execute("INSERT OR REPLACE INTO farm_plots(user_id,slot,unlocked,level) VALUES(?,?,1,1)",
                             (user["id"], slot))
                points = change_points(conn, user["id"], user["username"], -cost,
                                       "farm_unlock", f"开垦第{slot + 1}块地", ip)
            return self._send(200, {"ok": True, "points": points,
                                    "farm": farm_state(conn, user["id"], user["id"], user["username"])})

        if path == "/api/farm/upgrade":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            try:
                slot = int(data.get("slot", -1))
            except Exception:
                return self._send(400, {"error": "槽位错误"})
            with _lock, db() as conn:
                pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                                  (user["id"], slot)).fetchone()
                if slot >= DEFAULT_PLOTS and not (pr and pr["unlocked"]):
                    return self._send(400, {"error": "请先开垦该地块"})
                lv = pr["level"] if pr else 1
                if lv >= PLOT_MAX_LEVEL:
                    return self._send(400, {"error": "地块已满级"})
                cost = lv * PLOT_UPGRADE_BASE
                if user["points"] < cost:
                    return self._send(400, {"error": f"升级需要 {cost} 积分"})
                conn.execute("INSERT OR REPLACE INTO farm_plots(user_id,slot,unlocked,level) VALUES(?,?,1,?)",
                             (user["id"], slot, lv + 1))
                points = change_points(conn, user["id"], user["username"], -cost,
                                       "farm_upgrade", f"地块{slot + 1}升到{lv + 1}级", ip)
            return self._send(200, {"ok": True, "points": points,
                                    "farm": farm_state(conn, user["id"], user["id"], user["username"])})

        if path == "/api/farm/building":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            name = str(data.get("name", ""))
            if name not in BUILDINGS:
                return self._send(400, {"error": "建筑不存在"})
            with _lock, db() as conn:
                lv = building_level(conn, user["id"], name)
                if lv >= BUILDING_MAX_LEVEL:
                    return self._send(400, {"error": "该建筑已满级"})
                cost = building_upgrade_cost(name, lv)
                if user["points"] < cost:
                    return self._send(400, {"error": f"升级需要 {cost} 积分"})
                conn.execute("INSERT INTO user_buildings(user_id,name,level) VALUES(?,?,?) "
                             "ON CONFLICT(user_id,name) DO UPDATE SET level=level+1",
                             (user["id"], name, lv + 1))
                points = change_points(conn, user["id"], user["username"], -cost,
                                       "building_upgrade", f"{BUILDINGS[name]['name']}升到{lv + 1}级", ip)
            return self._send(200, {"ok": True, "points": points,
                                    "farm": farm_state(conn, user["id"], user["id"], user["username"])})

        if path == "/api/farm/steal-toggle":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            open_flag = 1 if data.get("open") else 0
            with _lock, db() as conn:
                conn.execute("UPDATE users SET steal_open=? WHERE id=?", (open_flag, user["id"]))
                conn.commit()
                log(conn, user["id"], user["username"], "steal_toggle",
                    "开启偷菜" if open_flag else "关闭偷菜", ip=ip)
            return self._send(200, {"ok": True, "steal_open": bool(open_flag)})

        if path == "/api/farm/steal-random":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            with _lock, db() as conn:
                state = farm_steal_random_state(conn, user["id"], user["username"])
            if state is None:
                return self._send(404, {"error": "暂时没有可以偷的目标，稍后再来试试"})
            return self._send(200, state)

        if path == "/api/farm/steal":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            target_name = str(data.get("target", "")).strip()
            try:
                slot = int(data.get("slot", -1))
            except Exception:
                return self._send(400, {"error": "槽位错误"})
            today = time.strftime("%Y-%m-%d")
            with _lock, db() as conn:
                own = conn.execute("SELECT steal_open FROM users WHERE id=?", (user["id"],)).fetchone()
                if own and not own["steal_open"]:
                    return self._send(400, {"error": "你已关闭偷菜，请先打开偷菜开关"})
                if not rate_check(f"steal:{user['username']}", STEAL_DAILY_MAX, 86400):
                    return self._send(400, {"error": f"今日偷菜次数已达上限（{STEAL_DAILY_MAX} 次）"})
                if not rate_check(f"stealh:{user['username']}", 6, 3600):
                    return self._send(429, {"error": "偷得太频繁了，歇会儿吧"})
                target = get_user_by_name(conn, target_name)
                if not target:
                    return self._send(404, {"error": "目标用户不存在"})
                if target["id"] == user["id"]:
                    return self._send(400, {"error": "不能偷自己的菜"})
                if not target["steal_open"]:
                    return self._send(400, {"error": "对方已关闭偷菜"})
                st = stamina_state(conn, user)
                if st["current"] < STEAL_STAMINA_COST:
                    return self._send(400, {"error": f"体力不足，偷菜需要 {STEAL_STAMINA_COST} 点体力"})
                row = conn.execute("SELECT * FROM farm WHERE user_id=? AND slot=?", (target["id"], slot)).fetchone()
                if not row or not row["crop"]:
                    return self._send(400, {"error": "对方这块地没有作物"})
                if row["stolen"]:
                    return self._send(400, {"error": "作物已经被偷过了"})
                gh = building_level(conn, target["id"], "greenhouse")
                pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                                  (target["id"], slot)).fetchone()
                lv = pr["level"] if pr else 1
                grow = farm_grow_seconds(row["crop"], lv, gh)
                if time.time() < row["planted_at"] + grow:
                    return self._send(400, {"error": "对方作物还没成熟"})
                sh = building_level(conn, target["id"], "storehouse")
                reward = max(1, round(farm_sell_value(row["crop"], sh) * STEAL_RATE))
                remain = DAILY_EARNED_CAP - daily_earned(user["username"], today)
                if reward > remain:
                    return self._send(400, {"error": "今日积分已达上限，明天再来偷吧"})
                conn.execute("UPDATE farm SET stolen=1, stolen_by=? WHERE user_id=? AND slot=?",
                             (user["username"], target["id"], slot))
                conn.execute("UPDATE users SET stamina=stamina-?, stamina_at=? WHERE id=?",
                             (STEAL_STAMINA_COST, time.time(), user["id"]))
                points = change_points(conn, user["id"], user["username"], reward,
                                       "farm_steal", f"偷了{target['username']}的{CROPS[row['crop']]['name']}", ip)
                add_daily_earned(user["username"], reward, today)
                conn.execute("INSERT INTO mail(from_id,to_id,title,content,mtype,created_at) VALUES(?,?,?,?,?,?)",
                             (user["id"], target["id"], "你的作物被偷了！",
                              f"{user['username']} 偷走了你第{slot + 1}块地的{CROPS[row['crop']]['name']} 😱",
                              "system", time.time()))
                conn.commit()
            return self._send(200, {"ok": True, "points": points, "reward": reward,
                                    "target": target_name,
                                    "farm": farm_state(conn, user["id"], user["id"], user["username"]),
                                    "target_farm": farm_state(conn, target["id"], user["id"], user["username"])})

        # ============ 转盘 ============
        if path == "/api/wheel/spin":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            if not rate_check(f"spin:{user['username']}", 60, 3600):
                return self._send(429, {"error": "转盘过于频繁"})
            with _lock, db() as conn:
                if user["points"] < WHEEL_COST:
                    return self._send(400, {"error": "积分不足"})
                idx = random.choices(range(len(WHEEL_SECTORS)), weights=WHEEL_WEIGHTS, k=1)[0]
                sector = WHEEL_SECTORS[idx]
                prize = sector["prize"]
                if prize == -1:
                    points = change_points(conn, user["id"], user["username"], -WHEEL_COST,
                                           "wheel_spin", "转盘：再转一次", ip)
                    free = True
                else:
                    points = change_points(conn, user["id"], user["username"], prize - WHEEL_COST,
                                           "wheel_spin", f"转盘：{sector['name']}", ip)
                    free = False
                conn.execute("INSERT INTO wheel_logs(user_id, username, sector, name, prize, created_at) "
                             "VALUES(?,?,?,?,?,?)",
                             (user["id"], user["username"], idx, sector["name"], prize, time.time()))
                conn.commit()
            return self._send(200, {"ok": True, "sector": idx, "name": sector["name"],
                                    "prize": prize, "free": free, "points": points})

        # ============ 站内信 ============
        if path == "/api/mail/send":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            to = str(data.get("to", "")).strip()
            title = str(data.get("title", "")).strip()[:40]
            content = str(data.get("content", "")).strip()[:500]
            if not to or not title or not content:
                return self._send(400, {"error": "内容不完整"})
            with _lock, db() as conn:
                target = get_user_by_name(conn, to)
                if not target:
                    return self._send(400, {"error": "收件人不存在"})
                conn.execute("INSERT INTO mail(from_id,to_id,title,content,mtype,created_at) VALUES(?,?,?,?,?,?)",
                             (user["id"], target["id"], title, content, "user", time.time()))
                conn.commit()
                log(conn, user["id"], user["username"], "mail_send", f"发信给 {to}", ip=ip)
            return self._send(200, {"ok": True})

        if path == "/api/mail/read":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            try:
                mid = int(data.get("id", 0))
            except Exception:
                return self._send(400, {"error": "参数错误"})
            with _lock, db() as conn:
                conn.execute("UPDATE mail SET is_read=1 WHERE id=? AND to_id=?", (mid, user["id"]))
                conn.commit()
            return self._send(200, {"ok": True})

        # ============ 漂流瓶 ============
        if path == "/api/bottle/throw":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            content = str(data.get("content", "")).strip()[:200]
            if not content:
                return self._send(400, {"error": "瓶子是空的"})
            daily = BOTTLE_THROW_DAILY + (1 if is_vip(user) else 0)
            if not rate_check(f"bottle:{user['username']}", daily, 86400):
                return self._send(429, {"error": f"每天最多扔 {daily} 个漂流瓶（VIP +1）"})
            with _lock, db() as conn:
                if user["points"] < BOTTLE_COST:
                    return self._send(400, {"error": f"扔漂流瓶需要 {BOTTLE_COST} 积分"})
                conn.execute("INSERT INTO bottles(user_id,username,content,created_at) VALUES(?,?,?,?)",
                             (user["id"], user["username"], content, time.time()))
                points = change_points(conn, user["id"], user["username"], -BOTTLE_COST,
                                       "bottle_throw", "投放漂流瓶", ip)
            return self._send(200, {"ok": True, "points": points})

        # ============ 水果老虎机（服务器随机判定） ============
        if path == "/api/slot/spin":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            if not rate_check(f"slot:{user['username']}", 60, 3600):
                return self._send(429, {"error": "转得也太快了"})
            with _lock, db() as conn:
                slot_cleanup(conn)
                today = time.strftime("%Y-%m-%d")
                if slot_daily_earned(user["username"], today) >= SLOT_DAILY_MAX:
                    return self._send(400, {"error": f"今日老虎机收益已达上限（{SLOT_DAILY_MAX} 金币），明天再来！"})
                if user["points"] < SLOT_COST:
                    return self._send(400, {"error": f"积分不足，每次需要 {SLOT_COST} 积分"})
                reel, pay, token = slot_spin(conn, user["id"], user["username"], ip)
                points = conn.execute("SELECT points FROM users WHERE id=?", (user["id"],)).fetchone()["points"]
            return self._send(200, {"ok": True, "reel": reel, "pay": pay, "cost": SLOT_COST,
                                    "points": points, "double_token": token,
                                    "can_double": bool(token and pay > SLOT_COST),
                                    "daily_left": SLOT_DAILY_MAX - slot_daily_earned(user["username"], today),
                                    "payouts": {s: SLOT_SYMBOLS[s]["x3"] for s in SLOT_SYMBOLS}})

        if path == "/api/slot/double":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            token = str(data.get("token", ""))
            if not rate_check(f"slotdbl:{user['username']}", 60, 3600):
                return self._send(429, {"error": "操作过于频繁"})
            with _lock, db() as conn:
                row = conn.execute("SELECT * FROM slot_pending WHERE token=? AND user_id=?",
                                   (token, user["id"])).fetchone()
                if not row:
                    return self._send(400, {"error": "没有待结算的奖励"})
                win = random.random() < 0.5
                if win:
                    today = time.strftime("%Y-%m-%d")
                    remain = SLOT_DAILY_MAX - slot_daily_earned(user["username"], today)
                    pending = min(row["pending"] * 2, SLOT_PENDING_MAX, max(0, remain))
                    conn.execute("UPDATE slot_pending SET pending=?, created_at=? WHERE token=?",
                                 (pending, time.time(), token))
                    conn.commit()
                    log(conn, user["id"], user["username"], "slot_double", f"翻倍成功 → {pending}", ip=ip)
                    return self._send(200, {"ok": True, "win": True, "pending": pending,
                                            "token": token, "points": user["points"]})
                conn.execute("DELETE FROM slot_pending WHERE token=?", (token,))
                conn.commit()
                log(conn, user["id"], user["username"], "slot_double", f"翻倍失败，{row['pending']} 分打了水漂", ip=ip)
                return self._send(200, {"ok": True, "win": False, "pending": 0,
                                        "token": "", "points": user["points"]})

        if path == "/api/slot/collect":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            token = str(data.get("token", ""))
            with _lock, db() as conn:
                slot_cleanup(conn)
                points, pending = slot_collect(conn, user["id"], user["username"], token, ip)
                if pending is None:
                    return self._send(400, {"error": "没有待结算的奖励"})
            return self._send(200, {"ok": True, "points": points, "pending": pending})

        # ============ 五子棋 ============
        if path == "/api/gomoku/create":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            mode = str(data.get("mode", "pvp"))
            if mode not in ("pvp", "bot"):
                return self._send(400, {"error": "模式错误"})
            if not rate_check(f"gomo:{user['username']}", 30, 3600):
                return self._send(429, {"error": "建房过于频繁"})
            code = secrets.token_hex(3).upper()
            with _lock, db() as conn:
                conn.execute("""INSERT INTO gomoku_rooms(code,player_black,player_white,board,turn,status,mode,created_at)
                                VALUES(?,?,?,?,?,?,?,?)""",
                             (code, user["id"], None, json.dumps(gomoku_new_board()), 1,
                              "playing" if mode == "bot" else "waiting", mode, time.time()))
                if mode == "bot":
                    conn.execute("UPDATE gomoku_rooms SET player_white=? WHERE code=?", (0, code))
                conn.commit()
                log(conn, user["id"], user["username"], "gomoku_create", f"创建房间 {code} ({mode})", ip=ip)
            return self._send(200, {"ok": True, "code": code, "mode": mode})

        if path == "/api/gomoku/join":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            code = str(data.get("code", "")).strip().upper()
            with _lock, db() as conn:
                row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
                if not row:
                    return self._send(404, {"error": "房间不存在"})
                if row["status"] != "waiting":
                    return self._send(400, {"error": "房间已满或已开始"})
                if row["player_black"] == user["id"]:
                    return self._send(400, {"error": "你已经在房间里了"})
                conn.execute("UPDATE gomoku_rooms SET player_white=?, status='playing', last_move_at=? WHERE code=?",
                             (user["id"], time.time(), code))
                conn.commit()
                log(conn, user["id"], user["username"], "gomoku_join", f"加入房间 {code}", ip=ip)
            _broadcast(code, None)
            return self._send(200, {"ok": True})

        if path == "/api/gomoku/move":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            code = str(data.get("code", "")).strip().upper()
            try:
                x, y = int(data.get("x", -1)), int(data.get("y", -1))
            except Exception:
                return self._send(400, {"error": "坐标错误"})
            if not (0 <= x < GOMOKU_SIZE and 0 <= y < GOMOKU_SIZE):
                return self._send(400, {"error": "超出棋盘"})
            with _lock, db() as conn:
                row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
                if not row:
                    return self._send(404, {"error": "房间不存在"})
                if row["status"] != "playing":
                    return self._send(400, {"error": "对局未在进行中"})
                color = 1 if user["id"] == row["player_black"] else (2 if user["id"] == row["player_white"] else 0)
                if not color:
                    return self._send(400, {"error": "你不是本局玩家"})
                if row["turn"] != color:
                    return self._send(400, {"error": "还没轮到你"})
                board = json.loads(row["board"])
                if board[y * GOMOKU_SIZE + x]:
                    return self._send(400, {"error": "这里已经有棋子"})
                board[y * GOMOKU_SIZE + x] = color
                won = gomoku_win(board, x, y, color)
                full = gomoku_full(board)
                if won or full:
                    conn.execute("UPDATE gomoku_rooms SET board=? WHERE code=?",
                                 (json.dumps(board), code))
                    conn.commit()
                    _finish_gomoku(conn, code, user["id"] if won else None,
                                   ("黑棋连五" if color == 1 else "白棋连五") if won else "棋盘已满", ip)
                    log(conn, user["id"], user["username"], "gomoku_move", f"房间{code}落子({x},{y})", ip=ip)
                    _broadcast(code, None)
                    return self._send(200, {"ok": True, "over": True, "winner": user["id"] if won else None})
                conn.execute("UPDATE gomoku_rooms SET board=?, turn=?, last_move_at=? WHERE code=?",
                             (json.dumps(board), 3 - color, time.time(), code))
                conn.commit()
                log(conn, user["id"], user["username"], "gomoku_move", f"房间{code}落子({x},{y})", ip=ip)
            _broadcast(code, None)
            if row["mode"] == "bot" and not won and not full:
                threading.Thread(target=_gomoku_bot_turn, args=(code,), daemon=True).start()
            return self._send(200, {"ok": True, "over": False})

        if path == "/api/gomoku/leave":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            code = str(data.get("code", "")).strip().upper()
            with _lock, db() as conn:
                row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
                if not row:
                    return self._send(404, {"error": "房间不存在"})
                if row["status"] == "waiting":
                    conn.execute("DELETE FROM gomoku_rooms WHERE code=?", (code,))
                    conn.commit()
                    return self._send(200, {"ok": True})
                if row["status"] == "playing":
                    uid = user["id"]
                    if uid in (row["player_black"], row["player_white"]):
                        opp = row["player_white"] if uid == row["player_black"] else row["player_black"]
                        conn.execute("UPDATE gomoku_rooms SET status='over', winner=?, reason='对手认输' WHERE code=?",
                                     (opp, code))
                        conn.commit()
                        _finish_gomoku(conn, code, opp, "对手认输", ip)
            _broadcast(code, None)
            return self._send(200, {"ok": True})

        # ============ 管理员 ============
        if path == "/api/admin/set-balance":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            try:
                uid = int(data.get("user_id", 0))
                amount = int(data.get("amount", 0))
            except Exception:
                return self._send(400, {"error": "参数错误"})
            if not uid or abs(amount) > 1000000:
                return self._send(400, {"error": "参数不合法"})
            note = str(data.get("note", ""))[:100]
            with _lock, db() as conn:
                target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                if not target:
                    return self._send(400, {"error": "用户不存在"})
                points = change_points(conn, uid, target["username"], amount,
                                       "admin_balance", f"管理员调整余额 {note}".strip(), ip)
                log(conn, user["id"], user["username"], "admin_op", f"给 {target['username']} 调整余额", amount, ip)
            return self._send(200, {"ok": True, "points": points})

        if path == "/api/admin/toggle-status":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            try:
                uid = int(data.get("user_id", 0))
            except Exception:
                return self._send(400, {"error": "参数错误"})
            with _lock, db() as conn:
                target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                if not target:
                    return self._send(400, {"error": "用户不存在"})
                if target["role"] == "admin" and target["id"] != user["id"]:
                    return self._send(400, {"error": "不能操作其他管理员"})
                new = "banned" if target["status"] == "active" else "active"
                conn.execute("UPDATE users SET status=? WHERE id=?", (new, uid))
                conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
                conn.commit()
                log(conn, user["id"], user["username"], "admin_op",
                    f"封禁/解封 {target['username']} → {new}", ip=ip)
            return self._send(200, {"ok": True, "status": new})

        if path == "/api/admin/mail":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            to = str(data.get("to", "")).strip()
            title = str(data.get("title", "")).strip()[:40]
            content = str(data.get("content", "")).strip()[:500]
            if not to or not title or not content:
                return self._send(400, {"error": "内容不完整"})
            with _lock, db() as conn:
                target = get_user_by_name(conn, to)
                if not target:
                    return self._send(400, {"error": "用户不存在"})
                conn.execute("INSERT INTO mail(from_id,to_id,title,content,mtype,created_at) VALUES(?,?,?,?,?,?)",
                             (user["id"], target["id"], title, content, "system", time.time()))
                conn.commit()
                log(conn, user["id"], user["username"], "admin_mail", f"系统信件给 {to}", ip=ip)
            return self._send(200, {"ok": True})

        if path == "/api/admin/del-bottle":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            try:
                bid = int(data.get("id", 0))
            except Exception:
                return self._send(400, {"error": "参数错误"})
            with _lock, db() as conn:
                conn.execute("DELETE FROM bottles WHERE id=?", (bid,))
                conn.commit()
                log(conn, user["id"], user["username"], "admin_op", f"删除漂流瓶 #{bid}", ip=ip)
            return self._send(200, {"ok": True})

        self._send(404, {"error": "接口不存在"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()


def main():
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"🎮 小游戏乐园已启动: http://localhost:{PORT}")
    print(f"   数据: {DB_PATH}   首个注册用户自动成为管理员")
    if ADMIN_USERS:
        print(f"   预设管理员: {', '.join(ADMIN_USERS)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
