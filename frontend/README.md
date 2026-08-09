# 小游戏乐园 · 前端外壳 (Issue #25)

渐进式外壳：用 Vue 3 + Vite + TypeScript 构建首页外壳与登录态，**游戏页仍为 `public/` 里的原生页面**，本次不迁移。

## 目录结构

```
frontend/
├── package.json        # 依赖(全部 devDependencies,构建期使用)
├── index.html          # HTML 模板
├── tsconfig.json       # TS 配置(strict + vue-tsc)
├── vite.config.ts      # Vite 配置(proxy /api → :8000,base=./)
├── README.md
└── src/
    ├── main.ts         # 入口:createApp(App).mount("#app")
    ├── env.d.ts        # vite/client 类型 + *.vue 模块声明
    ├── style.css       # 全局基础样式
    └── App.vue         # 外壳组件:读取 /api/me 显示登录态、导航、首页卡片
```

## 构建命令

```bash
cd frontend
npm install      # 安装 vue / vite / typescript / vue-tsc
npm run dev      # 开发模式(默认 http://localhost:5173,/api 已代理到 :8000)
npm run build    # 产出 dist/ 静态文件
npm run typecheck  # vue-tsc --noEmit 类型检查
```

## 部署方式

- **整合进现有 server.py**：`npm run build` 后把 `dist/` 的内容拷贝进 `public/`
  （`base: "./"` 为相对路径，首页 `index.html` 可被标准库 http.server 直接托管）。
- **独立部署**：`dist/` 放到任意静态服务器，并确保 `/api/*` 反向代理到后端（默认 `http://localhost:8000`）。

## 外壳功能

- 读取 `/api/me` 展示登录态（用户名 / 积分 / VIP / 今日已赚）。
- 导航链接到现有原生游戏页 `/games/*.html`（保持原生，不迁移）。
- **首屏「继续游戏」**：记录最近点开的游戏（`localStorage: gs_last_game`），一键回到上次玩的入口，默认开心农场。
- **「每日任务 · 签到」**：读取 `/api/checkin/status` 展示今日签到状态 / 可领奖励 / 连续天数。
- **游戏卡列表**：从 `/api/game/odds` + 静态元数据渲染每张卡的门票 / 预计时长 / 奖励规则
  （黄金矿工 / 音乐 / 老虎机 / 转盘 / 农场 / 五子棋）。
- 登录态统一：导航显示登录/注册按钮或用户信息，带退出按钮。
- 登录 / 注册跳转独立 `login.html`，与 `public/js/api.js` 共用同一 `gs_session` Cookie。
