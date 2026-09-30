import { useState, useRef, useEffect } from 'react';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { VIS_CATEGORIES, type VisComponent } from '@/api/visLibrary';
import Preview, { ShapePreview } from '@/components/VisComponentPreview';
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
import { toast } from 'sonner';
import { Plus, Edit, Trash2, RefreshCw, Lock, LayoutTemplate, Palette, Image, Gauge, Grid3x3, Frame, FileCode, BarChart3, Layers, FileText, Map } from 'lucide-react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import client from '@/api/client';
import { useAuthStore } from '@/stores/authStore';

const ICONS = { theme_pack: Layers, chart_style: BarChart3, screen_background: Image, kpi_card: Gauge,
  layout_template: Grid3x3, decoration_frame: Frame, color_theme: Palette, sql_template: FileCode, report_template: FileText, map_tile: Map };
const CATEGORIES = VIS_CATEGORIES.map(c => ({ ...c, icon: ICONS[c.id] }));

export default function VisLibrary() {
  const isAdmin = useAuthStore(s => s.user?.role) === 'admin';
  const { items, loading, error, refresh: load } = useVisLibrary(true);
  const [catFilter, setCatFilter] = useState<string>('theme_pack');
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
  const [chartType, setChartType] = useState('');
  const [styleConfig, setStyleConfig] = useState('{}');
  const [queryTemplate, setQueryTemplate] = useState('');
  const [reportContent, setReportContent] = useState('');
  const [reportFormat, setReportFormat] = useState<'markdown' | 'html'>('markdown');
  const [mapTileUrl, setMapTileUrl] = useState('');
  const [mapTileProvider, setMapTileProvider] = useState('');
  const [mapTileApiKey, setMapTileApiKey] = useState('');
  const [description, setDescription] = useState('');
  const [asBuiltin, setAsBuiltin] = useState(false);

  const openCreate = () => {
    setEdit(null); setName(''); setCategory('chart_style'); setChartType('');
    setStyleConfig(JSON.stringify({ config: { colorScheme: ['#22d3ee', '#3b82f6'] } }, null, 2));
    setQueryTemplate(''); setReportContent(''); setReportFormat('markdown');
    setMapTileUrl(''); setMapTileProvider(''); setMapTileApiKey('');
    setDescription(''); setAsBuiltin(false); setFormOpen(true);
  };
  const openEdit = (c: VisComponent) => {
    setEdit(c); setName(c.name); setCategory(c.category); setChartType(c.chart_type || '');
    setStyleConfig(JSON.stringify(c.style_config || {}, null, 2));
    setQueryTemplate(c.query_template ? JSON.stringify(c.query_template, null, 2) : '');
    setReportContent((c.style_config as any)?.content || '');
    setReportFormat((c.style_config as any)?.format || 'markdown');
    setMapTileUrl((c.style_config as any)?.tileUrl || '');
    setMapTileProvider((c.style_config as any)?.provider || '');
    setMapTileApiKey((c.style_config as any)?.apiKey || '');
    setDescription(c.description || ''); setAsBuiltin(!!c.is_builtin); setFormOpen(true);
  };

  const save = async () => {
    let sc: any, qt: any = null;
    try { sc = JSON.parse(styleConfig || '{}'); } catch { toast.error('样式配置不是合法 JSON'); return; }
    if (queryTemplate.trim()) {
      try { qt = JSON.parse(queryTemplate); } catch { toast.error('SQL 模板不是合法 JSON'); return; }
    }
    // 报告模板: content/format 存入 style_config
    if (category === 'report_template') {
      sc = { content: reportContent, format: reportFormat };
    }
    // 地图瓦片: tileUrl/provider/apiKey 存入 style_config
    if (category === 'map_tile') {
      sc = { tileUrl: mapTileUrl, provider: mapTileProvider, apiKey: mapTileApiKey };
    }
    setSaving(true);
    try {
      const payload = {
        name, category, chart_type: chartType || null, style_config: sc,
        query_template: qt, description, is_builtin: asBuiltin && isAdmin,
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

  const shown = catFilter === 'all' ? items : items.filter(i => i.category === catFilter);
  const canEdit = (c: VisComponent) => isAdmin || !c.is_builtin;

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2"><LayoutTemplate className="h-5 w-5" /> UI 字模库</h1>
          <p className="text-sm text-muted-foreground mt-1">大屏可复用的"活字":主题包 / 图表样式 / 背景 / KPI 卡 / 布局 / 装饰 / 配色 / SQL 模板。系统内置仅管理员可改,自定义可回存沉淀。</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={load}><RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />刷新</Button>
          <Button size="sm" onClick={openCreate}><Plus className="h-4 w-4 mr-1" />新建字模</Button>
        </div>
      </div>

      <div className="flex flex-wrap gap-1">
        <Button variant={catFilter === 'all' ? 'default' : 'outline'} size="sm" onClick={() => setCatFilter('all')}>全部</Button>
        {CATEGORIES.map(cat => (
          <Button key={cat.id} variant={catFilter === cat.id ? 'default' : 'outline'} size="sm" onClick={() => setCatFilter(cat.id)}>
            <cat.icon className="h-3.5 w-3.5 mr-1" />{cat.label}
          </Button>
        ))}
      </div>

      {error && <div role="alert" className="rounded-md border border-destructive p-3 text-sm">{error}<Button variant="link" onClick={load}>重试</Button></div>}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {shown.map(c => (
          <div key={c.id} className={`rounded-lg border p-3 space-y-2 ${c.is_active ? '' : 'opacity-50'}`}>
            <Preview c={c} />
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="font-medium truncate flex items-center gap-1.5">
                  {c.name}
                  {!!c.is_builtin && <Lock className="h-3 w-3 text-muted-foreground" />}
                </div>
                <div className="text-xs text-muted-foreground truncate">{c.code}{c.chart_type ? ` · ${c.chart_type}` : ''}</div>
              </div>
              <Badge variant={c.source === 'system' ? 'default' : 'secondary'}>{c.source === 'system' ? '内置' : '自定义'}</Badge>
            </div>
            {c.description && <p className="text-xs text-muted-foreground line-clamp-2">{c.description}</p>}
            <div className="flex flex-wrap items-center gap-2 pt-1">
              {c.category === 'theme_pack' && <Button variant="outline" size="sm" onClick={() => setViewPack(c)}>浏览整套组件</Button>}
              {c.category === 'map_tile' && <Button variant="outline" size="sm" onClick={() => setPreviewTile(c)}>预览瓦片</Button>}
              <Button variant="outline" size="sm" disabled={!canEdit(c)} onClick={() => openEdit(c)}>
                <Edit className="h-3.5 w-3.5 mr-1" />{canEdit(c) ? '编辑' : '只读'}
              </Button>
              <Button variant="ghost" size="sm" disabled={!canEdit(c)} onClick={() => setDeleteTarget(c)} className="text-destructive">
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        ))}
        {shown.length === 0 && !loading && !error && (
          <div className="col-span-full text-center text-muted-foreground py-12">该分类暂无字模</div>
        )}
      </div>

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
            <DialogDescription>样式配置以 JSON 描述,前端按类别解释(背景/卡片/图表样式/配色/布局/装饰)。</DialogDescription>
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
            {category === 'chart_style' && (
              <div className="space-y-1.5">
                <Label>适用图表类型</Label>
                <Select value={chartType || '_none'} onValueChange={v => setChartType(v === '_none' ? '' : v)}>
                  <SelectTrigger><SelectValue placeholder="不限" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="_none">不限</SelectItem>
                    {CHART_TYPES.filter(t => t.category !== 'widget').map(t => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            )}
            <div className="space-y-1.5">
              <Label>样式配置 (JSON)</Label>
              <Textarea value={styleConfig} onChange={e => setStyleConfig(e.target.value)} rows={8} className="font-mono text-xs" />
            </div>
            {category === 'sql_template' && (
              <div className="space-y-1.5">
                <Label>SQL 模板 (JSON, 供传统 BI 人工配置)</Label>
                <Textarea value={queryTemplate} onChange={e => setQueryTemplate(e.target.value)} rows={5}
                  placeholder='{"sql":"SELECT ... {{table}}","params":[{"name":"n","type":"number"}]}' className="font-mono text-xs" />
                <p className="text-[11px] text-muted-foreground">SQL 模板仅在图表配置里由管理员/数据开发使用, 不进入 Chat/Agent 取数。</p>
              </div>
            )}
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
