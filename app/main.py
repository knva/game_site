# -*- coding: utf-8 -*-
"""FastAPI 入口:组装路由、全局异常处理、静态文件、启动逻辑。

启动方式:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
或:
    python3 -m app.main

与旧 server.py(单文件 http.server)保持 API 完全兼容:
- 所有 /api/* 路由、响应字段、错误码(400/401/403/404/409/429/500)一致
- Set-Cookie gs_session(HttpOnly) / X-Token 头双通道认证
- 未匹配的 GET 走静态文件(index.html 兜底),未匹配的 POST 回 404
"""
import email.utils
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.responses import Response

from . import config
from .admin import ensure_admins, router as admin_router
from .auth import router as auth_router
from .db import init_db
from .farm import router as farm_router
from .games import _gomoku_cleanup_loop, router as games_router
from .http import json_response
from .social import router as social_router
from .wallet import router as wallet_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    ensure_admins()
    threading.Thread(target=_gomoku_cleanup_loop, daemon=True).start()
    print(f"🎮 小游戏乐园已启动 (FastAPI): http://localhost:{config.PORT}")
    print(f"   数据: {config.DB_PATH}   注册用户均为普通用户,不自动成为管理员")
    if config.ADMIN_USERS + config.ADMIN_INIT:
        print(f"   预设管理员: {', '.join(config.ADMIN_USERS + config.ADMIN_INIT)}")
    yield


app = FastAPI(title="小游戏乐园", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(wallet_router)
app.include_router(games_router)
app.include_router(farm_router)
app.include_router(social_router)
app.include_router(admin_router)


# 与旧 do_POST 一致:积分不足(ValueError)→ 400;未预期异常 → 500
@app.exception_handler(ValueError)
async def on_value_error(request: Request, exc: ValueError):
    return json_response(400, {"error": str(exc)})


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    return json_response(500, {"error": "服务器内部错误"})


# ---------------- 静态文件兜底(等价旧 _serve_static) ----------------
def _serve_static(path: str):
    if path == "/":
        path = "/index.html"
    if "/../" in path or path.startswith("/data") or path.startswith("/server"):
        return json_response(404, {"error": "not found"})
    fpath = os.path.normpath(os.path.join(config.PUBLIC_DIR, path.lstrip("/")))
    if not fpath.startswith(config.PUBLIC_DIR) or not os.path.isfile(fpath):
        return json_response(404, {"error": "not found"})
    ext = os.path.splitext(fpath)[1].lower()
    mime = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8", ".png": "image/png",
            ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon",
            ".json": "application/json", ".mp3": "audio/mpeg"}.get(ext, "application/octet-stream")
    body = open(fpath, "rb").read()
    return Response(content=body, media_type=mime, headers={
        "Cache-Control": "no-cache",
        "Last-Modified": email.utils.formatdate(os.path.getmtime(fpath), usegmt=True),
    })


@app.get("/{full_path:path}")
def serve_static(full_path: str):
    return _serve_static("/" + full_path)


@app.post("/{full_path:path}")
async def api_not_found(full_path: str):
    return json_response(404, {"error": "接口不存在"})


@app.options("/{full_path:path}")
async def api_options(full_path: str):
    return Response(status_code=204)


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()
