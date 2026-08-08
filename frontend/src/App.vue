<script setup lang="ts">
import { onMounted, ref } from "vue";

interface MeUser {
  id: number;
  username: string;
  points: number;
  role: string;
  vip: boolean;
  vip_days_left?: number;
}

interface MeResp {
  user: MeUser;
  unread: number;
  today_earned: number;
  daily_cap: number;
}

const me = ref<MeResp | null>(null);
const loading = ref(true);
const error = ref("");

async function fetchMe() {
  loading.value = true;
  error.value = "";
  try {
    const res = await fetch("/api/me", { credentials: "same-origin" });
    if (res.status === 401) {
      me.value = null;
      return;
    }
    if (!res.ok) {
      error.value = `加载失败(${res.status})`;
      return;
    }
    me.value = await res.json();
  } catch {
    error.value = "无法连接服务器";
  } finally {
    loading.value = false;
  }
}

async function onLogout() {
  try {
    await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  } catch {
    /* 忽略登出接口异常 */
  }
  me.value = null;
  location.href = "/";
}

onMounted(fetchMe);

const games = [
  { href: "/games/goldminer.html", icon: "⛏️", name: "黄金矿工", desc: "经典挖矿,60 秒限时,按分数结算" },
  { href: "/games/rhythm.html", icon: "🎹", name: "音乐游戏", desc: "8 键 3D 音轨,服务器谱面防作弊" },
  { href: "/games/gomoku.html", icon: "⚫", name: "五子棋", desc: "房间码联机或挑战 AI" },
  { href: "/games/slot.html", icon: "🎰", name: "老虎机", desc: "5 积分一拉,最高中 300 分" },
  { href: "/games/farm.html", icon: "🌱", name: "开心农场", desc: "播种浇水收获,离线也生长" },
  { href: "/games/wheel.html", icon: "🎡", name: "幸运大转盘", desc: "10 积分一次,最高赢 100 分" },
];

const homeCards = [
  { href: "/games/farm.html", icon: "🚜", title: "继续游戏", desc: "回到开心农场,一键收菜 / 一键播种" },
  { href: "/checkin.html", icon: "📅", title: "每日任务 · 签到", desc: "连续签到领积分,VIP 加成更多" },
  { href: "/bottle.html", icon: "🍾", title: "漂流瓶", desc: "扔一个瓶子,也捡一个别人的故事" },
];
</script>

<template>
  <header class="topbar">
    <a class="brand" href="/">🎮 小游戏乐园</a>
    <nav class="nav">
      <a href="/checkin.html">签到</a>
      <a href="/games/gomoku.html">五子棋</a>
      <a href="/games/slot.html">老虎机</a>
      <a href="/bottle.html">漂流瓶</a>
      <a href="/mail.html">站内信<span v-if="me?.unread" class="badge">{{ me.unread }}</span></a>
    </nav>
    <div class="user">
      <template v-if="loading">…</template>
      <template v-else-if="me">
        <span class="points">💎 {{ me.user.points }}</span>
        <span v-if="me.user.vip" class="vip" title="VIP 剩余天数">👑 VIP</span>
        <span class="whoami">{{ me.user.username }}</span>
        <button class="btn" @click="onLogout">退出</button>
      </template>
      <template v-else>
        <a class="btn primary" href="/login.html">登录</a>
        <a class="btn" href="/login.html?mode=register">注册</a>
      </template>
    </div>
  </header>

  <main>
    <section class="hero">
      <h1>🎮 小游戏乐园</h1>
      <p v-if="me">欢迎回来，{{ me.user.username }}！今日已赚 {{ me.today_earned }} / {{ me.daily_cap }} 积分</p>
      <p v-else>注册登录 · 六个小游戏赚取 💎积分 · 冲击排行榜之巅</p>
      <p v-if="error" class="err">{{ error }}</p>
    </section>

    <section class="cards">
      <a v-for="c in homeCards" :key="c.title" class="card" :href="c.href">
        <span class="card-icon">{{ c.icon }}</span>
        <div>
          <h3>{{ c.title }}</h3>
          <p>{{ c.desc }}</p>
        </div>
      </a>
    </section>

    <section class="games">
      <a v-for="g in games" :key="g.href" class="game-card" :href="g.href">
        <span class="game-icon">{{ g.icon }}</span>
        <div>
          <h3>{{ g.name }}</h3>
          <p>{{ g.desc }}</p>
        </div>
        <span class="go">进入 →</span>
      </a>
    </section>
  </main>

  <footer class="foot">游戏页(games/*.html)保持原生 · 外壳由 Vue 3 + Vite + TypeScript 构建</footer>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 12px 24px;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}
.brand { font-size: 18px; font-weight: 800; color: var(--text); }
.nav { flex: 1; display: flex; gap: 16px; font-size: 14px; }
.nav a { color: var(--text); }
.nav a:hover { color: var(--gold); text-decoration: none; }
.badge {
  background: var(--gold); color: #3a2400; border-radius: 10px;
  font-size: 11px; padding: 0 6px; margin-left: 2px;
}
.user { display: flex; align-items: center; gap: 12px; font-size: 14px; }
.points { color: var(--gold); font-weight: 700; }
.vip { color: var(--gold); font-size: 13px; }
.whoami { color: var(--muted); }
.btn {
  border: 1px solid var(--line); background: var(--panel2); color: var(--text);
  border-radius: 10px; padding: 6px 14px; cursor: pointer; font-size: 13px;
}
.btn:hover { text-decoration: none; border-color: var(--gold); }
.btn.primary { background: var(--gold); border-color: var(--gold); color: #3a2400; font-weight: 700; }

main { max-width: 1080px; margin: 0 auto; padding: 28px 20px 60px; }
.hero { text-align: center; padding: 28px 0 8px; }
.hero h1 { margin: 0 0 6px; font-size: 34px; }
.hero p { color: var(--muted); margin: 4px 0; }
.hero .err { color: #ff6b6b; }

.cards {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px; margin: 24px 0;
}
.card {
  display: flex; gap: 12px; align-items: flex-start;
  background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
  padding: 16px; color: var(--text);
}
.card:hover { border-color: var(--gold); text-decoration: none; transform: translateY(-2px); }
.card-icon { font-size: 28px; }
.card h3 { margin: 0 0 4px; font-size: 16px; }
.card p { margin: 0; color: var(--muted); font-size: 13px; }

.games { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
.game-card {
  display: flex; align-items: center; gap: 14px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
  padding: 18px; color: var(--text);
}
.game-card:hover { border-color: var(--gold); text-decoration: none; }
.game-icon { font-size: 34px; }
.game-card h3 { margin: 0 0 4px; font-size: 17px; }
.game-card p { margin: 0; color: var(--muted); font-size: 13px; }
.game-card .go { margin-left: auto; color: var(--gold); font-weight: 700; font-size: 13px; }

.foot { text-align: center; color: var(--muted); font-size: 12px; padding: 20px; border-top: 1px solid var(--line); }

@media (max-width: 640px) {
  .nav { order: 3; width: 100%; overflow-x: auto; }
}
</style>
