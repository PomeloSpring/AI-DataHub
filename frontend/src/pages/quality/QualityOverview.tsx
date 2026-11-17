import { useState, useEffect, useCallback } from 'react';
import { qualityApi } from '@/api/quality';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/lib/toast';
import {
  RefreshCw,
  FileText,
  Play,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  TrendingUp,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

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
  critical: { label: tr('k_e2e27a8725'), color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  high: { label: tr('k_b096b3f5ac'), color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
  medium: { label: tr('k_0869071c92'), color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
  low: { label: tr('k_b9ee259b7f'), color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
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
  const { t } = useTranslation('ui');
  // 质量域为全局共享（工作空间概念已退役，不再按工作空间隔离/选择）；
  // API 的 workspace 参数固定 0，后端按 IN (ws, 0) 兼容全局行。
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await qualityApi.getDashboard(0);
      setDashboard(data);
    } catch {
      toast.error(tr('k_909ffdaf59'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadDashboard(); }, [loadDashboard]);

  const handleGenerateReport = async () => {
    setActionLoading('report');
    try {
      await qualityApi.generateReport(0);
      toast.success(tr('k_97c6d7ae0b'));
      loadDashboard();
    } catch {
      toast.error(tr('k_5351559231'));
    } finally {
      setActionLoading(null);
    }
  };

  const handleRunAllChecks = async () => {
    setActionLoading('checks');
    try {
      await qualityApi.executeAll(0);
      toast.success(tr('k_efb88c205c'));
      loadDashboard();
    } catch {
      toast.error(tr('k_d976bd30dd'));
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t("k_eff2176b6d")}</h1>
          <p className="text-muted-foreground text-sm mt-1">
            {tr('k_964fed5186')}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadDashboard} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
            {tr('k_38108eaa1d')}</Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRunAllChecks}
            disabled={!!actionLoading}
          >
            <Play className="w-4 h-4 mr-1" />
            {actionLoading === 'checks' ? tr('k_cf0cffe9f8') : tr('k_5b1e19b75c')}
          </Button>
          <Button size="sm" onClick={handleGenerateReport} disabled={!!actionLoading}>
            <FileText className="w-4 h-4 mr-1" />
            {actionLoading === 'report' ? tr('k_4d020ba388') : tr('k_ca40d85dff')}
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
                  {tr('k_6f1d24f665')}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className={`text-4xl font-bold ${getScoreColor(dashboard.overall_score)}`}>
                  {dashboard.overall_score.toFixed(1)}
                </div>
                <p className="text-xs text-muted-foreground mt-1">{t("k_b42c6f855b")}</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4" />
                  {tr('k_b4582b86da')}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className={`text-4xl font-bold ${getScoreColor(dashboard.pass_rate)}`}>
                  {dashboard.pass_rate.toFixed(1)}%
                </div>
                <p className="text-xs text-muted-foreground mt-1">{t("k_aaf865f61f")}</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4" />
                  {tr('k_17cea87ddd')}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-4xl font-bold">{dashboard.total_rules}</div>
                <p className="text-xs text-muted-foreground mt-1">
                  {tr('k_d4e9ca3dd4')}{' '}{dashboard.active_rules} {tr('k_cc1bacb511')}</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                  <XCircle className="w-4 h-4" />
                  {tr('k_7706eb16f6')}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-4xl font-bold text-red-500">
                  {dashboard.top_issues.length}
                </div>
                <p className="text-xs text-muted-foreground mt-1">{t("k_12eac7428a")}</p>
              </CardContent>
            </Card>
          </>
        ) : null}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Reports */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("k_3d97ea36be")}</CardTitle>
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
                      <th className="text-left p-2 font-medium">{t("k_b6fed9af83")}</th>
                      <th className="text-right p-2 font-medium">{t("k_06db0729e4")}</th>
                      <th className="text-right p-2 font-medium">{t("k_b4582b86da")}</th>
                      <th className="text-right p-2 font-medium">{t("k_650c5270d2")}</th>
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
                {tr('k_64cfe4c702')}</div>
            )}
          </CardContent>
        </Card>

        {/* Top Issues */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("k_28f55f3cbb")}</CardTitle>
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
                      <th className="text-left p-2 font-medium">{t("k_1937bcb105")}</th>
                      <th className="text-left p-2 font-medium">{t("k_442be9c497")}</th>
                      <th className="text-center p-2 font-medium">{t("k_9272e8abe5")}</th>
                      <th className="text-right p-2 font-medium">{t("k_06b7ab3fb0")}</th>
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
                {tr('k_6ac81b1bc3')}</div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
