/**
 * ObjectHubTab — 模型工作区「对象 · Link」中枢页签。
 *
 * 职责边界（memory「对象Link页面展示规范」）：本页**仅展示属性与关系(Link)**。
 * 指标、维度、术语已迁至独立的「指标与维度」全局页，此处不再显示其面板/计数徽标，
 * 避免语义资产在本体页与专属页两处呈现造成归属错觉。
 */
import { useState, useEffect, useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Layers, Link2, AlertTriangle, Boxes } from 'lucide-react';
import { normalizeDocObjects, type DocObjectIssue } from '@/lib/ontologyObjects';
import { ObjectEditButton } from './ModelAssetTabs';
import { ontologyApi, type ContractObjectRow, type DraftObjectDiff, type PropertyDriftEntry } from '@/api/ontology';
import { t } from '@/i18n/runtime';
import { useTranslation } from 'react-i18next';

/** 草案视图下每个对象的状态标记（由服务端 draft_diff 算好，前端不再自己比） */
const DRAFT_STATE_META: Record<string, { label: string; cls: string }> = {
  added: { label: t('k_2cd9e6ce81'), cls: 'text-green-600 border-green-500/40' },
  changed: { label: t('k_c9c77517fe'), cls: 'text-blue-600 border-blue-500/40' },
  removed: { label: t('k_3755f56f2f'), cls: 'text-red-600 border-red-500/40' },
  unchanged: { label: '', cls: '' },
};

export interface HubObject {
  key: string;
  display_name?: string;
  aliases?: string[];
  description?: string;
  primary_table?: string;
  /** 业务本体路由指针（路由索引层）：指向目标源本体/源对象 + 源内过滤提示 */
  route?: {
    mode?: string; source_ontology?: string; datasource_name?: string; object_key?: string;
    filter_hints?: { dimension?: string; examples?: string[] }[];
  };
  properties?: { column?: string; name?: string; type?: string; is_key?: boolean; description?: string; enum?: string[] }[];
  links?: { type?: string; target?: string; cardinality?: string; description?: string }[];
  execution_binding?: { query_mode?: string; size_class?: string; physical_table?: string; bind_kind?: string; dataset_ref?: string };
}

/** 部署绑定层读取状态（对象是否可取数的唯一事实源）+ 属性列漂移（同一次读取带回） */
interface BindState {
  loading: boolean;
  error: string;
  rows: Record<string, ContractObjectRow>;
  /** object key(小写) → 漂移属性；"表还在但列改名"在页面上唯一的可见出口 */
  drift: Record<string, PropertyDriftEntry[]>;
}
const BIND_INIT: BindState = { loading: true, error: '', rows: {}, drift: {} };

export default function ObjectHubTab({ modelId, objects, issues = [], jsonContent, onSave, canEdit = false,
  viewMode = 'live', draftStates, draftHint, modelKind }: {
  modelId: number;
  objects: HubObject[];
  /** doc 里无法定位身份的元素（已由上层归一剔除），必须在页面上显式提示，不得静默当作“无对象” */
  issues?: DocObjectIssue[];
  /** 编辑对象需的当前 canonical doc + 存草案回调（2026-10-27 开放页面内编辑）。
   *  未传（如只读展示场景）则不渲染编辑入口。 */
  jsonContent?: string;
  onSave?: (next: string) => void;
  /** 是否可编辑（上层按 editable + ontology:save 权限裁决） */
  canEdit?: boolean;
  /** 当前渲染的是生效版还是草案正文（草案视图下对象带状态标记） */
  viewMode?: 'live' | 'draft';
  /** key(小写) → 草案状态；仅草案视图使用 */
  draftStates?: Record<string, DraftObjectDiff>;
  /** 草案视图下的引导文案（如“此处编辑会写入草案”） */
  draftHint?: string;
  /** 模型 kind（源本体不展示「同构站点部署」页，引导文案需随形态区分） */
  modelKind?: string;
}) {
  const { t } = useTranslation('ui');
  const [selKey, setSelKey] = useState<string>('');
  // 上界保护：即便调用方未做归一，也不会因元素缺 key / 非数组而在渲染期崩溃
  const list = useMemo(
    () => normalizeDocObjects(objects).objects as HubObject[],
    [objects]);

  /**
   * 对象绑定状态 = 部署绑定层事实（v0.5 B6b：物理绑定唯一在 `adh_ontology_bindings`，
   * doc 里的 `execution_binding` 在 save_draft 时被剥离、`primary_table` 对新建对象也不存在）。
   * 历史缺陷：徽标拿 doc 字段当裁决 → 对象明明有生效绑定（可取数）却常年显示「未绑表」，
   * 假告警把人工引向错误的修复方向（去补 doc 字段，而真正缺的是字典 target_table 一类错配）。
   */
  const [bind, setBind] = useState<BindState>(BIND_INIT);
  useEffect(() => {
    let cancelled = false;
    setBind(BIND_INIT);
    ontologyApi.sites(modelId)
      .then(({ data }) => {
        if (cancelled) return;
        const rows: Record<string, ContractObjectRow> = {};
        for (const r of (data?.contract_objects || [])) rows[String(r.object_key)] = r;
        const drift: Record<string, PropertyDriftEntry[]> = {};
        for (const [k, v] of Object.entries(data?.property_drift || {})) {
          drift[String(k).toLowerCase()] = v || [];
        }
        setBind({ loading: false, error: '', rows, drift });
      })
      .catch((e: any) => {
        if (cancelled) return;
        // 读不到绑定层时不得回退到 doc 字段“猜一个绑定了”，必须显式未知 + 原因
        setBind({ loading: false, error: String(e?.response?.statusText || e?.message || t('k_eb0c1382c1')), rows: {}, drift: {} });
      });
    return () => { cancelled = true; };
  }, [modelId]);

  const contractOf = (key: string): ContractObjectRow | undefined => {
    const rows = bind.rows;
    return rows[key] || rows[Object.keys(rows).find((k) => k.toLowerCase() === String(key).toLowerCase()) || ''];
  };

  const bindHint = viewMode === 'draft'
    ? t('k_7d27452eb0')
    : modelKind === 'source'
      ? t('k_dd7767afe2')
      : t('k_da0a0a64d0');

  /** 该对象的属性列漂移清单（服务端实时判，不靠前端猜列名） */
  const driftOf = (key: string): PropertyDriftEntry[] =>
    bind.drift[String(key ?? '').toLowerCase()] || [];


  const obj = useMemo(
    () => list.find((o) => o.key === selKey) || list[0] || null,
    [list, selKey]);

  const objKeys = useMemo(
    () => new Set(list.map((o) => String(o.key ?? '').toLowerCase()).filter(Boolean)),
    [list]);
  const orphanLinks = (obj?.links || []).filter(
    (l) => l.target && !objKeys.has(String(l.target).toLowerCase()));

  return (
    <div className="flex gap-4 min-h-[60vh]">
      {/* 对象清单 */}
      <div className="w-60 shrink-0 border-r pr-3">
        {issues.length > 0 && (
          <div className="mb-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-600">
            <AlertTriangle className="h-3 w-3 mr-1 inline" />
            {t('k_e216c26667', { p0: issues.length, p1: issues.map((e) => `${e.label}${e.hint}`).join('; ') })}
            <div className="mt-0.5 text-amber-600/80">{t("k_7e5b0f1568")}</div>
          </div>
        )}
        <div className="text-xs text-muted-foreground mb-2 flex items-center gap-1.5">
          {t('k_af21e8b47a', { p0: list.length })}
          {viewMode === 'draft' && (
            <Badge variant="outline" className="text-[10px] px-1.5 h-4 border-amber-500/40 text-amber-600">{t("k_bbcb968bf3")}</Badge>
          )}
        </div>
        {viewMode === 'draft' && draftHint && (
          <div className="mb-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-700">
            {draftHint}
          </div>
        )}
        <ScrollArea className="h-[62vh]">
          <div className="space-y-1.5 pr-2">
            {list.map((o) => {
              const ds = draftStates?.[String(o.key ?? '').toLowerCase()];
              const dsState = viewMode === 'draft' ? ds?.state : undefined;
              const st = dsState && dsState !== 'unchanged' ? DRAFT_STATE_META[dsState] : null;
              const delta = ds?.delta;
              return (
              <button
                key={o.key}
                onClick={() => setSelKey(o.key)}
                className={`w-full text-left rounded-lg border px-2.5 py-2 transition-colors
                  ${(obj?.key) === o.key ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/50'}`}
              >
                <div className="text-sm font-medium truncate flex items-center gap-1.5">
                  <span className="truncate">{o.display_name || o.key}</span>
                  {st && (
                    <Badge variant="outline" className={`text-[10px] px-1 py-0 h-4 shrink-0 ${st.cls}`}>
                      {st.label}
                    </Badge>
                  )}
                </div>
                <div className="flex items-center gap-1.5 mt-1 text-[11px] text-muted-foreground">
                  <span className="font-mono">{o.key}</span>
                  <span className="ml-auto" />
                  {st && delta ? (
                    <span className="truncate" title={t("k_026380221f")}>
                      {[delta.properties_added?.length ? `+${delta.properties_added.length}` : '',
                        delta.properties_removed?.length ? `-${delta.properties_removed.length}` : '',
                        delta.links_added?.length ? t('k_c57c604bef', { p0: delta.links_added.length }) : '']
                        .filter(Boolean).join(' ')}
                    </span>
                  ) : null}
                  {o.route?.source_ontology
                    ? <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-green-600 border-green-500/30"
                        title={t('k_f1be533989', { p0: o.route.source_ontology, p1: o.route.object_key ? ' · ' + o.route.object_key : '' })}>route</Badge>
                    : bind.loading
                      ? <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-muted-foreground border-border"
                          title={t("k_2f2d8627fb")}>…</Badge>
                      : bind.error
                        ? <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-red-500 border-red-500/30"
                            title={t('k_77d41e766a', { p0: bind.error })}>{t("k_5c067c3c3a")}</Badge>
                        : contractOf(o.key)?.physical_table
                          ? <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-green-600 border-green-500/30"
                              title={t('k_eb00b37218', { p0: contractOf(o.key)!.datasource || t('k_a44b739c55'), p1: contractOf(o.key)!.bind_kind || 'primary' })}>bound</Badge>
                          : <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-amber-500 border-amber-500/30"
                              title={bindHint}>{t("k_f059a1e4bf")}</Badge>}
                {(() => {
                  // 徽标按 verdict 分流：列不存在（定义要改）与列元数据未同步（要先同步）
                  // 不是同一件事，统一叫「列待修」会把人引向一个改了也没用的操作。
                  const dr = driftOf(o.key);
                  const unver = dr.filter((d) => d.verdict === 'unverified').length;
                  const missing = dr.length - unver;
                  if (!dr.length) return null;
                  return (<>
                    {missing > 0 && (
                      <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-red-500 border-red-500/30 shrink-0"
                        title={t('k_0d48d341a5', { p0: dr.filter((d) => d.verdict !== 'unverified').map((d) => `${d.property}(${d.column})`).join('、') })}>
                        {t('k_6791baa85b', { p0: missing })}
                      </Badge>
                    )}
                    {unver > 0 && (
                      <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-amber-500 border-amber-500/30 shrink-0"
                        title={t('k_c1d46e565d', { p0: unver })}>
                        {t('k_1488962e5c', { p0: unver })}
                      </Badge>
                    )}
                  </>);
                })()}
                </div>
              </button>
            );})}
          </div>
        </ScrollArea>
      </div>

      {/* 对象中枢详情 */}
      {!obj ? (
        <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
          <Boxes className="h-8 w-8 mr-2 opacity-40" />
          {issues.length > 0 ? t('k_d54610fd36') : t('k_4b7be4ab87')}
        </div>
      ) : (
        <div className="flex-1 min-w-0 space-y-4">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="text-base font-semibold">{obj.display_name || obj.key}</h3>
              <span className="font-mono text-xs text-muted-foreground">adh:obj:{obj.key}</span>
              {(obj.aliases || []).map((a) => <Badge key={a} variant="secondary" className="text-[11px]">{a}</Badge>)}
              {canEdit && onSave && jsonContent != null && (
                <span className="ml-auto">
                  <ObjectEditButton jsonContent={jsonContent} onSave={onSave} target={obj}
                    modelKind={modelKind} modelId={modelId}
                    boundTable={contractOf(obj.key)?.physical_table || ''}
                    drift={driftOf(obj.key)} />
                </span>
              )}
            </div>
            {obj.description && <p className="text-sm text-muted-foreground mt-1">{obj.description}</p>}
            {obj.route?.source_ontology && (
              <div className="text-xs text-muted-foreground mt-1">
                {t('k_007128c7cd', { p0: obj.route.source_ontology, p1: obj.route.object_key ? t('k_31d0f1f3e0', { p0: obj.route.object_key }) : '', p2: obj.route.datasource_name ? t('k_0bfb0f0d36', { p0: obj.route.datasource_name }) : '', p3: (obj.route.filter_hints || []).length > 0 &&
                  t('k_b841a13976', { p0: obj.route.filter_hints!.map((h) => `${h.dimension}(${(h.examples || []).join('/')})`).join(t('k_c4530db333')) }) })}
              </div>
            )}
            {(() => {
              const c = contractOf(obj.key);
              if (c?.physical_table) {
                const dr = driftOf(obj.key);
                return (
                  <div className="text-xs text-muted-foreground mt-1">
                    {t('k_90448b1471', { p0: c.physical_table })}
                    {c.datasource ? <> {t('k_5e9e5ee725', { p0: c.datasource })}</> : null}
                    {c.bind_kind === 'dataset' ? t('k_e49adfe7c7') : ''}
                    {c.column_count ? t('k_a1d2f4fd41', { p0: c.column_count }) : ''}
                    {c.sync_state === 'drifted' ? t('k_ce21714f0b') : ''}
                    {dr.length > 0 && (
                      <div className="text-amber-600 mt-0.5 flex items-start gap-1">
                        <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
                        <span>
                          {(() => {
                            const unv = dr.filter((d) => d.verdict === 'unverified');
                            const bad = dr.filter((d) => d.verdict !== 'unverified');
                            const seg: string[] = [];
                            if (bad.length) {
                              seg.push(t('k_a5104b3dec', { p0: bad.length, p1: bad.slice(0, 3).map((d) => `${d.property}→${d.column}`).join(t('k_b505660a97')) + (bad.length > 3 ? t('k_11ccee3ac8') : '') }));
                            }
                            if (unv.length) {
                              seg.push(t('k_f42836272f', { p0: unv.length, p1: unv.slice(0, 3).map((d) => `${d.property}→${d.column}`).join(t('k_b505660a97')) + (unv.length > 3 ? t('k_11ccee3ac8') : ''), p2: unv[0]?.table || t('k_9b93c54366') }));
                            }
                            return seg.join(' ');
                          })()}
                        </span>
                      </div>
                    )}
                  </div>
                );
              }
              if (obj.execution_binding?.physical_table) {
                // 调用方显式传入的绑定快照（非本页自取）保留展示，但不参与徽标裁决
                return (
                  <div className="text-xs text-muted-foreground mt-1">
                    {t('k_d039f4564c', { p0: obj.execution_binding.physical_table || obj.primary_table || '-' })}
                    {obj.execution_binding.query_mode && <> · {obj.execution_binding.query_mode}</>}
                    {obj.execution_binding.bind_kind === 'dataset' && obj.execution_binding.dataset_ref &&
                      <> {t('k_149a1caef7', { p0: obj.execution_binding.dataset_ref })}</>}
                  </div>
                );
              }
              if (bind.loading) return <div className="text-xs text-muted-foreground mt-1">{t('k_104875140a')}</div>;
              if (bind.error) return (
                <div className="text-xs text-red-500/80 mt-1">{t('k_40ae01a569', { p0: bind.error })}</div>);
              return (
                <div className="text-xs text-amber-600 mt-1 flex items-center gap-1">
                  <AlertTriangle className="h-3 w-3" />{t('k_6a285a8f45', { p0: bindHint })}
                </div>);
            })()}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* 属性 */}
            <div className="rounded-lg border">
              <div className="px-3 py-2 border-b text-sm font-medium flex items-center gap-2">
                <Layers className="h-4 w-4 text-muted-foreground" /> {t('k_83489a8e2f', { p0: (obj.properties || []).length })}
              </div>
              <ScrollArea className="h-56">
                <div className="p-1">
                  {(() => {
                    const dr = driftOf(obj.key);
                    return (obj.properties || []).map((p, i) => {
                      const d = dr.find((x) => x.property && x.property === (p.name || p.column));
                      return (
                        <div key={p.column || i} className="flex items-center gap-2 px-2 py-1.5 text-[13px] border-b last:border-0">
                          <span className="truncate">{p.name || p.column}</span>
                          <span className={`text-xs font-mono truncate ${d ? 'text-red-500' : 'text-muted-foreground'}`}>{p.column}</span>
                          <span className="ml-auto flex items-center gap-1">
                            {d && (
                              <Badge variant="outline" className="text-[10px] px-1 h-4 text-red-500 border-red-500/40"
                                title={d.verdict === 'unverified'
                                  ? t('k_7eb22f2e8c', { p0: d.column })
                                  : t('k_863abb4a59', { p0: d.column, p1: d.table })}>
                                {d.verdict === 'unverified' ? t('k_a9a5e3e6f1') : t('k_79d66eb6b3')}
                              </Badge>
                            )}
                            {p.is_key && <Badge variant="outline" className="text-[10px] px-1 h-4">PK</Badge>}
                            {p.enum?.length ? <Badge variant="outline" className="text-[10px] px-1 h-4">{t('k_c85885de70', { p0: p.enum.length })}</Badge> : null}
                          </span>
                        </div>
                      );
                    });
                  })()}
                  {!(obj.properties || []).length && (
                    <div className="text-xs text-muted-foreground p-3">{t("k_7c1acf8680")}</div>)}
                </div>
              </ScrollArea>
            </div>

            {/* 关系 */}
            <div className="rounded-lg border">
              <div className="px-3 py-2 border-b text-sm font-medium flex items-center gap-2">
                <Link2 className="h-4 w-4 text-muted-foreground" /> {t('k_73286542d3', { p0: (obj.links || []).length })}
              </div>
              <ScrollArea className="h-56">
                <div className="p-1">
                  {(obj.links || []).map((l, i) => {
                    const orphan = l.target && !objKeys.has(String(l.target).toLowerCase());
                    return (
                      <div key={i} className={`flex items-center gap-2 px-2 py-1.5 text-[13px] border-b last:border-0 ${orphan ? 'text-amber-500' : ''}`}>
                        <span>{l.type || 'references'}</span>
                        <button
                          className="underline decoration-dotted underline-offset-2"
                          onClick={() => l.target && setSelKey(String(l.target))}
                        >{l.target}</button>
                        {l.cardinality && <span className="text-xs text-muted-foreground">{l.cardinality}</span>}
                        {orphan && <span title={t("k_02efe42ae7")}><AlertTriangle className="h-3.5 w-3.5 inline" /></span>}
                      </div>
                    );
                  })}
                  {orphanLinks.length > 0 && (
                    <div className="px-2 py-1.5 text-[11px] text-amber-500">
                      {t('k_286d6ce2dd', { p0: orphanLinks.length })}</div>
                  )}
                  {!(obj.links || []).length && <div className="text-xs text-muted-foreground p-3">{t("k_d1db64962d")}</div>}
                </div>
              </ScrollArea>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
