import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 渐进式外壳:构建产物输出到 frontend/dist/(相对路径 base,可放任意子目录)。
// 与 server.py 整合:把 dist 内容拷贝进 public/ 即可;独立部署时 nginx 等静态服务器
// 需将 /api 反向代理到后端(默认 8000)。
export default defineConfig({
  plugins: [vue()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
