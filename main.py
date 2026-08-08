"""FastAPI 入口转发:uvicorn main:app 或 `python3 -m uvicorn main:app` 启动模块化版。
默认运行方式仍是标准库 server.py(零依赖);切到 FastAPI 版见 start.sh。"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.main import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
