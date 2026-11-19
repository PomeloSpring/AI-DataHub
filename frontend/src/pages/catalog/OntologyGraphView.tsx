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
import { toast } from '@/lib/toast';
import { Loader2, Play, Code2, Network, RefreshCw, UploadCloud } from 'lucide-react';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { yaml } from '@codemirror/lang-yaml';
import { markdown } from '@codemirror/lang-markdown';
import { ModelGraphTab, SyncBadges } from './ModelAssetTabs';
import { useTranslation } from 'react-i18next';
import { t as i18nTr } from '@/i18n/runtime';

const KIND_LABEL: Record<string, string> = {
  source: i18nTr('k_d848264863'), business: i18nTr('k_dbe8a8559c'), system: i18nTr('k_b41dfa6f3b'),
};

/** 知识库同步跳过原因 → 可读文案（不向用户直接回英文码） */
const KB_SKIP_LABEL: Record<string, string> = {
  model_not_found: i18nTr('k_88ef9e602a'),
  no_kb_bound: i18nTr('k_4899f0779a'),
  no_kb_with_sync_ontology: i18nTr('k_3071c5e8db'),
  // 手动选库但目标库已被删/不可用（不静默换库，显式回错）
  specified_kb_unavailable: i18nTr('k_4d1ff7968b'),
  status_draft: i18nTr('k_343a6fc70c'),
  status_archived: i18nTr('k_e2abed3ee1'),
  superseded_by_active_version: i18nTr('k_1e43830379'),
  no_md_content: i18nTr('k_9b2a743eaf'),
};

/** SPARQL 结果单元格归一: 兼容 {value,type,datatype} 绑定对象与裸字符串。 */
function formatSparqlCell(v: any): string {
  if (v == null) return '';
  if (typeof v === 'object') return String(v.value ?? JSON.stringify(v));
  return String(v);
}

// 命名图按 kind 分图（写入侧 graph_target_for_model 同口径）：system→ds:-1、business→ds:0（跨源聚合图）。
// 源本体已退役并入 business；具体数据源的 ds:N 只留物理血缘（无本体）。
// 严禁拿模型的 datasource_id 当图哨兵——业务模型 datasource_id 是真实雪花 id，ds:<那个 id> 根本不存在，
// 查询必然空（历史缺陷：SPARQL 示例按 datasource_id 拼 IRI，全部返回 0 行）。读写分叉就是缺陷本身。
function graphDsForModel(kind: string | undefined, datasourceId: number | undefined): number {
  const ds = Number(datasourceId || 0);
  if (kind === 'system') return -1;
  if (kind === 'business') return 0;
  return ds === -1 ? -1 : ds;
}

export default function OntologyGraphView() {
  const { t } = useTranslation('ui');
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

  // 「同步知识库」目标库选择：'0'=按模型 kind 默认路由；选中某库=一次性定向推送到该库（含源本体手动例外）
  const [kbOptions, setKbOptions] = useState<{ id: number; name: string }[]>([]);
  const [selectedKbId, setSelectedKbId] = useState<string>('0');

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

  // 拉取「同步知识库」可选目标库清单（全部 active qmind 库，供选库下拉）
  useEffect(() => {
    ontologyApi.kbSyncOptions()
      .then(({ data }) => setKbOptions(data.items || []))
      .catch(() => setKbOptions([]));
  }, []);

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

  // 切模型：重置命名图默认查询与同步目标库选择（回到默认路由）
  useEffect(() => {
    if (!selected) return;
    setSelectedKbId('0');
    const G = `<http://ai-datahub.org/ontology/ds:${graphDsForModel(selected.kind, selected.datasource_id)}>`;
    setSparqlQuery(
      `PREFIX adh: <http://ai-datahub.org/ontology/>\n` +
      `SELECT ?s ?p ?o WHERE { GRAPH ${G} { ?s ?p ?o } } LIMIT 50`);
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
      toast.error(e?.response?.data?.detail || t('k_9b2b07bcae'));
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
      setSparqlPreview(t('k_53c350c399'));
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
    if (selected.kind === 'system') return t('k_d1eb9b5b59');
    if (selected.kind === 'business') return t('k_bbee149234');
    if (selected.kind === 'source') return t('k_6721362c7e', { p0: selected.datasource_id || 0 });
    return t('k_d912de7356', { p0: selected.datasource_id || 0 });
  }, [selected]);

  const handleBuildGraph = async () => {
    if (!graphScope) return;
    setBuilding(true);
    try {
      const { data } = await client.post('/graph/sync', null, { params: graphScope });
      if (data?.success === false) throw new Error(data?.message || t('k_46c6521935'));
      toast.success(data?.message || t('k_a00040e6ac'));
      // 图、结构化投影、同步状态徽章全部重取（重建后滞后值会变）
      setGraphTick((t) => t + 1);
      setViewTick((t) => t + 1);
      setSyncTick((t) => t + 1);
    } catch (e: any) {
      // 409=同图正在生成、503=互斥锁不可用：服务端原文案透传，不当成功也不静默重试
      toast.error(e?.response?.data?.detail || e?.message || t('k_75c414fe36'));
    } finally {
      setBuilding(false);
    }
  };

  // ── 同步知识库：把生效模型的脱敏文档推送到目标知识库（仅 active，否则服务端会 skip）。
  //    targets 回的是**库名**，成败都要把它说给用户（不然只看到“同步了 1 个”不知道去哪了）。
  //    selectedKbId 非 '0' 时一次性定向推送到所选库（含源本体手动例外），'0' 走默认路由。
  const syncTargetKbName = useMemo(
    () => (selectedKbId !== '0' ? kbOptions.find((k) => String(k.id) === selectedKbId)?.name || '' : ''),
    [selectedKbId, kbOptions]);

  const handleSyncKb = async () => {
    if (!selected) return;
    setSyncingKb(true);
    try {
      const { data } = await ontologyApi.syncKb(
        selected.id, selectedKbId !== '0' ? Number(selectedKbId) : null);
      const names = (data.targets || []).join(' / ');
      if (data.removed) toast.success(t('k_3d2270d690', { p0: data.removed, p1: names ? `（${names}）` : '' }));
      else if (data.synced && data.error) toast.warning(t('k_730e1fa099', { p0: data.synced, p1: names ? `（${names}）` : '', p2: data.error }));
      else if (data.synced) toast.success(t('k_55326b89f6', { p0: names || data.synced }));
      else if (data.error) toast.error(t('k_c7afee6143', { p0: data.error }));
      else toast.warning(data.skipped ? t('k_0b8625abe0', { p0: KB_SKIP_LABEL[data.skipped] || data.skipped }) : t('k_0844deea63'));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_feb50f1fda'));
    } finally {
      setSyncingKb(false);
      setSyncTick((t) => t + 1);
    }
  };

  // SPARQL 示例查询（针对当前模型命名图）—— 与 RDF 投影词汇(ontology_to_rdf)同源
  const sparqlExamples = useMemo(() => {
    const G = `<http://ai-datahub.org/ontology/ds:${graphDsForModel(selected?.kind, selected?.datasource_id)}>`;
    const pre = 'PREFIX adh: <http://ai-datahub.org/ontology/>\n'
      + 'PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n'
      + 'PREFIX owl: <http://www.w3.org/2002/07/owl#>\n'
      + 'PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n';
    return [
      { label: t('k_ec2bfeda49'), desc: t('k_1f55171967'),
        query: `${pre}SELECT ?s ?p ?o WHERE { GRAPH ${G} { ?s ?p ?o } } LIMIT 50` },
      { label: t('k_aeba1b9334'), desc: t('k_8760c429ce'),
        query: `${pre}SELECT ?obj ?name ?table WHERE { GRAPH ${G} { ?obj a owl:Class ; rdfs:label ?name . OPTIONAL { ?obj adh:primaryTable ?table } } } LIMIT 100` },
      { label: t('k_8355e02aff'), desc: t('k_1f7a032ae7'),
        query: `${pre}SELECT ?from ?to ?type ?card ?join WHERE { GRAPH ${G} { ?l a adh:Link ; adh:linkFrom ?from ; adh:linkTo ?to ; adh:linkType ?type . OPTIONAL { ?l adh:cardinality ?card } OPTIONAL { ?l adh:joinExpr ?join } } } LIMIT 100` },
      { label: t('k_148456a1b3'), desc: t('k_003786a6fe'),
        query: `${pre}SELECT DISTINCT ?a ?b WHERE { GRAPH ${G} { ?a adh:linkedTo ?x . ?x adh:linkedTo+ ?b } } LIMIT 200` },
      { label: t('k_cc6dbedda5'), desc: t('k_09eeb3ee14'),
        query: `${pre}SELECT ?obj ?metric ?dim WHERE { GRAPH ${G} { ?obj a owl:Class . OPTIONAL { ?obj adh:hasMetric ?metric } OPTIONAL { ?obj adh:hasDimension ?dim } } } LIMIT 200` },
      { label: t('k_b27d1b149c'), desc: t('k_ff152f8f0e'),
        query: `${pre}SELECT ?obj ?pname ?dtype WHERE { GRAPH ${G} { ?obj adh:hasProperty ?p . ?p rdfs:label ?pname . OPTIONAL { ?p adh:dataType ?dtype } } } LIMIT 200` },
      { label: t('k_01327272b1'), desc: t('k_f4ff838b4f'),
        query: `${pre}SELECT DISTINCT ?obj ?name WHERE { GRAPH ${G} { { ?obj rdfs:label ?name } UNION { ?obj skos:altLabel ?name } FILTER(REGEX(STR(?name), "订单", "i")) } } LIMIT 50` },
    ];
  }, [selected?.kind, selected?.datasource_id]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Network className="h-5 w-5 text-primary" />
          <h1 className="text-lg font-semibold">{t("k_2ea6798094")}</h1>
        </div>
        <span className="text-xs text-muted-foreground">
          {t('k_3708a746a9')}
        </span>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <span>{t("k_98fd0cbd9c")}</span>
              <Select
                value={selectedId != null ? String(selectedId) : ''}
                onValueChange={(v) => setSelectedId(Number(v))}
                disabled={modelsLoading || !models.length}>
                <SelectTrigger className="w-72 h-8 text-sm">
                  <SelectValue placeholder={modelsLoading ? t('k_300ee3dee4') : t('k_f2d3731b92')} />
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
                <span className="text-xs text-muted-foreground">{t("k_158954fcfe")}</span>
              )}
            </CardTitle>
            {selected && (
              <div className="flex items-center gap-2 flex-wrap">
                <SyncBadges modelId={selected.id} refreshKey={syncTick} kind={selected.kind} />
                {/* 重建命名图是治理动作，按 graph:manage 裁决（无权限不给必 403 的按钮） */}
                {hasPerm('graph:manage') && (
                  <Button size="sm" variant="outline" onClick={handleBuildGraph} disabled={building}
                          title={t('k_6633708f34', { p0: graphScopeLabel })}>
                    {building
                      ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                      : <RefreshCw className="h-3.5 w-3.5 mr-1" />}
                    {t('k_800021e226')}
                  </Button>
                )}
                {/* 同步目标库选择：默认按模型 kind 路由（业务/系统→全局库、源本体不同步）；
                    选具体库=一次性定向推送到该库（含源本体，用户主动的手动例外） */}
                <Select value={selectedKbId} onValueChange={(v) => setSelectedKbId(v)}
                        disabled={syncingKb}>
                  <SelectTrigger className="w-44 h-8 text-xs" title={t("k_343e6fa5bb")}>
                    <SelectValue placeholder={t("k_85bab14432")} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="0">{t("k_85bab14432")}</SelectItem>
                    {kbOptions.map((kb) => (
                      <SelectItem key={kb.id} value={String(kb.id)}>
                        <span className="truncate max-w-[12rem]">{kb.name}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button size="sm" variant="outline" onClick={handleSyncKb}
                        disabled={syncingKb || selected.status !== 'active'}
                        title={selected.status !== 'active'
                          ? t('k_e0c202beb0')
                          : (selectedKbId !== '0'
                              ? t('k_bb6f402308', { p0: syncTargetKbName || selectedKbId })
                              : t('k_1c44d22877'))}>
                  {syncingKb
                    ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                    : <UploadCloud className="h-3.5 w-3.5 mr-1" />}
                  {t('k_010f64ab9b')}
                </Button>
                <Button size="sm" variant="outline" onClick={() => setViewTick((t) => t + 1)}
                        title={t("k_fb4e24d4ae")}>
                  <RefreshCw className="h-3.5 w-3.5 mr-1" />{t('k_38108eaa1d')}
                </Button>
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {!selected ? (
            <div className="text-sm text-muted-foreground py-12 text-center">{t("k_f44e675fce")}</div>
          ) : (
            <Tabs defaultValue="graph">
              <TabsList>
                <TabsTrigger value="graph">{t("k_34778e2933")}</TabsTrigger>
                <TabsTrigger value="editor">{t("k_66cdd98e11")}</TabsTrigger>
              </TabsList>
              <TabsContent value="graph" className="mt-2 h-[62vh] overflow-hidden">
                <ModelGraphTab
                  key={`graph-${graphTick}`}
                  datasourceId={selected.datasource_id}
                  kind={selected.kind}
                  domain={selected.domain}
                  modelId={selected.id}
                  singleView="ontology-overview" />
              </TabsContent>
              <TabsContent value="editor" className="mt-2">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs text-muted-foreground">{t("k_c6491797c5")}</span>
                  {viewLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
                </div>
                <Tabs defaultValue="json">
                  <TabsList>
                    <TabsTrigger value="json">JSON</TabsTrigger>
                    <TabsTrigger value="yaml">YAML</TabsTrigger>
                    <TabsTrigger value="md">{t("k_6af87bfe5d")}</TabsTrigger>
                    <TabsTrigger value="sparql">{t("k_5e42489108")}</TabsTrigger>
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
                      {t('k_103aa5a325')}
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
                      {t('k_fbb6198619')}
                    </div>
                  </TabsContent>
                  <TabsContent value="sparql" className="mt-2 space-y-3">
                    <div className="text-xs text-muted-foreground space-y-1.5">
                      <div>
                        {t('k_30fd7d23e6', { p0: graphDsForModel(selected.kind, selected.datasource_id) })}</div>
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
                        {t('k_a1e75f11c1')}
                      </div>
                      <div className="text-[11px] text-muted-foreground/90 leading-relaxed">
                        {t('k_4fdcc7597d')}
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
                        {t('k_4fb36a6129')}
                      </Button>
                      <Button size="sm" variant="outline" onClick={handleLoadSparqlPreview} disabled={sparqlLoading}>
                        {sparqlLoading ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Code2 className="h-3.5 w-3.5 mr-1" />}
                        {t('k_f509520f3c')}
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
                      <div className="text-xs text-muted-foreground">{t('k_bfe63997e6', { p0: sparqlResult.count ?? 0 })}</div>
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
