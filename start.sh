#!/bin/bash
cd "$(dirname "$0")"
# 可选：预设管理员（注册时这些昵称自动成为管理员）
# export ADMIN_USERS="管理员甲,管理员乙"
exec python3 -u server.py
