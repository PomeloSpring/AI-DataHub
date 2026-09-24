/**
 * ObjectHubTab — 模型工作区「对象 · Link」中枢页签。
 *
 * 一个本体对象的所有建模信息在同一屏配置/查看：属性、关系(Link)、挂接指标、维度、绑定术语。
 * 数据全部走"模型作用域"接口（scope=model），只显示 bound_object_key ∈ 本模型对象 的资产。
 * 编辑入口唯一性：此处只做 归属/跳转/概览，字段编辑仍在各自主编辑器（此处即调用同一列表 API）。
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import client from '@/api/client';
import { metricsApi } from '@/api/metrics';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Layers, Link2, BarChart3, BookOpen, AlertTriangle, Boxes } from 'lucide-react';

export interface HubObject {
  key: string;
  display_name?: string;
  aliases?: string[];
  description?: string;
  primary_table?: string;
  properties?: { column: string; name?: string; type?: string; is_key?: boolean; description?: string; enum?: string[] }[];
  links?: { type?: string; target?: string; cardinality?: string; description?: string }[];
  execution_binding?: { query_mode?: string; size_class?: string; physical_table?: string; bind_kind?: string; template_ref?: string };
}

interface BoundMetric {
  id: number; name: string; name_en?: string; display_name?: string;
  bound_object_key?: string; agg_type?: string; unit?: string;
}
interface BoundDim {
  id: number; name: string; bound_object_key?: string; category?: string;
  target_column?: string; value_labels?: any;
}
interface BoundTerm {
  id: number; term_cn: string; term_type?: string; target_table?: string;
  target_column?: string; description?: string;
}

function parseJson<T>(v: unknown, fallback: T): T {
  if (v == null) return fallback;
  if (typeof v === 'string') { try { return JSON.parse(v) as T; } catch { return fallback; } }
  return v as T;
}

export default function ObjectHubTab({ modelId, objects }: { modelId: number; objects: HubObject[] }) {
  const [selKey, setSelKey] = useState<string>('');
  const [metrics, setMetrics] = useState<BoundMetric[]>([]);
  const [dims, setDims] = useState<BoundDim[]>([]);
  const [terms, setTerms] = useState<BoundTerm[]>([]);
  const [metricsErr, setMetricsErr] = useState('');
  const [dimsErr, setDimsErr] = useState('');

  const obj = useMemo(
    () => objects.find((o) => o.key === selKey) || objects[0] || null,
    [objects, selKey]);

  // 模型作用域资产: 指标/维度 **各自独立加载**(不共用 Promise.all+catch),
  // 避免维度一次失败把已取回的指标一并清 0(旧行为=静默降级, 把"加载失败"伪装成"0 指标0维度")。
  const loadScoped = useCallback(async () => {
    setMetricsErr(''); setDimsErr('');
    try {
      const { data: m } = await metricsApi.list({ model_id: modelId, scope: 'model', page: 1, size: 200 });
      setMetrics(m?.items || []);
    } catch (e: any) {
      setMetrics([]);
      setMetricsErr(e?.response?.data?.detail || e?.message || '指标加载失败');
    }
    try {
      const { data: d } = await metricsApi.listAllDimensions({ model_id: modelId, scope: 'model' });
      setDims(d?.items || []);
    } catch (e: any) {
      setDims([]);
      setDimsErr(e?.response?.data?.detail || e?.message || '维度加载失败');
    }
    try {
      // 术语: 按数据源的模型对象表集合前端过滤(size 上限 200)
      const { data } = await client.get('/admin/terms', { params: { page: 1, size: 200 } });
      const rows = data?.items || data || [];
      setTerms(Array.isArray(rows) ? rows.filter((t: BoundTerm) => t.target_table) : []);
    } catch { setTerms([]); }
  }, [modelId]);

  useEffect(() => { loadScoped(); }, [loadScoped]);

  const objKeys = useMemo(() => new Set(objects.map((o) => o.key.toLowerCase())), [objects]);
  const objMetrics = metrics.filter((m) => (m.bound_object_key || '').toLowerCase() === obj?.key.toLowerCase());
  const objDims = dims.filter((d) => (d.bound_object_key || '').toLowerCase() === obj?.key.toLowerCase());
  const objTerms = obj?.primary_table
    ? terms.filter((t) => (t.target_table || '').toLowerCase() === obj.primary_table!.toLowerCase())
    : [];
  const orphanLinks = (obj?.links || []).filter(
    (l) => l.target && !objKeys.has(String(l.target).toLowerCase()));

  const metricsByObj = useMemo(() => {
    const map = new Map<string, number>();
    metrics.forEach((m) => {
      const k = (m.bound_object_key || '').toLowerCase();
      map.set(k, (map.get(k) || 0) + 1);
    });
    return map;
  }, [metrics]);

  return (
    <div className="flex gap-4 min-h-[60vh]">
      {/* 对象清单 */}
      <div className="w-60 shrink-0 border-r pr-3">
        <div className="text-xs text-muted-foreground mb-2">对象（{objects.length}）· 徽标=模型内挂接数</div>
        <ScrollArea className="h-[62vh]">
          <div className="space-y-1.5 pr-2">
            {objects.map((o) => (
              <button
                key={o.key}
                onClick={() => setSelKey(o.key)}
                className={`w-full text-left rounded-lg border px-2.5 py-2 transition-colors
                  ${(obj?.key) === o.key ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/50'}`}
              >
                <div className="text-sm font-medium truncate">{o.display_name || o.key}</div>
                <div className="flex items-center gap-1.5 mt-1 text-[11px] text-muted-foreground">
                  <span className="font-mono">{o.key}</span>
                  <span className="ml-auto" />
                  <span title="模型内挂接指标数">◆{metricsByObj.get(o.key.toLowerCase()) || 0}</span>
                  {o.primary_table
                    ? <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-green-600 border-green-500/30">bound</Badge>
                    : <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-amber-500 border-amber-500/30">未绑表</Badge>}
                </div>
              </button>
            ))}
          </div>
        </ScrollArea>
      </div>

      {/* 对象中枢详情 */}
      {!obj ? (
        <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
          <Boxes className="h-8 w-8 mr-2 opacity-40" /> 该模型暂无对象
        </div>
      ) : (
        <div className="flex-1 min-w-0 space-y-4">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="text-base font-semibold">{obj.display_name || obj.key}</h3>
              <span className="font-mono text-xs text-muted-foreground">adh:obj:{obj.key}</span>
              {(obj.aliases || []).map((a) => <Badge key={a} variant="secondary" className="text-[11px]">{a}</Badge>)}
            </div>
            {obj.description && <p className="text-sm text-muted-foreground mt-1">{obj.description}</p>}
            {obj.execution_binding && (
              <div className="text-xs text-muted-foreground mt-1">
                绑定: {obj.execution_binding.physical_table || obj.primary_table || '-'}
                {obj.execution_binding.query_mode && <> · {obj.execution_binding.query_mode}</>}
                {obj.execution_binding.bind_kind === 'sql_template' && obj.execution_binding.template_ref &&
                  <> · SQL 模板 {obj.execution_binding.template_ref}</>}
              </div>
            )}
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
                        {!dims.some((d) => (d.target_column || '').toLowerCase() === (p.column || '').toLowerCase())
                          && <Badge variant="outline" className="text-[10px] px-1 h-4 text-muted-foreground">未建维度</Badge>}
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

            {/* 本对象指标 */}
            <div className="rounded-lg border">
              <div className="px-3 py-2 border-b text-sm font-medium flex items-center gap-2">
                <BarChart3 className="h-4 w-4 text-muted-foreground" /> 本对象指标（{objMetrics.length}）
                <Button variant="ghost" size="sm" className="ml-auto text-xs h-6" onClick={loadScoped}>
                  刷新
                </Button>
              </div>
              <ScrollArea className="h-56">
                <div className="p-1">
                  {objMetrics.map((m) => (
                    <div key={m.id} className="px-2 py-1.5 text-[13px] border-b last:border-0">
                      {m.name}{m.unit && <span className="text-xs text-muted-foreground"> · {m.unit}</span>}
                      {m.agg_type && <span className="text-xs text-muted-foreground font-mono ml-1">{m.agg_type}</span>}
                    </div>
                  ))}
                  {metricsErr && (
                    <div className="px-3 py-2 text-xs text-destructive">指标加载失败: {metricsErr}</div>)}
                  {!objMetrics.length && !metricsErr && (
                    <div className="text-xs text-muted-foreground p-3">
                      无挂接指标 — 去「指标」页签新建（归属对象将锁定为 {obj.key}）
                    </div>)}
                </div>
              </ScrollArea>
            </div>

            {/* 维度 + 术语 */}
            <div className="rounded-lg border">
              <div className="px-3 py-2 border-b text-sm font-medium flex items-center gap-2">
                <BookOpen className="h-4 w-4 text-muted-foreground" /> 维度（{objDims.length}）· 绑定术语（{objTerms.length}）
              </div>
              <ScrollArea className="h-56">
                <div className="p-1">
                  {objDims.map((d) => (
                    <div key={d.id} className="px-2 py-1.5 text-[13px] border-b last:border-0 flex items-center gap-2">
                      <span>{d.name}</span>
                      {d.category && <Badge variant="outline" className="text-[10px] px-1 h-4">{d.category}</Badge>}
                      {parseJson<Record<string, string>>(d.value_labels, {}) &&
                        Object.keys(parseJson<Record<string, string>>(d.value_labels, {})).length > 0 && (
                        <span className="ml-auto text-[11px] text-muted-foreground truncate">
                          枚举 {Object.keys(parseJson<Record<string, string>>(d.value_labels, {})).length} 值
                        </span>
                      )}
                    </div>
                  ))}
                  {objTerms.map((t) => (
                    <div key={`t${t.id}`} className="px-2 py-1.5 text-[13px] border-b last:border-0 flex items-center gap-2">
                      <span className="text-amber-500">❝</span>
                      <span>{t.term_cn}</span>
                      <Badge variant="outline" className="text-[10px] px-1 h-4 ml-auto">{t.term_type || '术语'}</Badge>
                    </div>
                  ))}
                  {!objDims.length && !dimsErr && !objTerms.length && (
                    <div className="text-xs text-muted-foreground p-3">暂无维度/术语绑定</div>)}
                  {dimsErr && (
                    <div className="px-2 py-1.5 text-xs text-destructive">维度加载失败: {dimsErr}</div>)}
                </div>
              </ScrollArea>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
