import { useState, useRef, useEffect, useCallback } from 'react';
import PaginationBar, { DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { fetchVisComponentPage, VIS_CATEGORIES, isVisUsable, type VisComponent } from '@/api/visLibrary';
import { themeOfPackCode, themeLabel } from '@/components/ThemeMenuItems';
import Preview, { ShapePreview, typeLabelsOf } from '@/components/VisComponentPreview';
import { CHART_TYPES, CHART_TYPE_CATEGORIES } from '@/components/DashboardChart';
import { sanitizePack } from '@/lib/dashboardDesign';
import { DEFAULT_MOTION, validateMotion } from '@/lib/motion';
import MotionPresetEditor from '@/components/admin/MotionPresetEditor';
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
import { Plus, Edit, Trash2, Ban, Power, RefreshCw, Lock, LayoutTemplate, Palette, Image, Gauge, Grid3x3, Frame, BarChart3, Layers, FileText, Map, Search, TextCursorInput, Activity, type LucideIcon } from 'lucide-react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import client from '@/api/client';
import { useAuthStore } from '@/stores/authStore';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

const ICONS: Record<string, LucideIcon> = { theme_pack: Layers, chart_style: BarChart3, widget_style: TextCursorInput, screen_background: Image, kpi_card: Gauge,
  layout_template: Grid3x3, decoration_frame: Frame, color_theme: Palette, report_template: FileText, map_tile: Map, motion_preset: Activity };
const CATEGORIES = VIS_CATEGORIES.map(c => ({ ...c, icon: ICONS[c.id] }));
// 分类切换条选项（含「全部」，无图标）
const CATEGORY_TABS: Array<{ id: string; label: string; icon?: LucideIcon }> = [{ id: 'all', label: tr('k_778fc8f994') }, ...CATEGORIES];
// 适用类型显示业务名：typeLabelsOf 与预览组件同源（ui-resource-display.md）
// 预览框统一几何：每张卡片的预览区等高，长短不一就是列表页“乱”的直接原因
const PREVIEW_SLOT = 'flex h-24 shrink-0 items-center justify-center overflow-hidden border-b bg-muted/20 p-2';
/** 切到动效预设时判断“现 JSON 是不是动效形状”：不是就给一份完整四段模板，
 *  不让新建项一上来就带着 chart_style 的配色键被判非法 */
const needsMotionSeed = (raw: string) => {
  try { return validateMotion(JSON.parse(raw || '{}')).length > 0; } catch { return true; }
};

export default function VisLibrary() {
  const { t } = useTranslation('ui');
  const isAdmin = useAuthStore(s => s.user?.role) === 'admin';
  // 管理页 = 服务端真分页（不再拉全量前端切片）：口径含下架与禁用，
  // 禁用的字模要能列出来给管理员再启用；筛选/搜索/翻页全部下推服务端，行总数由 COUNT 得到。
  const [rows, setRows] = useState<VisComponent[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [catFilter, setCatFilter] = useState<string>('theme_pack');
  const [search, setSearch] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  // 增删改/启用禁用后重取当前页（不重拉全量）
  const load = useCallback(() => setReloadKey(k => k + 1), []);

  // 搜索防抖：停手 300ms 才发请求，并与回到第 1 页同批生效（边打字边翻页会拿到错位的空清单）
  useEffect(() => {
    const t = setTimeout(() => { setDebouncedQ(search.trim()); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError(null);
    fetchVisComponentPage({
      page, pageSize,
      category: catFilter === 'all' ? '' : catFilter,
      q: debouncedQ,
      // 管理页口径=全量（含下架与禁用）：禁用的字模要能列出来给管理员再启用
      includeInactive: true, includeDisabled: true,
    }).then(res => {
      if (!alive) return;
      setTotal(res.total);
      // 删空当前页（如删掉末页最后一条）时收敛到最后一个有效页重取，杜绝停在空白页
      const maxPage = Math.max(1, Math.ceil(res.total / pageSize));
      if (page > maxPage) { setPage(maxPage); return; }
      setRows(res.items);
    }).catch(() => {
      if (!alive) return;
      setRows([]); setTotal(0); setError(t('k_77cca9aebe'));
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [page, pageSize, catFilter, debouncedQ, reloadKey]);

  const [viewPack, setViewPack] = useState<VisComponent | null>(null);
  const [previewTile, setPreviewTile] = useState<VisComponent | null>(null);
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const packStyle = viewPack ? sanitizePack(viewPack.style_config) : {};

  const [formOpen, setFormOpen] = useState(false);
  const [edit, setEdit] = useState<VisComponent | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<VisComponent | null>(null);
  const [disableTarget, setDisableTarget] = useState<VisComponent | null>(null);
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
    try { sc = JSON.parse(styleConfig || '{}'); } catch { toast.error(t('k_ff5cd6b89b')); return; }
    // 报告模板: content/format 存入 style_config
    if (category === 'report_template') {
      sc = { content: reportContent, format: reportFormat };
    }
    // 地图瓦片: tileUrl/provider/apiKey/is_default 存入 style_config
    if (category === 'map_tile') {
      sc = { tileUrl: mapTileUrl, provider: mapTileProvider, apiKey: mapTileApiKey, ...(mapTileDefault ? { is_default: true } : {}) };
    }
    // 动效预设：写入口就拒非法值。存进去却不生效（渲染面只能按默认呈现）是字模库的老毛病
    if (category === 'motion_preset') {
      const errors = validateMotion(sc);
      if (errors.length) { toast.error(t('k_660cb0c792', { p0: errors.join('；') })); return; }
    }
    setSaving(true);
    try {
      const payload = {
        name, category, chart_type: chartTypes.join(',') || null, style_config: sc,
        description, is_builtin: asBuiltin && isAdmin,
      };
      if (edit) await client.put(`/vis-library/components/${edit.id}`, payload);
      else await client.post('/vis-library/components', payload);
      toast.success(edit ? t('k_5e1bb6e4bd') : t('k_fa9b62a11f'));
      setFormOpen(false); load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_6de920b4e4'));
    } finally { setSaving(false); }
  };

  // 禁用 = 停用但保留（与"下架"分档）：条目仍在库里可查可再启用，但一律不可被选用；
  // 主题包被禁用还要让系统主题菜单里不再出现对应主题，这个连带后果必须在提示里说清（不静默改行为）
  const toggleEnabled = async (c: VisComponent, enabled: boolean) => {
    try {
      await client.post(`/vis-library/components/${c.id}/enabled`, { enabled });
      const theme = c.category === 'theme_pack' ? themeOfPackCode(c.code) : undefined;
      toast.success(enabled
        ? t('k_a9367f7079', { p0: c.name })
        : theme ? t('k_8bd83771d4', { p0: c.name, p1: themeLabel(theme.id) }) : t('k_437144b1f5', { p0: c.name }));
      setDisableTarget(null); load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_bdf3369bc6'));
    }
  };

  const remove = async (c: VisComponent) => {
    try {
      await client.delete(`/vis-library/components/${c.id}`);
      toast.success(t('k_b71e95b4b1')); setDeleteTarget(null); load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_acf0664a54'));
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

  const canEdit = (c: VisComponent) => isAdmin || !c.is_builtin;

  return (
    <div className="mx-auto max-w-[1800px] space-y-5 p-5 md:p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="mb-2 text-xs tracking-[0.2em] text-muted-foreground">DATAFOUNDRY / VISUAL LIBRARY</div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <LayoutTemplate className="h-6 w-6 text-primary" />{t('k_8b38306417')}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">{t('k_a6b8490fc1')}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={load}><RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />{t("k_38108eaa1d")}</Button>
          <Button onClick={openCreate}><Plus className="h-4 w-4 mr-2" />{t("k_c57a15a10f")}</Button>
        </div>
      </header>

      {/* 分类切换 = 分段控件（与看板切换条同口径）；搜索与计数同一排，不再靠一排 outline 按钮堆 */}
      <div className="flex flex-wrap items-center gap-3 border-y py-3">
        <nav aria-label={t("k_5cbe95c7ae")} className="flex max-w-full items-center gap-1.5 overflow-x-auto rounded-full bg-muted/60 p-1.5 ring-1 ring-border/60 [scrollbar-width:none]">
          {CATEGORY_TABS.map(tab => {
            const on = catFilter === tab.id;
            const Icon = tab.icon;
            return <button key={tab.id} type="button" aria-pressed={on} onClick={() => { setCatFilter(tab.id); setPage(1); }}
              className={`shrink-0 whitespace-nowrap rounded-full px-4 py-1.5 text-[13px] transition-colors ${on ? 'bg-background font-medium text-primary shadow-sm ring-1 ring-primary/25' : 'text-muted-foreground hover:bg-background/70 hover:text-foreground'}`}>
              {!!Icon && <Icon className="mr-1.5 inline h-3.5 w-3.5 align-[-2px]" />}{tab.label}
            </button>;
          })}
        </nav>
        <div className="relative min-w-[200px] max-w-xs flex-1">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input aria-label={t("k_fb674c7eec")} placeholder={t("k_f2b812540e")} className="h-9 pl-9 text-sm" value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <span className="ml-auto text-xs text-muted-foreground">{t('k_a39767aaf0', { p0: total })}</span>
      </div>

      {error && <div role="alert" className="flex flex-wrap items-center gap-3 rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm">{error}<Button size="sm" variant="outline" onClick={load}>{t("k_e2d53a6d3a")}</Button></div>}
      {loading && !rows.length && <p role="status" className="text-sm text-muted-foreground">{t("k_213c2f13af")}</p>}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {rows.map(c => {
          const disabled = c.is_enabled === 0;
          return (
          <article key={c.id} className={`flex h-full flex-col overflow-hidden rounded-xl border bg-card transition-shadow hover:shadow-md ${isVisUsable(c) ? '' : 'opacity-60'} ${disabled ? 'border-amber-500/60' : ''}`}>
            <div className={PREVIEW_SLOT}><Preview c={c} /></div>
            <div className="flex-1 space-y-2 p-3">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <h2 className="flex items-center gap-1.5 truncate font-semibold" title={c.name}>
                    {c.name}{!!c.is_builtin && <Lock className="h-3 w-3 shrink-0 text-muted-foreground" />}
                  </h2>
                  <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{c.description || t('k_67a2199109')}</p>
                </div>
                <Badge variant={c.source === 'system' ? 'default' : 'secondary'} className="shrink-0">{c.source === 'system' ? t('k_09ceea7644') : t('k_c493338e8c')}</Badge>
              </div>
              <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                {/* 适用类型显示业务名；code 是内部标识，只作次要信息供排查 */}
                <span className="rounded-md bg-muted/60 px-1.5 py-0.5">{typeLabelsOf(c.chart_type)}</span>
                {disabled && <span className="rounded-md bg-amber-500/10 px-1.5 py-0.5 text-amber-600 dark:text-amber-400">{t("k_0fe5a98e9f")}</span>}
                {!c.is_active && <span className="rounded-md bg-destructive/10 px-1.5 py-0.5 text-destructive">{t("k_b07628f2f1")}</span>}
                <span className="ml-auto font-mono text-[10px]" title={t("k_40fabec9a7")}>{c.code}</span>
              </div>
            </div>
            <div className="flex items-center gap-2 border-t p-3">
              {c.category === 'theme_pack' && <Button variant="outline" size="sm" onClick={() => setViewPack(c)}>{t("k_88ed0bc1d9")}</Button>}
              {c.category === 'map_tile' && <Button variant="outline" size="sm" onClick={() => setPreviewTile(c)}>{t("k_37857853b0")}</Button>}
              <Button variant="outline" size="sm" disabled={!canEdit(c)} onClick={() => openEdit(c)}>
                <Edit className="h-3.5 w-3.5 mr-1" />{canEdit(c) ? t('k_a7f814c0a4') : t('k_85541bd9f7')}
              </Button>
              <Button variant="ghost" size="sm" disabled={!canEdit(c)}
                aria-label={tr('k_ad7917a50a', { p0: disabled ? tr('k_d4e9ca3dd4') : tr('k_be70be5a2e'), p1: c.name })}
                title={disabled ? t('k_2008553ba9') : t('k_95935ba2a9')}
                onClick={() => (disabled ? void toggleEnabled(c, true) : setDisableTarget(c))}
                className={disabled ? 'text-amber-600 dark:text-amber-400' : ''}>
                {disabled ? <Power className="h-3.5 w-3.5" /> : <Ban className="h-3.5 w-3.5" />}
              </Button>
              <Button variant="ghost" size="sm" disabled={!canEdit(c)} aria-label={t('k_4aea2438f6', { p0: c.name })} onClick={() => setDeleteTarget(c)} className="ml-auto text-destructive">
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          </article>
          );
        })}
        {rows.length === 0 && !loading && !error && (
          <div className="col-span-full rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
            {debouncedQ ? t('k_1ef7570f7a', { p0: debouncedQ }) : t('k_c82d38d0b7')}
          </div>
        )}
      </div>

      <PaginationBar
        page={page}
        pageSize={pageSize}
        total={total}
        busy={loading}
        onChange={(p: number) => setPage(p)}
        onPageSizeChange={(n: number) => { setPageSize(n); setPage(1); }}
        hideWhenSingle
        className="-mx-5 -mb-5 px-5 md:-mx-8 md:-mb-8 md:px-8"
      />

      <Dialog open={!!viewPack} onOpenChange={() => setViewPack(null)}>
        <DialogContent className="max-w-4xl max-h-[85vh] overflow-y-auto">
          <DialogHeader><DialogTitle>{t('k_6cf2eeeb3a', { p0: viewPack?.name })}</DialogTitle><DialogDescription>{t("k_34837876fd")}</DialogDescription></DialogHeader>
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
            <DialogTitle>{edit ? t('k_bdd1e77e4b') : t('k_c57a15a10f')}</DialogTitle>
            <DialogDescription>{t('k_8e4d8ca6a9')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>{t("k_1be7ae4fc2")}</Label>
                <Input value={name} onChange={e => setName(e.target.value)} placeholder={t('k_f419fc03b6')} />
              </div>
              <div className="space-y-1.5">
                <Label>{t("k_ed9f6d4d8e")}</Label>
                <Select value={category} onValueChange={v => {
                  setCategory(v);
                  if (v === 'motion_preset' && needsMotionSeed(styleConfig)) setStyleConfig(JSON.stringify(DEFAULT_MOTION, null, 2));
                }}>
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
                    <Label>{category === 'widget_style' ? t('k_4630aeb4ca') : t('k_35036a7e3f')}</Label>
                    {!!chartTypes.length && <Button variant="link" size="sm" className="h-auto p-0 text-[11px]" onClick={() => setChartTypes([])}>{t("k_78326d137d")}</Button>}
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
                  {!!unknown.length && <p className="text-[11px] text-destructive">{t('k_abea713fbd', { p0: unknown.join(' / ') })}</p>}
                  <p className="text-[11px] text-muted-foreground">{category === 'widget_style'
                    ? t('k_7f59edc154')
                    : t('k_fe3e8f2edc')}</p>
                </div>
              );
            })()}
            {category === 'motion_preset' && <MotionPresetEditor value={styleConfig} onChange={setStyleConfig} />}
            <div className="space-y-1.5">
              <Label>{t('k_b24e9592f6')}</Label>
              <Textarea value={styleConfig} onChange={e => setStyleConfig(e.target.value)} rows={8} className="font-mono text-xs" />
            </div>
            {category === 'report_template' && (
              <div className="space-y-1.5">
                <Label>{t("k_5202cfb3e9")}</Label>
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
                <Label>{t("k_38077d0626")}</Label>
                <Textarea value={reportContent} onChange={e => setReportContent(e.target.value)} rows={10}
                  placeholder={t('k_31caadc021')} className="font-mono text-xs" />
                {/* 说实话：生成侧读的是版式契约（headings + charts），不是这段正文里的 Jinja2 变量 */}
                <p className="text-[11px] text-muted-foreground">
                  {t('k_2849ab502a')}
                </p>
              </div>
            )}
            {category === 'map_tile' && (
              <div className="space-y-1.5">
                <Label>{t("k_a9adcc5d88")}</Label>
                <Select value={mapTileProvider} onValueChange={setMapTileProvider}>
                  <SelectTrigger><SelectValue placeholder={t("k_bd3e70d80e")} /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="openstreetmap">{t('k_4e2b517f99')}</SelectItem>
                    <SelectItem value="amap">{t('k_ee7f68fcf9')}</SelectItem>
                    <SelectItem value="tianditu">{t('k_51441b06d6')}</SelectItem>
                    <SelectItem value="carto">{t('k_d043a06546')}</SelectItem>
                    <SelectItem value="custom">{t("k_c493338e8c")}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
            {category === 'map_tile' && (
              <div className="space-y-1.5">
                <Label>{t("k_dcdf30e368")}</Label>
                <Input value={mapTileUrl} onChange={e => setMapTileUrl(e.target.value)}
                  placeholder="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
                <p className="text-[11px] text-muted-foreground">{t('k_c60ff100fd', { p0: '{z}/{x}/{y}' })}</p>
              </div>
            )}
            {category === 'map_tile' && mapTileProvider !== 'openstreetmap' && mapTileProvider !== 'carto' && (
              <div className="space-y-1.5">
                <Label>API Key</Label>
                <Input value={mapTileApiKey} onChange={e => setMapTileApiKey(e.target.value)}
                  placeholder={t("k_e8a9bfa388")} type="password" />
              </div>
            )}
            {category === 'map_tile' && (
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={mapTileDefault} onChange={e => setMapTileDefault(e.target.checked)} />
                {t('k_ab8cb5adcc')}
              </label>
            )}
            <div className="space-y-1.5">
              <Label>{t("k_412f54dc38")}</Label>
              <Input value={description} onChange={e => setDescription(e.target.value)} placeholder={t("k_402027cdf7")} />
            </div>
            {isAdmin && (
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={asBuiltin} onChange={e => setAsBuiltin(e.target.checked)} />
                {t('k_e067e82f42')}
              </label>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={save} disabled={saving || !name}>{saving ? t('k_2a33020e5b') : t('k_fadf24dbc5')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 禁用确认（停用但保留：说清连带影响，不让管理员以为只是"藏起来"） */}
      <Dialog open={!!disableTarget} onOpenChange={() => setDisableTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t("k_4e000dadbd")}</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">
            {tr('k_a557f99360')}{disableTarget?.name}」<span className="font-medium text-foreground">{t("k_c516466398")}</span>{tr('k_d6385e444c')}{' '}{disableTarget?.category === 'theme_pack' && themeOfPackCode(disableTarget.code)
              ? t('k_54c490715c', { p0: themeLabel(themeOfPackCode(disableTarget.code)!.id) })
              : ''}
            {t('k_2a07d87475')}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDisableTarget(null)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={() => disableTarget && void toggleEnabled(disableTarget, false)}>{t("k_be70be5a2e")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t('k_327748062c')}</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">{t('k_0e6cc14e1a', { p0: deleteTarget?.name })}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={() => deleteTarget && remove(deleteTarget)}>{t("k_a2698bcf9e")}</Button>
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
            const providerNames: Record<string, string> = { openstreetmap: 'OSM', amap: tr('k_9cc1b3fbd4'), tianditu: tr('k_83f357de73'), carto: 'CartoDB', custom: tr('k_c493338e8c') };
            return (
              <div className="space-y-3">
                <div className="flex items-center gap-2 text-sm">
                  <Badge>{providerNames[provider] || provider}</Badge>
                  <span className="text-muted-foreground truncate font-mono text-xs">{tileUrl}</span>
                </div>
                <div ref={mapContainerRef} className="h-[400px] rounded-lg border overflow-hidden" />
                <p className="text-xs text-muted-foreground">{t("k_361c0863bb")}</p>
              </div>
            );
          })()}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPreviewTile(null)}>{t("k_6c14bd7f6f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
