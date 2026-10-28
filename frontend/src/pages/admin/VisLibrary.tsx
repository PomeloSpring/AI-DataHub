import { useState, useRef, useEffect, useMemo } from 'react';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { useClientPagination } from '@/hooks/useClientPagination';
import { VIS_CATEGORIES, type VisComponent } from '@/api/visLibrary';
import Preview, { ShapePreview, typeLabelsOf } from '@/components/VisComponentPreview';
import { CHART_TYPES, CHART_TYPE_CATEGORIES } from '@/components/DashboardChart';
import { sanitizePack } from '@/lib/dashboardDesign';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from '@/lib/toast';
import { Plus, Edit, Trash2, RefreshCw, Lock, LayoutTemplate, Palette, Image, Gauge, Grid3x3, Frame, BarChart3, Layers, FileText, Map, Search, TextCursorInput, type LucideIcon } from 'lucide-react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import client from '@/api/client';
import { useAuthStore } from '@/stores/authStore';

const ICONS: Record<string, LucideIcon> = { theme_pack: Layers, chart_style: BarChart3, widget_style: TextCursorInput, screen_background: Image, kpi_card: Gauge,
  layout_template: Grid3x3, decoration_frame: Frame, color_theme: Palette, report_template: FileText, map_tile: Map };
const CATEGORIES = VIS_CATEGORIES.map(c => ({ ...c, icon: ICONS[c.id] }));
// 分类切换条选项（含「全部」，无图标）
const CATEGORY_TABS: Array<{ id: string; label: string; icon?: LucideIcon }> = [{ id: 'all', label: '全部' }, ...CATEGORIES];
// 适用类型显示业务名：typeLabelsOf 与预览组件同源（ui-resource-display.md）
// 预览框统一几何：每张卡片的预览区等高，长短不一就是列表页“乱”的直接原因
const PREVIEW_SLOT = 'flex h-24 shrink-0 items-center justify-center overflow-hidden border-b bg-muted/20 p-2';

export default function VisLibrary() {
  const isAdmin = useAuthStore(s => s.user?.role) === 'admin';
  const { items, loading, error, refresh: load } = useVisLibrary(true);
  const [catFilter, setCatFilter] = useState<string>('theme_pack');
  const [search, setSearch] = useState('');
  const [viewPack, setViewPack] = useState<VisComponent | null>(null);
  const [previewTile, setPreviewTile] = useState<VisComponent | null>(null);
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const packStyle = viewPack ? sanitizePack(viewPack.style_config) : {};

  const [formOpen, setFormOpen] = useState(false);
  const [edit, setEdit] = useState<VisComponent | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<VisComponent | null>(null);
  const [saving, setSaving] = useState(false);

  // form
  const [name, setName] = useState('');
  const [category, setCategory] = useState('chart_style');
  const [chartTypes, setChartTypes] = useState<string[]>([]);
  const [styleConfig, setStyleConfig] = useState('{}');
  const [reportContent, setReportContent] = useState('');
  const [reportFormat, setReportFormat] = useState<'markdown' | 'html'>('markdown');
  const [mapTileUrl, setMapTileUrl] = useState('');
  const [mapTileProvider, setMapTileProvider] = useState('');
  const [mapTileApiKey, setMapTileApiKey] = useState('');
  const [mapTileDefault, setMapTileDefault] = useState(false);
  const [description, setDescription] = useState('');
  const [asBuiltin, setAsBuiltin] = useState(false);

  const openCreate = () => {
    setEdit(null); setName(''); setCategory('chart_style'); setChartTypes([]);
    setStyleConfig(JSON.stringify({ config: { colorScheme: ['#22d3ee', '#3b82f6'] } }, null, 2));
    setReportContent(''); setReportFormat('markdown');
    setMapTileUrl(''); setMapTileProvider(''); setMapTileApiKey(''); setMapTileDefault(false);
    setDescription(''); setAsBuiltin(false); setFormOpen(true);
  };
  const openEdit = (c: VisComponent) => {
    setEdit(c); setName(c.name); setCategory(c.category);
    setChartTypes((c.chart_type || '').split(',').map(s => s.trim()).filter(Boolean));
    setStyleConfig(JSON.stringify(c.style_config || {}, null, 2));
    setReportContent((c.style_config as any)?.content || '');
    setReportFormat((c.style_config as any)?.format || 'markdown');
    setMapTileUrl((c.style_config as any)?.tileUrl || '');
    setMapTileProvider((c.style_config as any)?.provider || '');
    setMapTileApiKey((c.style_config as any)?.apiKey || '');
    setMapTileDefault(!!(c.style_config as any)?.is_default);
    setDescription(c.description || ''); setAsBuiltin(!!c.is_builtin); setFormOpen(true);
  };

  const save = async () => {
    let sc: any;
    try { sc = JSON.parse(styleConfig || '{}'); } catch { toast.error('样式配置不是合法 JSON'); return; }
    // 报告模板: content/format 存入 style_config
    if (category === 'report_template') {
      sc = { content: reportContent, format: reportFormat };
    }
    // 地图瓦片: tileUrl/provider/apiKey/is_default 存入 style_config
    if (category === 'map_tile') {
      sc = { tileUrl: mapTileUrl, provider: mapTileProvider, apiKey: mapTileApiKey, ...(mapTileDefault ? { is_default: true } : {}) };
    }
    setSaving(true);
    try {
      const payload = {
        name, category, chart_type: chartTypes.join(',') || null, style_config: sc,
        description, is_builtin: asBuiltin && isAdmin,
      };
      if (edit) await client.put(`/vis-library/components/${edit.id}`, payload);
      else await client.post('/vis-library/components', payload);
      toast.success(edit ? '字模已更新' : '字模已保存');
      setFormOpen(false); load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    } finally { setSaving(false); }
  };

  const remove = async (c: VisComponent) => {
    try {
      await client.delete(`/vis-library/components/${c.id}`);
      toast.success('字模已下架'); setDeleteTarget(null); load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '删除失败');
    }
  };

  // 初始化 Leaflet 地图预览
  useEffect(() => {
    if (!previewTile || !mapContainerRef.current) return;
    // 清理旧地图
    if (mapRef.current) { mapRef.current.remove(); mapRef.current = null; }
    const sc2 = previewTile.style_config as any;
    const tileUrl = sc2?.tileUrl || '';
    const apiKey = sc2?.apiKey || '';
    const url = tileUrl.replace('{apiKey}', apiKey);
    const map = L.map(mapContainerRef.current, {
      center: [39.9042, 116.4074], // 北京
      zoom: 10,
      zoomControl: true,
      attributionControl: false,
    });
    L.tileLayer(url, {
      maxZoom: 18,
      minZoom: 3,
    }).addTo(map);
    mapRef.current = map;
    // 弹窗打开后需要 invalidateSize 确保地图正确渲染
    setTimeout(() => map.invalidateSize(), 100);
    return () => { if (mapRef.current) { mapRef.current.remove(); mapRef.current = null; } };
  }, [previewTile]);

  const shown = useMemo(() => {
    const byCat = catFilter === 'all' ? items : items.filter(i => i.category === catFilter);
    const q = search.trim().toLowerCase();
    return q ? byCat.filter(i => `${i.name} ${i.code} ${i.description || ''}`.toLowerCase().includes(q)) : byCat;
  }, [items, catFilter, search]);
  // 字模清单客户端分页（全站统一默认每页 20 条，候选 20/50/100）
  const paged = useClientPagination(shown, DEFAULT_PAGE_SIZE);
  const canEdit = (c: VisComponent) => isAdmin || !c.is_builtin;

  return (
    <div className="mx-auto max-w-[1800px] space-y-5 p-5 md:p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="mb-2 text-xs tracking-[0.2em] text-muted-foreground">DATAFOUNDRY / VISUAL LIBRARY</div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <LayoutTemplate className="h-6 w-6 text-primary" />UI 字模库
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">大屏可复用的“活字”：主题包 / 图表样式 / 控件样式 / 背景 / KPI 卡 / 布局 / 装饰 / 配色 / 地图瓦片。系统内置仅管理员可改，自定义可回存沉淀。</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={load}><RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />刷新</Button>
          <Button onClick={openCreate}><Plus className="h-4 w-4 mr-2" />新建字模</Button>
        </div>
      </header>

      {/* 分类切换 = 分段控件（与看板切换条同口径）；搜索与计数同一排，不再靠一排 outline 按钮堆 */}
      <div className="flex flex-wrap items-center gap-3 border-y py-3">
        <nav aria-label="字模分类" className="flex max-w-full items-center gap-1.5 overflow-x-auto rounded-full bg-muted/60 p-1.5 ring-1 ring-border/60 [scrollbar-width:none]">
          {CATEGORY_TABS.map(tab => {
            const on = catFilter === tab.id;
            const Icon = tab.icon;
            return <button key={tab.id} type="button" aria-pressed={on} onClick={() => setCatFilter(tab.id)}
              className={`shrink-0 whitespace-nowrap rounded-full px-4 py-1.5 text-[13px] transition-colors ${on ? 'bg-background font-medium text-primary shadow-sm ring-1 ring-primary/25' : 'text-muted-foreground hover:bg-background/70 hover:text-foreground'}`}>
              {!!Icon && <Icon className="mr-1.5 inline h-3.5 w-3.5 align-[-2px]" />}{tab.label}
            </button>;
          })}
        </nav>
        <div className="relative min-w-[200px] max-w-xs flex-1">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input aria-label="搜索字模" placeholder="搜索名称或说明…" className="h-9 pl-9 text-sm" value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <span className="ml-auto text-xs text-muted-foreground">{shown.length} 个字模</span>
      </div>

      {error && <div role="alert" className="flex flex-wrap items-center gap-3 rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm">{error}<Button size="sm" variant="outline" onClick={load}>重试</Button></div>}
      {loading && !items.length && <p role="status" className="text-sm text-muted-foreground">正在加载字模…</p>}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {paged.slice.map(c => (
          <article key={c.id} className={`flex h-full flex-col overflow-hidden rounded-xl border bg-card transition-shadow hover:shadow-md ${c.is_active ? '' : 'opacity-60'}`}>
            <div className={PREVIEW_SLOT}><Preview c={c} /></div>
            <div className="flex-1 space-y-2 p-3">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <h2 className="flex items-center gap-1.5 truncate font-semibold" title={c.name}>
                    {c.name}{!!c.is_builtin && <Lock className="h-3 w-3 shrink-0 text-muted-foreground" />}
                  </h2>
                  <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{c.description || '暂无说明'}</p>
                </div>
                <Badge variant={c.source === 'system' ? 'default' : 'secondary'} className="shrink-0">{c.source === 'system' ? '内置' : '自定义'}</Badge>
              </div>
              <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                {/* 适用类型显示业务名；code 是内部标识，只作次要信息供排查 */}
                <span className="rounded-md bg-muted/60 px-1.5 py-0.5">{typeLabelsOf(c.chart_type)}</span>
                {!c.is_active && <span className="rounded-md bg-destructive/10 px-1.5 py-0.5 text-destructive">已下架</span>}
                <span className="ml-auto font-mono text-[10px]" title="字模代码（内部标识）">{c.code}</span>
              </div>
            </div>
            <div className="flex items-center gap-2 border-t p-3">
              {c.category === 'theme_pack' && <Button variant="outline" size="sm" onClick={() => setViewPack(c)}>浏览整套</Button>}
              {c.category === 'map_tile' && <Button variant="outline" size="sm" onClick={() => setPreviewTile(c)}>预览瓦片</Button>}
              <Button variant="outline" size="sm" disabled={!canEdit(c)} onClick={() => openEdit(c)}>
                <Edit className="h-3.5 w-3.5 mr-1" />{canEdit(c) ? '编辑' : '只读'}
              </Button>
              <Button variant="ghost" size="sm" disabled={!canEdit(c)} aria-label={`下架字模 ${c.name}`} onClick={() => setDeleteTarget(c)} className="ml-auto text-destructive">
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          </article>
        ))}
        {shown.length === 0 && !loading && !error && (
          <div className="col-span-full rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
            {search ? `没有匹配「${search}」的字模` : '该分类暂无字模，可点右上「新建字模」沉淀一套'}
          </div>
        )}
      </div>

      <PaginationBar
        page={paged.page}
        pageSize={paged.pageSize}
        total={paged.total}
        busy={loading}
        onChange={(p: number) => paged.setPage(p)}
        onPageSizeChange={paged.setPageSize}
        hideWhenSingle
        className="-mx-5 -mb-5 px-5 md:-mx-8 md:-mb-8 md:px-8"
      />

      <Dialog open={!!viewPack} onOpenChange={() => setViewPack(null)}>
        <DialogContent className="max-w-4xl max-h-[85vh] overflow-y-auto">
          <DialogHeader><DialogTitle>{viewPack?.name} · 整套 UI 字模</DialogTitle><DialogDescription>按同一主题展示全部图表与参数控件的形状示意；示例不读取业务数据。</DialogDescription></DialogHeader>
          <div className="space-y-5 rounded-lg p-4" style={{ backgroundColor: packStyle.background?.backgroundColor, backgroundImage: packStyle.background?.backgroundImage, backgroundSize: packStyle.background?.backgroundSize }}>
            {CHART_TYPE_CATEGORIES.map(group => <section key={group.key}>
              <h3 className="mb-2 text-sm font-medium" style={{ color: packStyle.mode === 'dark' ? '#e2e8f0' : '#334155' }}>{group.label}</h3>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">{CHART_TYPES.filter(t => t.category === group.key).map(t => {
                const styles = t.category === 'widget' ? packStyle.widgets : packStyle.charts;
                const cfg = { ...styles?.default, ...styles?.[t.value] };
                return <div key={t.value} className="rounded-lg border p-2" style={{ background: packStyle.card?.cardBg, border: packStyle.card?.cardBorder, borderRadius: packStyle.card?.cardRadius }}>
                  <ShapePreview type={t.value} palette={cfg.colorScheme || packStyle.palette || []} cfg={cfg} />
                  <p className="mt-2 text-center text-xs" style={{ color: packStyle.mode === 'dark' ? '#cbd5e1' : '#475569' }}>{t.label}</p>
                </div>;
              })}</div>
            </section>)}
          </div>
        </DialogContent>
      </Dialog>
      {/* 编辑弹窗 */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-[560px] max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{edit ? '编辑字模' : '新建字模'}</DialogTitle>
            <DialogDescription>样式配置以 JSON 描述,前端按类别解释(背景/卡片/图表样式/控件样式/配色/布局/装饰)。</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>名称</Label>
                <Input value={name} onChange={e => setName(e.target.value)} placeholder="如:霓虹渐变柱" />
              </div>
              <div className="space-y-1.5">
                <Label>类别</Label>
                <Select value={category} onValueChange={setCategory}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CATEGORIES.map(cat => <SelectItem key={cat.id} value={cat.id}>{cat.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            {(category === 'chart_style' || category === 'widget_style') && (() => {
              // 两个分类共用 chart_type 字段但取值域不相交：图表样式只列非控件，控件样式只列控件
              const options = CHART_TYPES.filter(t => category === 'widget_style' ? t.category === 'widget' : t.category !== 'widget');
              const unknown = chartTypes.filter(v => !options.some(t => t.value === v));
              return (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label>{category === 'widget_style' ? '适用控件类型（可多选）' : '适用图表类型（可多选）'}</Label>
                    {!!chartTypes.length && <Button variant="link" size="sm" className="h-auto p-0 text-[11px]" onClick={() => setChartTypes([])}>设为不限</Button>}
                  </div>
                  <div className="flex max-h-32 flex-wrap gap-1 overflow-y-auto rounded-md border bg-muted/10 p-2">
                    {options.map(t => {
                      const on = chartTypes.includes(t.value);
                      return <button key={t.value} type="button" aria-pressed={on} title={t.value}
                        onClick={() => setChartTypes(v => on ? v.filter(x => x !== t.value) : [...v, t.value])}
                        className={`rounded-full px-2.5 py-1 text-[11px] ring-1 transition-colors ${on ? 'bg-primary text-primary-foreground ring-primary' : 'bg-background text-muted-foreground ring-border hover:text-foreground'}`}>
                        {t.label}
                      </button>;
                    })}
                  </div>
                  {/* 悬空类型必须显式暴露：以前 seed 写了不存在的 widget_input，字模永远挂不上也没人知道 */}
                  {!!unknown.length && <p className="text-[11px] text-destructive">已存类型「{unknown.join(' / ')}」不是系统里的图表/控件类型，挂着也不会被任何组件命中，请改选或清空。</p>}
                  <p className="text-[11px] text-muted-foreground">{category === 'widget_style'
                    ? '控件样式写 style_config.widgetStyle 段（backgroundColor / textColor / borderColor / borderRadius / padding / fontSize / height / gap / labelWidth）。'
                    : '不选任何类型 = 通用字模（适用该类全部组件）。'}</p>
                </div>
              );
            })()}
            <div className="space-y-1.5">
              <Label>样式配置 (JSON)</Label>
              <Textarea value={styleConfig} onChange={e => setStyleConfig(e.target.value)} rows={8} className="font-mono text-xs" />
            </div>
            {category === 'report_template' && (
              <div className="space-y-1.5">
                <Label>模板格式</Label>
                <Select value={reportFormat} onValueChange={v => setReportFormat(v as 'markdown' | 'html')}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="markdown">Markdown</SelectItem>
                    <SelectItem value="html">HTML</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
            {category === 'report_template' && (
              <div className="space-y-1.5">
                <Label>模板内容 (Jinja2 语法)</Label>
                <Textarea value={reportContent} onChange={e => setReportContent(e.target.value)} rows={10}
                  placeholder="使用 Jinja2 语法编写报告模板，支持 {{ 变量 }} 和 {% 控制结构 %}" className="font-mono text-xs" />
                <p className="text-[11px] text-muted-foreground">报告模板用于自动化任务生成报告，支持 Jinja2 模板语法。</p>
              </div>
            )}
            {category === 'map_tile' && (
              <div className="space-y-1.5">
                <Label>瓦片服务</Label>
                <Select value={mapTileProvider} onValueChange={setMapTileProvider}>
                  <SelectTrigger><SelectValue placeholder="选择瓦片服务" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="openstreetmap">OpenStreetMap (免费)</SelectItem>
                    <SelectItem value="amap">高德地图 (需 API Key)</SelectItem>
                    <SelectItem value="tianditu">天地图 (需 API Key)</SelectItem>
                    <SelectItem value="carto">CartoDB (免费)</SelectItem>
                    <SelectItem value="custom">自定义</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
            {category === 'map_tile' && (
              <div className="space-y-1.5">
                <Label>瓦片 URL 模板</Label>
                <Input value={mapTileUrl} onChange={e => setMapTileUrl(e.target.value)}
                  placeholder="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
                <p className="text-[11px] text-muted-foreground">支持 {'{z}/{x}/{y}'} 占位符，部分服务需要 API Key。</p>
              </div>
            )}
            {category === 'map_tile' && mapTileProvider !== 'openstreetmap' && mapTileProvider !== 'carto' && (
              <div className="space-y-1.5">
                <Label>API Key</Label>
                <Input value={mapTileApiKey} onChange={e => setMapTileApiKey(e.target.value)}
                  placeholder="输入服务 API Key" type="password" />
              </div>
            )}
            {category === 'map_tile' && (
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={mapTileDefault} onChange={e => setMapTileDefault(e.target.checked)} />
                设为默认瓦片（Chat 地图卡片自动选用；多个默认时取清单首个）
              </label>
            )}
            <div className="space-y-1.5">
              <Label>描述</Label>
              <Input value={description} onChange={e => setDescription(e.target.value)} placeholder="用途说明" />
            </div>
            {isAdmin && (
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={asBuiltin} onChange={e => setAsBuiltin(e.target.checked)} />
                标记为系统内置(仅管理员可声明)
              </label>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={save} disabled={saving || !name}>{saving ? '保存中...' : '保存'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>下架字模?</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">将「{deleteTarget?.name}」下架(软删除),已引用它的大屏仍保留创建时快照。</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={() => deleteTarget && remove(deleteTarget)}>下架</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 地图瓦片预览 */}
      <Dialog open={!!previewTile} onOpenChange={() => setPreviewTile(null)}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{previewTile?.name}</DialogTitle>
            <p className="text-sm text-muted-foreground">{previewTile?.description}</p>
          </DialogHeader>
          {previewTile && (() => {
            const sc2 = previewTile.style_config as any;
            const tileUrl = sc2?.tileUrl || '';
            const provider = sc2?.provider || 'custom';
            const providerNames: Record<string, string> = { openstreetmap: 'OSM', amap: '高德', tianditu: '天地图', carto: 'CartoDB', custom: '自定义' };
            return (
              <div className="space-y-3">
                <div className="flex items-center gap-2 text-sm">
                  <Badge>{providerNames[provider] || provider}</Badge>
                  <span className="text-muted-foreground truncate font-mono text-xs">{tileUrl}</span>
                </div>
                <div ref={mapContainerRef} className="h-[400px] rounded-lg border overflow-hidden" />
                <p className="text-xs text-muted-foreground">支持鼠标滚轮缩放、拖拽平移。中心点：北京。</p>
              </div>
            );
          })()}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPreviewTile(null)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
