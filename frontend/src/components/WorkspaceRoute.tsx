import { useEffect, type ReactNode } from 'react';
import { Navigate, useParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { useWorkspaceStore } from '@/stores/workspaceStore';

function useWorkspaces() {
  const store = useWorkspaceStore();
  useEffect(() => {
    if (!store.loaded && !store.loading) void store.loadWorkspaces();
  }, [store.loaded, store.loading, store.loadWorkspaces]);
  return store;
}

export function WorkspaceStatus() {
  const store = useWorkspaceStore();
  return <div className="flex h-full min-h-48 flex-col items-center justify-center gap-3 p-6" role="status">
    <p>{store.loading || !store.loaded ? '正在加载工作空间…' : store.error || '暂无可访问的工作空间，请联系管理员分配权限'}</p>
    {!!store.workspaces.length && store.error && <select aria-label="选择可访问工作空间" className="rounded border bg-background p-2" value="" onChange={e => { store.setWorkspace(Number(e.target.value)); window.location.assign(`/dashboards/${e.target.value}`); }}><option value="" disabled>选择工作空间</option>{store.workspaces.map(ws => <option key={ws.id} value={ws.id}>{ws.name}</option>)}</select>}
    {store.loaded && <Button variant="outline" disabled={store.loading} onClick={() => void store.loadWorkspaces()}>重新加载</Button>}
  </div>;
}

export function WorkspaceEntry({ module = 'dashboards', legacyId = false }: { module?: 'dashboards' | 'ask' | 'workspace' | 'screen'; legacyId?: boolean }) {
  const store = useWorkspaces();
  const { dashboardId, id } = useParams();
  const wsId = store.getDefaultWorkspaceId();
  if (!store.loaded || store.loading || store.error || !wsId) return <WorkspaceStatus />;
  if (module === 'screen') return <Navigate to={`/screen${dashboardId || id ? `/${dashboardId || id}` : ''}?workspace_id=${wsId}&from=${encodeURIComponent(`/dashboards/${wsId}`)}`} replace />;
  const base = module === 'workspace' ? `/ws/${wsId}/chat` : `/${module}/${wsId}`;
  const target = legacyId && (dashboardId || id) ? `${base}/${dashboardId || id}` : base;
  return <Navigate to={target} replace />;
}

export function WorkspaceBoundary({ children, scope }: { children: ReactNode; scope?: number }) {
  const store = useWorkspaces();
  const { workspaceId } = useParams();
  const id = scope ?? Number(workspaceId);
  const valid = store.workspaces.some(w => w.id === id);
  useEffect(() => { if (valid && store.currentWorkspaceId !== id) store.setWorkspace(id); }, [valid, id, store.currentWorkspaceId, store.setWorkspace]);
  if (!store.loaded || store.loading || store.error) return <WorkspaceStatus />;
  if (!valid) return <div className="space-y-3 p-8" role="alert"><p>该工作空间不存在或已无访问权限。</p><a className="text-primary underline" href="/dashboards">选择可访问的工作空间</a></div>;
  return <>{children}</>;
}
