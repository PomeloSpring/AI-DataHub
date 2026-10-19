import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { BarChart3, LayoutDashboard, Play, Search, Settings } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { hasPerm } from '@/stores/permissionStore';
import { fetchVisibleGroups, flattenGroupBoards, type DashboardPreview, type GroupView } from '@/lib/dashboardCatalog';

// 工作空间「数据看板」清单: 卡片瀑布呈现当前用户可见的看板, 点卡片进详情。
// 数据口径 = GET /dashboard/groups/visible(组按角色分配 ∩ 角色可见, fail-closed);
// 浏览域只消费已启用看板 —— 设计中/已关闭看板不混进消费清单, 只在顶部显式提示数量。
// 本页纯消费(查看/播放), 不提供编辑: 看板编辑只在系统配置「看板管理」(仅属主/admin 可写)。

/** 卡片预览画布: 按画布宽高比撑高(瀑布因此高低错落), 区块只画几何不含口径 */
function DashboardPreviewCanvas({ preview }: { preview?: DashboardPreview }) {
  const width = preview?.width || 1920;
  const height = preview?.height || 1080;
  const blocks = preview?.blocks || [];
  return <div className="relative w-full overflow-hidden rounded-md border bg-muted/30"
    style={{ aspectRatio: `${width} / ${height}` }} aria-hidden>
    {blocks.map((b, i) => <span key={i}
      className="absolute rounded-sm border border-border/60 bg-card/80 p-[3%]"
      style={{ left: `${b.x / width * 100}%`, top: `${b.y / height * 100}%`, width: `${b.w / width * 100}%`, height: `${b.h / height * 100}%` }}>
      {b.widget ? <i className="block h-[2px] w-1/2 rounded bg-primary/40" />
        : <i className="flex h-full items-end gap-[2px]">{[45, 80, 60, 92].map((h, j) =>
          <b key={j} className="flex-1 rounded-sm bg-primary/25" style={{ height: `${h}%` }} />)}</i>}
    </span>)}
    {!blocks.length && <span className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">空白画布</span>}
  </div>;
}

export default function WorkspaceDashboards() {
  const { workspaceId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [groups, setGroups] = useState<GroupView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [groupId, setGroupId] = useState<number | 'all'>('all');
  const canManage = hasPerm('dashboard:manage');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setGroups(await fetchVisibleGroups());
    } catch {
      setError('加载看板清单失败，请重试');
      setGroups([]);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const boards = useMemo(() => flattenGroupBoards(groups), [groups]);
  const enabled = useMemo(() => boards.filter(b => b.status === 'enabled'), [boards]);
  // 组筛选只列「有已启用看板」的组, 避免选中后必然空集
  const selectableGroups = useMemo(() => groups.filter(g => g.dashboards.some(d => d.status === 'enabled')), [groups]);
  const visible = useMemo(() => enabled.filter(b =>
    (groupId === 'all' || b.group_id === groupId) &&
    `${b.name} ${b.description || ''}`.toLowerCase().includes(search.toLowerCase())
  ), [enabled, groupId, search]);
  const unreadyCount = boards.length - enabled.length;

  const open = (id: number) => navigate(`/ws/${workspaceId}/dashboards/${id}`, { state: { from: location.pathname } });
  const play = (id: number, group: number) => navigate(
    `/screen/${id}?group=${group}&from=${encodeURIComponent(location.pathname)}`, { state: { from: location.pathname } });

  return <div className="mx-auto max-w-[1800px] space-y-5 p-5 md:p-8">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <div className="mb-2 text-xs tracking-[0.2em] text-muted-foreground">WORKSPACE / DASHBOARDS</div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <BarChart3 className="h-6 w-6 text-primary" />数据看板
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">你被授权查看的看板都在这里，点开卡片进入看板详情。</p>
      </div>
      {canManage && <Button variant="outline" onClick={() => navigate('/system/dashboards')}>
        <Settings className="mr-2 h-4 w-4" />看板管理
      </Button>}
    </header>

    <div className="flex flex-wrap items-center gap-3 border-y py-4">
      <div className="relative min-w-[200px] max-w-md flex-1">
        <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
        <Input aria-label="搜索看板" placeholder="搜索名称或描述…" className="pl-9" value={search} onChange={e => setSearch(e.target.value)} />
      </div>
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="按看板组合筛选">
        <Button size="sm" variant={groupId === 'all' ? 'secondary' : 'ghost'} onClick={() => setGroupId('all')}>全部组合</Button>
        {selectableGroups.map(g => <Button key={g.id} size="sm" variant={groupId === g.id ? 'secondary' : 'ghost'}
          onClick={() => setGroupId(g.id)} title={g.description || undefined}>{g.name}</Button>)}
      </div>
      <span className="ml-auto text-xs text-muted-foreground">{visible.length} 个看板</span>
      {/* 未启用/已关闭的可见看板显式提示数量: 不混进消费清单, 也不静默消失 */}
      {!loading && !error && !!unreadyCount && <p className="w-full text-xs text-muted-foreground">
        另有 {unreadyCount} 个可见看板未启用（设计中/已关闭），不进入消费清单；
        {canManage ? '可在「看板管理」中查看并启用。' : '可联系管理员启用后查看。'}
      </p>}
    </div>

    {loading ? <p role="status" className="text-sm text-muted-foreground">正在加载看板清单…</p>
      : error ? <div role="alert" className="flex flex-wrap items-center gap-3 rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm">
        {error}<Button size="sm" variant="outline" onClick={() => void load()}>重试</Button>
      </div>
        : !enabled.length ? <div className="rounded-xl border border-dashed py-24 text-center">
          <LayoutDashboard className="mx-auto mb-4 h-10 w-10 text-muted-foreground" />
          <p className="font-medium">还没有分配给你的看板</p>
          <p className="mt-2 text-sm text-muted-foreground">
            {canManage ? '在看板管理中创建看板、编排组合并分配角色后，这里就会出现。' : '当前角色没有被分配看板组合，可联系管理员为你的角色分配看板。'}
          </p>
          {canManage && <Button size="sm" className="mt-4" onClick={() => navigate('/system/dashboards')}>去配置仪表盘</Button>}
        </div>
          : <div className="columns-1 gap-5 sm:columns-2 xl:columns-3 2xl:columns-4">
            {visible.map(b => <article key={`${b.group_id}-${b.id}`}
              className="group mb-5 break-inside-avoid overflow-hidden rounded-xl border bg-card transition-shadow hover:shadow-md">
              <button type="button" aria-label={`打开看板 ${b.name}`} className="block w-full border-b bg-muted/20 p-3 text-left"
                onClick={() => open(b.id)}>
                <DashboardPreviewCanvas preview={b.preview} />
              </button>
              <div className="space-y-3 p-4">
                <div className="min-w-0">
                  <h2 className="truncate font-semibold" title={b.name}>{b.name}</h2>
                  <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{b.description || '暂无描述'}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge variant="secondary">{b.group_name}</Badge>
                  {!!b.is_default && <Badge variant="outline">默认</Badge>}
                  <span className="text-muted-foreground">{b.chart_count ?? 0} 个图表</span>
                  <span className="ml-auto text-muted-foreground">更新于 {b.updated_at ? new Date(b.updated_at).toLocaleString('zh-CN') : '—'}</span>
                </div>
                <div className="flex gap-2 border-t pt-3">
                  <Button size="sm" className="flex-1" onClick={() => open(b.id)}>进入看板</Button>
                  <Button size="sm" variant="outline" title="大屏播放" aria-label={`大屏播放 ${b.name}`} onClick={() => play(b.id, b.group_id)}>
                    <Play className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            </article>)}
          </div>}
  </div>;
}
