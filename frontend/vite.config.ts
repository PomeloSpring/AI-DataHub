import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

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
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // FastAPI returns 307 redirects with absolute URL (http://127.0.0.1:8000/...).
        // Browser follows the redirect directly, bypassing the proxy → ERR_CONNECTION_REFUSED.
        // autoRewrite rewrites the Location header to match the proxy origin.
        autoRewrite: true,
      },
    },
  },
})
