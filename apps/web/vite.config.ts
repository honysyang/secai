import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 开发态：/api 及两条 WS 下行流代理到 SECAI-PT 后端（v4 §6：dev 5173 → backend 8700）
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8700',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
