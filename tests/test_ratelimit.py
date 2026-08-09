# -*- coding: utf-8 -*-
"""Issue #55:限流回归。

gend_h(单小时桶) / gend_d(每日桶) 独立:灌满一个桶不影响另一个桶。
结算接口先过限流再验会话,因此 429 断言无需有效 token。
"""
import sqlite3
import time


def _seed(client, key, count):
    with sqlite3.connect(client.db_path) as conn:
        conn.execute("INSERT OR REPLACE INTO rate_limits(key,count,window_start) VALUES(?,?,?)",
                     (key, count, time.time()))
        conn.commit()


def _count(client, key):
    with sqlite3.connect(client.db_path) as conn:
        row = conn.execute("SELECT count FROM rate_limits WHERE key=?", (key,)).fetchone()
        return row[0] if row else 0


def test_gend_hour_and_day_buckets_independent(client):
    token = client.register("rate_user")
    h_key = "gend_h:rate_user:goldminer"
    d_key = "gend_d:rate_user:goldminer"

    # 先做一次真实结算,两个桶各 +1(证明结算确实同时写两桶)
    st, start = client.post("/api/game/start", {"game": "goldminer"}, token=token)
    assert st == 200
    world = client.world(start["goldminer_seed"])
    catches = [{"id": it["id"], "v": it["v"]} for it in world[:1]]
    score = sum(it["v"] for it in world[:1])
    st, body = client.post("/api/game/end",
                           {"game": "goldminer", "token": start["token"],
                            "score": score, "stats": {"catches": catches}}, token=token)
    assert st == 200
    assert _count(client, h_key) == 1
    assert _count(client, d_key) == 1

    # 方向 A:灌满小时桶(6),每日桶仍远未满 → 提交被小时限流拒绝(429),且每日桶不增加
    _seed(client, h_key, 6)
    st, body = client.post("/api/game/end",
                           {"game": "goldminer", "token": "bogus",
                            "score": 0, "stats": {}}, token=token)
    assert st == 429
    assert "小时" in body["error"] or "频繁" in body["error"]
    assert _count(client, d_key) == 1          # 每日桶未被小时桶连带消耗

    # 方向 B:灌满每日桶(40),小时桶归零 → 提交被每日限流拒绝(429),小时桶不受影响
    _seed(client, d_key, 40)
    _seed(client, h_key, 0)
    st, body = client.post("/api/game/end",
                           {"game": "goldminer", "token": "bogus",
                            "score": 0, "stats": {}}, token=token)
    assert st == 429
    assert "今日" in body["error"]
    assert _count(client, h_key) == 1          # 小时桶从 0 正常递增到 1,未被每日桶打满
    assert _count(client, d_key) == 40         # 每日桶仍为上限


def test_gend_buckets_not_shared_across_games(client):
    """不同游戏各自的 gend_h / gend_d 桶互不共享。"""
    token = client.register("rate_user2")
    _seed(client, "gend_h:rate_user2:goldminer", 6)
    _seed(client, "gend_d:rate_user2:rhythm", 0)
    # goldminer 小时桶满,但 rhythm 的 gend_h 桶独立为空 → rhythm 结算不会被 goldminer 连带限流
    st, body = client.post("/api/game/end",
                           {"game": "rhythm", "token": "bogus",
                            "score": 0, "stats": {}}, token=token)
    assert st == 400                          # 先过限流,再因无效会话被拒
    assert "会话无效" in body["error"]
    assert _count(client, "gend_h:rate_user2:rhythm") == 1     # rhythm 小时桶独立 +1
    assert _count(client, "gend_h:rate_user2:goldminer") == 6  # goldminer 桶未被写
