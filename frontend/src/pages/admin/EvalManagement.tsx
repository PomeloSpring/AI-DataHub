/**
 * 评测中心（壳层）：负责装载跨面板共享的事实并摆 TAB。
 *
 * 面板拆分见 ./eval/*。这里刻意只做四件事：
 *  1. 拉一次 suites / capabilities / cases / runs / coverage / precheck，
 *     避免各面板各自拉一份导致口径不一致；
 *  2. 门禁阈值的读写（阈值改了要立刻反映到结论上，所以放在壳层统一刷新）；
 *  3. 概览矩阵点击 → 带着 (层, 契约) 跳到用例面板新建。
 *
 * 呈现纪律（旧版踩过的坑）：
 *  - 发布结论只认 release_ok，不认"未回退"；
 *  - L1 跑的是 mock 目录、L4 的 trace 是自录回放，两者的绿都不能当平台能力；
 *  - 资源显示用 name，内部 id 只作次要信息（ui-resource-display §1）。
 */
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { toast } from '@/lib/toast';
import { Dna, FlaskConical, GitCompare, LayoutDashboard, Plus, RefreshCw, Settings2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import client from '@/api/client';
import EvalOverview from './eval/EvalOverview';
import EvalCasesPanel from './eval/EvalCasesPanel';
import EvalRunsPanel from './eval/EvalRunsPanel';
import EvalComparePanel from './eval/EvalComparePanel';
import {
  expectList,
  type CapabilityMeta, type CoverageCell, type EvalCase, type EvalRun,
  type PrecheckPayload, type SuiteMeta,
} from './eval/shared';

/** 接口返回结构异常的显式暴露：代理漏配会被 SPA 兑底成 HTML，不能当成空数据。 */
function expectObject(payload: any, key: string, what: string): any {
  if (!payload || typeof payload[key] !== 'object') {
    throw new Error(`接口返回结构异常（期望 ${key} 对象，实际 ${typeof payload}）：${what}`);
  }
  return payload;
}

export default function EvalManagement() {
  const [tab, setTab] = useState('overview');
  const [suites, setSuites] = useState<SuiteMeta[]>([]);
  const [capabilities, setCapabilities] = useState<CapabilityMeta[]>([]);
  const [cases, setCases] = useState<EvalCase[]>([]);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [cells, setCells] = useState<CoverageCell[]>([]);
  const [precheck, setPrecheck] = useState<PrecheckPayload | null>(null);
  const [gate, setGate] = useState<any[]>([]);
  const [suiteFilter, setSuiteFilter] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [preset, setPreset] = useState<{ suite: string; capability: string; nonce: number } | null>(null);

  const loadMeta = useCallback(async () => {
    try {
      const { data } = await client.get('/eval/suites');
      if (!data || !Array.isArray(data.suites)) {
        throw new Error('接口返回结构异常（期望 suites 数组）。多半是 /api/eval 代理未配置。');
      }
      setSuites(data.suites);
      setCapabilities(Array.isArray(data.capabilities) ? data.capabilities : []);
    } catch (e: any) {
      const msg = e.message || e.response?.data?.detail || '加载评测层定义失败';
      setError(msg);
      toast.error(msg);
    }
  }, []);

  const loadCases = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/eval/cases', {
        params: { suite: suiteFilter, include_inactive: true },
      });
      setCases(expectList(data, '评测用例'));
      setError('');
    } catch (e: any) {
      const msg = e.message || e.response?.data?.detail || '加载用例失败';
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [suiteFilter]);

  const loadRuns = useCallback(async () => {
    try {
      const { data } = await client.get('/eval/runs', { params: { limit: 50 } });
      setRuns(expectList(data, '评测运行记录'));
    } catch (e: any) {
      toast.error(e.message || e.response?.data?.detail || '加载运行记录失败');
    }
  }, []);

  const loadPrecheck = useCallback(async () => {
    try {
      const { data } = await client.get('/eval/precheck');
      setPrecheck(expectObject(data, 'suites', '发布前检查'));
    } catch (e: any) {
      toast.error(e.message || e.response?.data?.detail || '加载发布前检查失败');
    }
  }, []);

  const loadCoverage = useCallback(async () => {
    try {
      const { data } = await client.get('/eval/coverage');
      setCells(Array.isArray(data?.cells) ? data.cells : []);
    } catch (e: any) {
      // 矩阵拉不到时概览会空白，必须显式告知，不能让"没数据"看起来像"没缺口"
      toast.error(e.message || e.response?.data?.detail || '加载覆盖矩阵失败');
    }
  }, []);

  const loadGate = useCallback(async () => {
    try {
      const { data } = await client.get('/eval/gate');
      setGate(expectList(data, '门禁策略'));
    } catch (e: any) {
      toast.error(e.message || e.response?.data?.detail || '加载门禁策略失败');
    }
  }, []);

  useEffect(() => {
    loadMeta(); loadRuns(); loadPrecheck(); loadCoverage(); loadGate();
  }, [loadMeta, loadRuns, loadPrecheck, loadCoverage, loadGate]);
  useEffect(() => { loadCases(); }, [loadCases]);

  const refreshAll = () => {
    loadCases(); loadRuns(); loadPrecheck(); loadCoverage(); loadGate();
  };

  const createAt = (suite: string, capability: string) => {
    setPreset({ suite, capability, nonce: Date.now() });
    setTab('cases');
  };

  const saveGate = async (row: any) => {
    try {
      await client.post('/eval/gate', {
        suite: row.suite, capability: row.capability || '',
        floor_accuracy: Number(row.floor_accuracy), min_cases: Number(row.min_cases),
        max_live_cases_per_run: row.max_live_cases_per_run ?? null,
        enabled: Number(row.enabled),
      });
      toast.success('门禁阈值已更新，结论按新阈值即时重算');
      loadPrecheck(); loadGate();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存门禁阈值失败');
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center gap-2">
        <FlaskConical className="h-5 w-5" />
        <h1 className="text-lg font-semibold">评测中心</h1>
        <Badge variant="outline" className="text-[10px]">层 × 能力契约 · 双口径门禁</Badge>
        <div className="flex-1" />
        <Link to="/system/evolution">
          <Button variant="outline" size="sm">
            <Dna className="h-3.5 w-3.5 mr-1" />进化中心
          </Button>
        </Link>
        <Button variant="outline" size="sm" onClick={refreshAll}>
          <RefreshCw className="h-3.5 w-3.5 mr-1" />刷新
        </Button>
      </div>

      <p className="text-xs text-muted-foreground leading-relaxed">
        四个被测层：<b>L1 编译规则</b>（跑 mock 目录，只验 planner 规则回归，
        <b>不反映真实本体与治理</b>）、<b>L2 真实语义</b>（真实 binding/字典/敏感与 RLS 基线，
        intent→闸门改写，<b>不下发数据源</b>）、<b>L3 真实检索</b>、
        <b>L4 端到端行为</b>（trace=自录回放 / handler=真实工具 / live=真实模型）。
        通过与否由确定性断言决定，LLM 评分只辅助评价解释质量；
        发布结论看 <b>release_ok</b>（相对未回退 AND 达绝对下限）。
      </p>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid grid-cols-5 w-full max-w-2xl">
          <TabsTrigger value="overview"><LayoutDashboard className="h-3.5 w-3.5 mr-1" />概览</TabsTrigger>
          <TabsTrigger value="cases"><Plus className="h-3.5 w-3.5 mr-1" />用例</TabsTrigger>
          <TabsTrigger value="runs"><RefreshCw className="h-3.5 w-3.5 mr-1" />运行</TabsTrigger>
          <TabsTrigger value="compare"><GitCompare className="h-3.5 w-3.5 mr-1" />对比</TabsTrigger>
          <TabsTrigger value="gate"><Settings2 className="h-3.5 w-3.5 mr-1" />门禁</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <EvalOverview suites={suites} capabilities={capabilities} cells={cells}
            precheck={precheck} onCreateAt={createAt} onRefresh={refreshAll} />
        </TabsContent>

        <TabsContent value="cases">
          <EvalCasesPanel suites={suites} capabilities={capabilities} cases={cases}
            loading={loading} error={error} suiteFilter={suiteFilter}
            onSuiteFilter={setSuiteFilter} preset={preset}
            onChanged={() => { loadCases(); loadCoverage(); loadPrecheck(); }} />
        </TabsContent>

        <TabsContent value="runs">
          <EvalRunsPanel suites={suites} capabilities={capabilities} runs={runs} cases={cases}
            onRefreshRuns={() => { loadRuns(); loadPrecheck(); }}
            onRefreshCases={() => { loadCases(); loadCoverage(); }} />
        </TabsContent>

        <TabsContent value="compare">
          <EvalComparePanel runs={runs} />
        </TabsContent>

        <TabsContent value="gate" className="space-y-3">
          <div className="border rounded-md p-3 space-y-2">
            <div className="flex items-center gap-2">
              <Settings2 className="h-4 w-4" />
              <b className="text-sm">绝对下限策略</b>
              <Badge variant="outline" className="text-[10px]">与相对回退判定正交</Badge>
            </div>
            <p className="text-[11px] text-muted-foreground">
              每条策略同时卡两件事：正确率不得低于 <code>floor_accuracy</code>，
              活跃用例数不得低于 <code>min_cases</code>（后者专拦"删/退役难用例刷通过率"）。
              某层没有任何启用策略时按<b>门禁失效</b>处理，不会当作通过。
              阈值不由 AI 放宽：权限码 <code>eval:gate</code> 为 ai_access=none。
            </p>
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/40">
                  <TableHead className="text-xs">层</TableHead>
                  <TableHead className="text-xs">能力契约</TableHead>
                  <TableHead className="text-xs">正确率下限</TableHead>
                  <TableHead className="text-xs">用例数下界</TableHead>
                  <TableHead className="text-xs">启用</TableHead>
                  <TableHead className="text-xs w-[90px]">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {gate.length === 0 && (
                  <TableRow><TableCell colSpan={6} className="text-center text-xs text-muted-foreground p-4">
                    无策略 —— 所有层都会被判门禁失效
                  </TableCell></TableRow>
                )}
                {gate.map(row => (
                  <GateRow key={row.id} row={row} capabilities={capabilities}
                    suites={suites} onSave={saveGate} />
                ))}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

/** 单条阈值行：本地编辑 + 显式保存，不做"改一下就静默写库"。 */
function GateRow({ row, capabilities, suites, onSave }: {
  row: any;
  capabilities: CapabilityMeta[];
  suites: SuiteMeta[];
  onSave: (r: any) => void;
}) {
  const [draft, setDraft] = useState(row);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(row), [row]);
  const dirty = String(draft.floor_accuracy) !== String(row.floor_accuracy)
    || String(draft.min_cases) !== String(row.min_cases)
    || Number(draft.enabled) !== Number(row.enabled);

  return (
    <TableRow>
      <TableCell className="text-xs p-2">
        {suites.find(s => s.suite === draft.suite)?.label || draft.suite}
      </TableCell>
      <TableCell className="text-xs p-2">
        {draft.capability
          ? (capabilities.find(c => c.key === draft.capability)?.label || draft.capability)
          : <span className="text-muted-foreground">该层整体</span>}
      </TableCell>
      <TableCell className="p-1.5">
        <Input className="h-7 text-xs w-24" type="number" step="0.01" min="0" max="1"
          value={draft.floor_accuracy ?? ''}
          onChange={e => setDraft({ ...draft, floor_accuracy: e.target.value })} />
      </TableCell>
      <TableCell className="p-1.5">
        <Input className="h-7 text-xs w-20" type="number" min="0"
          value={draft.min_cases ?? 0}
          onChange={e => setDraft({ ...draft, min_cases: e.target.value })} />
      </TableCell>
      <TableCell className="text-xs p-2">
        <Badge variant={Number(draft.enabled) ? 'secondary' : 'outline'} className="text-[9px]">
          {Number(draft.enabled) ? '启用' : '停用'}
        </Badge>
      </TableCell>
      <TableCell className="p-1.5">
        <Button size="sm" variant={dirty ? 'default' : 'ghost'}
          className="h-6 text-[11px]" disabled={!dirty || busy}
          onClick={async () => { setBusy(true); try { await onSave(draft); } finally { setBusy(false); } }}>
          {busy ? '保存中…' : dirty ? '保存' : '已保存'}
        </Button>
      </TableCell>
    </TableRow>
  );
}
