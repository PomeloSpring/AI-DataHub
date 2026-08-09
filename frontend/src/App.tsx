import { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom';
import { useAuthStore } from './stores/authStore';
import { useWorkspaceStore } from './stores/workspaceStore';
import WorkspaceLayout from './components/WorkspaceLayout';
import SystemLayout from './components/SystemLayout';
import DataPlatformLayout from './components/DataPlatformLayout';
import { AIFloatingBox } from './components/ai-assistant';
import Login from './pages/Login';
import Chat from './pages/Chat';
import Dashboard from './pages/Dashboard';
import DashboardEditor from './pages/DashboardEditor';
import History from './pages/History';
import Admin from './pages/Admin';
import Playground from './pages/Playground';
import Screen from './pages/Screen';
import Analysis from './pages/Analysis';
import Profile from './pages/Profile';
import ModelLab from './pages/ModelLab';
import ModelTrain from './pages/ModelTrain';
import WorkspaceManagerV2 from './pages/WorkspaceManagerV2';
import WorkflowConfig from './pages/admin/WorkflowConfig';
import WorkflowEditor from './pages/admin/WorkflowEditor';
import PromptManager from './pages/admin/PromptManager';
import ModelCenter from './pages/admin/ModelCenter';
import MCPAgentConfig from './pages/admin/MCPAgentConfig';
import MCPConfig from './pages/admin/MCPConfig';
import AgentConfig from './pages/admin/AgentConfig';
import ExecutionLayers from './pages/admin/ExecutionLayers';
import ScheduledTasks from './pages/admin/ScheduledTasks';
import NotificationChannels from './pages/admin/NotificationChannels';
import ReportTemplates from './pages/admin/ReportTemplates';
import ReportView from './pages/ReportView';
import KnowledgeBase from './pages/admin/KnowledgeBase';
import KnowledgeGraph from './pages/KnowledgeGraph';
import SkillsTemplateManager from './pages/admin/SkillsTemplateManager';
import ComingSoon from './pages/ComingSoon';

// 新增页面 - 数据中台
import QualityOverview from './pages/quality/QualityOverview';
import QualityRules from './pages/quality/QualityRules';
import LineageGraph from './pages/lineage/LineageGraph';
import MetricsCenter from './pages/catalog/MetricsCenter';
import OntologyModeling from './pages/catalog/OntologyModeling';
import TagsManager from './pages/catalog/TagsManager';
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
import KnowledgeManagement from './pages/admin/KnowledgeManagement';
import Monitoring from './pages/admin/Monitoring';

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
  const wsId = useWorkspaceRedirect();
  if (!wsId) return null;
  return <Navigate to={`/ws/${wsId}/history`} replace />;
}

export default function App() {
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
          <Route path="history" element={<History />} />
          <Route path="scheduled" element={<ScheduledTasks />} />
          {/* 报表 */}
          <Route path="reports" element={<ComingSoon title="报表中心" description="智能报表生成和查看" />} />
        </Route>

        {/* Data Platform mode: /data/* */}
        <Route path="/data" element={<PrivateRoute><DataPlatformLayout /></PrivateRoute>}>
          <Route index element={<Navigate to="/data/datasources" replace />} />
          <Route path="datasources" element={<Admin embeddedTab="datasources" />} />
          <Route path="tables" element={<Admin embeddedTab="metadata" />} />
          <Route path="ontology" element={<OntologyModeling />} />
          <Route path="metrics" element={<MetricsCenter />} />
          <Route path="tags" element={<TagsManager />} />
          <Route path="glossary" element={<Glossary />} />
          <Route path="quality" element={<QualityOverview />} />
          <Route path="quality/rules" element={<QualityRules />} />
          <Route path="lineage" element={<LineageGraph />} />
          <Route path="standards" element={<Standards />} />
          <Route path="sensitive" element={<SensitiveData />} />
          <Route path="sync" element={<SyncTasks />} />
          <Route path="sync/logs" element={<SyncLogs />} />
          <Route path="knowledge-graph" element={<KnowledgeGraph />} />
        </Route>

        {/* System config mode: /system/* */}
        <Route path="/system" element={<PrivateRoute><SystemLayout /></PrivateRoute>}>
          <Route index element={<Navigate to="/system/models" replace />} />
          <Route path="users" element={<Admin embeddedTab="users" />} />
          <Route path="models" element={<ModelCenter />} />
          <Route path="mcp-agent" element={<MCPAgentConfig />} />
          <Route path="mcp" element={<MCPConfig />} />
          <Route path="agents" element={<AgentConfig />} />
          <Route path="execution-layers" element={<ExecutionLayers />} />
          <Route path="workflows" element={<WorkflowConfig />} />
          <Route path="workflow-editor" element={<WorkflowEditor />} />
          <Route path="prompts" element={<PromptManager />} />
          <Route path="skills" element={<SkillsTemplateManager />} />
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
          <Route path="knowledge-management" element={<KnowledgeManagement />} />
          <Route path="dashboards" element={<Dashboard />} />
          <Route path="dashboards/:dashboardId" element={<Analysis />} />
          {/* 系统 */}
          <Route path="monitoring" element={<Monitoring />} />
        </Route>

        {/* Legacy route redirects */}
        <Route path="/ws/:workspaceId/settings" element={<Navigate to="/system/workspaces" replace />} />
        <Route path="/chat" element={<PrivateRoute><LegacyChatRedirect /></PrivateRoute>} />
        <Route path="/history" element={<PrivateRoute><LegacyHistoryRedirect /></PrivateRoute>} />
        <Route path="/page" element={<PrivateRoute><LegacyPageRedirect /></PrivateRoute>} />
        <Route path="/page/:dashboardId" element={<PrivateRoute><LegacyPageRedirect /></PrivateRoute>} />
        <Route path="/analysis/:id" element={<RedirectToPage />} />
        <Route path="/dashboard" element={<Navigate to="/page" replace />} />

        {/* Workspace management (standalone page, accessible from both modes) */}
        <Route path="/workspaces" element={<PrivateRoute><WorkspaceManagerV2 /></PrivateRoute>} />

        {/* Report view (public/private, auth optional) */}
        <Route path="/report/:reportId" element={<ReportView />} />

        {/* Profile (standalone) */}
        <Route path="/profile" element={<PrivateRoute><Profile /></PrivateRoute>} />

        {/* Legacy admin routes redirect to system */}
        <Route path="/admin" element={<Navigate to="/system/settings" replace />} />
        <Route path="/admin/data" element={<Navigate to="/system/datasources" replace />} />
        <Route path="/admin/model" element={<Navigate to="/system/models" replace />} />
        <Route path="/admin/mcp-agent" element={<Navigate to="/system/mcp-agent" replace />} />
        <Route path="/admin/workflow" element={<Navigate to="/system/workflows" replace />} />
        <Route path="/admin/prompts" element={<Navigate to="/system/skills" replace />} />

        {/* Other legacy routes */}
        <Route path="/playground" element={<PrivateRoute><Playground /></PrivateRoute>} />
        <Route path="/model-lab" element={<PrivateRoute><ModelLab /></PrivateRoute>} />
        <Route path="/model-train" element={<PrivateRoute><ModelTrain /></PrivateRoute>} />

        {/* Default redirect */}
        <Route path="/" element={<PrivateRoute><LegacyChatRedirect /></PrivateRoute>} />
      </Routes>

      {/* AI Assistant Floating Box - 全局悬浮框 */}
      <AIFloatingBox />
    </BrowserRouter>
  );
}
