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
    return { text: r.content ? '旧模板：版式从正文抽取' : '未登记版式（不会起作用）', warn: !r.content };
  }
  const parts = [headings ? `${headings} 个章节` : '', charts ? `${charts} 个图表` : ''].filter(Boolean);
  // 只有旧正文可抽（没显式登记）时也标出来：它随时可能因为正文改动而变样
  return { text: parts.join(' · ') + (r.layout_registered ? '' : '（从正文抽取）'), warn: false };
}

export default function ReportTemplatePanel() {
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
      toast.error(e?.response?.data?.detail || '加载模板失败');
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
    if (!content.trim()) { toast.error('先在「模板正文」里写好结构（章节标题与 ```chart 图表块）'); return; }
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
        toast.error('正文里没有可沿用的结构：至少要有章节标题或带 chart_type + columns 的 ```chart 块');
      }
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '抽取版式失败，请检查正文格式');
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
      toast.error(e?.response?.data?.detail || '预览生成失败');
    } finally {
      setAnalyzing(false);
    }
  };

  const handleSave = async () => {
    if (!name.trim()) { toast.error('请输入模板名称'); return; }
    const built = buildLayout();
    if (built.dropped) {
      // 半成品图表规格会被服务端清洗掉 = 配了但生成时不起作用，这里直接挡住而不是静默丢
      toast.error(`有 ${built.dropped} 个图表规格不完整（缺图表类型或列名），补齐或删掉再保存`);
      return;
    }
    let finalLayout = built.layout;
    if (!finalLayout.headings.length && !finalLayout.charts.length) {
      // 没手工编版式时按正文抽一次：配了正文却忘了抽版式是最常见的"配了不生效"形态
      try {
        finalLayout = (await analyzeReportTemplateLayout({ content, format })).layout;
      } catch (e: any) {
        toast.error(e?.response?.data?.detail || '抽取版式失败，模板未保存');
        return;
      }
    }
    if (!finalLayout.headings.length && !finalLayout.charts.length) {
      toast.error('这份模板没有约束任何东西：至少登记一个章节标题或图表规格（或写出带结构的正文）');
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
      toast.success(editRow ? '模板已更新，引用它的定时任务下次执行即生效'
        : '模板已创建：可在任务表单与报告中心「套用模板」里选到它');
      setFormOpen(false);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteReportTemplate(deleteTarget.id);
      toast.success('已下架');
      setDeleteTarget(null);
      load();
    } catch (e: any) {
      // 被任务引用时服务端回数量与处置办法：这段必须原样给用户，不能笼统报"删除失败"
      toast.error(e?.response?.data?.detail || '删除失败');
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          报告模板登记的是一份版式：章节骨架 + 图表规格 + 主题包。定时任务、报告「套用模板」与「用最新数据重新生成」共用这一份口径；
          模板只存结构与主题，<b>不存数据</b>。
        </p>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <Switch checked={includeInactive} onCheckedChange={setIncludeInactive} />
            显示已下架
          </label>
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />刷新
          </Button>
          <Button size="sm" onClick={openCreate}><Plus className="h-4 w-4 mr-1" />新建模板</Button>
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>模板名称</TableHead>
            <TableHead>格式</TableHead>
            <TableHead>版式</TableHead>
            <TableHead>主题</TableHead>
            <TableHead className="text-right">被任务引用</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>更新时间</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow><TableCell colSpan={8} className="text-center py-8 text-muted-foreground">加载中...</TableCell></TableRow>
          ) : rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={8} className="text-center py-8 text-muted-foreground">
                暂无模板：点右上「新建模板」，或在报告中心把好报告「另存为模板」
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
                    {!!r.is_builtin && <Lock className="h-3 w-3 text-muted-foreground" aria-label="内置模板" />}
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
                      {r.headings.slice(0, 4).join(' / ')}{r.headings.length > 4 ? ` 等 ${r.headings.length} 节` : ''}
                    </div>
                  )}
                </TableCell>
                <TableCell className="text-xs">
                  {r.theme
                    ? <span className="inline-flex items-center gap-1"><Palette className="h-3 w-3" />{themeName(r.theme)}</span>
                    : <span className="text-muted-foreground">跟随查看者</span>}
                </TableCell>
                <TableCell className="text-right">
                  {r.used_by_tasks
                    ? <Badge variant="outline" className="border-emerald-500/30 text-emerald-600">{r.used_by_tasks} 个任务</Badge>
                    : <span className="text-muted-foreground text-xs">未引用</span>}
                </TableCell>
                <TableCell>
                  <Badge variant="outline" className={Number(r.is_active) ? 'text-green-500' : 'text-gray-500'}>
                    {Number(r.is_active) ? '启用' : '已下架'}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                  {r.updated_at ? new Date(r.updated_at).toLocaleString() : '-'}
                </TableCell>
                <TableCell>
                  <div className="flex items-center justify-end gap-1">
                    <Button variant="ghost" size="sm" disabled={!canEdit(r)} onClick={() => openEdit(r)}>
                      <Edit className="h-3.5 w-3.5 mr-1" />{canEdit(r) ? '编辑' : '只读'}
                    </Button>
                    <Button variant="ghost" size="sm" disabled={!canEdit(r)} onClick={() => setDeleteTarget(r)}
                      className="text-destructive" aria-label="下架">
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
        模板在哪被用：① 任务调度新建/编辑任务时选「报告模板」，每次执行按版式出报告；
        ② 报告「用最新数据重新生成」沿用上一版版式，好报告可「另存为模板」沉淀回这里；
        ③ 智能问数出报告按同一份版式契约渲染。被任务引用的模板下架会被服务端挡住并给出任务数。
      </p>

      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-w-4xl max-h-[92vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editRow ? '编辑模板' : '新建模板'}</DialogTitle>
            <DialogDescription>
              版式（章节 + 图表规格）决定生成出来的报告长什么样；正文用于抽版式与旧模板兼容。
            </DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>模板名称</Label>
              <Input value={name} onChange={e => setName(e.target.value)} placeholder="如：周报版式模板" />
            </div>
            <div className="space-y-1.5">
              <Label>输出格式</Label>
              <Select value={format} onValueChange={v => setFormat(v as 'markdown' | 'html')}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="markdown">Markdown（章节 + ```chart 图表）</SelectItem>
                  <SelectItem value="html">HTML（整份自包含报告）</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>主题包（字模库）</Label>
              <Select value={theme} onValueChange={setTheme}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={THEME_NONE}>不锁定（跟随查看者主题）</SelectItem>
                  {themeOptions.map(o => <SelectItem key={o.theme} value={o.theme}>{o.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                锁定后 HTML 报告生成时按该主题令牌内联配色；Markdown/看板渲染仍跟随查看者主题。
              </p>
            </div>
            <div className="space-y-1.5">
              <Label>描述</Label>
              <Input value={description} onChange={e => setDescription(e.target.value)} placeholder="适用场景说明（可选）" />
            </div>
          </div>

          <Tabs value={tab} onValueChange={v => {
            const next = v as typeof tab;
            setTab(next);
            if (next === 'preview' && !preview) void showPreview();
          }}>
            <TabsList>
              <TabsTrigger value="layout"><Layers className="w-4 h-4 mr-1" />版式</TabsTrigger>
              <TabsTrigger value="body"><FileText className="w-4 h-4 mr-1" />正文</TabsTrigger>
              <TabsTrigger value="preview"><RefreshCw className="w-4 h-4 mr-1" />预览</TabsTrigger>
            </TabsList>

            <TabsContent value="layout" className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">
                  这份模板要求生成出来的报告保留下列章节与图表（数值一律取本轮查询结果）。
                </p>
                <Button variant="outline" size="sm" className="h-8 text-xs" onClick={extractFromBody} disabled={analyzing}>
                  {analyzing ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Wand2 className="h-3.5 w-3.5 mr-1" />}
                  从正文抽版式
                </Button>
              </div>

              <div className="space-y-2 border rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <Label className="text-xs">章节标题（按顺序逐字保留）</Label>
                  <Button variant="ghost" size="sm" className="h-7 text-xs"
                    onClick={() => { setLayoutEdited(true); setLayout(p => ({ ...p, headings: [...p.headings, ''] })); }}>
                    <Plus className="h-3.5 w-3.5 mr-1" />加章节
                  </Button>
                </div>
                {!layout.headings.length && <p className="text-xs text-muted-foreground">还没有章节</p>}
                {layout.headings.map((h, i) => (
                  <div key={`h-${i}`} className="flex items-center gap-2">
                    <span className="w-5 shrink-0 text-xs text-muted-foreground">{i + 1}</span>
                    <Input value={h} placeholder="如：核心结论"
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
                  <Label className="text-xs">图表规格（类型 + 列名不得改，只换数据）</Label>
                  <Button variant="ghost" size="sm" className="h-7 text-xs"
                    onClick={() => {
                      setLayoutEdited(true);
                      setLayout(p => ({ ...p, charts: [...p.charts, { title: '', chart_type: '', columns: '' }] }));
                    }}>
                    <Plus className="h-3.5 w-3.5 mr-1" />加图表
                  </Button>
                </div>
                {!layout.charts.length && <p className="text-xs text-muted-foreground">还没有图表规格</p>}
                {layout.charts.map((ch, i) => (
                  <div key={`c-${i}`} className="space-y-1.5 rounded-md border border-dashed p-2">
                    <div className="flex items-center gap-2">
                      <Input className="h-8 text-xs" value={ch.title} placeholder="图表标题（可选）"
                        onChange={e => {
                          setLayoutEdited(true);
                          setLayout(p => ({ ...p, charts: p.charts.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)) }));
                        }} />
                      <Select value={ch.chart_type || undefined} onValueChange={v => {
                        setLayoutEdited(true);
                        setLayout(p => ({ ...p, charts: p.charts.map((x, j) => (j === i ? { ...x, chart_type: v } : x)) }));
                      }}>
                        <SelectTrigger className="w-[150px] h-8 text-xs"><SelectValue placeholder="图表类型" /></SelectTrigger>
                        <SelectContent>
                          {CHART_TYPES.map(t => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
                        </SelectContent>
                      </Select>
                      <Button variant="ghost" size="sm" className="h-8 text-destructive"
                        onClick={() => { setLayoutEdited(true); setLayout(p => ({ ...p, charts: p.charts.filter((_, j) => j !== i) })); }}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                    <Input className="h-8 text-xs" value={ch.columns} placeholder="列名，逗号分隔，如：省份, PV"
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
                正文写结构（章节标题 + ```chart 块）后点「从正文抽版式」。历史模板的 Jinja2 正文同样会被抽成版式骨架，无需重写。
              </p>
              <Textarea value={content} onChange={e => setContent(e.target.value)}
                className="font-mono text-sm min-h-[360px]"
                placeholder={format === 'html' ? '<h2>章节标题</h2><p>…</p>' : '# 报告标题\n\n## 核心结论\n…'} />
            </TabsContent>

            <TabsContent value="preview" className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  预览与报告中心同一渲染管线，样式按「{themeName(theme === THEME_NONE ? currentTheme : theme)}」主题实渲（字模库）；
                  图里的「示例甲/乙/丙」只为看样式，模板不保存数值。
                </p>
                <div className="flex items-center gap-2 shrink-0">
                  {theme !== THEME_NONE && isPreviewableTheme(theme) && currentTheme !== theme && (
                    <Button variant="outline" size="sm" className="h-8 text-xs" onClick={() => setCurrentTheme(theme)}>
                      <Palette className="h-3.5 w-3.5 mr-1" />按「{themeName(theme)}」预览
                    </Button>
                  )}
                  <Button variant="outline" size="sm" className="h-8 text-xs" onClick={showPreview} disabled={analyzing}>
                    <RefreshCw className={`h-3.5 w-3.5 mr-1 ${analyzing ? 'animate-spin' : ''}`} />重算预览
                  </Button>
                </div>
              </div>
              {preview && !preview.hasLayout && (
                <p role="alert" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                  这份版式约束不了任何东西：没有章节也没有图表规格。这样保存的模板在任务里看得见却不生效，请先补齐。
                </p>
              )}
              {/* HTML 预览是整份 iframe（自带 78vh 高度与内部滚动）：外层再套 max-h 就变成双重裁切，
                  只露出上半截还滚不动 —— 按格式分流，Markdown 才用外层滚动容器 */}
              <div className={format === 'html'
                ? 'border rounded-lg p-3 bg-card'
                : 'border rounded-lg p-3 bg-card max-h-[460px] overflow-y-auto'}>
                {preview
                  ? <ReportContent content={preview.skeleton} format={format} />
                  : <p className="text-sm text-muted-foreground">点「重算预览」按当前版式生成骨架。</p>}
              </div>
            </TabsContent>
          </Tabs>

          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>取消</Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? <><Loader2 className="h-4 w-4 mr-1 animate-spin" />保存中…</> : editRow ? '更新' : '创建'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>下架模板?</DialogTitle><DialogDescription>
            下架后任务表单与报告中心不再能选到它；已引用它的任务下次执行会显式失败（不静默换成通用版式）。
          </DialogDescription></DialogHeader>
          <p className="text-sm text-muted-foreground">将「{deleteTarget?.name}」下架（软删除）。</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={handleDelete}>确认下架</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
