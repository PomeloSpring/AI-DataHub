/**
 * 关系图谱（语义层关系视图）— 看图 + 派生重建，不编辑 canonical doc。
 *
 * 定位：从「数据建模」工作区拆出的"看关系/看结构"入口（图谱 + 结构化视图），
 * 并承载两个**派生重建动作**（与图谱/知识库状态徽章同屏，做完立刻看到回显）：
 *   · 图谱生成：POST /api/graph/sync 按当前模型的域重建 RDF 命名图（graph:manage 把关）
 *   · 同步知识库：把生效模型的脱敏上云文档推送到目标知识库（模型须 active）
 * 两个入口原先分属「知识图谱」页与「数据建模」工具栏，与授权口径不一致（/api/graph/sync
 * 一度不在任何权限码的 api_pattern 内，非 admin 必 403）；现收拢到本页。
 *
 * 纪律：canonical doc 的唯一可编辑源仍在「数据建模」（ontology-modeling §1：图/结构化视图
 * 是派生投影）；本页只重建派生物，不写 doc、不改图内容本身。指标/维度口径编辑去「指标与维度」。
 */
import { useState, useEffect, useMemo, useCallback } from 'react';
import client from '@/api/client';
import { ontologyApi, type OntologyModelSummary } from '@/api/ontology';
import { hasPerm } from '@/stores/permissionStore';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';
import { Loader2, Play, Code2, Network, RefreshCw, UploadCloud } from 'lucide-react';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { yaml } from '@codemirror/lang-yaml';
import { markdown } from '@codemirror/lang-markdown';
import { ModelGraphTab, SyncBadges } from './ModelAssetTabs';

const KIND_LABEL: Record<string, string> = {
  source: '源本体', business: '业务本体', system: '系统本体',
};

/** 知识库同步跳过原因 → 可读文案（不向用户直接回英文码） */
const KB_SKIP_LABEL: Record<string, string> = {
  model_not_found: '模型不存在',
  no_kb_bound: '模型未绑定目标知识库',
  no_kb_with_sync_ontology: '没有开启同步的目标知识库',
  // 源本体不做 RAG 是后端归属裁决（sync_targets_for_model），不是配置遗漏
  source_model_semantic_only: '源本体只服务语义检索/取数，按设计不入知识库',
  status_draft: '草案未激活，不推送知识库',
  status_archived: '模型已归档，不推送知识库',
  superseded_by_active_version: '同名已有生效版本，本版本不上云',
  no_md_content: '无可渲染的语义内容',
};

/** SPARQL 结果单元格归一: 兼容 {value,type,datatype} 绑定对象与裸字符串。 */
function formatSparqlCell(v: any): string {
  if (v == null) return '';
  if (typeof v === 'object') return String(v.value ?? JSON.stringify(v));
  return String(v);
}

export default function OntologyGraphView() {
  const [models, setModels] = useState<OntologyModelSummary[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // 结构化只读视图：取 canonical doc（json/yaml/md_content）
  const [viewData, setViewData] = useState<{ json: string; yaml: string; md: string }>(
    { json: '', yaml: '', md: '' });
  const [viewLoading, setViewLoading] = useState(false);
  const [viewTick, setViewTick] = useState(0);

  // SPARQL 预览（静态 RDF/Turtle）
  const [sparqlPreview, setSparqlPreview] = useState('');
  const [sparqlLoading, setSparqlLoading] = useState(false);
  // SPARQL 查询控制台
  const [sparqlQuery, setSparqlQuery] = useState('');
  const [sparqlResult, setSparqlResult] = useState<any>(null);
  const [sparqlRunning, setSparqlRunning] = useState(false);

  // 派生重建动作（图谱生成 / 同步知识库）与状态徽章刷新
  const [graphTick, setGraphTick] = useState(0);
  const [building, setBuilding] = useState(false);
  const [syncingKb, setSyncingKb] = useState(false);
  const [syncTick, setSyncTick] = useState(0);

  const selected = useMemo(
    () => models.find((m) => m.id === selectedId) || null,
    [models, selectedId]);

  const loadModels = useCallback(async () => {
    setModelsLoading(true);
    try {
      const { data } = await ontologyApi.list(undefined, true);
      const items = data.items || [];
      setModels(items);
      // 默认选中：优先首个生效模型，回落首个（归档模型图仍可浏览）
      setSelectedId(() => {
        return null;
      });
    } catch {
      setModels([]);
    } finally {
      setModelsLoading(false);
    }
  }, []);

  useEffect(() => { loadModels(); }, [loadModels]);

  // 结构化只读视图拉取（取 canonical doc 内容）
  useEffect(() => {
    if (!selected?.id) { setViewData({ json: '', yaml: '', md: '' }); return; }
    let cancelled = false;
    setViewLoading(true);
    ontologyApi.get(selected.id)
      .then(({ data }) => {
        if (cancelled) return;
        setViewData({
          json: data?.json_content || '',
          yaml: data?.yaml_content || '',
          md: data?.md_content || '',
        });
      })
      .catch(() => { if (!cancelled) setViewData({ json: '', yaml: '', md: '' }); })
      .finally(() => { if (!cancelled) setViewLoading(false); });
    return () => { cancelled = true; };
  }, [selected?.id, viewTick]);

  // 切模型：重置命名图默认查询
  useEffect(() => {
    if (!selected) return;
    const ds = selected.datasource_id || 0;
    setSparqlQuery(
      `PREFIX adh: <http://ai-datahub.org/ontology/>\n` +
      `SELECT ?s ?p ?o WHERE { GRAPH <http://ai-datahub.org/ontology/ds:${ds}> { ?s ?p ?o } } LIMIT 50`);
    setSparqlResult(null);
    setSparqlPreview('');
  }, [selected?.id]);

  const handleRunSparql = async () => {
    if (!sparqlQuery.trim()) return;
    setSparqlRunning(true);
    setSparqlResult(null);
    try {
      const { data } = await client.post('/graph/query', { query: sparqlQuery });
      setSparqlResult(data);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'SPARQL 查询失败');
    } finally {
      setSparqlRunning(false);
    }
  };

  const handleLoadSparqlPreview = async () => {
    if (!selected) return;
    setSparqlLoading(true);
    try {
      const { data } = await client.get(`/ontology/${selected.id}/sparql-preview`);
      setSparqlPreview(data.turtle || data.sparql || '');
    } catch {
      setSparqlPreview('# 无法生成 RDF 预览，请确认模型已激活');
    } finally {
      setSparqlLoading(false);
    }
  };

  // ── 图谱生成：重建作用域与看图口径一致（系统本体→系统图、业务本体→聚合大图、源本体→本源图）──
  // kind 必须随请求上送：ds=0 且 kind 为空时服务端会退化成“把全部 active 模型塑进 ds:0”，
  // 把源/系统对象混进业务聚合图（污染徽章）。无 kind 的存量模型才交给服务端自己推断。
  const graphScope = useMemo(() => {
    if (!selected) return null;
    const kind = selected.kind || '';
    if (kind === 'system') return { datasource_id: 0, system_scope: true, kind };
    if (kind === 'business') return { datasource_id: 0, system_scope: false, kind };
    if (kind === 'source') return { datasource_id: selected.datasource_id || 0, system_scope: false, kind };
    return { datasource_id: selected.datasource_id || 0, system_scope: false, kind: '' };
  }, [selected]);

  const graphScopeLabel = useMemo(() => {
    if (!selected) return '';
    if (selected.kind === 'system') return '系统图 ds:-1';
    if (selected.kind === 'business') return '业务聚合图 ds:0';
    if (selected.kind === 'source') return `源本体图 ds:${selected.datasource_id || 0}`;
    return `命名图 ds:${selected.datasource_id || 0}`;
  }, [selected]);

  const handleBuildGraph = async () => {
    if (!graphScope) return;
    setBuilding(true);
    try {
      const { data } = await client.post('/graph/sync', null, { params: graphScope });
      if (data?.success === false) throw new Error(data?.message || '图谱生成未完成');
      toast.success(data?.message || '图谱已重建');
      // 图、结构化投影、同步状态徽章全部重取（重建后滞后值会变）
      setGraphTick((t) => t + 1);
      setViewTick((t) => t + 1);
      setSyncTick((t) => t + 1);
    } catch (e: any) {
      // 409=同图正在生成、503=互斥锁不可用：服务端原文案透传，不当成功也不静默重试
      toast.error(e?.response?.data?.detail || e?.message || '图谱生成失败，详见服务端日志');
    } finally {
      setBuilding(false);
    }
  };

  // ── 同步知识库：把生效模型的脱敏文档推送到目标知识库（仅 active，否则服务端会 skip）。
  //    targets 回的是**库名**，成败都要把它说给用户（不然只看到“同步了 1 个”不知道去哪了）。
  const handleSyncKb = async () => {
    if (!selected) return;
    setSyncingKb(true);
    try {
      const { data } = await ontologyApi.syncKb(selected.id);
      const names = (data.targets || []).join(' / ');
      if (data.removed) toast.success(`已从 ${data.removed} 个知识库下线旧文档${names ? `（${names}）` : ''}`);
      else if (data.synced && data.error) toast.warning(`已同步 ${data.synced} 个知识库${names ? `（${names}）` : ''}，但部分目标失败：${data.error}`);
      else if (data.synced) toast.success(`已同步到知识库：${names || data.synced}`);
      else if (data.error) toast.error(`知识库同步失败：${data.error}`);
      else toast.warning(data.skipped ? `未同步：${KB_SKIP_LABEL[data.skipped] || data.skipped}` : '本次未产生同步');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '知识库同步失败');
    } finally {
      setSyncingKb(false);
      setSyncTick((t) => t + 1);
    }
  };

  // SPARQL 示例查询（针对当前模型命名图）—— 与 RDF 投影词汇(ontology_to_rdf)同源
  const sparqlExamples = useMemo(() => {
    const ds = selected?.datasource_id || 0;
    const G = `<http://ai-datahub.org/ontology/ds:${ds}>`;
    const pre = 'PREFIX adh: <http://ai-datahub.org/ontology/>\n'
      + 'PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n'
      + 'PREFIX owl: <http://www.w3.org/2002/07/owl#>\n'
      + 'PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n';
    return [
      { label: '浏览全部三元组', desc: '查看本模型命名图内所有主-谓-宾，快速了解 RDF 结构',
        query: `${pre}SELECT ?s ?p ?o WHERE { GRAPH ${G} { ?s ?p ?o } } LIMIT 50` },
      { label: '列出业务对象', desc: '所有 owl:Class 对象及其中文名(rdfs:label)与主表(adh:primaryTable)',
        query: `${pre}SELECT ?obj ?name ?table WHERE { GRAPH ${G} { ?obj a owl:Class ; rdfs:label ?name . OPTIONAL { ?obj adh:primaryTable ?table } } } LIMIT 100` },
      { label: '对象关系（含 JOIN/基数）', desc: '实体化 adh:Link：来源→去向、关系名、基数与物理 JOIN 表达式',
        query: `${pre}SELECT ?from ?to ?type ?card ?join WHERE { GRAPH ${G} { ?l a adh:Link ; adh:linkFrom ?from ; adh:linkTo ?to ; adh:linkType ?type . OPTIONAL { ?l adh:cardinality ?card } OPTIONAL { ?l adh:joinExpr ?join } } } LIMIT 100` },
      { label: '关系多跳可达', desc: '用属性路径 adh:linkedTo+ 求对象间传递闭包（A 经若干步可达 B）',
        query: `${pre}SELECT DISTINCT ?a ?b WHERE { GRAPH ${G} { ?a adh:linkedTo ?x . ?x adh:linkedTo+ ?b } } LIMIT 200` },
      { label: '对象 → 指标 / 维度', desc: '字典权威经 bound_object_key 写入的归属边（adh:hasMetric / adh:hasDimension）',
        query: `${pre}SELECT ?obj ?metric ?dim WHERE { GRAPH ${G} { ?obj a owl:Class . OPTIONAL { ?obj adh:hasMetric ?metric } OPTIONAL { ?obj adh:hasDimension ?dim } } } LIMIT 200` },
      { label: '对象属性（数据类型）', desc: 'adh:hasProperty 展开的属性节点、中文名与 adh:dataType',
        query: `${pre}SELECT ?obj ?pname ?dtype WHERE { GRAPH ${G} { ?obj adh:hasProperty ?p . ?p rdfs:label ?pname . OPTIONAL { ?p adh:dataType ?dtype } } } LIMIT 200` },
      { label: '按业务名/别名检索对象', desc: '用中文名(rdfs:label)或别名(skos:altLabel)定位对象',
        query: `${pre}SELECT DISTINCT ?obj ?name WHERE { GRAPH ${G} { { ?obj rdfs:label ?name } UNION { ?obj skos:altLabel ?name } FILTER(REGEX(STR(?name), "订单", "i")) } } LIMIT 50` },
    ];
  }, [selected?.datasource_id]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Network className="h-5 w-5 text-primary" />
          <h1 className="text-lg font-semibold">关系图谱</h1>
        </div>
        <span className="text-xs text-muted-foreground">
          语义层关系视图（图谱 + 结构化投影）；图谱生成与同步知识库在本页，
          canonical 编辑请到「数据建模」，指标/维度口径请到「指标与维度」
        </span>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <span>模型</span>
              <Select
                value={selectedId != null ? String(selectedId) : ''}
                onValueChange={(v) => setSelectedId(Number(v))}
                disabled={modelsLoading || !models.length}>
                <SelectTrigger className="w-72 h-8 text-sm">
                  <SelectValue placeholder={modelsLoading ? '加载中…' : '选择模型'} />
                </SelectTrigger>
                <SelectContent>
                  {models.map((m) => (
                    <SelectItem key={m.id} value={String(m.id)}>
                      <span className="flex items-center gap-2">
                        <span className="truncate max-w-[16rem]">{m.name}</span>
                        {m.kind && KIND_LABEL[m.kind] && (
                          <Badge variant="outline" className="text-[10px] px-1 py-0">{KIND_LABEL[m.kind]}</Badge>
                        )}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {modelsLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
              {!modelsLoading && !models.length && (
                <span className="text-xs text-muted-foreground">暂无本体模型</span>
              )}
            </CardTitle>
            {selected && (
              <div className="flex items-center gap-2 flex-wrap">
                <SyncBadges modelId={selected.id} refreshKey={syncTick} kind={selected.kind} />
                {/* 重建命名图是治理动作，按 graph:manage 裁决（无权限不给必 403 的按钮） */}
                {hasPerm('graph:manage') && (
                  <Button size="sm" variant="outline" onClick={handleBuildGraph} disabled={building}
                          title={`从元数据重建${graphScopeLabel}（只合并该域的 active 模型对象），表/字段/术语/指标/关系一并刷新`}>
                    {building
                      ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                      : <RefreshCw className="h-3.5 w-3.5 mr-1" />}
                    图谱生成
                  </Button>
                )}
                <Button size="sm" variant="outline" onClick={handleSyncKb}
                        disabled={syncingKb || selected.status !== 'active'}
                        title={selected.status === 'active'
                          ? '手动把当前生效模型的脱敏文档同步到目标知识库（目标库见左侧「知识库 → …」徽章）'
                          : '仅生效模型可同步知识库'}>
                  {syncingKb
                    ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                    : <UploadCloud className="h-3.5 w-3.5 mr-1" />}
                  同步知识库
                </Button>
                <Button size="sm" variant="outline" onClick={() => setViewTick((t) => t + 1)}
                        title="重新拉取结构化投影（反映字典最新值）">
                  <RefreshCw className="h-3.5 w-3.5 mr-1" />刷新
                </Button>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {!selected ? (
            <div className="text-sm text-muted-foreground py-12 text-center">从上方选择一个模型以查看其图谱与结构化视图</div>
          ) : (
            <Tabs defaultValue="graph">
              <TabsList>
                <TabsTrigger value="graph">图谱</TabsTrigger>
                <TabsTrigger value="editor">结构化视图</TabsTrigger>
              </TabsList>
              <TabsContent value="graph" className="mt-2 h-[62vh] overflow-hidden">
                <ModelGraphTab
                  key={`graph-${graphTick}`}
                  datasourceId={selected.datasource_id}
                  kind={selected.kind}
                  domain={selected.domain}
                  singleView="ontology-overview" />
              </TabsContent>
              <TabsContent value="editor" className="mt-2">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs text-muted-foreground">canonical 文档只读预览（编辑请在「数据建模」的编辑器）</span>
                  {viewLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
                </div>
                <Tabs defaultValue="json">
                  <TabsList>
                    <TabsTrigger value="json">JSON</TabsTrigger>
                    <TabsTrigger value="yaml">YAML</TabsTrigger>
                    <TabsTrigger value="md">MD 预览</TabsTrigger>
                    <TabsTrigger value="sparql">SPARQL 查询</TabsTrigger>
                  </TabsList>
                  <TabsContent value="json" className="mt-2">
                    <CodeMirror
                      value={viewData.json}
                      height="60vh"
                      extensions={[json()]}
                      editable={false}
                      className="border border-border rounded-md text-xs overflow-hidden"
                    />
                    <div className="text-xs text-muted-foreground mt-1">
                      canonical JSON（唯一事实源）只读预览；编辑请在「数据建模」，指标/维度口径在「指标与维度」页维护。
                    </div>
                  </TabsContent>
                  <TabsContent value="yaml" className="mt-2">
                    <CodeMirror
                      value={viewData.yaml}
                      height="60vh"
                      extensions={[yaml()]}
                      editable={false}
                      className="border border-border rounded-md text-xs overflow-hidden"
                    />
                  </TabsContent>
                  <TabsContent value="md" className="mt-2">
                    <CodeMirror
                      value={viewData.md}
                      height="60vh"
                      extensions={[markdown()]}
                      editable={false}
                      className="border border-border rounded-md text-xs overflow-hidden"
                    />
                    <div className="text-xs text-muted-foreground mt-1">
                      内部 MD（含物理映射，不上云）；上云文档由本页「同步知识库」另行脱敏推送。
                    </div>
                  </TabsContent>
                  <TabsContent value="sparql" className="mt-2 space-y-3">
                    <div className="text-xs text-muted-foreground space-y-1.5">
                      <div>
                        对当前模型所在命名图（ds:{selected.datasource_id || 0}）执行 SPARQL；可编辑语句后点"执行查询"，或点下方示例载入。
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {sparqlExamples.map((ex) => (
                          <button key={ex.label} type="button" title={ex.desc}
                            onClick={() => setSparqlQuery(ex.query)}
                            className="rounded-md border border-border px-2 py-0.5 text-[11px] hover:bg-muted/60 transition-colors">
                            {ex.label}
                          </button>
                        ))}
                      </div>
                      <div className="text-[11px] text-muted-foreground/90 leading-relaxed">
                        示例说明：浏览全部三元组→概览命名图；列出业务对象→owl:Class 对象与中文名/主表；对象关系→实体化 adh:Link 的 JOIN/基数；关系多跳可达→adh:linkedTo+ 传递闭包；对象→指标/维度→字典权威归属边；对象属性→hasProperty 展开的数据类型；按业务名/别名检索→rdfs:label/skos:altLabel 定位对象。
                      </div>
                      <div className="text-[11px] text-muted-foreground/90 leading-relaxed">
                        词汇（与本体 RDF 投影同源）：对象节点 adh:obj:&#123;key&#125;（a owl:Class 兼 adh:Term，rdfs:label 中文名、skos:altLabel 别名、adh:primaryTable 主表）；对象间关系 adh:linkedTo / adh:mapsTo，及实体化 adh:Link（linkFrom/linkTo/linkType/cardinality/joinType/joinExpr）；属性 adh:hasProperty→adh:Property（dataType）；指标/维度归属 adh:hasMetric / adh:hasDimension（字典 bound_object_key 权威）；血缘 adh:boundTo→表；行为 adh:hasAction→adh:Action；规则 adh:Rule（governs/controls）；场景 adh:Scenario（usesObject/usesAction/usesRule）。命名图：源本体 ds:N、业务聚合图 ds:0、系统本体图 ds:-1。
                      </div>
                    </div>
                    <CodeMirror
                      value={sparqlQuery}
                      height="170px"
                      editable
                      onChange={(v) => setSparqlQuery(v)}
                      className="border border-border rounded-md text-xs overflow-hidden"
                    />
                    <div className="flex items-center gap-2">
                      <Button size="sm" onClick={handleRunSparql} disabled={sparqlRunning}>
                        {sparqlRunning ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Play className="h-3.5 w-3.5 mr-1" />}
                        执行查询
                      </Button>
                      <Button size="sm" variant="outline" onClick={handleLoadSparqlPreview} disabled={sparqlLoading}>
                        {sparqlLoading ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Code2 className="h-3.5 w-3.5 mr-1" />}
                        查看静态 RDF/Turtle
                      </Button>
                    </div>
                    {sparqlResult && (
                      <div className="border border-border rounded-md overflow-auto max-h-[38vh]">
                        {Array.isArray(sparqlResult.records) && sparqlResult.records.length ? (
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="bg-muted/50">
                                {Object.keys(sparqlResult.records[0]).map((k) => (
                                  <th key={k} className="text-left px-2 py-1 border-b font-medium">{k}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {sparqlResult.records.map((r: any, i: number) => (
                                <tr key={i} className="border-b last:border-0">
                                  {Object.keys(sparqlResult.records[0]).map((k) => (
                                    <td key={k} className="px-2 py-1 align-top whitespace-pre-wrap break-all">
                                      {formatSparqlCell(r?.[k])}
                                    </td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        ) : (
                          <pre className="p-3 text-xs overflow-auto">{JSON.stringify(sparqlResult, null, 2)}</pre>
                        )}
                      </div>
                    )}
                    {sparqlResult && (
                      <div className="text-xs text-muted-foreground">结果条数：{sparqlResult.count ?? 0}</div>
                    )}
                    {sparqlPreview && (
                      <CodeMirror
                        value={sparqlPreview}
                        height="30vh"
                        editable={false}
                        className="border border-border rounded-md text-xs overflow-hidden"
                      />
                    )}
                  </TabsContent>
                </Tabs>
              </TabsContent>
            </Tabs>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
