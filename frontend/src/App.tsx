import { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom';
import { useAuthStore } from './stores/authStore';
import { useWorkspaceStore } from './stores/workspaceStore';
import { usePermissionStore } from './stores/permissionStore';
import WorkspaceLayout from './components/WorkspaceLayout';
import SystemLayout from './components/SystemLayout';
import DataPlatformLayout from './components/DataPlatformLayout';
import Login from './pages/Login';
import Chat from './pages/Chat';
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
import WakerManager from './pages/admin/WakerManager';
import SkillsManager from './pages/admin/SkillsManager';
import ScheduledTasks from './pages/admin/ScheduledTasks';
import NotificationChannels from './pages/admin/NotificationChannels';
import ReportTemplates from './pages/admin/ReportTemplates';
import VisLibrary from './pages/admin/VisLibrary';
import ReportView from './pages/ReportView';
import KnowledgeBase from './pages/admin/KnowledgeBase';
import KnowledgeGraph from './pages/KnowledgeGraph';
import VersionedConfigEditor, { createPromptAdapter, createMcpAdapter } from './pages/admin/VersionedConfigEditor';
import ReportsCenter from './pages/ReportsCenter';

// 新增页面 - 数据中台
import QualityOverview from './pages/quality/QualityOverview';
import QualityRules from './pages/quality/QualityRules';
import LineageGraph from './pages/lineage/LineageGraph';
import MetricsCenter from './pages/catalog/MetricsCenter';
import OntologyModeling from './pages/catalog/OntologyModeling';
import TagsManager from './pages/catalog/TagsManager';
import SqlPairs from './pages/catalog/SqlPairs';
import Glossary from './pages/catalog/Glossary';
import SyncTasks from './pages/sync/SyncTasks';
import SyncLogs from './pages/sync/SyncLogs';
import Roles from './pages/admin/Roles';
import AuditLog from './pages/admin/AuditLog';
import Standards from './pages/admin/Standards';
import SensitiveData from './pages/admin/SensitiveData';

// 新增页面 - Multi-Agent Enhancement
import RLSManagement from './pages/admin/RLSManagement';
import RoleManagement from './pages/admin/RoleManagement';
import SandboxManagement from './pages/admin/SandboxManagement';
import QualityReview from './pages/admin/QualityReview';
import Observability from './pages/admin/Observability';
import KnowledgeManagement from './pages/admin/KnowledgeManagement';
import Monitoring from './pages/admin/Monitoring';
import AsBotSettings from './pages/admin/AsBotSettings';

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

/** Redirect old routes to workspace-scoped routes, loading workspaces first */
function useWorkspaceRedirect(): number | null {
  const { workspaces, currentWorkspaceId, loadWorkspaces, getDefaultWorkspaceId } = useWorkspaceStore();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (workspaces.length === 0 && !ready) {
      loadWorkspaces().then(() => setReady(true));
    } else {
      setReady(true);
    }
  }, [workspaces.length]);

  if (!ready || workspaces.length === 0) return null;
  return getDefaultWorkspaceId();
}

function LegacyChatRedirect() {
  const wsId = useWorkspaceRedirect();
  if (!wsId) return null; // wait for load
  return <Navigate to={`/ws/${wsId}/chat`} replace />;
}

function LegacyPageRedirect() {
  const wsId = useWorkspaceRedirect();
  if (!wsId) return null;
  return <Navigate to={`/ws/${wsId}/page`} replace />;
}

function LegacyHistoryRedirect() {
  if (!useWorkspaceRedirect()) return null;
  return <Navigate to="/system/observability" replace />;
}

export default function App() {
  const token = useAuthStore((s) => s.token);
  const { loadPermissions, loaded } = usePermissionStore();

  useEffect(() => {
    if (token && !loaded) loadPermissions();
  }, [token, loaded, loadPermissions]);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />

        {/* Full-screen pages - outside layout */}
        <Route path="/dashboard/editor/:id" element={<PrivateRoute><DashboardEditor /></PrivateRoute>} />
        <Route path="/screen" element={<PrivateRoute><Screen /></PrivateRoute>} />
        <Route path="/screen/:dashboardId" element={<PrivateRoute><Screen /></PrivateRoute>} />

        {/* Workspace mode: /ws/:workspaceId/* */}
        <Route path="/ws/:workspaceId" element={<PrivateRoute><WorkspaceLayout /></PrivateRoute>}>
          <Route index element={<Chat />} />
          <Route path="chat" element={<Chat />} />
          <Route path="scheduled" element={<ScheduledTasks />} />
          {/* 报表:自动化任务成功执行的报告,按任务归集 + 历史版本切换 */}
          <Route path="reports" element={<ReportsCenter />} />
          {/* 个人设置:在原本的框架(侧边栏/顶栏)下渲染, 非独立全屏页 */}
          <Route path="profile" element={<Profile />} />
        </Route>

        {/* Data Platform mode: /data/* */}
        <Route path="/data" element={<PrivateRoute><DataPlatformLayout /></PrivateRoute>}>
          <Route index element={<Navigate to="/data/datasources" replace />} />
          <Route path="datasources" element={<Admin embeddedTab="datasources" />} />
          <Route path="tables" element={<Admin embeddedTab="metadata" />} />
          <Route path="ontology" element={<OntologyModeling />} />
          <Route path="metrics" element={<MetricsCenter />} />
          <Route path="tags" element={<TagsManager />} />
          <Route path="sql-pairs" element={<SqlPairs />} />
          <Route path="glossary" element={<Glossary />} />
          <Route path="quality" element={<QualityOverview />} />
          <Route path="quality/rules" element={<QualityRules />} />
          <Route path="lineage" element={<LineageGraph />} />
          <Route path="standards" element={<Standards />} />
          <Route path="sensitive" element={<SensitiveData />} />
          <Route path="sync" element={<SyncTasks />} />
          <Route path="sync/logs" element={<SyncLogs />} />
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
          <Route path="wakers" element={<WakerManager />} />
          <Route path="skills" element={<SkillsManager />} />
          {/* 旧 Agents 配置页合并入 Waker,保留重定向以兼容书签 */}
          <Route path="agents" element={<Navigate to="/system/wakers" replace />} />
          {/* 执行层已合并入模型中心,旧路由重定向以兼容书签 */}
          <Route path="execution-layers" element={<Navigate to="/system/models" replace />} />
          <Route path="config-versions" element={<VersionedConfigEditor adapters={[createPromptAdapter(), createMcpAdapter()]} />} />
          <Route path="notification-channels" element={<NotificationChannels />} />
          <Route path="report-templates" element={<ReportTemplates />} />
          <Route path="knowledge-base" element={<KnowledgeBase />} />
          <Route path="knowledge-graph" element={<KnowledgeGraph />} />
          <Route path="settings" element={<Admin embeddedTab="brand" />} />
          {/* 权限管理 */}
          <Route path="workspaces" element={<WorkspaceManagerV2 />} />
          <Route path="roles" element={<RoleManagement />} />
          <Route path="audit" element={<AuditLog />} />
          <Route path="rls" element={<RLSManagement />} />
          <Route path="sandbox" element={<SandboxManagement />} />
          <Route path="quality-review" element={<QualityReview />} />
          <Route path="observability" element={<Observability />} />
          <Route path="as-bot" element={<AsBotSettings />} />
          <Route path="knowledge-management" element={<KnowledgeManagement />} />
          <Route path="dashboards" element={<Dashboard />} />
          <Route path="dashboards/:dashboardId" element={<Analysis />} />
          <Route path="vis-library" element={<VisLibrary />} />
          {/* 系统 */}
          <Route path="monitoring" element={<Monitoring />} />
          {/* 个人设置:在系统配置框架下渲染 */}
          <Route path="profile" element={<Profile />} />
        </Route>

        {/* Legacy route redirects */}
        <Route path="/ws/:workspaceId/settings" element={<Navigate to="/system/workspaces" replace />} />
        <Route path="/chat" element={<PrivateRoute><LegacyChatRedirect /></PrivateRoute>} />
        <Route path="/history" element={<PrivateRoute><LegacyHistoryRedirect /></PrivateRoute>} />
        <Route path="/page" element={<PrivateRoute><LegacyPageRedirect /></PrivateRoute>} />
        <Route path="/page/:dashboardId" element={<PrivateRoute><LegacyPageRedirect /></PrivateRoute>} />
        <Route path="/analysis/:id" element={<RedirectToPage />} />
        <Route path="/dashboard" element={<Navigate to="/page" replace />} />

        {/* 工作空间管理仅归属系统配置，旧入口/书签统一重定向 */}
        <Route path="/workspaces" element={<Navigate to="/system/workspaces" replace />} />

        {/* Report view (public/private, auth optional) */}
        <Route path="/report/:reportId" element={<ReportView />} />

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

        {/* Default redirect */}
        <Route path="/" element={<PrivateRoute><LegacyChatRedirect /></PrivateRoute>} />
      </Routes>

    </BrowserRouter>
  );
}
