import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// 微服务端口映射
const SERVICES = {
  authservice: 'http://127.0.0.1:8006',
  datacatalog: 'http://127.0.0.1:8005',
  datagov: 'http://127.0.0.1:8002',
  dataviz: 'http://127.0.0.1:8004',
  datamind: 'http://127.0.0.1:8001',
  dataflow: 'http://127.0.0.1:8003',
  aiplatform: 'http://127.0.0.1:8007',
}

// 创建代理配置：每个路径前缀代理到对应的微服务
function createProxyConfig() {
  const proxy: Record<string, any> = {}

  const proxyOptions = (target: string) => ({
    target,
    changeOrigin: true,
    autoRewrite: true,
  })

  // AuthService
  proxy['/api/auth'] = proxyOptions(SERVICES.authservice)
  proxy['/api/users'] = proxyOptions(SERVICES.authservice)
  proxy['/api/workspaces'] = proxyOptions(SERVICES.authservice)
  proxy['/api/roles'] = proxyOptions(SERVICES.authservice)
  proxy['/api/audit'] = proxyOptions(SERVICES.authservice)
  proxy['/api/admin/rls-policies'] = proxyOptions(SERVICES.authservice)
  proxy['/api/admin/rls-user-attributes'] = proxyOptions(SERVICES.authservice)
  proxy['/api/admin/rls-audit-logs'] = proxyOptions(SERVICES.authservice)
  proxy['/api/monitoring'] = proxyOptions(SERVICES.authservice)

  // DataMind (AI Engine) — 需要较长超时
  proxy['/api/chat'] = { ...proxyOptions(SERVICES.datamind), timeout: 600000 }
  proxy['/api/agent'] = { ...proxyOptions(SERVICES.datamind), timeout: 600000 }
  proxy['/api/execution'] = { ...proxyOptions(SERVICES.datamind), timeout: 600000 }
  proxy['/api/pipeline'] = { ...proxyOptions(SERVICES.datamind), timeout: 600000 }
  proxy['/api/knowledge'] = proxyOptions(SERVICES.datamind)

  // DataGov (Data Governance)
  proxy['/api/quality'] = proxyOptions(SERVICES.datagov)
  proxy['/api/lineage'] = proxyOptions(SERVICES.datagov)
  proxy['/api/standards'] = proxyOptions(SERVICES.datagov)
  proxy['/api/security'] = proxyOptions(SERVICES.datagov)

  // DataFlow (Data Integration)
  proxy['/api/sync'] = proxyOptions(SERVICES.dataflow)
  proxy['/api/workflow'] = proxyOptions(SERVICES.dataflow)
  proxy['/api/scheduled-tasks'] = proxyOptions(SERVICES.dataflow)
  proxy['/api/report-templates'] = proxyOptions(SERVICES.dataflow)
  proxy['/api/notification'] = proxyOptions(SERVICES.dataflow)

  // DataViz (Visualization)
  proxy['/api/dashboard'] = proxyOptions(SERVICES.dataviz)
  proxy['/api/charts'] = proxyOptions(SERVICES.dataviz)
  proxy['/api/reports'] = proxyOptions(SERVICES.dataviz)

  // DataCatalog
  proxy['/api/catalog'] = proxyOptions(SERVICES.datacatalog)
  proxy['/api/metadata'] = proxyOptions(SERVICES.datacatalog)
  proxy['/api/metrics'] = proxyOptions(SERVICES.datacatalog)
  proxy['/api/tags'] = proxyOptions(SERVICES.datacatalog)
  proxy['/api/glossary'] = proxyOptions(SERVICES.datacatalog)

  // DataCatalog - 数据源和元数据管理
  proxy['/api/datasources'] = proxyOptions(SERVICES.datacatalog)
  proxy['/api/admin/metadata'] = proxyOptions(SERVICES.datacatalog)
  proxy['/api/admin/table-info'] = proxyOptions(SERVICES.datacatalog)
  proxy['/api/admin/menu-tree'] = proxyOptions(SERVICES.datacatalog)

  // DataMind - 查询、历史、Playground
  proxy['/api/query'] = proxyOptions(SERVICES.datamind)
  proxy['/api/history'] = proxyOptions(SERVICES.datamind)
  proxy['/api/playground'] = proxyOptions(SERVICES.datamind)
  proxy['/api/model-config'] = proxyOptions(SERVICES.datamind)

  // AI Platform - MCP, Agents, Embed, Model Lab/Train, Workflows
  proxy['/api/admin/mcp-servers'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/admin/agents'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/admin/sync'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/admin/model-config'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/admin/workflows'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/admin/prompts'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/admin/workflow-logs'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/admin/brand'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/admin/cache'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/admin/execution-layers'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/embed'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/model-lab'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/model-train'] = proxyOptions(SERVICES.aiplatform)
  proxy['/api/mcp-market'] = proxyOptions(SERVICES.aiplatform)

  // DataCatalog - 管理后台
  proxy['/api/admin/templates'] = proxyOptions(SERVICES.datacatalog)
  proxy['/api/admin/terms'] = proxyOptions(SERVICES.datacatalog)
  proxy['/api/admin/relations'] = proxyOptions(SERVICES.datacatalog)

  // DataViz - 组件数据
  proxy['/api/component-data'] = proxyOptions(SERVICES.dataviz)

  // Health check - 随便指向一个服务
  proxy['/api/health'] = proxyOptions(SERVICES.datamind)

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
