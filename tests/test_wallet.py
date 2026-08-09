# -*- coding: utf-8 -*-
"""Issue #55:钱包不变量回归。

任何操作后:users.points == 初始赠送 + Σ(point_ledger.amount)(同用户)。
覆盖:签到 / 游戏门票+结算 / 农场买种-种植-收获-出售 / 转盘 / 漂流瓶。
"""
import sqlite3
import time

from conftest import assert_wallet_balanced


def _gm_catches(client, seed, n=2):
    world = client.world(seed)
    catches = [{"id": it["id"], "v": it["v"]} for it in world[:n]]
    score = sum(it["v"] for it in world[:n])
    return catches, score


def test_wallet_invariant_across_operations(client):
    token = client.register("wallet_user")
    uid = client.user_id("wallet_user")
    assert_wallet_balanced(client, uid)

    # 签到
    st, body = client.post("/api/checkin", {}, token=token)
    assert st == 200
    assert_wallet_balanced(client, uid)

    # 黄金矿工:买门票 → 扣 80
    st, start = client.post("/api/game/start", {"game": "goldminer"}, token=token)
    assert st == 200
    assert_wallet_balanced(client, uid)

    # 黄金矿工结算 → 得随机奖励
    catches, score = _gm_catches(client, start["goldminer_seed"])
    st, body = client.post("/api/game/end",
                           {"game": "goldminer", "token": start["token"],
                            "score": score, "stats": {"catches": catches}}, token=token)
    assert st == 200
    assert_wallet_balanced(client, uid)

    # 农场:买种子 / 种植 / 成熟收获 / 出售
    st, body = client.post("/api/farm/buy-seed", {"crop": "carrot", "count": 2}, token=token)
    assert st == 200
    assert_wallet_balanced(client, uid)

    st, body = client.post("/api/farm/plant", {"slot": 0, "crop": "carrot"}, token=token)
    assert st == 200
    assert_wallet_balanced(client, uid)

    with sqlite3.connect(client.db_path) as conn:
        conn.execute("UPDATE farm SET planted_at=? WHERE user_id=? AND slot=0",
                     (time.time() - 40, uid))
        conn.commit()
    st, body = client.post("/api/farm/harvest", {"slot": 0}, token=token)
    assert st == 200
    assert_wallet_balanced(client, uid)

    st, body = client.post("/api/farm/sell", {"crop": "carrot"}, token=token)
    assert st == 200
    assert_wallet_balanced(client, uid)

    # 转盘
    st, body = client.post("/api/wheel/spin", {"request_id": "w1"}, token=token)
    assert st == 200
    assert_wallet_balanced(client, uid)

    # 漂流瓶(扣费,无收益)
    st, body = client.post("/api/bottle/throw", {"content": "hello world"}, token=token)
    assert st == 200
    assert_wallet_balanced(client, uid)


def test_wallet_invariant_negative_insufficient(client):
    """扣款不足 → 400 且不破坏不变量(事务回滚)。"""
    token = client.register("poor_user")
    uid = client.user_id("poor_user")
    # 花光积分:买 20 个萝卜种子(5×20=100),余额归零
    st, body = client.post("/api/farm/buy-seed", {"crop": "carrot", "count": 20}, token=token)
    assert st == 200
    assert client.user_points(uid) == 0
    assert_wallet_balanced(client, uid)
    # 老虎机(5 分)应报积分不足,不写任何流水
    st, body = client.post("/api/slot/spin", {}, token=token)
    assert st == 400
    assert "积分不足" in body["error"]
    assert_wallet_balanced(client, uid)


def test_wallet_invariant_two_users_independent(client):
    """A/B 两用户流水互不影响。"""
    ta = client.register("alice_w")
    tb = client.register("bob_w")
    uid_a = client.user_id("alice_w")
    uid_b = client.user_id("bob_w")

    client.post("/api/checkin", {}, token=ta)
    client.post("/api/game/start", {"game": "goldminer"}, token=tb)

    assert_wallet_balanced(client, uid_a)
    assert_wallet_balanced(client, uid_b)
    with sqlite3.connect(client.db_path) as conn:
        a = conn.execute("SELECT points FROM users WHERE id=?", (uid_a,)).fetchone()[0]
        b = conn.execute("SELECT points FROM users WHERE id=?", (uid_b,)).fetchone()[0]
        assert a == client.welcome + 10          # 仅签到奖励
        assert b == client.welcome - 80          # 仅扣门票
