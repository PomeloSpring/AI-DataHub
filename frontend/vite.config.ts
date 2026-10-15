import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// 端口收敛终态：后端为单进程单一 web 端口 8000（与 nginx 上游 backend:8000、
// docker-compose、.env BACKEND_PORT 对齐）。所有 API 前缀统一代理到同一后端，
// 不再按微服务名区分端口。
const BACKEND = 'http://127.0.0.1:8000'

// 创建代理配置：每个路径前缀代理到对应的微服务
function createProxyConfig() {
  const proxy: Record<string, any> = {}

  const proxyOptions = (target: string) => ({
    target,
    changeOrigin: true,
    autoRewrite: true,
  })

  // AuthService
  proxy['/api/auth'] = proxyOptions(BACKEND)
  proxy['/api/users'] = proxyOptions(BACKEND)
  proxy['/api/workspaces'] = proxyOptions(BACKEND)
  proxy['/api/roles'] = proxyOptions(BACKEND)
  proxy['/api/audit'] = proxyOptions(BACKEND)
  proxy['/api/admin/rls-policies'] = proxyOptions(BACKEND)
  proxy['/api/admin/rls-user-attributes'] = proxyOptions(BACKEND)
  proxy['/api/admin/rls-audit-logs'] = proxyOptions(BACKEND)
  proxy['/api/monitoring'] = proxyOptions(BACKEND)
  proxy['/api/observability'] = proxyOptions(BACKEND)
  // 评测中心（用例 CRUD / 运行 / 版本对比）—— 必须显式加进白名单：
  // 本表是**显式路径白名单**，漏一条就会被 SPA 兜底成 index.html(200+HTML)，
  // 前端拿到非预期结构却显示成"空列表"，是个静默失败。
  proxy['/api/eval'] = proxyOptions(BACKEND)

  // 进化中心（候选看板 / 四出口 / 手动归纳）—— 同理必须显式入白名单，
  // 否则会被 SPA 兑底成 HTML，页面只能看到"没有候选"而看不出是代理没配。
  proxy['/api/evolution'] = proxyOptions(BACKEND)

  // DataMind (AI Engine) — 需要较长超时
  proxy['/api/chat'] = { ...proxyOptions(BACKEND), timeout: 600000 }
  proxy['/api/agent'] = { ...proxyOptions(BACKEND), timeout: 600000 }
  proxy['/api/execution'] = { ...proxyOptions(BACKEND), timeout: 600000 }
  proxy['/api/pipeline'] = { ...proxyOptions(BACKEND), timeout: 600000 }
  proxy['/api/knowledge'] = proxyOptions(BACKEND)
  proxy['/api/as-bot'] = { ...proxyOptions(BACKEND), timeout: 600000 }
  // Skills 管理(文件夹/SKILL.md)由 datamind 提供,须先于 /api/admin/* → aiplatform 的通用规则
  proxy['/api/admin/skills'] = proxyOptions(BACKEND)
  // 用户资产清单(资产跟随用户, OSS 托管)由 datamind 提供 —— 注意 /api/assets 与
  // /api/workspace-assets 是两个前缀, 漏任何一个都会被 SPA 兜底成 index.html(200+HTML)
  proxy['/api/assets'] = proxyOptions(BACKEND)
  proxy['/api/workspace-assets'] = proxyOptions(BACKEND)

  // DataGov (Data Governance)
  proxy['/api/quality'] = proxyOptions(BACKEND)
  proxy['/api/lineage'] = proxyOptions(BACKEND)
  proxy['/api/standards'] = proxyOptions(BACKEND)
  proxy['/api/security'] = proxyOptions(BACKEND)

  // DataFlow (Data Integration)
  proxy['/api/sync'] = proxyOptions(BACKEND)
  proxy['/api/dag'] = proxyOptions(BACKEND)
  proxy['/api/udfs'] = proxyOptions(BACKEND)
  proxy['/api/scheduled-tasks'] = proxyOptions(BACKEND)
  proxy['/api/report-templates'] = proxyOptions(BACKEND)
  proxy['/api/notification'] = proxyOptions(BACKEND)
  proxy['/api/task-monitor'] = proxyOptions(BACKEND)

  // DataViz (Visualization)
  proxy['/api/dashboard'] = proxyOptions(BACKEND)
  proxy['/api/charts'] = proxyOptions(BACKEND)
  proxy['/api/reports'] = proxyOptions(BACKEND)
  proxy['/api/vis-library'] = proxyOptions(BACKEND)
  proxy['/api/datasets'] = proxyOptions(BACKEND)

  // 知识图谱可视化 + SPARQL 查询 (Oxigraph 后端; 原 graphservice 已并入 semhub，同端口 8000)
  proxy['/api/graph'] = proxyOptions(BACKEND)
  // SemanticService → SemHub — 语义层只读契约端点 (resolve/query/health)
  proxy['/api/semantic'] = proxyOptions(BACKEND)

  // DataCatalog
  proxy['/api/catalog'] = proxyOptions(BACKEND)
  proxy['/api/metadata'] = proxyOptions(BACKEND)
  proxy['/api/metrics'] = proxyOptions(BACKEND)
  proxy['/api/tags'] = proxyOptions(BACKEND)
  proxy['/api/glossary'] = proxyOptions(BACKEND)

  // DataCatalog - 数据源和元数据管理
  proxy['/api/datasources'] = proxyOptions(BACKEND)
  proxy['/api/admin/metadata'] = proxyOptions(BACKEND)
  proxy['/api/admin/table-info'] = proxyOptions(BACKEND)
  proxy['/api/admin/menu-tree'] = proxyOptions(BACKEND)

  // DataMind - 查询、历史、Playground
  proxy['/api/query'] = proxyOptions(BACKEND)
  proxy['/api/history'] = proxyOptions(BACKEND)
  proxy['/api/playground'] = proxyOptions(BACKEND)
  proxy['/api/model-config'] = proxyOptions(BACKEND)

  // AI Platform - MCP, Agents, Embed, Model Lab/Train
  proxy['/api/admin/as-bots'] = proxyOptions(BACKEND)
  proxy['/api/admin/mcp-servers'] = proxyOptions(BACKEND)
  proxy['/api/admin/sync'] = proxyOptions(BACKEND)
  proxy['/api/admin/model-config'] = proxyOptions(BACKEND)
  proxy['/api/admin/prompts'] = proxyOptions(BACKEND)
  proxy['/api/admin/brand'] = proxyOptions(BACKEND)
  proxy['/api/admin/cache'] = proxyOptions(BACKEND)
  proxy['/api/admin/execution-layers'] = proxyOptions(BACKEND)
  proxy['/api/sandbox'] = proxyOptions(BACKEND)
  proxy['/api/embed'] = proxyOptions(BACKEND)
  proxy['/api/model-train'] = proxyOptions(BACKEND)
  proxy['/api/mcp-market'] = proxyOptions(BACKEND)

  // DataCatalog - 管理后台
  proxy['/api/admin/templates'] = proxyOptions(BACKEND)
  proxy['/api/admin/terms'] = proxyOptions(BACKEND)
  proxy['/api/admin/relations'] = proxyOptions(BACKEND)

  // DataViz - 组件数据
  proxy['/api/component-data'] = proxyOptions(BACKEND)

  // Health check - 随便指向一个服务
  proxy['/api/health'] = proxyOptions(BACKEND)

  return proxy
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
