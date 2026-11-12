import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { toast } from '@/lib/toast';
import client from '@/api/client';
import { Plus, Search, Star, MoreHorizontal, Edit, Eye, Play, Copy, Settings, ArrowUp, ArrowDown, Trash2, LayoutDashboard, Users } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { TOGGLE_ACTIVE_CLASS } from '@/lib/toggleState';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator } from '@/components/ui/dropdown-menu';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useDashboardStore, DASHBOARD_STATUS_MAP, type Dashboard as DashboardModel, type DashboardParam, type PageParam, type DashboardStatus } from '@/stores/dashboardStore';
import { useCancelChartRefreshOnLeave } from '@/hooks/useCancelChartRefreshOnLeave';
import { useAuthStore } from '@/stores/authStore';
import { DashboardThumbnail } from '@/components/DashboardCanvas';
import { DashboardThumbnailFrame } from '@/components/DashboardThumbnailFrame';
import DashboardExportImport from '@/components/DashboardExportImport';
import DashboardGroupsAdmin from '@/components/DashboardGroupsAdmin';
import DashboardTemplates from '@/components/DashboardTemplates';
import DashboardParamsManager from '@/components/editor/DashboardParamsManager';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { useThemeStore } from '@/stores/themeStore';
import { applyScreenComponent, themePackExact } from '@/lib/dashboardDesign';

export default function Dashboard() {
  const store = useDashboardStore();
  const library = useVisLibrary();
  const theme = useThemeStore(s => s.theme);
  const navigate = useNavigate();
  const location = useLocation();
  const { workspaceId } = useParams();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  // 标签过滤（多选 OR：命中任一已选标签即显示）；只影响管理页清单，不改任何取数/权限口径
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [nameDialog, setNameDialog] = useState<{ id?: number; name: string } | null>(null);
  const [settingsId, setSettingsId] = useState<number | null>(null);
  const [visibleRolesId, setVisibleRolesId] = useState<number | null>(null);
  const [deleteId, setDeleteId] = useState<number | null>(null);
  const [templates, setTemplates] = useState(false);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  // 离开看板页即终止未完成的图表查询，避免连接被占导致其他功能一直 pending
  useCancelChartRefreshOnLeave();
  useEffect(() => { void store.loadDashboards(workspaceId ? Number(workspaceId) : 0); }, [workspaceId]);
  const filtered = useMemo(() => store.dashboards.filter(d =>
    `${d.name} ${d.description || ''}`.toLowerCase().includes(search.toLowerCase()) &&
    (status === 'all' || (d.status || 'designing') === status) && (!favoritesOnly || store.favorites.includes(d.id)) &&
    (selectedTags.length === 0 || selectedTags.some(t => (d.tags || []).includes(t)))
  ), [store.dashboards, search, status, favoritesOnly, store.favorites, selectedTags]);
  // 已用标签 = 当前工作空间全部看板 tags 的去重并集（业务可读名，无裸 id）
  const allTags = useMemo(() => {
    const set = new Set<string>();
    store.dashboards.forEach(d => (d.tags || []).forEach(t => set.add(t)));
    return [...set].sort((a, b) => a.localeCompare(b, 'zh'));
  }, [store.dashboards]);
  const current = store.dashboards.find(d => d.id === store.currentId) || null;
  const settings = store.dashboards.find(d => d.id === settingsId);
  const run = async (action: () => Promise<unknown>) => {
    if (lock.current) return;
    lock.current = true; setBusy(true);
    // 透出服务端错因(如「仅看板属主或管理员可修改」), 不把权限拒绝吞成笼统失败
    try { await action(); } catch (e: any) { toast.error(e?.response?.data?.detail || e?.message || '操作失败，请重试'); }
    finally { lock.current = false; setBusy(false); }
  };
  // 可写性由服务端裁决(editable): 角色授予的可见权不含编辑权, 无编辑权不渲染写入口
  const canEdit = (d: DashboardModel) => d.editable === true;
  // 可见角色配置是平台级授权面(接口仅 admin), 不随看板属主开放
  const isAdmin = useAuthStore(s => s.user?.role === 'admin');
  const edit = (id: number) => navigate(`/dashboard/editor/${id}`, { state: { from: location.pathname } });
  // 卡片上的「播放」= 单看板大屏展示; 看板管理页的「轮播」入口已按需求移除
  // (组轮播仍在工作空间「数据看板」的组合卡上, 进大屏后间隔由播放菜单即时选)
  const play = (id: number) => navigate(`/screen/${id}?group=0&from=${encodeURIComponent(location.pathname)}`, { state: { from: location.pathname } });
  const move = (id: number, delta: number) => run(async () => {
    const ordered = [...store.dashboards];
    const index = ordered.findIndex(d => d.id === id), next = index + delta;
    if (index < 0 || next < 0 || next >= ordered.length) return;
    [ordered[index], ordered[next]] = [ordered[next], ordered[index]];
    await store.reorderDashboards(ordered.map((d, sort_order) => ({ id: d.id, sort_order })));
  });
  return <div className="flex h-full min-h-0 flex-col bg-background">
    {/* 顶部（标题/操作 + Tab + 过滤栏 + 标签芯片）全部留在滚动口之外：翻列表时页头不再被卷走，也不再占大片空白。 */}
    <Tabs defaultValue="boards" className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b bg-background px-5 py-2.5 md:px-8 flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold leading-tight tracking-tight">仪表盘</h1>
          <p className="text-xs text-muted-foreground">用可复用字模设计看板，统一预览与大屏展示。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => setTemplates(true)}>从模板创建</Button>
          <DashboardExportImport dashboard={current} onImport={async data => { await store.createFromTemplate(data); }} />
          <Button size="sm" disabled={busy} onClick={() => setNameDialog({ name: '' })}><Plus className="mr-2 h-4 w-4" />新建看板</Button>
        </div>
      </header>
      <div className="shrink-0 px-5 md:px-8 pt-3">
        <TabsList>
          <TabsTrigger value="boards">看板列表</TabsTrigger>
          <TabsTrigger value="groups">看板组合</TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="boards" className="mt-0 flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 flex flex-wrap items-center gap-3 px-5 md:px-8 py-3">
        <div className="relative min-w-[200px] flex-1 max-w-md"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input aria-label="搜索看板" placeholder="搜索名称或描述…" className="pl-9" value={search} onChange={e => setSearch(e.target.value)} /></div>
        <select aria-label="状态筛选" className="h-10 rounded-md border bg-background px-3 text-sm" value={status} onChange={e => setStatus(e.target.value)}><option value="all">全部状态</option>{Object.entries(DASHBOARD_STATUS_MAP).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</select>
        {/* 标签筛选：独立下拉框（与状态框同规格、同处筛选栏）；选项 = 全部看板已用标签去重并集，单选命中即显示 */}
        <select aria-label="标签筛选" className="h-10 rounded-md border bg-background px-3 text-sm" disabled={allTags.length === 0}
          value={selectedTags[0] || 'all'} onChange={e => setSelectedTags(e.target.value === 'all' ? [] : [e.target.value])}>
          <option value="all">{allTags.length === 0 ? '全部标签（暂无）' : '全部标签'}</option>
          {allTags.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        {/* 选中态走 lib/toggleState 那一份口径：旧写法只把 variant 换成 secondary，而亮色主题下
            --secondary 近乎白底，落在卡片上看不出有没有选中。这里除底色/文字/字重/ring 外，
            再给 aria-pressed（语义）与实心星形（图标）两层线索，不靠单一色相传达状态。 */}
        <Button variant="ghost" aria-pressed={favoritesOnly} className={favoritesOnly ? TOGGLE_ACTIVE_CLASS : ''} onClick={() => setFavoritesOnly(v => !v)}><Star className={`mr-2 h-4 w-4 ${favoritesOnly ? 'fill-current' : ''}`} />仅收藏</Button>
        <span className="ml-auto text-xs text-muted-foreground">{filtered.length} 个看板</span>
      </div>
      {/* 仅此区滚动。relative 不是装饰：卡片内绝对定位后代若祖先链无定位元素，会以 ICB 为包含块
          越过裁剪把文档撑高，滚轮便把整个系统页面卷走；滚动口自身作包含块才会被裁在这里。 */}
      <div className="relative flex-1 min-h-0 overflow-auto px-5 md:px-8 pb-5 md:pb-8 space-y-4">
      {library.error && <div role="alert" className="text-sm text-muted-foreground">字模加载失败；已有快照仍可展示。<Button variant="link" onClick={library.refresh}>重试</Button></div>}
      {store.error && <div role="alert">{store.error}<Button variant="link" onClick={() => store.loadDashboards()}>重试</Button></div>}
      {store.loading && !store.dashboards.length ? <p>正在加载看板…</p> : filtered.length === 0 ?
        <div className="rounded-xl border border-dashed py-24 text-center"><LayoutDashboard className="mx-auto mb-4 h-10 w-10 text-muted-foreground" /><p className="font-medium">{store.dashboards.length ? '没有符合条件的看板' : '从第一个看板开始'}</p><p className="mt-2 text-sm text-muted-foreground">创建空白画布，在编辑器选择图表和字模。</p></div> :
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
          {filtered.map(db => <article key={db.id} className="group flex h-full flex-col overflow-hidden rounded-xl border bg-card transition-shadow hover:shadow-md">
            <button type="button" aria-label={`预览 ${db.name}`} className="block w-full border-b" onClick={() => navigate(`/system/dashboards/${db.id}`)}>
              {/* 预览槽固定高度（与工作区数据看板同一口径）：长画布的看板不得把卡片撑高一屏 */}
              <DashboardThumbnailFrame dashboard={db}><DashboardThumbnail dashboard={db} items={library.items} /></DashboardThumbnailFrame>
            </button>
            <div className="flex flex-1 flex-col gap-1.5 px-3 py-2">
              <div className="flex items-start gap-2"><div className="min-w-0 flex-1"><h2 className="truncate text-sm font-semibold" title={db.name}>{db.name}</h2><p className="mt-0.5 truncate text-xs text-muted-foreground">{db.description || '暂无描述'}</p></div>
                <button aria-label={`${store.favorites.includes(db.id) ? '取消收藏' : '收藏'} ${db.name}`} className="p-1" onClick={() => store.toggleFavorite(db.id)}><Star className={`h-4 w-4 ${store.favorites.includes(db.id) ? 'fill-current' : 'text-muted-foreground'}`} /></button>
                <DropdownMenu><DropdownMenuTrigger asChild><Button aria-label={`${db.name} 更多操作`} size="icon" variant="ghost" className="h-6 w-6"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger><DropdownMenuContent align="end">
                  {canEdit(db) && <DropdownMenuItem disabled={busy} onClick={() => setNameDialog({ id: db.id, name: db.name })}><Edit className="mr-2 h-4 w-4" />重命名</DropdownMenuItem>}
                  <DropdownMenuItem disabled={busy} onClick={() => run(() => store.copyDashboard(db.id))}><Copy className="mr-2 h-4 w-4" />复制为我的看板</DropdownMenuItem>
                  {canEdit(db) && <DropdownMenuItem disabled={busy} onClick={() => run(() => store.setDefault(db.id))}><Star className="mr-2 h-4 w-4" />设为默认</DropdownMenuItem>}
                  {canEdit(db) && <DropdownMenuItem onClick={() => setSettingsId(db.id)}><Settings className="mr-2 h-4 w-4" />状态与参数设置</DropdownMenuItem>}
                  {canEdit(db) && isAdmin && <DropdownMenuItem onClick={() => setVisibleRolesId(db.id)}><Users className="mr-2 h-4 w-4" />可见角色</DropdownMenuItem>}
                  {/* 固化必须用当前主题的**精确**包：主题包被禁用/下架时宁可置灰，
                      也不能把兜底的 tp_datafoundry 物化进看板（静默换皮） */}
                  {canEdit(db) && <DropdownMenuItem disabled={busy || !themePackExact(theme, library.items)} onClick={() => run(async () => {
                    const packComp = themePackExact(theme, library.items);
                    if (packComp) await store.updateDashboard(db.id, { filters: applyScreenComponent(db.filters, packComp) } as any);
                  })}><Star className="mr-2 h-4 w-4" />固化当前主题({theme})</DropdownMenuItem>}
                  <DropdownMenuItem onClick={() => store.setCurrent(db.id)}>选为导出对象</DropdownMenuItem>
                  {canEdit(db) && <DropdownMenuSeparator />}
                  {canEdit(db) && <DropdownMenuItem disabled={busy || store.dashboards[0]?.id === db.id} onClick={() => move(db.id, -1)}><ArrowUp className="mr-2 h-4 w-4" />上移</DropdownMenuItem>}
                  {canEdit(db) && <DropdownMenuItem disabled={busy || store.dashboards[store.dashboards.length - 1]?.id === db.id} onClick={() => move(db.id, 1)}><ArrowDown className="mr-2 h-4 w-4" />下移</DropdownMenuItem>}
                  {canEdit(db) && <DropdownMenuSeparator />}{canEdit(db) && <DropdownMenuItem disabled={busy} className="text-destructive" onClick={() => setDeleteId(db.id)}><Trash2 className="mr-2 h-4 w-4" />删除</DropdownMenuItem>}
                  {!canEdit(db) && <DropdownMenuItem disabled><Users className="mr-2 h-4 w-4" />仅查看（角色授予，不可编辑）</DropdownMenuItem>}
                </DropdownMenuContent></DropdownMenu>
              </div>
              {/* 徽标与更新时间合成一行（与 DashboardCatalogCards 的 ROW_CLS 同口径）：卡片不再上下偏厚 */}
              <div className="flex min-h-5 items-center gap-2 overflow-hidden text-xs"><Badge variant="secondary" className="shrink-0">{DASHBOARD_STATUS_MAP[db.status || 'designing']?.label || '设计中'}</Badge>{db.is_default && <Badge variant="outline" className="shrink-0">默认</Badge>}<span className="shrink-0 self-center text-muted-foreground">{db.charts.filter(c => !c.chart_type.startsWith('widget_')).length} 个图表</span><span aria-hidden className="shrink-0 text-muted-foreground/60">·</span><span className="min-w-0 truncate text-muted-foreground" title={`更新于 ${db.updated_at ? new Date(db.updated_at).toLocaleString('zh-CN') : '—'}`}>更新于 {db.updated_at ? new Date(db.updated_at).toLocaleString('zh-CN') : '—'}</span></div>
              {/* 标签行（有标签才渲染）：同类看板靠标签一眼区分；最多展示 4 个，余下 +N */}
              {db.tags && db.tags.length > 0 && (
                <div className="flex flex-wrap gap-1 text-[11px]">
                  {db.tags.slice(0, 4).map(t => <span key={t} title={`标签：${t}`} className="max-w-[110px] truncate rounded bg-muted px-1.5 py-0.5 text-muted-foreground">#{t}</span>)}
                  {db.tags.length > 4 && <span className="text-muted-foreground">+{db.tags.length - 4}</span>}
                </div>
              )}
              {/* 行几何与「数据看板」的共享卡片同一口径（见 DashboardCatalogCards）：
                  预览槽固定高 + 操作条 mt-auto 贴底，描述长短/标签有无不得把按钮顶来顶去 */}
              <div className="mt-auto flex gap-2 border-t pt-2">{canEdit(db) ? <Button size="sm" className="flex-1" onClick={() => edit(db.id)}><Edit className="mr-1 h-3.5 w-3.5" />编辑</Button> : <Button size="sm" variant="outline" className="flex-1" onClick={() => navigate(`/system/dashboards/${db.id}`)}><Eye className="mr-1 h-3.5 w-3.5" />查看</Button>}<Button size="sm" variant="outline" onClick={() => navigate(`/system/dashboards/${db.id}`)}><Eye className="mr-1 h-3.5 w-3.5" />预览</Button><Button size="sm" variant="ghost" disabled={db.status !== 'enabled'} title="仅已启用看板可播放" aria-label="播放" onClick={() => play(db.id)}><Play className="h-4 w-4" /></Button></div>
            </div>
          </article>)}
        </div>}
      </div>
        </TabsContent>
        <TabsContent value="groups" className="mt-0 min-h-0 flex-1 overflow-auto px-5 md:px-8 py-4">
          <DashboardGroupsAdmin />
        </TabsContent>
      </Tabs>
    <Dialog open={!!nameDialog} onOpenChange={open => { if (!open && !busy) setNameDialog(null); }}><DialogContent><DialogHeader><DialogTitle>{nameDialog?.id ? '重命名看板' : '新建看板'}</DialogTitle><DialogDescription>新看板默认使用 1920 × 1080 画布。</DialogDescription></DialogHeader><Input autoFocus aria-label="看板名称" placeholder="输入看板名称" value={nameDialog?.name || ''} onChange={e => setNameDialog(d => d && ({ ...d, name: e.target.value }))} /><DialogFooter><Button disabled={busy || !nameDialog?.name.trim()} onClick={() => run(async () => {
      if (!nameDialog) return;
      if (nameDialog.id) { await store.updateDashboard(nameDialog.id, { name: nameDialog.name.trim() }); setNameDialog(null); }
      else { const id = await store.createDashboard(nameDialog.name.trim(), workspaceId ? Number(workspaceId) : undefined); setNameDialog(null); edit(id); }
    })}>{busy ? '保存中…' : '确认'}</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={deleteId !== null} onOpenChange={open => { if (!open && !busy) setDeleteId(null); }}><DialogContent><DialogHeader><DialogTitle>删除看板？</DialogTitle><DialogDescription>看板及其中的图表将被删除，此操作无法撤销。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" disabled={busy} onClick={() => setDeleteId(null)}>取消</Button><Button variant="destructive" disabled={busy} onClick={() => run(async () => { if (deleteId !== null) await store.deleteDashboard(deleteId); setDeleteId(null); })}>确认删除</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={!!settings} onOpenChange={open => { if (!open) setSettingsId(null); }}><DialogContent className="max-w-2xl max-h-[90vh] overflow-auto"><DialogHeader><DialogTitle>状态与参数设置</DialogTitle></DialogHeader>{settings && <DashboardSettings key={settings.id} dashboard={settings} onClose={() => setSettingsId(null)} />}</DialogContent></Dialog>
    <Dialog open={visibleRolesId !== null} onOpenChange={open => { if (!open) setVisibleRolesId(null); }}><DialogContent className="max-w-lg max-h-[85vh] overflow-auto"><DialogHeader><DialogTitle>可见角色</DialogTitle><DialogDescription>勾选可查看该看板的角色；未勾选任何角色 = 除 admin 外无人可见（fail-closed）。看板内图表数据仍按查看者实时治理。</DialogDescription></DialogHeader>{visibleRolesId !== null && <DashboardVisibleRoles key={visibleRolesId} dashboardId={visibleRolesId} onClose={() => setVisibleRolesId(null)} />}</DialogContent></Dialog>
    <DashboardTemplates open={templates} onClose={() => setTemplates(false)} items={library.items} onApply={async template => { const id = await store.createFromTemplate(template); setTemplates(false); edit(id); }} />
  </div>;
}

/** 看板可见角色配置：勾选可查看该看板的角色（授权表双向同一份数据） */
function DashboardVisibleRoles({ dashboardId, onClose }: { dashboardId: number; onClose: () => void }) {
  const [roles, setRoles] = useState<Array<{ id: number; name: string; display_name: string }>>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const lock = useRef(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const [{ data: roleData }, { data: visData }] = await Promise.all([
          client.get('/roles/'),
          client.get(`/dashboard/${dashboardId}/visible-roles`),
        ]);
        if (!alive) return;
        setRoles(Array.isArray(roleData) ? roleData : []);
        setSelected(Array.isArray(visData) ? visData.map((r: any) => r.role_id) : []);
      } catch {
        if (alive) toast.error('加载可见角色失败，请重试');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [dashboardId]);

  const toggle = (roleId: number) => {
    setSelected(prev => prev.includes(roleId) ? prev.filter(id => id !== roleId) : [...prev, roleId]);
  };

  const save = async () => {
    if (lock.current) return;
    lock.current = true; setSaving(true);
    try {
      await client.put(`/dashboard/${dashboardId}/visible-roles`, { role_ids: selected });
      toast.success('可见角色已保存');
      onClose();
    } catch {
      toast.error('保存失败');
    } finally {
      lock.current = false; setSaving(false);
    }
  };

  return <div className="space-y-4">
    {loading ? <p className="text-sm text-muted-foreground">加载中…</p> : roles.length === 0 ? (
      <p className="text-sm text-muted-foreground text-center py-4">暂无角色</p>
    ) : (
      <div className="space-y-1 max-h-[320px] overflow-auto">
        {roles.map(role => (
          <div key={role.id} className="flex items-center justify-between border rounded px-3 py-2">
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-sm font-medium truncate">{role.display_name}</span>
              <code className="text-xs bg-muted px-1.5 py-0.5 rounded flex-shrink-0">{role.name}</code>
            </div>
            <Switch checked={selected.includes(role.id)} onCheckedChange={() => toggle(role.id)} />
          </div>
        ))}
      </div>
    )}
    <p className="text-xs text-muted-foreground">admin 角色始终可见全部看板，无需配置。</p>
    <Button disabled={loading || saving} onClick={save}>{saving ? '保存中…' : '保存'}</Button>
  </div>;
}

function DashboardSettings({ dashboard, onClose }: { dashboard: DashboardModel; onClose: () => void }) {
  const updateDashboard = useDashboardStore(s => s.updateDashboard);
  const dashboards = useDashboardStore(s => s.dashboards);
  const [status, setStatus] = useState<DashboardStatus>(dashboard.status || 'designing');
  const [params, setParams] = useState<DashboardParam[]>(() => structuredClone(dashboard.params || []));
  const [pageParams, setPageParams] = useState<PageParam[]>(() => structuredClone(dashboard.page_params || []));
  const [tags, setTags] = useState<string[]>(() => (dashboard.tags ? [...dashboard.tags] : []));
  const [tagDraft, setTagDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const lock = useRef(false);
  // 标签自由录入：trim + 去空 + 去重（与后端归一口径一致，避免前端写了后端又改掉的错位）
  const addTag = () => {
    const t = tagDraft.trim();
    if (!t) return;
    setTags(prev => prev.includes(t) ? prev : [...prev, t]);
    setTagDraft('');
  };
  // 已有标签（全部看板 tags 去重并集）：点选直接加入/移除，避免重复手输（选择用选择框，不靠手打）
  const existingTags = useMemo(() => {
    const set = new Set<string>();
    dashboards.forEach(d => (d.tags || []).forEach(t => set.add(t)));
    return [...set].sort((a, b) => a.localeCompare(b, 'zh'));
  }, [dashboards]);
  const toggleTag = (t: string) => setTags(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t]);
  const save = async () => {
    if (lock.current) return;
    const valid = (items: { name: string }[]) => items.every(p => /^[A-Za-z_][\w]*$/.test(p.name)) && new Set(items.map(p => p.name)).size === items.length;
    if (!valid(params) || !valid(pageParams)) { toast.error('参数名须为唯一的英文字母、数字或下划线，且不能以数字开头'); return; }
    lock.current = true; setSaving(true);
    // 轮播间隔设置项随看板管理页的轮播入口一并移除：该值已无消费方（大屏页的间隔在播放菜单里即时选），
    // 留着就是“能填但不起作用”的死输入口；carousel_interval 字段本身仍在库/接口里，不写即保持原值。
    try { await updateDashboard(dashboard.id, { status, params, page_params: pageParams, tags }); toast.success('设置已保存'); onClose(); }
    catch { toast.error('保存失败，设置已保留'); } finally { lock.current = false; setSaving(false); }
  };
  return <div className="space-y-5"><p className="text-xs text-muted-foreground">设计中看板可管理预览，仅已启用看板允许播放。取消不会写入设置。</p>
    <div className="flex flex-wrap gap-2">{Object.entries(DASHBOARD_STATUS_MAP).map(([key, value]) => <Button key={key} variant={status === key ? 'default' : 'outline'} disabled={saving} onClick={() => setStatus(key as DashboardStatus)}>{value.label}</Button>)}</div>
    {/* 标签：自由文本分类，保存后进管理页「已用标签」芯片可过滤（不参与取数/权限） */}
    <div className="space-y-2">
      <Label className="text-sm font-medium">标签</Label>
      <div className="flex gap-2">
        <Input aria-label="新标签" placeholder="输入标签后回车添加，如：经营 / 周报" value={tagDraft}
          onChange={e => setTagDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addTag(); } }} />
        <Button type="button" variant="outline" disabled={saving || !tagDraft.trim()} onClick={addTag}>添加</Button>
      </div>
      {/* 已有标签：点选即加入（已在当前看板的显选中态，再点取消）；新标签用上方输入框 */}
      {existingTags.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground shrink-0">已有标签：</span>
          {existingTags.map(t => {
            const on = tags.includes(t);
            return (
              <button key={t} type="button" aria-pressed={on} disabled={saving} onClick={() => toggleTag(t)}
                title={on ? `从本看板移除「${t}」` : `添加标签「${t}」`}
                className={`rounded-full border px-2 py-0.5 text-xs transition-colors ${on ? TOGGLE_ACTIVE_CLASS : 'hover:bg-muted'}`}>
                #{t}
              </button>
            );
          })}
        </div>
      )}
      {tags.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {tags.map(t => (
            <span key={t} className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs">
              #{t}
              <button type="button" aria-label={`移除标签 ${t}`} disabled={saving} className="text-muted-foreground hover:text-foreground" onClick={() => setTags(prev => prev.filter(x => x !== t))}>×</button>
            </span>
          ))}
        </div>
      ) : <p className="text-xs text-muted-foreground">暂无标签。打上标签后，同类看板在列表页靠标签一眼区分。</p>}
    </div>
    <DashboardParamsManager params={params} pageParams={pageParams} charts={dashboard.charts || []}
      onChange={next => { setParams(next.params); setPageParams(next.page_params); }} />
    <Button disabled={saving} onClick={save}>{saving ? '保存中…' : '保存设置'}</Button>
  </div>;
}
