import { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';
import {
  BarChart3, Star, MessageSquare, RefreshCw, ThumbsUp, ThumbsDown,
  CheckCircle, XCircle, AlertCircle, Clock, Zap,
} from 'lucide-react';
import client from '@/api/client';

interface QualityReview {
  id: number;
  workspace_id: number;
  user_id: number;
  username: string;
  question: string;
  generated_sql: string;
  datasource_id: number;
  execution_status: 'success' | 'error' | 'empty';
  row_count: number;
  elapsed_ms: number;
  retry_count: number;
  pipeline_mode: string;
  user_feedback: number | null;
  llm_review: string;
  llm_reviewed_at: string;
  status: string;
  created_at: string;
}

interface QualityStats {
  total: number;
  success_count: number;
  error_count: number;
  empty_count: number;
  success_rate: number;
  avg_elapsed_ms: number;
  avg_row_count: number;
  avg_retry_count: number;
  thumbs_up: number;
  thumbs_down: number;
  satisfaction_rate: number;
  llm_reviewed: number;
  by_mode: Array<{ pipeline_mode: string; cnt: number; success: number; avg_ms: number }>;
  daily_trend: Array<{ dt: string; cnt: number; success: number }>;
}

const STATUS_CONFIG: Record<string, { label: string; icon: any; color: string }> = {
  success: { label: '成功', icon: CheckCircle, color: 'text-green-600 bg-green-50' },
  error: { label: '失败', icon: XCircle, color: 'text-red-600 bg-red-50' },
  empty: { label: '空结果', icon: AlertCircle, color: 'text-yellow-600 bg-yellow-50' },
};

const MODE_LABELS: Record<string, string> = {
  quick: '快速模式',
  deep: '深度模式',
  agent: 'Agent 模式',
};

function StatusBadge({ status }: { status: string }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.success;
  const Icon = config.icon;
  return (
    <Badge className={config.color}>
      <Icon className="h-3 w-3 mr-1" />
      {config.label}
    </Badge>
  );
}

export default function QualityReview({ workspaceId }: { workspaceId?: number } = {}) {
  const [reviews, setReviews] = useState<QualityReview[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState('');
  const [modeFilter, setModeFilter] = useState('');
  const [loading, setLoading] = useState(false);
  const [selectedReview, setSelectedReview] = useState<QualityReview | null>(null);
  const [stats, setStats] = useState<QualityStats | null>(null);
  const [llmReviewing, setLlmReviewing] = useState(false);

  // Workspace filter (only for system-level page where workspaceId is undefined)
  const [wsFilter, setWsFilter] = useState<number>(workspaceId ?? 0);
  const [workspaces, setWorkspaces] = useState<Array<{ id: number; name: string }>>([]);
  const isSystemLevel = workspaceId === undefined;

  // Load workspaces list (system-level only)
  useEffect(() => {
    if (isSystemLevel) {
      client.get('/workspaces/').then(({ data }) => {
        setWorkspaces(Array.isArray(data) ? data : []);
      }).catch(() => {});
    }
  }, [isSystemLevel]);

  const effectiveWsId = isSystemLevel ? wsFilter : (workspaceId ?? 0);

  const loadReviews = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = { workspace_id: effectiveWsId, page, size: 20 };
      if (statusFilter) params.execution_status = statusFilter;
      if (modeFilter) params.pipeline_mode = modeFilter;
      const { data } = await client.get('/admin/quality-reviews', { params });
      setReviews(data.items || []);
      setTotal(data.total || 0);
    } catch {
      toast.error('加载评审列表失败');
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter, modeFilter, effectiveWsId]);

  const loadStats = useCallback(async () => {
    try {
      const { data } = await client.get('/admin/quality-stats', { params: { workspace_id: effectiveWsId } });
      setStats(data);
    } catch {
      // ignore
    }
  }, [effectiveWsId]);

  useEffect(() => { loadReviews(); }, [loadReviews]);
  useEffect(() => { loadStats(); }, [loadStats]);

  const openDetail = async (review: QualityReview) => {
    setSelectedReview(review);
  };

  const handleLlmReview = async () => {
    if (!selectedReview) return;
    setLlmReviewing(true);
    try {
      const { data } = await client.post(`/admin/quality-reviews/${selectedReview.id}/llm-review`);
      toast.success('AI 评审完成');
      setSelectedReview(prev => prev ? { ...prev, llm_review: data.analysis } : null);
      loadReviews();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || 'AI 评审失败');
    } finally {
      setLlmReviewing(false);
    }
  };

  const formatMs = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  return (
    <div className="h-full overflow-auto">
      <h1 className="text-2xl font-bold mb-6 flex items-center gap-2">
        <Star className="h-6 w-6" />
        质量评审
      </h1>

      <Tabs defaultValue="list">
        <TabsList>
          <TabsTrigger value="list">
            <MessageSquare className="h-4 w-4 mr-2" />
            评审列表
          </TabsTrigger>
          <TabsTrigger value="stats">
            <BarChart3 className="h-4 w-4 mr-2" />
            统计概览
          </TabsTrigger>
        </TabsList>

        {/* ── Reviews List Tab ──────────────────────────────────── */}
        <TabsContent value="list">
          <div className="flex justify-between items-center mb-4">
            <div className="flex items-center gap-2">
              {isSystemLevel && (
                <Select value={String(wsFilter)} onValueChange={v => { setWsFilter(Number(v)); setPage(1); }}>
                  <SelectTrigger className="w-40">
                    <SelectValue placeholder="全部工作空间" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="0">全部工作空间</SelectItem>
                    {workspaces.map(ws => (
                      <SelectItem key={ws.id} value={String(ws.id)}>{ws.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
              <Select value={statusFilter} onValueChange={v => { setStatusFilter(v); setPage(1); }}>
                <SelectTrigger className="w-32">
                  <SelectValue placeholder="执行状态" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">全部状态</SelectItem>
                  <SelectItem value="success">成功</SelectItem>
                  <SelectItem value="error">失败</SelectItem>
                  <SelectItem value="empty">空结果</SelectItem>
                </SelectContent>
              </Select>
              <Select value={modeFilter} onValueChange={v => { setModeFilter(v); setPage(1); }}>
                <SelectTrigger className="w-32">
                  <SelectValue placeholder="查询模式" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">全部模式</SelectItem>
                  <SelectItem value="quick">快速模式</SelectItem>
                  <SelectItem value="deep">深度模式</SelectItem>
                  <SelectItem value="agent">Agent 模式</SelectItem>
                </SelectContent>
              </Select>
              <span className="text-sm text-muted-foreground">共 {total} 条</span>
            </div>
          </div>

          {loading ? (
            <div className="text-center py-8 text-muted-foreground">加载中...</div>
          ) : reviews.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">暂无评审记录</div>
          ) : (
            <div className="space-y-2">
              {reviews.map(review => (
                <div
                  key={review.id}
                  className="border rounded-lg p-3 cursor-pointer hover:bg-muted/50 transition-colors"
                  onClick={() => openDetail(review)}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-medium text-sm truncate">{review.question}</span>
                        <StatusBadge status={review.execution_status} />
                        {review.pipeline_mode && (
                          <Badge variant="outline" className="text-xs">
                            {MODE_LABELS[review.pipeline_mode] || review.pipeline_mode}
                          </Badge>
                        )}
                      </div>
                      <div className="flex items-center gap-4 text-xs text-muted-foreground">
                        <span>{review.username}</span>
                        <span className="flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {formatMs(review.elapsed_ms)}
                        </span>
                        <span>{review.row_count} 行</span>
                        {review.retry_count > 0 && (
                          <span className="text-orange-600">重试 {review.retry_count} 次</span>
                        )}
                        <span>{review.created_at}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 ml-4">
                      {review.user_feedback === 1 && <ThumbsUp className="h-4 w-4 text-green-600" />}
                      {review.user_feedback === 0 && <ThumbsDown className="h-4 w-4 text-red-600" />}
                      {review.llm_review && <Badge variant="secondary" className="text-xs">AI 已评审</Badge>}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {total > 20 && (
            <div className="flex justify-center gap-2 mt-4">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
                上一页
              </Button>
              <span className="py-1 px-3 text-sm">{page} / {Math.ceil(total / 20)}</span>
              <Button variant="outline" size="sm" disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)}>
                下一页
              </Button>
            </div>
          )}
        </TabsContent>

        {/* ── Stats Tab ─────────────────────────────────────────── */}
        <TabsContent value="stats">
          {stats ? (
            <div className="grid grid-cols-2 gap-4">
              <div className="border rounded-lg p-4">
                <h3 className="font-medium mb-3">执行质量</h3>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span>总查询数</span>
                    <span className="font-mono font-medium">{stats.total}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>成功率</span>
                    <span className="font-mono text-green-600">{stats.success_rate}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span>成功</span>
                    <span className="font-mono">{stats.success_count}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>失败</span>
                    <span className="font-mono text-red-600">{stats.error_count}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>空结果</span>
                    <span className="font-mono text-yellow-600">{stats.empty_count}</span>
                  </div>
                </div>
              </div>
              <div className="border rounded-lg p-4">
                <h3 className="font-medium mb-3">性能指标</h3>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span>平均耗时</span>
                    <span className="font-mono">{formatMs(stats.avg_elapsed_ms)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>平均返回行数</span>
                    <span className="font-mono">{stats.avg_row_count}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>平均重试次数</span>
                    <span className="font-mono">{stats.avg_retry_count}</span>
                  </div>
                </div>
              </div>
              <div className="border rounded-lg p-4">
                <h3 className="font-medium mb-3">用户满意度</h3>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span>👍 满意</span>
                    <span className="font-mono text-green-600">{stats.thumbs_up}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>👎 不满意</span>
                    <span className="font-mono text-red-600">{stats.thumbs_down}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>满意率</span>
                    <span className="font-mono">{stats.satisfaction_rate}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span>AI 评审数</span>
                    <span className="font-mono">{stats.llm_reviewed}</span>
                  </div>
                </div>
              </div>
              <div className="border rounded-lg p-4">
                <h3 className="font-medium mb-3">按模式分布</h3>
                <div className="space-y-2">
                  {(stats.by_mode || []).map(m => (
                    <div key={m.pipeline_mode} className="flex items-center justify-between text-sm">
                      <span>{MODE_LABELS[m.pipeline_mode] || m.pipeline_mode || '未知'}</span>
                      <span className="text-muted-foreground">
                        {m.cnt} 次 · 成功 {m.success} · 平均 {formatMs(m.avg_ms || 0)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">加载中...</div>
          )}
        </TabsContent>
      </Tabs>

      {/* ── Review Detail Dialog ────────────────────────────────── */}
      <Dialog open={!!selectedReview} onOpenChange={() => setSelectedReview(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>查询详情</DialogTitle>
          </DialogHeader>
          {selectedReview && (
            <div className="space-y-4">
              <div>
                <Label>用户问题</Label>
                <p className="text-sm">{selectedReview.question}</p>
              </div>

              <div className="grid grid-cols-4 gap-3">
                <div className="text-center p-2 border rounded">
                  <p className="text-xs text-muted-foreground mb-1">执行状态</p>
                  <StatusBadge status={selectedReview.execution_status} />
                </div>
                <div className="text-center p-2 border rounded">
                  <p className="text-xs text-muted-foreground mb-1">返回行数</p>
                  <p className="font-mono font-medium">{selectedReview.row_count}</p>
                </div>
                <div className="text-center p-2 border rounded">
                  <p className="text-xs text-muted-foreground mb-1">耗时</p>
                  <p className="font-mono font-medium">{formatMs(selectedReview.elapsed_ms)}</p>
                </div>
                <div className="text-center p-2 border rounded">
                  <p className="text-xs text-muted-foreground mb-1">重试次数</p>
                  <p className="font-mono font-medium">{selectedReview.retry_count}</p>
                </div>
              </div>

              {selectedReview.generated_sql && (
                <div>
                  <Label>生成的 SQL</Label>
                  <pre className="text-xs bg-muted p-2 rounded overflow-auto mt-1 max-h-40">
                    {selectedReview.generated_sql}
                  </pre>
                </div>
              )}

              <div className="flex items-center gap-2">
                <Label>用户反馈:</Label>
                {selectedReview.user_feedback === 1 && <Badge className="bg-green-50 text-green-600">👍 满意</Badge>}
                {selectedReview.user_feedback === 0 && <Badge className="bg-red-50 text-red-600">👎 不满意</Badge>}
                {selectedReview.user_feedback === null && <Badge variant="outline">未反馈</Badge>}
              </div>

              {selectedReview.llm_review && (
                <div>
                  <Label>AI 评审意见</Label>
                  <p className="text-sm text-muted-foreground mt-1 p-3 bg-muted/50 rounded">
                    {selectedReview.llm_review}
                  </p>
                  {selectedReview.llm_reviewed_at && (
                    <p className="text-xs text-muted-foreground mt-1">
                      评审时间: {selectedReview.llm_reviewed_at}
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setSelectedReview(null)}>关闭</Button>
            {selectedReview && !selectedReview.llm_review && (
              <Button onClick={handleLlmReview} disabled={llmReviewing}>
                {llmReviewing ? (
                  <><RefreshCw className="h-4 w-4 mr-2 animate-spin" />评审中...</>
                ) : (
                  <><Zap className="h-4 w-4 mr-2" />AI 评审</>
                )}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <label className="text-sm font-medium">{children}</label>;
}
