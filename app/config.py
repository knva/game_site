# -*- coding: utf-8 -*-
"""全局配置:路径、端口、跨模块共享的常量与环境变量。

数据库地址(DATABASE_URL)的解析与降级逻辑见 app/db.py(按 Issue #17 设计)。
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "game.db")
PORT = int(os.environ.get("PORT", "8000"))
ADMIN_USERS = [u.strip() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()]
# 部署时通过环境变量指定初始管理员(逗号分隔用户名),注册时/启动时提升为 admin
ADMIN_INIT = [u.strip() for u in os.environ.get("ADMIN_INIT", "").split(",") if u.strip()]

SESSION_COOKIE = "gs_session"  # HttpOnly 会话 Cookie(同源请求自动携带)
LOGIN_SESSION_DAYS = 7

WELCOME_POINTS = 100

# 跨模块共享的经济/限流常量(各领域私有常量见对应模块)
DAILY_EARNED_CAP = 30000
SUBMIT_PER_HOUR = 6
SUBMIT_PER_DAY = 40
