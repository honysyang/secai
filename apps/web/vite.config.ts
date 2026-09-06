import { defineConfig } from 'vite'
// 开发态：/api 及两条 WS 下行流代理到 SECAI-PT 后端（v4 §6：dev 5173 → backend 8700）
// server.watch.usePolling：本机 fs.inotify.max_user_watches 已耗尽（ENOSPC，
// 需 root 调大），开发态退化为轮询监听；不影响产物构建与 serve。
export default defineConfig({
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8700',
        changeOrigin: true,
        ws: true,
      },
    },
    watch: { usePolling: true, interval: 800 },
  },
})
