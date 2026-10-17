import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// 端口收敛终态：后端为单进程单一 web 端口 8000（与 nginx 上游 backend:8000、
// docker-compose、.env BACKEND_PORT 对齐）。所有 API 前缀统一代理到同一后端，
// 不再按微服务名区分端口。
const BACKEND = 'http://127.0.0.1:8000'

/**
 * dev 代理：`/api` **单条通配**，与生产 nginx 的 `location /api/` 同构。
 *
 * 为什么不再逐前缀白名单（历史上这里是 60+ 条 proxy['/api/xxx']）：
 * 白名单是微服务时代的残留 —— 所有 target 早已收敛到同一个 8000，逐条登记
 * 只剩一个作用：**漏一条就把请求兜底成 SPA 的 index.html(200 + text/html)**。
 * axios 拿到 HTML 不会报错，页面于是渲染成"空列表/未设置"，缺陷被完全静默。
 * 本仓已因此踩过四次（/api/eval、/api/evolution、/api/assets 都留过注释告警），
 * 实测又抓到三条漏配的：/api/memory（我的记忆页永远显示未设置，而记忆其实已落库）、
 * /api/ontology、/api/user-assets。
 * 通配后新增端点无需登记，这类静默失败从结构上消失。
 *
 * 回归护栏见 src/__tests__/viteProxy.test.ts。
 */
function createProxyConfig() {
  return {
    '/api': {
      target: BACKEND,
      changeOrigin: true,
      autoRewrite: true,
      // 长任务（Chat/Agent/执行流、同步与图谱构建）沿用原逐条 600s 的口径；
      // 这是代理 socket 不活动超时，不影响快请求。
      timeout: 600000,
    },
  }
}

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: createProxyConfig(),
  },
})
