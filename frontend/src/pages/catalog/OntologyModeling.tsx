import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import client from '@/api/client';
import {
  ontologyApi,
  startOntologyGenerate,
  getOntologyGenerateTask,
  listOntologyGenerateTasks,
  cancelOntologyGenerateTask,
  type GenerateTask,
  type OntologyModel,
  type OntologyModelSummary,
  type OntologyStatus,
  type DraftStatus,
  type DraftObjectDiff,
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
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from '@/lib/toast';
import {
  Boxes, Sparkles, CheckCircle2, Archive, RefreshCw, Loader2, Trash2, History, AlertTriangle,
  Upload, Undo2, BookMarked, GitCompare, Inbox, Eye, Search, Pencil,
} from 'lucide-react';
import ObjectHubTab, { type HubObject } from './ObjectHubTab';
import { normalizeDocObjects, showsM234, showsSiteScope, countM234Declarations, type NormalizeDocObjectsResult } from '@/lib/ontologyObjects';
import ModelDraftReview from './ModelDraftReview';
import Glossary from './Glossary';
import { ModelActionsTab, ModelRulesTab, ModelScenariosTab, ModelSitesTab, ObjectCreateButton, ObjectTagsTab, SyncBadges } from './ModelAssetTabs';
import { metricsApi } from '@/api/metrics';
import { useAsBotStore } from '@/stores/asBotStore';
import { hasPerm } from '@/stores/permissionStore';

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

/** 从 JSON 内容解析对象列表（预览用，容错）：旧字段名在本层适配，无法定位身份的元素交 UI 显式提示 */
function parseObjects(jsonContent: string): NormalizeDocObjectsResult {
  let raw: unknown = [];
  try {
    const doc = JSON.parse(jsonContent);
    raw = Array.isArray(doc?.objects) ? doc.objects : [];
  } catch {
    raw = [];
  }
  return normalizeDocObjects(raw);
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
                <span className="flex items-center gap-1 flex-shrink-0">
                  {(m.deployment_count ?? 0) >= 2 && (
                    <Badge variant="outline" className="border-indigo-300 text-indigo-600">
                      契约模型 · {m.deployment_count} 站部署
                    </Badge>
                  )}
                  <Badge variant="outline" className={`flex-shrink-0 ${meta.cls}`}>
                    {meta.label}
                  </Badge>
                  {/* 有未发布草案：在列表层就提醒“改了还没生效”，不让人到详情页才发现对象不见了 */}
                  {m.has_draft && (
                    <Badge variant="outline" className="flex-shrink-0 border-amber-500/40 text-amber-600">
                      待发布
                    </Badge>
                  )}
                </span>
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
  const navigate = useNavigate();

  // ── 模型列表 ──
  const [models, setModels] = useState<OntologyModelSummary[]>([]);
  const [modelKw, setModelKw] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  // 选中模型的同步 ref：异步回包时用它判断“当前还看着这个模型吗”（state 在回调里会闭包过期）
  const selectedIdRef = useRef<number | null>(null);
  const [model, setModel] = useState<OntologyModel | null>(null);

  // ── 编辑器（生效版正文；草案正文另存 draftJson）──
  const [jsonDraft, setJsonDraft] = useState('');
  const [saving, setSaving] = useState(false);

  // ── 生成任务：服务端后台静默执行，进度在 AS-BOT 侧边栏呈现（页面不再是执行载体）──
  const [genTask, setGenTask] = useState<GenerateTask | null>(null);
  // 辅助生成的知识库（可选）：从清单选名，不手输 id
  const [knowledgeBases, setKnowledgeBases] = useState<{ id: number; name: string }[]>([]);
  const [kbIds, setKbIds] = useState<number[]>([]);

  // ── 弹窗 ──
  const [activateOpen, setActivateOpen] = useState(false);
  const [activating, setActivating] = useState(false);

  // ── 导入 YAML ──
  const [yamlOpen, setYamlOpen] = useState(false);
  const [yamlDir, setYamlDir] = useState('ontology');
  const [importing, setImporting] = useState(false);

  // ── 归档可见（手动同步知识库已移到「关系图谱」页，与图谱/知识库状态同屏）──
  const [showArchived, setShowArchived] = useState(false);

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
      await loadModels();
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

  // ── 未归属资产池：已抽为独立页（/data/asset-pool），本页只留计数与入口 ──
  const [poolTotal, setPoolTotal] = useState(0);
  const refreshPool = useCallback(async () => {
    try {
      const { data } = await metricsApi.assetPool();
      setPoolTotal(data?.counts?.total ?? 0);
    } catch {
      // 资产池不可用不影响建模主流程（入口仍在，只是不显示计数）
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

  // 知识库候选：只列启用中的，选项显示 name（ui-resource-display）
  const loadKnowledgeBases = useCallback(async () => {
    try {
      const { data } = await client.get('/knowledge-bases');
      setKnowledgeBases((Array.isArray(data) ? data : [])
        .filter((k: any) => k.status === 'active')
        .map((k: any) => ({ id: k.id, name: k.name })));
    } catch {
      setKnowledgeBases([]);
    }
  }, []);

  // 模型列表**不按数据源过滤**：右上角数据源只回答“给哪个源生成”，不是筛选器
  const loadModels = useCallback(async (withArchived = showArchived) => {
    try {
      const { data } = await ontologyApi.list(undefined, withArchived);
      setModels(data.items || []);
    } catch {
      setModels([]);
    }
    refreshPool();
  }, [refreshPool, showArchived]);

  useEffect(() => {
    loadDatasources();
    loadKnowledgeBases();
    loadModels();
  }, [loadDatasources, loadKnowledgeBases, loadModels]);

  // 重进页面挂回正在后台跑的生成任务（执行在服务端，不因页面离开而停）
  useEffect(() => {
    let cancelled = false;
    listOntologyGenerateTasks(true)
      .then((items) => { if (!cancelled && items.length) setGenTask(items[0]); })
      .catch(() => { /* 列表拉不到不影响页面，任务仍在服务端跑 */ });
    return () => { cancelled = true; };
  }, []);

  // 任务进度的唯一事实源在服务端会话转录里；页面只关心“有没有在跑”与终态提醒
  const running = genTask?.status === 'running';

  // ── 草案（两段式）：状态与正文都以服务端为事实源 ──
  // 历史缺陷：draftPending 是个只由“本页点了存草案”置位的本地 state，从不调 GET /draft。
  // AS-BOT 在服务端写入草案时，本页既看不到新对象（列表读生效版）、又把发布按成
  // 「无待发布」的死开关。现改为：选中模型/存草案/发布后一律回读服务端。
  const [draftStatus, setDraftStatus] = useState<DraftStatus | null>(null);
  /** 草案正文（只在切到草案视图时拉；编辑基线必须是草案，否则会顶掉 AS-BOT/他人已写的草案） */
  const [draftJson, setDraftJson] = useState('');
  /** 工作区当前展示哪一份正文：生效版（线上只读）/ 草案（可编辑） */
  const [viewMode, setViewMode] = useState<'live' | 'draft'>('live');
  const [tab, setTab] = useState('hub');
  const [publishing, setPublishing] = useState(false);
  const draftPending = !!draftStatus?.has_draft;

  const loadDraftStatus = useCallback(async (id: number) => {
    try {
      const { data } = await ontologyApi.getDraft(id);
      // 快速切换模型时丢弃旧响应（不得把上一个模型的草案状态泼到当前模型上）
      if (selectedIdRef.current === id) setDraftStatus(data);
    } catch {
      // 草案状态拉不到不影响主流程，但不得回退成“无草案”假象（按钮保持禁用并给出原因）
      if (selectedIdRef.current === id) setDraftStatus(null);
    }
  }, []);

  /** 拉草案正文（返回正文串供调用方判断是否可切视图） */
  const loadDraftContent = useCallback(async (id: number): Promise<string> => {
    try {
      const { data } = await ontologyApi.getDraftContent(id);
      const content = data.has_draft ? (data.draft_json_content || '') : '';
      if (selectedIdRef.current === id) setDraftJson(content);
      return content;
    } catch {
      if (selectedIdRef.current === id) setDraftJson('');
      return '';
    }
  }, []);

  // 草案差异摘要（顶部条文案）：把“草案里到底有什么”用人话说出来
  const draftSummary = useMemo(() => {
    if (!draftStatus?.has_draft) return '';
    const parts: string[] = [];
    if (draftStatus.added.length) {
      parts.push(`新增「${draftStatus.added.map((a) => a.display_name || a.key).join('、')}」`);
    }
    if (draftStatus.changed.length) parts.push(`修改 ${draftStatus.changed.length} 个对象`);
    if (draftStatus.removed.length) parts.push(`删除 ${draftStatus.removed.length} 个对象`);
    return parts.join('；');
  }, [draftStatus]);

  // 对象清单用的草案状态（key 小写 → diff 项），仅草案视图消费
  const draftStates = useMemo(() => {
    const m: Record<string, DraftObjectDiff> = {};
    (draftStatus?.objects || []).forEach((o) => { m[String(o.key).toLowerCase()] = o; });
    return m;
  }, [draftStatus]);

  const loadModel = useCallback(async (id: number) => {
    selectedIdRef.current = id;
    setDraftStatus(null);   // 切模型先清空，不得拿上一个模型的“有草案”误标当前模型
    setDraftJson('');
    setViewMode('live');
    setTab('hub');
    try {
      const { data } = await ontologyApi.get(id);
      if (selectedIdRef.current !== id) return;
      setModel(data);
      setSelectedId(id);
      setJsonDraft(data.json_content || '');
      // 草案状态是服务端事实：选中即拉，否则 AS-BOT 写的草案对页面不可见
      void loadDraftStatus(id);
    } catch {
      if (selectedIdRef.current === id) toast.error('加载模型失败');
    }
  }, [loadDraftStatus]);

  /** 切视图：草案视图需先拉到正文才能切（拉不到就留在生效版，不得空白屏） */
  const switchView = useCallback(async (mode: 'live' | 'draft') => {
    if (!model || mode === viewMode) return;
    if (mode === 'draft') {
      const content = await loadDraftContent(model.id);
      if (!content) {
        toast.error(draftStatus ? '没有可展示的草案正文（可能刚被发布或丢弃）' : '草案状态未加载成功，请刷新重试');
        void loadDraftStatus(model.id);
        return;
      }
      setViewMode('draft');
      return;
    }
    setViewMode('live');
  }, [model, viewMode, loadDraftContent, loadDraftStatus, draftStatus]);

  // ── 生成草案：只负责“派发”，执行在服务端后台，进度在 AS-BOT 侧边栏呈现 ──
  const openTaskConversation = useCallback((convId: number) => {
    void useAsBotStore.getState().openTaskConversation(convId);
  }, []);

  const applyTaskResult = useCallback((task: GenerateTask) => {
    setGenTask(task);
    if (task.status === 'running') return;
    const viewAction = { label: '在 AS-BOT 中查看', onClick: () => openTaskConversation(task.conversation_id) };
    void loadModels();
    if (task.status === 'done' && task.model_id) {
      void loadModel(task.model_id);
      toast.success(`草案已生成：${task.object_count} 个业务对象`, { action: viewAction, duration: 10000 });
      return;
    }
    // 未落库/被取消/被重启打断：按真实终态告知，不假报成功
    const reason = task.status === 'cancelled' ? '生成任务已取消'
      : task.status === 'interrupted' ? '服务重启，生成任务已中断，请重新发起'
      : (task.error || '生成失败');
    toast.error(reason, { description: '执行明细见 AS-BOT 会话', action: viewAction, duration: 12000 });
  }, [loadModels, loadModel, openTaskConversation]);

  const handleGenerate = async () => {
    if (!datasourceId) {
      toast.warning('请先选择数据源');
      return;
    }
    try {
      const task = await startOntologyGenerate(datasourceId, kbIds);
      setGenTask(task);
      // 直接打开 AS-BOT 侧边窗口看任务（关面板/离开页面都不影响执行）
      openTaskConversation(task.conversation_id);
      toast.success('生成任务已在后台开始执行', {
        description: kbIds.length
          ? `已带 ${kbIds.length} 个参考知识库；进度见 AS-BOT 侧边栏，可离开本页`
          : '进度见 AS-BOT 侧边栏，可离开本页',
        duration: 6000,
      });
    } catch (e: any) {
      // 错误显式展示服务端 detail（执行层不可用/无权限/数据源未授权/已有任务在跑）
      toast.error(e?.response?.data?.detail || e?.message || '生成任务派发失败');
    }
  };

  const handleCancelGenerate = async () => {
    if (!genTask) return;
    try {
      applyTaskResult(await cancelOntologyGenerateTask(genTask.conversation_id));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '取消失败，任务仍在后台执行');
    }
  };

  // 轮询只为页面按钮态与终态提醒（进度正文由 AS-BOT 面板轮询会话呈现）
  useEffect(() => {
    if (genTask?.status !== 'running') return;
    const timer = setInterval(() => {
      getOntologyGenerateTask(genTask.conversation_id).then(applyTaskResult).catch(() => {
        // 单次轮询失败保持现状，下轮重试；连续失败由 AS-BOT 面板横幅提示
      });
    }, 3000);
    return () => clearInterval(timer);
  }, [genTask?.status, genTask?.conversation_id, applyTaskResult]);

  // ── 保存（只存草案，两段式第一段）──
  // 供建模页签（行为/规则/场景/新建对象/编辑对象）落库。
  // 2026-10-26 拍板：存草案，**不碰生效内容、不级联**；发布（ontology:publish）才生效。
  // 基线纪律：有未发布草案时，编辑基线**只能是草案**（拿生效版整份提交会顶掉 AS-BOT/他人已写的草案）。
  const saveDocFromTab = useCallback(async (nextJson: string) => {
    if (!model) return;
    if (viewMode !== 'draft' && draftPending) {
      // 顶栏已禁用编辑入口，走到这里只可能是旧组件缓存的回调：不静默覆盖，显式引导
      toast.warning('该模型已有未发布草案，已切到草案视图；请在草案上重新编辑，以免顶掉草案内容');
      void switchView('draft');
      return;
    }
    setSaving(true);
    try {
      await ontologyApi.saveDraft(model.id, nextJson);
      setDraftJson(nextJson);
      setJsonDraft((prev) => prev);   // 生效版不变（saveDraft 不碰线上内容）
      await loadDraftStatus(model.id);
      loadModels();
      // 首次写入（之前无草案）：自动切到草案视图，让人立刻看到“我的改动落在哪”
      if (viewMode !== 'draft') setViewMode('draft');
      setTab('changes');
      toast.success('已写入草案（尚未生效）：已打开「变更对比」，核对无误后点发布');
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '存草案失败');
    } finally {
      setSaving(false);
    }
  }, [model, viewMode, draftPending, loadModels, loadDraftStatus, switchView]);

  // ── 发布（两段式第二段）：生效 + 级联，需 ontology:publish 权限 ──
  const handlePublish = async () => {
    if (!model) return;
    setPublishing(true);
    try {
      await ontologyApi.publishDraft(model.id);
      setDraftJson('');
      setTab('hub');
      loadModels();
      // 发布改变了生效版：重拉模型正文（否则对象列表停在发布前的旧数量）与草案状态，并回生效版视图
      await loadModel(model.id);
      toast.success('已发布生效：对象表/图谱/知识库已级联刷新');
    } catch (e: any) {
      // 透传服务端具体错因（如“生效版已被他人改动”），不换成通用文案
      toast.error(e.response?.data?.detail || '发布失败');
    } finally {
      setPublishing(false);
    }
  };

  /** 丢弃草案（已由审阅面板调完 API，这里只负责把视图拉回一致状态） */
  const handleDiscarded = async () => {
    if (!model) return;
    setDraftJson('');
    setTab('hub');
    loadModels();
    await loadModel(model.id);
    toast.success('草案已丢弃，生效版与图谱/知识库未受影响');
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
      await loadModels();
      await loadModel(model.id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '激活失败');
    } finally {
      setActivating(false);
    }
  };

  // ── 归档 / 删除 ──
  const handleArchive = async () => {
    if (!model) return;
    try {
      await ontologyApi.archive(model.id);
      toast.success('模型已归档');
      await loadModels();
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
      loadModels();
      toast.success('已还原为草案，可继续编辑后激活');
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '还原失败');
    }
  };

  // ── 手动同步知识库与图谱重建均在「关系图谱」页（派生重建与状态回显同屏），
  //    本页只保留状态徽章与保存/激活的自动级联。

  const handleDelete = async () => {
    if (!model) return;
    try {
      await ontologyApi.remove(model.id);
      toast.success('模型已删除');
      setModel(null);
      setSelectedId(null);
      setJsonDraft('');
      loadModels();
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
      await loadModels();
      await loadModel(id);
      await fetchVersions(id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '激活失败');
    }
  };

  // 工作区当前展示的正文：草案视图用草案内容（所以对象清单/属性/关系就是草案的真实形状）
  const activeJson = viewMode === 'draft' ? draftJson : jsonDraft;
  const { objects, issues: objectIssues } = useMemo(
    () => model ? parseObjects(activeJson) : { objects: [], issues: [] },
    [model, activeJson]);
  const hubObjects = objects as HubObject[];
  // 模型分组按 kind（x4 归属改造）：本体归属是 domain+kind，不是数据源。
  // 业务本体的 datasource_id 也是 0，旧的“有合法数据源 → 业务”口径会把它们误归到系统组。
  const byKind = (k: string) => models.filter((m) => (m.kind || '') === k);
  const sourceModels = useMemo(() => byKind('source'), [models]);
  const bizModels = useMemo(() => byKind('business'), [models]);
  const sysModels = useMemo(() => byKind('system'), [models]);
  // 兼容无 kind 的存量模型：回落旧口径（有合法数据源 → 业务），不丢进任一组
  const legacyModels = useMemo(() =>
    models.filter((m) => !m.kind && !['source', 'business', 'system'].includes(m.kind || '')), [models]);

  const editable = !!model && model.status !== 'archived';
  /** 草案视图下对象清单的引导文案：说清“此处编辑落在哪”，避免用户以为在改线上口径 */
  const draftHint = '此处显示的是草案内容：新建/编辑只写草案，不会改动线上生效版；核对后到「变更对比」点发布才级联。';
  /** 生效版视图且已有草案：编辑入口关闭（要改必须切草案，否则整份提交会顶掉草案） */
  const liveLocked = viewMode === 'live' && draftPending;
  /** 编辑入口是否可用：生效版不可直接改（线上口径），有未发布草案时必须先到草案视图 */
  const canEditDoc = editable && hasPerm('ontology:save') && !liveLocked;
  /** 草案里待发布的变更数（顶部标记与页签标记） */
  const pendingChangeCount = draftPending
    ? (draftStatus?.added.length || 0) + (draftStatus?.changed.length || 0) + (draftStatus?.removed.length || 0)
    : 0;
  const matchModel = (m: OntologyModelSummary) => {
    const k = modelKw.trim().toLowerCase();
    return !k || (m.name || '').toLowerCase().includes(k) || (m.domain || '').toLowerCase().includes(k);
  };
  const filteredBizModels = useMemo(() => bizModels.filter(matchModel), [bizModels, modelKw]);
  const filteredSourceModels = useMemo(() => sourceModels.filter(matchModel), [sourceModels, modelKw]);
  const filteredSysModels = useMemo(() => sysModels.filter(matchModel), [sysModels, modelKw]);
  const filteredLegacyModels = useMemo(() => legacyModels.filter(matchModel), [legacyModels, modelKw]);
  // 源本体不承载 M2/M3/M4：页签隐藏后，若存量 doc 仍声明了行为/规则/场景，
  // 必须可看到“声明仍在”（不静默藏起来），并引导去清理
  const m234Declared = useMemo(() => {
    try {
      return countM234Declarations(JSON.parse(activeJson || '{}'));
    } catch {
      return 0;
    }
  }, [activeJson]);
  const hiddenM234 = !!model && !showsM234(model.kind) && m234Declared > 0;

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
        {/* 辅助生成的参考知识库（多选，选项显示名称）：不选则仅依据表结构归纳 */}
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" className="h-9 font-normal">
              <BookMarked className="h-4 w-4 mr-1 text-muted-foreground" />
              参考知识库
              <Badge variant="secondary" className="ml-1.5 text-[10px] px-1.5">
                {kbIds.length ? `已选 ${kbIds.length}` : '未选'}
              </Badge>
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72 p-2">
            <div className="text-xs text-muted-foreground px-1 pb-1.5">
              选中知识库会作为业务背景/关系线索辅助归纳（仅参考，不臆造口径）
            </div>
            <div className="max-h-64 overflow-y-auto space-y-0.5">
              {knowledgeBases.map((kb) => (
                <label key={kb.id}
                       className="flex items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-muted cursor-pointer">
                  <input type="checkbox" checked={kbIds.includes(kb.id)}
                         onChange={() => setKbIds((prev) => (
                           prev.includes(kb.id) ? prev.filter((x) => x !== kb.id) : [...prev, kb.id]))} />
                  <span className="truncate">{kb.name}</span>
                </label>
              ))}
              {!knowledgeBases.length && (
                <div className="px-1.5 py-1 text-xs text-muted-foreground">没有启用中的知识库</div>)}
            </div>
          </PopoverContent>
        </Popover>
        <Button onClick={handleGenerate} disabled={running || !datasourceId}>
          {running ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Sparkles className="h-4 w-4 mr-1" />}
          {running ? '后台执行中…' : '生成本体模型'}
        </Button>
        {running && (
          <Button variant="outline" onClick={() => genTask && openTaskConversation(genTask.conversation_id)}>
            查看进度
          </Button>
        )}
        {running && (
          <Button variant="ghost" className="text-red-500 hover:text-red-600" onClick={handleCancelGenerate}>
            取消任务
          </Button>
        )}
        <Button variant="outline" onClick={() => setYamlOpen(true)} disabled={!datasourceId}>
          <Upload className="h-4 w-4 mr-1" />导入 YAML
        </Button>
        <Button
          variant="outline"
          onClick={() => loadModels()}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-4 gap-4">
        {/* 左栏：模型列表（可搜索 + 按 kind 分组）+ 建模待办入口 */}
        <Card className="xl:col-span-1">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-sm">模型列表（{models.length}）</CardTitle>
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                <Switch checked={showArchived} onCheckedChange={(v) => {
                  setShowArchived(v);
                  loadModels(v);
                }} />
                显示归档
              </label>
            </div>
            <div className="relative mt-2">
              <Search className="h-3.5 w-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <Input value={modelKw} onChange={(e) => setModelKw(e.target.value)}
                placeholder="搜索模型名 / 业务域" className="h-8 pl-7 text-xs" />
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {models.length === 0 && (
              <div className="text-xs text-muted-foreground py-4 text-center">
                暂无模型，选择数据源后点击"生成本体模型"
              </div>
            )}
            {models.length > 0 && !filteredBizModels.length && !filteredSourceModels.length
              && !filteredSysModels.length && !filteredLegacyModels.length && (
              <div className="text-xs text-muted-foreground py-3 text-center">没有匹配“{modelKw}”的模型</div>
            )}
            <ModelGroup
              title="业务本体（按业务域）" list={filteredBizModels}
              selectedId={selectedId} onPick={loadModel}
            />
            <ModelGroup
              title="源本体（按数据源）" list={filteredSourceModels}
              selectedId={selectedId} onPick={loadModel}
            />
            <ModelGroup
              title="系统能力本体" list={filteredSysModels}
              selectedId={selectedId} onPick={loadModel}
            />
            <ModelGroup
              title="未分类" list={filteredLegacyModels}
              selectedId={selectedId} onPick={loadModel}
            />

            {/* 建模待办：已抽为独立页（不属于某个模型的全局缺口），本页只留入口与计数 */}
            <button
              onClick={() => navigate('/data/asset-pool')}
              className="w-full text-left rounded-lg border border-dashed px-3 py-2 text-sm transition-colors hover:bg-muted/50"
            >
              <div className="flex items-center gap-2">
                <Inbox className="h-4 w-4 text-amber-500" />
                建模待办（未归属资产）
                {poolTotal > 0 && (
                  <Badge variant="outline" className="ml-auto text-amber-500 border-amber-500/40">{poolTotal} 项</Badge>
                )}
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">跨模型的覆盖缺口 · 打开独立工作台处理</div>
            </button>
          </CardContent>
        </Card>

        {/* 右主区：模型工作区（生效版/草案 双视图 + 变更对比） */}
        <Card className="xl:col-span-3">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
                {model ? model.name : '工作区'}
                {model && (
                  <Badge variant="outline" className={`text-[10px] font-normal ${STATUS_META[model.status]?.cls}`}>
                    {STATUS_META[model.status]?.label}
                  </Badge>
                )}
                {model && viewMode === 'draft' && (
                  <Badge variant="outline" className="text-[10px] font-normal border-amber-500/40 text-amber-600">
                    草案视图·未生效
                  </Badge>
                )}
                {saving && <span className="text-xs text-muted-foreground">写入草案中…</span>}
              </CardTitle>
              {model && (
                <div className="flex items-center gap-2 flex-wrap">
                  {/* 视图切换：生效版（线上只读）↔ 草案（可编辑）。
                      这就是“有存草案按钮却没有编辑草案的地方”的答案：草案本身就是工作区的一个视图。 */}
                  <div className="inline-flex items-center rounded-md border border-border bg-muted/40 p-0.5">
                    <button
                      onClick={() => switchView('live')}
                      title="线上正在生效的口径（只读；要改请切到草案）"
                      className={`h-7 rounded px-2.5 text-xs transition-colors ${
                        viewMode === 'live' ? 'bg-background shadow-sm font-medium' : 'text-muted-foreground hover:text-foreground'}`}
                    >
                      生效版 · {draftStatus?.effective_object_count ?? objects.length}
                    </button>
                    <button
                      onClick={() => switchView('draft')}
                      disabled={!draftPending}
                      title={draftPending
                        ? `草案 ${draftStatus?.draft_object_count} 个对象（${draftStatus?.draft_updated_by || '未知'} 于 ${draftStatus?.draft_updated_at?.slice(0, 16).replace('T', ' ') || '—'} 保存）；此处编辑直接写入草案`
                        : '当前没有待发布的草案'}
                      className={`h-7 rounded px-2.5 text-xs inline-flex items-center gap-1 transition-colors ${
                        !draftPending ? 'text-muted-foreground/50 cursor-not-allowed'
                          : viewMode === 'draft' ? 'bg-amber-500/15 text-amber-700 shadow-sm font-medium'
                            : 'text-amber-600 hover:bg-amber-500/10'}`}
                    >
                      草案 · {draftStatus?.draft_object_count ?? 0}
                      {pendingChangeCount > 0 && (
                        <Badge variant="outline" className="text-[10px] px-1 h-4 border-amber-500/40 text-amber-600">
                          {pendingChangeCount}
                        </Badge>
                      )}
                    </button>
                  </div>
                  {(model.datasource_id || 0) > 0 && (
                    /* v0.5: 业务本体默认同步系统知识库，不再手动选库 */
                    <Badge variant="outline" className="h-8 px-3 text-muted-foreground">
                      系统知识库 · 自动同步
                    </Badge>
                  )}
                  <span title="派生状态回显（30s 轮询）；手动「图谱生成」与「同步知识库」请到「关系图谱」页">
                    <SyncBadges modelId={model.id} kind={model.kind} />
                  </span>
                  <Button size="sm" variant="outline" onClick={handleOpenVersions}>
                    <History className="h-3.5 w-3.5 mr-1" />
                    版本管理
                  </Button>
                  {/* 发布 = 两段式第二段：按 ontology:publish 显隐（无码不渲染，不给死开关）；
                      可用性由服务端草案状态裁决，AS-BOT 写的草案同样可发 */}
                  {draftPending && editable && hasPerm('ontology:publish') && (
                    <Button size="sm" variant="default" onClick={handlePublish}
                      disabled={publishing || !!draftStatus?.base_stale || !!draftStatus?.draft_parse_error}
                      title={draftStatus?.base_stale
                        ? '生效版在草案写入后又被改动，发布会被并发保护阻断；请基于最新生效版重做草案'
                        : draftStatus?.draft_parse_error
                          ? '草案正文结构异常，请先修正后再发布'
                          : `发布后级联刷新对象表/图谱/知识库：${draftSummary || '有改动'}`}
                    >
                      {publishing ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5 mr-1" />}
                      发布草案（{pendingChangeCount}）
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
            {/* 草案条：当前看的是哪一份 + 待发布内容概要 + 下一步动作（一屏内闭环） */}
            {model && draftPending && (
              <div className={`mt-2 flex items-start gap-2 rounded-md border px-3 py-2 text-xs ${
                viewMode === 'draft' ? 'border-amber-500/40 bg-amber-500/10' : 'border-amber-500/30 bg-amber-500/5'}`}>
                <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-amber-600" />
                <div className="min-w-0 space-y-0.5">
                  <div className="font-medium text-amber-700">
                    {viewMode === 'draft'
                      ? `正在查看草案（${draftStatus?.draft_object_count} 个对象）：此处新建/编辑直接写入草案，不影响线上口径。`
                      : `有 ${pendingChangeCount} 项待发布改动尚未生效（对象 ${draftStatus?.effective_object_count} → ${draftStatus?.draft_object_count}）。`}
                  </div>
                  <div className="text-muted-foreground">
                    {draftSummary || '草案与生效版对象清单一致（可能只改了属性/关系以外的内容）'}
                    {' · '}{draftStatus?.draft_updated_by || '未知'} 于 {draftStatus?.draft_updated_at?.slice(0, 16).replace('T', ' ') || '—'} 保存
                    {draftStatus?.base_stale && (
                      <span className="ml-1 text-red-600">· 生效版已再被改动，发布会被阻断，需重做草案</span>
                    )}
                  </div>
                  {viewMode === 'live' && (
                    <div className="text-muted-foreground">
                      草案内容在发布前不会进入取数链路（不展开到对象表、不进图谱/知识库），所以生效版视图里看不到它。
                    </div>
                  )}
                </div>
                <div className="ml-auto flex items-center gap-2 shrink-0">
                  <Button size="sm" variant="outline" className="h-7 text-xs"
                    onClick={() => { setTab('changes'); if (viewMode !== 'draft') void switchView('draft'); }}>
                    <GitCompare className="h-3 w-3 mr-1" />变更对比
                  </Button>
                  {viewMode === 'live' ? (
                    <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => void switchView('draft')}>
                      <Eye className="h-3 w-3 mr-1" />切到草案
                    </Button>
                  ) : (
                    <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => void switchView('live')}>
                      返回生效版
                    </Button>
                  )}
                </div>
              </div>
            )}
            {model && (
              <div className="text-xs text-muted-foreground mt-2">
                {viewMode === 'draft'
                  ? '草案视图：改动只写草案、不级联；在「变更对比」核对后点发布才同步对象表/图谱/知识库。同构站点部署为模型级配置，不受草案/发布影响。'
                  : model.status === 'archived'
                    ? '已归档模型只读；可「还原为草案」继续编辑，或「确认激活」直接恢复为生效（归档数据始终可查看）。'
                    : draftPending
                      ? '生效版只读（线上正在用的口径）；要改建模请切到草案视图。'
                      : '新增/编辑对象会先存为草案，点「发布」才级联同步对象/图谱/维度字典；同构多站部署由模型级「同构站点部署」声明统一路由，跨源去重/汇总口径请在知识库文档描述。'}
              </div>
            )}
          </CardHeader>
          <CardContent>
            {!model ? (
              <div className="text-sm text-muted-foreground py-12 text-center">
                从左侧选择模型（业务/系统能力本体），或先生成本体草案
              </div>
            ) : (
              <Tabs key={model.id} value={tab} onValueChange={setTab}>
                <TabsList>
                  <TabsTrigger value="hub">对象 · Link</TabsTrigger>
                  {draftPending && (
                    <TabsTrigger value="changes">
                      变更对比
                      <Badge variant="outline" className="ml-1 text-[10px] px-1 h-4 border-amber-500/40 text-amber-600">
                        {pendingChangeCount}
                      </Badge>
                    </TabsTrigger>
                  )}
                  <TabsTrigger value="terms">业务术语</TabsTrigger>
                  <TabsTrigger value="tags">标签</TabsTrigger>
                  {showsM234(model.kind) && <TabsTrigger value="m234">行为</TabsTrigger>}
                  {showsM234(model.kind) && <TabsTrigger value="rules">规则</TabsTrigger>}
                  {showsM234(model.kind) && <TabsTrigger value="scenarios">场景</TabsTrigger>}
                  {showsSiteScope(model.kind) && <TabsTrigger value="sites">同构站点部署</TabsTrigger>}
                </TabsList>
                {hiddenM234 && (
                  <div className="mt-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-1.5 text-[11px] text-amber-600">
                    <AlertTriangle className="h-3 w-3 mr-1 inline" />
                    该模型不承载行为/规则/场景建模位（{model.kind === 'source' ? '源本体是单源物理表语义投影' : '系统能力面才用行为/规则建模'}），
                    但 doc 内仍有 {m234Declared} 条声明未展示：写动作权限请配在菜单与功能权限码，口径规则请登记指标字典，
                    并从 JSON 内容中删除这些声明。
                  </div>
                )}
                <TabsContent value="hub" className="mt-2 h-[62vh] overflow-hidden">
                  <div className="flex items-center gap-2 mb-2">
                    {canEditDoc && (
                      <ObjectCreateButton jsonContent={activeJson} onSave={saveDocFromTab} />
                    )}
                    {liveLocked && editable && (
                      <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => void switchView('draft')}>
                        <Pencil className="h-3 w-3 mr-1" />切到草案后编辑
                      </Button>
                    )}
                    {viewMode === 'draft' && (
                      <span className="text-[11px] text-muted-foreground">
                        新建/编辑的对象只进草案，发布后才进对象表与图谱
                      </span>
                    )}
                  </div>
                  <div className="h-[calc(62vh-2.5rem)] overflow-auto">
                    <ObjectHubTab modelId={model.id} objects={hubObjects} issues={objectIssues}
                      jsonContent={activeJson} onSave={saveDocFromTab}
                      canEdit={canEditDoc} viewMode={viewMode} draftStates={draftStates}
                      draftHint={draftHint} modelKind={model.kind} />
                  </div>
                </TabsContent>
                {draftPending && (
                  <TabsContent value="changes" className="mt-2">
                    <ModelDraftReview modelId={model.id} draft={draftStatus}
                      canPublish={editable && hasPerm('ontology:publish')}
                      canEdit={editable && hasPerm('ontology:save')}
                      publishing={publishing}
                      onPublish={handlePublish}
                      onDiscard={handleDiscarded}
                      onEditInDraft={() => void switchView('draft')} />
                  </TabsContent>
                )}
                <TabsContent value="terms" className="mt-2 h-[62vh] overflow-auto">
                  <Glossary embedded datasourceId={model.datasource_id}
                    tables={hubObjects.map((o: any) => String(o.primary_table || '').toLowerCase()).filter(Boolean)} />
                </TabsContent>
                <TabsContent value="tags" className="mt-2 h-[62vh] overflow-auto">
                  <ObjectTagsTab objects={hubObjects as any} />
                </TabsContent>
                {showsM234(model.kind) && (
                  <TabsContent value="m234" className="mt-2 h-[62vh] overflow-hidden">
                    <ModelActionsTab jsonContent={activeJson} onSave={saveDocFromTab} />
                  </TabsContent>
                )}
                {showsM234(model.kind) && (
                  <TabsContent value="rules" className="mt-2 h-[62vh] overflow-hidden">
                    <ModelRulesTab jsonContent={activeJson} onSave={saveDocFromTab} />
                  </TabsContent>
                )}
                {showsM234(model.kind) && (
                  <TabsContent value="scenarios" className="mt-2 h-[62vh] overflow-hidden">
                    <ModelScenariosTab jsonContent={activeJson} onSave={saveDocFromTab} />
                  </TabsContent>
                )}
                {showsSiteScope(model.kind) && (
                  <TabsContent value="sites" className="mt-2 h-[62vh] overflow-hidden">
                    <ModelSitesTab modelId={model.id} readonly={!editable} />
                  </TabsContent>
                )}
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

      {/* 草案审阅弹窗已下线（旧「查看草案」把 JSON 摊出来让人自己比）：
          改由工作区内的「变更对比」页签承担，见 ModelDraftReview。 */}
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
