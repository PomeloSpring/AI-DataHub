// 模板中心 · 报告模板（表格形态）。
//
// 读写一律走报告域 `/reports/templates*`：菜单勾「模板中心」就该能配完报告模板，
// 过去这页为了拿主题清单去调 /api/vis-library，把可见性绑到了 vis:* 权限上（权限漂移）。
//
// 布局从卡片改表格：卡片里缩略图占大头，真正决定生不生效的「版式 / 被谁引用」反而挤成一行小字；
// 表格一列一件事，配完能一眼看出这份模板约束了什么、有没有任务在用。
import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from '@/lib/toast';
import {
  Plus, Edit, Trash2, RefreshCw, FileText, Lock, Layers, Palette, Wand2, Loader2,
} from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { useThemeStore, IS_DARK, type ThemeId } from '@/stores/themeStore';
import { CHART_TYPES } from '@/components/DashboardChart';
import ReportContent from '@/components/ReportContent';
import {
  analyzeReportTemplateLayout, deleteReportTemplate, listReportTemplateRows, listReportThemeOptions,
  saveReportTemplate, type ReportTemplateLayout, type ReportTemplateRow,
} from '@/api/report';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

const FORMAT_MAP: Record<string, { label: string; color: string }> = {
  markdown: { label: 'Markdown', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  html: { label: 'HTML', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
};

const THEME_NONE = '__follow_viewer__';

/** 主题包 code 解出来的主题名能不能真的切预览（字模库里可能多于前端 ThemeId 清单） */
const isPreviewableTheme = (value: string): value is ThemeId =>
  Object.prototype.hasOwnProperty.call(IS_DARK, value);

/** 新模板的默认正文：一份 markdown 骨架（点「从正文抽版式」即得到可复用版式） */
const DEFAULT_TEMPLATE_BODY = `# 报告标题

## 核心结论

（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）

## 分省明细

\`\`\`chart
{"title": "各省 PV", "chart_type": "bar", "columns": ["省份", "PV"], "rows": []}
\`\`\`
`;

/** 图表规格草稿：列名在界面里是逗号分隔的可读文本，落库前拆成数组 */
interface ChartDraft { title: string; chart_type: string; columns: string }
interface LayoutDraft { headings: string[]; charts: ChartDraft[] }

const EMPTY_LAYOUT: LayoutDraft = { headings: [], charts: [] };

const splitColumns = (text: string) => text.split(/[,，、/|]/).map(s => s.trim()).filter(Boolean);

/** 版式摘要：一眼看出这份模板约束了什么，别配出"看得见却不起作用"的空模板 */
function layoutSummary(r: ReportTemplateRow): { text: string; warn: boolean } {
  const headings = (r.headings || []).length;
  const charts = (r.charts || []).length;
  if (!headings && !charts) {
    // 既没登记版式、正文也抽不出结构：任务里选得到它，但生成时约束不了任何东西
    return { text: r.content ? tr('k_78831923e7') : tr('k_4c072956e2'), warn: !r.content };
  }
  const parts = [headings ? tr('k_b2ee70fd2d', { p0: headings }) : '', charts ? tr('k_288e89c181', { p0: charts }) : ''].filter(Boolean);
  // 只有旧正文可抽（没显式登记）时也标出来：它随时可能因为正文改动而变样
  return { text: parts.join(' · ') + (r.layout_registered ? '' : tr('k_5430d11c7b')), warn: false };
}

export default function ReportTemplatePanel() {
  const { t } = useTranslation('ui');
  const isAdmin = useAuthStore(s => s.user?.role) === 'admin';
  const currentTheme = useThemeStore(s => s.theme);
  const setCurrentTheme = useThemeStore(s => s.setTheme);

  const [rows, setRows] = useState<ReportTemplateRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [themeOptions, setThemeOptions] = useState<{ code: string; theme: string; name: string }[]>([]);
  const [formOpen, setFormOpen] = useState(false);
  const [editRow, setEditRow] = useState<ReportTemplateRow | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ReportTemplateRow | null>(null);

  // 表单状态
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [content, setContent] = useState('');
  const [format, setFormat] = useState<'markdown' | 'html'>('markdown');
  const [theme, setTheme] = useState(THEME_NONE);
  const [layout, setLayout] = useState<LayoutDraft>(EMPTY_LAYOUT);
  const [layoutEdited, setLayoutEdited] = useState(false);
  const [saving, setSaving] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [tab, setTab] = useState<'layout' | 'body' | 'preview'>('layout');
  const [preview, setPreview] = useState<{ skeleton: string; hasLayout: boolean } | null>(null);

  const themeName = (value: string) => themeOptions.find(o => o.theme === value)?.name || value;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await listReportTemplateRows(includeInactive));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_801541205a'));
    } finally {
      setLoading(false);
    }
  }, [includeInactive]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    // 主题清单从报告域取：这页不该依赖字模库权限（菜单勾了模板中心却读不到主题就是上次那种漂移）
    listReportThemeOptions().then(setThemeOptions).catch(() => setThemeOptions([]));
  }, []);

  const canEdit = (r: ReportTemplateRow) => isAdmin || !r.is_builtin;

  const openCreate = () => {
    setEditRow(null);
    setName('');
    setDescription('');
    setContent(DEFAULT_TEMPLATE_BODY);
    setFormat('markdown');
    setTheme(THEME_NONE);
    setLayout(EMPTY_LAYOUT);
    setLayoutEdited(false);
    setPreview(null);
    setTab('layout');
    setFormOpen(true);
  };

  const openEdit = (r: ReportTemplateRow) => {
    setEditRow(r);
    setName(r.name);
    setDescription(r.description || '');
    // 用完整正文而不是列表里的 240 字预览：拿预览回填会在保存时把正文截断写回去
    setContent(r.content || '');
    setFormat(r.format === 'html' ? 'html' : 'markdown');
    setTheme(r.theme || THEME_NONE);
    setLayout(r.headings?.length || r.charts?.length ? {
      headings: r.headings || [],
      charts: (r.charts || []).map(ch => ({
        title: ch.title || '', chart_type: ch.chart_type || '', columns: (ch.columns || []).join(', '),
      })),
    } : EMPTY_LAYOUT);
    setLayoutEdited(!!(r.headings?.length || r.charts?.length));
    setPreview(null);
    setTab('layout');
    setFormOpen(true);
  };

  /** 草稿 → 契约形状；不完整图表（缺类型或列名）留在这里被数出来，不静默丢弃 */
  const buildLayout = (): { layout: ReportTemplateLayout; dropped: number } => {
    const charts = layout.charts
      .map(ch => ({
        title: ch.title.trim(),
        chart_type: ch.chart_type.trim(),
        columns: splitColumns(ch.columns),
      }))
      .filter(ch => ch.chart_type && ch.columns.length);
    return {
      dropped: layout.charts.length - charts.length,
      layout: { format, headings: layout.headings.map(h => h.trim()).filter(Boolean), charts },
    };
  };

  /** 从正文抽版式（服务端同一抽取口径，前端不解析 markdown） */
  const extractFromBody = async () => {
    if (!content.trim()) { toast.error(t('k_bb528feed1')); return; }
    setAnalyzing(true);
    try {
      const out = await analyzeReportTemplateLayout({ content, format });
      setLayout({
        headings: out.layout.headings || [],
        charts: (out.layout.charts || []).map(ch => ({
          title: ch.title || '', chart_type: ch.chart_type || '', columns: (ch.columns || []).join(', '),
        })),
      });
      setLayoutEdited(true);
      setPreview({ skeleton: out.skeleton, hasLayout: out.has_layout });
      if (!out.has_layout) {
        toast.error(t('k_ca25e5a397'));
      }
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_ec02cf0a45'));
    } finally {
      setAnalyzing(false);
    }
  };

  /** 预览：手工编过版式就以它为准，否则按正文抽；渲染面与报告中心同一个 ReportContent */
  const showPreview = async () => {
    setAnalyzing(true);
    try {
      const out = await analyzeReportTemplateLayout({
        content, format, layout: layoutEdited ? buildLayout().layout : null,
        // HTML 骨架在隔离 iframe 里渲染，主文档主题变量进不去：不传主题就只剩浏览器默认样式
        // （不锁定按查看者当前主题，锁定则按锁定主题 —— 与生成时同一口径）
        theme: theme === THEME_NONE ? currentTheme : theme,
      });
      setPreview({ skeleton: out.skeleton, hasLayout: out.has_layout });
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_46652cdc4d'));
    } finally {
      setAnalyzing(false);
    }
  };

  const handleSave = async () => {
    if (!name.trim()) { toast.error(t('k_8f21b9868f')); return; }
    const built = buildLayout();
    if (built.dropped) {
      // 半成品图表规格会被服务端清洗掉 = 配了但生成时不起作用，这里直接挡住而不是静默丢
      toast.error(t('k_2668296dc1', { p0: built.dropped }));
      return;
    }
    let finalLayout = built.layout;
    if (!finalLayout.headings.length && !finalLayout.charts.length) {
      // 没手工编版式时按正文抽一次：配了正文却忘了抽版式是最常见的"配了不生效"形态
      try {
        finalLayout = (await analyzeReportTemplateLayout({ content, format })).layout;
      } catch (e: any) {
        toast.error(e?.response?.data?.detail || t('k_78dcd659f2'));
        return;
      }
    }
    if (!finalLayout.headings.length && !finalLayout.charts.length) {
      toast.error(t('k_ae255c9cf6'));
      return;
    }

    setSaving(true);
    try {
      await saveReportTemplate({
        name: name.trim(),
        format,
        theme: theme === THEME_NONE ? '' : theme,
        layout: finalLayout,
        content,
        description: description.trim(),
        is_builtin: !!editRow?.is_builtin,
      }, editRow?.id);
      toast.success(editRow ? t('k_baf8439559')
        : t('k_b365ab8dba'));
      setFormOpen(false);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_6de920b4e4'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteReportTemplate(deleteTarget.id);
      toast.success(t('k_b07628f2f1'));
      setDeleteTarget(null);
      load();
    } catch (e: any) {
      // 被任务引用时服务端回数量与处置办法：这段必须原样给用户，不能笼统报"删除失败"
      toast.error(e?.response?.data?.detail || t('k_acf0664a54'));
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {tr('k_924792ca8b')}<b>{t("k_4fd4923ba0")}</b>。
        </p>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <Switch checked={includeInactive} onCheckedChange={setIncludeInactive} />
            {t('k_8d09c1dddc')}
          </label>
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />{t('k_38108eaa1d')}
          </Button>
          <Button size="sm" onClick={openCreate}><Plus className="h-4 w-4 mr-1" />{t("k_a38a511b8c")}</Button>
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("k_bbc511d06d")}</TableHead>
            <TableHead>{t("k_9d2601c843")}</TableHead>
            <TableHead>{t("k_431c606023")}</TableHead>
            <TableHead>{t("k_e848ddd482")}</TableHead>
            <TableHead className="text-right">{t("k_c45eb317f8")}</TableHead>
            <TableHead>{t("k_62e951a692")}</TableHead>
            <TableHead>{t("k_093dea88c9")}</TableHead>
            <TableHead className="text-right">{t("k_f3ea6d345e")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow><TableCell colSpan={8} className="text-center py-8 text-muted-foreground">{t('k_26b5bd4947')}</TableCell></TableRow>
          ) : rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={8} className="text-center py-8 text-muted-foreground">
                {t('k_980bbba31e')}
              </TableCell>
            </TableRow>
          ) : rows.map(r => {
            const fmt = FORMAT_MAP[r.format === 'html' ? 'html' : 'markdown'];
            const sum = layoutSummary(r);
            return (
              <TableRow key={r.id} className={!Number(r.is_active) ? 'opacity-60' : ''}>
                <TableCell>
                  <div className="flex items-center gap-1.5 font-medium">
                    {r.name}
                    {!!r.is_builtin && <Lock className="h-3 w-3 text-muted-foreground" aria-label={t("k_1cddfcf703")} />}
                  </div>
                  {r.description && (
                    <div className="text-xs text-muted-foreground max-w-[320px] truncate">{r.description}</div>
                  )}
                </TableCell>
                <TableCell><Badge variant="outline" className={fmt.color}>{fmt.label}</Badge></TableCell>
                <TableCell>
                  {/* 配了啥一眼看得见：版式条目直接列在行里，不用点开卡片再猜 */}
                  <Badge variant="outline" className={`gap-1 ${sum.warn ? 'text-destructive border-destructive/40' : ''}`}>
                    <Layers className="h-3 w-3" />{sum.text}
                  </Badge>
                  {r.headings?.length > 0 && (
                    <div className="text-[11px] text-muted-foreground mt-1 max-w-[360px]" title={r.headings.join(' / ')}>
                      {r.headings.slice(0, 4).join(' / ')}{r.headings.length > 4 ? t('k_a5be3db3a3', { p0: r.headings.length }) : ''}
                    </div>
                  )}
                </TableCell>
                <TableCell className="text-xs">
                  {r.theme
                    ? <span className="inline-flex items-center gap-1"><Palette className="h-3 w-3" />{themeName(r.theme)}</span>
                    : <span className="text-muted-foreground">{t("k_d7a768d8e3")}</span>}
                </TableCell>
                <TableCell className="text-right">
                  {r.used_by_tasks
                    ? <Badge variant="outline" className="border-emerald-500/30 text-emerald-600">{t('k_c1cea3344e', { p0: r.used_by_tasks })}</Badge>
                    : <span className="text-muted-foreground text-xs">{t("k_2c6270bcce")}</span>}
                </TableCell>
                <TableCell>
                  <Badge variant="outline" className={Number(r.is_active) ? 'text-green-500' : 'text-gray-500'}>
                    {Number(r.is_active) ? t('k_d4e9ca3dd4') : t('k_b07628f2f1')}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                  {r.updated_at ? new Date(r.updated_at).toLocaleString() : '-'}
                </TableCell>
                <TableCell>
                  <div className="flex items-center justify-end gap-1">
                    <Button variant="ghost" size="sm" disabled={!canEdit(r)} onClick={() => openEdit(r)}>
                      <Edit className="h-3.5 w-3.5 mr-1" />{canEdit(r) ? t('k_a7f814c0a4') : t('k_85541bd9f7')}
                    </Button>
                    <Button variant="ghost" size="sm" disabled={!canEdit(r)} onClick={() => setDeleteTarget(r)}
                      className="text-destructive" aria-label={t("k_a2698bcf9e")}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      <p className="text-xs text-muted-foreground">
        {t('k_66fb00e956')}
      </p>

      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-4xl max-h-[92vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editRow ? t('k_c6aa350404') : t('k_a38a511b8c')}</DialogTitle>
            <DialogDescription>
              {t('k_9242ff502a')}
            </DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>{t("k_bbc511d06d")}</Label>
              <Input value={name} onChange={e => setName(e.target.value)} placeholder={t("k_fb58ee1fb5")} />
            </div>
            <div className="space-y-1.5">
              <Label>{t("k_b015409988")}</Label>
              <Select value={format} onValueChange={v => setFormat(v as 'markdown' | 'html')}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="markdown">{t('k_bc18fbe21d')}</SelectItem>
                  <SelectItem value="html">{t("k_27988b22a6")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>{t("k_a23dedc9e2")}</Label>
              <Select value={theme} onValueChange={setTheme}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={THEME_NONE}>{t("k_3dee804e18")}</SelectItem>
                  {themeOptions.map(o => <SelectItem key={o.theme} value={o.theme}>{o.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                {t('k_a76dbdf3a6')}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label>{t("k_412f54dc38")}</Label>
              <Input value={description} onChange={e => setDescription(e.target.value)} placeholder={t("k_abd27398a2")} />
            </div>
          </div>

          <Tabs value={tab} onValueChange={v => {
            const next = v as typeof tab;
            setTab(next);
            if (next === 'preview' && !preview) void showPreview();
          }}>
            <TabsList>
              <TabsTrigger value="layout"><Layers className="w-4 h-4 mr-1" />{t("k_431c606023")}</TabsTrigger>
              <TabsTrigger value="body"><FileText className="w-4 h-4 mr-1" />{t("k_a0d95bc304")}</TabsTrigger>
              <TabsTrigger value="preview"><RefreshCw className="w-4 h-4 mr-1" />{t("k_de61aa8e1c")}</TabsTrigger>
            </TabsList>

            <TabsContent value="layout" className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">
                  {t('k_cb97f6f97a')}
                </p>
                <Button variant="outline" size="sm" className="h-8 text-xs" onClick={extractFromBody} disabled={analyzing}>
                  {analyzing ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Wand2 className="h-3.5 w-3.5 mr-1" />}
                  {tr('k_138bf0b80d')}{' '}</Button>
              </div>

              <div className="space-y-2 border rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <Label className="text-xs">{t("k_7455c6711c")}</Label>
                  <Button variant="ghost" size="sm" className="h-7 text-xs"
                    onClick={() => { setLayoutEdited(true); setLayout(p => ({ ...p, headings: [...p.headings, ''] })); }}>
                    <Plus className="h-3.5 w-3.5 mr-1" />{t('k_aa941e6a2e')}
                  </Button>
                </div>
                {!layout.headings.length && <p className="text-xs text-muted-foreground">{t("k_bd73d88da9")}</p>}
                {layout.headings.map((h, i) => (
                  <div key={`h-${i}`} className="flex items-center gap-2">
                    <span className="w-5 shrink-0 text-xs text-muted-foreground">{i + 1}</span>
                    <Input value={h} placeholder={t("k_a781eef3dd")}
                      onChange={e => {
                        setLayoutEdited(true);
                        setLayout(p => ({ ...p, headings: p.headings.map((x, j) => (j === i ? e.target.value : x)) }));
                      }} />
                    <Button variant="ghost" size="sm" className="h-8 text-destructive"
                      onClick={() => { setLayoutEdited(true); setLayout(p => ({ ...p, headings: p.headings.filter((_, j) => j !== i) })); }}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}
              </div>

              <div className="space-y-2 border rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <Label className="text-xs">{t('k_e5a937bf17')}</Label>
                  <Button variant="ghost" size="sm" className="h-7 text-xs"
                    onClick={() => {
                      setLayoutEdited(true);
                      setLayout(p => ({ ...p, charts: [...p.charts, { title: '', chart_type: '', columns: '' }] }));
                    }}>
                    <Plus className="h-3.5 w-3.5 mr-1" />{t('k_8b9d4aaf57')}
                  </Button>
                </div>
                {!layout.charts.length && <p className="text-xs text-muted-foreground">{t("k_47b5f6eb48")}</p>}
                {layout.charts.map((ch, i) => (
                  <div key={`c-${i}`} className="space-y-1.5 rounded-md border border-dashed p-2">
                    <div className="flex items-center gap-2">
                      <Input className="h-8 text-xs" value={ch.title} placeholder={t("k_9891d7b39f")}
                        onChange={e => {
                          setLayoutEdited(true);
                          setLayout(p => ({ ...p, charts: p.charts.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)) }));
                        }} />
                      <Select value={ch.chart_type || undefined} onValueChange={v => {
                        setLayoutEdited(true);
                        setLayout(p => ({ ...p, charts: p.charts.map((x, j) => (j === i ? { ...x, chart_type: v } : x)) }));
                      }}>
                        <SelectTrigger className="w-[150px] h-8 text-xs"><SelectValue placeholder={t("k_e891b46dbb")} /></SelectTrigger>
                        <SelectContent>
                          {CHART_TYPES.map(t => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
                        </SelectContent>
                      </Select>
                      <Button variant="ghost" size="sm" className="h-8 text-destructive"
                        onClick={() => { setLayoutEdited(true); setLayout(p => ({ ...p, charts: p.charts.filter((_, j) => j !== i) })); }}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                    <Input className="h-8 text-xs" value={ch.columns} placeholder={t('k_67ca32ab64')}
                      onChange={e => {
                        setLayoutEdited(true);
                        setLayout(p => ({ ...p, charts: p.charts.map((x, j) => (j === i ? { ...x, columns: e.target.value } : x)) }));
                      }} />
                  </div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="body" className="space-y-2">
              <p className="text-xs text-muted-foreground">
                {t('k_ec59c2644d')}
              </p>
              <Textarea value={content} onChange={e => setContent(e.target.value)}
                className="font-mono text-sm min-h-[360px]"
                placeholder={format === 'html' ? '<h2>{t("k_023329e7f4")}</h2><p>…</p>' : t('k_1298eafe89')} />
            </TabsContent>

            <TabsContent value="preview" className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  {t('k_147277c45d', { p0: themeName(theme === THEME_NONE ? currentTheme : theme) })}</p>
                <div className="flex items-center gap-2 shrink-0">
                  {theme !== THEME_NONE && isPreviewableTheme(theme) && currentTheme !== theme && (
                    <Button variant="outline" size="sm" className="h-8 text-xs" onClick={() => setCurrentTheme(theme)}>
                      <Palette className="h-3.5 w-3.5 mr-1" />{tr('k_9a2d295807')}{themeName(theme)}{tr('k_55590fadcc')}{' '}</Button>
                  )}
                  <Button variant="outline" size="sm" className="h-8 text-xs" onClick={showPreview} disabled={analyzing}>
                    <RefreshCw className={`h-3.5 w-3.5 mr-1 ${analyzing ? 'animate-spin' : ''}`} />{t('k_f73706d9a0')}
                  </Button>
                </div>
              </div>
              {preview && !preview.hasLayout && (
                <p role="alert" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                  {t('k_3c7745a3d4')}
                </p>
              )}
              {/* HTML 预览是整份 iframe（自带 78vh 高度与内部滚动）：外层再套 max-h 就变成双重裁切，
                  只露出上半截还滚不动 —— 按格式分流，Markdown 才用外层滚动容器 */}
              <div className={format === 'html'
                ? 'border rounded-lg p-3 bg-card'
                : 'border rounded-lg p-3 bg-card max-h-[460px] overflow-y-auto'}>
                {preview
                  ? <ReportContent content={preview.skeleton} format={format} />
                  : <p className="text-sm text-muted-foreground">{t("k_e9a2532235")}</p>}
              </div>
            </TabsContent>
          </Tabs>

          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? <><Loader2 className="h-4 w-4 mr-1 animate-spin" />{t("k_6644f06197")}</> : editRow ? t('k_d9db02d07a') : t('k_fcbd093292')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t('k_84825607a7')}</DialogTitle><DialogDescription>
            {t('k_fa0c4031c7')}
          </DialogDescription></DialogHeader>
          <p className="text-sm text-muted-foreground">{t('k_68ffae4350', { p0: deleteTarget?.name })}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive" onClick={handleDelete}>{t("k_02d4dbab69")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
