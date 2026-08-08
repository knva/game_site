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
# 部署时通过环境变量指定初始管理员(逗号分隔用户名),注册时/启动时提升为 admin
ADMIN_INIT = [u.strip() for u in os.environ.get("ADMIN_INIT", "").split(",") if u.strip()]

WELCOME_POINTS = 100
LOGIN_SESSION_DAYS = 7
GAME_SESSION_MINUTES = 30
SESSION_COOKIE = "gs_session"  # HttpOnly 会话 Cookie(同源请求自动携带)

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
WHEEL_FREE_TTL = 86400   # 免费券有效期(秒)=24 小时
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

# 内容敏感词清单(仅用于举报时提示"涉嫌违规内容",不自动处理/不自动隐藏)
SENSITIVE_WORDS = ["作弊", "外挂", "脚本", "刷分", "代练", "赌博", "色情", "诈骗", "木马", "病毒", "封号"]


def _contains_sensitive(text):
    """内容是否命中敏感词(举报原因提示用,不做任何自动处理)"""
    if not text:
        return False
    return any(w in text for w in SENSITIVE_WORDS)

# 黄金矿工门票与经济
GOLDMINER_DAILY_LIMIT = 10
GOLDMINER_TICKET = 80
GOLDMINER_PAY_MIN = 100
GOLDMINER_PAY_MAX = 200
# 黄金矿工矿表(与前端 goldminer.html GEN 一致,服务器权威,用于重算地图校验结算)
GOLDMINER_GEN = [
    {"type": "gold", "r": 17, "w": 2.2, "v": 45, "big": 0},
    {"type": "gold", "r": 25, "w": 1.6, "v": 90, "big": 0},
    {"type": "gold", "r": 32, "w": 0.7, "v": 160, "big": 1},
    {"type": "diamond", "r": 19, "w": 0.5, "v": 380, "big": 1},
    {"type": "diamond", "r": 12, "w": 0.9, "v": 200, "big": 0.7},
    {"type": "bag", "r": 22, "w": 0.8, "v": 130, "big": 0.7},
    {"type": "rock", "r": 27, "w": 1.2, "v": 3, "big": 0},
    {"type": "rock", "r": 38, "w": 0.8, "v": 3, "big": 0},
]
GOLDMINER_W = 860
GOLDMINER_H = 520
GOLDMINER_ITEMS = 26


def _gm_mulberry32(a):
    """前端 mulberry32 的 Python 复刻(32 位无符号运算,序列一致)"""
    def imul(x, y):
        return (x * y) & 0xFFFFFFFF

    def f():
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = imul(a ^ (a >> 15), 1 | a)
        old = t
        t = (old + imul(old ^ (old >> 7), 61 | old)) & 0xFFFFFFFF
        t = (t ^ old) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296
    return f


def gen_goldminer_world(seed):
    """复刻前端 genWorld:固定 seed 生成 26 个矿(位置+类型+分值)"""
    rnd = _gm_mulberry32(seed & 0xFFFFFFFF)
    items = []
    guard = 0
    while len(items) < GOLDMINER_ITEMS and guard < 700:
        guard += 1
        x = 50 + rnd() * (GOLDMINER_W - 100)
        y = 210 + rnd() * ((GOLDMINER_H - 40) - 210)
        total = sum(g["w"] * (1 + 0.5 * 4 if g["big"] else 1) for g in GOLDMINER_GEN)
        t = rnd() * total
        chosen = GOLDMINER_GEN[-1]
        for g in GOLDMINER_GEN:
            t -= g["w"] * (1 + 0.5 * 4 if g["big"] else 1)
            if t <= 0:
                chosen = g
                break
        r = chosen["r"]
        if any(((o["x"] - x) ** 2 + (o["y"] - y) ** 2) ** 0.5 < o["r"] + r + 6 for o in items):
            continue
        items.append({**chosen, "x": x, "y": y})
    return items


def judge_rhythm(chart, timeline):
    """复刻前端判定+计分:Perfect<60ms(100分)/Good<160ms(60分),连击加成,按谱面重判。
    chart: [{"t":秒,"lane":0..7}];timeline: [{"t":秒,"lane":0..7}](相对开局,不受时钟偏差影响)"""
    keys_used = set()
    perfect = good = miss = 0
    combo = 0
    score = 0
    for n in sorted(chart, key=lambda x: x["t"]):
        best = None
        for i, k in enumerate(timeline):
            if i in keys_used or k["lane"] != n["lane"]:
                continue
            dt = n["t"] - k["t"]
            if abs(dt) <= 0.16:
                if best is None or abs(dt) < abs(best[0]):
                    best = (dt, i)
        if best is None:
            miss += 1
            combo = 0
            continue
        keys_used.add(best[1])
        if abs(best[0]) < 0.06:
            perfect += 1
            base = 100
        else:
            good += 1
            base = 60
        combo += 1
        score += round(base * (1 + min(combo, 100) / 100))
    return perfect, good, miss, score

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
GOMOKU_ROOM_TTL = 300          # 等待房间无人加入 5 分钟自动删除
GOMOKU_TURN_TIMEOUT = 60       # 每步 60 秒未落子判负
GOMOKU_GAME_TIMEOUT = 1800     # 整局最长 30 分钟
GOMOKU_MIN_MOVES = 4           # 少于 4 步的对局不发奖励
GOMOKU_PAIR_WINDOW = 3600      # 重复对手统计窗口(秒)
GOMOKU_PAIR_LIMIT = 3          # 窗口内同一对手超过该局数则减半奖励
GOMOKU_DAILY_CAP = 300         # 五子棋每日奖励上限

# ============ Issue #28:玩法收益模型(常量集中 + 目标返奖率) ============
# 返奖率 rtp = 期望产出 / 门票。设计目标:
#   黄金矿工:门票回收玩法(高 rtp),靠每日 10 次次数限制封顶;
#   老虎机/转盘:抽奖玩法(rtp < 100%),期望上消耗积分(金币回收池),由每日上限与翻倍机制兜底防无界;
#   音乐:免费无门票,收益=得分,依赖玩家水平,rtp 不适用。
def _slot_expected_payout():
    """老虎机每注期望产出:三连 P(s)^3×x3 + 对子 2×PAIR×ΣP(s)²(1-P(s))"""
    total = sum(SLOT_SYMBOLS[s]["w"] for s in SLOT_SYMBOLS)
    p = {s: SLOT_SYMBOLS[s]["w"] / total for s in SLOT_SYMBOLS}
    e = sum(p[s] ** 3 * SLOT_SYMBOLS[s]["x3"] for s in SLOT_SYMBOLS)
    e += 2 * SLOT_PAIR_PAY * sum(p[s] ** 2 * (1 - p[s]) for s in SLOT_SYMBOLS)
    return round(e, 2)


def _slot_expected_var():
    """老虎机期望产出方差:Var = E[X²] - E[X]²"""
    total = sum(SLOT_SYMBOLS[s]["w"] for s in SLOT_SYMBOLS)
    p = {s: SLOT_SYMBOLS[s]["w"] / total for s in SLOT_SYMBOLS}
    e2 = sum(p[s] ** 3 * SLOT_SYMBOLS[s]["x3"] ** 2 for s in SLOT_SYMBOLS)
    e2 += 2 * SLOT_PAIR_PAY ** 2 * sum(p[s] ** 2 * (1 - p[s]) for s in SLOT_SYMBOLS)
    e = _slot_expected_payout()
    return round(e2 - e * e, 1)


def _wheel_expected_prize():
    """转盘每次期望产出:Σ(prize×weight)/Σweight("再转一次"按 prize=-1 计,期望值仍准确)"""
    total = sum(WHEEL_WEIGHTS)
    return round(sum(WHEEL_SECTORS[i]["prize"] * WHEEL_WEIGHTS[i] for i in range(len(WHEEL_SECTORS))) / total, 2)


def _wheel_expected_var():
    """转盘期望产出方差"""
    total = sum(WHEEL_WEIGHTS)
    e = _wheel_expected_prize()
    e2 = sum(WHEEL_SECTORS[i]["prize"] ** 2 * WHEEL_WEIGHTS[i] for i in range(len(WHEEL_SECTORS))) / total
    return round(e2 - e * e, 1)


GAME_ECONOMY = {
    # 黄金矿工:门票 80,奖励在 [pay_min,pay_max] 上均匀随机(期望 150,方差 850),
    # 返奖率 187.5%(高于 100%,属门票回收玩法,靠每日 10 局次数封顶防刷)
    "goldminer": {
        "ticket": GOLDMINER_TICKET,
        "pay_range": [GOLDMINER_PAY_MIN, GOLDMINER_PAY_MAX],
        "expected": round((GOLDMINER_PAY_MIN + GOLDMINER_PAY_MAX) / 2, 1),
        "variance": round(((GOLDMINER_PAY_MAX - GOLDMINER_PAY_MIN + 1) ** 2 - 1) / 12, 1),
        "daily_limit": GOLDMINER_DAILY_LIMIT,
        "rtp": round(((GOLDMINER_PAY_MIN + GOLDMINER_PAY_MAX) / 2) / GOLDMINER_TICKET, 3),
    },
    # 水果老虎机:门票 5,期望产出 ≈3.20(三连大奖 + 对子保底),返奖率 ≈64%
    # 目标设计:rtp 控制在 60%~70%,中奖体验频繁但对期望为负,配合每日 300 上限防套利
    "slot": {
        "ticket": SLOT_COST,
        "pay_range": [0, max(SLOT_SYMBOLS[s]["x3"] for s in SLOT_SYMBOLS)],
        "expected": _slot_expected_payout(),
        "variance": _slot_expected_var(),
        "daily_limit": SLOT_DAILY_MAX,
        "rtp": round(_slot_expected_payout() / SLOT_COST, 3),
    },
    # 幸运大转盘:门票 10,期望产出 ≈6.99,返奖率 ≈69.9%(低于 100%,抽奖类积分回收)
    # "再转一次"按概率 6% 额外赠券,实际期望再上浮约 6%×6.99≈0.42,仍为负期望设计
    "wheel": {
        "ticket": WHEEL_COST,
        "pay_range": [min(s["prize"] for s in WHEEL_SECTORS), max(s["prize"] for s in WHEEL_SECTORS)],
        "expected": _wheel_expected_prize(),
        "variance": _wheel_expected_var(),
        "daily_limit": None,   # 无独立日上限,受频率限制与全局每日可赚上限约束
        "rtp": round(_wheel_expected_prize() / WHEEL_COST, 3),
    },
    # 音乐游戏:免费(无门票),收益 = 得分直接入账,期望随玩家水平(全 Perfect ≈ 谱面满分,
    # 普通玩家约 3000~6000 分),rtp 不适用(无成本)
    "rhythm": {
        "ticket": 0,
        "pay_range": [0, None],
        "expected": None,
        "expected_range": [3000, 6000],
        "variance": None,
        "daily_limit": SUBMIT_PER_DAY,
        "rtp": None,
    },
}


def game_odds():
    """公开收益模型(/api/game/odds):各玩法门票/期望/方差/返奖率 + 农场作物收益-时长比。"""
    farm_crops = {}
    for c, info in CROPS.items():
        net = info["sell"] - info["cost"]
        farm_crops[c] = {
            "name": info["name"], "cost": info["cost"], "sell": info["sell"],
            "grow_sec": info["grow"], "net_profit": net,
            "profit_per_min": round(net / info["grow"] * 60, 2),   # 收益/时长比(每分钟净收益)
            "vip": bool(info.get("vip")),
        }
    # 短时(≤90s)/长时(>90s)作物收益对比
    short = [c for c, v in farm_crops.items() if v["grow_sec"] <= 90]
    long_ = [c for c, v in farm_crops.items() if v["grow_sec"] > 90]

    def _avg(keys):
        return round(sum(farm_crops[k]["profit_per_min"] for k in keys) / len(keys), 2) if keys else None

    return {
        "goldminer": {"name": "黄金矿工", **GAME_ECONOMY["goldminer"]},
        "slot": {"name": "水果老虎机", **GAME_ECONOMY["slot"]},
        "wheel": {"name": "幸运大转盘", **GAME_ECONOMY["wheel"]},
        "rhythm": {"name": "音乐游戏", **GAME_ECONOMY["rhythm"]},
        "farm": {
            "crops": farm_crops,
            "compare": {
                "short": {"label": "短时作物(≤90s)", "avg_profit_per_min": _avg(short), "crops": short},
                "long": {"label": "长时作物(>90s)", "avg_profit_per_min": _avg(long_), "crops": long_},
            },
            "note": "profit_per_min = (售价 - 种子价) / 生长秒数 × 60;短时作物周转快,长时作物单次利润高但占用地块久",
        },
    }


_lock = threading.RLock()  # 可重入锁：嵌套调用不会死锁

# 五子棋房间事件订阅（SSE 广播）
_room_subscribers = {}
_room_online = {}  # code -> set(user_id)，以活跃 SSE 长连接判定在线
_sub_lock = threading.Lock()


def _broadcast(code, state):
    with _sub_lock:
        for q in list(_room_subscribers.get(code, ())):
            try:
                q.put(state)
            except Exception:
                pass


def _online_ids(code):
    """房间当前在线玩家 id 集合（以活跃 SSE 长连接为准）"""
    with _sub_lock:
        return set(_room_online.get(code, ()))


def _subscribe(code, user_id=None):
    import queue
    q = queue.Queue(maxsize=50)
    with _sub_lock:
        _room_subscribers.setdefault(code, []).append(q)
        if user_id:
            _room_online.setdefault(code, set()).add(user_id)
    return q


def _unsubscribe(code, q, user_id=None):
    with _sub_lock:
        subs = _room_subscribers.get(code)
        if subs and q in subs:
            subs.remove(q)
            if not subs:
                _room_subscribers.pop(code, None)
        if user_id:
            ids = _room_online.get(code)
            if ids:
                ids.discard(user_id)
                if not ids:
                    _room_online.pop(code, None)


# ---------------- 数据库 ----------------
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # #17 SQLite WAL + busy_timeout + 外键(PostgreSQL 迁移见 app/db.py 的 DATABASE_URL 降级)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.Error:
        pass
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        conn.execute("""CREATE TABLE IF NOT EXISTS rate_limits(
            key TEXT PRIMARY KEY,
            count INTEGER NOT NULL,
            window_start REAL NOT NULL)""")
        # Issue #21:游戏参数配置(draft/published 版本链,发布/回滚保留历史)
        conn.execute("""CREATE TABLE IF NOT EXISTS game_configs(
            name TEXT NOT NULL,
            value TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'draft',
            updated_by TEXT,
            created_at REAL NOT NULL,
            PRIMARY KEY(name, version))""")
        # Issue #22:风险事件(异常对局/账号风险,等级+状态流转 pending→reviewed)
        conn.execute("""CREATE TABLE IF NOT EXISTS risk_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            rule TEXT NOT NULL,
            level TEXT NOT NULL DEFAULT 'medium',
            status TEXT NOT NULL DEFAULT 'pending',
            note TEXT,
            created_at REAL NOT NULL)""")
        # Issue #23:内容举报(漂流瓶/站内信)审核队列
        conn.execute("""CREATE TABLE IF NOT EXISTS reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_type TEXT NOT NULL,
            content_id INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            handled_by TEXT,
            note TEXT,
            created_at REAL NOT NULL)""")
        # Issue #24:管理员操作审计日志(高风险操作:封禁/解封、角色变更、调账、删除内容、审核处理、配置发布)
        conn.execute("""CREATE TABLE IF NOT EXISTS admin_audit(
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
            created_at REAL NOT NULL)""")
        # 老库迁移：漂流瓶 / 站内信隐藏标记(hide 后不出现在公开列表)
        _b_cols = [r["name"] for r in conn.execute("PRAGMA table_info(bottles)").fetchall()]
        if "hidden" not in _b_cols:
            conn.execute("ALTER TABLE bottles ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
        _m_cols = [r["name"] for r in conn.execute("PRAGMA table_info(mail)").fetchall()]
        if "hidden" not in _m_cols:
            conn.execute("ALTER TABLE mail ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
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


def admin_audit(conn, admin_id, admin_name, action, target=None, before_value=None,
                after_value=None, reason=None, request_id=None, ip=""):
    """记录管理员操作审计(高风险确认机制的落点)。
    调用方需在事务内插入;由后续 log()/conn.commit() 一并持久化。"""
    conn.execute(
        "INSERT INTO admin_audit(admin_id,admin_name,target,action,before_value,after_value,reason,request_id,ip,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (admin_id, admin_name, target, action, before_value, after_value, reason, request_id, ip, time.time()))


# ---------------- 后台辅助(#18/#19:仪表盘/用户详情脱敏) ----------------
def _mask_ip(ip):
    """隐私脱敏:IP 只显示前段(IPv4 前 2 段,IPv6 首个分组)"""
    if not ip:
        return ""
    ip = str(ip)
    if ":" in ip:
        return ip.split(":")[0] + ":*"
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:2]) + ".*.*"
    return ip[: len(ip) // 2] + "*"


def _avg_score(conn, game):
    r = conn.execute("SELECT AVG(score) s FROM scores WHERE game=?", (game,)).fetchone()
    return round(r["s"], 1) if r["s"] is not None else None


def _balance_dist(conn):
    """余额分布:按档位统计用户数"""
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


# ---------------- 频率限制(持久化存储,重启不清零,多实例共享) ----------------
def rate_check(key, limit, window, now=None):
    """窗口限流:DB 持久化。窗口过期自动重置,计数原子递增。"""
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


# ---------------- Issue #21:游戏参数配置(draft/published + 内存缓存) ----------------
# 可配置的关键参数(读取侧失败时回退这些硬编码默认值)
CONFIG_DEFAULTS = {
    "goldminer_ticket": GOLDMINER_TICKET,
    "goldminer_pay_min": GOLDMINER_PAY_MIN,
    "goldminer_pay_max": GOLDMINER_PAY_MAX,
    "slot_cost": SLOT_COST,
    "wheel_cost": WHEEL_COST,
    "daily_earned_cap": DAILY_EARNED_CAP,
}
CONFIG_DESCS = {
    "goldminer_ticket": "黄金矿工门票",
    "goldminer_pay_min": "黄金矿工单局保底奖励",
    "goldminer_pay_max": "黄金矿工单局最高奖励",
    "slot_cost": "老虎机单次费用",
    "wheel_cost": "转盘单次费用",
    "daily_earned_cap": "每日可赚积分上限",
}

_config_cache = {}
_config_cache_lock = threading.Lock()


def _parse_config_value(raw):
    s = str(raw).strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    try:
        return float(s)
    except ValueError:
        return s


def config_invalidate(name=None):
    """发布/回滚后使缓存失效(游戏逻辑立即读到新值)"""
    with _config_cache_lock:
        if name is None:
            _config_cache.clear()
        else:
            _config_cache.pop(name, None)


def config_get(name, default=None):
    """读取已发布参数(带内存缓存);无记录或异常时回退硬编码默认值。"""
    with _config_cache_lock:
        if name in _config_cache:
            return _config_cache[name]
    val = default
    try:
        with _lock, db() as conn:
            row = conn.execute(
                "SELECT value FROM game_configs WHERE name=? AND status='published' "
                "ORDER BY version DESC LIMIT 1", (name,)).fetchone()
        if row:
            val = _parse_config_value(row["value"])
    except Exception:
        val = default
    with _config_cache_lock:
        _config_cache[name] = val
    return val


def config_set(conn, name, value, operator):
    """写入 draft 版本(同名草稿覆盖;版本号 = 全表最大 + 1)。"""
    if name not in CONFIG_DEFAULTS:
        raise ValueError("未知参数")
    raw = str(value).strip()
    try:
        num = float(raw)
    except (TypeError, ValueError):
        raise ValueError("参数值必须是数字")
    if num <= 0 or num != int(num):
        raise ValueError("参数值必须为正整数")
    num = int(num)
    if name == "goldminer_pay_min":
        mx = config_get("goldminer_pay_max", GOLDMINER_PAY_MAX)
        if mx is not None and num > mx:
            raise ValueError("保底奖励不能大于最高奖励")
    if name == "goldminer_pay_max":
        mn = config_get("goldminer_pay_min", GOLDMINER_PAY_MIN)
        if mn is not None and num < mn:
            raise ValueError("最高奖励不能小于保底奖励")
    value_str = str(num)
    draft = conn.execute(
        "SELECT version FROM game_configs WHERE name=? AND status='draft' ORDER BY version DESC LIMIT 1",
        (name,)).fetchone()
    now = time.time()
    if draft:
        conn.execute("UPDATE game_configs SET value=?, updated_by=?, created_at=? "
                     "WHERE name=? AND version=? AND status='draft'",
                     (value_str, operator, now, name, draft["version"]))
        conn.commit()
        return draft["version"]
    ver = conn.execute("SELECT COALESCE(MAX(version),0) v FROM game_configs WHERE name=?",
                       (name,)).fetchone()["v"] + 1
    conn.execute("INSERT INTO game_configs(name,value,version,status,updated_by,created_at) "
                 "VALUES(?,?,?,?,?,?)",
                 (name, value_str, ver, "draft", operator, now))
    conn.commit()
    return ver


def config_publish(conn, name, operator):
    """发布 draft → published(缓存失效,新开局即按新值扣费/发奖)。"""
    if name not in CONFIG_DEFAULTS:
        raise ValueError("未知参数")
    draft = conn.execute(
        "SELECT * FROM game_configs WHERE name=? AND status='draft' ORDER BY version DESC LIMIT 1",
        (name,)).fetchone()
    if not draft:
        raise ValueError("没有待发布的草稿")
    conn.execute("UPDATE game_configs SET status='published', updated_by=? WHERE name=? AND version=?",
                 (operator, name, draft["version"]))
    conn.commit()
    config_invalidate(name)
    return draft["version"]


def config_rollback(conn, name, operator):
    """回滚:发布上一个 published 版本的值(首次发布前回退硬编码默认值)。"""
    if name not in CONFIG_DEFAULTS:
        raise ValueError("未知参数")
    active = conn.execute(
        "SELECT * FROM game_configs WHERE name=? AND status='published' ORDER BY version DESC LIMIT 1",
        (name,)).fetchone()
    prev_value = None
    if active:
        prev = conn.execute(
            "SELECT value FROM game_configs WHERE name=? AND status='published' AND version<? "
            "ORDER BY version DESC LIMIT 1", (name, active["version"])).fetchone()
        if prev:
            prev_value = prev["value"]
    if prev_value is None:
        prev_value = str(CONFIG_DEFAULTS[name])
    ver = conn.execute("SELECT COALESCE(MAX(version),0) v FROM game_configs WHERE name=?",
                       (name,)).fetchone()["v"] + 1
    conn.execute("INSERT INTO game_configs(name,value,version,status,updated_by,created_at) "
                 "VALUES(?,?,?,?,?,?)",
                 (name, prev_value, ver, "published", operator, time.time()))
    conn.commit()
    config_invalidate(name)
    return {"version": ver, "value": prev_value}


def _admin_config_list(conn):
    """参数配置列表:每个参数给出默认值 / 当前已发布值 / 待发布草稿。"""
    items = []
    for name in CONFIG_DEFAULTS:
        rows = conn.execute(
            "SELECT * FROM game_configs WHERE name=? ORDER BY version ASC", (name,)).fetchall()
        active = None
        active_version = None
        draft = None
        for r in rows:
            if r["status"] == "published":
                active = _parse_config_value(r["value"])
                active_version = r["version"]
            elif r["status"] == "draft":
                draft = {"value": _parse_config_value(r["value"]), "version": r["version"],
                         "updated_by": r["updated_by"], "updated_at": r["created_at"]}
        items.append({
            "name": name,
            "desc": CONFIG_DESCS.get(name, name),
            "default": CONFIG_DEFAULTS[name],
            "value": active if active is not None else CONFIG_DEFAULTS[name],
            "published_version": active_version,
            "draft": draft,
        })
    return {"list": items, "defaults": dict(CONFIG_DEFAULTS)}


# ---------------- Issue #20:积分经济中心 / 通胀告警(数据与 point_ledger 流水总和一致) ----------------
def _admin_economy(conn):
    day_start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))

    def _sum_col(case):
        return conn.execute(
            f"SELECT COALESCE(SUM({case}),0) s FROM point_ledger WHERE created_at>=?",
            (day_start,)).fetchone()["s"]

    # by_business:按业务聚合(全量累计,金额入/出)
    by_business = {}
    for r in conn.execute(
            "SELECT business, "
            "SUM(CASE WHEN amount>0 THEN amount ELSE 0 END) AS amount_in, "
            "SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END) AS amount_out "
            "FROM point_ledger GROUP BY business").fetchall():
        by_business[r["business"]] = {"amount_in": r["amount_in"], "amount_out": r["amount_out"]}

    # daily_net:最近 14 天(含今天)
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

    # distribution:余额分布(0-100/100-1000/1000-10000/1万+)
    tiers = [("0~100", 0, 100), ("100~1000", 100, 1000), ("1000~10000", 1000, 10000), ("10000+", 10000, None)]
    distribution = []
    for label, lo, hi in tiers:
        if hi is None:
            c = conn.execute("SELECT COUNT(*) c FROM users WHERE points>=?", (lo,)).fetchone()["c"]
        else:
            c = conn.execute("SELECT COUNT(*) c FROM users WHERE points>=? AND points<?",
                             (lo, hi)).fetchone()["c"]
        distribution.append({"range": label, "users": c})

    # alerts:异常增长 / 来源突增 / 总量阈值
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


def gomoku_award(conn, user_id, username, amount, detail, ip, idem_key=None):
    points = change_points(conn, user_id, username, amount, "gomoku_award", detail, ip, idem_key=idem_key)
    return points


def _gomoku_daily_earned(conn, username, today):
    """五子棋今日已获得积分（以 gomoku_award 日志聚合，持久化可靠）"""
    day_start = time.mktime(time.strptime(today, "%Y-%m-%d"))
    row = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM logs "
                       "WHERE username=? AND action='gomoku_award' AND at>=?",
                       (username, day_start)).fetchone()
    return row["s"]


def _add_risk_event(conn, user_id, username, rule, level="medium", note=""):
    """写入风险事件(异常对局/账号风险中心)。默认 pending,由管理员 review 后置 reviewed。"""
    conn.execute(
        "INSERT INTO risk_events(user_id,username,rule,level,status,note,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (user_id, username, rule, level, "pending", note, time.time()))


def _gomoku_risk_check(conn, row, moves):
    """对局风控：过短对局 / 同 IP / 重复对手。返回 (命中列表, 奖励系数)。"""
    risks, mult = [], 1.0
    pb, pw = row["player_black"], row["player_white"]
    if moves < GOMOKU_MIN_MOVES:
        risks.append("too_short")
        mult = 0.0
    if row["ip_black"] and row["ip_white"] and row["ip_black"] == row["ip_white"]:
        risks.append("same_ip")
        mult = 0.0
    if pb and pw:
        n = conn.execute(
            """SELECT COUNT(*) c FROM gomoku_games
               WHERE ((player_black=? AND player_white=?) OR (player_black=? AND player_white=?)) AND at>?""",
            (pb, pw, pw, pb, time.time() - GOMOKU_PAIR_WINDOW)).fetchone()["c"]
        if n >= GOMOKU_PAIR_LIMIT:
            risks.append("repeat_opponent")
            mult = min(mult, 0.5)
    return risks, mult


def _gomoku_pay(conn, uid, base, code, role, mult, today, ip, reason, detail):
    """给一名玩家发五子棋奖励（受风控系数 + 每日上限约束），返回实际发放额。"""
    if not uid or uid == 0 or base <= 0:
        return 0
    u = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        return 0
    amount = int(round(base * mult))
    if amount <= 0:
        return 0
    remain = GOMOKU_DAILY_CAP - _gomoku_daily_earned(conn, u["username"], today)
    if remain <= 0:
        return 0
    amount = min(amount, remain)
    if amount <= 0:
        return 0
    gomoku_award(conn, uid, u["username"], amount, f"{detail} ({code})", ip,
                 idem_key=f"gomoku:{code}:{role}:{uid}")
    return amount


def _finish_gomoku(conn, code, winner, reason, ip="", loser=None):
    """结算五子棋：状态变更、历史记录与奖励发放同属一次调用（同一连接/事务内）。
    winner: 玩家 id / 0=AI 获胜 / None=平局
    reason: 'normal'(五连) / 'resign'(认输) / 'timeout'(超时) / 'draw'(和棋)
    幂等：rewarded 标记 + WHERE rewarded=0 双重守卫，重复触发不重复发奖。
    保存胜方(winner)、负方(loser)、结束原因(reason)。"""
    row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
    if not row or row["rewarded"]:
        return row
    now = time.time()
    cur = conn.execute(
        "UPDATE gomoku_rooms SET status='over', winner=?, reason=?, rewarded=1, last_move_at=? "
        "WHERE code=? AND rewarded=0", (winner, reason, now, code))
    if cur.rowcount == 0:
        return conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
    pb, pw = row["player_black"], row["player_white"]
    is_bot = pw == 0
    if winner is None:
        result = "draw"
    elif winner == 0:
        result = "white"
    else:
        result = "black" if winner == pb else ("white" if winner == pw else "draw")
    if loser is None and winner not in (None, 0):
        loser = pw if winner == pb else pb
    elif winner == 0 and loser is None and pb:
        loser = pb
    moves = row["moves"] or sum(1 for c in (json.loads(row["board"]) or []) if c)
    risks, mult = _gomoku_risk_check(conn, row, moves)
    # Issue #22:命中风控(同 IP / 重复对手)写入风险事件,双方玩家各记一条
    risk_rule_map = {"same_ip": ("gomoku_same_ip", "high", "对局双方同 IP"),
                     "repeat_opponent": ("gomoku_repeat", "medium", "窗口内重复对手")}
    for risk_name in risks:
        if risk_name in risk_rule_map:
            rule, level, label = risk_rule_map[risk_name]
            for uid in (pb, pw):
                if uid and uid != 0:
                    u = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
                    if u:
                        _add_risk_event(conn, uid, u["username"], rule, level,
                                        f"房间{code} {label}（{result}）")
    conn.execute(
        "INSERT INTO gomoku_games(code,player_black,player_white,winner,loser,result,reason,moves,risk,at,ended_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (code, pb, pw, winner, loser, result, reason, moves,
         json.dumps(risks, ensure_ascii=False), row["created_at"], now))
    today = time.strftime("%Y-%m-%d")
    if winner is None:
        if not is_bot and pw:
            _gomoku_pay(conn, pb, GOMOKU_DRAW_POINTS, code, "draw_b", mult, today, ip, reason, "五子棋平局")
            _gomoku_pay(conn, pw, GOMOKU_DRAW_POINTS, code, "draw_w", mult, today, ip, reason, "五子棋平局")
    elif winner == 0:
        if pb:
            _gomoku_pay(conn, pb, GOMOKU_LOSE_POINTS, code, "lose", mult, today, ip, reason, "五子棋输给AI")
    else:
        _gomoku_pay(conn, winner, GOMOKU_WIN_POINTS, code, "win", mult, today, ip, reason, "五子棋获胜")
        if loser:
            _gomoku_pay(conn, loser, GOMOKU_LOSE_POINTS, code, "lose", mult, today, ip, reason, "五子棋参与")
    conn.commit()
    return row


def _gomoku_check_timeout(conn, row, now=None):
    """进行中对局超时判定：回合超时（每步）或整局超时，超时方=当前轮到的一方，判负。
    返回是否已结算（此时对局状态为 over）。"""
    now = now or time.time()
    if row["status"] != "playing":
        return False
    anchor = row["last_move_at"] or row["started_at"] or row["created_at"]
    if not anchor:
        return False
    turn_over = now - anchor >= GOMOKU_TURN_TIMEOUT
    game_over = bool(row["started_at"]) and now - row["started_at"] >= GOMOKU_GAME_TIMEOUT
    if not turn_over and not game_over:
        return False
    loser = row["player_black"] if row["turn"] == 1 else row["player_white"]
    winner = row["player_white"] if row["turn"] == 1 else row["player_black"]
    _finish_gomoku(conn, row["code"], winner, "timeout", "", loser=loser)
    return True


def _gomoku_cleanup_loop():
    """后台循环：过期等待房间自动删除 + 超时对局自动判负（SSE 广播刷新）。"""
    while True:
        time.sleep(10)
        try:
            with _lock, db() as conn:
                now = time.time()
                conn.execute("DELETE FROM gomoku_rooms WHERE status='waiting' AND created_at<?",
                             (now - GOMOKU_ROOM_TTL,))
                conn.commit()
                rows = conn.execute(
                    "SELECT * FROM gomoku_rooms WHERE status='playing' "
                    "AND ((last_move_at IS NOT NULL AND last_move_at<?) "
                    "OR (started_at IS NOT NULL AND started_at<?))",
                    (now - GOMOKU_TURN_TIMEOUT, now - GOMOKU_GAME_TIMEOUT)).fetchall()
                settled = []
                for r in rows:
                    if _gomoku_check_timeout(conn, r, now):
                        settled.append(r["code"])
                conn.commit()
            for c in settled:
                _broadcast(c, None)
        except Exception:
            pass


def gomoku_state(row, my_id, conn=None):
    """房间状态。player_black/player_white 为用户 ID(数字)或 0=AI；
    black_online/white_online 为布尔(以 SSE 长连接判在线)。"""
    try:
        board = json.loads(row["board"])
    except Exception:
        board = gomoku_new_board()
    pb, pw = row["player_black"], row["player_white"]
    mine = "black" if my_id == pb else ("white" if my_id == pw else None)
    online = _online_ids(row["code"])
    black_online = pb is not None and pb in online
    white_online = pw is not None and pw in online
    black_name = white_name = None
    if conn is not None:
        if pb:
            r = conn.execute("SELECT username FROM users WHERE id=?", (pb,)).fetchone()
            black_name = r["username"] if r else None
        if pw:
            r = conn.execute("SELECT username FROM users WHERE id=?", (pw,)).fetchone()
            white_name = r["username"] if r else None
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
        "player_black": pb,
        "player_white": pw,
        "black_online": black_online,
        "white_online": white_online,
        "black_name": black_name,
        "white_name": white_name,
        "can_join": row["status"] == "waiting" and pb != my_id,
        "can_move": row["status"] == "playing" and mine is not None
                    and ((row["turn"] == 1 and mine == "black") or (row["turn"] == 2 and mine == "white")),
    }


def _gomoku_bot_turn(code):
    """AI 落子（延迟一点更像真人）"""
    time.sleep(0.55)
    with _lock, db() as conn:
        row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
        if not row or row["status"] != "playing" or row["turn"] != 2 or row["player_white"]:
            return  # 非 bot 局或有真人白方则不行动
        if _gomoku_check_timeout(conn, row):
            conn.commit()
            _broadcast(code, None)
            return
        board = json.loads(row["board"])
        mv = gomoku_bot_move(board)
        if mv is None:
            _finish_gomoku(conn, code, None, "draw", "")
            conn.commit()
            _broadcast(code, None)
            return
        x, y = mv
        board[y * GOMOKU_SIZE + x] = 2
        won = gomoku_win(board, x, y, 2)
        full = gomoku_full(board)
        if won or full:
            conn.execute("UPDATE gomoku_rooms SET board=?, turn=1, moves=moves+1 WHERE code=?",
                         (json.dumps(board), code))
            _finish_gomoku(conn, code, 0 if won else None, "normal" if won else "draw", "")
            conn.commit()
        else:
            conn.execute("UPDATE gomoku_rooms SET board=?, turn=1, last_move_at=?, moves=moves+1 WHERE code=?",
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
    change_points(conn, uid, username, -config_get("slot_cost", SLOT_COST),
                  "slot_spin", f"拉杆 {'中奖'+str(pay) if pay else '未中奖'}", ip)
    token = None
    if pay > 0:
        token = secrets.token_hex(16)
        conn.execute("INSERT INTO slot_pending(token,user_id,pending,created_at) VALUES(?,?,?,?)",
                     (token, uid, min(pay, SLOT_PENDING_MAX), time.time()))
        conn.commit()
    return reel, pay, token


def slot_collect(conn, uid, username, token, ip):
    """领取待结算奖励（含翻倍后），受每日 300 上限约束。状态机: pending → credited"""
    row = conn.execute("SELECT * FROM slot_pending WHERE token=? AND user_id=? AND status='pending'",
                       (token, uid)).fetchone()
    if not row:
        return None, None
    today = time.strftime("%Y-%m-%d")
    remain = SLOT_DAILY_MAX - slot_daily_earned(username, today)
    pending = min(row["pending"], SLOT_PENDING_MAX)
    if pending > remain:
        pending = max(0, remain)
    cur = conn.execute("UPDATE slot_pending SET status='credited' WHERE token=? AND status='pending'",
                       (token,))
    if cur.rowcount == 0:
        return None, None   # 已被并发领取/清理，避免重复入账
    conn.commit()
    if pending > 0:
        points = change_points(conn, uid, username, pending, "slot_win",
                               f"老虎机领取奖励 {pending}", ip,
                               idem_key=f"slot_credit:{token}")
        add_slot_daily_earned(username, pending, today)
        add_daily_earned(username, pending, today)
        return points, pending
    return conn.execute("SELECT points FROM users WHERE id=?", (uid,)).fetchone()["points"], 0


def slot_cleanup(conn):
    """过期待结算自动入账(不删除丢失)。状态机 pending → credited，单事务内处理，可重试且幂等。
    幂等双保险:① UPDATE 仅对 status='pending' 生效(rowcount 守卫);
    ② change_points 以 idem_key=slot_credit:{token} 写入 point_ledger,重复执行不会二次入账。"""
    rows = conn.execute("SELECT * FROM slot_pending WHERE status='pending' AND created_at<?",
                        (time.time() - SLOT_PENDING_TTL,)).fetchall()
    for r in rows:
        u = conn.execute("SELECT * FROM users WHERE id=?", (r["user_id"],)).fetchone()
        if not u:
            continue
        cur = conn.execute("UPDATE slot_pending SET status='credited' WHERE token=? AND status='pending'",
                           (r["token"],))
        if cur.rowcount == 0:
            continue   # 已被并发清理/领取
        pending = min(r["pending"], SLOT_PENDING_MAX)
        change_points(conn, r["user_id"], u["username"], pending,
                      "slot_win", f"老虎机过期待结算自动入账 {pending}", "",
                      idem_key=f"slot_credit:{r['token']}")
        add_slot_daily_earned(u["username"], pending, time.strftime("%Y-%m-%d"))
        add_daily_earned(u["username"], pending, time.strftime("%Y-%m-%d"))
    conn.commit()


def wheel_free_left(conn, user_id):
    """当前用户未过期、未使用的免费转券数量"""
    return conn.execute(
        "SELECT COUNT(*) c FROM wheel_free_tickets WHERE user_id=? AND used=0 AND expires_at>?",
        (user_id, time.time())).fetchone()["c"]


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
        cookie = getattr(self, "_session_cookie", None)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cookie_token(self):
        """从 Cookie 中解析会话 token(仅取 gs_session,不信任其它值)"""
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith(SESSION_COOKIE + "="):
                return part[len(SESSION_COOKIE) + 1:]
        return ""

    def _set_session_cookie(self, token):
        self._session_cookie = (f"{SESSION_COOKIE}={token}; HttpOnly; Path=/; SameSite=Lax; "
                                f"Max-Age={LOGIN_SESSION_DAYS * 86400}")

    def _clear_session_cookie(self):
        self._session_cookie = f"{SESSION_COOKIE}=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0"

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
        if not token:
            token = self._cookie_token()
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
                                    "daily_cap": config_get("daily_earned_cap", DAILY_EARNED_CAP)})

        if path == "/api/game/odds":
            # 公开接口:玩法收益模型(门票/期望/方差/返奖率/农场收益时长比),无需登录
            return self._send(200, game_odds())

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
                free_tickets = wheel_free_left(conn, user["id"])
            return self._send(200, {
                "total": total,
                "my_spins": my,
                "win_rate": round(win / total * 100, 1) if total else 0,
                "jackpots": jackpots,
                "my_recent": my_recent,
                "free_tickets": free_tickets,
            })

        if path == "/api/mail":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            with _lock, db() as conn:
                rows = conn.execute(
                    """SELECT m.*, COALESCE(u.username,'系统') AS from_name
                       FROM mail m LEFT JOIN users u ON u.id=m.from_id
                       WHERE m.to_id=? AND m.hidden=0 ORDER BY m.id DESC LIMIT 100""", (user["id"],)).fetchall()
                out = []
                for r in rows:
                    d = dict(r)
                    d["contains_sensitive"] = _contains_sensitive((r["title"] or "") + (r["content"] or ""))
                    out.append(d)
            return self._send(200, {"list": out})

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
                    "SELECT id, username, content, created_at, views FROM bottles "
                    "WHERE hidden=0 ORDER BY id DESC LIMIT 15").fetchall()
                out = []
                for r in rows:
                    d = dict(r)
                    d["contains_sensitive"] = _contains_sensitive(r["content"])
                    out.append(d)
            return self._send(200, {"list": out, "cost": BOTTLE_COST})

        if path == "/api/bottle/pick":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            if not rate_check(f"bottlepick:{user['username']}", BOTTLE_PICK_DAILY, 86400):
                return self._send(429, {"error": f"每天最多捡 {BOTTLE_PICK_DAILY} 个漂流瓶，明天再来吧"})
            with _lock, db() as conn:
                row = conn.execute(
                    """SELECT * FROM bottles WHERE picked=0 AND user_id<>? AND hidden=0
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

        if path == "/api/admin/dashboard":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            day_start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
            with _lock, db() as conn:
                dau = conn.execute(
                    "SELECT COUNT(DISTINCT user_id) c FROM ("
                    " SELECT user_id FROM logs WHERE at>=? AND user_id IS NOT NULL"
                    " UNION SELECT user_id FROM sessions WHERE created_at>=?) t",
                    (day_start, day_start)).fetchone()["c"]
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
                for game, (sa, fa) in {"slot": ("slot_spin", "slot_win"),
                                       "wheel": ("wheel_spin", "wheel_spin"),
                                       "gomoku": ("gomoku_create", None)}.items():
                    starts = conn.execute("SELECT COUNT(*) c FROM logs WHERE action=? AND at>=?",
                                          (sa, day_start)).fetchone()["c"]
                    if fa:
                        finishes = conn.execute("SELECT COUNT(*) c FROM logs WHERE action=? AND at>=?",
                                                (fa, day_start)).fetchone()["c"]
                    else:
                        finishes = conn.execute("SELECT COUNT(*) c FROM gomoku_games WHERE at>=?",
                                                (day_start,)).fetchone()["c"]
                    games[game] = {"starts": starts, "finishes": finishes, "avg_score": _avg_score(conn, game)}
                produced = conn.execute(
                    "SELECT COALESCE(SUM(amount),0) s FROM point_ledger WHERE amount>0 AND created_at>=?",
                    (day_start,)).fetchone()["s"]
                consumed = -conn.execute(
                    "SELECT COALESCE(SUM(amount),0) s FROM point_ledger WHERE amount<0 AND created_at>=?",
                    (day_start,)).fetchone()["s"]
                pl_rows = conn.execute(
                    "SELECT business, COUNT(*) n, COALESCE(SUM(amount),0) s FROM point_ledger "
                    "WHERE created_at>=? GROUP BY business ORDER BY n DESC", (day_start,)).fetchall()
                balance_dist = _balance_dist(conn)
            return self._send(200, {
                "dau": dau, "new_users": new_users, "games": games,
                "economy": {"produced": produced, "consumed": consumed, "net": produced - consumed,
                            "point_ledger": {"today": sum(r["n"] for r in pl_rows),
                                             "by_business": [{"business": r["business"], "count": r["n"],
                                                              "amount": r["s"]} for r in pl_rows]}},
                "users_total": users_total, "points_total": points_total,
                "balance_dist": balance_dist})

        if path == "/api/admin/user-detail":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            name = (q.get("name") or [""])[0].strip()
            if not name:
                return self._send(400, {"error": "缺少用户名"})
            with _lock, db() as conn:
                target = get_user_by_name(conn, name)
                if not target:
                    return self._send(400, {"error": "用户不存在"})
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
            return self._send(200, {
                "user": {k: target[k] for k in ("id", "username", "points", "role", "status",
                                                "created_at", "last_login", "vip_until", "exp", "steal_open")},
                "ledger": ledger, "scores": scores, "sessions": sessions,
                "ban_history": ban_history, "recent_logs": recent_logs})

        if path == "/api/admin/bottles":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            with _lock, db() as conn:
                rows = conn.execute("SELECT * FROM bottles ORDER BY id DESC LIMIT 100").fetchall()
            return self._send(200, {"list": [dict(r) for r in rows]})

        if path == "/api/admin/risk-list":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            status = (q.get("status") or [""])[0].strip()
            with _lock, db() as conn:
                cond, args = [], []
                if status in ("pending", "reviewed"):
                    cond.append("status=?")
                    args.append(status)
                where = (" WHERE " + " AND ".join(cond)) if cond else ""
                rows = conn.execute(
                    f"SELECT * FROM risk_events{where} ORDER BY id DESC LIMIT 200", args).fetchall()
                summary = conn.execute(
                    """SELECT user_id, username, COUNT(*) cnt,
                       SUM(CASE WHEN level='high' THEN 1 ELSE 0 END) high,
                       SUM(CASE WHEN level='medium' THEN 1 ELSE 0 END) medium,
                       SUM(CASE WHEN level='low' THEN 1 ELSE 0 END) low,
                       SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) pending
                       FROM risk_events GROUP BY user_id, username ORDER BY cnt DESC""").fetchall()
            return self._send(200, {"list": [dict(r) for r in rows],
                                    "summary": [dict(r) for r in summary]})

        if path == "/api/admin/report-list":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            status = (q.get("status") or [""])[0].strip()
            with _lock, db() as conn:
                cond, args = ["1=1"], []
                if status in ("pending", "handled", "rejected"):
                    cond.append("r.status=?")
                    args.append(status)
                rows = conn.execute(
                    """SELECT r.*, COALESCE(u.username,'-') AS reporter_name
                       FROM reports r LEFT JOIN users u ON u.id=r.reporter_id
                       WHERE {where} ORDER BY r.id DESC LIMIT 200""".format(where=" AND ".join(cond)),
                    args).fetchall()
                out = []
                for r in rows:
                    d = dict(r)
                    if r["content_type"] == "bottle":
                        b = conn.execute("SELECT user_id, username, content, hidden FROM bottles WHERE id=?",
                                         (r["content_id"],)).fetchone()
                        d["content"] = b["content"] if b else None
                        d["content_owner"] = b["username"] if b else None
                        d["user_id"] = b["user_id"] if b else None
                        d["hidden"] = bool(b["hidden"]) if b else None
                    else:
                        m = conn.execute("SELECT from_id, title, content, hidden FROM mail WHERE id=?",
                                         (r["content_id"],)).fetchone()
                        d["content"] = (m["title"] + "：" + m["content"]) if m else None
                        d["content_owner"] = None
                        d["user_id"] = m["from_id"] if m else None
                        d["hidden"] = bool(m["hidden"]) if m else None
                        if m and m["from_id"]:
                            fr = conn.execute("SELECT username FROM users WHERE id=?", (m["from_id"],)).fetchone()
                            d["content_owner"] = fr["username"] if fr else None
                    out.append(d)
            return self._send(200, {"list": out})

        if path == "/api/admin/audit-list":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            admin_name = (q.get("admin") or [""])[0].strip()
            target = (q.get("target") or [""])[0].strip()
            action = (q.get("action") or [""])[0].strip()
            cond, args = ["1=1"], []
            if admin_name:
                cond.append("admin_name=?")
                args.append(admin_name)
            if target:
                cond.append("target=?")
                args.append(target)
            if action:
                cond.append("action=?")
                args.append(action)
            with _lock, db() as conn:
                total = conn.execute(
                    f"SELECT COUNT(*) c FROM admin_audit WHERE {' AND '.join(cond)}", args).fetchone()["c"]
                rows = conn.execute(
                    f"""SELECT * FROM admin_audit WHERE {' AND '.join(cond)}
                        ORDER BY id DESC LIMIT 100""", args).fetchall()
                actions = [r["action"] for r in conn.execute(
                    "SELECT DISTINCT action FROM admin_audit ORDER BY action").fetchall()]
            return self._send(200, {"list": [dict(r) for r in rows], "total": total, "actions": actions})

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

        if path == "/api/admin/economy":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            with _lock, db() as conn:
                data = _admin_economy(conn)
            return self._send(200, data)

        if path == "/api/admin/config/list":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            with _lock, db() as conn:
                data = _admin_config_list(conn)
            return self._send(200, data)

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
                now = time.time()
                if row["status"] == "waiting" and now - row["created_at"] >= GOMOKU_ROOM_TTL:
                    conn.execute("DELETE FROM gomoku_rooms WHERE code=?", (code,))
                    conn.commit()
                    return self._send(404, {"error": "房间已过期"})
                if row["status"] == "playing":
                    _gomoku_check_timeout(conn, row, now)
                    conn.commit()
                    row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
                state = gomoku_state(row, user["id"], conn)
            return self._send(200, state)

        if path == "/api/gomoku/stream":
            code = (q.get("code") or [""])[0].strip().upper()
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            with _lock, db() as conn:
                row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
                if not row:
                    return self._send(404, {"error": "房间不存在"})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = _subscribe(code, user["id"])
            try:
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                _broadcast(code, None)  # 上线通知，让对手刷新在线状态
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
                        state = gomoku_state(row, user["id"], conn) if row else None
                    payload = json.dumps(state, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                _unsubscribe(code, q, user["id"])
                _broadcast(code, None)  # 离线通知
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
        """统一入口:捕获积分不足(ValueError→400)与未预期异常(→500),事务整体回滚"""
        try:
            return self._do_post()
        except ValueError as e:
            try:
                return self._send(400, {"error": str(e)})
            except Exception:
                pass
        except Exception as e:
            try:
                self.log_error("POST %s: %s", self.path, repr(e)[:300])
                return self._send(500, {"error": "服务器内部错误"})
            except Exception:
                pass

    def _do_post(self):
        path = urllib.parse.urlparse(self.path).path
        data = self._body()
        if data is None:
            return
        ip = self._ip()
        request_id = str(data.get("request_id", "")).strip() or secrets.token_hex(8)

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
                # 注册永远创建普通用户;仅当用户名命中预设管理员名单(ADMIN_USERS / ADMIN_INIT)时设为 admin
                role = "admin" if username in ADMIN_USERS or username in ADMIN_INIT else "user"
                cur = conn.execute("INSERT INTO users(username,password,salt,points,role,created_at) VALUES(?,?,?,?,?,?)",
                                   (username, hash_pw(password, salt), salt, WELCOME_POINTS, role, time.time()))
                uid = cur.lastrowid
                token = new_session(conn, uid, ip)
                self._set_session_cookie(token)
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
                self._set_session_cookie(token)
                conn.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), row["id"]))
                conn.commit()
                log(conn, row["id"], username, "login", "登录成功", ip=ip)
            return self._send(200, {"ok": True, "token": token, "user": {
                "id": row["id"], "username": username, "points": row["points"], "role": row["role"]}})

        if path == "/api/logout":
            token = (self.headers.get("X-Token") or "").strip()
            if not token:
                token = self._cookie_token()
            with _lock, db() as conn:
                row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
                if row:
                    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
                    conn.commit()
                    u = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
                    log(conn, row["user_id"], u["username"] if u else "?", "logout", "退出登录", ip=ip)
            self._clear_session_cookie()
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
                if daily_earned(user["username"], today_str) + reward > config_get("daily_earned_cap", DAILY_EARNED_CAP):
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
                ticket = config_get("goldminer_ticket", GOLDMINER_TICKET)
                if user["points"] < ticket:
                    return self._send(400, {"error": f"门票需要 {ticket} 积分"})
                with _lock, db() as conn:
                    change_points(conn, user["id"], user["username"], -ticket,
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
                                    "goldminer_seed": seed if game == "goldminer" else None,
                                    "duration": GAMES[game]["duration"],
                                    "ticket": ticket if game == "goldminer" else 0,
                                    "daily_left": played if game == "goldminer" else None,
                                    "limits": {"hour": SUBMIT_PER_HOUR, "day": SUBMIT_PER_DAY,
                                               "daily_cap": config_get("daily_earned_cap", DAILY_EARNED_CAP)}})

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
                # Issue #22:单小时提交结算次数超限 → 风险事件
                with _lock, db() as conn:
                    _add_risk_event(conn, user["id"], user["username"], "submit_burst", "medium",
                                    f"{GAMES[game]['name']} 单小时提交结算超限")
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
                    # 服务器按谱面重判按键时间线(忽略客户端统计,防伪造满分)
                    timeline = stats.get("timeline")
                    if not isinstance(timeline, list) or len(timeline) > 2000:
                        return self._send(400, {"error": "按键数据异常，提交被拒绝"})
                    tl = []
                    for x in timeline:
                        try:
                            t = float(x.get("t", -1))
                            lane = int(x.get("lane", -1))
                        except Exception:
                            return self._send(400, {"error": "按键数据异常，提交被拒绝"})
                        if 0 <= t <= 85 and 0 <= lane < 8:
                            tl.append({"t": t, "lane": lane})
                    p, g, m, server_score = judge_rhythm(chart, tl)
                    if p + g + m != len(chart):
                        pass  # 允许早退(未按键的音符记 miss 已在重判内)
                    score = min(server_score, max_score)
                earned = min(score, max_score)
                if game == "goldminer":
                    # 服务器用 seed 重算地图,校验抓取轨迹(防伪造分数)
                    catches = stats.get("catches")
                    if not isinstance(catches, list) or len(catches) > GOLDMINER_ITEMS:
                        return self._send(400, {"error": "抓取数据异常"})
                    world = gen_goldminer_world(sess["seed"])
                    avail = {}
                    for it in world:
                        avail[it["v"]] = avail.get(it["v"], 0) + 1
                    total_v = 0
                    for c in catches:
                        try:
                            v = int(c.get("v", -1))
                        except Exception:
                            return self._send(400, {"error": "抓取数据异常"})
                        if avail.get(v, 0) <= 0:
                            return self._send(400, {"error": "抓取数据与地图不符"})
                        avail[v] -= 1
                        total_v += v
                    if score != total_v:
                        return self._send(400, {"error": "分数与抓取记录不符"})
                    earned = random.randint(config_get("goldminer_pay_min", GOLDMINER_PAY_MIN),
                                            config_get("goldminer_pay_max", GOLDMINER_PAY_MAX))
                daily_cap = config_get("daily_earned_cap", DAILY_EARNED_CAP)
                if daily_earned(user["username"], today) + earned > daily_cap:
                    return self._send(400, {"error": f"今日可赚积分已达上限（{daily_cap}）"})
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
                # Issue #22:接近满分且用时过短 → 风险事件(疑似外挂/超快满分结算)
                elapsed = time.time() - sess["created_at"]
                if score > max_score * 0.98 and elapsed < GAMES[game]["duration"] * 0.5:
                    _add_risk_event(conn, user["id"], user["username"], "perfect_too_fast", "high",
                                    f"{GAMES[game]['name']} 得分 {score}/{max_score}，用时 {int(elapsed)}s")
                add_daily_earned(user["username"], earned, today)
                log(conn, user["id"], user["username"], "game_end", f"结算 {GAMES[game]['name']}", earned, ip)
            return self._send(200, {"ok": True, "earned": earned, "points": points,
                                    "is_best": is_best, "today_earned": daily_earned(user["username"], today),
                                    "ticket": config_get("goldminer_ticket", GOLDMINER_TICKET) if game == "goldminer" else 0,
                                    "pay_range": [config_get("goldminer_pay_min", GOLDMINER_PAY_MIN),
                                                  config_get("goldminer_pay_max", GOLDMINER_PAY_MAX)]
                                    if game == "goldminer" else None})

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

        # 批量播种(单事务校验种子/地块,逐格返回结果;种子不足时按数量截断)
        if path == "/api/farm/batch-plant":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            raw = data.get("slot_list")
            if not isinstance(raw, list) or not raw:
                return self._send(400, {"error": "slot_list 必须是数组"})
            crop = str(data.get("crop", ""))
            if crop not in CROPS:
                return self._send(400, {"error": "作物不存在"})
            if CROPS[crop].get("vip") and not is_vip(user):
                return self._send(400, {"error": "这是 VIP 专属作物，开通 VIP 后才能种植"})
            slots = []
            for s in raw:
                try:
                    si = int(s)
                except Exception:
                    continue
                if 0 <= si < PLOT_COUNT:
                    slots.append(si)
            slots = list(dict.fromkeys(slots))   # 去重
            if not slots:
                return self._send(400, {"error": "没有可操作的槽位"})
            results = {}
            with _lock, db() as conn:
                uid = user["id"]
                seed = conn.execute("SELECT count FROM farm_seeds WHERE user_id=? AND crop=?",
                                    (uid, crop)).fetchone()
                if not seed or seed["count"] < 1:
                    return self._send(400, {"error": "没有种子，先到种子商店购买"})
                bad = {}
                for slot in slots:
                    pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                                      (uid, slot)).fetchone()
                    if not (pr and pr["unlocked"]) and slot >= DEFAULT_PLOTS:
                        bad[slot] = "该地块还未开垦"
                        continue
                    cur = conn.execute("SELECT * FROM farm WHERE user_id=? AND slot=?",
                                       (uid, slot)).fetchone()
                    if cur and cur["crop"] is not None:
                        gh = building_level(conn, uid, "greenhouse")
                        lv = pr["level"] if pr else 1
                        if time.time() < cur["planted_at"] + farm_grow_seconds(cur["crop"], lv, gh):
                            bad[slot] = "该地块还没成熟"
                ok_slots = [s for s in slots if s not in bad]
                # 种子不足:前 N 块播种成功,其余标记"种子不足"
                if len(ok_slots) > seed["count"]:
                    for slot in ok_slots[seed["count"]:]:
                        bad[slot] = "种子不足"
                    ok_slots = ok_slots[:seed["count"]]
                for slot in ok_slots:
                    conn.execute("UPDATE farm_seeds SET count=count-1 WHERE user_id=? AND crop=?", (uid, crop))
                    conn.execute(
                        "INSERT OR REPLACE INTO farm(user_id,slot,crop,planted_at,waters,stolen,stolen_by) VALUES(?,?,?,?,0,0,NULL)",
                        (uid, slot, crop, time.time()))
                    results[slot] = {"ok": True}
                for slot, err in bad.items():
                    results[slot] = {"ok": False, "error": err}
                conn.execute("DELETE FROM farm_seeds WHERE user_id=? AND crop=? AND count<=0", (uid, crop))
                conn.commit()
            return self._send(200, {"results": results, "points": user["points"],
                                    "farm": farm_state(conn, uid, uid, user["username"])})

        # 批量收获(单事务逐格收获,返回每格结果;仓库容量不足的格子保留原样)
        if path == "/api/farm/batch-harvest":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            raw = data.get("slot_list")
            if not isinstance(raw, list) or not raw:
                return self._send(400, {"error": "slot_list 必须是数组"})
            slots = []
            for s in raw:
                try:
                    si = int(s)
                except Exception:
                    continue
                if 0 <= si < PLOT_COUNT:
                    slots.append(si)
            slots = list(dict.fromkeys(slots))
            if not slots:
                return self._send(400, {"error": "没有可操作的槽位"})
            results = {}
            with _lock, db() as conn:
                uid = user["id"]
                inv, units = farm_inventory(conn, uid)
                capacity = farm_capacity(conn, uid)
                for slot in slots:
                    row = conn.execute("SELECT * FROM farm WHERE user_id=? AND slot=?",
                                       (uid, slot)).fetchone()
                    if not row or not row["crop"]:
                        results[slot] = {"ok": False, "error": "没有作物"}
                        continue
                    gh = building_level(conn, uid, "greenhouse")
                    pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                                      (uid, slot)).fetchone()
                    lv = pr["level"] if pr else 1
                    grow = farm_grow_seconds(row["crop"], lv, gh)
                    if time.time() < row["planted_at"] + grow:
                        results[slot] = {"ok": False, "error": "还没成熟"}
                        continue
                    if row["stolen"]:
                        results[slot] = {"ok": False, "error": f"作物已被 {row['stolen_by']} 偷走了！"}
                        continue
                    size = CROP_SIZE.get(row["crop"], 1)
                    if units + size > capacity:
                        results[slot] = {"ok": False, "error": f"仓库已满（{units}/{capacity}），请先出售作物"}
                        continue
                    conn.execute("INSERT INTO inventory(user_id,crop,count) VALUES(?,?,1) "
                                 "ON CONFLICT(user_id,crop) DO UPDATE SET count=count+1",
                                 (uid, row["crop"]))
                    conn.execute("DELETE FROM farm WHERE user_id=? AND slot=?", (uid, slot))
                    conn.execute("UPDATE users SET exp=exp+5 WHERE id=?", (uid,))
                    units += size
                    results[slot] = {"ok": True, "crop": row["crop"], "name": CROPS[row["crop"]]["name"]}
                    log(conn, uid, user["username"], "farm_harvest", f"收获{CROPS[row['crop']]['name']}入仓", ip=ip)
                conn.commit()
            return self._send(200, {"results": results, "points": user["points"],
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
                daily_cap = config_get("daily_earned_cap", DAILY_EARNED_CAP)
                remain = daily_cap - daily_earned(user["username"], today)
                if earned > remain:
                    sell_count = remain // unit
                    if sell_count <= 0:
                        return self._send(400, {"error": f"今日可赚积分已达上限（{daily_cap}）"})
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
                remain = config_get("daily_earned_cap", DAILY_EARNED_CAP) - daily_earned(user["username"], today)
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
            request_id = str(data.get("request_id", "")).strip()
            with _lock, db() as conn:
                # 幂等:同一 request_id 直接返回上次结果(不重复扣费/扣券/发券)
                if request_id:
                    cached = conn.execute(
                        "SELECT result FROM wheel_spin_requests WHERE user_id=? AND request_id=?",
                        (user["id"], request_id)).fetchone()
                    if cached:
                        return self._send(200, json.loads(cached["result"]))
                # 优先原子消费一张未过期免费券(不扣积分);无券才扣积分
                used_free = False
                ticket = conn.execute(
                    "SELECT ticket_id FROM wheel_free_tickets "
                    "WHERE user_id=? AND used=0 AND expires_at>? ORDER BY created_at LIMIT 1",
                    (user["id"], time.time())).fetchone()
                if ticket:
                    cur = conn.execute(
                        "UPDATE wheel_free_tickets SET used=1 WHERE ticket_id=? AND used=0",
                        (ticket["ticket_id"],))
                    used_free = cur.rowcount > 0
                wheel_cost = config_get("wheel_cost", WHEEL_COST)
                if not used_free and user["points"] < wheel_cost:
                    return self._send(400, {"error": "积分不足"})
                idx = random.choices(range(len(WHEEL_SECTORS)), weights=WHEEL_WEIGHTS, k=1)[0]
                sector = WHEEL_SECTORS[idx]
                prize = sector["prize"]
                free = prize == -1
                if used_free:
                    points = conn.execute("SELECT points FROM users WHERE id=?",
                                          (user["id"],)).fetchone()["points"]
                elif free:
                    points = change_points(conn, user["id"], user["username"], -wheel_cost,
                                           "wheel_spin", "转盘：再转一次", ip)
                else:
                    points = change_points(conn, user["id"], user["username"], prize - wheel_cost,
                                           "wheel_spin", f"转盘：{sector['name']}", ip)
                if free:
                    # 抽中“再转一次”发放一次性免费券(24 小时有效)
                    conn.execute(
                        "INSERT INTO wheel_free_tickets(ticket_id,user_id,created_at,expires_at,used) "
                        "VALUES(?,?,?,?,0)",
                        (secrets.token_hex(16), user["id"], time.time(),
                         time.time() + WHEEL_FREE_TTL))
                conn.execute("INSERT INTO wheel_logs(user_id, username, sector, name, prize, created_at) "
                             "VALUES(?,?,?,?,?,?)",
                             (user["id"], user["username"], idx, sector["name"], prize, time.time()))
                free_left = wheel_free_left(conn, user["id"])
                result = {"ok": True, "sector": idx, "name": sector["name"],
                          "prize": prize, "free": free, "points": points, "free_left": free_left}
                if request_id:
                    conn.execute("DELETE FROM wheel_spin_requests WHERE user_id=? AND created_at<?",
                                 (user["id"], time.time() - 86400))  # 顺手清理旧幂等记录
                    conn.execute("INSERT OR REPLACE INTO wheel_spin_requests(user_id,request_id,result,created_at) "
                                 "VALUES(?,?,?,?)",
                                 (user["id"], request_id, json.dumps(result), time.time()))
                conn.commit()
            return self._send(200, result)

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
                slot_cost = config_get("slot_cost", SLOT_COST)
                if user["points"] < slot_cost:
                    return self._send(400, {"error": f"积分不足，每次需要 {slot_cost} 积分"})
                reel, pay, token = slot_spin(conn, user["id"], user["username"], ip)
                points = conn.execute("SELECT points FROM users WHERE id=?", (user["id"],)).fetchone()["points"]
            return self._send(200, {"ok": True, "reel": reel, "pay": pay, "cost": slot_cost,
                                    "points": points, "double_token": token,
                                    "can_double": bool(token and pay > slot_cost),
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
            now = time.time()
            with _lock, db() as conn:
                conn.execute("""INSERT INTO gomoku_rooms(code,player_black,player_white,board,turn,status,mode,created_at,started_at,ip_black)
                                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                             (code, user["id"], None, json.dumps(gomoku_new_board()), 1,
                              "playing" if mode == "bot" else "waiting", mode, now,
                              now if mode == "bot" else None, ip))
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
                if row["status"] == "over":
                    return self._send(409, {"error": "房间已结束"})
                if row["status"] != "waiting":
                    return self._send(409, {"error": "房间已满或已开始"})
                if row["player_black"] == user["id"]:
                    return self._send(400, {"error": "你已经在房间里了"})
                conn.execute(
                    "UPDATE gomoku_rooms SET player_white=?, status='playing', last_move_at=?, started_at=?, ip_white=? WHERE code=?",
                    (user["id"], time.time(), time.time(), ip, code))
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
                if _gomoku_check_timeout(conn, row):
                    conn.commit()
                    _broadcast(code, None)
                    return self._send(400, {"error": "回合超时，对局已结束"})
                row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
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
                    conn.execute("UPDATE gomoku_rooms SET board=?, moves=moves+1 WHERE code=?",
                                 (json.dumps(board), code))
                    _finish_gomoku(conn, code, user["id"] if won else None,
                                   "normal" if won else "draw", ip)
                    conn.commit()
                    log(conn, user["id"], user["username"], "gomoku_move", f"房间{code}落子({x},{y})", ip=ip)
                    _broadcast(code, None)
                    return self._send(200, {"ok": True, "over": True, "winner": user["id"] if won else None})
                conn.execute("UPDATE gomoku_rooms SET board=?, turn=?, last_move_at=?, moves=moves+1 WHERE code=?",
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
                uid = user["id"]
                if row["status"] == "waiting":
                    # 仅房主(player_black 创建者)可取消/删除等待中的房间;身份以服务端登录用户为准
                    if row["player_black"] != uid:
                        return self._send(403, {"error": "只有房主可以取消房间"})
                    conn.execute("DELETE FROM gomoku_rooms WHERE code=?", (code,))
                    conn.commit()
                    log(conn, uid, user["username"], "gomoku_cancel", f"取消房间 {code}", ip=ip)
                    _broadcast(code, None)
                    return self._send(200, {"ok": True})
                if row["status"] == "playing":
                    if uid not in (row["player_black"], row["player_white"]):
                        return self._send(403, {"error": "你不是本局玩家"})
                    opp = row["player_white"] if uid == row["player_black"] else row["player_black"]
                    # 认输：胜方=对手，结束原因 resign，奖励与状态在同一事务内结算
                    _finish_gomoku(conn, code, opp, "resign", ip, loser=uid)
                    conn.commit()
                    log(conn, uid, user["username"], "gomoku_leave", f"房间{code}认输", ip=ip)
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
                before = target["points"]
                points = change_points(conn, uid, target["username"], amount,
                                       "admin_balance", f"管理员调整余额 {note}".strip(), ip)
                admin_audit(conn, user["id"], user["username"], "balance_adjust",
                            target=target["username"],
                            before_value=str(before), after_value=str(points),
                            reason=note or None, request_id=request_id, ip=ip)
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
            reason = str(data.get("reason", "")).strip()[:200]
            if not reason:
                return self._send(400, {"error": "封禁/解封必须填写理由"})
            with _lock, db() as conn:
                target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                if not target:
                    return self._send(400, {"error": "用户不存在"})
                if target["role"] == "admin" and target["id"] != user["id"]:
                    return self._send(400, {"error": "不能操作其他管理员"})
                new = "banned" if target["status"] == "active" else "active"
                before = target["status"]
                conn.execute("UPDATE users SET status=? WHERE id=?", (new, uid))
                conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
                admin_audit(conn, user["id"], user["username"],
                            "unban" if new == "active" else "ban",
                            target=target["username"],
                            before_value=before, after_value=new,
                            reason=reason, request_id=request_id, ip=ip)
                conn.commit()
                detail = f"封禁/解封 {target['username']} → {new}，理由：{reason}"
                log(conn, user["id"], user["username"], "admin_op", detail, ip=ip)
            return self._send(200, {"ok": True, "status": new})

        if path == "/api/admin/kick-session":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
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
                    return self._send(400, {"error": "用户不存在"})
                cur = conn.execute("DELETE FROM sessions WHERE user_id=?", (target["id"],))
                kicked = cur.rowcount
                admin_audit(conn, user["id"], user["username"], "kick_session",
                            target=target["username"], after_value=f"注销{kicked}个会话",
                            reason=None, request_id=request_id, ip=ip)
                conn.commit()
                log(conn, user["id"], user["username"], "admin_op",
                    f"强制下线 {target['username']}(注销 {kicked} 个会话)", ip=ip)
            return self._send(200, {"ok": True, "kicked": kicked})

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
            reason = str(data.get("reason", "")).strip()[:200]
            if not reason:
                return self._send(400, {"error": "删除漂流瓶必须填写理由"})
            with _lock, db() as conn:
                row = conn.execute("SELECT content FROM bottles WHERE id=?", (bid,)).fetchone()
                if row:
                    admin_audit(conn, user["id"], user["username"], "del_bottle",
                                target=f"bottle#{bid}",
                                before_value=(row["content"] or "")[:200],
                                reason=reason, request_id=request_id, ip=ip)
                conn.execute("DELETE FROM bottles WHERE id=?", (bid,))
                conn.commit()
                log(conn, user["id"], user["username"], "admin_op", f"删除漂流瓶 #{bid}，理由：{reason}", ip=ip)
            return self._send(200, {"ok": True})

        # ============ Issue #23:用户举报(漂流瓶 / 站内信) ============
        if path == "/api/report":
            user = self._me()
            if not user:
                return self._send(401, {"error": "未登录"})
            ctype = str(data.get("type", ""))
            try:
                cid = int(data.get("id", 0))
            except Exception:
                return self._send(400, {"error": "参数错误"})
            reason = str(data.get("reason", "")).strip()[:200]
            if ctype not in ("bottle", "mail"):
                return self._send(400, {"error": "举报类型错误"})
            if not cid or not reason:
                return self._send(400, {"error": "参数不完整"})
            with _lock, db() as conn:
                table = "bottles" if ctype == "bottle" else "mail"
                row = conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (cid,)).fetchone()
                if not row:
                    return self._send(404, {"error": "内容不存在或已被删除"})
                dup = conn.execute(
                    "SELECT 1 FROM reports WHERE content_type=? AND content_id=? AND reporter_id=?",
                    (ctype, cid, user["id"])).fetchone()
                if dup:
                    return self._send(400, {"error": "你已举报过该内容"})
                conn.execute(
                    "INSERT INTO reports(content_type,content_id,reporter_id,reason,status,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (ctype, cid, user["id"], reason, "pending", time.time()))
                conn.commit()
                log(conn, user["id"], user["username"], "report", f"举报{ctype}#{cid}：{reason}", ip=ip)
            return self._send(200, {"ok": True})

        # ============ Issue #22:风险事件处理 ============
        if path == "/api/admin/risk-review":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            try:
                rid = int(data.get("id", 0))
            except Exception:
                return self._send(400, {"error": "参数错误"})
            note = str(data.get("note", "")).strip()[:200]
            with _lock, db() as conn:
                cur = conn.execute("UPDATE risk_events SET status='reviewed', note=? WHERE id=?",
                                   (note or None, rid))
                conn.commit()
                if cur.rowcount == 0:
                    return self._send(404, {"error": "风险事件不存在"})
                admin_audit(conn, user["id"], user["username"], "risk_review",
                            target=f"risk#{rid}", before_value="pending",
                            after_value="reviewed", reason=note or None,
                            request_id=request_id, ip=ip)
                log(conn, user["id"], user["username"], "admin_op", f"风险事件 #{rid} 已复核", ip=ip)
            return self._send(200, {"ok": True})

        # ============ Issue #23:举报处理(hide / reject / warn) ============
        if path == "/api/admin/report-handle":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            try:
                rid = int(data.get("id", 0))
            except Exception:
                return self._send(400, {"error": "参数错误"})
            action = str(data.get("action", ""))
            note = str(data.get("note", "")).strip()[:200]
            if action not in ("hide", "reject", "warn"):
                return self._send(400, {"error": "处理动作不合法"})
            with _lock, db() as conn:
                rep = conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
                if not rep:
                    return self._send(404, {"error": "举报不存在"})
                if rep["status"] != "pending":
                    return self._send(400, {"error": "该举报已处理"})
                status = "handled"
                if action == "hide":
                    table = "bottles" if rep["content_type"] == "bottle" else "mail"
                    conn.execute(f"UPDATE {table} SET hidden=1 WHERE id=?", (rep["content_id"],))
                elif action == "warn":
                    offender_id = None
                    if rep["content_type"] == "bottle":
                        b = conn.execute("SELECT user_id FROM bottles WHERE id=?",
                                         (rep["content_id"],)).fetchone()
                        offender_id = b["user_id"] if b else None
                    else:
                        m = conn.execute("SELECT from_id FROM mail WHERE id=?", (rep["content_id"],)).fetchone()
                        offender_id = m["from_id"] if m else None
                    if offender_id:
                        conn.execute(
                            "INSERT INTO mail(from_id,to_id,title,content,mtype,created_at) VALUES(?,?,?,?,?,?)",
                            (user["id"], offender_id, "内容审核警告",
                             f"你的{'漂流瓶' if rep['content_type'] == 'bottle' else '站内信'}因被举报已收到警告，请注意文明发言。"
                             + (f"备注：{note}" if note else ""),
                             "system", time.time()))
                elif action == "reject":
                    status = "rejected"
                conn.execute("UPDATE reports SET status=?, handled_by=?, note=? WHERE id=?",
                             (status, user["username"], note or None, rid))
                admin_audit(conn, user["id"], user["username"], "report_handle",
                            target=f"report#{rid}", before_value="pending",
                            after_value=status, reason=note or None,
                            request_id=request_id, ip=ip)
                conn.commit()
                log(conn, user["id"], user["username"], "admin_op",
                    f"处理举报 #{rid}（{action}）", ip=ip)
            return self._send(200, {"ok": True})

        # ============ Issue #21:游戏参数配置(draft/publish/rollback) ============
        if path == "/api/admin/config/set":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            name = str(data.get("name", "")).strip()
            value = data.get("value")
            try:
                with _lock, db() as conn:
                    ver = config_set(conn, name, value, user["username"])
                    log(conn, user["id"], user["username"], "config_set",
                        f"修改参数 {name} → {value}（草稿 v{ver}）", ip=ip)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True, "name": name, "version": ver})

        if path == "/api/admin/config/publish":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            name = str(data.get("name", "")).strip()
            try:
                with _lock, db() as conn:
                    ver = config_publish(conn, name, user["username"])
                    admin_audit(conn, user["id"], user["username"], "config_publish",
                                target=name, after_value=f"v{ver}",
                                request_id=request_id, ip=ip)
                    log(conn, user["id"], user["username"], "config_publish",
                        f"发布参数 {name}（v{ver}）", ip=ip)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True, "name": name, "version": ver})

        if path == "/api/admin/config/rollback":
            user = self._me(admin=True)
            if user is None or user is False:
                return self._send(403, {"error": "无权限"})
            name = str(data.get("name", "")).strip()
            try:
                with _lock, db() as conn:
                    res = config_rollback(conn, name, user["username"])
                    admin_audit(conn, user["id"], user["username"], "config_rollback",
                                target=name, after_value=f"{res['value']}(v{res['version']})",
                                request_id=request_id, ip=ip)
                    log(conn, user["id"], user["username"], "config_rollback",
                        f"回滚参数 {name} → {res['value']}（v{res['version']}）", ip=ip)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True, "name": name, **res})

        self._send(404, {"error": "接口不存在"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()


def ensure_admins():
    """启动时将 ADMIN_USERS / ADMIN_INIT 中的存量用户提升为 admin(幂等)"""
    names = [n for n in ADMIN_USERS + ADMIN_INIT if n]
    if not names:
        return
    with _lock, db() as conn:
        for n in names:
            u = get_user_by_name(conn, n)
            if u and u["role"] != "admin":
                conn.execute("UPDATE users SET role='admin' WHERE id=?", (u["id"],))
                admin_audit(conn, u["id"], n, "role_change", target=n,
                            before_value=u["role"], after_value="admin",
                            reason="预设管理员名单提升", request_id="startup")
                conn.commit()
                log(conn, u["id"], n, "admin_op", "预设管理员提升")


def main():
    init_db()
    ensure_admins()
    threading.Thread(target=_gomoku_cleanup_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"🎮 小游戏乐园已启动: http://localhost:{PORT}")
    print(f"   数据: {DB_PATH}   注册用户均为普通用户,不自动成为管理员")
    if ADMIN_USERS + ADMIN_INIT:
        print(f"   预设管理员: {', '.join(ADMIN_USERS + ADMIN_INIT)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
