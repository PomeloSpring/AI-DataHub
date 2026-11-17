import { useEffect, useRef, useState } from 'react';
import { Loader2, RefreshCw, Check, ArrowRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import DashboardChart, { CHART_TYPES } from '../DashboardChart';
import { useChartDesign } from '@/hooks/useChartDesign';
import client from '../../api/client';
import { useDashboardStore } from '../../stores/dashboardStore';
import { DESIGN_STATUS, designError, designPath, getDesign } from '../../api/dashboardDesign';
import type { DashboardDesign, DesignOptions, DesignPreview, DesignWidget } from '../../api/dashboardDesign';
import { useTranslation } from 'react-i18next';

interface Props {
  designId: string;
  onClose: () => void;
  onChanged: () => void;
  onContinue: (text: string) => void;
}
const selectClass = 'h-9 w-full rounded-md border bg-background px-2 text-sm';

export default function DashboardDesignPanel({ designId, onClose, onChanged, onContinue }: Props) {
  const { t } = useTranslation('ui');
  // 图表样式走 UI 字模库主题包管线(useChartDesign): 设计预览与看板/Chat 同观感
  const { chartStyle } = useChartDesign();
  const [design, setDesign] = useState<DashboardDesign | null>(null);
  const [options, setOptions] = useState<DesignOptions | null>(null);
  const [domain, setDomain] = useState('');
  const [kbs, setKbs] = useState<number[]>([]);
  const [catalogOnly, setCatalogOnly] = useState(false);
  const [dashboardChoice, setDashboardChoice] = useState('');
  const [name, setName] = useState('');
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [widgets, setWidgets] = useState<DesignWidget[]>([]);
  const [sql, setSql] = useState<Record<string, string>>({});
  const [originalSql, setOriginalSql] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<DesignPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [tab, setTab] = useState('design');
  const [visualDirty, setVisualDirty] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const lock = useRef(false);
  const alive = useRef(true);
  const path = designPath(designId);
  const terminal = design?.status === 'published' || design?.status === 'cancelled';
  const selectedDomain = options?.domains.find(d => d.datasource_name === domain);
  const sqlDirty = Object.keys(sql).some(k => sql[k] !== originalSql[k]);

  const accept = (next: DashboardDesign) => {
    if (!alive.current) return;
    setDesign(next); setName(next.name); setAnswers(next.answers); setWidgets(next.widgets);
    setDashboardChoice(next.operation === 'create' ? 'new' : next.selection && next.selection.dashboard_id ? String(next.selection.dashboard_id) : '');
    setSql({}); setOriginalSql({}); setVisualDirty(false); setPreview(null);
  };
  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    Promise.all([getDesign(designId), client.get<DesignOptions>(`${path}/options`)]).then(([d, o]) => {
      if (!cancelled) { accept(d); setOptions(o.data); }
    }).catch(e => { if (!cancelled) setError(designError(e)); });
    return () => { cancelled = true; alive.current = false; };
  }, [designId]);

  const run = async (operation: () => Promise<void>) => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try { await operation(); if (alive.current) onChanged(); }
    catch (e) {
      if (alive.current) {
        setError(designError(e)); setPreview(null);
        // 后端可能已废弃旧预览；刷新版本，但不把未保存编辑伪装成已保存。
        try { const next = await getDesign(designId); if (alive.current) setDesign(next); } catch { /* 原错误保留可见 */ }
      }
    } finally { lock.current = false; if (alive.current) setBusy(false); }
  };
  const continueChat = () => {
    onContinue(`请继续仪表盘设计 ${designId}，先用 get_dashboard_design 读取最新选择、口径答案与版本，再准备方案。`);
    onClose();
  };
  const saveSelection = () => run(async () => {
    if (!design || !selectedDomain) return;
    const operation = dashboardChoice === 'new' ? 'create' : (design.operation === 'update' || dashboardChoice.startsWith('chart:')) ? 'update' : 'append';
    const target = dashboardChoice.startsWith('chart:') ? dashboardChoice.slice(6).split(':') : null;
    const { data } = await client.post<DashboardDesign>(`${path}/selection`, {
      expected_version: design.version, name: name || design.name, operation,
      selection: { datasource_name: selectedDomain.datasource_name,
        knowledge_base_ids: catalogOnly ? [] : kbs,
        ...(target ? { dashboard_id: Number(target[0]), chart_id: Number(target[1]) } : {}),
        ...(dashboardChoice !== 'new' && dashboardChoice && !target ? { dashboard_id: Number(dashboardChoice) } : {}) },
    });
    accept(data); setSelecting(false);
  });
  const saveAnswers = () => run(async () => {
    const { data } = await client.patch<DashboardDesign>(path, { expected_version: design!.version, patch: { answers } });
    accept(data);
  });
  const loadSql = () => run(async () => {
    const { data } = await client.get<{ version: number; queries: { key: string; sql: string }[] }>(`${path}/sql`);
    if (data.version !== design?.version) throw new Error('version');
    const values = Object.fromEntries(data.queries.map(q => [q.key, q.sql]));
    setSql(values); setOriginalSql(values); setTab('sql');
  });
  const saveSql = () => run(async () => {
    let current = design!;
    for (const key of Object.keys(sql).filter(k => sql[k] !== originalSql[k])) {
      const { data } = await client.put<DashboardDesign>(`${path}/sql`, {
        expected_version: current.version, widget_key: key, sql: sql[key],
      });
      current = data;
    }
    accept(current);
  });
  const saveVisuals = () => run(async () => {
    const { data } = await client.patch<DashboardDesign>(path, { expected_version: design!.version,
      patch: { visuals: widgets.map(w => ({ key: w.key, title: w.title, chart_type: w.chart_type, config: w.config, position: w.position })) } });
    accept(data);
  });
  const generatePreview = () => run(async () => {
    setPreview(null);
    const { data } = await client.post<DesignPreview>(`${path}/preview`, { expected_version: design!.version });
    accept(data.design); setPreview(data); setTab('preview');
  });
  const publish = () => run(async () => {
    if (!preview || preview.design.version !== design?.version) return;
    await client.post(`${path}/publish`, { expected_version: design!.version });
    accept(await getDesign(designId));
    const dashboards = useDashboardStore.getState();
    await dashboards.loadDashboards();
    if (dashboards.currentId === design.selection?.dashboard_id) await useDashboardStore.getState().refreshCharts();
  });
  const updateWidget = (index: number, patch: Partial<DesignWidget>) => {
    setWidgets(items => items.map((w, i) => i === index ? { ...w, ...patch } : w));
    setVisualDirty(true); setPreview(null);
  };

  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <DialogContent className="max-w-[1100px] w-[95vw] max-h-[92vh] overflow-y-auto" onInteractOutside={event => event.preventDefault()}>
      <DialogHeader><DialogTitle>仪表盘协作设计 · {DESIGN_STATUS[design?.status || ''] || '加载中'}</DialogTitle></DialogHeader>
      <p className="text-sm text-muted-foreground">选择业务范围 → 确认口径 → 设计与 SQL → 真实预览 → 确认发布</p>
      {error && <div role="alert" className="rounded border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}
      {!design ? <Loader2 className="animate-spin" /> : <>
        <div className="flex items-center justify-between gap-3 rounded bg-muted/50 p-3 text-sm">
          <span>{design.request} <span className="text-muted-foreground">· 版本 {design.version}</span></span>
          <Button size="sm" variant="outline" disabled={busy || sqlDirty || visualDirty} onClick={() => run(async () => accept(await getDesign(designId)))}><RefreshCw className="mr-1 h-3 w-3" />{t("k_38108eaa1d")}</Button>
        </div>
        {(!design.selection || selecting) && !terminal ? <fieldset disabled={busy} className="space-y-4 rounded border p-4">
          <h3 className="font-medium">{t("k_a335bbacdd")}</h3>
          <p className="text-xs text-muted-foreground">{t("k_cfd2b9a547")}</p>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <label className="space-y-1 text-sm">{t("k_6d79785fd7")}<select aria-label={t("k_6d79785fd7")} className={selectClass} value={dashboardChoice} onChange={e => setDashboardChoice(e.target.value)}>
              <option value="">{t("k_a48169e534")}</option>
              <option value="new">＋ 新建仪表盘</option>
              {(options?.dashboards || []).flatMap(d => [
                <option key={`d${d.id}`} value={String(d.id)}>追加到：{d.name}</option>,
                ...d.charts.map(c => <option key={`c${d.id}-${c.id}`} value={`chart:${d.id}:${c.id}`}>修改图表：{d.name} / {c.name}</option>),
              ])}
            </select></label>
            {dashboardChoice === 'new' && <label className="space-y-1 text-sm">{t("k_d30671dd5d")}<Input aria-label={t("k_d30671dd5d")} value={name} onChange={e => setName(e.target.value)} /></label>}
            <label className="space-y-1 text-sm">{t("k_c09ae64145")}<select aria-label={t("k_8c045213f7")} className={selectClass} value={domain} onChange={e => { setDomain(e.target.value); setKbs([]); setCatalogOnly(false); }}>
              <option value="">{t("k_1e63d4599f")}</option>{options?.domains.map(d => <option key={d.datasource_name} value={d.datasource_name}>{d.datasource_name}（{d.model_name}）</option>)}
            </select></label>
          </div>
          {options && !options.domains.length && <p className="text-sm text-destructive">{t("k_33eb6e08bd")}</p>}
          {selectedDomain && <div className="space-y-2 text-sm"><Label>{t("k_cf8b0af665")}</Label>
            {!selectedDomain.knowledge_bases.length && <p className="text-xs text-muted-foreground">{t("k_82072f81d3")}</p>}
            {selectedDomain.knowledge_bases.map(k => <label key={k.id} className="flex items-center gap-2">
              <input type="checkbox" disabled={catalogOnly} checked={kbs.includes(k.id)} onChange={e => setKbs(ids => e.target.checked ? [...ids, k.id] : ids.filter(id => id !== k.id))} />{k.name}
            </label>)}
            <label className="flex items-center gap-2"><input type="checkbox" checked={catalogOnly} onChange={e => { setCatalogOnly(e.target.checked); setKbs([]); }} />{t("k_5e15c90de6")}</label>
          </div>}
          <Button onClick={saveSelection} disabled={!selectedDomain || !dashboardChoice || (dashboardChoice === 'new' && !name.trim())}>{t("k_a86371aab8")}</Button>
        </fieldset> : design.selection && <div className="flex flex-wrap items-center gap-3 text-sm">
          <span>业务域：{design.selection.datasource_name}</span><span>操作：{{ create: '新建', append: '追加图表', update: '修改图表' }[design.operation]}</span>
          {!terminal && <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setSelecting(true); setPreview(null); }}>{t("k_09b1e27c68")}</Button>}
        </div>}
        {!!design.questions.length && !terminal && <fieldset disabled={busy} className="space-y-3 rounded border p-4">
          <h3 className="font-medium">{t("k_3d73fdebcd")}</h3>
          {design.questions.map(q => <label key={q.key} className="block space-y-1 text-sm">{q.label}
            <select className={selectClass} value={answers[q.key] || ''} onChange={e => { setAnswers(a => ({ ...a, [q.key]: e.target.value })); setPreview(null); }}>
              <option value="">{t("k_382f4b5559")}</option>{q.options.map(o => <option key={o}>{o}</option>)}
            </select></label>)}
          <Button onClick={saveAnswers} disabled={design.questions.some(q => !answers[q.key])}>{t("k_0b2028c5d7")}</Button>
        </fieldset>}
        {design.selection && !terminal && <Button variant="outline" disabled={busy || sqlDirty || visualDirty} onClick={continueChat}>{t("k_d6a219e09b")}<ArrowRight className="ml-2 h-4 w-4" /></Button>}
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList><TabsTrigger value="design">{t("k_aabf470861")}</TabsTrigger><TabsTrigger value="sql">{t("k_ff66f25314")}</TabsTrigger><TabsTrigger value="preview">{t("k_c5dd50f3c2")}</TabsTrigger></TabsList>
          <TabsContent value="design" className="space-y-4">
            <ol className="list-inside list-decimal space-y-2 text-sm">{design.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
            {!design.widgets.length && <p className="py-6 text-sm text-muted-foreground">{t("k_e4f04d1f40")}</p>}
            <fieldset disabled={busy || terminal} className="space-y-4">
              {widgets.map((w, i) => <div key={w.key} className="space-y-3 rounded border p-3">
                <div className="grid grid-cols-2 gap-3"><label className="text-sm">{t("k_63177e26b4")}<Input aria-label={`图表标题 ${i + 1}`} value={w.title} onChange={e => updateWidget(i, { title: e.target.value })} /></label>
                  <label className="text-sm">{t("k_e891b46dbb")}<select className={selectClass} value={w.chart_type} onChange={e => updateWidget(i, { chart_type: e.target.value })}>{CHART_TYPES.filter(t => t.category !== 'widget').map(t => <option key={t.value} value={t.value}>{t.label}</option>)}</select></label></div>
                <div className="grid grid-cols-2 gap-3">{['xCol', 'yCol'].map(key => <label key={key} className="text-sm">{key === 'xCol' ? 'X 轴' : 'Y 轴'}<Input value={String(w.config[key] || '')} onChange={e => updateWidget(i, { config: { ...w.config, [key]: e.target.value } })} /></label>)}</div>
                <div className="grid grid-cols-4 gap-2">{(['x', 'y', 'w', 'h'] as const).map(key => <label className="text-xs" key={key}>{key}<Input type="number" value={w.position[key]} onChange={e => updateWidget(i, { position: { ...w.position, [key]: Number(e.target.value) } })} /></label>)}</div>
                <label className="block text-sm">{t("k_d1202cc9f7")}<Input value={String(w.config.color || '')} placeholder="例如 #111111" onChange={e => updateWidget(i, { config: { ...w.config, color: e.target.value } })} /></label>
                <details className="text-xs"><summary>{t("k_5d63f1c758")}</summary><pre className="mt-2 overflow-auto rounded bg-muted p-2">{JSON.stringify(w.query, null, 2)}</pre></details>
                {w.query_source === 'raw_sql' && <p className="text-xs text-amber-700">{t("k_92a6138ad3")}</p>}
              </div>)}
              {visualDirty && <Button onClick={saveVisuals}>{t("k_bac397a225")}</Button>}
            </fieldset>
          </TabsContent>
          <TabsContent value="sql" className="space-y-3">
            <p className="text-sm text-muted-foreground">{t("k_653484b02a")}</p>
            <Button variant="outline" disabled={busy || !design.widgets.length || sqlDirty || visualDirty} onClick={loadSql}>{t("k_b5fbb3be20")}</Button>
            {design.widgets.map(w => sql[w.key] !== undefined && <label key={w.key} className="block space-y-2 text-sm">{w.title}
              <textarea aria-label={`SQL ${w.title}`} className="h-52 w-full rounded border bg-muted/40 p-3 font-mono text-xs" disabled={busy || terminal} value={sql[w.key]} onChange={e => { setSql(q => ({ ...q, [w.key]: e.target.value })); setPreview(null); }} />
            </label>)}
            {sqlDirty && <Button disabled={busy} onClick={saveSql}>{t("k_6d0d3f9fc0")}</Button>}
            {design.widgets.some(w => w.query_source === 'raw_sql') && !terminal && <Button variant="outline" disabled={busy} onClick={() => run(async () => {
              const { data } = await client.patch<DashboardDesign>(path, { expected_version: design.version, patch: { reset_sql: true } }); accept(data);
            })}>{t("k_179b4387e4")}</Button>}
          </TabsContent>
          <TabsContent value="preview" className="space-y-4">
            {!preview && <p className="py-8 text-sm text-muted-foreground">{t("k_e9f0382f85")}</p>}
            {preview && <><p className="text-xs text-muted-foreground">预览时间（UTC）：{preview.generated_at} · 10 分钟内有效 · 最多 500 行</p>
              {preview.charts.map(p => {
                const w = design.widgets.find(w => w.key === p.key)!;
                const styled = chartStyle(w.config, w.chart_type);
                return <div key={p.key} className="rounded border p-4"><h3 className="mb-2 font-medium">{w.title}</h3>
                  {p.rows.length ? <div className="relative h-[360px]"><DashboardChart chartType={w.chart_type} data={p} config={styled.config} visual={styled.visual} draft /></div> : <p className="py-10 text-center text-muted-foreground">{t("k_ffdd7759c7")}</p>}
                  <p className="text-xs text-muted-foreground">{p.row_count} 行{p.possibly_truncated ? ' · 已达到预览上限，可能存在更多数据' : ''}</p>
                </div>;
              })}</>}
          </TabsContent>
        </Tabs>
        {design.result?.success && <a className="text-sm font-medium text-primary underline" href={design.result.url}>{t("k_6778b8c866")}</a>}
        {!terminal && <div className="sticky bottom-0 flex flex-wrap items-center justify-end gap-2 border-t bg-background pt-3">
          <Button variant="ghost" disabled={busy} onClick={() => run(async () => { const { data } = await client.post<DashboardDesign>(`${path}/cancel`, { expected_version: design.version }); accept(data); })}>{t("k_15ecffe6f1")}</Button>
          <Button variant="outline" disabled={busy || !design.widgets.length || sqlDirty || visualDirty || selecting} onClick={generatePreview}>{t("k_eceb8ce5ef")}</Button>
          <Button disabled={busy || !preview || sqlDirty || visualDirty || !design.preview_valid || selecting} onClick={publish}><Check className="mr-1 h-4 w-4" />{t("k_70de69fcee")}</Button>
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}
        </div>}
      </>}
    </DialogContent>
  </Dialog>;
}
