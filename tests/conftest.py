# -*- coding: utf-8 -*-
"""pytest 基线与临时数据库 fixture(每个用例独立 tmp DB,互不污染)。

- backend 参数化覆盖两套实现:
    - "stdlib": server.py(标准库 http.server,进程内起 ephemeral 端口)
    - "fastapi": app/(FastAPI,用 TestClient,含 lifespan 启动 init_db)
- 每次请求经 HTTP 层走完整链路(X-Token 头双通道认证),等价真实客户端。
"""
import http.client
import json
import os
import sqlite3
import sys
import threading

import pytest

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

import server as stdlib_server  # noqa: E402

from app import config as app_config  # noqa: E402


def _db_path(tmp_path, name):
    d = tmp_path / "db"
    d.mkdir(exist_ok=True)
    return str(d / f"{name}.db")


class _BaseClient:
    backend = ""
    db_path = ""
    welcome = 0

    def get(self, path, token=None):
        raise NotImplementedError

    def post(self, path, data=None, token=None):
        raise NotImplementedError

    def register(self, username, password="test1234"):
        status, body = self.post("/api/register",
                                 {"username": username, "password": password},
                                 token="xt-client")
        assert status == 200, (status, body)
        assert body["ok"] is True
        return body["token"]

    def wallet(self, user_id):
        """返回 (users.points, SUM(point_ledger.amount)) 供钱包不变量断言。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            p = conn.execute("SELECT points FROM users WHERE id=?", (user_id,)).fetchone()["points"]
            s = conn.execute("SELECT COALESCE(SUM(amount),0) s FROM point_ledger WHERE user_id=?",
                             (user_id,)).fetchone()["s"]
            return p, s

    def user_points(self, user_id):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT points FROM users WHERE id=?", (user_id,)).fetchone()
            return row[0] if row else None

    def user_id(self, username):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            return row[0] if row else None


class StdlibClient(_BaseClient):
    backend = "stdlib"

    def __init__(self, tmp_path, name):
        self.db_path = _db_path(tmp_path, name)
        stdlib_server.DATA_DIR = os.path.dirname(self.db_path)
        stdlib_server.DB_PATH = self.db_path
        stdlib_server.init_db()
        self.welcome = stdlib_server.WELCOME_POINTS
        self.world = stdlib_server.gen_goldminer_world
        self.httpd = stdlib_server.ThreadingHTTPServer(("127.0.0.1", 0), stdlib_server.Handler)
        self.httpd.daemon_threads = True
        self._port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _request(self, method, path, data=None, token=None):
        headers = {}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if token:
            headers["X-Token"] = token
        conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=15)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
        finally:
            conn.close()
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            payload = raw
        return resp.status, payload

    def get(self, path, token=None):
        return self._request("GET", path, token=token)

    def post(self, path, data=None, token=None):
        return self._request("POST", path, data=data, token=token)


class FastApiClient(_BaseClient):
    backend = "fastapi"

    def __init__(self, tmp_path, name):
        self.db_path = _db_path(tmp_path, name)
        app_config.DATA_DIR = os.path.dirname(self.db_path)
        app_config.DB_PATH = self.db_path
        from fastapi.testclient import TestClient
        from app.main import app
        from app.games import gen_goldminer_world
        self.welcome = app_config.WELCOME_POINTS
        self.world = gen_goldminer_world
        self._client = TestClient(app)
        self._client.__enter__()

    def close(self):
        self._client.__exit__(None, None, None)

    def _request(self, method, path, data=None, token=None):
        headers = {}
        if token:
            headers["X-Token"] = token
        resp = self._client.request(method, path, json=data if data is not None else None,
                                    headers=headers)
        # 与 stdlib 客户端一致:每次请求独立(不依赖 Cookie 会话),只认 X-Token
        self._client.cookies.clear()
        try:
            payload = resp.json()
        except (ValueError, TypeError):
            payload = resp.text
        return resp.status_code, payload

    def get(self, path, token=None):
        return self._request("GET", path, token=token)

    def post(self, path, data=None, token=None):
        return self._request("POST", path, data=data, token=token)


@pytest.fixture(params=["stdlib", "fastapi"])
def client(request, tmp_path):
    """每个用例一个全新临时 DB + 独立服务(后端参数化)。"""
    if request.param == "stdlib":
        c = StdlibClient(tmp_path, request.node.name)
    else:
        c = FastApiClient(tmp_path, request.node.name)
    request.addfinalizer(c.close)
    return c


def assert_wallet_balanced(client, user_id):
    """钱包不变量:points == 初始赠送 + Σ(point_ledger.amount)"""
    points, ledger_sum = client.wallet(user_id)
    assert points == client.welcome + ledger_sum, \
        f"钱包不平衡: points={points}, welcome={client.welcome}, ledger_sum={ledger_sum}"
