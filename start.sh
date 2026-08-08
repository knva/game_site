#!/bin/bash
cd "$(dirname "$0")"
# 可选：预设管理员（注册时这些昵称自动成为管理员）
# export ADMIN_USERS="管理员甲,管理员乙"
exec python3 -u -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
