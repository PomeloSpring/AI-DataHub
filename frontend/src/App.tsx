import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom';
import { useAuthStore } from './stores/authStore';
import { usePermissionStore } from './stores/permissionStore';
import { useThemeStore, applyTheme } from './stores/themeStore';
import WorkspaceLayout from './components/WorkspaceLayout';
import { WorkspaceEntry, WorkspaceBoundary } from './components/WorkspaceRoute';
import SystemLayout from './components/SystemLayout';
import DataPlatformLayout from './components/DataPlatformLayout';
import Login from './pages/Login';
import Chat from './pages/Chat';
import Home from './pages/Home';
import WorkspaceAssets from './pages/WorkspaceAssets';
import Dashboard from './pages/Dashboard';
import DashboardEditor from './pages/DashboardEditor';
import Admin from './pages/Admin';
import Playground from './pages/Playground';
import Screen from './pages/Screen';
import Analysis from './pages/Analysis';
import Profile from './pages/Profile';
import WorkspaceManagerV2 from './pages/WorkspaceManagerV2';
import ModelCenter from './pages/admin/ModelCenter';
import MCPAgentConfig from './pages/admin/MCPAgentConfig';
import MCPConfig from './pages/admin/MCPConfig';
import AsBotManager from './pages/admin/AsBotManager';
import SkillsManager from './pages/admin/SkillsManager';
import ScheduledTasks from './pages/admin/ScheduledTasks';
import NotificationChannels from './pages/admin/NotificationChannels';
import VisLibrary from './pages/admin/VisLibrary';
import ReportTemplates from './pages/admin/ReportTemplates';
import ReportView from './pages/ReportView';
import KnowledgeBase from './pages/admin/KnowledgeBase';
import KnowledgeGraph from './pages/KnowledgeGraph';
import VersionedConfigEditor, { createPromptAdapter } from './pages/admin/VersionedConfigEditor';
import ReportsCenter from './pages/ReportsCenter';
import AsBotPanel from './components/asbot/AsBotPanel';

// 新增页面 - 数据中台
import QualityOverview from './pages/quality/QualityOverview';
import QualityRules from './pages/quality/QualityRules';
import LineageGraph from './pages/lineage/LineageGraph';
import OntologyModeling from './pages/catalog/OntologyModeling';
import TagsManager from './pages/catalog/TagsManager';
import SqlPairs from './pages/catalog/SqlPairs';
import Glossary from './pages/catalog/Glossary';
import Datasets from './pages/datasets/Datasets';
import DatasetDetail from './pages/datasets/DatasetDetail';
import SyncTasks from './pages/sync/SyncTasks';
import SyncLogs from './pages/sync/SyncLogs';
import DagEditor from './pages/sync/DagEditor';
import DagRunDetail from './pages/sync/DagRunDetail';
import AuditLog from './pages/admin/AuditLog';
import Standards from './pages/admin/Standards';
import SensitiveData from './pages/admin/SensitiveData';

// 新增页面 - Multi-Agent Enhancement
import RLSManagement from './pages/admin/RLSManagement';
import RoleManagement from './pages/admin/RoleManagement';
import SandboxManagement from './pages/admin/SandboxManagement';
import QualityReview from './pages/admin/QualityReview';
import Observability from './pages/admin/Observability';
import EvalManagement from './pages/admin/EvalManagement';
import KnowledgeManagement from './pages/admin/KnowledgeManagement';
import Monitoring from './pages/admin/Monitoring';
import TaskMonitor from './pages/admin/TaskMonitor';
import UdfManagement from './pages/admin/UdfManagement';

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token);
  return token ? <>{children}</> : <Navigate to="/login" />;
}

/** Redirects /analysis/:id and /screen/:id to /page/:id */
function RedirectToPage() {
  const { id, dashboardId } = useParams();
  const targetId = id ?? dashboardId;
  return <Navigate to={`/page/${targetId}`} replace />;
}

export default function App() {
  const token = useAuthStore((s) => s.token);
  const refreshUser = useAuthStore((s) => s.refreshUser);
  const { loadPermissions, loaded } = usePermissionStore();
  const theme = useThemeStore((s) => s.theme);

  // 主题在应用根统一生效:首页/登录/工作空间入口等无布局包裹的独立页同样消费主题,
  // 否则它们停留在 :root 默认(暗色)调色板,与系统默认主题脱节
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    if (token && !loaded) loadPermissions();
  }, [token, loaded, loadPermissions]);

  // 身份显示以服务端为准: 启动时按 token 校正 user(防多 tab/多账号 localStorage 串号)
  useEffect(() => {
    if (token) void refreshUser();
  }, [token, refreshUser]);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />

        {/* Full-screen pages - outside layout */}
        <Route path="/dashboard/editor/:id" element={<PrivateRoute><DashboardEditor /></PrivateRoute>} />
        <Route path="/screen" element={<PrivateRoute><Screen /></PrivateRoute>} />
        <Route path="/screen/:dashboardId" element={<PrivateRoute><Screen /></PrivateRoute>} />

        {/* 数据看板: 仪表盘组维度(组按角色分配; 旧工作空间维度链接语义迁移为组) */}
        <Route path="/dashboards" element={<PrivateRoute><Analysis /></PrivateRoute>} />
        <Route path="/dashboards/:groupId" element={<PrivateRoute><Analysis /></PrivateRoute>} />
        <Route path="/dashboards/:groupId/:dashboardId" element={<PrivateRoute><Analysis /></PrivateRoute>} />
        <Route path="/ask" element={<PrivateRoute><WorkspaceEntry module="ask" /></PrivateRoute>} />
        <Route path="/workspace" element={<PrivateRoute><WorkspaceEntry module="workspace" /></PrivateRoute>} />
        <Route path="/ask/:workspaceId" element={<PrivateRoute><WorkspaceBoundary><WorkspaceLayout module="ask" /></WorkspaceBoundary></PrivateRoute>}>
          <Route index element={<Chat />} />
          <Route path="profile" element={<Profile />} />
        </Route>

        {/* Workspace mode: /ws/:workspaceId/* */}
        <Route path="/ws/:workspaceId" element={<PrivateRoute><WorkspaceBoundary><WorkspaceLayout /></WorkspaceBoundary></PrivateRoute>}>
          <Route index element={<Chat />} />
          <Route path="chat" element={<Chat />} />
          {/* 资产清单: 工作站共享项目(会话产物归档收藏, OSS 托管) */}
          <Route path="assets" element={<WorkspaceAssets />} />
          <Route path="scheduled" element={<ScheduledTasks />} />
          {/* 报表:自动化任务成功执行的报告,按任务归集 + 历史版本切换 */}
          <Route path="reports" element={<ReportsCenter />} />
          {/* 个人设置:在原本的框架(侧边栏/顶栏)下渲染, 非独立全屏页 */}
          <Route path="profile" element={<Profile />} />
          {/* 仪表盘/可视化大屏菜单: 默认内嵌展示（保留工作空间侧栏），需要播放/轮播时由页面内按钮跳转 /screen/:id */}
          <Route path="page/:dashboardId" element={<Analysis />} />
        </Route>

        {/* Data Platform mode: /data/* */}
        <Route path="/data" element={<PrivateRoute><DataPlatformLayout /></PrivateRoute>}>
          <Route index element={<Navigate to="/data/datasources" replace />} />
          <Route path="datasources" element={<Admin embeddedTab="datasources" />} />
          <Route path="tables" element={<Admin embeddedTab="metadata" />} />
          <Route path="ontology" element={<OntologyModeling />} />
          {/* 指标中心已并入模型工作区页签; 旧路由重定向兼容书签(本体可视化路由保留) */}
          <Route path="metrics" element={<Navigate to="/data/ontology" replace />} />
          <Route path="datasets" element={<Datasets />} />
          <Route path="datasets/:id" element={<DatasetDetail />} />
          <Route path="tags" element={<TagsManager />} />
          <Route path="sql-pairs" element={<SqlPairs />} />
          <Route path="glossary" element={<Glossary />} />
          <Route path="quality" element={<QualityOverview />} />
          <Route path="quality/rules" element={<QualityRules />} />
          <Route path="lineage" element={<LineageGraph />} />
          <Route path="standards" element={<Standards />} />
          <Route path="sensitive" element={<SensitiveData />} />
          {/* 数据安全配置: 敏感数据 + RLS 行级安全聚合在数据中台(自 /system/rls 迁入) */}
          <Route path="rls" element={<RLSManagement />} />
          <Route path="sync" element={<SyncTasks />} />
          <Route path="sync/logs" element={<SyncLogs />} />
          <Route path="sync/dag" element={<Navigate to="/data/sync" replace />} />
          <Route path="sync/dag/new" element={<DagEditor />} />
          <Route path="sync/dag/:workflowId" element={<DagEditor />} />
          <Route path="sync/dag-runs/:runId" element={<DagRunDetail />} />
          <Route path="udfs" element={<UdfManagement />} />
          <Route path="knowledge-graph" element={<KnowledgeGraph />} />
          <Route path="playground" element={<Playground />} />
          {/* 个人设置:在数据中台框架下渲染 */}
          <Route path="profile" element={<Profile />} />
        </Route>

        {/* System config mode: /system/* */}
        <Route path="/system" element={<PrivateRoute><SystemLayout /></PrivateRoute>}>
          <Route index element={<Navigate to="/system/models" replace />} />
          <Route path="users" element={<Admin embeddedTab="users" />} />
          <Route path="models" element={<ModelCenter />} />
          <Route path="mcp-agent" element={<MCPAgentConfig />} />
          <Route path="mcp" element={<MCPConfig />} />
          <Route path="as-bots" element={<AsBotManager />} />
          {/* 旧 Waker 路由重定向以兼容书签（Waker 已更名为 AS-BOT） */}
          <Route path="wakers" element={<Navigate to="/system/as-bots" replace />} />
          <Route path="skills" element={<SkillsManager />} />
          {/* 旧 Agents 配置页已合并入 AS-BOT 配置,保留重定向以兼容书签 */}
          <Route path="agents" element={<Navigate to="/system/as-bots" replace />} />
          {/* 执行层已合并入模型中心,旧路由重定向以兼容书签 */}
          <Route path="execution-layers" element={<Navigate to="/system/models" replace />} />
          {/* 版本化配置只管 Prompt; MCP 版本化已取消(服务本身由 MCP 配置页管理), skills 版本在 Skills 管理内做 */}
          <Route path="config-versions" element={<VersionedConfigEditor adapters={[createPromptAdapter()]} />} />
          <Route path="notification-channels" element={<NotificationChannels />} />
          <Route path="report-templates" element={<ReportTemplates />} />
          <Route path="knowledge-base" element={<KnowledgeBase />} />
          {/* 旧本体图路由重定向到数据中台本体建模 */}
          <Route path="knowledge-graph" element={<Navigate to="/data/ontology" replace />} />
          <Route path="settings" element={<Admin embeddedTab="brand" />} />
          {/* 权限管理 */}
          <Route path="workspaces" element={<WorkspaceManagerV2 />} />
          <Route path="roles" element={<RoleManagement />} />
          <Route path="audit" element={<AuditLog />} />
          {/* 行级安全已迁入数据中台「数据安全配置」(/data/rls), 旧路由重定向兼容书签 */}
          <Route path="rls" element={<Navigate to="/data/rls" replace />} />
          <Route path="sandbox" element={<SandboxManagement />} />
          <Route path="quality-review" element={<QualityReview />} />
          <Route path="observability" element={<Observability />} />
          <Route path="eval" element={<EvalManagement />} />
          <Route path="knowledge-management" element={<KnowledgeManagement />} />
          <Route path="dashboards" element={<Dashboard />} />
          <Route path="dashboards/:dashboardId" element={<Analysis />} />
          <Route path="vis-library" element={<VisLibrary />} />
          {/* 系统 */}
          <Route path="monitoring" element={<Monitoring />} />
          <Route path="task-monitor" element={<TaskMonitor />} />
          {/* 个人设置:在系统配置框架下渲染 */}
          <Route path="profile" element={<Profile />} />
        </Route>

        {/* Legacy route redirects */}
        <Route path="/ws/:workspaceId/settings" element={<Navigate to="/system/workspaces" replace />} />
        <Route path="/chat" element={<PrivateRoute><WorkspaceEntry module="ask" /></PrivateRoute>} />
        <Route path="/history" element={<PrivateRoute><Navigate to="/system/observability" replace /></PrivateRoute>} />
        <Route path="/page" element={<PrivateRoute><WorkspaceEntry legacyId /></PrivateRoute>} />
        <Route path="/page/:dashboardId" element={<PrivateRoute><WorkspaceEntry legacyId /></PrivateRoute>} />
        <Route path="/analysis/:id" element={<RedirectToPage />} />
        <Route path="/dashboard" element={<Navigate to="/page" replace />} />

        {/* 工作空间管理仅归属系统配置，旧入口/书签统一重定向 */}
        <Route path="/workspaces" element={<Navigate to="/system/workspaces" replace />} />

        {/* Report view (public/private, auth optional) */}
        <Route path="/report/:reportId" element={<ReportView />} />

        {/* AS-BOT 全屏独立标签页 */}
        <Route path="/as-bot/chat" element={<PrivateRoute><AsBotPanel fullscreen /></PrivateRoute>} />

        {/* Profile: 旧全屏独立路由 → 重定向到数据中台框架下的内嵌页(兼容书签) */}
        <Route path="/profile" element={<Navigate to="/data/profile" replace />} />

        {/* Legacy admin routes redirect to system */}
        <Route path="/admin" element={<Navigate to="/system/settings" replace />} />
        <Route path="/admin/data" element={<Navigate to="/system/datasources" replace />} />
        <Route path="/admin/model" element={<Navigate to="/system/models" replace />} />
        <Route path="/admin/mcp-agent" element={<Navigate to="/system/mcp-agent" replace />} />
        <Route path="/admin/prompts" element={<Navigate to="/system/config-versions" replace />} />

        {/* Other legacy routes */}
        <Route path="/playground" element={<PrivateRoute><Playground /></PrivateRoute>} />

        {/* 默认首页: '/' 恒展示 Home(模块切换选「首页」即进首页,不因有看板而跳走);
            登录时刻“有看板直达数据看板”的落点判定在 Login */}
        <Route path="/" element={<PrivateRoute><Home /></PrivateRoute>} />
      </Routes>

    </BrowserRouter>
  );
}
