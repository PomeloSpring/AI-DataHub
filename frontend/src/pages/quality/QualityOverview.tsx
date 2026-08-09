import { useState, useEffect, useCallback } from 'react';
import { qualityApi } from '@/api/quality';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from 'sonner';
import {
  RefreshCw,
  FileText,
  Play,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  TrendingUp,
} from 'lucide-react';

interface DashboardData {
  overall_score: number;
  pass_rate: number;
  total_rules: number;
  active_rules: number;
  recent_reports: Array<{
    id: number;
    report_date: string;
    overall_score: number;
    pass_rate: number;
    total_checks: number;
    passed_checks: number;
    failed_checks: number;
  }>;
  top_issues: Array<{
    rule_id: number;
    rule_name: string;
    rule_type: string;
    target_table: string;
    severity: string;
    failure_count: number;
    last_failure_at: string;
  }>;
}

const SEVERITY_MAP: Record<string, { label: string; color: string }> = {
  critical: { label: '严重', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  high: { label: '高', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
  medium: { label: '中', color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  low: { label: '低', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
};

function getScoreColor(score: number): string {
  if (score >= 90) return 'text-green-500';
  if (score >= 70) return 'text-yellow-500';
  return 'text-red-500';
}

function ScoreSkeleton() {
  return (
    <Card>
      <CardHeader className="pb-2">
        <Skeleton className="h-4 w-24" />
      </CardHeader>
      <CardContent>
        <Skeleton className="h-10 w-32 mb-2" />
        <Skeleton className="h-3 w-20" />
      </CardContent>
    </Card>
  );
}

export default function QualityOverview() {
  const { currentWorkspaceId } = useWorkspaceStore();
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const loadDashboard = useCallback(async () => {
    if (!currentWorkspaceId) return;
    setLoading(true);
    try {
      const { data } = await qualityApi.getDashboard(currentWorkspaceId);
      setDashboard(data);
    } catch {
      toast.error('加载质量概览失败');
    } finally {
      setLoading(false);
    }
  }, [currentWorkspaceId]);

  useEffect(() => { loadDashboard(); }, [loadDashboard]);

  const handleGenerateReport = async () => {
    setActionLoading('report');
    try {
      await qualityApi.generateReport(currentWorkspaceId);
      toast.success('质量报告生成成功');
      loadDashboard();
    } catch {
      toast.error('报告生成失败');
    } finally {
      setActionLoading(null);
    }
  };

  const handleRunAllChecks = async () => {
    setActionLoading('checks');
    try {
      await qualityApi.executeAll(currentWorkspaceId);
      toast.success('所有检查已触发执行');
      loadDashboard();
    } catch {
      toast.error('执行检查失败');
    } finally {
      setActionLoading(null);
    }
  };

  if (!currentWorkspaceId) {
    return (
      <div className="p-6 text-center text-muted-foreground">
        请先选择工作空间
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">数据质量概览</h1>
          <p className="text-muted-foreground text-sm mt-1">
            监控数据质量指标，查看检查报告和问题趋势
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadDashboard} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRunAllChecks}
            disabled={!!actionLoading}
          >
            <Play className="w-4 h-4 mr-1" />
            {actionLoading === 'checks' ? '执行中...' : '运行所有检查'}
          </Button>
          <Button size="sm" onClick={handleGenerateReport} disabled={!!actionLoading}>
            <FileText className="w-4 h-4 mr-1" />
            {actionLoading === 'report' ? '生成中...' : '生成报告'}
          </Button>
        </div>
      </div>

      {/* Score Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {loading ? (
          <>
            <ScoreSkeleton />
            <ScoreSkeleton />
            <ScoreSkeleton />
            <ScoreSkeleton />
          </>
        ) : dashboard ? (
          <>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                  <TrendingUp className="w-4 h-4" />
                  综合质量分
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className={`text-4xl font-bold ${getScoreColor(dashboard.overall_score)}`}>
                  {dashboard.overall_score.toFixed(1)}
                </div>
                <p className="text-xs text-muted-foreground mt-1">满分 100</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4" />
                  通过率
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className={`text-4xl font-bold ${getScoreColor(dashboard.pass_rate)}`}>
                  {dashboard.pass_rate.toFixed(1)}%
                </div>
                <p className="text-xs text-muted-foreground mt-1">检查通过比例</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4" />
                  规则总数
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-4xl font-bold">{dashboard.total_rules}</div>
                <p className="text-xs text-muted-foreground mt-1">
                  启用 {dashboard.active_rules} 条
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                  <XCircle className="w-4 h-4" />
                  问题规则
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-4xl font-bold text-red-500">
                  {dashboard.top_issues.length}
                </div>
                <p className="text-xs text-muted-foreground mt-1">TOP 问题规则数</p>
              </CardContent>
            </Card>
          </>
        ) : null}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Reports */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">最近质量报告</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                {[1, 2, 3].map(i => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : dashboard?.recent_reports?.length ? (
              <div className="border rounded-lg overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50">
                    <tr>
                      <th className="text-left p-2 font-medium">日期</th>
                      <th className="text-right p-2 font-medium">质量分</th>
                      <th className="text-right p-2 font-medium">通过率</th>
                      <th className="text-right p-2 font-medium">通过/总数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dashboard.recent_reports.map((report) => (
                      <tr key={report.id} className="border-t hover:bg-muted/30">
                        <td className="p-2 text-xs">
                          {new Date(report.report_date).toLocaleDateString()}
                        </td>
                        <td className={`p-2 text-right font-medium ${getScoreColor(report.overall_score)}`}>
                          {report.overall_score.toFixed(1)}
                        </td>
                        <td className={`p-2 text-right ${getScoreColor(report.pass_rate)}`}>
                          {report.pass_rate.toFixed(1)}%
                        </td>
                        <td className="p-2 text-right text-muted-foreground">
                          {report.passed_checks}/{report.total_checks}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-8 text-muted-foreground text-sm">
                暂无质量报告
              </div>
            )}
          </CardContent>
        </Card>

        {/* Top Issues */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">问题 TOP 10</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                {[1, 2, 3].map(i => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : dashboard?.top_issues?.length ? (
              <div className="border rounded-lg overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50">
                    <tr>
                      <th className="text-left p-2 font-medium">规则名称</th>
                      <th className="text-left p-2 font-medium">目标表</th>
                      <th className="text-center p-2 font-medium">严重度</th>
                      <th className="text-right p-2 font-medium">失败次数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dashboard.top_issues.map((issue) => (
                      <tr key={issue.rule_id} className="border-t hover:bg-muted/30">
                        <td className="p-2 font-medium truncate max-w-[160px]">
                          {issue.rule_name}
                        </td>
                        <td className="p-2 text-xs text-muted-foreground">
                          <code className="bg-muted px-1 py-0.5 rounded">
                            {issue.target_table}
                          </code>
                        </td>
                        <td className="p-2 text-center">
                          <Badge
                            variant="outline"
                            className={SEVERITY_MAP[issue.severity]?.color}
                          >
                            {SEVERITY_MAP[issue.severity]?.label || issue.severity}
                          </Badge>
                        </td>
                        <td className="p-2 text-right font-medium text-red-500">
                          {issue.failure_count}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-center py-8 text-muted-foreground text-sm">
                暂无问题记录
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
