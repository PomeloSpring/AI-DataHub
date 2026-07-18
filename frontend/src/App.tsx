import { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom';
import { useAuthStore } from './stores/authStore';
import { useWorkspaceStore } from './stores/workspaceStore';
import WorkspaceLayout from './components/WorkspaceLayout';
import SystemLayout from './components/SystemLayout';
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
import ScheduledTasks from './pages/admin/ScheduledTasks';
import NotificationChannels from './pages/admin/NotificationChannels';
import ReportTemplates from './pages/admin/ReportTemplates';
import ReportView from './pages/ReportView';
import KnowledgeBase from './pages/admin/KnowledgeBase';

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
          <Route path="page" element={<Dashboard />} />
          <Route path="page/:dashboardId" element={<Analysis />} />
          <Route path="history" element={<History />} />
          <Route path="settings" element={<WorkspaceManagerV2 />} />
        </Route>

        {/* System config mode: /system/* */}
        <Route path="/system" element={<PrivateRoute><SystemLayout /></PrivateRoute>}>
          <Route index element={<Navigate to="/system/datasources" replace />} />
          <Route path="datasources" element={<Admin embeddedTab="datasources" />} />
          <Route path="metadata" element={<Admin embeddedTab="metadata" />} />
          <Route path="relations" element={<Admin embeddedTab="relations" />} />
          <Route path="templates" element={<Admin embeddedTab="templates" />} />
          <Route path="terms" element={<Admin embeddedTab="terms" />} />
          <Route path="users" element={<Admin embeddedTab="users" />} />
          <Route path="models" element={<ModelCenter />} />
          <Route path="mcp-agent" element={<MCPAgentConfig />} />
          <Route path="workflows" element={<WorkflowConfig />} />
          <Route path="workflow-editor" element={<WorkflowEditor />} />
          <Route path="prompts" element={<PromptManager />} />
          <Route path="scheduled-tasks" element={<ScheduledTasks />} />
          <Route path="notification-channels" element={<NotificationChannels />} />
          <Route path="report-templates" element={<ReportTemplates />} />
          <Route path="knowledge-base" element={<KnowledgeBase />} />
          <Route path="settings" element={<Admin embeddedTab="brand" />} />
        </Route>

        {/* Legacy route redirects */}
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
        <Route path="/admin/prompts" element={<Navigate to="/system/prompts" replace />} />

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
