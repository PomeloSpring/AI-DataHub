import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import client from '@/api/client';
import {
  ontologyApi,
  generateOntologyDraft,
  type OntologyModel,
  type OntologyModelSummary,
  type OntologyStatus,
} from '@/api/ontology';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Switch } from '@/components/ui/switch';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from 'sonner';
import {
  Boxes, Sparkles, Save, CheckCircle2, Archive, RefreshCw, Loader2, Trash2, Code2, Play, History, AlertTriangle,
  Upload, Undo2,
} from 'lucide-react';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { yaml } from '@codemirror/lang-yaml';
import { markdown } from '@codemirror/lang-markdown';
import ObjectHubTab, { type HubObject } from './ObjectHubTab';
import AssetPoolPanel from './AssetPoolPanel';
import MetricsCenter from './MetricsCenter';
import DimensionsDictTab from './DimensionsDictTab';
import Glossary from './Glossary';
import { ModelGraphTab, ModelActionsTab, ModelRulesTab, ModelScenariosTab, ObjectCreateButton, ObjectTagsTab, SyncBadges } from './ModelAssetTabs';
import { metricsApi } from '@/api/metrics';

interface Datasource {
  id: number;
  name: string;
  db_type?: string;
}

const STATUS_META: Record<OntologyStatus, { label: string; cls: string }> = {
  draft: { label: '草案', cls: 'bg-amber-500/10 text-amber-500 border-amber-500/20' },
  active: { label: '激活', cls: 'bg-green-500/10 text-green-500 border-green-500/20' },
  archived: { label: '已归档', cls: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
};

/** 知识库同步跳过原因 → 可读文案（不向用户甩英文码） */
const KB_SKIP_LABEL: Record<string, string> = {
  model_not_found: '模型不存在',
  no_kb_bound: '模型未绑定目标知识库',
  no_kb_with_sync_ontology: '没有开启同步的目标知识库',
  status_draft: '草案未激活，不推送知识库',
  status_archived: '模型已归档，不推送知识库',
  superseded_by_active_version: '同名已有生效版本，本版本不上云',
  no_md_content: '无可渲染的语义内容',
};

/** 从 JSON 内容解析对象列表（预览用，容错） */
function parseObjects(jsonContent: string): any[] {
  try {
    const doc = JSON.parse(jsonContent);
    return Array.isArray(doc?.objects) ? doc.objects : [];
  } catch {
    return [];
  }
}

/** SPARQL 结果单元格归一: 兼容 {value,type,datatype} 绑定对象与裸字符串。 */
function formatSparqlCell(v: any): string {
  if (v == null) return '';
  if (typeof v === 'object') return String(v.value ?? JSON.stringify(v));
  return String(v);
}

/** 模型列表分组(业务/系统能力): 只列本体模型 —— 指标/维度/术语是模型内容, 在模型工作区内配置 */
function ModelGroup({ title, list, selectedId, onPick }: {
  title: string;
  list: OntologyModelSummary[];
  selectedId: number | null;
  onPick: (id: number) => void;
}) {
  if (!list.length) return null;
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1.5">
        {title}（{list.length}）
      </div>
      <div className="space-y-1">
        {list.map((m) => {
          const meta = STATUS_META[m.status] || STATUS_META.draft;
          return (
            <button
              key={m.id}
              onClick={() => onPick(m.id)}
              className={`w-full text-left rounded-lg border px-3 py-2 transition-colors
                ${selectedId === m.id ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/50'}`}
            >
              <div className="flex items-center justify-between gap-1">
                <span className="text-sm font-medium truncate">{m.name}</span>
                <Badge variant="outline" className={`flex-shrink-0 ${meta.cls}`}>
                  {meta.label}
                </Badge>
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {m.domain ? `${m.domain} · ` : ''}{m.object_count} 个对象 · {m.updated_at?.slice(0, 16).replace('T', ' ')}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function OntologyModeling() {
  // ── 数据源 ──
  const [datasources, setDatasources] = useState<Datasource[]>([]);
  const [datasourceId, setDatasourceId] = useState<number>(0);

  // ── 模型列表 ──
  const [models, setModels] = useState<OntologyModelSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [model, setModel] = useState<OntologyModel | null>(null);

  // ── 编辑器 ──
  const [jsonDraft, setJsonDraft] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  // ── 生成 SSE ──
  const [generating, setGenerating] = useState(false);
  const [progressLogs, setProgressLogs] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  // ── SPARQL 预览 ──
  const [sparqlPreview, setSparqlPreview] = useState('');
  const [sparqlLoading, setSparqlLoading] = useState(false);

  // ── SPARQL 查询控制台 ──
  const [sparqlQuery, setSparqlQuery] = useState('');
  const [sparqlResult, setSparqlResult] = useState<any>(null);
  const [sparqlRunning, setSparqlRunning] = useState(false);

  // ── 目标知识库绑定(业务本体同步去向) ──
  const [kbOptions, setKbOptions] = useState<{ id: number; name: string }[]>([]);
  const [savingKb, setSavingKb] = useState(false);

  // ── 弹窗 ──
  const [activateOpen, setActivateOpen] = useState(false);
  const [activating, setActivating] = useState(false);

  // ── 导入 YAML ──
  const [yamlOpen, setYamlOpen] = useState(false);
  const [yamlDir, setYamlDir] = useState('ontology');
  const [importing, setImporting] = useState(false);

  // ── 归档可见 / 手动同步知识库 ──
  const [showArchived, setShowArchived] = useState(false);
  const [syncingKb, setSyncingKb] = useState(false);
  const [syncTick, setSyncTick] = useState(0);

  const handleImportYaml = async () => {
    if (!datasourceId) {
      toast.warning('请先在右上角选择数据源');
      return;
    }
    setImporting(true);
    try {
      const { data } = await client.post('/catalog/ontology/import-yaml', {
        datasource_id: datasourceId, dir: yamlDir, rebuild_graph: true,
      });
      toast.success(`导入完成：${data.object_count ?? '?'} 对象 / ${data.links ?? '?'} Link，图谱已重建`);
      setYamlOpen(false);
      await loadModels(datasourceId);
      if (data.model_id) loadModel(data.model_id);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '导入失败');
    } finally {
      setImporting(false);
    }
  };

  // 写动作直执行：审批通道已退役，保存即生效（后端 ontology:save 权限码 fail-closed 把关），
  // 无直接写权限时保存会 403 并给出可解释原因，不再走「提交变更(AS-BOT)」审批单。

  // ── 版本管理（归档版本在此呈现，不入主列表） ──
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [versions, setVersions] = useState<OntologyModelSummary[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);

  // ── 未归属资产池(左栏独立入口, 不混入模型列表) ──
  const [poolMode, setPoolMode] = useState(false);
  const [poolTotal, setPoolTotal] = useState(0);
  const refreshPool = useCallback(async () => {
    try {
      const { data } = await metricsApi.assetPool();
      setPoolTotal(data?.counts?.total ?? 0);
    } catch {
      // 资产池不可用不影响建模主流程
    }
  }, []);

  const loadDatasources = useCallback(async () => {
    try {
      const { data } = await client.get('/datasources');
      setDatasources(Array.isArray(data) ? data : []);
    } catch {
      // ignore
    }
  }, []);

  const loadKbOptions = useCallback(async () => {
    try {
      const { data } = await client.get('/catalog/ontology/kb-options');
      setKbOptions(Array.isArray(data?.items) ? data.items : []);
    } catch {
      setKbOptions([]);
    }
  }, []);

  const loadModels = useCallback(async (dsId?: number, withArchived = showArchived) => {
    try {
      const { data } = await ontologyApi.list(dsId, withArchived);
      setModels(data.items || []);
    } catch {
      setModels([]);
    }
    refreshPool();
  }, [refreshPool, showArchived]);

  useEffect(() => {
    loadDatasources();
    loadModels();
    loadKbOptions();
  }, [loadDatasources, loadModels, loadKbOptions]);

  useEffect(() => {
    if (datasourceId) loadModels(datasourceId);
  }, [datasourceId, loadModels]);

  const loadModel = useCallback(async (id: number) => {
    try {
      const { data } = await ontologyApi.get(id);
      setModel(data);
      setSelectedId(id);
      setJsonDraft(data.json_content || '');
      setDirty(false);
    } catch {
      toast.error('加载模型失败');
    }
  }, []);

  // ── 生成草案（SSE） ──
  const handleGenerate = () => {
    if (!datasourceId) {
      toast.warning('请先选择数据源');
      return;
    }
    setGenerating(true);
    setProgressLogs([]);
    abortRef.current = generateOntologyDraft(datasourceId, (event, data) => {
      if (event === 'progress') {
        setProgressLogs((prev) => [...prev, data.detail || data.stage]);
      } else if (event === 'done') {
        setGenerating(false);
        toast.success(`草案已生成：${data.object_count} 个业务对象`);
        loadModels(datasourceId);
        if (data.model_id) loadModel(data.model_id);
      } else {
        setGenerating(false);
        toast.error(data.message || '生成失败');
      }
    });
  };

  // ── 保存（仅 draft） ──
  // 供建模页签（行为/规则/场景/新建对象）落库：改 doc 后走 ontologyApi.save（save_draft 级联重建派生物）
  const saveDocFromTab = useCallback(async (nextJson: string) => {
    if (!model) return;
    setJsonDraft(nextJson);
    try {
      const { data } = await ontologyApi.save(model.id, nextJson);
      const wasActive = model.status === 'active';
      setModel(data);
      setJsonDraft(data.json_content || '');
      setDirty(false);
      loadModels(datasourceId || undefined);
      toast.success(wasActive ? '已保存，并同步刷新对象/绑定/图谱' : '草案已保存，YAML/MD 已同步派生');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    }
  }, [model, datasourceId, loadModels]);

  const handleSave = async () => {
    if (!model) return;
    setSaving(true);
    try {
      const { data } = await ontologyApi.save(model.id, jsonDraft);
      const wasActive = model.status === 'active';
      setModel(data);
      setJsonDraft(data.json_content || '');
      setDirty(false);
      loadModels(datasourceId || undefined);
      toast.success(wasActive ? '已保存，并同步刷新对象/绑定/图谱' : '草案已保存，YAML/MD 已同步派生');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  // ── 激活 ──
  const handleActivate = async () => {
    if (!model) return;
    setActivating(true);
    try {
      await ontologyApi.activate(model.id);
      // 激活后写入 Oxigraph RDF
      try {
        await client.post(`/ontology/${model.id}/write-rdf`);
      } catch {
        // RDF 写入失败不阻塞激活
      }
      toast.success('模型已激活，RDF 已写入 Oxigraph，对象向量已重建');
      setActivateOpen(false);
      await loadModels(datasourceId || undefined);
      await loadModel(model.id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '激活失败');
    } finally {
      setActivating(false);
    }
  };

  // ── SPARQL 预览 ──
  const handleLoadSparqlPreview = async () => {
    if (!model) return;
    setSparqlLoading(true);
    try {
      const { data } = await client.get(`/ontology/${model.id}/sparql-preview`);
      setSparqlPreview(data.turtle || data.sparql || '');
    } catch {
      setSparqlPreview('# 无法生成 RDF 预览，请确认模型已激活');
    } finally {
      setSparqlLoading(false);
    }
  };

  // ── SPARQL 查询控制台(对当前模型命名图执行) ──
  useEffect(() => {
    if (!model) return;
    const ds = (model as any).datasource_id || 0;
    setSparqlQuery(
      `PREFIX adh: <http://ai-datahub.org/ontology/>\n` +
      `SELECT ?s ?p ?o WHERE { GRAPH <http://ai-datahub.org/ontology/ds:${ds}> { ?s ?p ?o } } LIMIT 50`);
    setSparqlResult(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [(model as any)?.id]);

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

  // ── 选定目标知识库(业务本体同步去向); 改绑后后台自动重推/下线旧库 ──
  const handleBindKb = async (kbId: string) => {
    if (!model) return;
    setSavingKb(true);
    try {
      const val = kbId && kbId !== 'none' ? Number(kbId) : null;
      const { data } = await client.put(`/catalog/ontology/models/${model.id}/kb`, { kb_id: val });
      setModel(data);
      toast.success(val ? '已绑定目标知识库，正在后台同步' : '已解绑知识库');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '绑定知识库失败');
    } finally {
      setSavingKb(false);
    }
  };

  // ── 归档 / 删除 ──
  const handleArchive = async () => {
    if (!model) return;
    try {
      await ontologyApi.archive(model.id);
      toast.success('模型已归档');
      await loadModels(datasourceId || undefined);
      await loadModel(model.id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '归档失败');
    }
  };

  // ── 还原归档模型为草案（归档数据可逆） ──
  const handleRestore = async () => {
    if (!model) return;
    try {
      const { data } = await ontologyApi.restore(model.id);
      setModel(data);
      setJsonDraft(data.json_content || '');
      setDirty(false);
      loadModels(datasourceId || undefined);
      toast.success('已还原为草案，可继续编辑后激活');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '还原失败');
    }
  };

  // ── 手动同步知识库：把生效模型的脱敏文档推送到目标知识库 ──
  const handleSyncKb = async () => {
    if (!model) return;
    setSyncingKb(true);
    try {
      const { data } = await ontologyApi.syncKb(model.id);
      if (data.removed) toast.success(`已从 ${data.removed} 个知识库下线旧文档`);
      else if (data.synced) toast.success(`已同步到 ${data.synced} 个知识库`);
      else toast.warning(data.skipped ? `未同步：${KB_SKIP_LABEL[data.skipped] || data.skipped}` : '本次未产生同步');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '知识库同步失败');
    } finally {
      setSyncingKb(false);
      setSyncTick((t) => t + 1);
    }
  };

  const handleDelete = async () => {
    if (!model) return;
    try {
      await ontologyApi.remove(model.id);
      toast.success('模型已删除');
      setModel(null);
      setSelectedId(null);
      setJsonDraft('');
      loadModels(datasourceId || undefined);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '删除失败');
    }
  };

  // ── 版本管理：拉取同一模型（数据源 + 名称）的全部版本（含归档） ──
  const fetchVersions = useCallback(async (id: number) => {
    setVersionsLoading(true);
    try {
      const { data } = await ontologyApi.versions(id);
      setVersions(data.items || []);
    } catch {
      toast.error('加载版本历史失败');
      setVersions([]);
    } finally {
      setVersionsLoading(false);
    }
  }, []);

  const handleOpenVersions = async () => {
    if (!model) return;
    setVersionsOpen(true);
    await fetchVersions(model.id);
  };

  // 回滚：激活某个历史/归档版本（服务端自动归档当前 active）
  const handleRestoreVersion = async (id: number) => {
    try {
      await ontologyApi.activate(id);
      try {
        await client.post(`/ontology/${id}/write-rdf`);
      } catch {
        // RDF 写入失败不阻塞激活
      }
      toast.success('已回滚并激活该版本');
      await loadModels(datasourceId || undefined);
      await loadModel(id);
      await fetchVersions(id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '激活失败');
    }
  };

  const objects = useMemo(() => parseObjects(model ? jsonDraft : ''), [model, jsonDraft]);
  const hubObjects = objects as HubObject[];
  const objectOptions = useMemo(
    () => hubObjects.map((o: any) => ({ key: o.key, display_name: o.display_name || o.key })),
    [objects]);
  // 模型分组按 kind（x4 归属改造）：本体归属是 domain+kind，不是数据源。
  // 业务本体的 datasource_id 也是 0，旧的“有合法数据源 → 业务”口径会把它们误归到系统组。
  const byKind = (k: string) => models.filter((m) => (m.kind || '') === k);
  const sourceModels = useMemo(() => byKind('source'), [models]);
  const bizModels = useMemo(() => byKind('business'), [models]);
  const sysModels = useMemo(() => byKind('system'), [models]);
  // 兼容无 kind 的存量模型：回落旧口径（有合法数据源 → 业务），不丢进任一组
  const legacyModels = useMemo(() =>
    models.filter((m) => !m.kind && !['source', 'business', 'system'].includes(m.kind || '')), [models]);
  const jsonInvalid = useMemo(() => {
    if (!dirty || !jsonDraft.trim()) return false;
    try {
      JSON.parse(jsonDraft);
      return false;
    } catch {
      return true;
    }
  }, [jsonDraft, dirty]);

  const editable = !!model && model.status !== 'archived';

  return (
    <div className="space-y-4">
      {/* 顶部工具栏 */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Boxes className="h-5 w-5 text-primary" />
          <h1 className="text-lg font-semibold">本体建模</h1>
        </div>
        <div className="flex-1" />
        <Select
          value={datasourceId ? String(datasourceId) : ''}
          onValueChange={(v) => setDatasourceId(Number(v))}
        >
          <SelectTrigger className="w-56">
            <SelectValue placeholder="选择数据源" />
          </SelectTrigger>
          <SelectContent>
            {datasources.map((ds) => (
              <SelectItem key={ds.id} value={String(ds.id)}>
                {ds.name}（{ds.db_type || '-'}）
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button onClick={handleGenerate} disabled={generating || !datasourceId}>
          {generating ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Sparkles className="h-4 w-4 mr-1" />}
          {generating ? '生成中…' : '生成本体模型'}
        </Button>
        <Button variant="outline" onClick={() => setYamlOpen(true)} disabled={!datasourceId}>
          <Upload className="h-4 w-4 mr-1" />导入 YAML
        </Button>
        <Button
          variant="outline"
          onClick={() => loadModels(datasourceId || undefined)}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>

      {/* 生成进度 */}
      {generating && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">生成进度</CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-24">
              <div className="space-y-1">
                {progressLogs.map((line, i) => (
                  <div key={i} className="text-xs text-muted-foreground">• {line}</div>
                ))}
                {progressLogs.length === 0 && (
                  <div className="text-xs text-muted-foreground">正在启动…</div>
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-4 gap-4">
        {/* 左栏：模型列表(只放本体模型, 分组隔离) + 未归属资产池入口 */}
        <Card className="xl:col-span-1">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-sm">模型列表</CardTitle>
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                <Switch checked={showArchived} onCheckedChange={(v) => {
                  setShowArchived(v);
                  loadModels(datasourceId || undefined, v);
                }} />
                显示归档
              </label>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <button
              onClick={() => setPoolMode((v) => !v)}
              className={`w-full text-left rounded-lg border px-3 py-2 text-sm transition-colors
                ${poolMode ? 'border-primary bg-primary/5' : 'border-dashed hover:bg-muted/50'}`}
            >
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-amber-500" />
                未归属资产池
                {poolTotal > 0 && (
                  <Badge variant="outline" className="ml-auto text-amber-500 border-amber-500/40">{poolTotal} 项</Badge>
                )}
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">待归属/悬空的建模资产 · 清空即覆盖度提升</div>
            </button>

            {models.length === 0 && (
              <div className="text-xs text-muted-foreground py-4 text-center">
                暂无模型，选择数据源后点击"生成本体模型"
              </div>
            )}
            <ModelGroup
              title="业务本体（按业务域）" list={bizModels}
              selectedId={poolMode ? null : selectedId}
              onPick={(id) => { setPoolMode(false); loadModel(id); }}
            />
            <ModelGroup
              title="源本体（按数据源）" list={sourceModels}
              selectedId={poolMode ? null : selectedId}
              onPick={(id) => { setPoolMode(false); loadModel(id); }}
            />
            <ModelGroup
              title="系统能力本体" list={sysModels}
              selectedId={poolMode ? null : selectedId}
              onPick={(id) => { setPoolMode(false); loadModel(id); }}
            />
            <ModelGroup
              title="未分类" list={legacyModels}
              selectedId={poolMode ? null : selectedId}
              onPick={(id) => { setPoolMode(false); loadModel(id); }}
            />
          </CardContent>
        </Card>

        {/* 右主区：模型工作区(对象中枢/指标/维度/编辑器/RDF) */}
        <Card className="xl:col-span-3">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <CardTitle className="text-sm">
                {poolMode ? '未归属资产池' : (model ? model.name : '工作区')}
                {!poolMode && dirty && editable && <span className="ml-2 text-xs text-amber-500">未保存</span>}
                {!poolMode && jsonInvalid && <span className="ml-2 text-xs text-red-500">JSON 格式错误</span>}
              </CardTitle>
              {model && (
                <div className="flex items-center gap-2">
                  {(model.datasource_id || 0) > 0 && (
                    <Select value={String(model.kb_id ?? 'none')} onValueChange={handleBindKb} disabled={savingKb}>
                      <SelectTrigger className="h-8 w-[180px]">
                        <SelectValue placeholder="选择目标知识库" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">未绑定知识库（不同步）</SelectItem>
                        {kbOptions.map((k) => (
                          <SelectItem key={k.id} value={String(k.id)}>{k.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                  <SyncBadges modelId={model.id} refreshKey={syncTick} />
                  <Button size="sm" variant="outline" onClick={handleSyncKb}
                          disabled={syncingKb || model.status !== 'active'}
                          title={model.status === 'active' ? '手动把当前生效模型的脱敏文档同步到目标知识库' : '仅生效模型可同步知识库'}>
                    {syncingKb
                      ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                      : <RefreshCw className="h-3.5 w-3.5 mr-1" />}
                    同步知识库
                  </Button>
                  <Button size="sm" variant="outline" onClick={handleOpenVersions}>
                    <History className="h-3.5 w-3.5 mr-1" />
                    版本管理
                  </Button>
                  {editable && (
                    <Button size="sm" onClick={handleSave} disabled={saving || jsonInvalid || !dirty}>
                      {saving ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Save className="h-3.5 w-3.5 mr-1" />}
                      {model.status === 'draft' ? '保存草案' : '保存'}
                    </Button>
                  )}
                  {(model.status === 'draft' || model.status === 'archived') && (
                    <Button size="sm" variant="default" onClick={() => setActivateOpen(true)}>
                      <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
                      确认激活
                    </Button>
                  )}
                  {model.status === 'archived' && (
                    <Button size="sm" variant="outline" onClick={handleRestore}>
                      <Undo2 className="h-3.5 w-3.5 mr-1" />
                      还原为草案
                    </Button>
                  )}
                  {model.status === 'active' && (
                    <Button size="sm" variant="outline" onClick={handleArchive}>
                      <Archive className="h-3.5 w-3.5 mr-1" />
                      归档
                    </Button>
                  )}
                  {model.status !== 'active' && (
                    <Button size="sm" variant="ghost" className="text-red-500 hover:text-red-600" onClick={handleDelete}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
              )}
            </div>
            {model && !poolMode && (
              <div className="text-xs text-muted-foreground">
                {model.status === 'active'
                  ? '激活模型可直接编辑；保存将依据真实表元数据重算绑定，并同步对象/图谱，与数据目录联动。'
                  : model.status === 'archived'
                    ? '已归档模型只读；可「还原为草案」继续编辑，或「确认激活」直接恢复为生效（归档数据始终可查看）。'
                    : '草案编辑后保存，确认无误后激活。'}
              </div>
            )}
          </CardHeader>
          <CardContent>
            {poolMode ? (
              <AssetPoolPanel modelId={selectedId || undefined} objects={objectOptions} />
            ) : !model ? (
              <div className="text-sm text-muted-foreground py-12 text-center">
                从左侧选择模型（业务/系统能力本体），或先生成本体草案
              </div>
            ) : (
              <Tabs key={model.id} defaultValue="hub">
                <TabsList>
                  <TabsTrigger value="hub">对象 · Link</TabsTrigger>
                  <TabsTrigger value="metrics">指标</TabsTrigger>
                  <TabsTrigger value="dimensions">维度</TabsTrigger>
                  <TabsTrigger value="terms">业务术语</TabsTrigger>
                  <TabsTrigger value="tags">标签</TabsTrigger>
                  <TabsTrigger value="m234">行为</TabsTrigger>
                  <TabsTrigger value="rules">规则</TabsTrigger>
                  <TabsTrigger value="scenarios">场景</TabsTrigger>
                  <TabsTrigger value="graph">图谱</TabsTrigger>
                  <TabsTrigger value="editor">编辑器</TabsTrigger>
                </TabsList>
                <TabsContent value="hub" className="mt-2 h-[62vh] overflow-hidden">
                  <div className="flex items-center gap-2 mb-2">
                    <ObjectCreateButton jsonContent={jsonDraft} onSave={saveDocFromTab} />
                  </div>
                  <div className="h-[calc(62vh-2.5rem)] overflow-auto">
                    <ObjectHubTab modelId={model.id} objects={hubObjects} />
                  </div>
                </TabsContent>
                <TabsContent value="metrics" className="mt-2 h-[62vh] overflow-auto">
                  <MetricsCenter embedded view="metrics" modelId={model.id} objects={objectOptions} />
                </TabsContent>
                <TabsContent value="dimensions" className="mt-2 h-[62vh] overflow-auto">
                  <DimensionsDictTab modelId={model.id} scope="model" />
                </TabsContent>
                <TabsContent value="terms" className="mt-2 h-[62vh] overflow-auto">
                  <Glossary embedded datasourceId={model.datasource_id}
                    tables={hubObjects.map((o: any) => String(o.primary_table || '').toLowerCase()).filter(Boolean)} />
                </TabsContent>
                <TabsContent value="tags" className="mt-2 h-[62vh] overflow-auto">
                  <ObjectTagsTab objects={hubObjects as any} />
                </TabsContent>
                <TabsContent value="m234" className="mt-2 h-[62vh] overflow-hidden">
                  <ModelActionsTab jsonContent={jsonDraft} onSave={saveDocFromTab} />
                </TabsContent>
                <TabsContent value="rules" className="mt-2 h-[62vh] overflow-hidden">
                  <ModelRulesTab jsonContent={jsonDraft} onSave={saveDocFromTab} />
                </TabsContent>
                <TabsContent value="scenarios" className="mt-2 h-[62vh] overflow-hidden">
                  <ModelScenariosTab jsonContent={jsonDraft} onSave={saveDocFromTab} />
                </TabsContent>
                <TabsContent value="graph" className="mt-2 h-[62vh] overflow-hidden">
                  {/* 系统模型(datasource_id 空/0)的图在专用系统图 ds:-1: 不带 system_scope
                      会落到 ds:0 聚合图(=全部数据源), 把业务表/test-alb 本体灌进来 */}
                  <ModelGraphTab datasourceId={model.datasource_id} kind={model.kind} domain={model.domain} />
                </TabsContent>
                <TabsContent value="editor" className="mt-2">
              <Tabs defaultValue="json">
                <TabsList>
                  <TabsTrigger value="json">JSON</TabsTrigger>
                  <TabsTrigger value="yaml">YAML</TabsTrigger>
                  <TabsTrigger value="md">MD 预览</TabsTrigger>
                  <TabsTrigger value="sparql">SPARQL 查询</TabsTrigger>
                </TabsList>
                <TabsContent value="json" className="mt-2">
                  <CodeMirror
                    value={jsonDraft}
                    height="60vh"
                    extensions={[json()]}
                    editable={editable}
                    onChange={(v) => {
                      setJsonDraft(v);
                      setDirty(true);
                    }}
                    className="border border-border rounded-md text-xs overflow-hidden"
                  />
                  <div className="text-xs text-muted-foreground mt-1">
                    JSON 为唯一事实源；保存后服务端自动重新派生 YAML 与 MD。
                  </div>
                </TabsContent>
                <TabsContent value="yaml" className="mt-2">
                  <CodeMirror
                    value={model.yaml_content || ''}
                    height="60vh"
                    extensions={[yaml()]}
                    editable={false}
                    className="border border-border rounded-md text-xs overflow-hidden"
                  />
                </TabsContent>
                <TabsContent value="md" className="mt-2">
                  <CodeMirror
                    value={model.md_content || ''}
                    height="60vh"
                    extensions={[markdown()]}
                    editable={false}
                    className="border border-border rounded-md text-xs overflow-hidden"
                  />
                  <div className="text-xs text-muted-foreground mt-1">
                    MD 按对象分节生成，激活时逐段向量化；直接修改 MD 不会回写结构。
                  </div>
                </TabsContent>
                <TabsContent value="sparql" className="mt-2 space-y-3">
                  <div className="text-xs text-muted-foreground">
                    对当前模型所在命名图（ds:{model.datasource_id || 0}）执行 SPARQL；可编辑语句后点“执行查询”。
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
                      extensions={[markdown()]}
                      editable={false}
                      className="border border-border rounded-md text-xs overflow-hidden"
                    />
                  )}
                  <div className="text-xs text-muted-foreground">
                    激活时本体模型自动转换为 RDF/Turtle 写入 Oxigraph；本页签可直接查询该图。
                  </div>
                </TabsContent>
              </Tabs>
                </TabsContent>
              </Tabs>
            )}
          </CardContent>
        </Card>
      </div>

      {/* 导入 YAML 弹窗 */}
      <Dialog open={yamlOpen} onOpenChange={setYamlOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>导入 Palantir Ontology YAML</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <div>
              <label className="text-sm font-medium block mb-1">服务端 YAML 目录</label>
              <Input value={yamlDir} onChange={(e) => setYamlDir(e.target.value)}
                placeholder="如: ontology" />
              <p className="text-xs text-muted-foreground mt-1">
                目标数据源：{datasources.find((d) => d.id === datasourceId)?.name || '-'}；
                导入将合并全部域为单个全量本体并立即激活、重建图谱。
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setYamlOpen(false)}>取消</Button>
            <Button onClick={handleImportYaml} disabled={importing}>
              {importing ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Upload className="h-4 w-4 mr-1" />}
              {importing ? '导入中…' : '开始导入'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 激活确认弹窗 */}
      <Dialog open={activateOpen} onOpenChange={setActivateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认激活本体模型？</DialogTitle>
          </DialogHeader>
          <div className="text-sm text-muted-foreground space-y-2">
            <p>激活将执行以下操作：</p>
            <ul className="list-disc pl-5 space-y-1">
              <li>该数据源原 active 模型将被归档</li>
              <li>本体模型转换为 RDF/Turtle 写入 Oxigraph</li>
              <li>逐对象 MD 段重新向量化写入向量库（{objects.length} 个对象）</li>
              <li>ontology_first 检索策略将使用新模型</li>
            </ul>
            <p>激活前请确认 JSON 内容已检查无误。</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setActivateOpen(false)} disabled={activating}>
              取消
            </Button>
            <Button onClick={handleActivate} disabled={activating}>
              {activating && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
              确认激活
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 版本管理弹窗（同一模型的归档/历史版本在此呈现） */}
      <Dialog open={versionsOpen} onOpenChange={setVersionsOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>版本管理 · {model?.name}</DialogTitle>
          </DialogHeader>
          <div className="text-xs text-muted-foreground">
            同一模型（数据源 + 名称）的全部版本；当前激活版本置顶，已归档的历史版本在此呈现，不占用主列表。
          </div>
          <ScrollArea className="h-[50vh] mt-2">
            {versionsLoading ? (
              <div className="py-6 text-center text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 mr-1 inline animate-spin" />加载中…
              </div>
            ) : versions.length === 0 ? (
              <div className="py-6 text-center text-sm text-muted-foreground">暂无版本记录</div>
            ) : (
              <div className="space-y-2 pr-2">
                {versions.map((v) => {
                  const meta = STATUS_META[v.status] || STATUS_META.draft;
                  return (
                    <div
                      key={v.id}
                      className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2"
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium truncate">{v.name}</span>
                          <Badge variant="outline" className={meta.cls}>{meta.label}</Badge>
                        </div>
                        <div className="text-xs text-muted-foreground mt-0.5">
                          {v.object_count} 个对象 · {v.created_by || '—'} · 更新于 {v.updated_at?.slice(0, 16).replace('T', ' ')}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => { loadModel(v.id); setVersionsOpen(false); }}
                        >
                          查看
                        </Button>
                        {v.status !== 'active' && (
                          <Button size="sm" variant="outline" onClick={() => handleRestoreVersion(v.id)}>
                            激活
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </ScrollArea>
          <DialogFooter>
            <Button variant="outline" onClick={() => setVersionsOpen(false)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
