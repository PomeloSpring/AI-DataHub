// 报表中心: 报表跟随属主(跨工作站), 支持管理「不一定是定时任务产出」的报表。
// 与资产清单的分工: 资产=归档的文件(下载复用), 报表=稳定内容(在线实时查看)。
// 归集口径: 数据派生报表(scheduled/analysis)按任务归集并切换历史版本;
//          内容型报表(manual/conversation/legacy)一条一报表, 可新建/重命名/编辑/删除。
import { useState, useEffect, useMemo, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import {
  FileBarChart, Loader2, Eye, Inbox, Plus, Pencil, Trash2, Search, MessageSquareText,
} from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogDescription,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import ReportContent from '@/components/ReportContent';
import {
  listReports, getReportDetail, createReport, updateReport, deleteReport,
  isEditableReport, isDeletableReport,
  type ReportSummary, type ReportDetail, type ReportSourceType,
} from '@/api/report';

/** 归集后的一组报表(版本按时间倒序, [0] 为最新) */
interface Series {
  key: string;
  name: string;
  source: ReportSourceType;
  editable: boolean;
  deletable: boolean;
  reports: ReportSummary[];
}

/** 编辑器状态: create=新建内容型报表, edit=重命名/改正文 */
interface EditorState {
  open: boolean;
  mode: 'create' | 'edit';
  reportId: number;
  title: string;
  content: string;
  format: string;
  saving: boolean;
}

const SOURCE_META: Record<string, { label: string; cls: string }> = {
  scheduled: { label: '定时任务', cls: 'text-blue-600 border-blue-200' },
  analysis: { label: '分析生成', cls: 'text-indigo-600 border-indigo-200' },
  manual: { label: '手动新建', cls: 'text-emerald-600 border-emerald-200' },
  conversation: { label: '会话保存', cls: 'text-teal-600 border-teal-200' },
  legacy: { label: '历史报表', cls: 'text-amber-600 border-amber-200' },
};
const SOURCE_ORDER: ReportSourceType[] = ['scheduled', 'analysis', 'conversation', 'manual', 'legacy'];

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** 任务报表标题形如「任务名 - 2026-07-05」, 归集名只留任务名(显示用 name) */
function seriesName(r: ReportSummary): string {
  if (r.task_name) return r.task_name;
  return (r.title || '').replace(/\s*-\s*\d{4}-\d{2}-\d{2}.*$/, '').trim() || '未命名报表';
}

/** 归集键: 可编辑的内容型一条即一份报表; 其余按任务(或同标题)归集为多版本 */
function editableKey(r: ReportSummary): string {
  if (isEditableReport(r)) return `report:${r.id}`;
  return r.task_id ? `task:${r.task_id}` : `title:${r.title}`;
}

// 生成状态真实映射：degraded 是 M2 降级事实表，绝不等同 ready 证据化报告。
const GEN_STATUS: Record<string, { label: string; cls: string }> = {
  queued: { label: '排队中', cls: 'text-blue-500' },
  running: { label: '生成中', cls: 'text-blue-500' },
  ready: { label: '已就绪', cls: 'text-green-500' },
  degraded: { label: '降级事实报告', cls: 'text-amber-500' },
  failed: { label: '生成失败', cls: 'text-red-500' },
  timeout: { label: '生成超时', cls: 'text-yellow-500' },
  legacy: { label: '历史报表', cls: 'text-amber-500' },
};
const PENDING = ['queued', 'running'];
const NO_BODY = ['queued', 'running', 'failed', 'timeout'];

const EMPTY_EDITOR: EditorState = {
  open: false, mode: 'create', reportId: 0, title: '', content: '', format: 'markdown', saving: false,
};

export default function ReportsCenter() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const wsId = Number(workspaceId || 0);

  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeKey, setActiveKey] = useState<string>('');
  const [activeId, setActiveId] = useState<number>(0);
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [scopeWs, setScopeWs] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<string>('all');
  const [keyword, setKeyword] = useState('');
  const [editor, setEditor] = useState<EditorState>(EMPTY_EDITOR);

  const reload = useCallback(() =>
    // 报表跟随属主: 默认跨工作站(0=全部), 只在看本工作站时收窄
    listReports(scopeWs ? wsId : 0)
      .then(data => setReports(data || []))
      .catch(e => setError(e?.response?.data?.detail || e?.message || '加载报表清单失败')),
  [scopeWs, wsId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    listReports(scopeWs ? wsId : 0)
      .then(data => { if (!cancelled) setReports(data || []); })
      .catch(e => {
        if (!cancelled) setError(e?.response?.data?.detail || e?.message || '加载报表清单失败');
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [wsId, scopeWs]);

  // 按报表归集(list 已时间倒序, 组内顺序即版本顺序)
  const series = useMemo<Series[]>(() => {
    const map = new Map<string, Series>();
    reports.forEach(r => {
      const source = (r.source_type || 'legacy') as ReportSourceType;
      // 内容型一条即一份报表(编辑就地更新); 数据派生按任务归集为多版本
      const key = editableKey(r);
      if (!map.has(key)) {
        map.set(key, { key, name: seriesName(r), source,
                       editable: isEditableReport(r), deletable: isDeletableReport(r),
                       reports: [] });
      }
      map.get(key)!.reports.push(r);
    });
    const kw = keyword.trim().toLowerCase();
    return Array.from(map.values()).filter(s =>
      (sourceFilter === 'all' || s.source === sourceFilter)
      && (!kw || s.name.toLowerCase().includes(kw)));
  }, [reports, sourceFilter, keyword]);

  // 默认选中最新的报表组及其最新版本
  useEffect(() => {
    if (!series.length) return;
    if (!activeKey || !series.some(s => s.key === activeKey)) {
      setActiveKey(series[0].key);
      setActiveId(series[0].reports[0].id);
    }
  }, [series, activeKey]);

  const loadDetail = useCallback((id: number) => {
    if (!id) return;
    setDetailLoading(true);
    getReportDetail(id)
      .then(data => { setDetail(data); setError(data.access_warning || ''); })
      .catch(e => {
        setDetail(null);
        setError(e?.response?.data?.detail || '加载报表内容失败');
      })
      .finally(() => setDetailLoading(false));
  }, []);

  useEffect(() => {
    if (activeId) loadDetail(activeId);
  }, [activeId, loadDetail]);

  // 生成中的报表自动轮询，直到进入终态
  useEffect(() => {
    if (!detail || !PENDING.includes(detail.generation_status || '')) return;
    const timer = setTimeout(() => loadDetail(detail.id), 2000);
    return () => clearTimeout(timer);
  }, [detail, loadDetail]);

  const activeSeries = series.find(s => s.key === activeKey) || null;

  const pickSeries = (s: Series) => {
    setActiveKey(s.key);
    setActiveId(s.reports[0]?.id ?? 0);
    setError('');
  };

  const openCreate = () => setEditor({ ...EMPTY_EDITOR, open: true });

  const openEdit = (r: ReportDetail) => setEditor({
    open: true, mode: 'edit', reportId: r.id, title: r.title || '',
    content: r.content || '', format: r.format || 'markdown', saving: false,
  });

  /** 列表不带正文, 编辑前先取那一份报表的详情(同时选中它) */
  const editSeries = async (s: Series) => {
    try {
      const d = await getReportDetail(s.reports[0].id);
      setActiveKey(s.key);
      setActiveId(d.id);
      openEdit(d);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '读取报表正文失败，请重试');
    }
  };

  const saveEditor = async () => {
    if (!editor.title.trim()) { toast.error('报表标题不能为空'); return; }
    if (!editor.content.trim()) { toast.error('报表正文不能为空'); return; }
    setEditor(prev => ({ ...prev, saving: true }));
    try {
      if (editor.mode === 'create') {
        const { report_id } = await createReport({
          workspace_id: wsId, title: editor.title, content: editor.content, format: editor.format,
        });
        toast.success('报表已创建');
        await reload();
        setActiveKey(`report:${report_id}`);
        setActiveId(report_id);
      } else {
        await updateReport(editor.reportId, {
          title: editor.title, content: editor.content, format: editor.format,
        });
        toast.success('报表已更新');
        await reload();
        loadDetail(editor.reportId);
      }
      setEditor(EMPTY_EDITOR);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '保存失败，请重试');
      setEditor(prev => ({ ...prev, saving: false }));
    }
  };

  const removeReport = async (r: ReportSummary) => {
    if (!window.confirm(`确定删除报表「${seriesName(r)}」？删除后不可恢复。`)) return;
    try {
      await deleteReport(r.id);
      toast.success('报表已删除');
      if (activeId === r.id) { setActiveKey(''); setActiveId(0); setDetail(null); }
      await reload();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '删除失败，请重试');
    }
  };

  return (
    <div className="h-full flex flex-col gap-4 p-6 overflow-hidden">
      <div className="flex items-center gap-2 shrink-0 flex-wrap">
        <FileBarChart className="h-5 w-5 text-primary" />
        <h1 className="text-lg font-semibold">报表中心</h1>
        <span className="text-sm text-muted-foreground">
          报表跟随账号，在线实时查看；任务产出的报表按版本归集，会话/手写的报表可直接新建与编辑
        </span>
        <Button size="sm" className="h-8 ml-auto" onClick={openCreate}>
          <Plus className="h-4 w-4 mr-1" /> 新建报表
        </Button>
      </div>

      {/* 筛选行: 范围 / 来源 / 关键词 */}
      <div className="flex items-center gap-2 shrink-0 flex-wrap">
        <Select value={scopeWs ? 'ws' : 'all'} onValueChange={v => setScopeWs(v === 'ws')}>
          <SelectTrigger className="w-[150px] h-8 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部工作站</SelectItem>
            <SelectItem value="ws">本工作站</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sourceFilter} onValueChange={setSourceFilter}>
          <SelectTrigger className="w-[140px] h-8 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部来源</SelectItem>
            {SOURCE_ORDER.map(s => (
              <SelectItem key={s} value={s}>{SOURCE_META[s].label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input value={keyword} onChange={e => setKeyword(e.target.value)}
            placeholder="按报表名称搜索" className="h-8 w-56 pl-7 text-xs" />
        </div>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载中...
        </div>
      ) : !series.length ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-muted-foreground border border-dashed rounded-lg bg-card">
          <Inbox className="h-10 w-10 opacity-50" />
          <p className="text-sm">{error || '暂无报表：可在智能问数里把分析结果「存为报表」，或点右上角新建报表'}</p>
        </div>
      ) : (
        <div className="flex-1 flex gap-4 min-h-0">
          {/* 左栏: 报表清单(任务型按版本归集) */}
          <div className="w-72 shrink-0 overflow-y-auto border rounded-lg bg-card">
            {series.map(s => {
              const latest = s.reports[0];
              const active = s.key === activeKey;
              return (
                <div key={s.key}
                  className={`w-full px-3 py-2.5 border-b last:border-b-0 transition-colors ${
                    active ? 'bg-primary/10 border-l-2 border-l-primary' : 'hover:bg-muted/50'
                  }`}>
                  <div className="flex items-center gap-2">
                    <button onClick={() => pickSeries(s)} className="flex-1 min-w-0 text-left">
                      <span className={`block text-sm font-medium truncate ${active ? 'text-primary' : ''}`}>
                        {s.name}
                      </span>
                    </button>
                    {s.reports.length > 1 && (
                      <Badge variant="outline" className="shrink-0 text-[10px]">
                        {s.reports.length} 个版本
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <Badge variant="outline" className={`text-[10px] ${SOURCE_META[s.source]?.cls || ''}`}>
                      {SOURCE_META[s.source]?.label || s.source}
                    </Badge>
                    <span className="text-xs text-muted-foreground truncate">
                      最新 {latest ? new Date(latest.created_at).toLocaleDateString() : '-'}
                    </span>
                    {s.editable && (
                      <Button variant="ghost" size="sm" className="h-6 w-6 p-0 ml-auto" title="重命名 / 编辑正文"
                        onClick={() => void editSeries(s)}>
                        <Pencil className="h-3 w-3" />
                      </Button>
                    )}
                    {s.deletable && (
                      <Button variant="ghost" size="sm"
                        className={`h-6 w-6 p-0 text-destructive ${s.editable ? '' : 'ml-auto'}`} title="删除报表"
                        onClick={() => removeReport(latest)}>
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* 右栏: 报表详情 */}
          <div className="flex-1 min-w-0 flex flex-col border rounded-lg bg-card overflow-hidden">
            <div className="shrink-0 px-5 py-3 border-b flex items-center justify-between gap-4">
              <div className="min-w-0">
                <h2 className="text-sm font-semibold truncate">{detail?.title || activeSeries?.name || '选择报表'}</h2>
                <div className="flex items-center gap-2 mt-0.5 text-xs text-muted-foreground">
                  {detail && (
                    <>
                      <Badge variant="outline" className="text-[10px]">
                        {detail.format === 'html' ? 'HTML' : 'Markdown'}
                      </Badge>
                      <Badge variant="outline" className={`text-[10px] ${SOURCE_META[detail.source_type || 'legacy']?.cls || ''}`}>
                        {SOURCE_META[detail.source_type || 'legacy']?.label || detail.source_type}
                      </Badge>
                      {detail.generation_status && GEN_STATUS[detail.generation_status] && (
                        <Badge variant="outline" className={`text-[10px] ${GEN_STATUS[detail.generation_status].cls}`}>
                          {GEN_STATUS[detail.generation_status].label}
                        </Badge>
                      )}
                      <span>{formatDate(detail.created_at)}</span>
                      <span className="flex items-center gap-1">
                        <Eye className="h-3 w-3" /> {detail.view_count}
                      </span>
                      {detail.origin?.channel === 'chat' && !!detail.origin?.conversation_id && (
                        <span className="flex items-center gap-1" title={`来自会话 #${detail.origin.conversation_id}`}>
                          <MessageSquareText className="h-3 w-3" />
                          {detail.origin.conversation_title || '来自会话'}
                        </span>
                      )}
                    </>
                  )}
                </div>
              </div>
              <div className="shrink-0 flex items-center gap-2">
                {detail && isEditableReport(detail) && (
                  <Button variant="outline" size="sm" className="h-8 text-xs" onClick={() => openEdit(detail)}>
                    <Pencil className="h-3 w-3 mr-1" /> 编辑
                  </Button>
                )}
                {detail && isDeletableReport(detail) && (
                  <Button variant="outline" size="sm" className="h-8 text-xs" onClick={() => removeReport(detail)}>
                    <Trash2 className="h-3 w-3 mr-1" /> 删除
                  </Button>
                )}
                {/* 版本管理: 所选任务的历史报表(时间倒序) */}
                {activeSeries && activeSeries.reports.length > 0 && (
                  <select
                    value={activeId}
                    onChange={e => setActiveId(Number(e.target.value))}
                    className="shrink-0 h-8 rounded-md border bg-background px-2 text-xs"
                    title="版本管理"
                  >
                    {activeSeries.reports.map((r, i) => (
                      <option key={r.id} value={r.id}>
                        v{activeSeries.reports.length - i} · {formatDate(r.created_at)}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            </div>

            <div className="flex-1 overflow-y-auto px-6 py-5">
              {error ? (
                <p className="text-sm text-destructive">{error}</p>
              ) : detailLoading || !detail ? (
                <div className="h-full flex items-center justify-center text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载报表内容...
                </div>
              ) : NO_BODY.includes(detail.generation_status || '') ? (
                <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
                  {PENDING.includes(detail.generation_status || '')
                    ? <><Loader2 className="h-4 w-4 animate-spin mr-2" /> 报表生成中，页面将自动刷新...</>
                    : '本次运行未生成可展示的报告正文（生成失败或超时）。'}
                </div>
              ) : (
                <>
                  {detail.generation_status === 'degraded' && (
                    <p className="mb-3 text-xs text-amber-600">
                      降级事实报告：仅展示已授权查询返回的样本，尚未完成证据校验，不可对外发布，不代表总体。
                    </p>
                  )}
                  <ReportContent content={detail.content || ''} format={detail.format} />
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 新建 / 编辑报表 */}
      <Dialog open={editor.open} onOpenChange={o => { if (!o) setEditor(EMPTY_EDITOR); }}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{editor.mode === 'create' ? '新建报表' : '编辑报表'}</DialogTitle>
            <DialogDescription>
              内容型报表仅本人可见、可在线查看与再次编辑；定时任务/分析来源产出的报表正文由受治理取数生成，不在此编辑。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="report-title">报表名称</Label>
              <Input id="report-title" value={editor.title}
                onChange={e => setEditor(prev => ({ ...prev, title: e.target.value }))}
                placeholder="业务可读名称，如「新加坡近30天新增案例」" />
            </div>
            <div className="flex items-center gap-3">
              <Label className="shrink-0">正文格式</Label>
              <Select value={editor.format}
                onValueChange={v => setEditor(prev => ({ ...prev, format: v }))}>
                <SelectTrigger className="w-[160px] h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="markdown">Markdown</SelectItem>
                  <SelectItem value="html">HTML</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="report-content">报表正文</Label>
              <Textarea id="report-content" value={editor.content}
                onChange={e => setEditor(prev => ({ ...prev, content: e.target.value }))}
                placeholder={editor.format === 'html' ? '<h1>报表标题</h1><p>正文…</p>'
                  : '# 报表标题\n\n正文支持 Markdown 与表格'}
                className="font-mono text-xs min-h-[320px]" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditor(EMPTY_EDITOR)} disabled={editor.saving}>取消</Button>
            <Button onClick={saveEditor} disabled={editor.saving}>
              {editor.saving ? <><Loader2 className="h-4 w-4 mr-1 animate-spin" />保存中…</> : '保存'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
