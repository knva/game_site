# -*- coding: utf-8 -*-
"""农场模块:开地 / 升级 / 建筑 / 种植 / 浇水 / 收获 / 出售 / 偷菜 / 体力。

SQLite 差异点:多处 `INSERT OR REPLACE`(PG 用 `INSERT ... ON CONFLICT DO UPDATE`)、
`ON CONFLICT(user_id,crop) DO UPDATE`(PG 用 `EXCLUDED.count`)。
"""
import time

from fastapi import APIRouter, Request

from . import config
from .auth import get_user_by_name, is_vip, me, user_level
from .db import _lock, db
from .gameconfig import config_get
from .http import json_response, parse_body
from .wallet import (add_daily_earned, change_points, daily_earned, log, rate_check,
                     _rate_peek)

router = APIRouter()

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
    import random
    tid = random.choice(pool)
    state = farm_state(conn, tid, user_id, username)
    state["steal_daily_left"] = STEAL_DAILY_MAX - _rate_peek(f"steal:{username}", 86400)
    return state


# ================= 路由 =================
@router.get("/api/farm")
def farm_get(request: Request):
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    target_name = request.query_params.get("target", "").strip()
    with _lock, db() as conn:
        if target_name:
            t = get_user_by_name(conn, target_name)
            if not t:
                return json_response(404, {"error": "该用户不存在"})
            if t["id"] == user["id"]:
                return json_response(200, farm_state(conn, t["id"], user["id"], user["username"]))
            state = farm_state(conn, t["id"], user["id"], user["username"])
            state["steal_daily_left"] = STEAL_DAILY_MAX - _rate_peek(f"steal:{user['username']}", 86400)
            return json_response(200, state)
        return json_response(200, farm_state(conn, user["id"]))


@router.get("/api/farm/steal-random")
def farm_steal_random_get(request: Request):
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    with _lock, db() as conn:
        state = farm_steal_random_state(conn, user["id"], user["username"])
    if state is None:
        return json_response(404, {"error": "暂时没有可以偷的目标，稍后再来试试"})
    return json_response(200, state)


@router.post("/api/farm/steal-random")
async def farm_steal_random_post(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    with _lock, db() as conn:
        state = farm_steal_random_state(conn, user["id"], user["username"])
    if state is None:
        return json_response(404, {"error": "暂时没有可以偷的目标，稍后再来试试"})
    return json_response(200, state)


@router.post("/api/farm/plant")
async def farm_plant(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    try:
        slot = int(data.get("slot", -1))
    except Exception:
        return json_response(400, {"error": "槽位错误"})
    crop = str(data.get("crop", ""))
    if crop not in CROPS or not (0 <= slot < PLOT_COUNT):
        return json_response(400, {"error": "参数不合法"})
    if CROPS[crop].get("vip") and not is_vip(user):
        return json_response(400, {"error": "这是 VIP 专属作物，开通 VIP 后才能种植"})
    with _lock, db() as conn:
        uid = user["id"]
        seed = conn.execute("SELECT count FROM farm_seeds WHERE user_id=? AND crop=?",
                            (uid, crop)).fetchone()
        if not seed or seed["count"] < 1:
            return json_response(400, {"error": "没有种子，先到种子商店购买"})
        pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                          (uid, slot)).fetchone()
        if not (pr and pr["unlocked"]) and slot >= DEFAULT_PLOTS:
            return json_response(400, {"error": "该地块还未开垦"})
        cur = conn.execute("SELECT * FROM farm WHERE user_id=? AND slot=?", (uid, slot)).fetchone()
        if cur and cur["crop"] is not None:
            gh = building_level(conn, uid, "greenhouse")
            lv = pr["level"] if pr else 1
            if time.time() < cur["planted_at"] + farm_grow_seconds(cur["crop"], lv, gh):
                return json_response(400, {"error": "该地块还没成熟"})
        conn.execute("UPDATE farm_seeds SET count=count-1 WHERE user_id=? AND crop=?", (uid, crop))
        # SQLite `INSERT OR REPLACE` → PG: `INSERT ... ON CONFLICT(user_id,slot) DO UPDATE SET ...`
        conn.execute("INSERT OR REPLACE INTO farm(user_id,slot,crop,planted_at,waters,stolen,stolen_by) VALUES(?,?,?,?,0,0,NULL)",
                     (uid, slot, crop, time.time()))
        conn.execute("DELETE FROM farm_seeds WHERE user_id=? AND crop=? AND count<=0", (uid, crop))
        conn.commit()
    return json_response(200, {"ok": True,
                               "farm": farm_state(conn, uid, uid, user["username"])})


# 批量播种(单事务校验种子/地块,逐格返回结果;种子不足时按数量截断)
@router.post("/api/farm/batch-plant")
async def farm_batch_plant(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    raw = data.get("slot_list")
    if not isinstance(raw, list) or not raw:
        return json_response(400, {"error": "slot_list 必须是数组"})
    crop = str(data.get("crop", ""))
    if crop not in CROPS:
        return json_response(400, {"error": "作物不存在"})
    if CROPS[crop].get("vip") and not is_vip(user):
        return json_response(400, {"error": "这是 VIP 专属作物，开通 VIP 后才能种植"})
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
        return json_response(400, {"error": "没有可操作的槽位"})
    results = {}
    with _lock, db() as conn:
        uid = user["id"]
        seed = conn.execute("SELECT count FROM farm_seeds WHERE user_id=? AND crop=?",
                            (uid, crop)).fetchone()
        if not seed or seed["count"] < 1:
            return json_response(400, {"error": "没有种子，先到种子商店购买"})
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
        if len(ok_slots) > seed["count"]:
            for slot in ok_slots[seed["count"]:]:
                bad[slot] = "种子不足"
            ok_slots = ok_slots[:seed["count"]]
        for slot in ok_slots:
            conn.execute("UPDATE farm_seeds SET count=count-1 WHERE user_id=? AND crop=?", (uid, crop))
            # SQLite `INSERT OR REPLACE` → PG: `INSERT ... ON CONFLICT(user_id,slot) DO UPDATE SET ...`
            conn.execute("INSERT OR REPLACE INTO farm(user_id,slot,crop,planted_at,waters,stolen,stolen_by) VALUES(?,?,?,?,0,0,NULL)",
                         (uid, slot, crop, time.time()))
            results[slot] = {"ok": True}
        for slot, err in bad.items():
            results[slot] = {"ok": False, "error": err}
        conn.execute("DELETE FROM farm_seeds WHERE user_id=? AND crop=? AND count<=0", (uid, crop))
        conn.commit()
    return json_response(200, {"results": results, "points": user["points"],
                               "farm": farm_state(conn, uid, uid, user["username"])})


# 批量收获(单事务逐格收获,返回每格结果;仓库容量不足的格子保留原样)
@router.post("/api/farm/batch-harvest")
async def farm_batch_harvest(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    raw = data.get("slot_list")
    if not isinstance(raw, list) or not raw:
        return json_response(400, {"error": "slot_list 必须是数组"})
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
        return json_response(400, {"error": "没有可操作的槽位"})
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
                         "ON CONFLICT(user_id,crop) DO UPDATE SET count=count+1",  # PG: EXCLUDED.count
                         (uid, row["crop"]))
            conn.execute("DELETE FROM farm WHERE user_id=? AND slot=?", (uid, slot))
            conn.execute("UPDATE users SET exp=exp+5 WHERE id=?", (uid,))
            units += size
            results[slot] = {"ok": True, "crop": row["crop"], "name": CROPS[row["crop"]]["name"]}
            log(conn, uid, user["username"], "farm_harvest", f"收获{CROPS[row['crop']]['name']}入仓", ip=ip)
        conn.commit()
    return json_response(200, {"results": results, "points": user["points"],
                               "farm": farm_state(conn, uid, uid, user["username"])})


# 购买种子(道具,不可出售)
@router.post("/api/farm/buy-seed")
async def farm_buy_seed(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    crop = str(data.get("crop", ""))
    try:
        count = max(1, min(99, int(data.get("count", 1))))
    except Exception:
        return json_response(400, {"error": "数量错误"})
    if crop not in CROPS:
        return json_response(400, {"error": "作物不存在"})
    if CROPS[crop].get("vip") and not is_vip(user):
        return json_response(400, {"error": "这是 VIP 专属作物，开通 VIP 后才能购买"})
    cost = CROPS[crop]["cost"] * count
    with _lock, db() as conn:
        if user["points"] < cost:
            return json_response(400, {"error": f"积分不足，需要 {cost} 积分"})
        points = change_points(conn, user["id"], user["username"], -cost,
                               "farm_buy_seed", f"购买{count}个{CROPS[crop]['name']}种子", ip)
        conn.execute("INSERT INTO farm_seeds(user_id,crop,count) VALUES(?,?,?) "
                     "ON CONFLICT(user_id,crop) DO UPDATE SET count=count+?",  # PG: EXCLUDED.count
                     (user["id"], crop, count, count))
        conn.commit()
    return json_response(200, {"ok": True, "points": points,
                               "farm": farm_state(conn, user["id"], user["id"], user["username"])})


@router.post("/api/farm/water")
async def farm_water(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    try:
        slot = int(data.get("slot", -1))
    except Exception:
        return json_response(400, {"error": "槽位错误"})
    with _lock, db() as conn:
        row = conn.execute("SELECT * FROM farm WHERE user_id=? AND slot=?", (user["id"], slot)).fetchone()
        if not row or not row["crop"]:
            return json_response(400, {"error": "没有作物"})
        limit = WATER_LIMIT + building_level(conn, user["id"], "well")
        gh = building_level(conn, user["id"], "greenhouse")
        pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                          (user["id"], slot)).fetchone()
        lv = pr["level"] if pr else 1
        grow = farm_grow_seconds(row["crop"], lv, gh)
        if row["planted_at"] + grow - time.time() <= 0:
            return json_response(400, {"error": "已成熟，无需浇水"})
        if row["waters"] >= limit:
            return json_response(400, {"error": f"浇水次数已用完（{limit} 次）"})
        conn.execute("UPDATE farm SET waters=waters+1, planted_at=planted_at-? WHERE user_id=? AND slot=?",
                     (WATER_SECONDS, user["id"], slot))
        conn.commit()
    return json_response(200, {"ok": True,
                               "farm": farm_state(conn, user["id"], user["id"], user["username"])})


@router.post("/api/farm/harvest")
async def farm_harvest(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    try:
        slot = int(data.get("slot", -1))
    except Exception:
        return json_response(400, {"error": "槽位错误"})
    with _lock, db() as conn:
        row = conn.execute("SELECT * FROM farm WHERE user_id=? AND slot=?", (user["id"], slot)).fetchone()
        if not row or not row["crop"]:
            return json_response(400, {"error": "没有作物"})
        gh = building_level(conn, user["id"], "greenhouse")
        pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                          (user["id"], slot)).fetchone()
        lv = pr["level"] if pr else 1
        grow = farm_grow_seconds(row["crop"], lv, gh)
        if time.time() < row["planted_at"] + grow:
            return json_response(400, {"error": "还没成熟"})
        if row["stolen"]:
            return json_response(400, {"error": f"作物已被 {row['stolen_by']} 偷走了！"})
        size = CROP_SIZE.get(row["crop"], 1)
        sh = building_level(conn, user["id"], "storehouse")
        inv, units = farm_inventory(conn, user["id"])
        capacity = farm_capacity(conn, user["id"])
        if units + size > capacity:
            return json_response(400, {"error": f"仓库已满（{units}/{capacity}），请先出售作物"})
        conn.execute("INSERT INTO inventory(user_id,crop,count) VALUES(?,?,1) "
                     "ON CONFLICT(user_id,crop) DO UPDATE SET count=count+1",  # PG: EXCLUDED.count
                     (user["id"], row["crop"]))
        conn.execute("DELETE FROM farm WHERE user_id=? AND slot=?", (user["id"], slot))
        conn.execute("UPDATE users SET exp=exp+5 WHERE id=?", (user["id"],))   # 收获经验
        conn.commit()
        log(conn, user["id"], user["username"], "farm_harvest", f"收获{CROPS[row['crop']]['name']}入仓", ip=ip)
    return json_response(200, {"ok": True,
                               "farm": farm_state(conn, user["id"], user["id"], user["username"])})


@router.post("/api/farm/sell")
async def farm_sell(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    crop = str(data.get("crop", ""))
    if crop not in CROPS:
        return json_response(400, {"error": "作物不存在"})
    today = time.strftime("%Y-%m-%d")
    with _lock, db() as conn:
        inv = conn.execute("SELECT * FROM inventory WHERE user_id=? AND crop=?",
                           (user["id"], crop)).fetchone()
        if not inv or inv["count"] <= 0:
            return json_response(400, {"error": "仓库里没有这种作物"})
        sh = building_level(conn, user["id"], "storehouse")
        unit = farm_sell_value(crop, sh)
        earned = inv["count"] * unit
        daily_cap = config_get("daily_earned_cap", config.DAILY_EARNED_CAP)
        remain = daily_cap - daily_earned(user["username"], today)
        if earned > remain:
            sell_count = remain // unit
            if sell_count <= 0:
                return json_response(400, {"error": f"今日可赚积分已达上限（{daily_cap}）"})
            conn.execute("UPDATE inventory SET count=count-? WHERE user_id=? AND crop=?",
                         (sell_count, user["id"], crop))
            earned = sell_count * unit
        else:
            conn.execute("DELETE FROM inventory WHERE user_id=? AND crop=?",
                         (user["id"], crop))
        points = change_points(conn, user["id"], user["username"], earned,
                               "farm_sell", f"出售{CROPS[crop]['name']}", ip)
        add_daily_earned(user["username"], earned, today)
    return json_response(200, {"ok": True, "points": points, "earned": earned,
                               "farm": farm_state(conn, user["id"], user["id"], user["username"])})


@router.post("/api/farm/unlock")
async def farm_unlock(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    try:
        slot = int(data.get("slot", -1))
    except Exception:
        return json_response(400, {"error": "槽位错误"})
    if not (0 <= slot < PLOT_COUNT) or slot < DEFAULT_PLOTS:
        return json_response(400, {"error": "该地块无需开垦"})
    if user_level(user) < PLOT_UNLOCK_LEVELS[slot]:
        return json_response(400, {"error": f"需要达到 {PLOT_UNLOCK_LEVELS[slot]} 级才能开垦"})
    cost = PLOT_UNLOCK_COSTS[slot]
    with _lock, db() as conn:
        pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                          (user["id"], slot)).fetchone()
        if pr and pr["unlocked"]:
            return json_response(400, {"error": "已开垦"})
        if user["points"] < cost:
            return json_response(400, {"error": f"开地需要 {cost} 积分"})
        # SQLite `INSERT OR REPLACE` → PG: `INSERT ... ON CONFLICT(user_id,slot) DO UPDATE ...`
        conn.execute("INSERT OR REPLACE INTO farm_plots(user_id,slot,unlocked,level) VALUES(?,?,1,1)",
                     (user["id"], slot))
        points = change_points(conn, user["id"], user["username"], -cost,
                               "farm_unlock", f"开垦第{slot + 1}块地", ip)
    return json_response(200, {"ok": True, "points": points,
                               "farm": farm_state(conn, user["id"], user["id"], user["username"])})


@router.post("/api/farm/upgrade")
async def farm_upgrade(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    try:
        slot = int(data.get("slot", -1))
    except Exception:
        return json_response(400, {"error": "槽位错误"})
    with _lock, db() as conn:
        pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                          (user["id"], slot)).fetchone()
        if slot >= DEFAULT_PLOTS and not (pr and pr["unlocked"]):
            return json_response(400, {"error": "请先开垦该地块"})
        lv = pr["level"] if pr else 1
        if lv >= PLOT_MAX_LEVEL:
            return json_response(400, {"error": "地块已满级"})
        cost = lv * PLOT_UPGRADE_BASE
        if user["points"] < cost:
            return json_response(400, {"error": f"升级需要 {cost} 积分"})
        # SQLite `INSERT OR REPLACE` → PG: `INSERT ... ON CONFLICT(user_id,slot) DO UPDATE ...`
        conn.execute("INSERT OR REPLACE INTO farm_plots(user_id,slot,unlocked,level) VALUES(?,?,1,?)",
                     (user["id"], slot, lv + 1))
        points = change_points(conn, user["id"], user["username"], -cost,
                               "farm_upgrade", f"地块{slot + 1}升到{lv + 1}级", ip)
    return json_response(200, {"ok": True, "points": points,
                               "farm": farm_state(conn, user["id"], user["id"], user["username"])})


@router.post("/api/farm/building")
async def farm_building(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    name = str(data.get("name", ""))
    if name not in BUILDINGS:
        return json_response(400, {"error": "建筑不存在"})
    with _lock, db() as conn:
        lv = building_level(conn, user["id"], name)
        if lv >= BUILDING_MAX_LEVEL:
            return json_response(400, {"error": "该建筑已满级"})
        cost = building_upgrade_cost(name, lv)
        if user["points"] < cost:
            return json_response(400, {"error": f"升级需要 {cost} 积分"})
        conn.execute("INSERT INTO user_buildings(user_id,name,level) VALUES(?,?,?) "
                     "ON CONFLICT(user_id,name) DO UPDATE SET level=level+1",  # PG: EXCLUDED.level
                     (user["id"], name, lv + 1))
        points = change_points(conn, user["id"], user["username"], -cost,
                               "building_upgrade", f"{BUILDINGS[name]['name']}升到{lv + 1}级", ip)
    return json_response(200, {"ok": True, "points": points,
                               "farm": farm_state(conn, user["id"], user["id"], user["username"])})


@router.post("/api/farm/steal-toggle")
async def farm_steal_toggle(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    open_flag = 1 if data.get("open") else 0
    with _lock, db() as conn:
        conn.execute("UPDATE users SET steal_open=? WHERE id=?", (open_flag, user["id"]))
        conn.commit()
        log(conn, user["id"], user["username"], "steal_toggle",
            "开启偷菜" if open_flag else "关闭偷菜", ip=ip)
    return json_response(200, {"ok": True, "steal_open": bool(open_flag)})


@router.post("/api/farm/steal")
async def farm_steal(request: Request):
    data = await parse_body(request)
    if data is None:
        return json_response(400, {"error": "请求格式错误"})
    ip = request.client.host if request.client else ""
    user = me(request)
    if not user:
        return json_response(401, {"error": "未登录"})
    target_name = str(data.get("target", "")).strip()
    try:
        slot = int(data.get("slot", -1))
    except Exception:
        return json_response(400, {"error": "槽位错误"})
    today = time.strftime("%Y-%m-%d")
    with _lock, db() as conn:
        own = conn.execute("SELECT steal_open FROM users WHERE id=?", (user["id"],)).fetchone()
        if own and not own["steal_open"]:
            return json_response(400, {"error": "你已关闭偷菜，请先打开偷菜开关"})
        if not rate_check(f"steal:{user['username']}", STEAL_DAILY_MAX, 86400):
            return json_response(400, {"error": f"今日偷菜次数已达上限（{STEAL_DAILY_MAX} 次）"})
        if not rate_check(f"stealh:{user['username']}", 6, 3600):
            return json_response(429, {"error": "偷得太频繁了，歇会儿吧"})
        target = get_user_by_name(conn, target_name)
        if not target:
            return json_response(404, {"error": "目标用户不存在"})
        if target["id"] == user["id"]:
            return json_response(400, {"error": "不能偷自己的菜"})
        if not target["steal_open"]:
            return json_response(400, {"error": "对方已关闭偷菜"})
        st = stamina_state(conn, user)
        if st["current"] < STEAL_STAMINA_COST:
            return json_response(400, {"error": f"体力不足，偷菜需要 {STEAL_STAMINA_COST} 点体力"})
        row = conn.execute("SELECT * FROM farm WHERE user_id=? AND slot=?", (target["id"], slot)).fetchone()
        if not row or not row["crop"]:
            return json_response(400, {"error": "对方这块地没有作物"})
        if row["stolen"]:
            return json_response(400, {"error": "作物已经被偷过了"})
        gh = building_level(conn, target["id"], "greenhouse")
        pr = conn.execute("SELECT * FROM farm_plots WHERE user_id=? AND slot=?",
                          (target["id"], slot)).fetchone()
        lv = pr["level"] if pr else 1
        grow = farm_grow_seconds(row["crop"], lv, gh)
        if time.time() < row["planted_at"] + grow:
            return json_response(400, {"error": "对方作物还没成熟"})
        sh = building_level(conn, target["id"], "storehouse")
        reward = max(1, round(farm_sell_value(row["crop"], sh) * STEAL_RATE))
        remain = config_get("daily_earned_cap", config.DAILY_EARNED_CAP) - daily_earned(user["username"], today)
        if reward > remain:
            return json_response(400, {"error": "今日积分已达上限，明天再来偷吧"})
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
    return json_response(200, {"ok": True, "points": points, "reward": reward,
                               "target": target_name,
                               "farm": farm_state(conn, user["id"], user["id"], user["username"]),
                               "target_farm": farm_state(conn, target["id"], user["id"], user["username"])})
