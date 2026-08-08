# -*- coding: utf-8 -*-
"""游戏模块:黄金矿工 / 音乐 / 转盘 / 水果老虎机 / 五子棋(含 SSE 长连接)。

防作弊:一次性游戏令牌、服务器生成谱面/地图、服务器重判、分数上限、频率限制、
每日积分上限、幂等结算(point_ledger biz_no)。

SQLite 差异点:`INSERT OR REPLACE`(PG 用 `ON CONFLICT DO UPDATE`)、
`ORDER BY RANDOM()` 对应 PG 的 `ORDER BY random()`。
"""
import json
import math
import os
import queue
import random
import secrets
import threading
import time

from fastapi import APIRouter, Request, Response
from starlette.responses import StreamingResponse

from . import config
from .auth import me
from .db import _lock, db
from .gameconfig import config_get
from .http import json_response, parse_body
from .wallet import (add_daily_earned, add_slot_daily_earned, change_points, daily_earned,
                     log, rate_check, slot_daily_earned)

router = APIRouter()

# 游戏防作弊参数
GAMES = {
    "goldminer": {"name": "黄金矿工", "max_score": 6000, "duration": 60},
    "rhythm": {"name": "音乐游戏", "max_score": None, "duration": 80},
}
GAME_SESSION_MINUTES = 30
RHYTHM_BPM = 132
RHYTHM_SONG_SEC = 80

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

# 转盘
WHEEL_COST = 10
WHEEL_FREE_TTL = 86400   # 免费券有效期(秒)=24 小时
WHEEL_SECTORS = [
    {"name": "+5分", "prize": 5}, {"name": "+10分", "prize": 10},
    {"name": "0分", "prize": 0}, {"name": "+50分", "prize": 50},
    {"name": "+2分", "prize": 2}, {"name": "再转一次", "prize": -1},
    {"name": "+100分", "prize": 100}, {"name": "-5分", "prize": -5},
]
WHEEL_WEIGHTS = [25, 20, 18, 4, 15, 6, 2, 10]

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


# ---------------- 谱面 / 地图（服务器生成，客户端无法篡改） ----------------
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


# ---------------- 五子棋房间事件订阅（SSE 广播） ----------------
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


# ================= 路由 =================
@router.get("/api/wheel/stats")
def wheel_stats(request: Request):
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
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
    return json_response(200, {
        "total": total,
        "my_spins": my,
        "win_rate": round(win / total * 100, 1) if total else 0,
        "jackpots": jackpots,
        "my_recent": my_recent,
        "free_tickets": free_tickets,
    })


@router.post("/api/game/start")
async def game_start(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    game = str(data.get("game", ""))
    if game not in GAMES:
        return json_response(400, {"error": "未知游戏"})
    if not rate_check(f"gstart:{user['username']}:{game}", 30, 3600):
        return json_response(429, {"error": "开局过于频繁"})
    played = 0
    if game == "goldminer":
        day_start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
        with _lock, db() as conn:
            played = conn.execute(
                "SELECT COUNT(*) c FROM game_sessions WHERE user_id=? AND game='goldminer' AND created_at>=?",
                (user["id"], day_start)).fetchone()["c"]
        if played >= GOLDMINER_DAILY_LIMIT:
            return json_response(429, {"error": f"黄金矿工每天限玩 {GOLDMINER_DAILY_LIMIT} 次，明天再来！"})
        ticket = config_get("goldminer_ticket", GOLDMINER_TICKET)
        if user["points"] < ticket:
            return json_response(400, {"error": f"门票需要 {ticket} 积分"})
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
    return json_response(200, {"ok": True, "token": token, "game": game,
                               "chart": chart, "max_score": max_score,
                               "goldminer_seed": seed if game == "goldminer" else None,
                               "duration": GAMES[game]["duration"],
                               "ticket": ticket if game == "goldminer" else 0,
                               "daily_left": played if game == "goldminer" else None,
                               "limits": {"hour": config.SUBMIT_PER_HOUR, "day": config.SUBMIT_PER_DAY,
                                          "daily_cap": config_get("daily_earned_cap", config.DAILY_EARNED_CAP)}})


@router.post("/api/game/end")
async def game_end(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    token = str(data.get("token", ""))
    game = str(data.get("game", ""))
    try:
        score = int(data.get("score", -1))
    except Exception:
        return json_response(400, {"error": "分数格式错误"})
    stats = data.get("stats") or {}
    if game not in GAMES:
        return json_response(400, {"error": "未知游戏"})
    today = time.strftime("%Y-%m-%d")
    if not rate_check(f"gend:{user['username']}:{game}", config.SUBMIT_PER_HOUR, 3600):
        return json_response(429, {"error": "本小时提交次数已达上限"})
    if not rate_check(f"gend:{user['username']}:{game}", config.SUBMIT_PER_DAY, 86400):
        return json_response(429, {"error": "今日提交次数已达上限"})
    if not rate_check(f"gendip:{ip}", 30, 3600):
        return json_response(429, {"error": "提交过于频繁"})
    with _lock, db() as conn:
        sess = conn.execute("SELECT * FROM game_sessions WHERE token=? AND user_id=?",
                            (token, user["id"])).fetchone()
        if not sess:
            return json_response(400, {"error": "会话无效或已过期"})
        if sess["used"]:
            return json_response(400, {"error": "该局已结算，禁止重放"})
        if sess["game"] != game or sess["expires_at"] < time.time():
            return json_response(400, {"error": "会话无效或已过期"})
        max_score = sess["max_score"]
        if score < 0 or score > max_score:
            return json_response(400, {"error": "分数异常，提交被拒绝"})
        if game == "rhythm":
            chart = json.loads(sess["chart"] or "[]")
            # 服务器按谱面重判按键时间线(忽略客户端统计,防伪造满分)
            timeline = stats.get("timeline")
            if not isinstance(timeline, list) or len(timeline) > 2000:
                return json_response(400, {"error": "按键数据异常，提交被拒绝"})
            tl = []
            for x in timeline:
                try:
                    t = float(x.get("t", -1))
                    lane = int(x.get("lane", -1))
                except Exception:
                    return json_response(400, {"error": "按键数据异常，提交被拒绝"})
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
                return json_response(400, {"error": "抓取数据异常"})
            world = gen_goldminer_world(sess["seed"])
            avail = {}
            for it in world:
                avail[it["v"]] = avail.get(it["v"], 0) + 1
            total_v = 0
            for c in catches:
                try:
                    v = int(c.get("v", -1))
                except Exception:
                    return json_response(400, {"error": "抓取数据异常"})
                if avail.get(v, 0) <= 0:
                    return json_response(400, {"error": "抓取数据与地图不符"})
                avail[v] -= 1
                total_v += v
            if score != total_v:
                return json_response(400, {"error": "分数与抓取记录不符"})
            earned = random.randint(config_get("goldminer_pay_min", GOLDMINER_PAY_MIN),
                                    config_get("goldminer_pay_max", GOLDMINER_PAY_MAX))
        daily_cap = config_get("daily_earned_cap", config.DAILY_EARNED_CAP)
        if daily_earned(user["username"], today) + earned > daily_cap:
            return json_response(400, {"error": f"今日可赚积分已达上限（{daily_cap}）"})
        conn.execute("UPDATE game_sessions SET used=1 WHERE token=?", (token,))
        conn.execute("UPDATE users SET exp=exp+? WHERE id=?", (max(1, earned // 20), user["id"]))  # 结算经验
        points = change_points(conn, user["id"], user["username"], earned,
                               "game_award", f"{GAMES[game]['name']} 得分 {score}", ip,
                               idem_key=f"settle:{token}")
        prev = conn.execute("SELECT score FROM scores WHERE game=? AND user_id=?",
                            (game, user["id"])).fetchone()
        is_best = prev is None or score > prev["score"]
        if is_best:
            # SQLite `INSERT OR REPLACE` → PG: `INSERT ... ON CONFLICT(game,user_id) DO UPDATE ...`
            conn.execute("INSERT OR REPLACE INTO scores(game,user_id,name,score,at) VALUES(?,?,?,?,?)",
                         (game, user["id"], user["username"], score, time.time()))
            conn.commit()
        add_daily_earned(user["username"], earned, today)
        log(conn, user["id"], user["username"], "game_end", f"结算 {GAMES[game]['name']}", earned, ip)
    return json_response(200, {"ok": True, "earned": earned, "points": points,
                               "is_best": is_best, "today_earned": daily_earned(user["username"], today),
                               "ticket": config_get("goldminer_ticket", GOLDMINER_TICKET) if game == "goldminer" else 0,
                               "pay_range": [config_get("goldminer_pay_min", GOLDMINER_PAY_MIN),
                                             config_get("goldminer_pay_max", GOLDMINER_PAY_MAX)]
                               if game == "goldminer" else None})


@router.post("/api/wheel/spin")
async def wheel_spin(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    if not rate_check(f"spin:{user['username']}", 60, 3600):
        return json_response(429, {"error": "转盘过于频繁"})
    request_id = str(data.get("request_id", "")).strip()
    with _lock, db() as conn:
        # 幂等:同一 request_id 直接返回上次结果(不重复扣费/扣券/发券)
        if request_id:
            cached = conn.execute(
                "SELECT result FROM wheel_spin_requests WHERE user_id=? AND request_id=?",
                (user["id"], request_id)).fetchone()
            if cached:
                return json_response(200, json.loads(cached["result"]))
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
            return json_response(400, {"error": "积分不足"})
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
            # SQLite `INSERT OR REPLACE` → PG: `INSERT ... ON CONFLICT(user_id,request_id) DO UPDATE ...`
            conn.execute("INSERT OR REPLACE INTO wheel_spin_requests(user_id,request_id,result,created_at) "
                         "VALUES(?,?,?,?)",
                         (user["id"], request_id, json.dumps(result), time.time()))
        conn.commit()
    return json_response(200, result)


@router.post("/api/slot/spin")
async def slot_spin_route(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    if not rate_check(f"slot:{user['username']}", 60, 3600):
        return json_response(429, {"error": "转得也太快了"})
    with _lock, db() as conn:
        slot_cleanup(conn)
        today = time.strftime("%Y-%m-%d")
        if slot_daily_earned(user["username"], today) >= SLOT_DAILY_MAX:
            return json_response(400, {"error": f"今日老虎机收益已达上限（{SLOT_DAILY_MAX} 金币），明天再来！"})
        slot_cost = config_get("slot_cost", SLOT_COST)
        if user["points"] < slot_cost:
            return json_response(400, {"error": f"积分不足，每次需要 {slot_cost} 积分"})
        reel, pay, token = slot_spin(conn, user["id"], user["username"], ip)
        points = conn.execute("SELECT points FROM users WHERE id=?", (user["id"],)).fetchone()["points"]
    return json_response(200, {"ok": True, "reel": reel, "pay": pay, "cost": slot_cost,
                               "points": points, "double_token": token,
                               "can_double": bool(token and pay > slot_cost),
                               "daily_left": SLOT_DAILY_MAX - slot_daily_earned(user["username"], today),
                               "payouts": {s: SLOT_SYMBOLS[s]["x3"] for s in SLOT_SYMBOLS}})


@router.post("/api/slot/double")
async def slot_double(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    token = str(data.get("token", ""))
    if not rate_check(f"slotdbl:{user['username']}", 60, 3600):
        return json_response(429, {"error": "操作过于频繁"})
    with _lock, db() as conn:
        row = conn.execute("SELECT * FROM slot_pending WHERE token=? AND user_id=?",
                           (token, user["id"])).fetchone()
        if not row:
            return json_response(400, {"error": "没有待结算的奖励"})
        win = random.random() < 0.5
        if win:
            today = time.strftime("%Y-%m-%d")
            remain = SLOT_DAILY_MAX - slot_daily_earned(user["username"], today)
            pending = min(row["pending"] * 2, SLOT_PENDING_MAX, max(0, remain))
            conn.execute("UPDATE slot_pending SET pending=?, created_at=? WHERE token=?",
                         (pending, time.time(), token))
            conn.commit()
            log(conn, user["id"], user["username"], "slot_double", f"翻倍成功 → {pending}", ip=ip)
            return json_response(200, {"ok": True, "win": True, "pending": pending,
                                       "token": token, "points": user["points"]})
        conn.execute("DELETE FROM slot_pending WHERE token=?", (token,))
        conn.commit()
        log(conn, user["id"], user["username"], "slot_double", f"翻倍失败，{row['pending']} 分打了水漂", ip=ip)
        return json_response(200, {"ok": True, "win": False, "pending": 0,
                                   "token": "", "points": user["points"]})


@router.post("/api/slot/collect")
async def slot_collect_route(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    token = str(data.get("token", ""))
    with _lock, db() as conn:
        slot_cleanup(conn)
        points, pending = slot_collect(conn, user["id"], user["username"], token, ip)
        if pending is None:
            return json_response(400, {"error": "没有待结算的奖励"})
    return json_response(200, {"ok": True, "points": points, "pending": pending})


@router.get("/api/gomoku/rank")
def gomoku_rank(request: Request):
    with _lock, db() as conn:
        rows = conn.execute(
            """SELECT u.username, COUNT(*) wins FROM gomoku_games g
               JOIN users u ON u.id=g.winner
               WHERE g.winner IS NOT NULL GROUP BY g.winner ORDER BY wins DESC LIMIT 20""").fetchall()
    return json_response(200, {"list": [dict(r) for r in rows]})


@router.get("/api/gomoku/room")
def gomoku_room(request: Request):
    code = request.query_params.get("code", "").strip().upper()
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    with _lock, db() as conn:
        row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
        if not row:
            return json_response(404, {"error": "房间不存在"})
        now = time.time()
        if row["status"] == "waiting" and now - row["created_at"] >= GOMOKU_ROOM_TTL:
            conn.execute("DELETE FROM gomoku_rooms WHERE code=?", (code,))
            conn.commit()
            return json_response(404, {"error": "房间已过期"})
        if row["status"] == "playing":
            _gomoku_check_timeout(conn, row, now)
            conn.commit()
            row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
        state = gomoku_state(row, user["id"], conn)
    return json_response(200, state)


@router.get("/api/gomoku/stream")
def gomoku_stream(request: Request):
    code = request.query_params.get("code", "").strip().upper()
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    with _lock, db() as conn:
        row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
        if not row:
            return json_response(404, {"error": "房间不存在"})

    def event_stream():
        q = _subscribe(code, user["id"])
        try:
            yield "retry: 2000\n\n"
            _broadcast(code, None)  # 上线通知，让对手刷新在线状态
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                if ev is None:
                    ev = "refresh"
                with _lock, db() as conn:
                    row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
                    state = gomoku_state(row, user["id"], conn) if row else None
                payload = json.dumps(state, ensure_ascii=False)
                yield f"data: {payload}\n\n"
        finally:
            _unsubscribe(code, q, user["id"])
            _broadcast(code, None)  # 离线通知

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.post("/api/gomoku/create")
async def gomoku_create(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    mode = str(data.get("mode", "pvp"))
    if mode not in ("pvp", "bot"):
        return json_response(400, {"error": "模式错误"})
    if not rate_check(f"gomo:{user['username']}", 30, 3600):
        return json_response(429, {"error": "建房过于频繁"})
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
    return json_response(200, {"ok": True, "code": code, "mode": mode})


@router.post("/api/gomoku/join")
async def gomoku_join(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    code = str(data.get("code", "")).strip().upper()
    with _lock, db() as conn:
        row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
        if not row:
            return json_response(404, {"error": "房间不存在"})
        if row["status"] == "over":
            return json_response(409, {"error": "房间已结束"})
        if row["status"] != "waiting":
            return json_response(409, {"error": "房间已满或已开始"})
        if row["player_black"] == user["id"]:
            return json_response(400, {"error": "你已经在房间里了"})
        conn.execute(
            "UPDATE gomoku_rooms SET player_white=?, status='playing', last_move_at=?, started_at=?, ip_white=? WHERE code=?",
            (user["id"], time.time(), time.time(), ip, code))
        conn.commit()
        log(conn, user["id"], user["username"], "gomoku_join", f"加入房间 {code}", ip=ip)
    _broadcast(code, None)
    return json_response(200, {"ok": True})


@router.post("/api/gomoku/move")
async def gomoku_move(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    code = str(data.get("code", "")).strip().upper()
    try:
        x, y = int(data.get("x", -1)), int(data.get("y", -1))
    except Exception:
        return json_response(400, {"error": "坐标错误"})
    if not (0 <= x < GOMOKU_SIZE and 0 <= y < GOMOKU_SIZE):
        return json_response(400, {"error": "超出棋盘"})
    with _lock, db() as conn:
        row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
        if not row:
            return json_response(404, {"error": "房间不存在"})
        if row["status"] != "playing":
            return json_response(400, {"error": "对局未在进行中"})
        if _gomoku_check_timeout(conn, row):
            conn.commit()
            _broadcast(code, None)
            return json_response(400, {"error": "回合超时，对局已结束"})
        row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
        color = 1 if user["id"] == row["player_black"] else (2 if user["id"] == row["player_white"] else 0)
        if not color:
            return json_response(400, {"error": "你不是本局玩家"})
        if row["turn"] != color:
            return json_response(400, {"error": "还没轮到你"})
        board = json.loads(row["board"])
        if board[y * GOMOKU_SIZE + x]:
            return json_response(400, {"error": "这里已经有棋子"})
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
            return json_response(200, {"ok": True, "over": True, "winner": user["id"] if won else None})
        conn.execute("UPDATE gomoku_rooms SET board=?, turn=?, last_move_at=?, moves=moves+1 WHERE code=?",
                     (json.dumps(board), 3 - color, time.time(), code))
        conn.commit()
        log(conn, user["id"], user["username"], "gomoku_move", f"房间{code}落子({x},{y})", ip=ip)
    _broadcast(code, None)
    if row["mode"] == "bot" and not won and not full:
        threading.Thread(target=_gomoku_bot_turn, args=(code,), daemon=True).start()
    return json_response(200, {"ok": True, "over": False})


@router.post("/api/gomoku/leave")
async def gomoku_leave(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    code = str(data.get("code", "")).strip().upper()
    with _lock, db() as conn:
        row = conn.execute("SELECT * FROM gomoku_rooms WHERE code=?", (code,)).fetchone()
        if not row:
            return json_response(404, {"error": "房间不存在"})
        uid = user["id"]
        if row["status"] == "waiting":
            # 仅房主(player_black 创建者)可取消/删除等待中的房间;身份以服务端登录用户为准
            if row["player_black"] != uid:
                return json_response(403, {"error": "只有房主可以取消房间"})
            conn.execute("DELETE FROM gomoku_rooms WHERE code=?", (code,))
            conn.commit()
            log(conn, uid, user["username"], "gomoku_cancel", f"取消房间 {code}", ip=ip)
            _broadcast(code, None)
            return json_response(200, {"ok": True})
        if row["status"] == "playing":
            if uid not in (row["player_black"], row["player_white"]):
                return json_response(403, {"error": "你不是本局玩家"})
            opp = row["player_white"] if uid == row["player_black"] else row["player_black"]
            # 认输：胜方=对手，结束原因 resign，奖励与状态在同一事务内结算
            _finish_gomoku(conn, code, opp, "resign", ip, loser=uid)
            conn.commit()
            log(conn, uid, user["username"], "gomoku_leave", f"房间{code}认输", ip=ip)
    _broadcast(code, None)
    return json_response(200, {"ok": True})
