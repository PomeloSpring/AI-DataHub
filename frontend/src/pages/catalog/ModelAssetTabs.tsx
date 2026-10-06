/**
 * 模型工作区附加页签：图谱可视化(嵌入) 与 对象×标签。
 *
 * ModelGraphTab: 可视化整体并入工作区 —— 按当前模型的数据源拉图,
 *   支持 总览/表关系/业务知识/血缘 四视图切换; 复用 KnowledgeGraphView(ReactFlow)。
 * ObjectTagsTab: 本模型对象(按 primary_table)已打的标签聚合视图;
 *   标签定义管理仍在标签中心, 此处解决"建模时看不到对象业务色彩"的问题。
 */
import { useState, useEffect, useCallback } from 'react';
import client from '@/api/client';
import { ontologyApi } from '@/api/ontology';
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

const GRAPH_VIEWS = [
  { key: 'ontology-overview', label: '本体总览' },
  { key: 'table-relation', label: '表关系' },
  { key: 'business-knowledge', label: '业务知识' },
  { key: 'data-lineage', label: '血缘桥接' },
];

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
  const [view, setView] = useState(singleView || 'ontology-overview');
  const [loading, setLoading] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [selected, setSelected] = useState<any | null>(null);
  const { graphData, fetchGraphData } = useGraphStore();

  const load = useCallback(async () => {
    setLoading(true);
    setSelected(null);
    try {
      // 系统域走 ds:-1(system_scope), 否则按当前模型数据源取图
      await fetchGraphData({ graphType: view, datasourceId, systemScope, limit: 200 });
    } finally {
      setLoading(false);
    }
  }, [view, datasourceId, systemScope, fetchGraphData]);

  useEffect(() => { load(); }, [load]);

  const stale = graphData && (graphData.nodes.length === 0) && !loading;
  // 系统域不提供血缘桥接(血缘面向业务数据源); AS-BOT 只需 总览/表关系/业务知识。
  const availableViews = systemScope
    ? GRAPH_VIEWS.filter((v) => v.key !== 'data-lineage')
    : GRAPH_VIEWS;

  return (
    <div className={fullscreen
      ? 'fixed inset-0 z-50 bg-background p-4 flex flex-col'
      : 'h-[68vh] flex flex-col border rounded-lg overflow-hidden'}>
      <div className="flex items-center gap-1 px-2 py-1.5 border-b bg-muted/30 shrink-0">
        {!singleView && availableViews.map((v) => (
          <Button
            key={v.key}
            size="sm"
            variant={view === v.key ? 'secondary' : 'ghost'}
            className="h-7 text-xs"
            onClick={() => setView(v.key)}
          >{v.label}</Button>
        ))}
        {singleView && (
          <span className="text-xs font-medium text-muted-foreground px-1">本体总览（对象为点 · Link 为名边）</span>
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
              <div className="text-xs mt-1">激活模型或保存 active 模型会自动重建图谱</div>
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

/** 工作区头部同步状态徽章(图谱/知识库) */
export function SyncBadges({ modelId, refreshKey }: { modelId: number; refreshKey?: number }) {
  const [st, setSt] = useState<{ graph: { state: string; classes?: number; objects?: number }; kb: { state: string; error?: string } } | null>(null);

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
  const kOk = k.state === 'success', kWarn = k.state === 'failed' || k.state === 'no_kb_bound';
  const gLabel = ({ synced: '已同步', lagging: `滞后 ${g.classes ?? 0}/${g.objects ?? 0}`, absent: '未生成', draft: '草案未激活', unreachable: '图服务不可达' } as Record<string, string>)[g.state] || g.state;
  const kLabel = ({ success: '已同步', failed: k.error ? `失败: ${k.error.slice(0, 30)}` : '失败', pending: '待同步', removed: '已下线', no_kb: '未开启同步', no_kb_bound: '未绑定知识库' } as Record<string, string>)[k.state] || k.state;
  return (
    <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
      <span className="flex items-center gap-1">{dot(gOk, gWarn)}图谱 {gLabel}</span>
      <span className="flex items-center gap-1">{dot(kOk, kWarn)}知识库 {kLabel}</span>
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
    return JSON.parse(jsonContent || '{}');
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
        .filter((m) => m.status === 'active' && (m.kind === 'source' || !m.kind))
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
      setSrcObjects((doc.objects || []).map((o: any) => ({ key: o.key || '', label: o.display_name || o.key || '' })));
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
