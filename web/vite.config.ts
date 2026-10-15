import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 8210,
    // 本地开发：前端 8210、后端 8310 是不同端口，浏览器视角属于跨域。
    // 这里用同源代理把它消掉——生产环境由 nginx 做完全相同的事（/api → 后端），
    // 所以 src 里可以统一用相对路径，开发与部署行为一致，不必改两处地址。
    proxy: {
      '/api': { target: 'http://127.0.0.1:8310', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8310', changeOrigin: true },
    },
  },
})
