/**
 * 模型工作区附加页签：图谱可视化(嵌入) 与 对象×标签。
 *
 * ModelGraphTab: 可视化整体并入工作区 —— 按当前模型的数据源拉图,
 *   支持 总览/表关系/业务知识/血缘 四视图切换; 复用 KnowledgeGraphView(ReactFlow)。
 * ObjectTagsTab: 本模型对象(按 primary_table)已打的标签聚合视图;
 *   标签定义管理仍在标签中心, 此处解决"建模时看不到对象业务色彩"的问题。
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import { toast } from '@/lib/toast';
import client from '@/api/client';
import { ontologyApi, type ObjectColumnOption, type PropertyDriftEntry } from '@/api/ontology';
import { useGraphStore } from '@/stores/graphStore';
import KnowledgeGraphView, { NODE_CONFIG } from '@/components/graph/KnowledgeGraph';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Network, Tag as TagIcon, Maximize2, Minimize2, X, Plus, Pencil, Trash2, AlertTriangle } from 'lucide-react';
import { findObjectIndex, normalizeDocObjects, writeObjectField } from '@/lib/ontologyObjects';
import { useTranslation } from 'react-i18next';
import { t } from '@/i18n/runtime';

// 视图已收敛为后端 GraphType.UNIFIED 单图(分域组装 总览+业务知识+物理层/血缘),
// 不再拆四视图切换; singleView 传入时仍锁定旧单视图(系统助手/图谱页兼容)。

/**
 * 同构站点部署子页（原「适用站点」）：模型级声明「这份契约适用哪些站点 + 每站点路由到哪个数据源」。
 *
 * 旧口径要为每个对象×每个站点各登记一行绑定（27 对象 × 5 站 = 135 行机械复制），
 * 现收敛为一次配置全对象共享；站点 schema 真不同时，按拍板「为该站单独配一个
 * 本体或使用源本体」表达。显示/选择一律用业务名（ui-resource-display）。
 *
 * 对象级例外（白名单机制）：「同构多站 + 个别站点才有某对象」时，在对象契约清单
 * 里点掉该对象不适用的站点即可——**未配置的对象默认全部勾选站点可用**（免逐个配置），
 * 配置过的只在所选站点可用（取数时未部署的站点直接拒查，不拿别站数据充数）。
 */
export function ModelSitesTab({ modelId, readonly }: { modelId: number; readonly?: boolean }) {
  const { t } = useTranslation('ui');
  const [sites, setSites] = useState<import('@/api/ontology').ModelSiteRow[]>([]);
  const [objects, setObjects] = useState<import('@/api/ontology').ContractObjectRow[]>([]);
  const [enabled, setEnabled] = useState<Record<string, boolean>>({});
  const [route, setRoute] = useState<Record<string, string>>({});
  /** 对象级站点白名单：key 存在 = 该对象显式限定到列出的站点；缺失 = 不限（沿用全部勾选站点） */
  const [objRules, setObjRules] = useState<Record<string, string[]>>({});
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(() => {
    if (!modelId) return;
    ontologyApi.sites(modelId).then((r: any) => {
      const list: import('@/api/ontology').ModelSiteRow[] = r?.data?.sites || [];
      const objs: import('@/api/ontology').ContractObjectRow[] = r?.data?.contract_objects || [];
      setSites(list);
      setObjects(objs);
      setEnabled(Object.fromEntries(list.map((s) => [s.site_code, s.selected])));
      // 站点内唯一源自动填（不要求人手输）；多源保留已配置值或留空待人选择
      setRoute(Object.fromEntries(list.map((s) => [
        s.site_code, s.datasource || (s.datasource_options.length === 1 ? s.datasource_options[0] : ''),
      ])));
      setObjRules(Object.fromEntries(objs
        .filter((o) => (o.allowed_sites || []).length)
        .map((o) => [o.object_key, [...o.allowed_sites].sort()])));
      setError('');
      setLoaded(true);
    }).catch((e: any) => {
      setError(String(e?.response?.data?.detail || e?.message || e));
      setLoaded(true);
    });
  }, [modelId]);
  useEffect(() => { load(); }, [load]);

  /** 上方已勾选的站点（对象级白名单只能在这个集合里收紧，不能扩到未部署的站） */
  const enabledCodes = useMemo(
    () => sites.filter((s) => enabled[s.site_code]).map((s) => s.site_code),
    [sites, enabled]);
  const siteLabel = useCallback(
    (code: string) => sites.find((s) => s.site_code === code)?.display_name || code,
    [sites]);
  /** 基线（后端已存配置）与当前编辑态同形状，用于 dirty 判定 */
  const baselineRules = useMemo(() => Object.fromEntries(
    objects.filter((o) => (o.allowed_sites || []).length)
      .map((o) => [o.object_key, [...o.allowed_sites].sort()]),
  ), [objects]);

  const rulesChanged = useMemo(() => {
    const keys = new Set([...Object.keys(objRules), ...Object.keys(baselineRules)]);
    for (const k of keys) {
      const a = (objRules[k] || []).join(',');
      const b = (baselineRules[k] || []).join(',');
      if (a !== b) return true;
    }
    return false;
  }, [objRules, baselineRules]);

  const dirty = loaded && (sites.some((s) => {
    const want = !!enabled[s.site_code];
    const wantDs = want ? (route[s.site_code] || '') : '';
    return want !== s.selected
      || wantDs !== (s.selected ? (s.datasource || (s.datasource_options.length === 1 ? s.datasource_options[0] : '')) : '');
  }) || rulesChanged);

  /** 点一下站点芯片：未收紧时先物化为「全部勾选站点」再增/减，保证语义是白名单而非黑名单 */
  const toggleObjectSite = (objectKey: string, code: string) => {
    if (readonly) return;
    const cur = objRules[objectKey] ?? enabledCodes;
    const next = cur.includes(code) ? cur.filter((c) => c !== code) : [...cur, code].sort();
    if (!next.length) {
      const name = objects.find((o) => o.object_key === objectKey)?.object_name || objectKey;
      setError(t('k_db27ad9c49', { p0: name }));
      return;
    }
    setError('');
    setObjRules({ ...objRules, [objectKey]: next });
  };

  const releaseObject = (objectKey: string) => {
    if (readonly) return;
    const next = { ...objRules };
    delete next[objectKey];
    setObjRules(next);
    setError('');
  };

  const save = async () => {
    const payload: { site_code: string; datasource: string }[] = [];
    for (const s of sites) {
      if (!enabled[s.site_code]) continue;
      const ds = route[s.site_code] || '';
      if (!ds) {
        setError(t('k_8857b7c9ad', { p0: s.display_name }));
        return;
      }
      payload.push({ site_code: s.site_code, datasource: ds });
    }
    // 对象级白名单跟随模型级勾选剪除（站点取消勾选后，其引用不再有意义）
    const keepCodes = new Set(payload.map((p) => p.site_code));
    const rulesOut: Record<string, string[]> = {};
    for (const [ok, codes] of Object.entries(objRules)) {
      const kept = codes.filter((c) => keepCodes.has(c));
      if (!kept.length) {
        const name = objects.find((o) => o.object_key === ok)?.object_name || ok;
        setError(t('k_a2927861cf', { p0: name }));
        return;
      }
      rulesOut[ok] = kept;
    }
    setSaving(true);
    setError('');
    try {
      const r: any = await ontologyApi.saveSites(modelId, payload, rulesOut);
      toast.success(t('k_64756ef231'));
      (r?.data?.pruned || []).forEach((msg: string) => toast.warning(msg));
      load();
    } catch (e: any) {
      // 校验失败原样外显（不静默回滚成旧配置）
      setError(String(e?.response?.data?.detail || e?.message || t('k_6de920b4e4')));
    } finally {
      setSaving(false);
    }
  };

  return (
    <ScrollArea className="h-full">
      <div className="p-3 space-y-4">
        <div className="text-xs font-medium text-muted-foreground space-y-1">
          <p>
            {t('k_cd8b33222c')}
          </p>
          <p>
            {t('k_24c42e47e8')}
          </p>
        </div>
        {error && <div className="text-xs text-destructive">{error}</div>}
        {loaded && !sites.length && (
          <div className="text-sm text-muted-foreground">
            {t('k_370daf3f0c')}
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
                    <Badge variant="outline" className="text-xs text-muted-foreground">{t("k_eec46c136f")}</Badge>
                  )}
                </div>
                {enabled[s.site_code] && (
                  <Select
                    value={route[s.site_code] || undefined}
                    disabled={readonly || !s.datasource_options.length}
                    onValueChange={(v) => setRoute({ ...route, [s.site_code]: v })}
                  >
                    <SelectTrigger className="w-[220px] h-8">
                      <SelectValue placeholder={s.datasource_options.length ? t('k_5609b4cbe1') : t('k_ccfbbd77d6')} />
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
                  {saving ? t('k_6644f06197') : t('k_079223cf61')}
                </Button>
                {!dirty && <span className="text-xs text-muted-foreground">{t("k_38f5c9d9d7")}</span>}
              </div>
            )}
          </div>
        )}

        <div>
          <div className="text-xs font-medium text-muted-foreground mb-2">
            {t('k_7b2e57339c', { p0: objects.length, p1: Object.keys(objRules).length })}</div>
          {!objects.length && <div className="text-sm text-muted-foreground">{t("k_2aa068ab63")}</div>}
          {!!objects.length && !!enabledCodes.length && !readonly && (
            <div className="text-[11px] text-muted-foreground mb-2">
              {t('k_65178265b7')}
            </div>
          )}
          {!!objects.length && (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground border-b">
                  <th className="py-1.5 pr-2 font-medium">{t("k_539cb3b0a6")}</th>
                  <th className="py-1.5 pr-2 font-medium" title={t("k_b438e67399")}>{t("k_046c1af41a")}</th>
                  <th className="py-1.5 pr-2 font-medium">{t("k_1b729397db")}</th>
                  <th className="py-1.5 pr-2 font-medium">{t("k_46262d23fe")}</th>
                  <th className="py-1.5 pr-2 font-medium">{t("k_cdc0e8f21e")}</th>
                  <th className="py-1.5 pr-2 font-medium">{t("k_0150205d5c")}</th>
                  <th className="py-1.5 font-medium whitespace-nowrap">{t("k_f5984fbcfa")}</th>
                </tr>
              </thead>
              <tbody>
                {objects.map((r, i) => (
                  <tr key={`${r.object_key}-${i}`} className="border-b last:border-0 align-top">
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
                        {r.sync_state === 'bound' ? t('k_3f946dd267') : (r.sync_state || '—')}
                      </Badge>
                    </td>
                    <td className="py-1.5">
                      {!enabledCodes.length ? (
                        <span className="text-muted-foreground">{t("k_b868ba9a4b")}</span>
                      ) : (
                        <div className="flex flex-wrap items-center gap-1">
                          {enabledCodes.map((code) => {
                            const on = (objRules[r.object_key] ?? enabledCodes).includes(code);
                            const label = siteLabel(code);
                            return (
                              <button
                                key={code}
                                type="button"
                                disabled={readonly}
                                title={on ? t('k_ab74fd3669', { p0: label }) : t('k_a84eaeaa03', { p0: label })}
                                onClick={() => toggleObjectSite(r.object_key, code)}
                                className={`px-1.5 py-0.5 rounded border text-[11px] whitespace-nowrap ${
                                  on
                                    ? 'border-primary/40 bg-primary/10 text-foreground'
                                    : 'border-border text-muted-foreground line-through'
                                } ${readonly ? 'cursor-default' : 'hover:border-primary/60'}`}
                              >
                                {label}
                              </button>
                            );
                          })}
                          {!!objRules[r.object_key] && !readonly && (
                            <button
                              type="button"
                              onClick={() => releaseObject(r.object_key)}
                              className="text-[11px] text-primary whitespace-nowrap"
                              title={t("k_761cfb40f1")}
                            >
                              {t('k_8441b348c3')}
                            </button>
                          )}
                        </div>
                      )}
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





export function ModelGraphTab({ datasourceId, kind, domain, singleView, modelId, fill = false }: {
  datasourceId: number;
  /** 本体类型：source/business/system。图谱归属与标题按 kind，**不按 datasource_id**——
   *  业务本体跨源 datasource_id=0，旧的 `!datasource_id` 判系统域会把业务本体误标为 AS-BOT。 */
  kind?: 'source' | 'business' | 'system' | '';
  /** 业务域（business 本体的归属） */
  domain?: string;
  /** 锁定单视图(如仅总览), 不渲染切换器 */
  singleView?: string;
  /** 当前选中的本体模型 id：business 域默认按此聚焦（只看本模型对象），留空则看全业务图。 */
  modelId?: number;
  /** 内嵌在工作台画布时用 h-full 填满父容器（默认保留 68vh 视口高 + 外框，供独立页/弹窗使用） */
  fill?: boolean;
}) {
  const { t } = useTranslation('ui');
  // 系统域仅当 kind=system（不是 datasource_id=0）
  const systemScope = kind === 'system';
  // 工作区默认统一大图(unified); singleView 传入时锁定旧单视图(向后兼容)。
  const view = singleView || 'unified';
  const unified = view === 'unified';
  // 业务本体跨源: 后端按 kind 路由将业务本体建在聚合图 ds:0, 前端取数须对齐(不按 model.datasource_id)。
  const graphDatasourceId = kind === 'business' ? 0 : datasourceId;
  const isBusiness = kind === 'business';
  // 业务聚合图多模型共存：默认只加载当前选中模型（选谁看谁）；开关打开=看全业务合并图。
  const [showAllBusiness, setShowAllBusiness] = useState(false);
  // 属性默认**打开**：本体关系图的内容主体就是「对象 → 指标/维度/属性」，
  // 默认关掉的结果是用户以为本体没录属性（历史反馈两次「对象属性还是没有显示」）。
  // 关掉只是降噪选项（只留主语义对象/指标/维度）。
  const [showProps, setShowProps] = useState(true);
  const [loading, setLoading] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [selected, setSelected] = useState<any | null>(null);
  const { graphData, fetchGraphData } = useGraphStore();

  const load = useCallback(async () => {
    setLoading(true);
    setSelected(null);
    try {
      // 系统域走 ds:-1(system_scope), 否则按当前模型数据源取图; 统一大图取跨源聚合图
      // 业务域默认按选中模型聚焦（model_id）；「看全业务图」或非业务域不传 model_id=全图。
      const scopedModelId = isBusiness && !showAllBusiness && modelId ? modelId : undefined;
      // 统一大图与本体总览都支持平铺属性（对象直连的语义明细）：
      // 默认 400 只放主语义（对象+指标+维度），打开开关才抬到 1200 让属性全量入图。
      const propsCapable = unified || view === 'ontology-overview';
      await fetchGraphData({
        graphType: view,
        datasourceId: graphDatasourceId,
        systemScope,
        // 打开「显示属性」时提高预算：属性是对象直连的语义展开（本模型可达数百条），
        // 沿用 400 会被对象+指标+维度吃满、属性几乎看不到（历史反馈「属性怎么没有展示」）。
        limit: propsCapable ? (showProps ? 1200 : 400) : 200,
        includeProperties: propsCapable ? showProps : undefined,
        modelId: scopedModelId,
      });
    } finally {
      setLoading(false);
    }
  }, [view, unified, graphDatasourceId, systemScope, showProps, showAllBusiness, isBusiness, modelId, fetchGraphData]);

  useEffect(() => { load(); }, [load]);

  const stale = graphData && (graphData.nodes.length === 0) && !loading;

  // 节点详情：把选中节点的一跳邻居按类型分组（对象 → 属性/指标/维度/关联对象）。
  // 旧实现把 node.data 原样 Object.entries 平铺，用户看到的是 iri/object_key/binding 之类内部键，
  // 「这个对象到底有哪些属性」根本看不出来（ui-resource-display：展示业务名，不展示内部标识）。
  const detailSections = useMemo(() => {
    if (!selected || !graphData) return [] as { type: string; items: { id: string; label: string }[] }[];
    const byId = new Map<string, any>(graphData.nodes.map((n: any) => [n.id, n]));
    const groups = new Map<string, { id: string; label: string }[]>();
    for (const e of graphData.edges) {
      const otherId = e.source === selected.id ? e.target : e.target === selected.id ? e.source : null;
      if (!otherId) continue;
      const n = byId.get(otherId);
      if (!n) continue;
      const type = String(n.label || n.type || '');
      const label = n.properties?.label || n.properties?.name || n.id;
      if (!groups.has(type)) groups.set(type, []);
      groups.get(type)!.push({ id: n.id, label });
    }
    // 属性排最前：这是用户点开对象最想看的东西
    const order = ['Property', 'Metric', 'Dimension', 'Action', 'Rule', 'Scenario',
      'Term', 'Object', 'Table', 'Column', 'DataSource', 'ETLTask'];
    const rank = (type: string) => {
      const i = order.indexOf(type);
      return i < 0 ? 99 : i;
    };
    return [...groups.entries()]
      .sort((a, b) => rank(a[0]) - rank(b[0]))
      .map(([type, items]) => ({ type, items: items.sort((x, y) => x.label.localeCompare(y.label)) }));
  }, [selected, graphData]);

  return (
    <div className={fullscreen
      ? 'fixed inset-0 z-50 bg-background p-4 flex flex-col'
      : fill ? 'h-full min-h-0 flex flex-col overflow-hidden'
        : 'h-[68vh] flex flex-col border rounded-lg overflow-hidden'}>
      <div className="flex items-center gap-1 px-2 py-1.5 border-b bg-muted/30 shrink-0">
        {unified ? (
          <span className="text-xs font-medium text-muted-foreground px-1">{t("k_bcbd70917c")}</span>
        ) : (
          <span className="text-xs font-medium text-muted-foreground px-1">{t("k_2bebeeaab5")}</span>
        )}
        {(unified || view === 'ontology-overview') && (
          <label className="flex items-center gap-1.5 pl-2 text-[11px] text-muted-foreground cursor-pointer select-none">
            <Switch checked={showProps} onCheckedChange={setShowProps} />
            {t('k_7566a7315d')}
          </label>
        )}
        {isBusiness && !!modelId && (
          <label className="flex items-center gap-1.5 pl-2 text-[11px] text-muted-foreground cursor-pointer select-none"
            title={t("k_d5bf58dd5f")}>
            <Switch checked={showAllBusiness} onCheckedChange={setShowAllBusiness} />
            {t('k_0f9ad366f2')}
          </label>
        )}
        <span className="ml-auto text-[11px] text-muted-foreground">
          {systemScope ? t('k_dac2018ccd')
            : kind === 'business' ? t('k_ae4250f7e4', {
              p0: domain ? t('k_45fdbccd1b', { p0: domain }) : '',
              p1: showAllBusiness ? t('k_e5570dd407') : t('k_e18ae87522'),
            })
            : kind === 'source' ? t('k_2f51081dde', { p0: datasourceId })
            : singleView ? t('k_ecf61faaf7') : t('k_345f1060a8', { p0: datasourceId })} · {loading ? t('k_300ee3dee4') : t('k_faf141c63b', { p0: graphData?.nodes.length ?? 0, p1: graphData?.edges.length ?? 0 })}
        </span>
        <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={load}>{t("k_38108eaa1d")}</Button>
        <Button size="sm" variant="ghost" className="h-7 w-7 p-0" title={fullscreen ? t('k_49041f2450') : t('k_93c44f6b1b')}
          onClick={() => setFullscreen((f) => !f)}>
          {fullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
        </Button>
      </div>
      <div className="flex-1 relative min-h-0">
        {stale && (
          <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none">
            <div className="text-center text-sm text-muted-foreground bg-card/80 border rounded-lg px-6 py-4">
              {isBusiness && !showAllBusiness && modelId ? t('k_3ab2e7b8ab') : t('k_72fd239464')}
              <div className="text-xs mt-1">{t('k_1bbd64f226', { p0: isBusiness && modelId ? t('k_44883ea5c1') : '' })}</div>
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
          <div className="absolute right-2 top-2 bottom-2 z-20 w-80 bg-card/95 backdrop-blur border rounded-lg shadow-xl flex flex-col">
            <div className="flex items-center gap-2 px-3 py-2 border-b shrink-0">
              <Badge variant="outline" className="text-[10px] shrink-0"
                style={{ color: NODE_CONFIG[selected.label]?.color, borderColor: NODE_CONFIG[selected.label]?.color }}>
                {NODE_CONFIG[selected.label]?.label || selected.label}
              </Badge>
              <span className="text-sm font-medium truncate">
                {selected.properties?.label || selected.properties?.name || selected.id}
              </span>
              <button className="ml-auto text-muted-foreground hover:text-foreground" onClick={() => setSelected(null)}>
                <X className="h-4 w-4" />
              </button>
            </div>
            <ScrollArea className="flex-1">
              <div className="p-3 space-y-3 text-xs">
                {(selected.properties?.comment || selected.properties?.description) && (
                  <p className="text-muted-foreground leading-relaxed">
                    {String(selected.properties?.comment || selected.properties?.description)}
                  </p>
                )}
                {detailSections.map((sec) => (
                  <div key={sec.type}>
                    <div className="mb-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      <span className="h-2 w-2 rounded-full"
                        style={{ background: NODE_CONFIG[sec.type]?.color || '#94a3b8' }} />
                      {NODE_CONFIG[sec.type]?.label || sec.type}
                      <span className="text-muted-foreground/70">{sec.items.length}</span>
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {sec.items.map((it) => (
                        <span key={it.id} title={it.id}
                          className="rounded bg-muted px-1.5 py-0.5 text-[11px] leading-tight">
                          {it.label}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
                {detailSections.length === 0 && (
                  <div className="text-muted-foreground">该节点暂无关联明细</div>
                )}
                {/* 显式提示：属性明细未加载时不能装作“这个对象没有属性” */}
                {selected.label === 'Object' && !showProps && (
                  <div className="text-[11px] text-amber-600">
                    属性明细未加载：打开上方「显示属性」后刷新，即可看到该对象的全部属性。
                  </div>
                )}
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
  const { t } = useTranslation('ui');
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
          {t('k_c2d2d88ff9')}
        </div>
        <Button size="sm" variant="outline" onClick={load} disabled={loading}>
          {loading ? t('k_300ee3dee4') : t('k_38108eaa1d')}
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
                  <div className="text-[11px] font-mono text-muted-foreground truncate">{o.primary_table || t('k_66fa310a42')}</div>
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
          {!objects.length && <div className="text-sm text-muted-foreground">{t("k_27d5e9e589")}</div>}
        </div>
      </ScrollArea>
      {unusedTags.length > 0 && (
        <div className="text-xs text-muted-foreground border-t pt-2">
          {t('k_659a89487f')}
          {unusedTags.slice(0, 12).map((t) => (
            <Badge key={t.id} variant="outline" className="ml-1 text-[10px] font-normal">{t.name}</Badge>
          ))}
          {unusedTags.length > 12 && t('k_674c56abd3', { p0: unusedTags.length })}
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
  const { t } = useTranslation('ui');
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
    synced: t('k_8bf97a5b00'),
    // 方向写清：旧文案“滞后 1/4”看不出谁多谁少
    lagging: t('k_adce72ea39', { p0: g.classes ?? 0, p1: g.objects ?? 0 }),
    absent: t('k_da3b420e78'), draft: t('k_be8adfb9d2'), unreachable: t('k_62329246b2'),
  } as Record<string, string>)[g.state] || g.state;
  const gNeedRebuild = g.state === 'lagging' || g.state === 'absent';
  const gTip = [
    g.graph_datasource_id == null ? '' : t('k_f9e44fab1d', { p0: g.graph_datasource_id }),
    t('k_946c362890', { p0: g.classes ?? 0, p1: g.objects ?? 0 }),
    g.state === 'lagging'
      ? t('k_77aaa7dfb8')
      : g.state === 'absent' ? t('k_1cb4c155dd') : '',
  ].filter(Boolean).join(t('k_c4530db333'));

  const kTargets = k.target_names || [];
  const kOk = k.state === 'success';
  // 源本体不参与 RAG 上云 = 按设计，不算缺口（旧口径会把它当“未开启同步”让人以为坏了）
  const kByDesign = (k.state === 'no_kb' || k.state === 'no_kb_bound') && kind === 'source';
  const kWarn = k.state === 'failed' || k.state === 'no_kb_bound';
  const kStateLabel = ({
    success: t('k_8bf97a5b00'),
    failed: k.error ? t('k_393a572a31', { p0: k.error.slice(0, 30) }) : t('k_3e3c8068bb'),
    pending: t('k_a0bf20e813'), removed: t('k_0a6667596a'),
    no_kb: kByDesign ? t('k_1e1bbcbf03') : t('k_809437f4f5'),
    no_kb_bound: kByDesign ? t('k_1e1bbcbf03') : t('k_8413f6bfac'),
  } as Record<string, string>)[k.state] || k.state;
  const kLabel = kStateLabel + (kTargets.length
    ? ` → ${kTargets[0]}${kTargets.length > 1 ? ' ' + t('k_674c56abd3', { p0: kTargets.length }) : ''}` : '');
  const kTip = [
    kTargets.length ? t('k_c053f6447d', { p0: kTargets.join(' / ') })
      : (kByDesign ? t('k_855629ef9a') : t('k_ec91548961')),
    k.synced_at ? t('k_5b7c452524', { p0: k.synced_at }) : '',
    k.error ? t('k_3cbd8ca072', { p0: k.error }) : '',
  ].filter(Boolean).join(t('k_c4530db333'));
  return (
    <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
      <span className="flex items-center gap-1" title={gTip}>
        {dot(gOk, gWarn)}{t('k_34778e2933')} {gLabel}
        {gNeedRebuild && <span className="text-amber-600">{t("k_10faa62c5c")}</span>}
      </span>
      <span className="flex items-center gap-1" title={kTip}>
        {/* 状态点必须是 JSX 节点，不能当 i18n 插值：模板字符串化后会渲染成 [object Object] */}
        {dot(kOk, kWarn)}{t('k_a2e0483367', { p0: '', p1: kLabel })}
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
  type === 'permission' ? t('k_95f4519aab') : type === 'metric_constraint' ? t('k_3402b1c79a')
    : type === 'business_constraint' ? t('k_705ff04956') : type === 'quality' ? t('k_e974beee2d') : (type || t('k_ed904c685a'));

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
  const { t } = useTranslation('ui');
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
      <Field label={t("k_ddea5353a7")}>
        <Select value={value.mode} onValueChange={(v) => onChange({ ...value, mode: v as RouteValue['mode'] })}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="source">{t("k_a0b36e2428")}</SelectItem>
            <SelectItem value="object_filter">{t('k_4cf8e8301d')}</SelectItem>
          </SelectContent>
        </Select>
      </Field>
      <Field label={t("k_5444af3edb")}>
        <Select value={value.source_ontology || undefined}
          onValueChange={(v) => onChange({ ...value, source_ontology: v, object_key: undefined })}>
          <SelectTrigger><SelectValue placeholder={sources.length ? t('k_ee05d3489d') : t('k_d922dcc2f6')} /></SelectTrigger>
          <SelectContent>
            {sources.map((s) => <SelectItem key={s.id} value={s.name}>{s.name}</SelectItem>)}
          </SelectContent>
        </Select>
      </Field>
      {value.mode === 'object_filter' && (
        <Field label={t("k_6092a35eee")}>
          <Select value={value.object_key || undefined}
            onValueChange={(v) => onChange({ ...value, object_key: v })}>
            <SelectTrigger><SelectValue placeholder={value.source_ontology ? (srcObjects.length ? t('k_921996ff91') : t('k_f4e691ac5a')) : t('k_54dfdbbc8f')} /></SelectTrigger>
            <SelectContent>
              {srcObjects.map((o) => <SelectItem key={o.key} value={o.key}>{o.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </Field>
      )}
      <Field label={t('k_751208caa8')}>
        {hints.map((h, i) => (
          <div key={i} className="flex gap-2 mb-1">
            <Input placeholder={t("k_0551151d1e")} value={h.dimension}
              onChange={(e) => { const next = [...hints]; next[i] = { ...h, dimension: e.target.value }; setHints(next); }} />
            <Input placeholder={t("k_039e26f365")} value={(h.examples || []).join(',')}
              onChange={(e) => { const next = [...hints]; next[i] = { ...h, examples: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) }; setHints(next); }} />
            <Button variant="outline" size="sm" onClick={() => setHints(hints.filter((_, j) => j !== i))}>{t("k_38cadf487e")}</Button>
          </div>
        ))}
        <Button variant="outline" size="sm"
          onClick={() => setHints([...hints, { dimension: '', examples: [] }])}>{t('k_6fa78c155f')}</Button>
      </Field>
    </div>
  );
}

function ItemOps({ onEdit, onDelete }: { onEdit: () => void; onDelete: () => void }) {
  return (
    <div className="flex items-center gap-1 ml-auto shrink-0">
      <Button variant="ghost" size="icon" className="h-7 w-7" title={t("k_a7f814c0a4")} onClick={onEdit}>
        <Pencil className="h-3.5 w-3.5" />
      </Button>
      <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" title={t("k_3755f56f2f")} onClick={onDelete}>
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
  const { t } = useTranslation('ui');
  const doc = parseM234(jsonContent);
  const [editing, setEditing] = useState<null | {
    mode: 'create' | 'edit'; origKey?: string; objectKey: string; key: string;
    label: string; effect: string; read_only: boolean; risk: string; requires_approval: boolean;
  }>(null);

  if (!doc) return <M234Empty hint={t('k_0814573a76')} />;
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
    if (!window.confirm(t('k_b83544c032', { p0: akey }))) return;
    applyEdit(jsonContent, (d) => {
      const obj = (d.objects || []).find((o: any) => o.key === objKey);
      if (obj) obj.actions = (obj.actions || []).filter((a: any) => a.key !== akey);
    }, onSave);
  };

  return (
    <ScrollArea className="h-full">
      <div className="p-4">
        <TabToolbar title={t("k_eb4d32abb7")} count={totalActs} unit={t('k_d55ccdf498')}
          createLabel={t('k_d474378d6e')}
          onCreate={() => setEditing({
            mode: 'create', objectKey: objects[0]?.key || '', key: '', label: '',
            effect: '', read_only: true, risk: 'medium', requires_approval: true,
          })} />
        {totalActs === 0 && <M234Empty hint={t('k_b5f73c3b86')} />}
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
                      {a.read_only ? t('k_981506ab42') : t('k_acfe9ff42a', { p0: a.risk ? `(${a.risk})` : '' })}
                    </span>
                    <button className="text-muted-foreground hover:text-foreground" title={t("k_a7f814c0a4")}
                      onClick={() => setEditing({
                        mode: 'edit', origKey: a.key, objectKey: obj.key || '', key: a.key || '',
                        label: a.label || '', effect: a.effect || '', read_only: !!a.read_only,
                        risk: a.risk || 'medium', requires_approval: !a.read_only,
                      })}>
                      <Pencil className="h-3 w-3" />
                    </button>
                    <button className="text-muted-foreground hover:text-destructive" title={t("k_3755f56f2f")}
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
            <DialogTitle>{editing?.mode === 'edit' ? t('k_c3f5a25f12') : t('k_d474378d6e')}</DialogTitle>
          </DialogHeader>
          {editing && (
            <div className="space-y-3">
              <Field label={t("k_36c16b14cf")}>
                <Select value={editing.objectKey} disabled={editing.mode === 'edit'}
                  onValueChange={(v) => setEditing({ ...editing, objectKey: v })}>
                  <SelectTrigger><SelectValue placeholder={t("k_0d14cc5e65")} /></SelectTrigger>
                  <SelectContent>
                    {objects.map((o) => (
                      <SelectItem key={o.key} value={o.key || ''}>
                        {o.display_name || o.key}（{o.key}）
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label={t('k_2cd4bc3642')}>
                <Input value={editing.key} disabled={editing.mode === 'edit'}
                  onChange={(e) => setEditing({ ...editing, key: e.target.value })} />
              </Field>
              <Field label={t("k_e73713fb32")}>
                <Input value={editing.label} placeholder={t("k_46fb65f0fa")}
                  onChange={(e) => setEditing({ ...editing, label: e.target.value })} />
              </Field>
              <Field label={t("k_13e31f54fe")}>
                <Input value={editing.effect} placeholder={t("k_3122d42622")}
                  onChange={(e) => setEditing({ ...editing, effect: e.target.value })} />
              </Field>
              <div className="flex items-center justify-between">
                <Label className="text-xs">{t("k_1951bd2fb2")}</Label>
                <Switch checked={editing.read_only}
                  onCheckedChange={(v) => setEditing({ ...editing, read_only: v })} />
              </div>
              {!editing.read_only && (
                <>
                  <Field label={t("k_276ce067ea")}>
                    <Select value={editing.risk}
                      onValueChange={(v) => setEditing({ ...editing, risk: v })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="low">{t("k_1c121d6121")}</SelectItem>
                        <SelectItem value="medium">{t("k_83cb9543a4")}</SelectItem>
                        <SelectItem value="high">{t("k_6f6801eb9d")}</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                  <div className="flex items-center justify-between">
                    <Label className="text-xs">{t("k_7d8d066be1")}</Label>
                    <Switch checked={editing.requires_approval}
                      onCheckedChange={(v) => setEditing({ ...editing, requires_approval: v })} />
                  </div>
                </>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={submit} disabled={!editing?.key.trim()}>{t("k_fadf24dbc5")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ScrollArea>
  );
}

/** M3 规则页签：口径/约束/质量/权限规则（支持新建/编辑/删除）。 */
export function ModelRulesTab({ jsonContent, onSave }: { jsonContent: string; onSave: (next: string) => void }) {
  const { t } = useTranslation('ui');
  const doc = parseM234(jsonContent);
  const [editing, setEditing] = useState<null | {
    mode: 'create' | 'edit'; origKey?: string; key: string; type: string;
    statement: string; source: string;
    subject: string; action: string; level: string; applies_to: string[];
  }>(null);

  if (!doc) return <M234Empty hint={t('k_0814573a76')} />;
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
    if (!window.confirm(t('k_a00da004f0', { p0: rkey }))) return;
    applyEdit(jsonContent, (d) => {
      d.rules = (d.rules || []).filter((r: any) => r.key !== rkey);
    }, onSave);
  };

  return (
    <ScrollArea className="h-full">
      <div className="p-4">
        <TabToolbar title={t("k_9f21293dc9")} count={rules.length} unit={t('k_ecb4f20944')}
          createLabel={t('k_f10ed4681f')}
          onCreate={() => setEditing({
            mode: 'create', key: '', type: 'business_constraint', statement: '', source: '',
            subject: 'role:admin', action: '*', level: 'allow', applies_to: [],
          })} />
        {rules.length === 0 && <M234Empty hint={t('k_9d43a19751')} />}
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
              <div className="text-sm mt-1.5">{r.statement || t('k_42957a482d')}</div>
              {r.enforcement?.subject && (
                <div className="text-xs text-muted-foreground mt-1">
                  {t('k_f2f1c917ed', { p0: r.enforcement.subject, p1: r.enforcement.action ? ` × ${r.enforcement.action}` : '' })}
                </div>
              )}
              {r.source && <div className="text-xs text-muted-foreground mt-0.5">{t('k_e97b6e0954', { p0: r.source })}</div>}
            </div>
          ))}
        </div>
      </div>

      <Dialog open={!!editing} onOpenChange={(v) => !v && setEditing(null)}>
        <DialogContent className="max-w-md max-h-[88vh] overflow-y-auto overflow-x-hidden">
          <DialogHeader>
            <DialogTitle>{editing?.mode === 'edit' ? t('k_a1d940b0dc') : t('k_f10ed4681f')}</DialogTitle>
          </DialogHeader>
          {editing && (
            <div className="space-y-3">
              <Field label={t('k_54ffe94bb7')}>
                <Input value={editing.key} disabled={editing.mode === 'edit'}
                  onChange={(e) => setEditing({ ...editing, key: e.target.value })} />
              </Field>
              <Field label={t("k_7655f477f1")}>
                <Select value={editing.type} disabled={editing.mode === 'edit'}
                  onValueChange={(v) => setEditing({ ...editing, type: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="business_constraint">{t("k_705ff04956")}</SelectItem>
                    <SelectItem value="metric_constraint">{t("k_3402b1c79a")}</SelectItem>
                    <SelectItem value="quality">{t("k_e974beee2d")}</SelectItem>
                    <SelectItem value="permission">{t("k_95f4519aab")}</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field label={t("k_e2b75130ec")}>
                <Textarea rows={2} value={editing.statement} placeholder={t("k_8373a77eff")}
                  onChange={(e) => setEditing({ ...editing, statement: e.target.value })} />
              </Field>
              <Field label={t("k_b88fdb4275")}>
                <Input value={editing.source} placeholder={t('k_b50a4f70b8')}
                  onChange={(e) => setEditing({ ...editing, source: e.target.value })} />
              </Field>
              {editing.type === 'permission' ? (
                <>
                  <Field label={t('k_9871775e33')}>
                    <Input value={editing.subject} placeholder="role:admin"
                      onChange={(e) => setEditing({ ...editing, subject: e.target.value })} />
                  </Field>
                  <Field label={t('k_3db63f8c0e')}>
                    <Input value={editing.action} placeholder="*"
                      onChange={(e) => setEditing({ ...editing, action: e.target.value })} />
                  </Field>
                  <Field label={t("k_3682923533")}>
                    <Select value={editing.level}
                      onValueChange={(v) => setEditing({ ...editing, level: v })}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="allow">{t("k_f687b1634f")}</SelectItem>
                        <SelectItem value="require_approval">{t('k_5072107492')}</SelectItem>
                        <SelectItem value="deny">{t("k_d93d771421")}</SelectItem>
                      </SelectContent>
                    </Select>
                  </Field>
                </>
              ) : (
                <Field label={t("k_c763413aa1")}>
                  <CheckList options={objOptions} value={editing.applies_to} empty={t('k_4c2bfa7fb6')}
                    onChange={(v) => setEditing({ ...editing, applies_to: v })} />
                </Field>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={submit} disabled={!editing?.key.trim()}>{t("k_fadf24dbc5")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ScrollArea>
  );
}

/** M4 场景页签：使用场景（支持新建/编辑/删除）。 */
export function ModelScenariosTab({ jsonContent, onSave }: { jsonContent: string; onSave: (next: string) => void }) {
  const { t } = useTranslation('ui');
  const doc = parseM234(jsonContent);
  const [editing, setEditing] = useState<null | {
    mode: 'create' | 'edit'; origKey?: string; key: string; title: string; goal: string;
    uses_objects: string[]; uses_actions: string[]; uses_rules: string[]; eval_seed: string;
    route_sources: string[]; join_hint: string;
  }>(null);

  if (!doc) return <M234Empty hint={t('k_0814573a76')} />;
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
    if (!window.confirm(t('k_eef5c7f5c8', { p0: skey }))) return;
    applyEdit(jsonContent, (d) => {
      d.scenarios = (d.scenarios || []).filter((s: any) => s.key !== skey);
    }, onSave);
  };

  return (
    <ScrollArea className="h-full">
      <div className="p-4">
        <TabToolbar title={t("k_b1d78e638d")} count={scenarios.length} unit={t('k_a256b2d857')}
          createLabel={t('k_a5cab13a74')}
          onCreate={() => setEditing({
            mode: 'create', key: '', title: '', goal: '',
            uses_objects: [], uses_actions: [], uses_rules: [], eval_seed: '',
            route_sources: [], join_hint: '',
          })} />
        {scenarios.length === 0 && <M234Empty hint={t('k_792634f3c2')} />}
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
                {(s.uses_objects || []).map((x) => <Badge key={`o${x}`} variant="secondary">{t('k_21277ce209', { p0: x })}</Badge>)}
                {(s.uses_actions || []).map((x) => <Badge key={`a${x}`} variant="secondary">{t('k_1c3f28486c', { p0: x })}</Badge>)}
                {(s.uses_rules || []).map((x) => <Badge key={`r${x}`} variant="secondary">{t('k_46dbc21389', { p0: x })}</Badge>)}
              </div>
              {(s.route?.sources || []).length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 mt-2">
                  {(s.route!.sources || []).map((e) => (
                    <Badge key={e.source_ontology} variant="outline" className="text-[10px]">
                      {t('k_88b42b4ce6', { p0: e.source_ontology, p1: e.object_keys?.length ? ` · ${e.object_keys.join('/')}` : '' })}
                    </Badge>
                  ))}
                  {s.route!.join_hint && (
                    <span className="text-xs text-muted-foreground">{t('k_c3c35a7ce9', { p0: s.route!.join_hint })}</span>
                  )}
                </div>
              )}
              {(s.eval_seed || []).length > 0 && (
                <div className="text-xs text-muted-foreground mt-1.5">
                  {t('k_685ecdeb53', { p0: s.eval_seed!.join(' / ') })}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <Dialog open={!!editing} onOpenChange={(v) => !v && setEditing(null)}>
        <DialogContent className="max-w-xl max-h-[88vh] overflow-y-auto overflow-x-hidden">
          <DialogHeader>
            <DialogTitle>{editing?.mode === 'edit' ? t('k_583eb8ba99') : t('k_a5cab13a74')}</DialogTitle>
          </DialogHeader>
          {editing && (
            <div className="space-y-3 min-w-0">
              <div className="grid grid-cols-2 gap-3">
                <Field label={t('k_bacfe3ad20')}>
                  <Input value={editing.key} disabled={editing.mode === 'edit'}
                    onChange={(e) => setEditing({ ...editing, key: e.target.value })} />
                </Field>
                <Field label={t("k_bb3094d4f8")}>
                  <Input value={editing.title} placeholder={t("k_1c83c57183")}
                    onChange={(e) => setEditing({ ...editing, title: e.target.value })} />
                </Field>
              </div>
              <Field label={t("k_941f08313a")}>
                <Input value={editing.goal} placeholder={t("k_81f799fe01")}
                  onChange={(e) => setEditing({ ...editing, goal: e.target.value })} />
              </Field>
              <FormSection title={t("k_55ef356fe5")} hint={t('k_1b8abac041')}>
                <Field label={t("k_a25c9c51df")}>
                  <CheckList options={objOptions} value={editing.uses_objects} empty={t('k_4c2bfa7fb6')}
                    onChange={(v) => setEditing({ ...editing, uses_objects: v })} />
                </Field>
                <Field label={t("k_fe49fdf2be")}>
                  <CheckList options={actOptions} value={editing.uses_actions} empty={t('k_c42f0232ba')}
                    onChange={(v) => setEditing({ ...editing, uses_actions: v })} />
                </Field>
                <Field label={t("k_fedd386c5c")}>
                  <CheckList options={ruleOptions} value={editing.uses_rules} empty={t('k_164947fc67')}
                    onChange={(v) => setEditing({ ...editing, uses_rules: v })} />
                </Field>
              </FormSection>
              <FormSection title={t("k_37f8e66df7")} hint={t('k_50b1b03a9f')}>
                <Field label={t("k_059c632309")}>
                  <Textarea rows={3} value={editing.eval_seed} placeholder={t('k_e57725f322')}
                    onChange={(e) => setEditing({ ...editing, eval_seed: e.target.value })} />
                </Field>
              </FormSection>
              <FormSection title={t("k_73785c79b8")} hint={t('k_e0e7c09e35')}>
                <Field label={t("k_1704ee052c")}>
                  <CheckList options={srcOptions} value={editing.route_sources} empty={t('k_d922dcc2f6')}
                    onChange={(v) => setEditing({ ...editing, route_sources: v })} />
                </Field>
                <Field label={t('k_009ae3480c')}>
                  <Input value={editing.join_hint} placeholder={t("k_bdce2e1691")}
                    onChange={(e) => setEditing({ ...editing, join_hint: e.target.value })} />
                </Field>
              </FormSection>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={submit} disabled={!editing?.key.trim()}>{t("k_fadf24dbc5")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ScrollArea>
  );
}

/** 哨兵项前缀：漂移列不在这张表的实存列里，拿它当 Select value 会渲染成空（看着像被清空）。
 *  用显式的「不修改（当前 x）」项承载保持原值：既不假装选中一个不存在的列，也不让行变空。 */
const KEEP_COLUMN_PREFIX = '__keep__::';

/** 「不绑定物理列」哨兵项：该表确实没有这个概念时，把属性列清空是唯一诚实的写法。
 *  留着指向不存在列的映射更坏：planner 会把它编译进 SQL，取数直接报错；
 *  页面也因此永远挂着修不掉的红旗（引导指向一个改了也没用的操作）。
 *  属性定义与业务名保留，只是不再声称绑到某个物理列（column_map 少一项，解析时回抛候选）。 */
const NONE_COLUMN_VALUE = '__none__::';

/** 属性行草稿（弹窗内可新增/删除/改名/改列，提交时整体写回 doc.properties）。
 *  origIndex 指向 doc 里的原属性行：有它才谈得上「删除」，且原行的额外字段
 *  （is_key/description/enum 等）原样保留；新增行没有它，类型按列元数据带出。 */
export type PropRow = {
  rid: string;
  name: string;
  column: string;
  type?: string;
  origIndex?: number;
};

/** 原属性的身份名（与后端 `_prop_identity_names` 同口径：name > display_name > column）。
 *  删除声明必须用这个身份名，否则服务端护栏认不出“这是被点名的删除”而照旧阻断。 */
export function propIdentityName(p: any): string {
  for (const k of ['name', 'display_name', 'column']) {
    const v = String(p?.[k] ?? '').trim();
    if (v) return v;
  }
  return '';
}

let _propRowSeed = 0;

/** doc.properties → 弹窗行草稿（新增/删除/改名都在这一层做，提交时回写）。 */
export function toPropRows(properties: any[]): PropRow[] {
  return (properties || []).map((p, i) => ({
    rid: `p${++_propRowSeed}`,
    name: String(p?.name ?? p?.display_name ?? ''),
    column: String(p?.column ?? ''),
    type: String(p?.type ?? p?.data_type ?? ''),
    origIndex: i,
  }));
}

/** 属性清单编辑器（「编辑对象」弹窗内）：新增 / 删除 / 改业务名 / 改物理列。
 *
 *  为什么必须可增删：换库/改表后服务端会判出「列不存在」并亮「列待修」徽标，
 *  而此前页面只把**已有**属性行的列重选一遍 —— 新表多出来的列登记不进来，
 *  没意义的属性也删不掉（只能改草案 JSON，而草案 JSON 视图只读）。
 *  删除走 doc 顶层 `_declared_removals` 显式声明：后端护栏只放行被点名删除的属性，
 *  其余“属性无故消失”照旧阻断（宁阻断勿损毁不打折）。
 *  候选来自列元数据（与漂移判定同源），选择用选择框而非手输列名（ui-resource-display）。 */
function PropertyColumnEditor({ modelId, objectKey, table, rows, drift, onRowsChange }: {
  modelId: number;
  objectKey: string;
  /** 弹窗内当前表名覆盖值；空 = 由服务端按生效部署绑定行 → doc primary_table 解析 */
  table: string;
  rows: PropRow[];
  drift: PropertyDriftEntry[];
  onRowsChange: (rows: PropRow[]) => void;
}) {
  const { t } = useTranslation('ui');
  const [cols, setCols] = useState<ObjectColumnOption[]>([]);
  const [resolvedTable, setResolvedTable] = useState('');
  const [note, setNote] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    // 表名是同一弹窗里的输入框：防逐字符重拉候选（350ms 防抖）
    const timer = setTimeout(() => {
      ontologyApi.objectColumns(modelId, objectKey, table)
        .then(({ data }) => {
          if (cancelled) return;
          setCols(Array.isArray(data?.columns) ? data.columns : []);
          setResolvedTable(String(data?.table || ''));
          setNote(String(data?.note || ''));
          setLoading(false);
        })
        .catch((e: any) => {
          if (cancelled) return;
          setCols([]);
          setNote('');
          // 拿不到候选必须出声：静默当作“没列可改”会把漂移留到线上错数才暴露
          setError(String(e?.response?.data?.detail || e?.message || t('k_eb0c1382c1')));
          setLoading(false);
        });
    }, 350);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [modelId, objectKey, table]);

  const names = useMemo(() => cols.map((c) => c.name), [cols]);
  // 表限定写法（`表.列`）剥前缀后才能与候选列名对上；前缀不是本表时保持完整写法（就是待修的那一个）
  const normCurrent = (ref: string) => {
    const s = String(ref || '');
    const dot = s.indexOf('.');
    if (dot > 0 && resolvedTable && s.slice(0, dot).toLowerCase() === resolvedTable.toLowerCase()) {
      return s.slice(dot + 1);
    }
    return s;
  };
  const driftOfRow = (row: PropRow) => drift.find((d) =>
    (d.column && d.column === row.column) || (d.property && d.property === row.name));

  const patchRow = (rid: string, patch: Partial<PropRow>) => {
    onRowsChange(rows.map((r) => (r.rid === rid ? { ...r, ...patch } : r)));
  };
  const removeRow = (rid: string) => onRowsChange(rows.filter((r) => r.rid !== rid));
  const addRow = () => onRowsChange([...rows, { rid: `p${++_propRowSeed}`, name: '', column: '' }]);

  return (
    <div className="rounded-md border p-2">
      <div className="flex items-center gap-1.5 text-xs font-medium mb-1">
        {t('k_ddd8060872', { p0: rows.length })}
        <span className="text-[11px] font-normal text-muted-foreground">{t('k_827281b669')}</span>
      </div>
      {drift.length > 0 && (
        <div className="mb-1.5 rounded border border-amber-500/30 bg-amber-500/5 px-2 py-1 text-[11px] text-amber-700">
          <AlertTriangle className="h-3 w-3 mr-1 inline" />
          {(() => {
            const unv = drift.filter((d) => d.verdict === 'unverified').length;
            const bad = drift.length - unv;
            const seg: string[] = [];
            if (bad) seg.push(t('k_4c9cb60d58', { p0: bad }));
            if (unv) seg.push(t('k_2cac71eab0', { p0: unv }));
            return seg.join(t('k_c4530db333')) + t('k_81b580e835');
          })()}
        </div>
      )}
      {loading && (
        <div className="text-[11px] text-muted-foreground py-1">{t("k_244918077d")}</div>
      )}
      {error && (
        <div className="mb-1.5 rounded border border-red-500/30 bg-red-500/5 px-2 py-1 text-[11px] text-red-600">
          {t('k_237d529482', { p0: error })}</div>
      )}
      {!loading && !error && note && (
        <div className="mb-1.5 rounded border border-amber-500/30 bg-amber-500/5 px-2 py-1 text-[11px] text-amber-700">
          {note}
        </div>
      )}
      <div className="space-y-1 max-h-64 overflow-y-auto pr-1">
        {!loading && rows.length === 0 && (
          <div className="text-[11px] text-muted-foreground py-1">{t('k_2f6d8a1c40')}</div>
        )}
        {!loading && rows.map((row) => {
          const raw = row.column;
          const cur = normCurrent(raw);
          const useSelect = !loading && !error && names.length > 0;
          const bad = !!driftOfRow(row);
          const selected = useSelect
            ? (raw === '' ? NONE_COLUMN_VALUE
              : (names.includes(cur) ? cur : `${KEEP_COLUMN_PREFIX}${raw}`))
            : raw;
          const label = row.name.trim() || t('k_9c3a7e5d12');
          return (
            <div key={row.rid} className="flex items-center gap-1.5">
              <Input value={row.name} aria-label={row.name.trim()
                ? `${row.name.trim()} ${t('k_9c3a7e5d12')}` : t('k_9c3a7e5d12')}
                placeholder={t('k_9c3a7e5d12')}
                onChange={(e) => patchRow(row.rid, { name: e.target.value })}
                className="h-7 w-28 shrink-0 text-[11px]" />
              {useSelect ? (
                <Select value={selected} onValueChange={(v) => {
                  // 「不修改」= 保持原列名不写回（漂移列不在候选里时唯一诚实的选项）
                  if (v.startsWith(KEEP_COLUMN_PREFIX)) return;
                  if (v === NONE_COLUMN_VALUE) { patchRow(row.rid, { column: '' }); return; }
                  const col = cols.find((c) => c.name === v);
                  patchRow(row.rid, { column: v, ...(col?.type ? { type: col.type } : {}) });
                }}>
                  <SelectTrigger aria-label={t('k_66c5f8bce2', { p0: label })}
                    className={`h-7 flex-1 text-[11px] font-mono ${bad ? 'border-red-400 text-red-600' : ''}`}>
                    <SelectValue placeholder={t("k_62a91b82a0")} />
                  </SelectTrigger>
                  <SelectContent>
                    {raw !== '' && !names.includes(cur) && (
                      <SelectItem value={`${KEEP_COLUMN_PREFIX}${raw}`} className="text-[11px]">
                        <span className="text-red-500">{t('k_a5d914a4c5', { p0: raw || t('k_0e223ebb16') })}</span>
                      </SelectItem>
                    )}
                    {(bad || raw === '') && (
                      <SelectItem value={NONE_COLUMN_VALUE} className="text-[11px]">
                        <span className="text-amber-600">{t("k_bde30433cd")}</span>
                      </SelectItem>
                    )}
                    {cols.map((c) => (
                      <SelectItem key={c.name} value={c.name} className="text-[11px]">
                        <span className="font-mono">{c.name}</span>
                        {c.comment && <span className="ml-1.5 text-muted-foreground">{c.comment}</span>}
                        {c.type && <span className="ml-1.5 text-muted-foreground/70">{c.type}</span>}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                // 无候选可枚举（接口失败 / 元数据未同步 / 走数据集）：回退为手填并在上方说明原因
                <Input value={raw} onChange={(e) => patchRow(row.rid, { column: e.target.value })}
                  aria-label={t('k_66c5f8bce2', { p0: label })}
                  className={`h-7 flex-1 text-[11px] font-mono ${bad ? 'border-red-400 text-red-600' : ''}`} />
              )}
              {bad && (
                <Badge variant="outline" className="text-[10px] px-1 h-4 shrink-0 text-red-500 border-red-500/40"
                  title={driftOfRow(row)?.verdict === 'unverified'
                    ? t('k_b60cf7abdb', { p0: raw })
                    : t('k_6f0901b493', { p0: raw, p1: driftOfRow(row)?.table || resolvedTable || t('k_9b93c54366') })}>
                  {driftOfRow(row)?.verdict === 'unverified' ? t('k_a9a5e3e6f1') : t('k_79d66eb6b3')}
                </Badge>
              )}
              <Button type="button" variant="ghost" size="sm"
                aria-label={t('k_8b2f6d4c01', { p0: label })}
                title={t('k_8b2f6d4c01', { p0: label })}
                className="h-7 w-7 p-0 shrink-0 text-muted-foreground hover:text-red-600"
                onClick={() => removeRow(row.rid)}>
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          );
        })}
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <Button type="button" variant="outline" size="sm" className="h-7 text-[11px]" onClick={addRow}>
          <Plus className="h-3.5 w-3.5 mr-1" />{t('k_7a1e5c3b90')}
        </Button>
        <span className="text-[10px] text-muted-foreground">{t('k_5e9c0d3a21')}</span>
      </div>
    </div>
  );
}

/** 内联新建对象行（对标设计稿 `docs/demo/ontology-modeling-redesign.html` 的 `.inline-new`）。
 *  2026-11-19 拍板：不再弹居中对话框——点「＋ 新建对象」在对象画布顶部就地展开一行，
 *  key / 显示名 / 形状填完即建，回车也能保存（设计稿里已是这个交互）。
 *  业务本体对象的形状描述可选：不绑表（先建概念）/ 绑主表 / 路由到源本体对象；
 *  v0.5 B6b 起物理绑定唯一在部署绑定层（adh_ontology_bindings），**发布级联**才登记绑定行
 *  （对象清单在发布前会显式标「未绑定物理表」）。 */
export function ObjectNewRow({ jsonContent, onSave, onDone, onCancel, modelKind }: {
  jsonContent: string;
  /** opts.stay：建完不跳「变更对比」，留在对象画布继续补属性/路由（设计稿的建完自动选中） */
  onSave: (next: string, opts?: { stay?: boolean }) => void;
  /** 建好后把新对象 key 交回外层（选中它、收起内联行） */
  onDone?: (key: string) => void;
  onCancel: () => void;
  /** 模型行的 kind（真值源）；存量 doc 顶层可能没写 kind */
  modelKind?: string;
}) {
  const { t } = useTranslation('ui');
  const [form, setForm] = useState({ key: '', display_name: '', description: '', primary_table: '' });
  const [shape, setShape] = useState<'none' | 'table' | 'route'>('none');
  const [route, setRoute] = useState<RouteValue>({
    mode: 'object_filter', source_ontology: '', filter_hints: [],
  });

  let docKind = '';
  try { docKind = String(JSON.parse(jsonContent || '{}').kind || ''); } catch { /* 非法 JSON：按模型行 kind */ }
  const isBusiness = (modelKind || docKind) === 'business';

  const submit = () => {
    const key = form.key.trim();
    if (!key) return;
    let doc: any;
    try {
      doc = JSON.parse(jsonContent || '{}');
    } catch {
      return;
    }
    doc.objects = doc.objects || [];
    // 查重按归一后的对象身份（不是裸 o.key）：存量 doc 只有 object_key 时，
    // 按 o.key 比永远不命中 → 同名对象被重复 push，保存才被服务端判重拒掉（错在更早的环节）。
    if (findObjectIndex(doc.objects, key) >= 0) {
      window.alert(t('k_afdc4db8f4', { p0: key }));
      return;
    }
    const srcName = route.source_ontology.trim();
    const table = form.primary_table.trim();
    const hints = (route.filter_hints || []).filter((h) => h.dimension.trim());
    doc.objects.push({
      key,
      display_name: form.display_name.trim() || key,
      description: form.description.trim(),
      ...(shape === 'route' && isBusiness && srcName
        ? {
            route: {
              mode: route.mode,
              source_ontology: srcName,
              datasource_name: route.datasource_name || '',
              ...(route.mode === 'object_filter' && route.object_key
                ? { object_key: route.object_key } : {}),
              ...(hints.length ? { filter_hints: hints } : {}),
            },
          }
        : {}),
      ...(shape === 'table' && table ? { primary_table: table } : {}),
      properties: [], links: [],
    });
    onSave(JSON.stringify(doc, null, 2), { stay: true });
    setForm({ key: '', display_name: '', description: '', primary_table: '' });
    setRoute({ mode: 'object_filter', source_ontology: '', filter_hints: [] });
    onDone?.(key);
  };

  return (
    <div className="rounded-lg border border-primary/30 bg-primary/5 p-2">
      <div className="flex flex-wrap items-center gap-2">
        <Input autoFocus value={form.key} className="h-7 w-52 text-xs"
          placeholder={t('k_90675ca431')}
          onChange={(e) => setForm({ ...form, key: e.target.value })}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); }} />
        <Input value={form.display_name} className="h-7 w-44 text-xs"
          placeholder={t('k_992c065229')}
          onChange={(e) => setForm({ ...form, display_name: e.target.value })}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); }} />
        <Select value={shape} onValueChange={(v) => setShape(v as 'none' | 'table' | 'route')}>
          <SelectTrigger className="h-7 w-48 text-xs" aria-label={t('k_f99ecd29c4')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none" className="text-xs">{t('k_edb62a49ac')}</SelectItem>
            <SelectItem value="table" className="text-xs">{t('k_a37b8e6c69')}</SelectItem>
            {isBusiness && (
              <SelectItem value="route" className="text-xs">{t('k_2683f8829a')}</SelectItem>
            )}
          </SelectContent>
        </Select>
        <Button size="sm" className="h-7 text-xs" onClick={submit} disabled={!form.key.trim()}>
          {t('k_7cacf6d9ea')}
        </Button>
        <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={onCancel}>
          {t('k_4d0b4688c7')}
        </Button>
        <span className="text-[11px] text-muted-foreground">{t('k_1b116e9be5')}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <Field label={t('k_581778ce60')}>
          <Input value={form.description} className="h-7 w-72 text-xs"
            onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </Field>
        {shape === 'table' && (
          <Field label={isBusiness ? t('k_df45ea2b1f') : t('k_d87c90f8b0')}>
            <Input value={form.primary_table} className="h-7 w-64 text-xs"
              placeholder={t('k_45fd0871f6')}
              onChange={(e) => setForm({ ...form, primary_table: e.target.value })} />
          </Field>
        )}
      </div>
      {shape === 'route' && isBusiness && (
        // 形状选「路由到源本体对象」时才就地展开路由编辑器（不选就不占版面）
        <div className="mt-2"><RouteEditor value={route} onChange={setRoute} /></div>
      )}
    </div>
  );
}

/** 编辑已有对象（标量字段 + 别名 + 路由/主表 + 属性列映射）。
 *  2026-10-27 拍板：废除「修改仅 AS-BOT」硬禁令，用户侧开放页面内编辑。
 *  2026-11-19 拍板：改为**右侧检查器就地编辑**，不再弹居中对话框
 *  （弹窗盖住对象上下文，改一处字段要来回关开；检查器本来就常驻在对象右侧）。
 *  与新建同一落库路径：改 canonical doc → saveDraft（存草案，两段式第一段），
 *  发布才级联生效；关系不在本表单（保留原值，量大且由 AS-BOT/JSON 维护）。
 *  属性列映射在本表单（不开放就只能改草案 JSON，而草案 JSON 视图只读 →「列待修」无修入口）。
 *  对象形状：已声明 route 的业务对象编路由，其余（含存量直绑物理表的业务对象）编 primary_table。 */
export function ObjectEditPanel({ jsonContent, onSave, target, modelKind, modelId, boundTable, drift, onCancel }: {
  jsonContent: string;
  onSave: (next: string) => void;
  /** 模型行的 kind（真值源）；存量 doc 顶层可能没写 kind，缺它时会把业务本体当源本体表单 */
  modelKind?: string;
  /** 模型 id：传入才渲染属性列映射（列候选需要按模型自有源/部署绑定解析） */
  modelId?: number;
  /** 生效部署绑定层的物理表（doc 没写 primary_table 时作为当前表名展示） */
  boundTable?: string;
  /** 该对象的属性列漂移清单（服务端实时判），用于标红待修行 */
  drift?: PropertyDriftEntry[];
  target: {
    key: string; display_name?: string; description?: string; aliases?: string[];
    primary_table?: string;
    properties?: { column?: string; name?: string; type?: string }[];
    route?: { mode?: string; source_ontology?: string; datasource_name?: string;
      object_key?: string; filter_hints?: { dimension?: string; examples?: string[] }[] };
  };
  /** 退出就地编辑（取消，或保存后由调用方把检查器切回展示态） */
  onCancel: () => void;
}) {
  const { t } = useTranslation('ui');
  const [form, setForm] = useState(() => ({
    display_name: target.display_name || '',
    description: target.description || '',
    aliases: (target.aliases || []).join(', '),
    primary_table: target.primary_table || boundTable || '',
  }));
  // 属性清单草稿（新增/删除/改名/改列）：只存本地，点「保存为草案」才写回 doc
  const [propRows, setPropRows] = useState<PropRow[]>(() => toPropRows(target.properties || []));
  const initRoute = (): RouteValue => ({
    mode: target.route?.mode === 'source' ? 'source' : 'object_filter',
    source_ontology: target.route?.source_ontology || '',
    datasource_name: target.route?.datasource_name || '',
    object_key: target.route?.object_key || '',
    filter_hints: (target.route?.filter_hints || [])
      .map((h) => ({ dimension: h.dimension || '', examples: h.examples || [] })),
  });
  const [route, setRoute] = useState<RouteValue>(initRoute);

  // kind 真值源是模型行，doc.kind 只兜底（后端 save_draft 才把 kind 补写进 doc，
  // 存量正文里根本没有该字段）。
  let docKind = '';
  try { docKind = String(JSON.parse(jsonContent || '{}').kind || ''); } catch { /* 非法 JSON：按模型行 kind */ }
  const isBusiness = (modelKind || docKind) === 'business';
  // 路由编辑器只在对象**已声明 route** 时出现：v0.5 起业务契约的 route 层不再是强制校验
  // （服务端 find_business_route_issues 已退役），存量直绑物理表的业务对象若被强制补路由，
  // 等于给「编辑」新加门槛 → 一个也改不动。未声明 route 的对象按物理表编辑。
  const editRoute = isBusiness && !!target.route;
  // 属性清单：非路由对象都可编（含 0 属性的新对象——旧口径要求“已有属性”才渲染，
  // 结果是新对象永远补不进属性）；路由对象物理列在源本体上。需 modelId 才能拉列候选
  const showColumnMap = !!modelId && !editRoute;

  // 进入另一个对象的编辑时重置表单。不把 boundTable 列入依赖：检查器就地编辑期间
  // 部署绑定若晚到，重置会把用户刚敲的内容抹掉（弹窗时代该风险被“打开即加载完”掩盖）。
  useEffect(() => {
    setForm({
      display_name: target.display_name || '',
      description: target.description || '',
      aliases: (target.aliases || []).join(', '),
      primary_table: target.primary_table || boundTable || '',
    });
    setRoute(initRoute());
    setPropRows(toPropRows(target.properties || []));
  }, [target.key]);  // eslint-disable-line react-hooks/exhaustive-deps

  const submit = () => {
    const key = target.key.trim();
    if (!key) return;
    const srcName = route.source_ontology.trim();
    let doc: any;
    try { doc = JSON.parse(jsonContent || '{}'); } catch { return; }
    // 定位按归一后的对象身份：存量 doc 用旧字段名 object_key/name，展示层已归一出 key，
    // 写入口若只认 o.key 就会假报「对象不存在」并把保存静默中止（历史缺陷）。
    const idx = findObjectIndex(doc.objects, key);
    if (idx < 0) {
      window.alert(t('k_5fdc26f2d8', { p0: target.display_name || key, p1: key }));
      return;
    }
    const hints = (route.filter_hints || []).filter((h) => h.dimension.trim());
    const next = { ...doc.objects[idx] };
    // 标量写回同步旧别名字段（doc 里不留 name/display_name 互相矛盾的两份值）
    writeObjectField(next, 'display_name', form.display_name.trim() || key);
    next.description = form.description.trim();
    next.aliases = form.aliases.split(',').map((s) => s.trim()).filter(Boolean);
    // 属性清单写回：新增/删除/改名/改列都在这里落地。
    // 原行（origIndex）的额外字段（is_key/description/enum/别名）原样保留，只覆盖改过的
    // name/column；新行按列元数据带出 type。空行（无业务名无物理列）不落库。
    const origProps: any[] = Array.isArray(doc.objects[idx].properties)
      ? doc.objects[idx].properties : [];
    const keptIdx = new Set(propRows.map((r) => r.origIndex)
      .filter((i): i is number => typeof i === 'number'));
    const nextProps = propRows.map((r) => {
      const name = r.name.trim();
      if (r.origIndex !== undefined && origProps[r.origIndex]) {
        const base = { ...origProps[r.origIndex] };
        // 存量 doc 里两种属性形状（name / display_name、column / key）都要跟原形状写，
        // 否则改一个名字就多出一份矛盾字段（_prop_identity_names 会优先看 name）。
        if (name) {
          if ('display_name' in base && !('name' in base)) base.display_name = name;
          else base.name = name;
        }
        if (r.column !== String(base.column ?? '')) base.column = r.column;
        if (r.type && !base.type && !base.data_type) base.type = r.type;
        return base;
      }
      return { column: r.column, name, ...(r.type ? { type: r.type } : {}) };
    }).filter((p) => String(p.name || p.column || '').trim());
    next.properties = nextProps;
    // 被删掉的原属性名必须**显式声明**（后端护栏按“属性名消失”阻断，未声明一律拒）：
    // 声明只放行点名删除，其他“整份覆盖顺手抹掉”照旧拦截（宁阻断勿损毁不打折）。
    const removed = origProps
      .map((p, i) => (keptIdx.has(i) ? '' : propIdentityName(p)))
      .filter((n) => !!n);
    const decl = { ...((doc as any)._declared_removals || {}) };
    const declKey = String(next.key || target.key);
    if (removed.length) decl[declKey] = removed;
    else delete decl[declKey];
    if (Object.keys(decl).length) (doc as any)._declared_removals = decl;
    else delete (doc as any)._declared_removals;
    if (!editRoute) {
      if (form.primary_table.trim()) next.primary_table = form.primary_table.trim();
      else delete next.primary_table;
    } else if (srcName) {
      next.route = {
        mode: route.mode,
        source_ontology: srcName,
        datasource_name: route.datasource_name || '',
        ...(route.mode === 'object_filter' && route.object_key ? { object_key: route.object_key } : {}),
        ...(hints.length ? { filter_hints: hints } : {}),
      };
    }
    // editRoute 但未选源（选框无「清空」项，只可能是存量 route 缺 source_ontology 的脏数据）：
    // 保留原声明不静默抹掉，也不阻止用户改显示名/描述（否则脏数据会让对象彻底改不动）
    doc.objects[idx] = next;
    onSave(JSON.stringify(doc, null, 2));
  };

  return (
    <div className="rounded-lg border bg-background">
      {/* 检查器就地编辑：头部即操作区，取消/保存在视线内，无需滚到弹窗底部 */}
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <Pencil className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="text-sm font-medium truncate">
          {t('k_49030f2381', { p0: target.display_name || target.key })}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-2">
          <Button size="sm" variant="outline" onClick={onCancel}>{t("k_4d0b4688c7")}</Button>
          <Button size="sm" onClick={submit}>{t("k_7cacf6d9ea")}</Button>
        </span>
      </div>
      <div className="space-y-3 p-3">
        <Field label={t("k_90675ca431")}>
          <Input value={target.key} readOnly disabled />
        </Field>
        <Field label={t("k_c35a132911")}>
          <Input value={form.display_name} placeholder={t("k_992c065229")}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        </Field>
        <Field label={t("k_e1ddcc8f96")}>
          <Input value={form.aliases} placeholder={t('k_72ce31f7e4')}
            onChange={(e) => setForm({ ...form, aliases: e.target.value })} />
        </Field>
        <Field label={t("k_581778ce60")}>
          <Textarea rows={2} value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </Field>
        {editRoute ? (
          <RouteEditor value={route} onChange={setRoute} />
        ) : (
          <Field label={t('k_f8b7cb9d18')}>
            <Input value={form.primary_table} placeholder={t('k_45fd0871f6')}
              onChange={(e) => setForm({ ...form, primary_table: e.target.value })} />
          </Field>
        )}
        {showColumnMap && (
          <PropertyColumnEditor modelId={Number(modelId)} objectKey={target.key}
            // 候选永远跟着表单里看到的表名走（所见即所校）；留空则服务端按部署绑定行 → doc 解析
            table={form.primary_table.trim()}
            rows={propRows} drift={drift || []}
            onRowsChange={setPropRows} />
        )}
      </div>
    </div>
  );
}
