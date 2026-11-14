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

/**
 * 进入「工作空间」的落点唯一口径（登录落地 / 模块切换器 / 首页卡片共用）。
 *
 * 工作空间默认页 = 智能问数（侧栏「数据分析」组第一项）。已知默认空间 id 时直达该页，
 * 未加载或当前用户无可访问空间时回 /workspace 入口由 WorkspaceEntry 解析：
 * 它会拉清单并显式呈现加载态/「暂无可访问的工作空间」，不静默停在空白页。
 */
export function workspaceLandingPath(wsId: number) {
  return wsId ? `/ws/${wsId}/chat` : '/workspace';
}

export function WorkspaceStatus() {
  const store = useWorkspaceStore();
  return <div className="flex h-full min-h-48 flex-col items-center justify-center gap-3 p-6" role="status">
    <p>{store.loading || !store.loaded ? '正在加载工作空间…' : store.error || '暂无可访问的工作空间，请联系管理员分配权限'}</p>
    {!!store.workspaces.length && store.error && <select aria-label="选择可访问工作空间" className="rounded border bg-background p-2" value="" onChange={e => { store.setWorkspace(Number(e.target.value)); window.location.assign(`/ws/${e.target.value}/dashboards`); }}><option value="" disabled>选择工作空间</option>{store.workspaces.map(ws => <option key={ws.id} value={ws.id}>{ws.name}</option>)}</select>}
    {store.loaded && <Button variant="outline" disabled={store.loading} onClick={() => void store.loadWorkspaces()}>重新加载</Button>}
    {/* 无可访问空间时本组件是登录后的落地面，必须给出可去的另一处入口，不能把人关在状态页里 */}
    {!store.loading && !store.workspaces.length && <a className="text-sm text-primary underline" href="/">返回首页</a>}
  </div>;
}

export function WorkspaceEntry({ module = 'dashboards', legacyId = false }: { module?: 'dashboards' | 'ask' | 'workspace' | 'screen'; legacyId?: boolean }) {
  const store = useWorkspaces();
  const { dashboardId, id, groupId } = useParams();
  const wsId = store.getDefaultWorkspaceId();
  if (!store.loaded || store.loading || store.error || !wsId) return <WorkspaceStatus />;
  if (module === 'screen') return <Navigate to={`/screen${dashboardId || id ? `/${dashboardId || id}` : ''}?workspace_id=${wsId}&from=${encodeURIComponent(`/ws/${wsId}/dashboards`)}`} replace />;
  // 两个不同语义（不得归一）：'workspace' = 工作空间默认页（走 workspaceLandingPath，现 = 智能问数）；
  // 'dashboards' = 旧独立看板域书签/大屏回跳的落点（看板组合清单），不随默认页移动。
  // 旧单段链接 /dashboards/<groupId> 仍指向该组合入口（由入口页直接跳组内看板详情，不降级成“看全部”）
  const base = module === 'workspace' ? workspaceLandingPath(wsId)
    : module === 'dashboards' ? `/ws/${wsId}/dashboards` : `/${module}/${wsId}`;
  const target = legacyId && (dashboardId || id) ? `${base}/${dashboardId || id}`
    : module === 'dashboards' && groupId ? `${base}/group/${groupId}` : base;
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
