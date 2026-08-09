# -*- coding: utf-8 -*-
"""Issue #55:防作弊回归。

- goldminer 错 id 抓取被拒
- rhythm 原样回传谱面被拒
- 结算幂等:同一 token 重复提交只结算一次
"""
import sqlite3

from conftest import assert_wallet_balanced


def test_goldminer_wrong_id_rejected(client):
    token = client.register("gm_hacker")
    st, start = client.post("/api/game/start", {"game": "goldminer"}, token=token)
    assert st == 200
    # 抓取轨迹带不存在的物品 id(9999)→ 400,不结算
    st, body = client.post("/api/game/end",
                           {"game": "goldminer", "token": start["token"],
                            "score": 100, "stats": {"catches": [{"id": 9999, "v": 100}]}},
                           token=token)
    assert st == 400
    assert "不符" in body["error"]
    uid = client.user_id("gm_hacker")
    with sqlite3.connect(client.db_path) as conn:
        used = conn.execute("SELECT used FROM game_sessions WHERE token=?",
                            (start["token"],)).fetchone()[0]
        assert used == 0          # 会话未被消费,可重试
    assert_wallet_balanced(client, uid)


def test_goldminer_score_mismatch_rejected(client):
    """goldminer 分数与抓取轨迹不符被拒。"""
    token = client.register("gm_hacker2")
    st, start = client.post("/api/game/start", {"game": "goldminer"}, token=token)
    assert st == 200
    world = client.world(start["goldminer_seed"])
    catches = [{"id": it["id"], "v": it["v"]} for it in world[:1]]
    total = sum(it["v"] for it in world[:1])
    # 故意把 score 写错(与实际轨迹价值不符,且不超 6000 上限)→ 400
    st, body = client.post("/api/game/end",
                           {"game": "goldminer", "token": start["token"],
                            "score": total + 1, "stats": {"catches": catches}},
                           token=token)
    assert st == 400
    assert "不符" in body["error"]


def test_rhythm_raw_chart_echo_rejected(client):
    token = client.register("rhythm_hacker")
    st, start = client.post("/api/game/start", {"game": "rhythm"}, token=token)
    assert st == 200
    chart = start["chart"]
    # 把服务器谱面原样当时间线回传(立即回传,无真实游玩时长)→ 400
    timeline = [{"t": n["t"], "lane": n["lane"]} for n in chart]
    st, body = client.post("/api/game/end",
                           {"game": "rhythm", "token": start["token"],
                            "score": len(chart) * 200,
                            "stats": {"timeline": timeline}},
                           token=token)
    assert st == 400
    assert "时长异常" in body["error"]
    uid = client.user_id("rhythm_hacker")
    assert_wallet_balanced(client, uid)


def test_settlement_idempotent(client):
    """同一 token 重复结算只生效一次(used=1 + 幂等 biz_no)。"""
    token = client.register("settle_idem")
    st, start = client.post("/api/game/start", {"game": "goldminer"}, token=token)
    assert st == 200
    uid = client.user_id("settle_idem")
    world = client.world(start["goldminer_seed"])
    catches = [{"id": it["id"], "v": it["v"]} for it in world[:2]]
    score = sum(it["v"] for it in world[:2])

    st, body = client.post("/api/game/end",
                           {"game": "goldminer", "token": start["token"],
                            "score": score, "stats": {"catches": catches}},
                           token=token)
    assert st == 200 and body["ok"]
    earned = body["earned"]
    p0 = client.user_points(uid)
    assert_wallet_balanced(client, uid)

    # 重复提交同一 token → 400,积分不变,流水不变
    st, body2 = client.post("/api/game/end",
                            {"game": "goldminer", "token": start["token"],
                             "score": score, "stats": {"catches": catches}},
                            token=token)
    assert st == 400
    assert "已结算" in body2["error"]
    p1 = client.user_points(uid)
    assert p1 == p0
    with sqlite3.connect(client.db_path) as conn:
        used = conn.execute("SELECT used FROM game_sessions WHERE token=?",
                            (start["token"],)).fetchone()[0]
        assert used == 1
        n = conn.execute("SELECT COUNT(*) FROM point_ledger WHERE biz_no=?",
                         (f"settle:{start['token']}",)).fetchone()[0]
        assert n == 1
        ledger_before = conn.execute("SELECT COALESCE(SUM(amount),0) FROM point_ledger WHERE user_id=?",
                                     (uid,)).fetchone()[0]
    assert ledger_before == p1 - client.welcome
