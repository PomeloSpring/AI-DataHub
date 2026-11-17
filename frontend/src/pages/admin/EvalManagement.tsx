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
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

/** 接口返回结构异常的显式暴露：代理漏配会被 SPA 兑底成 HTML，不能当成空数据。 */
function expectObject(payload: any, key: string, what: string): any {
  if (!payload || typeof payload[key] !== 'object') {
    throw new Error(tr('k_f1c3749a71', { p0: key, p1: typeof payload, p2: what }));
  }
  return payload;
}

export default function EvalManagement() {
  const { t } = useTranslation('ui');
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
        throw new Error(tr('k_1635f6aaac'));
      }
      setSuites(data.suites);
      setCapabilities(Array.isArray(data.capabilities) ? data.capabilities : []);
    } catch (e: any) {
      const msg = e.message || e.response?.data?.detail || tr('k_55a5fb288c');
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
      setCases(expectList(data, tr('k_c4819b2d07')));
      setError('');
    } catch (e: any) {
      const msg = e.message || e.response?.data?.detail || tr('k_4d14eab7ca');
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [suiteFilter]);

  const loadRuns = useCallback(async () => {
    try {
      const { data } = await client.get('/eval/runs', { params: { limit: 50 } });
      setRuns(expectList(data, tr('k_408cd702ca')));
    } catch (e: any) {
      toast.error(e.message || e.response?.data?.detail || t('k_21ad12cf48'));
    }
  }, []);

  const loadPrecheck = useCallback(async () => {
    try {
      const { data } = await client.get('/eval/precheck');
      setPrecheck(expectObject(data, 'suites', tr('k_ebd3709151')));
    } catch (e: any) {
      toast.error(e.message || e.response?.data?.detail || t('k_22150019be'));
    }
  }, []);

  const loadCoverage = useCallback(async () => {
    try {
      const { data } = await client.get('/eval/coverage');
      setCells(Array.isArray(data?.cells) ? data.cells : []);
    } catch (e: any) {
      // 矩阵拉不到时概览会空白，必须显式告知，不能让"没数据"看起来像"没缺口"
      toast.error(e.message || e.response?.data?.detail || t('k_3e0d5aa0cd'));
    }
  }, []);

  const loadGate = useCallback(async () => {
    try {
      const { data } = await client.get('/eval/gate');
      setGate(expectList(data, tr('k_743733b78c')));
    } catch (e: any) {
      toast.error(e.message || e.response?.data?.detail || t('k_426aa5bc7b'));
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
      toast.success(t('k_dea608d178'));
      loadPrecheck(); loadGate();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_cac9742148'));
    }
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center gap-2">
        <FlaskConical className="h-5 w-5" />
        <h1 className="text-lg font-semibold">{t("k_7ef657c48e")}</h1>
        <Badge variant="outline" className="text-[10px]">{t('k_df39a57c25')}</Badge>
        <div className="flex-1" />
        <Link to="/system/evolution">
          <Button variant="outline" size="sm">
            <Dna className="h-3.5 w-3.5 mr-1" />{t('k_849e449611')}
          </Button>
        </Link>
        <Button variant="outline" size="sm" onClick={refreshAll}>
          <RefreshCw className="h-3.5 w-3.5 mr-1" />{t('k_38108eaa1d')}
        </Button>
      </div>

      <p className="text-xs text-muted-foreground leading-relaxed">
        {tr('k_42a88e92bb')}<b>{t("k_74dab54146")}</b>{tr('k_bb5141bbec')}{' '}<b>{t("k_c4aa974b5b")}</b>）、<b>{t("k_1cc9c0c911")}</b>{tr('k_02049f0530')}<b>{t("k_f80c1d9fec")}</b>）、<b>{t("k_84ab44bf9a")}</b>、
        <b>{t("k_7a043aa209")}</b>{tr('k_812930da07')}{' '}<b>release_ok</b>{t('k_20fe802002')}
      </p>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid grid-cols-5 w-full max-w-2xl">
          <TabsTrigger value="overview"><LayoutDashboard className="h-3.5 w-3.5 mr-1" />{t("k_5060421d15")}</TabsTrigger>
          <TabsTrigger value="cases"><Plus className="h-3.5 w-3.5 mr-1" />{t("k_024dad9fb0")}</TabsTrigger>
          <TabsTrigger value="runs"><RefreshCw className="h-3.5 w-3.5 mr-1" />{t("k_0c3acd446f")}</TabsTrigger>
          <TabsTrigger value="compare"><GitCompare className="h-3.5 w-3.5 mr-1" />{t("k_ef765015fe")}</TabsTrigger>
          <TabsTrigger value="gate"><Settings2 className="h-3.5 w-3.5 mr-1" />{t("k_a0d14adcf1")}</TabsTrigger>
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
              <b className="text-sm">{t("k_b83074c5e8")}</b>
              <Badge variant="outline" className="text-[10px]">{t("k_a2f1012ebd")}</Badge>
            </div>
            <p className="text-[11px] text-muted-foreground">
              {tr('k_a1f2e07174')}{' '}<code>floor_accuracy</code>{tr('k_1b8729d193')}{' '}<code>min_cases</code>{tr('k_7a372c7920')}<b>{t("k_ea6a4c3883")}</b>{tr('k_a1baf62722')}{' '}<code>eval:gate</code> {tr('k_c7aa2164f6')}{' '}</p>
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/40">
                  <TableHead className="text-xs">{t("k_7895e237ab")}</TableHead>
                  <TableHead className="text-xs">{t("k_b13ab1ec69")}</TableHead>
                  <TableHead className="text-xs">{t("k_849b6fc6d8")}</TableHead>
                  <TableHead className="text-xs">{t("k_7097daeee3")}</TableHead>
                  <TableHead className="text-xs">{t("k_d4e9ca3dd4")}</TableHead>
                  <TableHead className="text-xs w-[90px]">{t("k_f3ea6d345e")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {gate.length === 0 && (
                  <TableRow><TableCell colSpan={6} className="text-center text-xs text-muted-foreground p-4">
                    {t('k_6ce75fc96f')}
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
  const { t } = useTranslation('ui');
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
          : <span className="text-muted-foreground">{t("k_caea821078")}</span>}
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
          {Number(draft.enabled) ? t('k_d4e9ca3dd4') : t('k_5c56a88945')}
        </Badge>
      </TableCell>
      <TableCell className="p-1.5">
        <Button size="sm" variant={dirty ? 'default' : 'ghost'}
          className="h-6 text-[11px]" disabled={!dirty || busy}
          onClick={async () => { setBusy(true); try { await onSave(draft); } finally { setBusy(false); } }}>
          {busy ? t('k_6644f06197') : dirty ? t('k_fadf24dbc5') : t('k_f8dfedcd8a')}
        </Button>
      </TableCell>
    </TableRow>
  );
}
