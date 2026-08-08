# -*- coding: utf-8 -*-
"""HTTP 小工具:JSON 响应、请求体解析、会话 Cookie 读写。

与原 server.py 的 Handler 保持等价:Content-Type=application/json; charset=utf-8,
错误码与 body 结构 {"error": ...} 完全一致;Set-Cookie 仍为 gs_session(HttpOnly),
并兼容 X-Token 头认证(见 app/auth.me)。
"""
import json

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import config


def json_response(code, obj):
    return JSONResponse(content=obj, media_type="application/json; charset=utf-8", status_code=code)


async def parse_body(request: Request):
    """读取 JSON 请求体;解析失败返回 None(由各路由回 400 {"error": "请求格式错误"})。"""
    try:
        raw = await request.body()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return None


def cookie_token(request: Request):
    """从 Cookie 解析会话 token(仅取 gs_session,不信任其它值)。"""
    raw = request.headers.get("Cookie") or ""
    for part in raw.split(";"):
        part = part.strip()
        if part.startswith(config.SESSION_COOKIE + "="):
            return part[len(config.SESSION_COOKIE):]
    return ""


def set_session_cookie(response: Response, token: str):
    response.set_cookie(config.SESSION_COOKIE, token,
                        max_age=config.LOGIN_SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", path="/")


def clear_session_cookie(response: Response):
    response.set_cookie(config.SESSION_COOKIE, "", max_age=0,
                        httponly=True, samesite="lax", path="/")
