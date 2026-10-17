/**
 * ObjectHubTab — 模型工作区「对象 · Link」中枢页签。
 *
 * 职责边界（memory「对象Link页面展示规范」）：本页**仅展示属性与关系(Link)**。
 * 指标、维度、术语已迁至独立的「指标与维度」全局页，此处不再显示其面板/计数徽标，
 * 避免语义资产在本体页与专属页两处呈现造成归属错觉。
 */
import { useState, useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Layers, Link2, AlertTriangle, Boxes } from 'lucide-react';
import { normalizeDocObjects, type DocObjectIssue } from '@/lib/ontologyObjects';

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

export default function ObjectHubTab({ objects, issues = [] }: {
  modelId: number;
  objects: HubObject[];
  /** doc 里无法定位身份的元素（已由上层归一剔除），必须在页面上显式提示，不得静默当作“无对象” */
  issues?: DocObjectIssue[];
}) {
  const [selKey, setSelKey] = useState<string>('');
  // 上界保护：即便调用方未做归一，也不会因元素缺 key / 非数组而在渲染期崩溃
  const list = useMemo(
    () => normalizeDocObjects(objects).objects as HubObject[],
    [objects]);

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
        <div className="text-xs text-muted-foreground mb-2">对象（{list.length}）</div>
        <ScrollArea className="h-[62vh]">
          <div className="space-y-1.5 pr-2">
            {list.map((o) => (
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
                  {o.route?.source_ontology
                    ? <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-green-600 border-green-500/30"
                        title={`路由至 ${o.route.source_ontology}${o.route.object_key ? ' · ' + o.route.object_key : ''}`}>route</Badge>
                    : o.primary_table
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
            {obj.execution_binding && (
              <div className="text-xs text-muted-foreground mt-1">
                绑定: {obj.execution_binding.physical_table || obj.primary_table || '-'}
                {obj.execution_binding.query_mode && <> · {obj.execution_binding.query_mode}</>}
                {obj.execution_binding.bind_kind === 'dataset' && obj.execution_binding.dataset_ref &&
                  <> · 查询数据集 {obj.execution_binding.dataset_ref}</>}
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
