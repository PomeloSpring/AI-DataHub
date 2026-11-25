import { useState, useEffect, useCallback, useRef } from 'react';
import { qualityApi } from '@/api/quality';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/lib/toast';
import { useHasPerm } from '@/stores/permissionStore';
import {
  RefreshCw,
  FileText,
  Play,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  TrendingUp,
  ChevronRight,
  Trash2,
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

// /quality/results?rule_id= 返回的行（detail 只透出 execution/status/error）
interface QualityResultItem {
  id: number;
  check_time: string;
  passed: number | boolean;
  total_rows: number | null;
  failed_rows: number | null;
  pass_rate: number | null;
  detail?: { status?: string; error?: string };
}

/** 结果行状态：执行异常（detail.status=unknown）不能笼统算作「失败」，原因不同。 */
function resultStatus(r: QualityResultItem): 'passed' | 'failed' | 'unknown' {
  if (r.detail?.status === 'unknown') return 'unknown';
  return Number(r.passed) === 1 ? 'passed' : 'failed';
}

const RULE_TYPE_MAP: Record<string, string> = {
  not_null: tr('k_985648ecf5'),
  unique: tr('k_9fc917b05d'),
  range: tr('k_b8688ed844'),
  format: tr('k_5e396f0d67'),
  referential: tr('k_1d45d02cf8'),
  custom_sql: tr('k_790605a4d0'),
  freshness: tr('k_b4b6b65d23'),
  row_count: tr('k_c4f0759ddc'),
  distribution: tr('k_2eeb22529e'),
};

const RESULT_STATUS_MAP: Record<string, { label: string; color: string }> = {
  passed: { label: tr('k_dcc4233255'), color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  failed: { label: tr('k_3e3c8068bb'), color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  unknown: { label: tr('k_b0d5b6f860'), color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
};

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

  // ── 失败原因明细（问题 TOP10 行可点开查看）────────────────────
  const [detailIssue, setDetailIssue] = useState<DashboardData['top_issues'][number] | null>(null);
  const [results, setResults] = useState<QualityResultItem[]>([]);
  const [resultsLoading, setResultsLoading] = useState(false);
  const [resultsError, setResultsError] = useState('');
  const [samples, setSamples] = useState<Record<string, unknown>[] | null>(null);
  const [samplesLoading, setSamplesLoading] = useState(false);
  const [samplesError, setSamplesError] = useState('');

  const loadResults = useCallback(async (ruleId: number) => {
    setResultsLoading(true);
    setResultsError('');
    try {
      const { data } = await qualityApi.getResults({ rule_id: ruleId, page: 1, page_size: 10 });
      setResults(Array.isArray(data?.items) ? data.items : []);
    } catch {
      // 查询失败必须显式告知，不得显示为「无记录」
      setResultsError(tr('k_7fa569b5ec'));
    } finally {
      setResultsLoading(false);
    }
  }, []);

  const openIssue = (issue: DashboardData['top_issues'][number]) => {
    setDetailIssue(issue);
    setResults([]);
    setSamples(null);
    setResultsError('');
    setSamplesError('');
    loadResults(issue.rule_id);
  };

  const closeIssue = () => {
    setDetailIssue(null);
    setResults([]);
    setSamples(null);
    setResultsError('');
    setSamplesError('');
  };

  // 失败样本需实时重跑规则（不落结果表），按当前查看者权限脱敏
  const loadSamples = async () => {
    if (!detailIssue) return;
    setSamplesLoading(true);
    setSamplesError('');
    try {
      const { data } = await qualityApi.getRuleSamples(detailIssue.rule_id);
      if (data?.error) {
        setSamplesError(String(data.error));
        setSamples([]);
      } else {
        setSamples(Array.isArray(data?.samples) ? data.samples : []);
      }
    } catch (e: any) {
      setSamplesError(e?.response?.data?.detail || tr('k_4049e98568'));
    } finally {
      setSamplesLoading(false);
    }
  };

  const latestFailure = results.find(r => resultStatus(r) !== 'passed');
  const sampleColumns = Array.from(
    new Set((samples || []).flatMap(row => Object.keys(row))),
  );

  // ── 删除（质量报告 / 问题记录）────────────────────────────────
  // 删除属写动作，与 /api/quality* 的写权限码同源；无码不渲染入口（点了必 403）
  const canManage = useHasPerm('quality:manage');
  const [reportToDelete, setReportToDelete] = useState<DashboardData['recent_reports'][number] | null>(null);
  const [deletingReport, setDeletingReport] = useState(false);
  // 问题记录 = 该规则的失败检查结果（passed=0）；删哪些由用户在弹窗里勾选决定
  const [issueToDelete, setIssueToDelete] = useState<DashboardData['top_issues'][number] | null>(null);
  const [failedRecords, setFailedRecords] = useState<QualityResultItem[]>([]);
  const [failedTotal, setFailedTotal] = useState(0);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordsError, setRecordsError] = useState('');
  const [deletingRecords, setDeletingRecords] = useState(false);
  // 快速切换不同规则时，晚到的旧请求不得覆盖新弹窗的记录
  const recordsRuleRef = useRef<number | null>(null);

  const loadFailedRecords = useCallback(async (ruleId: number) => {
    setRecordsLoading(true);
    setRecordsError('');
    try {
      const { data } = await qualityApi.getResults({
        rule_id: ruleId, passed: 0, page: 1, page_size: 100,
      });
      if (recordsRuleRef.current !== ruleId) return;
      const items: QualityResultItem[] = Array.isArray(data?.items) ? data.items : [];
      setFailedRecords(items);
      setFailedTotal(Number(data?.total) || items.length);
      // 显式删除入口：默认勾选全部失败记录，用户可逐条取消
      setSelectedIds(items.map(r => r.id));
    } catch {
      setRecordsError(tr('k_9f01fa0abb'));
    } finally {
      setRecordsLoading(false);
    }
  }, []);

  const openIssueDelete = (issue: DashboardData['top_issues'][number]) => {
    recordsRuleRef.current = issue.rule_id;
    setIssueToDelete(issue);
    setFailedRecords([]);
    setFailedTotal(0);
    setSelectedIds([]);
    setRecordsError('');
    loadFailedRecords(issue.rule_id);
  };

  const closeIssueDelete = () => {
    recordsRuleRef.current = null;
    setIssueToDelete(null);
    setFailedRecords([]);
    setFailedTotal(0);
    setSelectedIds([]);
    setRecordsError('');
  };

  const toggleRecord = (id: number, checked: boolean) => {
    setSelectedIds(prev => (
      checked ? Array.from(new Set([...prev, id])) : prev.filter(x => x !== id)
    ));
  };

  const handleDeleteReport = async () => {
    if (!reportToDelete) return;
    setDeletingReport(true);
    try {
      await qualityApi.deleteReport(reportToDelete.id);
      toast.success(tr('k_5715cace0a'));
      setReportToDelete(null);
      loadDashboard();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || tr('k_329310d55d'));
    } finally {
      setDeletingReport(false);
    }
  };

  const handleDeleteRecords = async () => {
    if (!issueToDelete || selectedIds.length === 0) return;
    setDeletingRecords(true);
    try {
      const { data } = await qualityApi.deleteResultsBatch(selectedIds);
      const failed: Array<{ id: number; reason: string }> = Array.isArray(data?.failed) ? data.failed : [];
      const deleted: number[] = Array.isArray(data?.deleted) ? data.deleted : [];
      loadDashboard();
      if (failed.length) {
        // 部分失败不静默：保留弹窗、只勾选失败项供重试
        setFailedRecords(prev => prev.filter(r => !deleted.includes(r.id)));
        setSelectedIds(failed.map(f => f.id));
        toast.error(`${tr('k_3f2a2fda89', { p0: failed.length })} ${failed.slice(0, 3).map(f => `#${f.id} ${f.reason}`).join('；')}`);
      } else {
        toast.success(tr('k_83fe796ec5', { p0: data?.deleted_count ?? deleted.length }));
        closeIssueDelete();
      }
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || tr('k_acf0664a54'));
    } finally {
      setDeletingRecords(false);
    }
  };

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
                      {canManage && (
                        <th className="text-right p-2 font-medium">{t("k_f3ea6d345e")}</th>
                      )}
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
                        {canManage && (
                          <td className="p-2 text-right">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7 text-muted-foreground hover:text-red-500"
                              title={tr('k_d03bee5558')}
                              aria-label={`${new Date(report.report_date).toLocaleDateString()} ${tr('k_d03bee5558')}`}
                              onClick={() => setReportToDelete(report)}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          </td>
                        )}
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
            <CardTitle className="text-base flex items-baseline gap-2">
              {t("k_28f55f3cbb")}
              <span className="text-xs font-normal text-muted-foreground">{tr('k_328f3d1dd7')}</span>
            </CardTitle>
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
                      {canManage && (
                        <th className="text-right p-2 font-medium">{t("k_f3ea6d345e")}</th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {dashboard.top_issues.map((issue) => (
                      <tr
                        key={issue.rule_id}
                        className="border-t hover:bg-muted/30 cursor-pointer focus:outline-none focus-visible:bg-muted/50"
                        tabIndex={0}
                        role="button"
                        aria-label={`${issue.rule_name} - ${tr('k_13d5f24381')}`}
                        onClick={() => openIssue(issue)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            openIssue(issue);
                          }
                        }}
                      >
                        <td className="p-2 font-medium truncate max-w-[160px]">
                          <span className="inline-flex items-center gap-0.5 min-w-0 max-w-full">
                            <span className="truncate">{issue.rule_name}</span>
                            <ChevronRight className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
                          </span>
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
                        {canManage && (
                          <td className="p-2 text-right">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7 text-muted-foreground hover:text-red-500"
                              title={`${tr('k_54bdb51e89')} - ${issue.rule_name}`}
                              aria-label={`${tr('k_54bdb51e89')} - ${issue.rule_name}`}
                              onClick={(e) => { e.stopPropagation(); openIssueDelete(issue); }}
                              onKeyDown={(e) => e.stopPropagation()}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          </td>
                        )}
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

      {/* 失败原因明细：问题 TOP10 行可点开（失败原因 + 最近结果 + 失败样本） */}
      <Dialog open={!!detailIssue} onOpenChange={(open) => { if (!open) closeIssue(); }}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{detailIssue?.rule_name}</DialogTitle>
            <DialogDescription asChild>
              <div className="flex flex-wrap items-center gap-2">
                <code className="bg-muted px-1 py-0.5 rounded text-xs">
                  {detailIssue?.target_table}
                </code>
                {detailIssue && (
                  <Badge variant="outline" className="bg-purple-500/10 text-purple-500 border-purple-500/20">
                    {RULE_TYPE_MAP[detailIssue.rule_type] || detailIssue.rule_type}
                  </Badge>
                )}
                {detailIssue && (
                  <Badge variant="outline" className={SEVERITY_MAP[detailIssue.severity]?.color}>
                    {SEVERITY_MAP[detailIssue.severity]?.label || detailIssue.severity}
                  </Badge>
                )}
                <span className="text-xs">
                  {tr('k_20eb933ee8', { p0: detailIssue?.failure_count ?? 0 })}
                </span>
              </div>
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-5">
            <section className="space-y-2">
              <h4 className="text-sm font-medium">{tr('k_13d5f24381')}</h4>
              {resultsLoading ? (
                <div className="space-y-2">
                  {[1, 2, 3].map(i => (
                    <Skeleton key={i} className="h-8 w-full" />
                  ))}
                </div>
              ) : resultsError ? (
                <p className="text-sm text-destructive break-words">{resultsError}</p>
              ) : results.length === 0 ? (
                <p className="text-sm text-muted-foreground">{tr('k_3baa6786e4')}</p>
              ) : latestFailure ? (
                latestFailure.detail?.error ? (
                  <p className="text-sm text-destructive break-words">{latestFailure.detail.error}</p>
                ) : (
                  <p className="text-sm">
                    {tr('k_d5733a419e', {
                      p0: latestFailure.failed_rows ?? 0,
                      p1: latestFailure.total_rows ?? 0,
                      p2: Number(latestFailure.pass_rate ?? 0).toFixed(2),
                    })}
                  </p>
                )
              ) : (
                <p className="text-sm text-muted-foreground">{tr('k_9a5ddec89f')}</p>
              )}
            </section>

            <section className="space-y-2">
              <h4 className="text-sm font-medium">{tr('k_4dea4bb876')}</h4>
              {results.length > 0 && !resultsLoading && !resultsError && (
                <div className="border rounded-lg overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/50">
                      <tr>
                        <th className="text-left p-2 font-medium">{tr('k_e2aaac6dae')}</th>
                        <th className="text-center p-2 font-medium">{t("k_62e951a692")}</th>
                        <th className="text-right p-2 font-medium">{t("k_b4582b86da")}</th>
                        <th className="text-right p-2 font-medium">
                          {tr('k_9085659645')}/{tr('k_31769d6063')}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {results.map(r => {
                        const status = resultStatus(r);
                        return (
                          <tr key={r.id} className="border-t">
                            <td className="p-2 text-xs">{new Date(r.check_time).toLocaleString()}</td>
                            <td className="p-2 text-center">
                              <Badge variant="outline" className={RESULT_STATUS_MAP[status]?.color}>
                                {RESULT_STATUS_MAP[status]?.label || status}
                              </Badge>
                            </td>
                            <td className="p-2 text-right">{Number(r.pass_rate ?? 0).toFixed(2)}%</td>
                            <td className="p-2 text-right text-muted-foreground">
                              {r.failed_rows ?? 0}/{r.total_rows ?? 0}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <h4 className="text-sm font-medium">{tr('k_5f5a8684b4')}</h4>
                <Button variant="outline" size="sm" onClick={loadSamples} disabled={samplesLoading}>
                  {samplesLoading ? tr('k_f013ea9dcb') : tr('k_bc129be98d')}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">{tr('k_7803daf931')}</p>
              {samplesError && <p className="text-sm text-destructive break-words">{samplesError}</p>}
              {samples && !samplesError && (
                samples.length === 0 ? (
                  <p className="text-sm text-muted-foreground">{tr('k_f0b9e15eca')}</p>
                ) : (
                  <div className="border rounded-lg overflow-auto max-h-64">
                    <table className="w-full text-xs">
                      <thead className="bg-muted/50">
                        <tr>
                          {sampleColumns.map(c => (
                            <th key={c} className="text-left p-2 font-medium whitespace-nowrap">{c}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {samples.map((row, i) => (
                          <tr key={i} className="border-t">
                            {sampleColumns.map(c => (
                              <td key={c} className="p-2 whitespace-nowrap">
                                {row[c] === null || row[c] === undefined ? '—' : String(row[c])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              )}
            </section>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={closeIssue}>{t("k_6c14bd7f6f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除质量报告：报告只是快照，删掉不影响规则与检查结果 */}
      <Dialog open={!!reportToDelete} onOpenChange={(open) => { if (!open) setReportToDelete(null); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{tr('k_d03bee5558')}</DialogTitle>
            <DialogDescription>{tr('k_4b3ac95d2f')}</DialogDescription>
          </DialogHeader>
          {reportToDelete && (
            <div className="text-sm text-muted-foreground space-y-1">
              <div>{t("k_b6fed9af83")}：{new Date(reportToDelete.report_date).toLocaleDateString()}</div>
              <div>{t("k_06db0729e4")}：{reportToDelete.overall_score.toFixed(1)}</div>
              <div>{t("k_b4582b86da")}：{reportToDelete.pass_rate.toFixed(1)}%</div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setReportToDelete(null)} disabled={deletingReport}>
              {t("k_4d0b4688c7")}
            </Button>
            <Button variant="destructive" onClick={handleDeleteReport} disabled={deletingReport}>
              {deletingReport ? tr('k_f8de3dbe92') : tr('k_3755f56f2f')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除问题记录：列该规则失败检查结果，勾选哪几条由用户自己决定 */}
      <Dialog open={!!issueToDelete} onOpenChange={(open) => { if (!open) closeIssueDelete(); }}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{tr('k_54bdb51e89')}</DialogTitle>
            <DialogDescription asChild>
              <div className="flex flex-wrap items-center gap-2">
                {issueToDelete && (
                  <>
                    <span className="text-sm font-medium text-foreground">{issueToDelete.rule_name}</span>
                    <code className="bg-muted px-1 py-0.5 rounded text-xs">{issueToDelete.target_table}</code>
                  </>
                )}
              </div>
            </DialogDescription>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">{tr('k_9c20cf52c9')}</p>

          {recordsLoading ? (
            <div className="space-y-2">
              {[1, 2, 3].map(i => <Skeleton key={i} className="h-9 w-full" />)}
            </div>
          ) : recordsError ? (
            <p className="text-sm text-destructive break-words">{recordsError}</p>
          ) : failedRecords.length === 0 ? (
            <p className="text-sm text-muted-foreground">{tr('k_0031c83eab')}</p>
          ) : (
            <div className="space-y-2">
              {failedTotal > failedRecords.length && (
                <p className="text-xs text-orange-500">
                  {tr('k_0eae1be41b', { p0: failedTotal })}
                </p>
              )}
              <div className="border rounded-lg overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50">
                    <tr>
                      <th className="p-2 w-8">
                        <input
                          type="checkbox"
                          className="h-3.5 w-3.5 accent-primary"
                          aria-label={tr('k_3e44b2a933')}
                          checked={selectedIds.length === failedRecords.length}
                          onChange={(e) => setSelectedIds(
                            e.target.checked ? failedRecords.map(r => r.id) : [],
                          )}
                        />
                      </th>
                      <th className="text-left p-2 font-medium">{t("k_e2aaac6dae")}</th>
                      <th className="text-center p-2 font-medium">{t("k_62e951a692")}</th>
                      <th className="text-right p-2 font-medium">{t("k_b4582b86da")}</th>
                      <th className="text-right p-2 font-medium">
                        {tr('k_9085659645')}/{tr('k_31769d6063')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {failedRecords.map((r) => {
                      const status = resultStatus(r);
                      return (
                        <tr key={r.id} className="border-t">
                          <td className="p-2">
                            <input
                              type="checkbox"
                              className="h-3.5 w-3.5 accent-primary"
                              aria-label={`${new Date(r.check_time).toLocaleString()} ${tr('k_3755f56f2f')}`}
                              checked={selectedIds.includes(r.id)}
                              onChange={(e) => toggleRecord(r.id, e.target.checked)}
                            />
                          </td>
                          <td className="p-2 text-xs">{new Date(r.check_time).toLocaleString()}</td>
                          <td className="p-2 text-center">
                            <Badge variant="outline" className={RESULT_STATUS_MAP[status]?.color}>
                              {RESULT_STATUS_MAP[status]?.label || status}
                            </Badge>
                          </td>
                          <td className="p-2 text-right">
                            {r.pass_rate === null || r.pass_rate === undefined
                              ? '—' : `${Number(r.pass_rate).toFixed(2)}%`}
                          </td>
                          <td className="p-2 text-right text-muted-foreground">
                            {r.failed_rows ?? '—'}/{r.total_rows ?? '—'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {selectedIds.length === 0 && (
                <p className="text-xs text-muted-foreground">{tr('k_d1ebd112e1')}</p>
              )}
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={closeIssueDelete} disabled={deletingRecords}>
              {t("k_4d0b4688c7")}
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteRecords}
              disabled={deletingRecords || selectedIds.length === 0}
            >
              {deletingRecords
                ? tr('k_f8de3dbe92')
                : tr('k_ec7f888139', { p0: selectedIds.length })}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
