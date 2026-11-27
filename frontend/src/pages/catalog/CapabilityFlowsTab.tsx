/**
 * CapabilityFlowsTab — 能力流程（P3-5）：受控 auto 场景的运行台账。
 *
 * 挂在「本体建模」页的**系统能力本体**（kind='system'）视图；仅
 * `hasPerm('capability:run')` 可见（编排入口权限——发起/观察/取消流程，
 * 不是执行权限，每步仍由动作自身权限码在服务端裁决）。
 *
 * 口径：
 * - 列表服务端真分页（total 来自 COUNT）；搜索防抖后下推 q 并回第 1 页；
 * - 人工步骤（channel=human）停 waiting_human：「复核推进」只做服务端复核
 *   （读内容服务的发布事实），不接受客户端"已完成"宣告；
 * - 取消不删已创建业务数据（动作无补偿器声明），失败原因/半态如实展示；
 * - 资源一律显示 name/label（场景业务名），不要求手输内部 ID。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Loader2, RefreshCw } from 'lucide-react';
import client from '@/api/client';
import { PaginationBar, DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { useHasPerm } from '@/stores/permissionStore';
import { toast } from '@/lib/toast';
import { t as tr } from '@/i18n/runtime';
import { useTranslation } from 'react-i18next';

type RunStatus = 'pending' | 'running' | 'waiting_human' | 'succeeded' | 'failed' | 'cancelled';

interface FlowStep {
  step_no: number;
  action: string;
  label: string;
  channel: string;
  status: string;
  attempts: number;
  error_message: string;
  finished_at: string;
}

interface FlowRun {
  run_id: number;
  scenario: string;
  scenario_title: string;
  status: RunStatus;
  current_step: number;
  initiated_by_name: string;
  error_code: string;
  error_message: string;
  created_at: string;
  finished_at: string;
  steps: FlowStep[];
  events?: { step_no: number; event: string; detail: string; at: string }[];
}

const STATUS_LABEL: Record<RunStatus, string> = {
  pending: tr('k_5d1e9a7c5c'),
  running: tr('k_5d1e9a7c5d'),
  waiting_human: tr('k_5d1e9a7c5e'),
  succeeded: tr('k_5d1e9a7c5f'),
  failed: tr('k_5d1e9a7c60'),
  cancelled: tr('k_5d1e9a7c61'),
};

const STATUS_TONE: Record<RunStatus, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  pending: 'outline', running: 'secondary', waiting_human: 'default',
  succeeded: 'secondary', failed: 'destructive', cancelled: 'outline',
};

const TERMINAL: RunStatus[] = ['succeeded', 'failed', 'cancelled'];

export default function CapabilityFlowsTab() {
  const { t } = useTranslation('ui');
  const canRun = useHasPerm('capability:run');
  const [runs, setRuns] = useState<FlowRun[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [status, setStatus] = useState<string>('');
  const [qInput, setQInput] = useState('');
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState<number[]>([]);
  const [busyId, setBusyId] = useState(0);
  const firstRef = useRef(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await client.get('/capability-flows/runs', {
        params: {
          page, page_size: pageSize,
          ...(status ? { status } : {}),
          ...(q ? { q } : {}),
        },
      });
      setRuns(Array.isArray(data?.items) ? data.items : []);
      setTotal(Number(data?.total) || 0);
    } catch (e: any) {
      // 失败即暴露：不把接口错误渲染成"没有运行记录"
      const msg = String(e?.response?.data?.detail || e?.message || '');
      setError(msg);
      toast.error(msg || t('k_22d159f3b7'));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, status, q, t]);

  // 搜索防抖 300ms：停手才下推，改搜索回第 1 页（首渲染不触发无谓重取）
  useEffect(() => {
    if (firstRef.current) { firstRef.current = false; return; }
    const timer = setTimeout(() => {
      setPage(1);
      setQ(qInput.trim());
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qInput]);

  useEffect(() => { void load(); }, [load]);

  const act = async (run: FlowRun, kind: 'cancel' | 'resume') => {
    setBusyId(run.run_id);
    try {
      const { data } = await client.post(`/capability-flows/runs/${run.run_id}/${kind}`);
      if (kind === 'resume') {
        toast.success(data?.status === 'succeeded'
          ? tr('k_5d1e9a7c5f') : tr('k_5d1e9a7c53'));
      } else {
        toast.success(tr('k_5d1e9a7c61'));
      }
      await load();
    } catch (e: any) {
      const detail = String(e?.response?.data?.detail || e?.message || '');
      toast.error(detail || t('k_3e20d5d1ce'));
      await load();
    } finally {
      setBusyId(0);
    }
  };

  if (!canRun) {
    return (
      <div className="p-6 text-xs text-amber-600">
        {tr('k_5d1e9a7c4b', { p0: 'capability:run' })}
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto p-3 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <Select value={status || 'all'} onValueChange={(v) => {
          setStatus(v === 'all' ? '' : v);
          setPage(1);
        }}>
          <SelectTrigger className="h-8 w-[150px] text-xs" aria-label={t('k_62e951a692')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('k_778fc8f994')}</SelectItem>
            {(Object.keys(STATUS_LABEL) as RunStatus[]).map((s) => (
              <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative w-52">
          <Input value={qInput} onChange={(e) => setQInput(e.target.value)}
            placeholder={tr('k_5d1e9a7c57')} className="h-8 text-xs" />
        </div>
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => void load()}>
          <RefreshCw className="h-3.5 w-3.5 mr-1" />{t('k_38108eaa1d')}
        </Button>
      </div>

      {loading && !runs.length && (
        <div className="py-8 text-center text-xs text-muted-foreground">
          <Loader2 className="h-4 w-4 mr-1 inline animate-spin" />{t('k_300ee3dee4')}
        </div>
      )}
      {!loading && error && (
        <div className="text-xs text-destructive p-3 border border-destructive/40 rounded">{error}</div>
      )}
      {!loading && !error && runs.length === 0 && (
        <div className="py-8 text-center text-xs text-muted-foreground border rounded">
          {tr('k_5d1e9a7c55')}
        </div>
      )}

      {runs.map((run) => {
        const open = expanded.includes(run.run_id);
        const busy = busyId === run.run_id;
        return (
          <div key={run.run_id} className="border rounded-lg p-3 text-sm space-y-1.5">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium">{run.scenario_title || run.scenario}</span>
              <Badge variant={STATUS_TONE[run.status] || 'outline'} className="text-[10px]">
                {STATUS_LABEL[run.status] || run.status}
              </Badge>
              <Badge variant="outline" className="text-[10px]">
                {tr('k_5d1e9a7c51')}: {run.current_step}/{run.steps?.length || '?'}
              </Badge>
              <span className="text-[11px] text-muted-foreground">
                {tr('k_5d1e9a7c58')}: {run.initiated_by_name || '—'}（{run.created_at}）
              </span>
              <div className="flex-1" />
              {run.status === 'waiting_human' && (
                <Button size="sm" variant="outline" className="h-6 text-[11px]" disabled={busy}
                  onClick={() => void act(run, 'resume')}>
                  {tr('k_5d1e9a7c53')}
                </Button>
              )}
              {!TERMINAL.includes(run.status) && (
                <Button size="sm" variant="ghost" className="h-6 text-[11px]" disabled={busy}
                  onClick={() => void act(run, 'cancel')}>
                  {tr('k_5d1e9a7c54')}
                </Button>
              )}
              <Button size="sm" variant="ghost" className="h-6 text-[11px]"
                onClick={() => setExpanded((v) => open
                  ? v.filter((x) => x !== run.run_id) : [...v, run.run_id])}>
                {open ? t('k_0a4a3daeff') : t('k_f1d192b54c')}
              </Button>
            </div>
            {run.status === 'waiting_human' && (
              <div className="text-[11px] text-amber-600">{tr('k_5d1e9a7c56')}</div>
            )}
            {!!run.error_message && (
              <div className="text-[11px] text-destructive">
                {tr('k_5d1e9a7c52')}：{run.error_message}
              </div>
            )}
            {open && (
              <div className="mt-1 border-t pt-1.5 space-y-1">
                {(run.steps || []).map((s) => (
                  <div key={s.step_no} className="flex items-center gap-2 text-[11px] text-muted-foreground">
                    <span className="w-8">{s.step_no}.</span>
                    <span className="text-foreground">{s.label || s.action}</span>
                    <Badge variant="outline" className="text-[9px]">
                      {s.channel === 'human' ? tr('k_5d1e9a7c62') : 'AI'}
                    </Badge>
                    <Badge variant={s.status === 'failed' ? 'destructive' : 'outline'}
                      className="text-[9px]">{s.status}</Badge>
                    {!!s.error_message && <span className="text-destructive">{s.error_message}</span>}
                  </div>
                ))}
                {(run.events || []).slice(-6).map((e, i) => (
                  <div key={i} className="text-[10px] text-muted-foreground">
                    [{e.at}] {e.event}（#{e.step_no}）{e.detail}
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}

      <PaginationBar page={page} pageSize={pageSize} total={total} busy={loading}
        onChange={setPage}
        onPageSizeChange={(s) => { setPageSize(s); setPage(1); }}
        pageSizeOptions={[20, 50]} />
    </div>
  );
}
