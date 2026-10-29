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
import { ontologyApi, type ContractObjectRow, type DraftObjectDiff } from '@/api/ontology';

/** 草案视图下每个对象的状态标记（由服务端 draft_diff 算好，前端不再自己比） */
const DRAFT_STATE_META: Record<string, { label: string; cls: string }> = {
  added: { label: '新增', cls: 'text-green-600 border-green-500/40' },
  changed: { label: '修改', cls: 'text-blue-600 border-blue-500/40' },
  removed: { label: '删除', cls: 'text-red-600 border-red-500/40' },
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

/** 部署绑定层读取状态（对象是否可取数的唯一事实源） */
interface BindState {
  loading: boolean;
  error: string;
  rows: Record<string, ContractObjectRow>;
}
const BIND_INIT: BindState = { loading: true, error: '', rows: {} };

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
        setBind({ loading: false, error: '', rows });
      })
      .catch((e: any) => {
        if (cancelled) return;
        // 读不到绑定层时不得回退到 doc 字段“猜一个绑定了”，必须显式未知 + 原因
        setBind({ loading: false, error: String(e?.response?.statusText || e?.message || '接口失败'), rows: {} });
      });
    return () => { cancelled = true; };
  }, [modelId]);

  const contractOf = (key: string): ContractObjectRow | undefined => {
    const rows = bind.rows;
    return rows[key] || rows[Object.keys(rows).find((k) => k.toLowerCase() === String(key).toLowerCase()) || ''];
  };

  const bindHint = viewMode === 'draft'
    ? '草案对象尚未部署（发布后在部署绑定层登记物理表）'
    : modelKind === 'source'
      ? '源本体对象的物理绑定由元数据同步自动登记，该对象尚无生效绑定行'
      : '请在「同构站点部署」页为该对象登记物理表';

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
            {issues.length} 个对象定义无法展示：{issues.map((e) => `${e.label}${e.hint}`).join('；')}
            <div className="mt-0.5 text-amber-600/80">请在 JSON 中补齐 key（对象英文标识）后保存</div>
          </div>
        )}
        <div className="text-xs text-muted-foreground mb-2 flex items-center gap-1.5">
          对象（{list.length}）
          {viewMode === 'draft' && (
            <Badge variant="outline" className="text-[10px] px-1.5 h-4 border-amber-500/40 text-amber-600">草案</Badge>
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
                    <span className="truncate" title="草案中的变更摘要">
                      {[delta.properties_added?.length ? `+${delta.properties_added.length}` : '',
                        delta.properties_removed?.length ? `-${delta.properties_removed.length}` : '',
                        delta.links_added?.length ? `关系+${delta.links_added.length}` : '']
                        .filter(Boolean).join(' ')}
                    </span>
                  ) : null}
                  {o.route?.source_ontology
                    ? <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-green-600 border-green-500/30"
                        title={`路由至 ${o.route.source_ontology}${o.route.object_key ? ' · ' + o.route.object_key : ''}`}>route</Badge>
                    : bind.loading
                      ? <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-muted-foreground border-border"
                          title="绑定状态来自部署绑定层，加载中">…</Badge>
                      : bind.error
                        ? <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-red-500 border-red-500/30"
                            title={`无法读取部署绑定（${bind.error}），不拿 doc 字段猜绑定状态`}>绑定状态未知</Badge>
                        : contractOf(o.key)?.physical_table
                          ? <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-green-600 border-green-500/30"
                              title={`部署绑定: ${contractOf(o.key)!.datasource || '未指定数据源'} · ${contractOf(o.key)!.bind_kind || 'primary'}`}>bound</Badge>
                          : <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-amber-500 border-amber-500/30"
                              title={bindHint}>未绑定物理表</Badge>}
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
          {issues.length > 0 ? '该模型的对象定义均缺少 key，无法展示（见左侧提示）' : '该模型暂无对象'}
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
                  <ObjectEditButton jsonContent={jsonContent} onSave={onSave} target={obj} />
                </span>
              )}
            </div>
            {obj.description && <p className="text-sm text-muted-foreground mt-1">{obj.description}</p>}
            {obj.route?.source_ontology && (
              <div className="text-xs text-muted-foreground mt-1">
                路由至: 源本体「{obj.route.source_ontology}」
                {obj.route.object_key ? ` · 对象「${obj.route.object_key}」` : ''}
                {obj.route.datasource_name ? `（数据源: ${obj.route.datasource_name}）` : ''}
                {(obj.route.filter_hints || []).length > 0 &&
                  `，过滤提示: ${obj.route.filter_hints!.map((h) => `${h.dimension}(${(h.examples || []).join('/')})`).join('；')}`}
              </div>
            )}
            {(() => {
              const c = contractOf(obj.key);
              if (c?.physical_table) {
                return (
                  <div className="text-xs text-muted-foreground mt-1">
                    物理绑定: {c.physical_table}
                    {c.datasource ? <> · 数据源「{c.datasource}」</> : null}
                    {c.bind_kind === 'dataset' ? ' · 走数据集（快速路径）' : ''}
                    {c.column_count ? ` · ${c.column_count} 列映射` : ''}
                  </div>
                );
              }
              if (obj.execution_binding?.physical_table) {
                // 调用方显式传入的绑定快照（非本页自取）保留展示，但不参与徽标裁决
                return (
                  <div className="text-xs text-muted-foreground mt-1">
                    绑定: {obj.execution_binding.physical_table || obj.primary_table || '-'}
                    {obj.execution_binding.query_mode && <> · {obj.execution_binding.query_mode}</>}
                    {obj.execution_binding.bind_kind === 'dataset' && obj.execution_binding.dataset_ref &&
                      <> · 查询数据集 {obj.execution_binding.dataset_ref}</>}
                  </div>
                );
              }
              if (bind.loading) return <div className="text-xs text-muted-foreground mt-1">物理绑定: 加载中…</div>;
              if (bind.error) return (
                <div className="text-xs text-red-500/80 mt-1">物理绑定: 读取部署绑定失败（{bind.error}）</div>);
              return (
                <div className="text-xs text-amber-600 mt-1 flex items-center gap-1">
                  <AlertTriangle className="h-3 w-3" />物理绑定: 该对象尚无生效的部署绑定行，无法取数 · {bindHint}
                </div>);
            })()}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* 属性 */}
            <div className="rounded-lg border">
              <div className="px-3 py-2 border-b text-sm font-medium flex items-center gap-2">
                <Layers className="h-4 w-4 text-muted-foreground" /> 属性（{(obj.properties || []).length}）
              </div>
              <ScrollArea className="h-56">
                <div className="p-1">
                  {(obj.properties || []).map((p, i) => (
                    <div key={p.column || i} className="flex items-center gap-2 px-2 py-1.5 text-[13px] border-b last:border-0">
                      <span className="truncate">{p.name || p.column}</span>
                      <span className="text-xs text-muted-foreground font-mono truncate">{p.column}</span>
                      <span className="ml-auto flex items-center gap-1">
                        {p.is_key && <Badge variant="outline" className="text-[10px] px-1 h-4">PK</Badge>}
                        {p.enum?.length ? <Badge variant="outline" className="text-[10px] px-1 h-4">枚举{p.enum.length}</Badge> : null}
                      </span>
                    </div>
                  ))}
                  {!(obj.properties || []).length && (
                    <div className="text-xs text-muted-foreground p-3">暂无属性</div>)}
                </div>
              </ScrollArea>
            </div>

            {/* 关系 */}
            <div className="rounded-lg border">
              <div className="px-3 py-2 border-b text-sm font-medium flex items-center gap-2">
                <Link2 className="h-4 w-4 text-muted-foreground" /> 关系 Link（{(obj.links || []).length}）
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
                        {orphan && <span title="目标对象不在本模型内（引用悬空）"><AlertTriangle className="h-3.5 w-3.5 inline" /></span>}
                      </div>
                    );
                  })}
                  {orphanLinks.length > 0 && (
                    <div className="px-2 py-1.5 text-[11px] text-amber-500">
                      ⚠ {orphanLinks.length} 条 Link 目标不在本模型，激活校验会拒绝
                    </div>
                  )}
                  {!(obj.links || []).length && <div className="text-xs text-muted-foreground p-3">暂无关系</div>}
                </div>
              </ScrollArea>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
