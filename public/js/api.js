"use strict";
/* 共享 API 工具与 UI */

const Store = {
  get token() { return localStorage.getItem("gs_token") || ""; },
  set token(v) { v ? localStorage.setItem("gs_token", v) : localStorage.removeItem("gs_token"); },
  get user() {
    try { return JSON.parse(localStorage.getItem("gs_user") || "null"); } catch (e) { return null; }
  },
  set user(v) { v ? localStorage.setItem("gs_user", JSON.stringify(v)) : localStorage.removeItem("gs_user"); },
};

async function api(url, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (Store.token) headers["X-Token"] = Store.token;
  const res = await fetch(url, { ...opts, headers });
  const json = await res.json().catch(() => ({}));
  if (res.status === 401) {
    Store.token = "";
    Store.user = null;
    if (window.onAuthExpired) window.onAuthExpired();
  }
  if (!res.ok) throw new Error(json.error || `请求失败(${res.status})`);
  return json;
}

const Api = {
  get: (url) => api(url),
  post: (url, body) => api(url, { method: "POST", body: JSON.stringify(body || {}) }),

  register: (username, password) => api("/api/register", { method: "POST", body: JSON.stringify({ username, password }) }),
  login: (username, password) => api("/api/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => api("/api/logout", { method: "POST" }),
  me: () => api("/api/me"),
  leaderboard: (type = "points", game = "") => api(`/api/leaderboard?type=${type}&game=${encodeURIComponent(game)}`),

  gameStart: (game) => Api.post("/api/game/start", { game }),
  gameEnd: (game, token, score, stats) => Api.post("/api/game/end", { game, token, score, stats }),

  farm: () => Api.get("/api/farm"),
  farmTarget: (name) => Api.get(`/api/farm?target=${encodeURIComponent(name)}`),
  plant: (slot, crop) => Api.post("/api/farm/plant", { slot, crop }),
  water: (slot) => Api.post("/api/farm/water", { slot }),
  harvest: (slot) => Api.post("/api/farm/harvest", { slot }),
  sell: (crop) => Api.post("/api/farm/sell", { crop }),
  unlock: (slot) => Api.post("/api/farm/unlock", { slot }),
  upgrade: (slot) => Api.post("/api/farm/upgrade", { slot }),
  building: (name) => Api.post("/api/farm/building", { name }),
  steal: (target, slot) => Api.post("/api/farm/steal", { target, slot }),
  farmPlant: (slot, crop) => Api.post("/api/farm/plant", { slot, crop }),
  farmWater: (slot) => Api.post("/api/farm/water", { slot }),
  farmHarvest: (slot) => Api.post("/api/farm/harvest", { slot }),
  farmSell: (crop) => Api.post("/api/farm/sell", { crop }),
  farmUnlock: (slot) => Api.post("/api/farm/unlock", { slot }),
  farmUpgrade: (slot) => Api.post("/api/farm/upgrade", { slot }),
  farmBuilding: (name) => Api.post("/api/farm/building", { name }),
  farmSteal: (target, slot) => Api.post("/api/farm/steal", { target, slot }),

  spin: () => Api.post("/api/wheel/spin"),
  slotSpin: () => Api.post("/api/slot/spin"),
  slotDouble: (token) => Api.post("/api/slot/double", { token }),
  slotCollect: (token) => Api.post("/api/slot/collect", { token }),

  mail: () => Api.get("/api/mail"),
  mailSend: (to, title, content) => Api.post("/api/mail/send", { to, title, content }),
  mailRead: (id) => Api.post("/api/mail/read", { id }),

  bottleFeed: () => Api.get("/api/bottle/feed"),
  bottlePick: () => Api.get("/api/bottle/pick"),
  bottleThrow: (content) => Api.post("/api/bottle/throw", { content }),

  gomokuCreate: (mode) => Api.post("/api/gomoku/create", { mode }),
  gomokuJoin: (code) => Api.post("/api/gomoku/join", { code }),
  gomokuMove: (code, x, y) => Api.post("/api/gomoku/move", { code, x, y }),
  gomokuLeave: (code) => Api.post("/api/gomoku/leave", { code }),
  gomokuRoom: (code) => Api.get(`/api/gomoku/room?code=${code}`),
  gomokuRank: () => Api.get("/api/gomoku/rank"),

  checkinStatus: () => Api.get("/api/checkin/status"),
  checkin: () => Api.post("/api/checkin"),
  checkinMakeup: (day) => Api.post("/api/checkin/makeup", { day }),
  vipBuy: (days) => Api.post("/api/vip/buy", { days }),

  adminStats: () => Api.get("/api/admin/stats"),
  adminUsers: (search, page) => Api.get(`/api/admin/users?search=${encodeURIComponent(search)}&page=${page}`),
  adminLogs: (username, action, page) =>
    Api.get(`/api/admin/logs?username=${encodeURIComponent(username)}&action=${encodeURIComponent(action)}&page=${page}`),
  adminBottles: () => Api.get("/api/admin/bottles"),
  adminSetBalance: (user_id, amount, note) => Api.post("/api/admin/set-balance", { user_id, amount, note }),
  adminToggle: (user_id) => Api.post("/api/admin/toggle-status", { user_id }),
  adminMail: (to, title, content) => Api.post("/api/admin/mail", { to, title, content }),
  adminDelBottle: (id) => Api.post("/api/admin/del-bottle", { id }),
};

/* ---------- 登录/注册:跳转独立页面 ---------- */
function authModal() {
  const cur = location.pathname + location.search;
  location.href = "/login.html?redirect=" + encodeURIComponent(cur);
}

async function logout() {
  try { await Api.logout(); } catch (e) {}
  Store.token = "";
  Store.user = null;
  toast("已退出登录");
  if (window.onAuthChange) window.onAuthChange();
  if (window.onUserChange) window.onUserChange();
}

/* ---------- 顶部栏 ---------- */
async function initTopBar() {
  const bar = document.getElementById("topbar");
  if (!bar) return;
  bar.innerHTML = `
    <a class="brand" href="/">🎮 小游戏乐园</a>
    <nav class="top-nav">
      <a href="/checkin.html">签到</a>
      <a href="/games/gomoku.html">五子棋</a>
      <a href="/games/slot.html">老虎机</a>
      <a href="/bottle.html">漂流瓶</a>
      <a href="/mail.html" id="nav-mail">站内信<span class="badge" id="mail-badge" style="display:none">0</span></a>
    </nav>
    <div class="top-right" id="top-right"></div>`;
  const right = document.getElementById("top-right");
  const me = Store.user;
  if (me) {
    right.innerHTML = `
      <span class="points-chip">💎 ${me.points}</span>
      ${me.vip ? `<span class="vip-chip" title="VIP ${me.vip_days_left} 天">👑 VIP</span>` : ""}
      ${me.role === "admin" ? `<a class="btn gray mini" href="/admin.html">管理后台</a>` : ""}
      <span class="whoami">${escapeHtml(me.username)}</span>
      <button class="btn gray mini" id="btn-logout">退出</button>`;
    document.getElementById("btn-logout").onclick = logout;
    refreshPoints();
  } else {
    const cur = encodeURIComponent(location.pathname + location.search);
    right.innerHTML = `
      <a class="btn mini" href="/login.html?redirect=${cur}">登录</a>
      <a class="btn gray mini" href="/login.html?mode=register&redirect=${cur}">注册</a>`;
  }
  window.refreshPoints = refreshPoints;
}

async function refreshPoints() {
  if (!Store.token) return;
  try {
    const { user, unread } = await Api.me();
    Store.user = { ...Store.user, ...user };
    const chip = document.querySelector(".points-chip");
    if (chip) chip.textContent = `💎 ${user.points}`;
    const badge = document.getElementById("mail-badge");
    if (badge) {
      badge.textContent = unread;
      badge.style.display = unread ? "" : "none";
    }
    const vipEl = document.querySelector(".vip-chip");
    const tr = document.getElementById("top-right");
    if (user.vip && !vipEl && tr) {
      const s = document.createElement("span");
      s.className = "vip-chip";
      s.title = `VIP 剩余 ${user.vip_days_left} 天`;
      s.textContent = "👑 VIP";
      tr.insertBefore(s, tr.firstChild);
    } else if (!user.vip && vipEl) {
      vipEl.remove();
    }
    if (Store.user.role === "admin" && !document.querySelector('a[href="/admin.html"]') && tr) {
      const a = document.createElement("a");
      a.className = "btn gray mini";
      a.href = "/admin.html";
      a.textContent = "管理后台";
      tr.insertBefore(a, document.getElementById("btn-logout"));
    }
  } catch (e) {}
}

window.onAuthExpired = () => {
  toast("登录已过期，请重新登录", "err");
  const cur = encodeURIComponent(location.pathname + location.search);
  setTimeout(() => { location.href = "/login.html?redirect=" + cur; }, 600);
};

/* ---------- 排行榜 ---------- */
async function renderLeaderboard(container, opts = {}) {
  const { list } = await Api.leaderboard(opts.type || "points", opts.game || "");
  const medals = ["🥇", "🥈", "🥉"];
  container.innerHTML = list.length
    ? list.map((r, i) => `
        <li class="rank-row ${i < 3 ? "top" : ""}">
          <span class="rank">${medals[i] || i + 1}</span>
          <span class="rank-name">${escapeHtml(r.name)}</span>
          <span class="rank-val">${opts.type === "score" ? r.score + " 分" : "💎 " + r.points}</span>
        </li>`).join("")
    : `<li class="rank-row empty">暂无数据，快来上榜！</li>`;
}

/* ---------- 通用 ---------- */
function toast(msg, type = "info") {
  let box = document.getElementById("toast-box");
  if (!box) { box = document.createElement("div"); box.id = "toast-box"; document.body.appendChild(box); }
  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => { el.classList.add("toast-out"); setTimeout(() => el.remove(), 400); }, 2600);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtTime(t) {
  const d = new Date(t * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function requireLogin() {
  if (!Store.token) {
    toast("请先登录", "err");
    authModal();
    return false;
  }
  return true;
}

function gameShell(title) {
  const shell = document.createElement("div");
  shell.className = "game-shell";
  shell.innerHTML = `
    <div class="game-nav">
      <a class="btn gray mini" href="/">⬅ 首页</a>
      <h1>${title}</h1>
      <span class="points-chip" id="points-chip">💎 0</span>
    </div>`;
  document.body.prepend(shell);
}
