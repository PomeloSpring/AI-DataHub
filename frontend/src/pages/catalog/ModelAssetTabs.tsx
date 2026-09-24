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
import { useGraphStore } from '@/stores/graphStore';
import KnowledgeGraphView from '@/components/graph/KnowledgeGraph';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Network, Tag as TagIcon, Maximize2, Minimize2, X } from 'lucide-react';

const GRAPH_VIEWS = [
  { key: 'ontology-overview', label: '本体总览' },
  { key: 'table-relation', label: '表关系' },
  { key: 'business-knowledge', label: '业务知识' },
  { key: 'data-lineage', label: '血缘桥接' },
];

export function ModelGraphTab({ datasourceId, singleView, systemScope }: {
  datasourceId: number;
  /** 锁定单视图(如仅总览), 不渲染切换器 */
  singleView?: string;
  /** 系统域(AS-BOT): 只读系统图 ds:-1, 展示 总览/表关系/业务知识 三视图, 隔离业务本体 */
  systemScope?: boolean;
}) {
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
          {systemScope ? 'AS-BOT 系统能力本体（系统域·已隔离业务本体）' : singleView ? 'AS-BOT 系统能力本体' : `数据源 #${datasourceId}`} · {loading ? '加载中…' : `${graphData?.nodes.length ?? 0} 节点 / ${graphData?.edges.length ?? 0} 边 · 可拖拽布局, ↻ 一键还原`}
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
export function SyncBadges({ modelId }: { modelId: number }) {
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
  }, [modelId]);

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
