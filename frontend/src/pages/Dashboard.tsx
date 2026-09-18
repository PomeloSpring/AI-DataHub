import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import { Plus, Search, Star, MoreHorizontal, Edit, Eye, Play, Copy, Settings, ArrowUp, ArrowDown, Trash2, LayoutDashboard, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, DropdownMenuSeparator } from '@/components/ui/dropdown-menu';
import { useDashboardStore, DASHBOARD_STATUS_MAP, type Dashboard as DashboardModel, type DashboardParam, type PageParam, type DashboardStatus } from '@/stores/dashboardStore';
import { DashboardThumbnail } from '@/components/DashboardCanvas';
import DashboardExportImport from '@/components/DashboardExportImport';
import DashboardTemplates from '@/components/DashboardTemplates';
import CarouselView from '@/components/CarouselView';
import { useVisLibrary } from '@/hooks/useVisLibrary';

export default function Dashboard() {
  const store = useDashboardStore();
  const library = useVisLibrary();
  const navigate = useNavigate();
  const location = useLocation();
  const { workspaceId } = useParams();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [nameDialog, setNameDialog] = useState<{ id?: number; name: string } | null>(null);
  const [settingsId, setSettingsId] = useState<number | null>(null);
  const [deleteId, setDeleteId] = useState<number | null>(null);
  const [templates, setTemplates] = useState(false);
  const [carousel, setCarousel] = useState(false);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  useEffect(() => { void store.loadDashboards(workspaceId ? Number(workspaceId) : undefined); }, [workspaceId]);
  const filtered = useMemo(() => store.dashboards.filter(d =>
    `${d.name} ${d.description || ''}`.toLowerCase().includes(search.toLowerCase()) &&
    (status === 'all' || (d.status || 'designing') === status) && (!favoritesOnly || store.favorites.includes(d.id))
  ), [store.dashboards, search, status, favoritesOnly, store.favorites]);
  const enabled = store.dashboards.filter(d => d.status === 'enabled');
  const current = store.dashboards.find(d => d.id === store.currentId) || null;
  const settings = store.dashboards.find(d => d.id === settingsId);
  const run = async (action: () => Promise<unknown>) => {
    if (lock.current) return;
    lock.current = true; setBusy(true);
    try { await action(); } catch { toast.error('操作失败，请重试'); }
    finally { lock.current = false; setBusy(false); }
  };
  const edit = (id: number) => navigate(`/dashboard/editor/${id}`, { state: { from: location.pathname } });
  const move = (id: number, delta: number) => run(async () => {
    const ordered = [...store.dashboards];
    const index = ordered.findIndex(d => d.id === id), next = index + delta;
    if (index < 0 || next < 0 || next >= ordered.length) return;
    [ordered[index], ordered[next]] = [ordered[next], ordered[index]];
    await store.reorderDashboards(ordered.map((d, sort_order) => ({ id: d.id, sort_order })));
  });
  return <div className="h-full overflow-auto bg-background">
    <div className="mx-auto max-w-[1800px] p-5 md:p-8 space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div><div className="mb-2 text-xs tracking-[0.2em] text-muted-foreground">DATAFOUNDRY / WORKSPACE</div>
          <h1 className="text-2xl font-semibold tracking-tight">仪表盘</h1><p className="mt-1 text-sm text-muted-foreground">用可复用字模设计看板，统一预览与大屏展示。</p></div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => setTemplates(true)}>从模板创建</Button>
          <Button variant="outline" disabled={!enabled.length} onClick={() => setCarousel(true)}><Play className="mr-2 h-4 w-4" />轮播</Button>
          <DashboardExportImport dashboard={current} onImport={async data => { await store.createFromTemplate(data); }} />
          <Button disabled={busy} onClick={() => setNameDialog({ name: '' })}><Plus className="mr-2 h-4 w-4" />新建看板</Button>
        </div>
      </header>
      <div className="flex flex-wrap items-center gap-3 border-y py-4">
        <div className="relative min-w-[200px] flex-1 max-w-md"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input aria-label="搜索看板" placeholder="搜索名称或描述…" className="pl-9" value={search} onChange={e => setSearch(e.target.value)} /></div>
        <select aria-label="状态筛选" className="h-10 rounded-md border bg-background px-3 text-sm" value={status} onChange={e => setStatus(e.target.value)}><option value="all">全部状态</option>{Object.entries(DASHBOARD_STATUS_MAP).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</select>
        <Button variant={favoritesOnly ? 'secondary' : 'ghost'} onClick={() => setFavoritesOnly(v => !v)}><Star className="mr-2 h-4 w-4" />仅收藏</Button>
        <span className="ml-auto text-xs text-muted-foreground">{filtered.length} 个看板</span>
      </div>
      {library.error && <div role="alert" className="text-sm text-muted-foreground">字模加载失败；已有快照仍可展示。<Button variant="link" onClick={library.refresh}>重试</Button></div>}
      {store.error && <div role="alert">{store.error}<Button variant="link" onClick={() => store.loadDashboards()}>重试</Button></div>}
      {store.loading && !store.dashboards.length ? <p>正在加载看板…</p> : filtered.length === 0 ?
        <div className="rounded-xl border border-dashed py-24 text-center"><LayoutDashboard className="mx-auto mb-4 h-10 w-10 text-muted-foreground" /><p className="font-medium">{store.dashboards.length ? '没有符合条件的看板' : '从第一个看板开始'}</p><p className="mt-2 text-sm text-muted-foreground">创建空白画布，在编辑器选择图表和字模。</p></div> :
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {filtered.map(db => <article key={db.id} className="group overflow-hidden rounded-xl border bg-card transition-shadow hover:shadow-md">
            <button type="button" aria-label={`预览 ${db.name}`} className="block w-full border-b p-3 bg-muted/20" onClick={() => navigate(`/system/dashboards/${db.id}`)}><DashboardThumbnail dashboard={db} items={library.items} /></button>
            <div className="space-y-4 p-4">
              <div className="flex items-start gap-2"><div className="min-w-0 flex-1"><h2 className="truncate font-semibold" title={db.name}>{db.name}</h2><p className="mt-1 truncate text-xs text-muted-foreground">{db.description || '暂无描述'}</p></div>
                <button aria-label={`${store.favorites.includes(db.id) ? '取消收藏' : '收藏'} ${db.name}`} className="p-1" onClick={() => store.toggleFavorite(db.id)}><Star className={`h-4 w-4 ${store.favorites.includes(db.id) ? 'fill-current' : 'text-muted-foreground'}`} /></button>
                <DropdownMenu><DropdownMenuTrigger asChild><Button aria-label={`${db.name} 更多操作`} size="icon" variant="ghost" className="h-6 w-6"><MoreHorizontal className="h-4 w-4" /></Button></DropdownMenuTrigger><DropdownMenuContent align="end">
                  <DropdownMenuItem disabled={busy} onClick={() => setNameDialog({ id: db.id, name: db.name })}><Edit className="mr-2 h-4 w-4" />重命名</DropdownMenuItem>
                  <DropdownMenuItem disabled={busy} onClick={() => run(() => store.copyDashboard(db.id))}><Copy className="mr-2 h-4 w-4" />复制</DropdownMenuItem>
                  <DropdownMenuItem disabled={busy} onClick={() => run(() => store.setDefault(db.id))}><Star className="mr-2 h-4 w-4" />设为默认</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setSettingsId(db.id)}><Settings className="mr-2 h-4 w-4" />状态与参数设置</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => store.setCurrent(db.id)}>选为导出对象</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem disabled={busy || store.dashboards[0]?.id === db.id} onClick={() => move(db.id, -1)}><ArrowUp className="mr-2 h-4 w-4" />上移</DropdownMenuItem>
                  <DropdownMenuItem disabled={busy || store.dashboards[store.dashboards.length - 1]?.id === db.id} onClick={() => move(db.id, 1)}><ArrowDown className="mr-2 h-4 w-4" />下移</DropdownMenuItem>
                  <DropdownMenuSeparator /><DropdownMenuItem disabled={busy} className="text-destructive" onClick={() => setDeleteId(db.id)}><Trash2 className="mr-2 h-4 w-4" />删除</DropdownMenuItem>
                </DropdownMenuContent></DropdownMenu>
              </div>
              <div className="flex flex-wrap gap-2 text-xs"><Badge variant="secondary">{DASHBOARD_STATUS_MAP[db.status || 'designing']?.label || '设计中'}</Badge>{db.is_default && <Badge variant="outline">默认</Badge>}<span className="self-center text-muted-foreground">{db.charts.filter(c => !c.chart_type.startsWith('widget_')).length} 个图表</span></div>
              <p className="text-xs text-muted-foreground">更新于 {db.updated_at ? new Date(db.updated_at).toLocaleString('zh-CN') : '—'}</p>
              <div className="flex gap-2 border-t pt-3"><Button size="sm" className="flex-1" onClick={() => edit(db.id)}><Edit className="mr-1 h-3.5 w-3.5" />编辑</Button><Button size="sm" variant="outline" onClick={() => navigate(`/system/dashboards/${db.id}`)}><Eye className="mr-1 h-3.5 w-3.5" />预览</Button><Button size="sm" variant="ghost" disabled={db.status !== 'enabled'} title="仅已启用看板可播放" onClick={() => navigate(`/screen/${db.id}`)}><Play className="h-4 w-4" /><span className="sr-only">播放</span></Button></div>
            </div>
          </article>)}
        </div>}
    </div>
    <Dialog open={!!nameDialog} onOpenChange={open => { if (!open && !busy) setNameDialog(null); }}><DialogContent><DialogHeader><DialogTitle>{nameDialog?.id ? '重命名看板' : '新建看板'}</DialogTitle><DialogDescription>新看板默认使用 1920 × 1080 画布。</DialogDescription></DialogHeader><Input autoFocus aria-label="看板名称" placeholder="输入看板名称" value={nameDialog?.name || ''} onChange={e => setNameDialog(d => d && ({ ...d, name: e.target.value }))} /><DialogFooter><Button disabled={busy || !nameDialog?.name.trim()} onClick={() => run(async () => {
      if (!nameDialog) return;
      if (nameDialog.id) { await store.updateDashboard(nameDialog.id, { name: nameDialog.name.trim() }); setNameDialog(null); }
      else { const id = await store.createDashboard(nameDialog.name.trim(), workspaceId ? Number(workspaceId) : undefined); setNameDialog(null); edit(id); }
    })}>{busy ? '保存中…' : '确认'}</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={deleteId !== null} onOpenChange={open => { if (!open && !busy) setDeleteId(null); }}><DialogContent><DialogHeader><DialogTitle>删除看板？</DialogTitle><DialogDescription>看板及其中的图表将被删除，此操作无法撤销。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" disabled={busy} onClick={() => setDeleteId(null)}>取消</Button><Button variant="destructive" disabled={busy} onClick={() => run(async () => { if (deleteId !== null) await store.deleteDashboard(deleteId); setDeleteId(null); })}>确认删除</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={!!settings} onOpenChange={open => { if (!open) setSettingsId(null); }}><DialogContent className="max-w-2xl max-h-[90vh] overflow-auto"><DialogHeader><DialogTitle>状态与参数设置</DialogTitle></DialogHeader>{settings && <DashboardSettings key={settings.id} dashboard={settings} onClose={() => setSettingsId(null)} />}</DialogContent></Dialog>
    <DashboardTemplates open={templates} onClose={() => setTemplates(false)} onApply={async template => { const id = await store.createFromTemplate(template); setTemplates(false); edit(id); }} />
    {carousel && <CarouselView dashboards={enabled} interval={current?.carousel_interval || 10} startIndex={Math.max(0, enabled.findIndex(d => d.id === current?.id))} onClose={() => setCarousel(false)} />}
  </div>;
}

function DashboardSettings({ dashboard, onClose }: { dashboard: DashboardModel; onClose: () => void }) {
  const updateDashboard = useDashboardStore(s => s.updateDashboard);
  const [status, setStatus] = useState<DashboardStatus>(dashboard.status || 'designing');
  const [interval, setInterval] = useState(dashboard.carousel_interval || 10);
  const [params, setParams] = useState<DashboardParam[]>(() => structuredClone(dashboard.params || []));
  const [pageParams, setPageParams] = useState<PageParam[]>(() => structuredClone(dashboard.page_params || []));
  const [saving, setSaving] = useState(false);
  const lock = useRef(false);
  const save = async () => {
    if (lock.current) return;
    const valid = (items: { name: string }[]) => items.every(p => /^[A-Za-z_][\w]*$/.test(p.name)) && new Set(items.map(p => p.name)).size === items.length;
    if (!valid(params) || !valid(pageParams)) { toast.error('参数名须为唯一的英文字母、数字或下划线，且不能以数字开头'); return; }
    lock.current = true; setSaving(true);
    try { await updateDashboard(dashboard.id, { status, params, page_params: pageParams, carousel_interval: interval }); toast.success('设置已保存'); onClose(); }
    catch { toast.error('保存失败，设置已保留'); } finally { lock.current = false; setSaving(false); }
  };
  return <div className="space-y-5"><p className="text-xs text-muted-foreground">设计中看板可管理预览，仅已启用看板允许播放。取消不会写入设置。</p>
    <div className="flex flex-wrap gap-2">{Object.entries(DASHBOARD_STATUS_MAP).map(([key, value]) => <Button key={key} variant={status === key ? 'default' : 'outline'} disabled={saving} onClick={() => setStatus(key as DashboardStatus)}>{value.label}</Button>)}</div>
    <label className="block text-sm">轮播间隔（秒）<Input type="number" min={3} max={3600} value={interval} onChange={e => setInterval(Math.max(3, Math.min(3600, Number(e.target.value) || 10)))} /></label>
    {[false, true].map(page => {
      const items = page ? pageParams : params;
      const setItems = (next: any[]) => page ? setPageParams(next) : setParams(next);
      return <section key={String(page)} className="space-y-2"><h3 className="text-sm font-medium">{page ? '页面参数' : '查询参数'}</h3>{items.map((p, i) => {
        const update = (key: string, value: unknown) => setItems(items.map((v, j) => j === i ? { ...v, [key]: value } : v));
        return <div key={i} className="flex items-start gap-2 rounded border p-3"><div className="grid flex-1 grid-cols-2 gap-2"><Input aria-label="参数名" placeholder="参数名" value={p.name} onChange={e => update('name', e.target.value)} /><Input aria-label="参数标签" placeholder="显示标签" value={p.label} onChange={e => update('label', e.target.value)} /><select className="rounded border bg-background text-sm" value={p.type} onChange={e => update('type', e.target.value)}>{(page ? ['string', 'number', 'date', 'date_range'] : ['text', 'number', 'date', 'select']).map(t => <option key={t} value={t}>{t}</option>)}</select><Input aria-label="默认值" placeholder="默认值" value={p.default || ''} onChange={e => update('default', e.target.value)} />{!page && p.type === 'select' && <Input className="col-span-2" placeholder="选项，逗号分隔" value={((p as DashboardParam).options || []).join(',')} onChange={e => update('options', e.target.value.split(',').map(v => v.trim()).filter(Boolean))} />}</div><Button variant="ghost" size="icon" aria-label="移除参数" onClick={() => setItems(items.filter((_, j) => j !== i))}><X className="h-4 w-4" /></Button></div>;
      })}<Button variant="outline" size="sm" onClick={() => setItems([...items, { name: '', label: '', type: page ? 'string' : 'text', default: '' }])}>添加{page ? '页面' : '查询'}参数</Button></section>;
    })}
    <Button disabled={saving} onClick={save}>{saving ? '保存中…' : '保存设置'}</Button>
  </div>;
}
