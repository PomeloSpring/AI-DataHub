import { useEffect, type ReactNode } from 'react';
import { Navigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
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
  const { t } = useTranslation('workspace');
  const store = useWorkspaceStore();
  return <div className="flex h-full min-h-48 flex-col items-center justify-center gap-3 p-6" role="status">
    <p>{store.loading || !store.loaded ? t('route.loading') : store.error || t('route.noAccess')}</p>
    {!!store.workspaces.length && store.error && <select aria-label={t('route.selectAria')} className="rounded border bg-background p-2" value="" onChange={e => { store.setWorkspace(Number(e.target.value)); window.location.assign(`/ws/${e.target.value}/dashboards`); }}><option value="" disabled>{t('route.selectPlaceholder')}</option>{store.workspaces.map(ws => <option key={ws.id} value={ws.id}>{ws.name}</option>)}</select>}
    {store.loaded && <Button variant="outline" disabled={store.loading} onClick={() => void store.loadWorkspaces()}>{t('route.reload')}</Button>}
  </div>;
}

export function WorkspaceEntry({ module = 'dashboards', legacyId = false }: { module?: 'dashboards' | 'ask' | 'workspace' | 'screen'; legacyId?: boolean }) {
  const store = useWorkspaces();
  const { dashboardId, id, groupId } = useParams();
  const wsId = store.getDefaultWorkspaceId();
  if (!store.loaded || store.loading || store.error || !wsId) return <WorkspaceStatus />;
  if (module === 'screen') return <Navigate to={`/screen${dashboardId || id ? `/${dashboardId || id}` : ''}?workspace_id=${wsId}&from=${encodeURIComponent(`/ws/${wsId}/dashboards`)}`} replace />;
  // 工作空间默认页 = 数据看板组合清单; 数据看板已收进工作空间('dashboards' 同落点)
  // 旧独立看板域的单段链接 /dashboards/<groupId> 仍指向该组合入口（由入口页直接跳组内看板详情，不降级成“看全部”）
  const base = module === 'workspace' || module === 'dashboards' ? `/ws/${wsId}/dashboards` : `/${module}/${wsId}`;
  const target = legacyId && (dashboardId || id) ? `${base}/${dashboardId || id}`
    : module === 'dashboards' && groupId ? `${base}/group/${groupId}` : base;
  return <Navigate to={target} replace />;
}

export function WorkspaceBoundary({ children, scope }: { children: ReactNode; scope?: number }) {
  const { t } = useTranslation('workspace');
  const store = useWorkspaces();
  const { workspaceId } = useParams();
  const id = scope ?? Number(workspaceId);
  const valid = store.workspaces.some(w => w.id === id);
  useEffect(() => { if (valid && store.currentWorkspaceId !== id) store.setWorkspace(id); }, [valid, id, store.currentWorkspaceId, store.setWorkspace]);
  if (!store.loaded || store.loading || store.error) return <WorkspaceStatus />;
  if (!valid) return <div className="space-y-3 p-8" role="alert"><p>{t('route.notFound')}</p><a className="text-primary underline" href="/dashboards">{t('route.chooseAccessible')}</a></div>;
  return <>{children}</>;
}
