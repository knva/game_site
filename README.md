# 小游戏乐园

Python 标准库 `http.server` + SQLite 的小游戏平台,含 FastAPI 镜像实现(`app/`)。
功能:注册/登录/签到/VIP、积分钱包(不可变流水)、四个小游戏(黄金矿工/音乐/五子棋/老虎机)、
开心农场、幸运大转盘、漂流瓶、站内信、管理员后台。

## 目录

- `server.py` — 标准库版服务器(零依赖,默认入口)
- `app/` — FastAPI 镜像(路由/接口与 server.py 对齐,`uvicorn app.main:app`)
- `public/` — 原生前端页面(游戏页等)
- `frontend/` — Vue 3 + Vite + TypeScript 外壳(Issue #25)
- `migrations/` — 版本化数据库迁移(Issue #17)
- `tests/` — pytest 回归(契约 / 钱包不变量 / 防作弊 / 限流)

## 启动

```bash
# 标准库版(零依赖)
python3 server.py                # 默认 http://localhost:8000

# FastAPI 版
pip install -r requirements.txt
python3 main.py                  # 或 uvicorn app.main:app --port 8000
```

## 数据库:SQLite ↔ PostgreSQL 切换(Issue #17)

数据库地址统一从环境变量 `DATABASE_URL` 读取(server.py 与 app/db.py 行为一致):

- 不设置 → 默认 `data/game.db`
- `DATABASE_URL=sqlite:///绝对路径` → 指定 SQLite 库
- `DATABASE_URL=/path/to/game.db` → 纯路径亦支持
- `DATABASE_URL=postgres://user:pass@host/db` → **当前未适配 psycopg2/SQL**,启动时
  打印明确提示并优雅降级为 SQLite(不崩溃)。

### 用迁移脚本建表 / 升级

```bash
python3 migrations/migrate.py                     # 迁移默认 data/game.db
python3 migrations/migrate.py --db /path/to/db    # 指定库
python3 migrations/migrate.py --dry-run           # 只预览待执行迁移
```

- 按文件名前缀数字升序执行 `migrations/000N_*.sql`,结果记录在 `schema_migrations` 表,
  重复运行自动跳过(幂等)。
- 空库执行 `0001_baseline.sql` 后,表结构与 `init_db()` 一致(已验证 28 张公共表列结构相同)。
- 老库升级:先把现有 `data/game.db` 的旧结构用 `init_db()` 跑一遍对齐,再引入后续迁移;新增
  迁移时在 `migrations/` 下递增命名(如 `0002_xxx.sql`)。

### 迁移到 PostgreSQL 的注意事项

SQLite 特有语法与 PG 差异清单见 `app/db.py` 顶部注释(`INSERT OR REPLACE` → `ON CONFLICT`,
`?` → `%s`,`AUTOINCREMENT` → `BIGSERIAL`,`PRAGMA` → 连接池等)。迁移基线(`0001`)目前为
SQLite 方言,切 PG 时需按清单改写 SQL 并提供 psycopg2 连接。

## 测试(Issue #55)

```bash
python -m pytest tests/            # 两套后端(stdlib + fastapi)全量
python -m pytest tests/ -k "not fastapi"   # 仅标准库版
python -m pytest tests/ -k "fastapi"       # 仅 FastAPI 版(需 pip install -r requirements.txt)
```

每个用例独立临时 DB,覆盖:核心 API 契约(登录/注册/me、各游戏 start/end、农场种收、转盘、
错误码 400/401/403/404/429)、钱包不变量(`points == 初始 + Σ point_ledger`)、
防作弊回归(错 id 被拒 / 谱面回传被拒 / 结算幂等)、限流(gend_h/gend_d 独立)。
CI:`.github/workflows/ci.yml`,push/PR 触发。

## 前端外壳(Issue #25)

```bash
cd frontend
npm install
npm run dev          # 开发 http://localhost:5173,/api 代理到 :8000
npm run typecheck    # vue-tsc --noEmit 类型检查
npm run build        # 产物 dist/(base=./ 相对路径)
```

- 首页首屏:「继续游戏」(最近玩过的游戏入口)、「每日任务·签到」(签到状态)、游戏卡列表
  (门票 / 预计时长 / 奖励规则,数据来自 `/api/game/odds` + `/api/me`)。
- 登录态统一:导航显示登录按钮或用户信息,带退出按钮。
- 整合进 server.py:`npm run build` 后把 `dist/` 内容拷贝进 `public/` 即可由标准库
  http.server 直接托管;独立部署时 `/api/*` 需反向代理到后端(默认 :8000)。
