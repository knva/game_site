# -*- coding: utf-8 -*-
"""Issue #56 / #57 回归。

- #56:已签到的当天计入连续天数(status 接口 streak 不少算 1 天)
- #57:体力恢复保留未满一个周期的累计进度(599 秒恢复 1 点后余 299 秒)
"""
import sqlite3
import time
from datetime import date, timedelta

import pytest

from conftest import assert_wallet_balanced


def _checkin_days(client, uid, days):
    """直接写入 N 个连续签到日(含今天),返回写入的日期列表。"""
    today = date.today()
    with sqlite3.connect(client.db_path) as conn:
        for i in range(days):
            d = (today - timedelta(days=i)).isoformat()
            conn.execute("INSERT OR IGNORE INTO checkins(user_id,day,reward,make_up,at) "
                         "VALUES(?,?,?,0,?)", (uid, d, 10, time.time()))
        conn.commit()


# ---------------- #56 ----------------
@pytest.mark.parametrize("checked_days", [1, 2, 3, 7])
def test_streak_counts_today(client, checked_days):
    """连续签到 N 天(含今天)后 status 接口 streak == N(此前少算 1 天)。"""
    token = client.register("streak_user")
    uid = client.user_id("streak_user")
    _checkin_days(client, uid, checked_days)
    st, body = client.get("/api/checkin/status", token=token)
    assert st == 200
    assert body["today_checked"] is True
    assert body["streak"] == checked_days, \
        f"期望 streak={checked_days},实际 {body['streak']}(#56 回归)"


def test_streak_breaks_with_gap(client):
    """断签(今天未签)不计入:streak 按现有语义回落。"""
    token = client.register("streak_user2")
    uid = client.user_id("streak_user2")
    today = date.today()
    with sqlite3.connect(client.db_path) as conn:
        # 前天、大前天签了,昨天和今天断签
        conn.execute("INSERT INTO checkins(user_id,day,reward,make_up,at) VALUES(?,?,?,0,?)",
                     (uid, (today - timedelta(days=2)).isoformat(), 10, time.time()))
        conn.execute("INSERT INTO checkins(user_id,day,reward,make_up,at) VALUES(?,?,?,0,?)",
                     (uid, (today - timedelta(days=3)).isoformat(), 10, time.time()))
        conn.commit()
    st, body = client.get("/api/checkin/status", token=token)
    assert st == 200
    assert body["today_checked"] is False
    assert body["streak"] == 0


def test_checkin_post_updates_status(client):
    """真实签到流程:签到后 status 立即反映新 streak。"""
    token = client.register("streak_user3")
    uid = client.user_id("streak_user3")
    st, body = client.post("/api/checkin", {}, token=token)
    assert st == 200
    assert body["streak"] == 1
    st, status = client.get("/api/checkin/status", token=token)
    assert status["today_checked"] is True
    assert status["streak"] == 1
    # 签到写入今天后,compute_streak 把今天算上(连续签到不少算)
    with sqlite3.connect(client.db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO checkins(user_id,day,reward,make_up,at) "
                     "VALUES(?,?,?,0,?)",
                     (uid, (date.today() - timedelta(days=1)).isoformat(), 10, time.time()))
        conn.commit()
    st, status = client.get("/api/checkin/status", token=token)
    assert status["streak"] == 2


# ---------------- #57 ----------------
def test_stamina_carries_partial_cycle(client):
    """经过 599 秒(≈2 个周期 -1s)恢复 1 点后,stamina_at 只前推进整数周期(300s),
    余 299 秒计入下次 → next_in=1,再等 1 秒又可恢复 1 点。"""
    token = client.register("stamina_user")
    uid = client.user_id("stamina_user")
    now = time.time()
    with sqlite3.connect(client.db_path) as conn:
        conn.execute("UPDATE users SET stamina=40, stamina_at=? WHERE id=?",
                     (now - 599, uid))
        conn.commit()

    # 触发结算(GET /api/farm 内部调用 stamina_state)
    st, farm = client.get("/api/farm", token=token)
    assert st == 200
    assert farm["stamina"]["current"] == 41
    assert farm["stamina"]["next_in"] == 1          # 余 299s,下一点 1 秒后

    # stamina_at 应推进到 now-299(保留 299s 进度,而不是归零重计)
    with sqlite3.connect(client.db_path) as conn:
        row = conn.execute("SELECT stamina, stamina_at FROM users WHERE id=?", (uid,)).fetchone()
        assert row[0] == 41
        assert abs(row[1] - (time.time() - 299)) < 3.0

    # 模拟再等 1 秒:把锚点回拨 1s(=距现在 300s),下次结算应再 +1(未丢 299s 进度)
    with sqlite3.connect(client.db_path) as conn:
        conn.execute("UPDATE users SET stamina_at=? WHERE id=?", (time.time() - 300, uid))
        conn.commit()
    st, farm = client.get("/api/farm", token=token)
    assert st == 200
    assert farm["stamina"]["current"] == 42         # 未丢 299s 进度
    assert_wallet_balanced(client, uid)


def test_stamina_full_stops_regeneration(client):
    """体力满时 stamina_at 归零,不再累计恢复。"""
    token = client.register("stamina_user2")
    uid = client.user_id("stamina_user2")
    now = time.time()
    with sqlite3.connect(client.db_path) as conn:
        conn.execute("UPDATE users SET stamina=49, stamina_at=? WHERE id=?",
                     (now - 1200, uid))
        conn.commit()
    st, farm = client.get("/api/farm", token=token)
    assert st == 200
    assert farm["stamina"]["current"] == 50         # 封顶
    assert farm["stamina"]["next_in"] == 0
    with sqlite3.connect(client.db_path) as conn:
        at = conn.execute("SELECT stamina_at FROM users WHERE id=?", (uid,)).fetchone()[0]
        assert at == 0                              # 满体力停止恢复
