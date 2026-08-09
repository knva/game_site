# -*- coding: utf-8 -*-
"""Issue #55:核心 API 契约回归。

登录/注册/me、游戏 start/end、农场种收、转盘 spin、错误码(400/401/403/404/429)。
两套后端(stdlib / fastapi)同一套断言,保证契约对齐。
"""
import sqlite3
import time

import pytest

from conftest import assert_wallet_balanced


def _gm_catches(client, seed, n=2):
    world = client.world(seed)
    catches = [{"id": it["id"], "v": it["v"]} for it in world[:n]]
    score = sum(it["v"] for it in world[:n])
    return catches, score


# ---------------- 注册 / 登录 / me ----------------
def test_register_login_me_contract(client):
    # 昵称不合法 → 400
    st, body = client.post("/api/register", {"username": "x", "password": "test1234"})
    assert st == 400
    # 密码过短 → 400
    st, body = client.post("/api/register", {"username": "alice", "password": "12"})
    assert st == 400

    token = client.register("alice")

    # me 需登录 → 401
    st, body = client.get("/api/me")
    assert st == 401

    # me 返回用户信息
    st, me = client.get("/api/me", token=token)
    assert st == 200
    assert me["user"]["username"] == "alice"
    assert me["user"]["points"] == client.welcome
    assert "vip" in me["user"]
    assert "unread" in me and "today_earned" in me and "daily_cap" in me

    # 登录成功(API 客户端模式 X-Token 回传 token)
    st, body = client.post("/api/login", {"username": "alice", "password": "test1234"},
                           token="xt-client")
    assert st == 200 and body["ok"] and body["user"]["username"] == "alice" and "token" in body

    # 密码错误 → 400
    st, body = client.post("/api/login", {"username": "alice", "password": "wrong"})
    assert st == 400

    # 重复注册同名 → 400
    st, body = client.post("/api/register", {"username": "alice", "password": "test1234"})
    assert st == 400

    # 封禁账号登录 → 403
    uid = client.user_id("alice")
    with sqlite3.connect(client.db_path) as conn:
        conn.execute("UPDATE users SET status='banned' WHERE id=?", (uid,))
        conn.commit()
    st, body = client.post("/api/login", {"username": "alice", "password": "test1234"})
    assert st == 403

    # 未知接口 → 404
    st, body = client.post("/api/nonexistent", {})
    assert st == 404
    st, body = client.get("/api/nonexistent")
    assert st == 404


def test_me_consistency_after_operations(client):
    """多次操作后 me 的 points 与钱包流水一致(契约 + 不变量双保险)。"""
    token = client.register("charlie")
    st, body = client.post("/api/checkin", {}, token=token)
    assert st == 200
    uid = client.user_id("charlie")
    assert_wallet_balanced(client, uid)
    st, me = client.get("/api/me", token=token)
    assert me["user"]["points"] == client.user_points(uid)


# ---------------- 游戏 start / end ----------------
def test_game_start_end_contract(client):
    token = client.register("bob")

    # 未知游戏 → 400
    st, body = client.post("/api/game/start", {"game": "nope"}, token=token)
    assert st == 400

    # goldminer 开局(扣门票)
    st, start = client.post("/api/game/start", {"game": "goldminer"}, token=token)
    assert st == 200 and start["ok"]
    assert start["token"] and start["goldminer_seed"] is not None
    uid = client.user_id("bob")
    assert client.user_points(uid) == client.welcome - 80

    # 无效 token 结算 → 400
    st, body = client.post("/api/game/end",
                           {"game": "goldminer", "token": "bogus", "score": 100, "stats": {}},
                           token=token)
    assert st == 400

    # 分数超上限 → 400
    st, body = client.post("/api/game/end",
                           {"game": "goldminer", "token": start["token"],
                            "score": 999999, "stats": {}}, token=token)
    assert st == 400

    # 正常结算 → 200,积分变动
    catches, score = _gm_catches(client, start["goldminer_seed"])
    st, body = client.post("/api/game/end",
                           {"game": "goldminer", "token": start["token"],
                            "score": score, "stats": {"catches": catches}}, token=token)
    assert st == 200 and body["ok"]
    assert_wallet_balanced(client, uid)

    # rhythm 开局返回服务器谱面
    st, start = client.post("/api/game/start", {"game": "rhythm"}, token=token)
    assert st == 200
    assert isinstance(start["chart"], list) and len(start["chart"]) > 0
    assert start["max_score"] > 0

    # 未知游戏结算 → 400
    st, body = client.post("/api/game/end",
                           {"game": "nope", "token": start["token"], "score": 0, "stats": {}},
                           token=token)
    assert st == 400


def test_rhythm_happy_path(client):
    """rhythm 正常结算(服务器按谱面重判,时间线覆盖末键 + 真实经过时长通过校验)。"""
    token = client.register("rhythm_player")
    st, start = client.post("/api/game/start", {"game": "rhythm"}, token=token)
    assert st == 200
    chart = sorted(start["chart"], key=lambda n: n["t"])
    chart_last = chart[-1]["t"]
    uid = client.user_id("rhythm_player")
    # 开局时间拨回谱面末尾之前(约 80s),使 elapsed 足够覆盖末键,免真实等待
    with sqlite3.connect(client.db_path) as conn:
        conn.execute("UPDATE game_sessions SET created_at=? WHERE token=?",
                     (time.time() - (chart_last + 5), start["token"]))
        conn.commit()
    # 时间线与谱面同构但整行错位一个轨道(满足覆盖/密度/时长校验,真实游玩特征),
    # 判定得分远低于每日上限,避免 30000 积分 cap 干扰结算
    timeline = [{"t": n["t"], "lane": (n["lane"] + 1) % 8} for n in chart]
    st, body = client.post("/api/game/end",
                           {"game": "rhythm", "token": start["token"],
                            "score": 0, "stats": {"timeline": timeline}}, token=token)
    assert st == 200, (st, body)
    assert body["ok"] and body["earned"] >= 0
    assert_wallet_balanced(client, uid)


# ---------------- 农场:种 / 收 ----------------
def test_farm_plant_harvest_contract(client):
    token = client.register("farmer")

    # 无种子种植 → 400
    st, body = client.post("/api/farm/plant", {"slot": 0, "crop": "carrot"}, token=token)
    assert st == 400

    # 买种子 → 200
    st, body = client.post("/api/farm/buy-seed", {"crop": "carrot", "count": 1}, token=token)
    assert st == 200 and body["ok"]

    # 种植 → 200
    st, body = client.post("/api/farm/plant", {"slot": 0, "crop": "carrot"}, token=token)
    assert st == 200 and body["ok"]

    # 未成熟收获 → 400
    st, body = client.post("/api/farm/harvest", {"slot": 0}, token=token)
    assert st == 400

    # 直接推进成熟(测试直连 DB 制造状态)再收获 → 200
    uid = client.user_id("farmer")
    with sqlite3.connect(client.db_path) as conn:
        conn.execute("UPDATE farm SET planted_at=? WHERE user_id=? AND slot=0",
                     (time.time() - 40, uid))
        conn.commit()
    st, body = client.post("/api/farm/harvest", {"slot": 0}, token=token)
    assert st == 200 and body["ok"]
    assert_wallet_balanced(client, uid)

    # 出售入库作物 → 200
    st, body = client.post("/api/farm/sell", {"crop": "carrot"}, token=token)
    assert st == 200 and body["ok"]
    assert_wallet_balanced(client, uid)


# ---------------- 转盘 spin ----------------
def test_wheel_spin_contract(client):
    token = client.register("spinner")
    uid = client.user_id("spinner")

    # 抽奖 → 200,返回扇区/奖品
    st, body = client.post("/api/wheel/spin", {"request_id": "r1"}, token=token)
    assert st == 200 and body["ok"]
    assert "prize" in body and "sector" in body and "points" in body
    assert_wallet_balanced(client, uid)

    # 同一 request_id 幂等:返回相同结果,不重复扣费
    st, body2 = client.post("/api/wheel/spin", {"request_id": "r1"}, token=token)
    assert st == 200 and body2 == body
    assert_wallet_balanced(client, uid)


# ---------------- 公开接口 ----------------
def test_game_odds_public(client):
    st, odds = client.get("/api/game/odds")
    assert st == 200
    for key in ("goldminer", "slot", "wheel", "rhythm", "farm"):
        assert key in odds
    assert "ticket" in odds["goldminer"]
    assert "crops" in odds["farm"]


# ---------------- 限流 429 ----------------
def test_register_throttled(client):
    """同一 IP 注册频繁 → 429(注册本身限流 reg:{ip} 10/h)。"""
    ip_bucket = "reg:127.0.0.1"
    if client.backend == "fastapi":
        ip_bucket = "reg:testclient"
    for i in range(10):
        st, body = client.post("/api/register",
                               {"username": f"user{i:02d}", "password": "test1234"})
        assert st == 200, (st, body)
    # 第 11 次应触发 429
    st, body = client.post("/api/register",
                           {"username": "userZZ", "password": "test1234"})
    assert st == 429
    # 确认限流桶已计数到上限
    with sqlite3.connect(client.db_path) as conn:
        row = conn.execute("SELECT count FROM rate_limits WHERE key=?", (ip_bucket,)).fetchone()
        assert row and row[0] >= 10
