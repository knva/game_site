# -*- coding: utf-8 -*-
"""Issue #21:游戏参数配置(draft/published + 内存缓存)。

读取侧:config_get(name, default) 从 game_configs 已发布版本读取,带内存缓存,
无记录或异常时回退调用方传入的硬编码默认值。
管理侧:config_set(写 draft) / config_publish(发布 draft → published) /
config_rollback(发布上一个 published 版本,无历史则回退硬编码默认值)。
校验:数值型参数强制数字;门票/费用/上限必须为正整数;保底奖励 ≤ 最高奖励。
"""
import re
import threading
import time

from .db import _lock, db

CONFIG_DEFAULTS = {
    "goldminer_ticket": 80,
    "goldminer_pay_min": 100,
    "goldminer_pay_max": 200,
    "slot_cost": 5,
    "wheel_cost": 10,
    "daily_earned_cap": 30000,
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
    """发布/回滚后使缓存失效(游戏逻辑立即读到新值)。"""
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
        mx = config_get("goldminer_pay_max", CONFIG_DEFAULTS["goldminer_pay_max"])
        if mx is not None and num > mx:
            raise ValueError("保底奖励不能大于最高奖励")
    if name == "goldminer_pay_max":
        mn = config_get("goldminer_pay_min", CONFIG_DEFAULTS["goldminer_pay_min"])
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


def admin_config_list(conn):
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
