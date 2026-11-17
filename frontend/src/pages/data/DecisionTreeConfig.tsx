import { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, Plus, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Empty } from '@/components/ui/empty';
import DecisionTreeEditor from '@/components/decision/DecisionTreeEditor';
import DecisionTreeGraph from '@/components/decision/DecisionTreeGraph';
import { t } from '@/i18n/runtime';
import {
  decisionsApi, errorText, TREE_STATUS_LABELS,
  type AdviceSeverity, type DecisionAdviceRow, type DecisionTreeDocInput, type DecisionTreeRow, type TreeStatus,
} from '@/api/decisions';
import { concreteSites, listSites, type Site } from '@/api/sites';
import { listNotificationChannels, type NotificationChannel } from '@/api/scheduledTask';
import { hasPerm } from '@/stores/permissionStore';
import { toast } from '@/lib/toast';
import { useTranslation } from 'react-i18next';

/**
 * 数据中台「决策建议」配置页（路由 /data/decisions ↔ 菜单 data:decisions）。
 *
 * 为什么配置面在这里而不是工作空间/系统配置：决策树的 `when.metric/dimension`
 * 引用 `adh_metrics`/`adh_dimensions` 字典，本质是**口径资产**，与「指标与维度」
 * 「本体建模」同族（2026-11-12 拍板）。系统配置是一级 AI 模板/凭据域，语义不符。
 *
 * 两段式：保存只落草案；发布是独立权限码 `decision:publish`（未勾时按钮禁用并
 * 点名缺哪条码，不做"看起来能点其实 403"的死开关）。
 */

const PUBLISH_PERM = 'decision:publish';

const EMPTY_DOC: DecisionTreeDocInput = {
  root: {
    type: 'branch', label: '', op: 'all', children: [{
      type: 'leaf', label: '', advice_key: '', when: { metric: '', comparator: '>', value: null },
    }],
  },
  note: '',
};

interface DraftForm {
  tree_key: string;
  name: string;
  description: string;
  workspace_id: number;
  tree_doc: DecisionTreeDocInput;
  apply_sites: { site_code: string; datasource: string }[];
  schedule_cron: string;
  timezone: string;
  notify_channel_id: number;
}

const blankForm = (): DraftForm => ({
  tree_key: '', name: '', description: '', workspace_id: 0, tree_doc: EMPTY_DOC,
  apply_sites: [], schedule_cron: '', timezone: 'Asia/Shanghai', notify_channel_id: 0,
});

export default function DecisionTreeConfig() {
  const { t } = useTranslation('ui');
  const [tab, setTab] = useState<'trees' | 'advice'>('trees');
  const [trees, setTrees] = useState<DecisionTreeRow[]>([]);
  const [adviceRows, setAdviceRows] = useState<DecisionAdviceRow[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [form, setForm] = useState<DraftForm>(blankForm());
  const [isNew, setIsNew] = useState(true);
  // 判定条件树的两种画法：图面（ReactFlow，复用图可视化基准）/ 表单。
  // 两者共用同一份 form.tree_doc，切视图不会让数据分叉。
  const [docView, setDocView] = useState<'graph' | 'form'>('graph');
  const [issues, setIssues] = useState<{ errors: any[]; warnings: any[] } | null>(null);
  const [busy, setBusy] = useState('');

  const canPublish = useMemo(() => hasPerm(PUBLISH_PERM), []);

  const load = useCallback(async () => {
    try {
      const [t, a] = await Promise.all([
        decisionsApi.listTrees({ include_retired: true }),
        decisionsApi.listAdvice(0),
      ]);
      setTrees(t.data.items || []);
      setAdviceRows(a.data.items || []);
    } catch (err: any) {
      toast.error(errorText(err));
    }
  }, []);

  useEffect(() => {
    load();
    listSites().then(s => setSites(concreteSites(s))).catch(() => setSites([]));
    listNotificationChannels().then(setChannels).catch(() => setChannels([]));
  }, [load]);

  const selectTree = (row: DecisionTreeRow) => {
    setIsNew(false);
    setIssues(null);
    setForm({
      tree_key: row.tree_key, name: row.name, description: row.description || '',
      workspace_id: row.workspace_id || 0,
      tree_doc: (row.tree_doc && Object.keys(row.tree_doc).length ? row.tree_doc : EMPTY_DOC),
      apply_sites: (row.apply_sites || []).map(s => ({ site_code: s.site_code, datasource: s.datasource || '' })),
      schedule_cron: row.schedule_cron || '', timezone: row.timezone || 'Asia/Shanghai',
      notify_channel_id: row.notify_channel_id || 0,
    });
  };

  const validate = async () => {
    setBusy('validate');
    try {
      const { data } = await decisionsApi.validate(form.tree_doc);
      setIssues({ errors: data.errors || [], warnings: data.warnings || [] });
      if (data.valid) toast.success('校验通过：口径引用都能对上字典');
    } catch (err: any) {
      toast.error(errorText(err));
    } finally {
      setBusy('');
    }
  };

  const save = async () => {
    setBusy('save');
    try {
      const payload = { ...form };
      const { data } = isNew
        ? await decisionsApi.createTree(payload)
        : await decisionsApi.updateTree(form.tree_key, payload);
      setIssues({ errors: data?.validation_errors || [], warnings: data?.validation_warnings || [] });
      toast.success('已保存为草案（发布后才参与评估）');
      setIsNew(false);
      await load();
    } catch (err: any) {
      toast.error(errorText(err));
    } finally {
      setBusy('');
    }
  };

  const act = async (kind: 'publish' | 'retire' | 'restore' | 'delete') => {
    if (!form.tree_key) { toast.error('请先保存草案'); return; }
    setBusy(kind);
    try {
      if (kind === 'publish') await decisionsApi.publish(form.tree_key);
      else if (kind === 'retire') await decisionsApi.retire(form.tree_key);
      else if (kind === 'restore') await decisionsApi.restore(form.tree_key);
      else await decisionsApi.deleteTree(form.tree_key);
      toast.success({
        publish: '已发布，该树开始参与评估', retire: '已下架，不再参与评估',
        restore: '已恢复启用', delete: '已删除（历史评估记录保留）',
      }[kind]);
      if (kind === 'delete') { setForm(blankForm()); setIsNew(true); }
      await load();
    } catch (err: any) {
      toast.error(errorText(err));
    } finally {
      setBusy('');
    }
  };

  const current = trees.find(t => t.tree_key === form.tree_key);
  const siteCodes = form.apply_sites.map(s => s.site_code);

  const toggleSite = (code: string) => {
    setForm(f => ({
      ...f,
      apply_sites: siteCodes.includes(code)
        ? f.apply_sites.filter(s => s.site_code !== code)
        : [...f.apply_sites, { site_code: code, datasource: '' }],
    }));
  };

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-lg font-semibold">{t("k_0effa92d4e")}</h1>
          <p className="text-sm text-muted-foreground">
            决策树是业务判断口径资产：条件只能引用指标/维度字典的业务名，判定由确定性引擎完成
          </p>
        </div>
        <div className="ml-auto flex gap-2">
          <Button size="sm" variant={tab === 'trees' ? 'default' : 'outline'} onClick={() => setTab('trees')}>{t("k_642df3fd9d")}</Button>
          <Button size="sm" variant={tab === 'advice' ? 'default' : 'outline'} onClick={() => setTab('advice')}>{t("k_a9511454bb")}</Button>
        </div>
      </header>

      {tab === 'advice'
        ? <AdviceLibrary rows={adviceRows} onChanged={load} />
        : (
          <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
            <aside className="space-y-2">
              <Button size="sm" className="w-full" onClick={() => { setForm(blankForm()); setIsNew(true); setIssues(null); }}>
                <Plus className="h-4 w-4" /><span className="ml-1">{t("k_ec140df087")}</span>
              </Button>
              {!trees.length && <p className="text-xs text-muted-foreground">{t("k_0a1ee1a953")}</p>}
              <ul className="space-y-1">
                {trees.map(t => (
                  <li key={t.tree_key}>
                    <button type="button"
                      onClick={() => selectTree(t)}
                      className={`w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted ${t.tree_key === form.tree_key && !isNew ? 'bg-muted font-medium' : ''}`}>
                      <span className="block truncate">{t.name}</span>
                      <span className="block text-xs text-muted-foreground">
                        {TREE_STATUS_LABELS[t.status as TreeStatus]} · v{t.version}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </aside>

            <section className="space-y-4">
              <div className="grid gap-3 md:grid-cols-2">
                <Field label={t("k_c2a38044cb")}>
                  <Input aria-label={t("k_c2a38044cb")} value={form.name}
                    onChange={e => setForm({ ...form, name: e.target.value })}
                    placeholder={t("k_6726c93690")} className="h-8 text-sm" />
                </Field>
                <Field label={t("k_46c928248d")}>
                  <Input aria-label={t("k_fc8eb0bf70")} value={form.tree_key} disabled={!isNew}
                    onChange={e => setForm({ ...form, tree_key: e.target.value })}
                    placeholder="accuracy-risk" className="h-8 text-sm" />
                </Field>
                <Field label={t("k_26670dda42")}>
                  <Input aria-label={t("k_26670dda42")} value={form.description}
                    onChange={e => setForm({ ...form, description: e.target.value })}
                    placeholder={t("k_ac83736a09")} className="h-8 text-sm" />
                </Field>
                <Field label="适用站点（空=按角色授权站点派生）">
                  <div className="flex flex-wrap gap-2 pt-1">
                    {sites.length === 0 && <span className="text-xs text-muted-foreground">{t("k_c3e54b616f")}</span>}
                    {sites.map(s => (
                      <label key={s.site_code} className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs">
                        <input type="checkbox" checked={siteCodes.includes(s.site_code)}
                          onChange={() => toggleSite(s.site_code)} />
                        {s.display_name || s.site_code}
                      </label>
                    ))}
                  </div>
                </Field>
                <Field label="定时评估 Cron（留空=不定时）">
                  <Input aria-label={t("k_b1ad2c42da")} value={form.schedule_cron}
                    onChange={e => setForm({ ...form, schedule_cron: e.target.value })}
                    placeholder="0 8 * * *" className="h-8 text-sm" />
                </Field>
                <Field label={t("k_570cdddec0")}>
                  <select aria-label={t("k_56807dfa08")} value={form.notify_channel_id}
                    onChange={e => setForm({ ...form, notify_channel_id: Number(e.target.value) })}
                    className="h-8 w-full rounded-md border border-border bg-background px-2 text-sm">
                    <option value={0}>{t("k_8c6daa58db")}</option>
                    {channels.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                </Field>
              </div>

              <div>
                <div className="mb-2 flex items-center gap-2">
                  <h2 className="text-sm font-medium">{t("k_fafe31fcdf")}</h2>
                  <div className="ml-auto flex gap-1">
                    <Button size="sm" variant={docView === 'graph' ? 'default' : 'outline'}
                      aria-pressed={docView === 'graph'} onClick={() => setDocView('graph')}>{t("k_7e60f1359f")}</Button>
                    <Button size="sm" variant={docView === 'form' ? 'default' : 'outline'}
                      aria-pressed={docView === 'form'} onClick={() => setDocView('form')}>{t("k_1f515561b6")}</Button>
                  </div>
                </div>
                {docView === 'graph' ? (
                  <DecisionTreeGraph value={form.tree_doc} adviceRows={adviceRows}
                    onChange={(tree_doc) => setForm(f => ({ ...f, tree_doc }))} />
                ) : (
                  <DecisionTreeEditor value={form.tree_doc} adviceRows={adviceRows}
                    onChange={(tree_doc) => setForm(f => ({ ...f, tree_doc }))} />
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button size="sm" variant="outline" onClick={validate} disabled={busy === 'validate'}>
                  {busy === 'validate' && <Loader2 className="h-4 w-4 animate-spin" />}
                  <span className="ml-1">{t("k_82c7038b77")}</span>
                </Button>
                <Button size="sm" onClick={save} disabled={busy === 'save'}>{t("k_23ba959a54")}</Button>
                <span title={canPublish ? '' : `缺少权限码 ${PUBLISH_PERM}`}>
                  <Button size="sm" variant="secondary" disabled={!canPublish || !form.tree_key}
                    onClick={() => act('publish')}>
                    {!canPublish && <ShieldAlert className="mr-1 h-4 w-4" />}
                    发布
                  </Button>
                </span>
                {current?.status === 'active' && (
                  <Button size="sm" variant="ghost" onClick={() => act('retire')}>{t("k_a2698bcf9e")}</Button>
                )}
                {current?.status === 'retired' && (
                  <Button size="sm" variant="ghost" onClick={() => act('restore')}>{t("k_3c3a525acc")}</Button>
                )}
                {current && (
                  <Button size="sm" variant="ghost" className="text-red-600" onClick={() => act('delete')}>{t("k_3755f56f2f")}</Button>
                )}
                {!canPublish && (
                  <span className="text-xs text-amber-700 dark:text-amber-400">
                    发布需要权限码 <code>{PUBLISH_PERM}</code>，当前角色未勾选，请在「系统配置 → 角色权限」的
                    「决策建议」菜单下勾「发布决策树」。
                  </span>
                )}
              </div>

              {issues && (
                <div className="space-y-1 rounded-lg border border-border bg-muted/30 p-3 text-xs">
                  {issues.errors.length === 0 && issues.warnings.length === 0 && (
                    <p className="text-emerald-700 dark:text-emerald-400">{t("k_68fd6dea38")}</p>
                  )}
                  {issues.errors.map((e, i) => (
                    <p key={`e${i}`} className="text-red-700 dark:text-red-400">
                      【阻断】{e.where}：{e.message}
                    </p>
                  ))}
                  {issues.warnings.map((w, i) => (
                    <p key={`w${i}`} className="text-amber-700 dark:text-amber-400">
                      【告警】{w.where}：{w.message}
                    </p>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-xs text-muted-foreground">{label}</label>
      {children}
    </div>
  );
}

function AdviceLibrary({ rows, onChanged }: { rows: DecisionAdviceRow[]; onChanged: () => void }) {
  const [form, setForm] = useState({
    advice_key: '', action: '', rationale: '', severity: 'medium', kb_refs: '', is_active: true,
  });
  const [isNew, setIsNew] = useState(true);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      const payload = {
        ...form,
        severity: form.severity as AdviceSeverity,
        kb_refs: form.kb_refs.split(',').map(s => s.trim()).filter(Boolean),
      };
      if (isNew) await decisionsApi.createAdvice(payload);
      else await decisionsApi.updateAdvice(form.advice_key, payload);
      toast.success('建议文案已保存');
      setForm({ advice_key: '', action: '', rationale: '', severity: 'medium', kb_refs: '', is_active: true });
      setIsNew(true);
      onChanged();
    } catch (err: any) {
      toast.error(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
      <div className="overflow-hidden rounded-xl border border-border">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-xs text-muted-foreground">
            <tr className="text-left">
              <th className="px-3 py-2 font-medium">{t("k_3537d5ef90")}</th>
              <th className="px-3 py-2 font-medium">{t("k_a77d2fb1b0")}</th>
              <th className="px-3 py-2 font-medium">{t("k_af156f3ea7")}</th>
              <th className="px-3 py-2 font-medium">{t("k_1dcd7aacf3")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(a => (
              <tr key={a.advice_key} className="border-t border-border/60">
                <td className="px-3 py-2 font-mono text-xs">{a.advice_key}</td>
                <td className="px-3 py-2">
                  <button type="button" className="text-left hover:text-primary" onClick={() => {
                    setIsNew(false);
                    setForm({
                      advice_key: a.advice_key, action: a.action, rationale: a.rationale || '',
                      severity: a.severity, kb_refs: (a.kb_refs || []).join(','),
                      is_active: !!a.is_active,
                    });
                  }}>{a.action}</button>
                  {a.rationale && <div className="text-xs text-muted-foreground">{a.rationale}</div>}
                </td>
                <td className="px-3 py-2 text-xs">{a.severity}</td>
                <td className="px-3 py-2 text-xs">{(a.kb_refs || []).join('、') || '—'}</td>
              </tr>
            ))}
            {!rows.length && (
              <tr><td colSpan={4}>
                <Empty title={t("k_7ad3348774")}
                  description="叶子只存 advice_key 引用，文案在这里登记一次，多棵树共用（同一概念只登记一处）。" />
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <aside className="space-y-3 rounded-xl border border-border p-3">
        <h2 className="text-sm font-medium">{isNew ? '新增建议文案' : `编辑 ${form.advice_key}`}</h2>
        <Field label={t("k_10c3ff4d10")}>
          <Input aria-label={t("k_0b7d3956a9")} value={form.advice_key} disabled={!isNew}
            onChange={e => setForm({ ...form, advice_key: e.target.value })}
            placeholder="adv_recalibrate" className="h-8 text-sm" />
        </Field>
        <Field label={t("k_a77d2fb1b0")}>
          <Input aria-label={t("k_a77d2fb1b0")} value={form.action}
            onChange={e => setForm({ ...form, action: e.target.value })}
            placeholder={t("k_5da2d27838")} className="h-8 text-sm" />
        </Field>
        <Field label={t("k_a207006f19")}>
          <Input aria-label={t("k_a207006f19")} value={form.rationale}
            onChange={e => setForm({ ...form, rationale: e.target.value })}
            placeholder={t("k_c99c1da608")} className="h-8 text-sm" />
        </Field>
        <Field label={t("k_af156f3ea7")}>
          <select aria-label={t("k_af156f3ea7")} value={form.severity}
            onChange={e => setForm({ ...form, severity: e.target.value })}
            className="h-8 w-full rounded-md border border-border bg-background px-2 text-sm">
            {['low', 'medium', 'high', 'critical'].map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </Field>
        <Field label={t("k_c010667ec0")}>
          <Input aria-label={t("k_1dcd7aacf3")} value={form.kb_refs}
            onChange={e => setForm({ ...form, kb_refs: e.target.value })}
            placeholder="calibration-sop,precision-standard" className="h-8 text-sm" />
        </Field>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={save} disabled={busy || !form.advice_key || !form.action}>{t("k_fadf24dbc5")}</Button>
          {!isNew && (
            <Button size="sm" variant="ghost" onClick={() => {
              setIsNew(true);
              setForm({ advice_key: '', action: '', rationale: '', severity: 'medium', kb_refs: '', is_active: true });
            }}>{t("k_0cda8d1c71")}</Button>
          )}
        </div>
      </aside>
    </div>
  );
}
