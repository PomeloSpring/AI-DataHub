/**
 * 模型工作区附加页签：图谱可视化(嵌入) 与 对象×标签。
 *
 * ModelGraphTab: 可视化整体并入工作区 —— 按当前模型的数据源拉图,
 *   支持 总览/表关系/业务知识/血缘 四视图切换; 复用 KnowledgeGraphView(ReactFlow)。
 * ObjectTagsTab: 本模型对象(按 primary_table)已打的标签聚合视图;
 *   标签定义管理仍在标签中心, 此处解决"建模时看不到对象业务色彩"的问题。
 */
import { useState, useEffect, useCallback } from 'react';
import { toast } from '@/lib/toast';
import client from '@/api/client';
import { ontologyApi, type CarrierInput, type CarrierOptions } from '@/api/ontology';
import { useGraphStore } from '@/stores/graphStore';
import KnowledgeGraphView from '@/components/graph/KnowledgeGraph';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Network, Tag as TagIcon, Maximize2, Minimize2, X, Plus, Pencil, Trash2 } from 'lucide-react';
import { normalizeDocObjects } from '@/lib/ontologyObjects';

// 视图已收敛为后端 GraphType.UNIFIED 单图(分域组装 总览+业务知识+物理层/血缘),
// 不再拆四视图切换; singleView 传入时仍锁定旧单视图(系统助手/图谱页兼容)。

/**
 * 同构站点部署子页（原「适用站点」）：模型级声明「这份契约适用哪些站点 + 每站点路由到哪个数据源」。
 *
 * 旧口径要为每个对象×每个站点各登记一行绑定（27 对象 × 5 站 = 135 行机械复制），
 * 现收敛为一次配置全对象共享；站点 schema 真不同时，按拍板「为该站单独配一个
 * 本体或使用源本体」表达。显示/选择一律用业务名（ui-resource-display）。
 */
export function ModelSitesTab({ modelId, readonly }: { modelId: number; readonly?: boolean }) {
  const [sites, setSites] = useState<import('@/api/ontology').ModelSiteRow[]>([]);
  const [objects, setObjects] = useState<import('@/api/ontology').ContractObjectRow[]>([]);
  const [enabled, setEnabled] = useState<Record<string, boolean>>({});
  const [route, setRoute] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(() => {
    if (!modelId) return;
    ontologyApi.sites(modelId).then((r: any) => {
      const list: import('@/api/ontology').ModelSiteRow[] = r?.data?.sites || [];
      setSites(list);
      setObjects(r?.data?.contract_objects || []);
      setEnabled(Object.fromEntries(list.map((s) => [s.site_code, s.selected])));
      // 站点内唯一源自动填（不要求人手输）；多源保留已配置值或留空待人选择
      setRoute(Object.fromEntries(list.map((s) => [
        s.site_code, s.datasource || (s.datasource_options.length === 1 ? s.datasource_options[0] : ''),
      ])));
      setError('');
      setLoaded(true);
    }).catch((e: any) => {
      setError(String(e?.response?.data?.detail || e?.message || e));
      setLoaded(true);
    });
  }, [modelId]);
  useEffect(() => { load(); }, [load]);

  const dirty = loaded && sites.some((s) => {
    const want = !!enabled[s.site_code];
    const wantDs = want ? (route[s.site_code] || '') : '';
    return want !== s.selected
      || wantDs !== (s.selected ? (s.datasource || (s.datasource_options.length === 1 ? s.datasource_options[0] : '')) : '');
  });

  const save = async () => {
    const payload: { site_code: string; datasource: string }[] = [];
    for (const s of sites) {
      if (!enabled[s.site_code]) continue;
      const ds = route[s.site_code] || '';
      if (!ds) {
        setError(`站点「${s.display_name}」还未选择路由数据源`);
        return;
      }
      payload.push({ site_code: s.site_code, datasource: ds });
    }
    setSaving(true);
    setError('');
    try {
      await ontologyApi.saveSites(modelId, payload);
      toast.success('同构站点部署已保存（全模型对象共享）');
      load();
    } catch (e: any) {
      // 校验失败原样外显（不静默回滚成旧配置）
      setError(String(e?.response?.data?.detail || e?.message || '保存失败'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <ScrollArea className="h-full">
      <div className="p-3 space-y-4">
        <div className="text-xs font-medium text-muted-foreground">
          同构站点部署（一份契约 × N 站）：在此声明的业务本体整体适用于哪些站点，取数时按站点自动路由到对应数据源。
          某站点数据结构不同时，请为该站单独配一个本体或直接使用源本体。
          跨源/跨层的取数路径（数仓全局汇总、日志源、镜像）不在这里声明，请到「取数路径」页维护。
        </div>
        {error && <div className="text-xs text-destructive">{error}</div>}
        {loaded && !sites.length && (
          <div className="text-sm text-muted-foreground">
            暂无可用站点。请先在「系统管理 → 数据源 → 站点管理」维护站点清单，并在数据源上配置归属站点。
          </div>
        )}
        {!!sites.length && (
          <div className="space-y-2">
            {sites.map((s) => (
              <div key={s.site_code} className="flex items-center justify-between gap-3 border rounded px-3 py-2">
                <div className="flex items-center gap-2 min-w-0">
                  <Switch
                    checked={!!enabled[s.site_code]}
                    disabled={readonly}
                    onCheckedChange={(v) => setEnabled({ ...enabled, [s.site_code]: v })}
                  />
                  <span className="text-sm font-medium">{s.display_name}</span>
                  {!enabled[s.site_code] && (
                    <Badge variant="outline" className="text-xs text-muted-foreground">不适用</Badge>
                  )}
                </div>
                {enabled[s.site_code] && (
                  <Select
                    value={route[s.site_code] || undefined}
                    disabled={readonly || !s.datasource_options.length}
                    onValueChange={(v) => setRoute({ ...route, [s.site_code]: v })}
                  >
                    <SelectTrigger className="w-[220px] h-8">
                      <SelectValue placeholder={s.datasource_options.length ? '选择该站数据源' : '该站无数据源'} />
                    </SelectTrigger>
                    <SelectContent>
                      {s.datasource_options.map((name) => (
                        <SelectItem key={name} value={name}>{name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </div>
            ))}
            {!readonly && (
              <div className="flex items-center gap-2 pt-1">
                <Button size="sm" onClick={save} disabled={!dirty || saving}>
                  {saving ? '保存中…' : '保存同构站点部署'}
                </Button>
                {!dirty && <span className="text-xs text-muted-foreground">无变更</span>}
              </div>
            )}
          </div>
        )}

        <div>
          <div className="text-xs font-medium text-muted-foreground mb-2">
            对象契约清单（共 {objects.length} 个对象，每对象只登记一份形状描述，站点由上方声明展开）
          </div>
          {!objects.length && <div className="text-sm text-muted-foreground">暂无已绑定的对象</div>}
          {!!objects.length && (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground border-b">
                  <th className="py-1.5 pr-2 font-medium">对象</th>
                  <th className="py-1.5 pr-2 font-medium">契约源</th>
                  <th className="py-1.5 pr-2 font-medium">物理表</th>
                  <th className="py-1.5 pr-2 font-medium">列映射</th>
                  <th className="py-1.5 pr-2 font-medium">绑定类型</th>
                  <th className="py-1.5 pr-2 font-medium">同步状态</th>
                </tr>
              </thead>
              <tbody>
                {objects.map((r, i) => (
                  <tr key={`${r.object_key}-${i}`} className="border-b last:border-0">
                    <td className="py-1.5 pr-2">{r.object_name}</td>
                    <td className="py-1.5 pr-2">{r.datasource || '—'}</td>
                    <td className="py-1.5 pr-2 font-mono">{r.physical_table || '—'}</td>
                    <td className="py-1.5 pr-2">{r.column_count}</td>
                    <td className="py-1.5 pr-2">{r.bind_kind}</td>
                    <td className="py-1.5 pr-2">
                      <Badge
                        variant="outline"
                        className={r.sync_state === 'bound' ? 'text-green-600 border-green-300' : 'text-muted-foreground'}
                      >
                        {r.sync_state === 'bound' ? '✓ 已绑定' : (r.sync_state || '—')}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </ScrollArea>
  );
}

/** 承载类型 / 新鲜度的中文标签（显示用 name，不露英文码） */
const CARRIER_TYPE_LABEL: Record<string, string> = {
  detail: '各站点明细', global: '全局汇总（数仓）', log: '日志·埋点', mirror: '镜像副本',
};
const FRESHNESS_LABEL: Record<string, string> = {
  realtime: '实时', t_plus_1: 'T+1（不含今天）', lagged: '滞后',
};
// Radix Select 不允许空串 value，用哨兵表示“不指定”（提交前换回 ''）
const NONE = '__none__';

const emptyCarrier = (objectKey = ''): CarrierInput => ({
  object_key: objectKey, carrier_key: '', display_name: '', carrier_type: 'detail',
  datasource: '', sites: [], source_model: '', source_object: '',
  grain: '', freshness: 'realtime', as_of_note: '', is_authority: 0, priority: 100,
  status: 'active',
});

/**
 * 取数路径（承载）子页：声明同一个业务概念有哪几条可取数路径。
 *
 * 与「同构站点部署」的分工：站点部署管**同构多站点复制**（一份契约 × N 站，取数时按站点
 * 展开到该站业务库）；本页管**跨源/跨层承载**——例如“用户”既能在各站业务库查明细，
 * 也能在数仓查全局去重汇总（T+1）。“全球问题该逐站查还是走汇总层”这个知识就存在这里。
 *
 * 纪律：
 * - 选项全部来自资源清单接口（后端随 GET 一次带回），不得手输 id（ui-resource-display）；
 * - 承载不存物理表/列，只指向“哪个源本体的哪个对象”（物理形状唯一在部署绑定层）；
 * - 保存是整模型替换：未提交的既有路径会被置为废弃（可回退，不物理删）；
 * - 校验失败原样外显，不静默回滚成旧配置。
 */
export function ModelCarriersTab({ modelId, readonly }: { modelId: number; readonly?: boolean }) {
  const [opts, setOpts] = useState<CarrierOptions | null>(null);
  const [rows, setRows] = useState<CarrierInput[]>([]);
  const [baseline, setBaseline] = useState('[]');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);
  // null = 关闭；-1 = 新增；其余为 rows 下标
  const [editing, setEditing] = useState<number | null>(null);

  const load = useCallback(() => {
    if (!modelId) return;
    ontologyApi.carriers(modelId).then((r: any) => {
      const payload: any = r?.data || {};
      setOpts(payload);
      const next: CarrierInput[] = (payload.items || []).map((c: any) => ({
        object_key: c.object_key, carrier_key: c.carrier_key, display_name: c.display_name,
        carrier_type: c.carrier_type, datasource: c.datasource, sites: c.sites || [],
        source_model: c.source_model, source_object: c.source_object, grain: c.grain,
        freshness: c.freshness, as_of_note: c.as_of_note, is_authority: c.is_authority,
        priority: c.priority, size_class: c.size_class, status: c.status,
      }));
      setRows(next);
      setBaseline(JSON.stringify(next));
      setError('');
      setLoaded(true);
    }).catch((e: any) => {
      setError(String(e?.response?.data?.detail || e?.message || e));
      setLoaded(true);
    });
  }, [modelId]);
  useEffect(() => { load(); }, [load]);

  const dirty = loaded && JSON.stringify(rows) !== baseline;
  const objectName = (key: string) =>
    opts?.objects.find((o) => o.key === key)?.name || key || '（未选对象）';
  const siteLabel = (code: string) =>
    opts?.sites.find((s) => s.site_code === code)?.display_name || code;

  const save = async () => {
    // 前端先拦一道可读提示（后端 topology.validate_carriers 仍是硬门禁，不靠前端兼容）
    const seen = new Set<string>();
    for (const row of rows) {
      const okey = String(row.object_key || '');
      const ckey = String(row.carrier_key || '').trim();
      if (!okey || !ckey) { setError('每条取数路径都要选归属对象并填写承载标识'); return; }
      const pair = `${okey}/${ckey}`;
      if (seen.has(pair)) { setError(`对象「${objectName(okey)}」下承载标识「${ckey}」重复`); return; }
      seen.add(pair);
      const t = String(row.carrier_type || 'detail');
      if (t !== 'detail' && !(row.source_model && row.source_object)) {
        setError(`「${CARRIER_TYPE_LABEL[t] || t}」必须指向源本体对象（物理形状另有其人）`); return;
      }
    }
    const authorities = new Set<string>();
    for (const row of rows) {
      if (!Number(row.is_authority)) continue;
      const okey = String(row.object_key || '');
      if (authorities.has(okey)) { setError(`对象「${objectName(okey)}」只能有一个权威口径`); return; }
      authorities.add(okey);
    }
    setSaving(true);
    setError('');
    try {
      await ontologyApi.saveCarriers(modelId, rows);
      toast.success('取数路径已保存（Chat 侧下次提问即按新拓扑选路）');
      load();
    } catch (e: any) {
      setError(String(e?.response?.data?.detail || e?.message || '保存失败'));
    } finally {
      setSaving(false);
    }
  };

  const grouped = rows.reduce<Record<string, { row: CarrierInput; index: number }[]>>((acc, row, index) => {
    const key = String(row.object_key || '');
    (acc[key] = acc[key] || []).push({ row, index });
    return acc;
  }, {});

  return (
    <ScrollArea className="h-full">
      <div className="p-3 space-y-4">
        <div className="text-xs font-medium text-muted-foreground">
          取数路径（承载）：声明这个业务概念的数据能从哪几条路径取到——各站点明细、数仓全局汇总、
          日志源等。Chat 选「全球」时，LLM 就按这里的声明决定“逐站各查一次”还是“直接查汇总层”。
          承载只存路由（数据源 + 源本体对象 + 粒度/新鲜度），物理表与列映射仍唯一在源本体的部署绑定。
          未授权该数据源的角色在 Chat 里看不到这条路径（自然退化到能查的那条）。
        </div>
        {error && <div className="text-xs text-destructive whitespace-pre-wrap">{error}</div>}
        {loaded && !rows.length && (
          <div className="text-sm text-muted-foreground">
            尚未声明任何取数路径。不声明也能用（按「同构站点部署」逐站展开），但全球去重类口径
            （如全球用户数）必须先在数仓建对象并在此声明全局汇总，否则系统不会去查汇总层。
          </div>
        )}

        {Object.entries(grouped).map(([okey, items]) => (
          <div key={okey} className="space-y-2">
            <div className="text-xs font-semibold">{objectName(okey)}
              <span className="ml-1 text-muted-foreground font-normal">（{items.length} 条路径）</span>
            </div>
            {items.map(({ row, index }) => (
              <div key={`${okey}-${row.carrier_key}-${index}`}
                className="border rounded px-3 py-2 flex items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium">{row.display_name || row.carrier_key}</span>
                    <Badge variant="outline" className="text-xs">
                      {CARRIER_TYPE_LABEL[String(row.carrier_type)] || String(row.carrier_type)}
                    </Badge>
                    {!!Number(row.is_authority) && (
                      <Badge variant="outline" className="text-xs text-green-600 border-green-300">权威口径</Badge>
                    )}
                    {row.status === 'deprecated' && (
                      <Badge variant="outline" className="text-xs text-muted-foreground">已废弃</Badge>
                    )}
                    <span className="text-[11px] text-muted-foreground font-mono">{row.carrier_key}</span>
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {row.datasource
                      ? <>数据源 <span className="text-foreground">{row.datasource}</span></>
                      : <>数据源 <span className="text-foreground">按同构站点部署展开</span></>}
                    {(row.sites || []).length > 0 && <>・站点 {(row.sites || []).map(siteLabel).join('、')}</>}
                    {(row.source_model || row.source_object) && (
                      <>・源本体 <span className="text-foreground">{row.source_model || '—'}</span>
                        {' / '}{row.source_object || '—'}</>
                    )}
                    {row.grain && <>・粒度 {row.grain}</>}
                    <>・新鲜度 {FRESHNESS_LABEL[String(row.freshness || 'realtime')] || String(row.freshness)}</>
                    {row.as_of_note && <span className="ml-1">（{row.as_of_note}）</span>}
                  </div>
                </div>
                {!readonly && (
                  <div className="flex items-center gap-1 shrink-0">
                    <Button size="sm" variant="ghost" className="h-7 w-7 p-0"
                      title="编辑" onClick={() => setEditing(index)}><Pencil className="h-3.5 w-3.5" /></Button>
                    <Button size="sm" variant="ghost" className="h-7 w-7 p-0 text-destructive"
                      title="从清单移除（保存后该路径置为废弃）"
                      onClick={() => setRows(rows.filter((_, i) => i !== index))}>
                      <Trash2 className="h-3.5 w-3.5" /></Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        ))}

        {!readonly && (
          <div className="flex items-center gap-2 pt-1">
            <Button size="sm" variant="outline" onClick={() => setEditing(-1)}>
              <Plus className="h-3.5 w-3.5 mr-1" />新增取数路径
            </Button>
            <Button size="sm" onClick={save} disabled={!dirty || saving}>
              {saving ? '保存中…' : '保存取数路径'}
            </Button>
            {!dirty && <span className="text-xs text-muted-foreground">无变更</span>}
          </div>
        )}

        <CarrierEditor
          open={editing !== null}
          index={editing ?? 0}
          draft={editing === null || editing === -1 ? emptyCarrier() : rows[editing]}
          opts={opts}
          onClose={() => setEditing(null)}
          onApply={(next) => {
            if (editing === -1) setRows([...rows, next]);
            else setRows(rows.map((r, i) => (i === editing ? next : r)));
            setEditing(null);
          }}
        />
      </div>
    </ScrollArea>
  );
}

/** 承载编辑弹窗：关联资源一律选择框（对象/数据源/站点/源本体/源对象），不手输 id */
function CarrierEditor({ open, index, draft, opts, onClose, onApply }: {
  open: boolean;
  index: number;
  draft: CarrierInput;
  opts: CarrierOptions | null;
  onClose: () => void;
  onApply: (next: CarrierInput) => void;
}) {
  const [form, setForm] = useState<CarrierInput>(draft);
  useEffect(() => { setForm(draft); }, [draft, index, open]);

  const patch = (p: Partial<CarrierInput>) => setForm({ ...form, ...p });
  const type = String(form.carrier_type || 'detail');
  const needsSource = type !== 'detail';
  const srcOnt = (opts?.source_ontologies || []).find((s) => s.name === form.source_model);
  const err = !String(form.object_key || '').trim() ? '请选择归属对象'
    : !String(form.carrier_key || '').trim() ? '请填写承载标识'
    : needsSource && !(form.source_model && form.source_object) ? '该类型必须选源本体与源对象'
    : '';

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader><DialogTitle>取数路径（承载）</DialogTitle></DialogHeader>
        <div className="space-y-3 max-h-[62vh] overflow-y-auto pr-1">
          <Field label="归属对象（业务本体对象）">
            <Select value={form.object_key || undefined} onValueChange={(v) => patch({ object_key: v })}>
              <SelectTrigger className="h-8 text-xs"><SelectValue placeholder="选择对象" /></SelectTrigger>
              <SelectContent>
                {(opts?.objects || []).map((o) => (
                  <SelectItem key={o.key} value={o.key}>{o.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="承载标识（英文，选路用）">
              <Input className="h-8 text-xs" value={form.carrier_key || ''} placeholder="global"
                onChange={(e) => patch({ carrier_key: e.target.value })} />
            </Field>
            <Field label="显示名（给人看）">
              <Input className="h-8 text-xs" value={form.display_name || ''} placeholder="数仓全局汇总"
                onChange={(e) => patch({ display_name: e.target.value })} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="路径类型">
              <Select value={type} onValueChange={(v) => patch({ carrier_type: v })}>
                <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(opts?.types || Object.keys(CARRIER_TYPE_LABEL)).map((t) => (
                    <SelectItem key={t} value={t}>{CARRIER_TYPE_LABEL[t] || t}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="新鲜度">
              <Select value={String(form.freshness || 'realtime')} onValueChange={(v) => patch({ freshness: v })}>
                <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(opts?.freshness_levels || Object.keys(FRESHNESS_LABEL)).map((f) => (
                    <SelectItem key={f} value={f}>{FRESHNESS_LABEL[f] || f}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
          <Field label="数据源（留空=按同构站点部署逐站展开）">
            <Select value={form.datasource || NONE} onValueChange={(v) => patch({ datasource: v === NONE ? '' : v })}>
              <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>（不指定，按站点展开）</SelectItem>
                {(opts?.datasources || []).map((d) => (
                  <SelectItem key={d.name} value={d.name}>{d.name}
                    <span className="ml-1 text-muted-foreground">{d.db_type}</span></SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          {!!(opts?.sites || []).length && (
            <Field label="适用站点（可多选；全局汇总通常不限定站点）">
              <div className="flex flex-wrap gap-1">
                {(opts?.sites || []).map((s) => {
                  const on = (form.sites || []).includes(s.site_code);
                  return (
                    <button key={s.site_code} type="button"
                      onClick={() => patch({
                        sites: on ? (form.sites || []).filter((x) => x !== s.site_code)
                          : [...(form.sites || []), s.site_code],
                      })}
                      className={`px-2 py-0.5 rounded border text-xs ${
                        on ? 'border-primary text-primary' : 'border-border text-muted-foreground'}`}>
                      {s.display_name}
                    </button>
                  );
                })}
              </div>
            </Field>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Field label="源本体（物理形状来源）">
              <Select value={form.source_model || NONE} disabled={!needsSource}
                onValueChange={(v) => patch({ source_model: v === NONE ? '' : v, source_object: '' })}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue placeholder={needsSource ? '选择源本体' : '明细可选'} /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>（未选定）</SelectItem>
                  {(opts?.source_ontologies || []).map((s) => (
                    <SelectItem key={s.name} value={s.name}>{s.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="源对象（该源本体的对象）">
              <Select value={form.source_object || NONE} disabled={!needsSource || !srcOnt}
                onValueChange={(v) => patch({ source_object: v === NONE ? '' : v })}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue placeholder={srcOnt ? '选择源对象' : '请先选源本体'} /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>（未选定）</SelectItem>
                  {(srcOnt?.objects || []).map((o) => (
                    <SelectItem key={o.key} value={o.key}>{o.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
          {!needsSource && (
            <div className="text-[11px] text-muted-foreground">
              明细路径不选源本体时，物理形状仍由业务本体的契约行按站点展开取得（同构假设）。
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Field label="数据粒度">
              <Input className="h-8 text-xs" value={form.grain || ''} placeholder="用户级 / 日汇总"
                onChange={(e) => patch({ grain: e.target.value })} />
            </Field>
            <Field label="择路优先级（小者优先）">
              <Input className="h-8 text-xs" type="number" value={String(form.priority ?? 100)}
                onChange={(e) => patch({ priority: Number(e.target.value) || 0 })} />
            </Field>
          </div>
          <Field label="口径时效说明（会随结果告知用户）">
            <Input className="h-8 text-xs" value={form.as_of_note || ''} placeholder="T+1，不含今天"
              onChange={(e) => patch({ as_of_note: e.target.value })} />
          </Field>
          <label className="flex items-center gap-2">
            <Switch checked={!!Number(form.is_authority)}
              onCheckedChange={(v) => patch({ is_authority: v ? 1 : 0 })} />
            <span className="text-xs">本对象该口径的权威承载（一个对象只能有一条）</span>
          </label>
        </div>
        <DialogFooter>
          <Button size="sm" variant="ghost" onClick={onClose}><X className="h-3.5 w-3.5 mr-1" />取消</Button>
          <Button size="sm" disabled={!!err} onClick={() => onApply(form)}>确定{err ? `（${err}）` : ''}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ModelGraphTab({ datasourceId, kind, domain, singleView }: {
  datasourceId: number;
  /** 本体类型：source/business/system。图谱归属与标题按 kind，**不按 datasource_id**——
   *  业务本体跨源 datasource_id=0，旧的 `!datasource_id` 判系统域会把业务本体误标为 AS-BOT。 */
  kind?: 'source' | 'business' | 'system' | '';
  /** 业务域（business 本体的归属） */
  domain?: string;
  /** 锁定单视图(如仅总览), 不渲染切换器 */
  singleView?: string;
}) {
  // 系统域仅当 kind=system（不是 datasource_id=0）
  const systemScope = kind === 'system';
  // 工作区默认统一大图(unified); singleView 传入时锁定旧单视图(向后兼容)。
  const view = singleView || 'unified';
  const unified = view === 'unified';
  // 业务本体跨源: 后端按 kind 路由将业务本体建在聚合图 ds:0, 前端取数须对齐(不按 model.datasource_id)。
  const graphDatasourceId = kind === 'business' ? 0 : datasourceId;
  const [showProps, setShowProps] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [selected, setSelected] = useState<any | null>(null);
  const { graphData, fetchGraphData } = useGraphStore();

  const load = useCallback(async () => {
    setLoading(true);
    setSelected(null);
    try {
      // 系统域走 ds:-1(system_scope), 否则按当前模型数据源取图; 统一大图取跨源聚合图
      await fetchGraphData({
        graphType: view,
        datasourceId: graphDatasourceId,
        systemScope,
        limit: unified ? 400 : 200,
        includeProperties: unified ? showProps : undefined,
      });
    } finally {
      setLoading(false);
    }
  }, [view, unified, graphDatasourceId, systemScope, showProps, fetchGraphData]);

  useEffect(() => { load(); }, [load]);

  const stale = graphData && (graphData.nodes.length === 0) && !loading;

  return (
    <div className={fullscreen
      ? 'fixed inset-0 z-50 bg-background p-4 flex flex-col'
      : 'h-[68vh] flex flex-col border rounded-lg overflow-hidden'}>
      <div className="flex items-center gap-1 px-2 py-1.5 border-b bg-muted/30 shrink-0">
        {unified ? (
          <span className="text-xs font-medium text-muted-foreground px-1">统一大图（语义主干 · 物理骨架 · 血缘桥接）</span>
        ) : (
          <span className="text-xs font-medium text-muted-foreground px-1">本体总览（对象为点 · Link 为名边）</span>
        )}
        {unified && (
          <label className="flex items-center gap-1.5 pl-2 text-[11px] text-muted-foreground cursor-pointer select-none">
            <Switch checked={showProps} onCheckedChange={setShowProps} />
            显示属性
          </label>
        )}
        <span className="ml-auto text-[11px] text-muted-foreground">
          {systemScope ? 'AS-BOT 系统能力本体（系统域·已隔离业务本体）'
            : kind === 'business' ? `业务本体${domain ? `·${domain}` : ''}（跨域连通图）`
            : kind === 'source' ? `源本体·数据源 #${datasourceId}`
            : singleView ? 'AS-BOT 系统能力本体' : `数据源 #${datasourceId}`} · {loading ? '加载中…' : `${graphData?.nodes.length ?? 0} 节点 / ${graphData?.edges.length ?? 0} 边 · 可拖拽布局, ↻ 一键还原`}
        </span>
        <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={load}>刷新</Button>
        <Button size="sm" variant="ghost" className="h-7 w-7 p-0" title={fullscreen ? '退出全屏' : '全屏'}
          onClick={() => setFullscreen((f) => !f)}>
          {fullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
        </Button>
      </div>
      <div className="flex-1 relative min-h-0">
        {stale && (
          <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none">
            <div className="text-center text-sm text-muted-foreground bg-card/80 border rounded-lg px-6 py-4">
              该数据源图谱暂无节点
              <div className="text-xs mt-1">激活模型或保存 active 模型会自动重建图谱；也可点上方「图谱生成」手动重建</div>
            </div>
          </div>
        )}
        {/* viewMode=edit 仅为开启节点拖拽; 无 onConnect 落库, 连线不会持久化 */}
        <KnowledgeGraphView
          graphType={view}
          viewMode="edit"
          isLoading={loading}
          onNodeSelect={(n) => setSelected(n)}
        />
        {/* 节点详情面板 */}
        {selected && (
          <div className="absolute right-2 top-2 bottom-2 z-20 w-72 bg-card/95 backdrop-blur border rounded-lg shadow-xl flex flex-col">
            <div className="flex items-center gap-2 px-3 py-2 border-b shrink-0">
              <Badge variant="outline" className="text-[10px]">{selected.label}</Badge>
              <span className="text-sm font-medium truncate">
                {selected.properties?.label || selected.properties?.name || selected.id}
              </span>
              <button className="ml-auto text-muted-foreground hover:text-foreground" onClick={() => setSelected(null)}>
                <X className="h-4 w-4" />
              </button>
            </div>
            <ScrollArea className="flex-1">
              <div className="p-3 space-y-2 text-xs">
                {Object.entries(selected.properties || {}).map(([k, v]) => (
                  <div key={k} className="flex gap-2 border-b border-dashed pb-1.5 last:border-0">
                    <span className="text-muted-foreground w-28 shrink-0">{k}</span>
                    <span className="font-mono break-all">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
                  </div>
                ))}
                <div className="text-[10px] text-muted-foreground pt-1">ID: {selected.id}</div>
              </div>
            </ScrollArea>
          </div>
        )}
      </div>
    </div>
  );
}

interface TagDef { id: number; name: string; color?: string; entity_type?: string }
interface TagValue { entity_id: string; value?: string }

export function ObjectTagsTab({ objects }: { objects: { key: string; display_name?: string; primary_table?: string }[] }) {
  const [tags, setTags] = useState<TagDef[]>([]);
  const [values, setValues] = useState<Record<number, TagValue[]>>({});
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/tags', { params: { page: 1, size: 200 } });
      const list: TagDef[] = data?.items || data || [];
      setTags(list);
      const entries = await Promise.all(list.map(async (t) => {
        try {
          const { data: vals } = await client.get(`/tags/${t.id}/values`);
          return [t.id, (Array.isArray(vals) ? vals : vals?.items || []) as TagValue[]] as const;
        } catch {
          return [t.id, [] as TagValue[]] as const;
        }
      }));
      setValues(Object.fromEntries(entries));
    } catch {
      setTags([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // 对象 ← 标签: 以 primary_table 匹配 entity_id(打标实体为物理表, 与血缘一致)
  const tagsOf = (tableName?: string): TagDef[] => {
    if (!tableName) return [];
    const tn = tableName.toLowerCase();
    return tags.filter((t) => (values[t.id] || []).some(
      (v) => String(v.entity_id || '').toLowerCase() === tn));
  };
  const usedTagIds = new Set<number>();
  objects.forEach((o) => tagsOf(o.primary_table).forEach((t) => usedTagIds.add(t.id)));
  const unusedTags = tags.filter((t) => !usedTagIds.has(t.id));

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          对象按主表继承标签（打标实体 = 物理表）；标签定义与打标在标签中心维护。
        </div>
        <Button size="sm" variant="outline" onClick={load} disabled={loading}>
          {loading ? '加载中…' : '刷新'}
        </Button>
      </div>
      <ScrollArea className="h-[55vh]">
        <div className="space-y-2 pr-3">
          {objects.map((o) => {
            const ts = tagsOf(o.primary_table);
            return (
              <div key={o.key} className="flex items-center gap-3 border rounded-lg px-3 py-2">
                <div className="min-w-0 w-52">
                  <div className="text-sm font-medium truncate">{o.display_name || o.key}</div>
                  <div className="text-[11px] font-mono text-muted-foreground truncate">{o.primary_table || '未绑定表'}</div>
                </div>
                <div className="flex flex-wrap gap-1">
                  {ts.length ? ts.map((t) => (
                    <Badge key={t.id} variant="outline" className="text-[11px]"
                      style={t.color ? { borderColor: t.color, color: t.color } : undefined}>
                      <TagIcon className="h-3 w-3 mr-1" />{t.name}
                    </Badge>
                  )) : <span className="text-xs text-muted-foreground">—</span>}
                </div>
              </div>
            );
          })}
          {!objects.length && <div className="text-sm text-muted-foreground">模型暂无对象</div>}
        </div>
      </ScrollArea>
      {unusedTags.length > 0 && (
        <div className="text-xs text-muted-foreground border-t pt-2">
          未被本模型任何表使用的标签：
          {unusedTags.slice(0, 12).map((t) => (
            <Badge key={t.id} variant="outline" className="ml-1 text-[10px] font-normal">{t.name}</Badge>
          ))}
          {unusedTags.length > 12 && ` 等 ${unusedTags.length} 个`}
        </div>
      )}
    </div>
  );
}

/** 图谱/知识库同步状态徽章（「关系图谱」页与「数据建模」工具栏共用，只读 30s 轮询）。
 *
 * 口径全来自服务端 /sync-status：比对哪张命名图由 graph_target_for_model 定，
 * 目标知识库清单由 sync_targets_for_model 定。徽章只负责把“比对的是什么 / 同步到哪”说人话：
 * · “滞后”不是错误，是**图里的对象比模型少**（模型改过而图未重建），处置就是点「图谱生成」；
 * · 知识库面直接带目标库名（不只给“已同步”，也不露裸 id）；
 * · 源本体按设计不做 RAG（不入知识库），不能当故障黄点吓人。
 */
export function SyncBadges({ modelId, refreshKey, kind }: {
  modelId: number; refreshKey?: number; kind?: string;
}) {
  const [st, setSt] = useState<{
    graph: { state: string; classes?: number; objects?: number; graph_datasource_id?: number };
    kb: { state: string; error?: string; targets?: number; target_names?: string[]; synced_at?: string };
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetch_ = async () => {
      try {
        const { data } = await client.get(`/catalog/ontology/models/${modelId}/sync-status`);
        if (!cancelled) setSt(data);
      } catch {
        if (!cancelled) setSt(null);
      }
    };
    fetch_();
    const timer = setInterval(fetch_, 30000);   // 图谱/知识库为异步级联, 30s 轮询回显
    return () => { cancelled = true; clearInterval(timer); };
  }, [modelId, refreshKey]);

  if (!st) return null;
  const g = st.graph, k = st.kb;
  const dot = (ok: boolean, warn?: boolean) =>
    <span className={`inline-block w-2 h-2 rounded-full ${ok ? 'bg-green-500' : warn ? 'bg-amber-500' : 'bg-gray-400'}`} />;
  const gOk = g.state === 'synced', gWarn = g.state === 'lagging' || g.state === 'unreachable';
  const gLabel = ({
    synced: '已同步',
    // 方向写清：旧文案“滞后 1/4”看不出谁多谁少
    lagging: `滞后（图中 ${g.classes ?? 0}/模型 ${g.objects ?? 0}）`,
    absent: '未生成', draft: '草案未激活', unreachable: '图服务不可达',
  } as Record<string, string>)[g.state] || g.state;
  const gNeedRebuild = g.state === 'lagging' || g.state === 'absent';
  const gTip = [
    g.graph_datasource_id == null ? '' : `比对命名图 ds:${g.graph_datasource_id}`,
    `图中 ${g.classes ?? 0} 个对象类 / 模型 ${g.objects ?? 0} 个对象`,
    g.state === 'lagging'
      ? '含义：模型已改过而图没跟着重建（不是数据错），点「图谱生成」重建即对齐'
      : g.state === 'absent' ? '含义：该命名图里还没有本模型的对象，点「图谱生成」建图' : '',
  ].filter(Boolean).join('；');

  const kTargets = k.target_names || [];
  const kOk = k.state === 'success';
  // 源本体不参与 RAG 上云 = 按设计，不算缺口（旧口径会把它当“未开启同步”让人以为坏了）
  const kByDesign = (k.state === 'no_kb' || k.state === 'no_kb_bound') && kind === 'source';
  const kWarn = k.state === 'failed' || k.state === 'no_kb_bound';
  const kStateLabel = ({
    success: '已同步',
    failed: k.error ? `失败: ${k.error.slice(0, 30)}` : '失败',
    pending: '待同步', removed: '已下线',
    no_kb: kByDesign ? '不适用（源本体）' : '未开启同步',
    no_kb_bound: kByDesign ? '不适用（源本体）' : '未绑定知识库',
  } as Record<string, string>)[k.state] || k.state;
  const kLabel = kStateLabel + (kTargets.length
    ? ` → ${kTargets[0]}${kTargets.length > 1 ? ` 等 ${kTargets.length} 个` : ''}` : '');
  const kTip = [
    kTargets.length ? `目标知识库：${kTargets.join(' / ')}`
      : (kByDesign ? '源本体只服务语义检索/取数，按设计不入知识库' : '未解析到目标知识库'),
    k.synced_at ? `最近同步：${k.synced_at}` : '',
    k.error ? `错误：${k.error}` : '',
  ].filter(Boolean).join('；');
  return (
    <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
      <span className="flex items-center gap-1" title={gTip}>
        {dot(gOk, gWarn)}图谱 {gLabel}
        {gNeedRebuild && <span className="text-amber-600">· 需重建</span>}
      </span>
      <span className="flex items-center gap-1" title={kTip}>
        {dot(kOk, kWarn)}知识库 {kLabel}
      </span>
    </span>
  );
}

/** Network 图标 re-export, 供页签使用 */
export { Network };

/** M2/M3/M4 建模声明解析（行为/规则/场景各自独立页签共用）。 */
type M234Doc = {
  objects?: { key?: string; display_name?: string; actions?: { key?: string; label?: string; effect?: string; read_only?: boolean; risk?: string }[] }[];
  rules?: { key?: string; type?: string; statement?: string; source?: string; enforcement?: { subject?: string; action?: string; level?: string; applies_to?: string[] } }[];
  scenarios?: { key?: string; title?: string; goal?: string; uses_objects?: string[]; uses_actions?: string[]; uses_rules?: string[]; eval_seed?: string[];
    route?: { sources?: { source_ontology?: string; datasource_name?: string; object_keys?: string[] }[]; join_hint?: string } }[];
};

function parseM234(jsonContent: string): M234Doc | null {
  try {
    const doc = JSON.parse(jsonContent || '{}');
    // 存量源本体 doc 用旧字段名（object_key/name）：在读取边界补齐 key/display_name，
    // 否则对象下拉与标签会渲染出 undefined（只适配展示，不改写保存内容）
    if (Array.isArray(doc?.objects)) doc.objects = normalizeDocObjects(doc.objects).objects;
    return doc as M234Doc;
  } catch {
    return null;
  }
}

const ruleBadge = (type?: string) =>
  type === 'permission' ? '权限规则' : type === 'metric_constraint' ? '口径规则'
    : type === 'business_constraint' ? '业务约束' : type === 'quality' ? '质量规则' : (type || '规则');

function M234Empty({ hint }: { hint: string }) {
  return <div className="p-4 text-sm text-muted-foreground">{hint}</div>;
}

/** 编辑落库统一入口：mutate 改 doc，onSave 走 ontologyApi.save（save_draft 级联重建派生物）。 */
function applyEdit(jsonContent: string, mutate: (doc: any) => void, onSave: (next: string) => void) {
  let doc: any;
  try {
    doc = JSON.parse(jsonContent || '{}');
  } catch {
    return;
  }
  mutate(doc);
  onSave(JSON.stringify(doc, null, 2));
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  );
}

/** 表单分节：小标题 + 提示，把长表单按语义分组（避免平铺刷屏）。 */
function FormSection({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2 border-t pt-2.5">
      <div className="flex items-baseline gap-2">
        <span className="text-xs font-medium">{title}</span>
        {hint && <span className="text-[10px] text-muted-foreground">{hint}</span>}
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

/** 条目卡片右上角的编辑/删除操作（与其他模块一致）。 */
// ── 业务本体路由（路由索引层）─────────────────────────────────────
// 业务本体对象只留概念 + route（指向源本体），物理绑定唯一在源本体；
// 源本体/对象一律用选择框选（ui-resource-display：显示 name，不手输 id）。

export type RouteValue = {
  mode: 'source' | 'object_filter';
  source_ontology: string;
  datasource_name?: string;
  object_key?: string;
  filter_hints?: { dimension: string; examples: string[] }[];
};

/** active 源本体清单（名称稳定键；选项显示 name） */
function useSourceOntologies() {
  const [sources, setSources] = useState<{ name: string; id: number }[]>([]);
  useEffect(() => {
    ontologyApi.list().then((r) => {
      setSources((r.data.items || [])
        .filter((m) => m.status === 'active' && m.kind !== 'system')
        .map((m) => ({ name: m.name, id: m.id })));
    }).catch(() => setSources([]));
  }, []);
  return sources;
}

/** 对象/场景的路由编辑器：选目标源本体（+可选源对象 +源内过滤提示） */
function RouteEditor({ value, onChange }: { value: RouteValue; onChange: (v: RouteValue) => void }) {
  const sources = useSourceOntologies();
  const [srcObjects, setSrcObjects] = useState<{ key: string; label: string }[]>([]);

  // 选源后懒加载其对象清单与数据源名（对象选择框的选项来源）
  useEffect(() => {
    const src = sources.find((s) => s.name === value.source_ontology);
    if (!src) { setSrcObjects([]); return; }
    let cancelled = false;
    ontologyApi.get(src.id).then((r) => {
      if (cancelled) return;
      let doc: any = {};
      try { doc = JSON.parse(r.data.json_content || '{}'); } catch { /* 非法 JSON：对象选项留空 */ }
      setSrcObjects(normalizeDocObjects(doc.objects || []).objects
        .map((o: any) => ({ key: o.key, label: o.display_name || o.key })));
      const dsName = String(doc.datasource_name || '');
      if (dsName && dsName !== (value.datasource_name || '')) {
        onChange({ ...value, datasource_name: dsName });
      }
    }).catch(() => setSrcObjects([]));
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.source_ontology, sources]);

  const hints = value.filter_hints || [];
  const setHints = (next: { dimension: string; examples: string[] }[]) =>
    onChange({ ...value, filter_hints: next });

  return (
    <div className="space-y-3">
      <Field label="路由模式">
        <Select value={value.mode} onValueChange={(v) => onChange({ ...value, mode: v as RouteValue['mode'] })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="source">选源（问题指向整个数据源/站点）</SelectItem>
            <SelectItem value="object_filter">选对象+过滤（问题落在源内对象/维度）</SelectItem>
          </SelectContent>
        </Select>
      </Field>
      <Field label="目标源本体（选择框，名称为稳定键）">
        <Select value={value.source_ontology || undefined}
          onValueChange={(v) => onChange({ ...value, source_ontology: v, object_key: undefined })}>
          <SelectTrigger><SelectValue placeholder={sources.length ? '选择源本体' : '暂无 active 源本体'} /></SelectTrigger>
          <SelectContent>
            {sources.map((s) => <SelectItem key={s.id} value={s.name}>{s.name}</SelectItem>)}
          </SelectContent>
        </Select>
      </Field>
      {value.mode === 'object_filter' && (
        <Field label="目标源对象（来自所选源本体）">
          <Select value={value.object_key || undefined}
            onValueChange={(v) => onChange({ ...value, object_key: v })}>
            <SelectTrigger><SelectValue placeholder={value.source_ontology ? (srcObjects.length ? '选择源对象' : '加载中/源本体无对象') : '请先选源本体'} /></SelectTrigger>
            <SelectContent>
              {srcObjects.map((o) => <SelectItem key={o.key} value={o.key}>{o.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </Field>
      )}
      <Field label="源内过滤提示（可选；如 站点=日本/美国，是维度过滤、不是选源）">
        {hints.map((h, i) => (
          <div key={i} className="flex gap-2 mb-1">
            <Input placeholder="维度名，如 站点" value={h.dimension}
              onChange={(e) => { const next = [...hints]; next[i] = { ...h, dimension: e.target.value }; setHints(next); }} />
            <Input placeholder="例子，逗号分隔" value={(h.examples || []).join(',')}
              onChange={(e) => { const next = [...hints]; next[i] = { ...h, examples: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) }; setHints(next); }} />
            <Button variant="outline" size="sm" onClick={() => setHints(hints.filter((_, j) => j !== i))}>删</Button>
          </div>
        ))}
        <Button variant="outline" size="sm"
          onClick={() => setHints([...hints, { dimension: '', examples: [] }])}>+ 过滤提示</Button>
      </Field>
    </div>
  );
}

function ItemOps({ onEdit, onDelete }: { onEdit: () => void; onDelete: () => void }) {
  return (
    <div className="flex items-center gap-1 ml-auto shrink-0">
      <Button variant="ghost" size="icon" className="h-7 w-7" title="编辑" onClick={onEdit}>
        <Pencil className="h-3.5 w-3.5" />
      </Button>
      <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" title="删除" onClick={onDelete}>
        <Trash2 className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

/** 页签工具条：标题 + 新建按钮（与业务术语模块同款交互）。 */
function TabToolbar({ title, count, unit, onCreate, createLabel }: {
  title: string; count: number; unit: string; onCreate: () => void; createLabel: string;
}) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <Badge variant="outline">{title}</Badge>
      <span className="text-sm font-medium">{count} {unit}</span>
      <Button size="sm" className="ml-auto" onClick={onCreate}>
        <Plus className="h-4 w-4 mr-1" />{createLabel}
      </Button>
    </div>
  );
}

/** 多选小件：checkbox 群（对象/行为/规则选择用，选项显示业务名，右侧小字技术键值）。 */
function CheckList({ options, value, onChange, empty }: {
  options: { value: string; label: string }[]; value: string[];
  onChange: (v: string[]) => void; empty: string;
}) {
  if (!options.length) return <div className="text-xs text-muted-foreground">{empty}</div>;
  return (
    <div className="max-h-36 overflow-y-auto overflow-x-hidden border rounded-md p-2 space-y-0.5">
      {options.map((o) => (
        <label key={o.value} className="flex items-center gap-2 text-xs cursor-pointer min-w-0 py-0.5">
          <input
            type="checkbox"
            className="shrink-0"
            checked={value.includes(o.value)}
            onChange={(e) => onChange(e.target.checked
              ? [...value, o.value]
              : value.filter((x) => x !== o.value))}
          />
          <span className="flex-1 min-w-0 truncate" title={o.label}>{o.label}</span>
          <span className="shrink-0 max-w-[45%] truncate text-right font-mono text-[10px] text-muted-foreground" title={o.value}>{o.value}</span>
        </label>
      ))}
    </div>
  );
}

/** M2 行为页签：对象级分析/操作行为（支持新建/编辑/删除，落库走 save_draft 级联）。 */
export function ModelActionsTab({ jsonContent, onSave }: { jsonContent: string; onSave: (next: string) => void }) {
  const doc = parseM234(jsonContent);
  const [editing, setEditing] = useState<null | {
    mode: 'create' | 'edit'; origKey?: string; objectKey: string; key: string;
    label: string; effect: string; read_only: boolean; risk: string; requires_approval: boolean;
  }>(null);

  if (!doc) return <M234Empty hint="模型内容不是合法 JSON，无法解析建模声明" />;
  const objects = doc.objects || [];
  const actionsByObj = objects
    .map((o) => ({ obj: o, acts: o.actions || [] }))
    .filter((x) => x.acts.length > 0);
  const totalActs = actionsByObj.reduce((n, x) => n + x.acts.length, 0);

  const submit = () => {
    if (!editing || !editing.key.trim() || !editing.objectKey) return;
    applyEdit(jsonContent, (d) => {
      const obj = (d.objects || []).find((o: any) => o.key === editing.objectKey);
      if (!obj) return;
      obj.actions = obj.actions || [];
      const payload = {
        key: editing.key.trim(), label: editing.label.trim() || editing.key.trim(),
        effect: editing.effect.trim(), read_only: editing.read_only,
        ...(editing.read_only ? { requires_approval: false } : {
          risk: editing.risk || 'medium', requires_approval: editing.requires_approval,
        }),
      };
      if (editing.mode === 'edit' && editing.origKey) {
        const idx = obj.actions.findIndex((a: any) => a.key === editing.origKey);
        if (idx >= 0) obj.actions[idx] = { ...obj.actions[idx], ...payload };
        else obj.actions.push(payload);
      } else {
        obj.actions.push(payload);
      }
    }, onSave);
    setEditing(null);
  };

  const remove = (objKey: string, akey: string) => {
    if (!window.confirm(`确认删除行为「${akey}」？`)) return;
    applyEdit(jsonContent, (d) => {
      const obj = (d.objects || []).find((o: any) => o.key === objKey);
      if (obj) obj.actions = (obj.actions || []).filter((a: any) => a.key !== akey);
    }, onSave);
  };

  return (
    <ScrollArea className="h-full">
      <div className="p-4">
        <TabToolbar title="M2 行为" count={totalActs} unit="个分析/操作行为"
          createLabel="新建行为"
          onCreate={() => setEditing({
            mode: 'create', objectKey: objects[0]?.key || '', key: '', label: '',
            effect: '', read_only: true, risk: 'medium', requires_approval: true,
          })} />
        {totalActs === 0 && <M234Empty hint="本模型暂无行为声明（M2 建模位为空）" />}
        <div className="space-y-3">
          {actionsByObj.map(({ obj, acts }) => (
            <div key={obj.key} className="border rounded-lg p-3">
              <div className="text-sm font-medium mb-1.5">
                {obj.display_name || obj.key}
                <span className="text-xs text-muted-foreground ml-2">{obj.key}</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {acts.map((a) => (
                  <span key={a.key}
                    className="inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs"
                    title={a.effect || ''}>
                    {a.label || a.key}
                    <span className={a.read_only ? 'text-muted-foreground' : 'text-amber-600'}>
                      {a.read_only ? '·只读' : `·写${a.risk ? `(${a.risk})` : ''}`}
                    </span>
                    <button className="text-muted-foreground hover:text-foreground" title="编辑"
                      onClick={() => setEditing({
                        mode: 'edit', origKey: a.key, objectKey: obj.key || '', key: a.key || '',
                        label: a.label || '', effect: a.effect || '', read_only: !!a.read_only,
                        risk: a.risk || 'medium', requires_approval: !a.read_only,
                      })}>
                      <Pencil className="h-3 w-3" />
                    </button>
                    <button className="text-muted-foreground hover:text-destructive" title="删除"
                      onClick={() => remove(obj.key || '', a.key || '')}>
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      <Dialog open={!!editing} onOpenChange={(v) => !v && setEditing(null)}>
        <DialogContent className="max-w-md max-h-[88vh] overflow-y-auto overflow-x-hidden">
          <DialogHeader>
            <DialogTitle>{editing?.mode === 'edit' ? '编辑行为' : '新建行为'}</DialogTitle>
          </DialogHeader>
          {editing && (
            <div className="space-y-3">
              <Field label="所属对象">
                <Select value={editing.objectKey} disabled={editing.mode === 'edit'}
                  onValueChange={(v) => setEditing({ ...editing, objectKey: v })}>
                  <SelectTrigger><SelectValue placeholder="选择对象" /></SelectTrigger>
                  <SelectContent>
                    {objects.map((o) => (
                      <SelectItem key={o.key} value={o.key || ''}>
                        {o.display_name || o.key}（{o.key}）
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="行为 key（全局唯一，如 case.trend）">
                <Input value={editing.key} disabled={editing.mode === 'edit'}
                  onChange={(e) => setEditing({ ...editing, key: e.target.value })} />
              </Field>
              <Field label="展示名">
                <Input value={editing.label} placeholder="如：趋势分析"
                  onChange={(e) => setEditing({ ...editing, label: e.target.value })} />
              </Field>
              <Field label="效果说明">
                <Input value={editing.effect} placeholder="如：按时间粒度观察指标走势"
                  onChange={(e) => setEditing({ ...editing, effect: e.target.value })} />
              </Field>
              <div className="flex items-center justify-between">
                <Label className="text-xs">只读分析行为（不进审批通道）</Label>
                <Switch checked={editing.read_only}
                  onCheckedChange={(v) => setEditing({ ...editing, read_only: v })} />
              </div>
              {!editing.read_only && (
                <>
                  <Field label="风险级别">
                    <Select value={editing.risk}
                      onValueChange={(v) => setEditing({ ...editing, risk: v })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="low">low（低）</SelectItem>
                        <SelectItem value="medium">medium（中）</SelectItem>
                        <SelectItem value="high">high（高）</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                  <div className="flex items-center justify-between">
                    <Label className="text-xs">需人工审批</Label>
                    <Switch checked={editing.requires_approval}
                      onCheckedChange={(v) => setEditing({ ...editing, requires_approval: v })} />
                  </div>
                </>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)}>取消</Button>
            <Button onClick={submit} disabled={!editing?.key.trim()}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ScrollArea>
  );
}

/** M3 规则页签：口径/约束/质量/权限规则（支持新建/编辑/删除）。 */
export function ModelRulesTab({ jsonContent, onSave }: { jsonContent: string; onSave: (next: string) => void }) {
  const doc = parseM234(jsonContent);
  const [editing, setEditing] = useState<null | {
    mode: 'create' | 'edit'; origKey?: string; key: string; type: string;
    statement: string; source: string;
    subject: string; action: string; level: string; applies_to: string[];
  }>(null);

  if (!doc) return <M234Empty hint="模型内容不是合法 JSON，无法解析建模声明" />;
  const rules = doc.rules || [];
  const objOptions = (doc.objects || []).map((o) => ({ value: o.key || '', label: o.display_name || o.key || '' }));

  const submit = () => {
    if (!editing || !editing.key.trim()) return;
    applyEdit(jsonContent, (d) => {
      d.rules = d.rules || [];
      const enforcement = editing.type === 'permission'
        ? { subject: editing.subject.trim(), action: editing.action.trim() || '*', level: editing.level }
        : { applies_to: editing.applies_to };
      const payload = {
        key: editing.key.trim(), type: editing.type,
        statement: editing.statement.trim(), source: editing.source.trim(), enforcement,
      };
      if (editing.mode === 'edit' && editing.origKey) {
        const idx = d.rules.findIndex((r: any) => r.key === editing.origKey);
        if (idx >= 0) d.rules[idx] = { ...d.rules[idx], ...payload };
        else d.rules.push(payload);
      } else {
        d.rules.push(payload);
      }
    }, onSave);
    setEditing(null);
  };

  const remove = (rkey: string) => {
    if (!window.confirm(`确认删除规则「${rkey}」？`)) return;
    applyEdit(jsonContent, (d) => {
      d.rules = (d.rules || []).filter((r: any) => r.key !== rkey);
    }, onSave);
  };

  return (
    <ScrollArea className="h-full">
      <div className="p-4">
        <TabToolbar title="M3 规则" count={rules.length} unit="条约束/口径"
          createLabel="新建规则"
          onCreate={() => setEditing({
            mode: 'create', key: '', type: 'business_constraint', statement: '', source: '',
            subject: 'role:admin', action: '*', level: 'allow', applies_to: [],
          })} />
        {rules.length === 0 && <M234Empty hint="本模型暂无规则声明（M3 建模位为空）" />}
        <div className="space-y-2">
          {rules.map((r) => (
            <div key={r.key} className="border rounded-lg p-3">
              <div className="flex items-center gap-2">
                <Badge variant="secondary">{ruleBadge(r.type)}</Badge>
                {r.enforcement?.level && <Badge variant="outline">{r.enforcement.level}</Badge>}
                <span className="text-xs text-muted-foreground">{r.key}</span>
                <ItemOps
                  onEdit={() => setEditing({
                    mode: 'edit', origKey: r.key, key: r.key || '', type: r.type || 'business_constraint',
                    statement: r.statement || '', source: r.source || '',
                    subject: r.enforcement?.subject || 'role:admin',
                    action: r.enforcement?.action || '*',
                    level: r.enforcement?.level || 'allow',
                    applies_to: r.enforcement?.applies_to || [],
                  })}
                  onDelete={() => remove(r.key || '')} />
              </div>
              <div className="text-sm mt-1.5">{r.statement || '(缺规则说明)'}</div>
              {r.enforcement?.subject && (
                <div className="text-xs text-muted-foreground mt-1">
                  适用: {r.enforcement.subject}
                  {r.enforcement.action ? ` × ${r.enforcement.action}` : ''}
                </div>
              )}
              {r.source && <div className="text-xs text-muted-foreground mt-0.5">依据: {r.source}</div>}
            </div>
          ))}
        </div>
      </div>

      <Dialog open={!!editing} onOpenChange={(v) => !v && setEditing(null)}>
        <DialogContent className="max-w-md max-h-[88vh] overflow-y-auto overflow-x-hidden">
          <DialogHeader>
            <DialogTitle>{editing?.mode === 'edit' ? '编辑规则' : '新建规则'}</DialogTitle>
          </DialogHeader>
          {editing && (
            <div className="space-y-3">
              <Field label="规则 key（全局唯一，如 rule_exclude_deleted）">
                <Input value={editing.key} disabled={editing.mode === 'edit'}
                  onChange={(e) => setEditing({ ...editing, key: e.target.value })} />
              </Field>
              <Field label="规则类型">
                <Select value={editing.type} disabled={editing.mode === 'edit'}
                  onValueChange={(v) => setEditing({ ...editing, type: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="business_constraint">业务约束</SelectItem>
                    <SelectItem value="metric_constraint">口径规则</SelectItem>
                    <SelectItem value="quality">质量规则</SelectItem>
                    <SelectItem value="permission">权限规则</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field label="规则说明（人话，可上云）">
                <Textarea rows={2} value={editing.statement} placeholder="如：业务口径一律排除已删除记录"
                  onChange={(e) => setEditing({ ...editing, statement: e.target.value })} />
              </Field>
              <Field label="依据（来源/出处，可选）">
                <Input value={editing.source} placeholder="如：业务约定 / ontology-modeling §3"
                  onChange={(e) => setEditing({ ...editing, source: e.target.value })} />
              </Field>
              {editing.type === 'permission' ? (
                <>
                  <Field label="授权主体（role:<角色名>）">
                    <Input value={editing.subject} placeholder="role:admin"
                      onChange={(e) => setEditing({ ...editing, subject: e.target.value })} />
                  </Field>
                  <Field label="动作（action key 或 * 通配）">
                    <Input value={editing.action} placeholder="*"
                      onChange={(e) => setEditing({ ...editing, action: e.target.value })} />
                  </Field>
                  <Field label="授权级别">
                    <Select value={editing.level}
                      onValueChange={(v) => setEditing({ ...editing, level: v })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="allow">allow（允许）</SelectItem>
                        <SelectItem value="require_approval">require_approval（需审批）</SelectItem>
                        <SelectItem value="deny">deny（拒绝）</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                </>
              ) : (
                <Field label="适用对象">
                  <CheckList options={objOptions} value={editing.applies_to} empty="本模型暂无对象"
                    onChange={(v) => setEditing({ ...editing, applies_to: v })} />
                </Field>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)}>取消</Button>
            <Button onClick={submit} disabled={!editing?.key.trim()}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ScrollArea>
  );
}

/** M4 场景页签：使用场景（支持新建/编辑/删除）。 */
export function ModelScenariosTab({ jsonContent, onSave }: { jsonContent: string; onSave: (next: string) => void }) {
  const doc = parseM234(jsonContent);
  const [editing, setEditing] = useState<null | {
    mode: 'create' | 'edit'; origKey?: string; key: string; title: string; goal: string;
    uses_objects: string[]; uses_actions: string[]; uses_rules: string[]; eval_seed: string;
    route_sources: string[]; join_hint: string;
  }>(null);

  if (!doc) return <M234Empty hint="模型内容不是合法 JSON，无法解析建模声明" />;
  const scenarios = doc.scenarios || [];
  const srcOptions = useSourceOntologies().map((s) => ({ value: s.name, label: s.name }));
  const objOptions = (doc.objects || []).map((o) => ({ value: o.key || '', label: o.display_name || o.key || '' }));
  const actOptions = (doc.objects || []).flatMap((o) => (o.actions || [])
    .map((a) => ({ value: a.key || '', label: `${a.label || a.key}（${o.key}）` })));
  const ruleOptions = (doc.rules || []).map((r) => ({ value: r.key || '', label: r.statement || r.key || '' }));

  const submit = () => {
    if (!editing || !editing.key.trim()) return;
    applyEdit(jsonContent, (d) => {
      d.scenarios = d.scenarios || [];
      const payload: any = {
        key: editing.key.trim(), title: editing.title.trim() || editing.key.trim(),
        goal: editing.goal.trim(), uses_objects: editing.uses_objects,
        uses_actions: editing.uses_actions, uses_rules: editing.uses_rules,
        eval_seed: editing.eval_seed.split('\n').map((x) => x.trim()).filter(Boolean),
      };
      // 场景路由（单源/跨源）：object_keys 从 uses_objects 的对象 route 自动映射，
      // 不猜绑定；未选路由源时置 undefined（spread 后丢弃，等效删除旧 route）。
      if (editing.route_sources.length) {
        const routeByKey: Record<string, any> = {};
        (d.objects || []).forEach((o: any) => { if (o.route) routeByKey[o.key] = o.route; });
        payload.route = {
          sources: editing.route_sources.map((name) => {
            const rs = editing.uses_objects
              .map((k) => routeByKey[k])
              .filter((r: any) => r && r.source_ontology === name);
            return {
              source_ontology: name,
              datasource_name: ((rs.find((r: any) => r?.datasource_name) as any) || {}).datasource_name || '',
              object_keys: Array.from(new Set(rs.map((r: any) => r?.object_key).filter(Boolean))),
            };
          }),
          ...(editing.join_hint.trim() ? { join_hint: editing.join_hint.trim() } : {}),
        };
      } else {
        payload.route = undefined;
      }
      if (editing.mode === 'edit' && editing.origKey) {
        const idx = d.scenarios.findIndex((s: any) => s.key === editing.origKey);
        if (idx >= 0) d.scenarios[idx] = { ...d.scenarios[idx], ...payload };
        else d.scenarios.push(payload);
      } else {
        d.scenarios.push(payload);
      }
    }, onSave);
    setEditing(null);
  };

  const remove = (skey: string) => {
    if (!window.confirm(`确认删除场景「${skey}」？`)) return;
    applyEdit(jsonContent, (d) => {
      d.scenarios = (d.scenarios || []).filter((s: any) => s.key !== skey);
    }, onSave);
  };

  return (
    <ScrollArea className="h-full">
      <div className="p-4">
        <TabToolbar title="M4 场景" count={scenarios.length} unit="个使用场景"
          createLabel="新建场景"
          onCreate={() => setEditing({
            mode: 'create', key: '', title: '', goal: '',
            uses_objects: [], uses_actions: [], uses_rules: [], eval_seed: '',
            route_sources: [], join_hint: '',
          })} />
        {scenarios.length === 0 && <M234Empty hint="本模型暂无场景声明（M4 建模位为空）" />}
        <div className="space-y-2">
          {scenarios.map((s) => (
            <div key={s.key} className="border rounded-lg p-3">
              <div className="flex items-center">
                <div className="text-sm font-medium">
                  {s.title || s.key}
                  <span className="text-xs text-muted-foreground ml-2">{s.key}</span>
                </div>
                <ItemOps
                  onEdit={() => setEditing({
                    mode: 'edit', origKey: s.key, key: s.key || '', title: s.title || '',
                    goal: s.goal || '', uses_objects: s.uses_objects || [],
                    uses_actions: s.uses_actions || [], uses_rules: s.uses_rules || [],
                    eval_seed: (s.eval_seed || []).join('\n'),
                    route_sources: (s.route?.sources || []).map((e) => e.source_ontology || '').filter(Boolean),
                    join_hint: s.route?.join_hint || '',
                  })}
                  onDelete={() => remove(s.key || '')} />
              </div>
              {s.goal && <div className="text-sm text-muted-foreground mt-1">{s.goal}</div>}
              <div className="flex flex-wrap gap-1.5 mt-2">
                {(s.uses_objects || []).map((x) => <Badge key={`o${x}`} variant="secondary">对象 {x}</Badge>)}
                {(s.uses_actions || []).map((x) => <Badge key={`a${x}`} variant="secondary">行为 {x}</Badge>)}
                {(s.uses_rules || []).map((x) => <Badge key={`r${x}`} variant="secondary">规则 {x}</Badge>)}
              </div>
              {(s.route?.sources || []).length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 mt-2">
                  {(s.route!.sources || []).map((e) => (
                    <Badge key={e.source_ontology} variant="outline" className="text-[10px]">
                      路由源: {e.source_ontology}{e.object_keys?.length ? ` · ${e.object_keys.join('/')}` : ''}
                    </Badge>
                  ))}
                  {s.route!.join_hint && (
                    <span className="text-xs text-muted-foreground">跨源关联: {s.route!.join_hint}</span>
                  )}
                </div>
              )}
              {(s.eval_seed || []).length > 0 && (
                <div className="text-xs text-muted-foreground mt-1.5">
                  典型问题: {s.eval_seed!.join(' / ')}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <Dialog open={!!editing} onOpenChange={(v) => !v && setEditing(null)}>
        <DialogContent className="max-w-xl max-h-[88vh] overflow-y-auto overflow-x-hidden">
          <DialogHeader>
            <DialogTitle>{editing?.mode === 'edit' ? '编辑场景' : '新建场景'}</DialogTitle>
          </DialogHeader>
          {editing && (
            <div className="space-y-3 min-w-0">
              <div className="grid grid-cols-2 gap-3">
                <Field label="场景 key（全局唯一，如 ana_case_trend）">
                  <Input value={editing.key} disabled={editing.mode === 'edit'}
                    onChange={(e) => setEditing({ ...editing, key: e.target.value })} />
                </Field>
                <Field label="场景标题">
                  <Input value={editing.title} placeholder="如：案例趋势分析"
                    onChange={(e) => setEditing({ ...editing, title: e.target.value })} />
                </Field>
              </div>
              <Field label="目标">
                <Input value={editing.goal} placeholder="如：观察案例量走势与周期性变化"
                  onChange={(e) => setEditing({ ...editing, goal: e.target.value })} />
              </Field>
              <FormSection title="涉及范围" hint="勾选本场景用到的对象 / 行为 / 规则">
                <Field label="涉及对象">
                  <CheckList options={objOptions} value={editing.uses_objects} empty="本模型暂无对象"
                    onChange={(v) => setEditing({ ...editing, uses_objects: v })} />
                </Field>
                <Field label="涉及行为">
                  <CheckList options={actOptions} value={editing.uses_actions} empty="本模型暂无行为声明"
                    onChange={(v) => setEditing({ ...editing, uses_actions: v })} />
                </Field>
                <Field label="涉及规则">
                  <CheckList options={ruleOptions} value={editing.uses_rules} empty="本模型暂无规则声明"
                    onChange={(v) => setEditing({ ...editing, uses_rules: v })} />
                </Field>
              </FormSection>
              <FormSection title="评测种子" hint="eval 用例来源，每行一个自然语言问题">
                <Field label="典型问题（每行一个）">
                  <Textarea rows={3} value={editing.eval_seed} placeholder={"最近30天案例量趋势如何？"}
                    onChange={(e) => setEditing({ ...editing, eval_seed: e.target.value })} />
                </Field>
              </FormSection>
              <FormSection title="跨源路由" hint="数据去哪个源本体查；单源场景可不填">
                <Field label="路由源（跨源场景可多选）">
                  <CheckList options={srcOptions} value={editing.route_sources} empty="暂无 active 源本体"
                    onChange={(v) => setEditing({ ...editing, route_sources: v })} />
                </Field>
                <Field label="跨源关联说明（join_hint，人话口径，不含物理 JOIN）">
                  <Input value={editing.join_hint} placeholder="如：按公司编码关联两源的客户主数据"
                    onChange={(e) => setEditing({ ...editing, join_hint: e.target.value })} />
                </Field>
              </FormSection>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)}>取消</Button>
            <Button onClick={submit} disabled={!editing?.key.trim()}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ScrollArea>
  );
}

/** 手动新建对象按钮 + 对象表单（业务本体支持手工新增对象，不只 LLM 生成/编辑器改 JSON）。
 *  业务本体=路由索引层：对象填 route（选源本体/源对象），不绑物理表；
 *  源本体对象仍可绑物理表（物理事实唯一在源本体）。 */
export function ObjectCreateButton({ jsonContent, onSave }: { jsonContent: string; onSave: (next: string) => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ key: '', display_name: '', description: '', primary_table: '' });
  const [route, setRoute] = useState<RouteValue>({
    mode: 'object_filter', source_ontology: '', filter_hints: [],
  });

  let kind = '';
  try { kind = String(JSON.parse(jsonContent || '{}').kind || ''); } catch { /* 非法 JSON：按源本体表单 */ }
  const isBusiness = kind === 'business';

  const submit = () => {
    const key = form.key.trim();
    if (!key) return;
    if (isBusiness && !route.source_ontology.trim()) {
      window.alert('业务对象必须声明路由：请选择目标源本体');
      return;
    }
    let doc: any;
    try {
      doc = JSON.parse(jsonContent || '{}');
    } catch {
      return;
    }
    doc.objects = doc.objects || [];
    if (doc.objects.some((o: any) => o.key === key)) {
      window.alert(`对象 key 已存在: ${key}`);
      return;
    }
    const hints = (route.filter_hints || []).filter((h) => h.dimension.trim());
    doc.objects.push({
      key,
      display_name: form.display_name.trim() || key,
      description: form.description.trim(),
      ...(isBusiness
        ? {
            route: {
              mode: route.mode,
              source_ontology: route.source_ontology.trim(),
              datasource_name: route.datasource_name || '',
              ...(route.mode === 'object_filter' && route.object_key
                ? { object_key: route.object_key } : {}),
              ...(hints.length ? { filter_hints: hints } : {}),
            },
          }
        : (form.primary_table.trim() ? { primary_table: form.primary_table.trim() } : {})),
      properties: [], links: [],
    });
    onSave(JSON.stringify(doc, null, 2));
    setForm({ key: '', display_name: '', description: '', primary_table: '' });
    setRoute({ mode: 'object_filter', source_ontology: '', filter_hints: [] });
    setOpen(false);
  };

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        <Plus className="h-4 w-4 mr-1" />新建对象
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className={isBusiness ? 'max-w-lg max-h-[88vh] overflow-y-auto overflow-x-hidden' : 'max-w-md max-h-[88vh] overflow-y-auto overflow-x-hidden'}>
          <DialogHeader>
            <DialogTitle>新建业务对象</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <Field label="对象 key（唯一，snake_case，如 order_item）">
              <Input value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} />
            </Field>
            <Field label="业务显示名">
              <Input value={form.display_name} placeholder="如：订单明细"
                onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
            </Field>
            <Field label="业务描述">
              <Textarea rows={2} value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
            {isBusiness ? (
              <RouteEditor value={route} onChange={setRoute} />
            ) : (
              <Field label="绑定物理表（可选；数据产品登记后可在编辑器补绑定）">
                <Input value={form.primary_table} placeholder="如：t_order_item"
                  onChange={(e) => setForm({ ...form, primary_table: e.target.value })} />
              </Field>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
            <Button onClick={submit} disabled={!form.key.trim()}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** 编辑已有对象（标量字段 + 别名 + 路由/主表）。
 *  2026-10-27 拍板：废除「修改仅 AS-BOT」硬禁令，用户侧开放页面内编辑。
 *  与新建同一落库路径：改 canonical doc → saveDraft（存草案，两段式第一段），
 *  发布才级联生效；属性/关系不在本表单（保留原值，量大且由 AS-BOT/JSON 维护）。
 *  业务本体=路由索引层（编 route）；源本体=物理事实（编 primary_table）。 */
export function ObjectEditButton({ jsonContent, onSave, target }: {
  jsonContent: string;
  onSave: (next: string) => void;
  target: {
    key: string; display_name?: string; description?: string; aliases?: string[];
    primary_table?: string;
    route?: { mode?: string; source_ontology?: string; datasource_name?: string;
      object_key?: string; filter_hints?: { dimension?: string; examples?: string[] }[] };
  };
}) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(() => ({
    display_name: target.display_name || '',
    description: target.description || '',
    aliases: (target.aliases || []).join(', '),
    primary_table: target.primary_table || '',
  }));
  const initRoute = (): RouteValue => ({
    mode: target.route?.mode === 'source' ? 'source' : 'object_filter',
    source_ontology: target.route?.source_ontology || '',
    datasource_name: target.route?.datasource_name || '',
    object_key: target.route?.object_key || '',
    filter_hints: (target.route?.filter_hints || [])
      .map((h) => ({ dimension: h.dimension || '', examples: h.examples || [] })),
  });
  const [route, setRoute] = useState<RouteValue>(initRoute);

  let kind = '';
  try { kind = String(JSON.parse(jsonContent || '{}').kind || ''); } catch { /* 非法 JSON：按源本体表单 */ }
  const isBusiness = kind === 'business';

  // 切换对象（详情面板选中变了）时重置表单为当前对象值
  useEffect(() => {
    setForm({
      display_name: target.display_name || '',
      description: target.description || '',
      aliases: (target.aliases || []).join(', '),
      primary_table: target.primary_table || '',
    });
    setRoute(initRoute());
  }, [target.key]);  // eslint-disable-line react-hooks/exhaustive-deps

  const submit = () => {
    const key = target.key.trim();
    if (!key) return;
    if (isBusiness && !route.source_ontology.trim()) {
      window.alert('业务对象必须声明路由：请选择目标源本体');
      return;
    }
    let doc: any;
    try { doc = JSON.parse(jsonContent || '{}'); } catch { return; }
    const idx = (doc.objects || []).findIndex((o: any) => o.key === key);
    if (idx < 0) { window.alert(`对象不存在: ${key}`); return; }
    const hints = (route.filter_hints || []).filter((h) => h.dimension.trim());
    const next = { ...doc.objects[idx] };
    next.display_name = form.display_name.trim() || key;
    next.description = form.description.trim();
    next.aliases = form.aliases.split(',').map((s) => s.trim()).filter(Boolean);
    if (isBusiness) {
      next.route = {
        mode: route.mode,
        source_ontology: route.source_ontology.trim(),
        datasource_name: route.datasource_name || '',
        ...(route.mode === 'object_filter' && route.object_key ? { object_key: route.object_key } : {}),
        ...(hints.length ? { filter_hints: hints } : {}),
      };
    } else if (form.primary_table.trim()) {
      next.primary_table = form.primary_table.trim();
    } else {
      delete next.primary_table;
    }
    doc.objects[idx] = next;
    onSave(JSON.stringify(doc, null, 2));
    setOpen(false);
  };

  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <Pencil className="h-3.5 w-3.5 mr-1" />编辑对象
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className={isBusiness ? 'max-w-lg max-h-[88vh] overflow-y-auto overflow-x-hidden' : 'max-w-md max-h-[88vh] overflow-y-auto overflow-x-hidden'}>
          <DialogHeader>
            <DialogTitle>编辑对象 · {target.display_name || target.key}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <Field label="对象 key（唯一标识，不可改）">
              <Input value={target.key} readOnly disabled />
            </Field>
            <Field label="业务显示名">
              <Input value={form.display_name} placeholder="如：订单明细"
                onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
            </Field>
            <Field label="业务别名（逗号分隔；语义查询按别名解析）">
              <Input value={form.aliases} placeholder="如：用户, 客户, 账号"
                onChange={(e) => setForm({ ...form, aliases: e.target.value })} />
            </Field>
            <Field label="业务描述">
              <Textarea rows={2} value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
            {isBusiness ? (
              <RouteEditor value={route} onChange={setRoute} />
            ) : (
              <Field label="绑定物理表">
                <Input value={form.primary_table} placeholder="如：t_order_item"
                  onChange={(e) => setForm({ ...form, primary_table: e.target.value })} />
              </Field>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>取消</Button>
            <Button onClick={submit}>保存为草案</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
