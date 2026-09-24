// 报表中心: 自动化任务成功执行生成的报告, 按任务归集 + 右上角版本切换查看历史
import { useState, useEffect, useMemo, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { FileBarChart, Loader2, Eye, Inbox } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import ReportContent from '@/components/ReportContent';
import {
  listReports, getReportDetail,
  type ReportSummary, type ReportDetail,
} from '@/api/scheduledTask';

/** 按任务归集后的一组报告(版本按时间倒序, [0] 为最新) */
interface TaskGroup {
  key: string;
  taskName: string;
  reports: ReportSummary[];
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

// 生成状态真实映射：degraded 是 M2 降级事实表，绝不等同 ready 证据化报告。
const GEN_STATUS: Record<string, { label: string; cls: string }> = {
  queued: { label: '排队中', cls: 'text-blue-500' },
  running: { label: '生成中', cls: 'text-blue-500' },
  ready: { label: '已就绪', cls: 'text-green-500' },
  degraded: { label: '降级事实报告', cls: 'text-amber-500' },
  failed: { label: '生成失败', cls: 'text-red-500' },
  timeout: { label: '生成超时', cls: 'text-yellow-500' },
};
const PENDING = ['queued', 'running'];
const NO_BODY = ['queued', 'running', 'failed', 'timeout'];

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

  // 摘要清单: 后端已按生成时间倒序
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    listReports(wsId || undefined)
      .then(data => { if (!cancelled) setReports(data || []); })
      .catch(e => { if (!cancelled) setError(e?.response?.data?.detail || '加载报告列表失败'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [wsId]);

  // 按任务名归集(list 已时间倒序, 组内顺序即版本顺序)
  const groups = useMemo<TaskGroup[]>(() => {
    const map = new Map<string, TaskGroup>();
    reports.forEach(r => {
      const key = String(r.task_id || r.task_name || r.title);
      if (!map.has(key)) {
        map.set(key, { key, taskName: r.task_name || r.title || '未命名任务', reports: [] });
      }
      map.get(key)!.reports.push(r);
    });
    return Array.from(map.values());
  }, [reports]);

  // 默认选中最新的任务组及其最新版本
  useEffect(() => {
    if (!groups.length) return;
    if (!activeKey || !groups.some(g => g.key === activeKey)) {
      setActiveKey(groups[0].key);
      setActiveId(groups[0].reports[0].id);
    }
  }, [groups, activeKey]);

  const loadDetail = useCallback((id: number) => {
    if (!id) return;
    setDetailLoading(true);
    getReportDetail(id)
      .then(data => { setDetail(data); setError(data.access_warning || ''); })
      .catch(e => {
        setDetail(null);
        setError(e?.response?.data?.detail || '加载报告内容失败');
      })
      .finally(() => setDetailLoading(false));
  }, []);

  useEffect(() => {
    if (activeId) loadDetail(activeId);
  }, [activeId, loadDetail]);

  // 生成中的报告自动轮询，直到进入终态
  useEffect(() => {
    if (!detail || !PENDING.includes(detail.generation_status || '')) return;
    const timer = setTimeout(() => loadDetail(detail.id), 2000);
    return () => clearTimeout(timer);
  }, [detail, loadDetail]);

  const activeGroup = groups.find(g => g.key === activeKey) || null;

  const pickTask = (g: TaskGroup) => {
    setActiveKey(g.key);
    setActiveId(g.reports[0]?.id ?? 0);
    setError('');
  };

  return (
    <div className="h-full flex flex-col gap-4 p-6 overflow-hidden">
      <div className="flex items-center gap-2 shrink-0">
        <FileBarChart className="h-5 w-5 text-primary" />
        <h1 className="text-lg font-semibold">报表中心</h1>
        <span className="text-sm text-muted-foreground">自动化任务生成的报告，按任务归集，可切换历史版本</span>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载中...
        </div>
      ) : !groups.length ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-muted-foreground border border-dashed rounded-lg bg-card">
          <Inbox className="h-10 w-10 opacity-50" />
          <p className="text-sm">{error || '暂无报表：自动化任务成功执行后会自动生成报告'}</p>
        </div>
      ) : (
        <div className="flex-1 flex gap-4 min-h-0">
          {/* 左栏: 任务分组列表 */}
          <div className="w-72 shrink-0 overflow-y-auto border rounded-lg bg-card">
            {groups.map(g => {
              const latest = g.reports[0];
              const active = g.key === activeKey;
              return (
                <button
                  key={g.key}
                  onClick={() => pickTask(g)}
                  className={`w-full text-left px-4 py-3 border-b last:border-b-0 transition-colors ${
                    active ? 'bg-primary/10 border-l-2 border-l-primary' : 'hover:bg-muted/50'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className={`text-sm font-medium truncate ${active ? 'text-primary' : ''}`}>
                      {g.taskName}
                    </span>
                    {g.reports.length > 1 && (
                      <Badge variant="outline" className="shrink-0 text-[10px]">
                        {g.reports.length} 个版本
                      </Badge>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1">
                    最新 {latest ? new Date(latest.created_at).toLocaleDateString() : '-'}
                  </div>
                </button>
              );
            })}
          </div>

          {/* 右栏: 报告详情 */}
          <div className="flex-1 min-w-0 flex flex-col border rounded-lg bg-card overflow-hidden">
            <div className="shrink-0 px-5 py-3 border-b flex items-center justify-between gap-4">
              <div className="min-w-0">
                <h2 className="text-sm font-semibold truncate">{detail?.title || activeGroup?.taskName || '选择报告'}</h2>
                <div className="flex items-center gap-2 mt-0.5 text-xs text-muted-foreground">
                  {detail && (
                    <>
                      <Badge variant="outline" className="text-[10px]">
                        {detail.format === 'html' ? 'HTML' : 'Markdown'}
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
                    </>
                  )}
                </div>
              </div>
              {/* 版本管理: 所选任务的历史报告(时间倒序) */}
              {activeGroup && activeGroup.reports.length > 0 && (
                <select
                  value={activeId}
                  onChange={e => setActiveId(Number(e.target.value))}
                  className="shrink-0 h-8 rounded-md border bg-background px-2 text-xs"
                  title="版本管理"
                >
                  {activeGroup.reports.map((r, i) => (
                    <option key={r.id} value={r.id}>
                      v{activeGroup.reports.length - i} · {formatDate(r.created_at)}
                    </option>
                  ))}
                </select>
              )}
            </div>

            <div className="flex-1 overflow-y-auto px-6 py-5">
              {error ? (
                <p className="text-sm text-destructive">{error}</p>
              ) : detailLoading || !detail ? (
                <div className="h-full flex items-center justify-center text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" /> 加载报告内容...
                </div>
              ) : NO_BODY.includes(detail.generation_status || '') ? (
                <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
                  {PENDING.includes(detail.generation_status || '')
                    ? <><Loader2 className="h-4 w-4 animate-spin mr-2" /> 报告生成中，页面将自动刷新...</>
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
    </div>
  );
}
