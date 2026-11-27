import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
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
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from '@/lib/toast';
import {
  Activity, Archive, BookMarked, BookOpen, CheckCircle2, Dna, FileText, GitCompare, History,
  Layers, LayoutGrid, Loader2, MapPin, Pencil, Plus, RefreshCw, ScrollText, Share2, Sparkles,
  Tags, Trash2, Undo2, Upload, Workflow, Zap,
} from 'lucide-react';
import CapabilityFlowsTab from './CapabilityFlowsTab';
import OntologyWorkbench, { type WbRailSection, type WbTray } from './OntologyWorkbench';
import CanonicalDocPanel from './CanonicalDocPanel';
import ObjectHubTab, { type HubObject, type InspTab } from './ObjectHubTab';
import ModelHealthTab from './ModelHealthTab';
import EvolutionCenter from './EvolutionCenter';
import { normalizeDocObjects, showsM234, showsSiteScope, type NormalizeDocObjectsResult } from '@/lib/ontologyObjects';
import ModelDraftReview from './ModelDraftReview';
import GraphRebuildButton from './GraphRebuildButton';
import KbSyncButton from './KbSyncButton';
import Glossary from './Glossary';
import { ModelActionsTab, ModelGraphTab, ModelRulesTab, ModelScenariosTab, ModelSitesTab, ObjectTagsTab, SyncBadges } from './ModelAssetTabs';
import { metricsApi } from '@/api/metrics';
import { useAsBotStore } from '@/stores/asBotStore';
import { hasPerm } from '@/stores/permissionStore';
import { useTranslation } from 'react-i18next';
import { t as tr } from '@/i18n/runtime';

interface Datasource {
  id: number;
  name: string;
  db_type?: string;
}

const STATUS_META: Record<OntologyStatus, { label: string; cls: string }> = {
  draft: { label: tr('k_bbcb968bf3'), cls: 'bg-amber-500/10 text-amber-500 border-amber-500/20' },
  active: { label: tr('k_83a991d77c'), cls: 'bg-green-500/10 text-green-500 border-green-500/20' },
  archived: { label: tr('k_c3ba167c87'), cls: 'bg-gray-500/10 text-gray-500 border-gray-500/20' },
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

export default function OntologyModeling() {
  const { t } = useTranslation('ui');
  // ── 数据源 ──
  const [datasources, setDatasources] = useState<Datasource[]>([]);
  const [datasourceId, setDatasourceId] = useState<number>(0);

  // ── 模型列表 ──
  const [models, setModels] = useState<OntologyModelSummary[]>([]);
  const [modelKw, setModelKw] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  // 选中模型的同步 ref：异步回包时用它判断“当前还看着这个模型吗”（state 在回调里会闭包过期）
  const selectedIdRef = useRef<number | null>(null);
  const [model, setModel] = useState<OntologyModel | null>(null);

  // ── 编辑器（生效版正文；草案正文另存 draftJson）──
  const [jsonDraft, setJsonDraft] = useState('');
  const [, setSaving] = useState(false);

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

  // ── 归档可见（手动同步知识库就在本页工具栏，与图谱/知识库状态徽章同屏）──
  const [showArchived, setShowArchived] = useState(false);

  const handleImportYaml = async () => {
    if (model?.kind === 'system') return;
    if (!datasourceId) {
      toast.warning(t('k_acac930033'));
      return;
    }
    setImporting(true);
    try {
      const { data } = await client.post('/catalog/ontology/import-yaml', {
        datasource_id: datasourceId, dir: yamlDir, rebuild_graph: true,
      });
      toast.success(t('k_ef1c89aa30', { p0: data.object_count ?? '?', p1: data.links ?? '?' }));
      setYamlOpen(false);
      await loadModels();
      if (data.model_id) loadModel(data.model_id);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_fddcd7c6e4'));
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
  // 只读文档面板（侧栏「文档」入口；不在模型下拉里，不再塞进选择器）
  const [docOpen, setDocOpen] = useState(false);

  // ── 未归属资产池（建模待办）：本页内嵌页签，跨模型缺口不离开建模上下文 ──
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
  // 深链：/data/ontology?tab=evolution（旧 /data/evolution 的重定向落点）
  const [tab, setTab] = useState(() => {
    try { return new URLSearchParams(window.location.search).get('tab') || 'hub'; } catch { return 'hub'; }
  });
  // 内联新建对象行：点「＋ 新建对象」在画布顶部就地展开（不再弹居中对话框）
  const [creatingObject, setCreatingObject] = useState(false);
  const [publishing, setPublishing] = useState(false);
  /** 撤销草案：确认弹窗收在页面一处，托盘与「变更对比」都只负责发起（不各造一套实现） */
  const [discardOpen, setDiscardOpen] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  /** 图谱重建 / 知识库同步计数：据此重挂图面板并重取同步徽章（否则「需重建 / 待同步」还挂着，图仍是旧的） */
  const [graphTick, setGraphTick] = useState(0);
  const draftPending = !!draftStatus?.has_draft;
  const systemReadOnly = model?.kind === 'system';

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
      parts.push(t('k_d9bd023660', { p0: draftStatus.added.map((a) => a.display_name || a.key).join(t('k_b505660a97')) }));
    }
    if (draftStatus.changed.length) parts.push(t('k_e661d0e42f', { p0: draftStatus.changed.length }));
    if (draftStatus.removed.length) parts.push(t('k_cbf20245c7', { p0: draftStatus.removed.length }));
    return parts.join(t('k_c4530db333'));
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
      if (selectedIdRef.current === id) toast.error(t('k_bf77543ebf'));
    }
  }, [loadDraftStatus]);

  /** 切视图：草案视图需先拉到正文才能切（拉不到就留在生效版，不得空白屏） */
  const switchView = useCallback(async (mode: 'live' | 'draft') => {
    if (!model || mode === viewMode) return;
    if (mode === 'draft') {
      const content = await loadDraftContent(model.id);
      if (!content) {
        toast.error(draftStatus ? t('k_4fc822ea4b') : t('k_0a5a1a4a65'));
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
    const viewAction = { label: t('k_dc4f492def'), onClick: () => openTaskConversation(task.conversation_id) };
    void loadModels();
    if (task.status === 'done' && task.model_id) {
      void loadModel(task.model_id);
      toast.success(t('k_ffd20f4aab', { p0: task.object_count }), { action: viewAction, duration: 10000 });
      return;
    }
    // 未落库/被取消/被重启打断：按真实终态告知，不假报成功
    const reason = task.status === 'cancelled' ? t('k_99570f6d9e')
      : task.status === 'interrupted' ? t('k_68d5cf5395')
      : (task.error || t('k_7f7de8a201'));
    toast.error(reason, { description: t('k_9f44625085'), action: viewAction, duration: 12000 });
  }, [loadModels, loadModel, openTaskConversation]);

  const handleGenerate = async () => {
    if (model?.kind === 'system') return;
    if (!datasourceId) {
      toast.warning(t('k_192dc9f17f'));
      return;
    }
    try {
      const task = await startOntologyGenerate(datasourceId, kbIds);
      setGenTask(task);
      // 直接打开 AS-BOT 侧边窗口看任务（关面板/离开页面都不影响执行）
      openTaskConversation(task.conversation_id);
      toast.success(t('k_9afeb6ad06'), {
        description: kbIds.length
          ? t('k_a6252502e9', { p0: kbIds.length })
          : t('k_e3da8453bd'),
        duration: 6000,
      });
    } catch (e: any) {
      // 错误显式展示服务端 detail（执行层不可用/无权限/数据源未授权/已有任务在跑）
      toast.error(e?.response?.data?.detail || e?.message || t('k_3429036a3d'));
    }
  };

  const handleCancelGenerate = async () => {
    if (!genTask) return;
    try {
      applyTaskResult(await cancelOntologyGenerateTask(genTask.conversation_id));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_0000707549'));
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
  const saveDocFromTab = useCallback(async (nextJson: string, opts?: { stay?: boolean }) => {
    if (!model || model.kind === 'system') return;
    if (viewMode !== 'draft' && draftPending) {
      // 顶栏已禁用编辑入口，走到这里只可能是旧组件缓存的回调：不静默覆盖，显式引导
      toast.warning(t('k_7dd74b20b9'));
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
      // 就地新建（opts.stay）不跳走：建完继续在对象画布上补属性/路由；编辑仍去「变更对比」核对
      if (!opts?.stay) setTab('changes');
      toast.success(t('k_58272cf7ae'));
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_2281d5a551'));
    } finally {
      setSaving(false);
    }
  }, [model, viewMode, draftPending, loadModels, loadDraftStatus, switchView]);

  // ── 发布（两段式第二段）：生效 + 级联，需 ontology:publish 权限 ──
  const handlePublish = async () => {
    if (!model || model.kind === 'system') return;
    setPublishing(true);
    try {
      const { data } = await ontologyApi.publishDraft(model.id);
      setDraftJson('');
      setTab('hub');
      loadModels();
      // 发布改变了生效版：重拉模型正文（否则对象列表停在发布前的旧数量）与草案状态，并回生效版视图
      await loadModel(model.id);
      // 文案必须与级联真做的事一致：发布刷新对象表/部署绑定/口径字典/图谱；知识库不在这条链上
      // （由后台 kb_sync_reconcile 周期重推，或在本页工具栏手动「同步知识库」）。以前写成
      // "知识库已级联刷新"，徽章却还是"待同步"——假承诺会让人以为同步坏了。
      const assets = data?.semantic_assets;
      const created = (assets?.metrics_created?.length || 0) + (assets?.dimensions_created?.length || 0);
      const merged = (assets?.metrics_merged?.length || 0) + (assets?.dimensions_merged?.length || 0);
      const blocked = assets?.blocked?.length || 0;
      const warns = data?.warnings || [];
      toast.success(t('k_fc975e5ae8', { p0: created || merged ? t('k_748608846f', { p0: created + merged, p1: created }) : '' }));
      // 级联里“没成功”的部分必须看得见：只报“发布成功”就是把缺口径留给线上错数
      if (blocked || warns.length) {
        const first = assets?.blocked?.[0]
          ? `${assets.blocked[0].kind}「${assets.blocked[0].name}」：${assets.blocked[0].reason}`
          : String(warns[0] || '');
        toast.warning(t('k_91f0f4630b', { p0: blocked + warns.length, p1: first.slice(0, 180) }));
      }
    } catch (e: any) {
      // 透传服务端具体错因（如“生效版已被他人改动”），不换成通用文案
      toast.error(e.response?.data?.detail || t('k_250688d7c9'));
    } finally {
      setPublishing(false);
    }
  };

  /** 撤销草案（丢弃整份草案回到生效版）：动作收敛在页面，审阅面板与底部托盘都走这里 */
  const requestDiscardDraft = useCallback(() => {
    if (!model || model.kind === 'system' || !draftPending) return;
    setDiscardOpen(true);
  }, [model, draftPending]);

  const confirmDiscardDraft = async () => {
    if (!model || model.kind === 'system') return;
    setDiscarding(true);
    try {
      await ontologyApi.discardDraft(model.id);
      setDiscardOpen(false);
      setDraftJson('');
      setTab('hub');
      loadModels();
      // 撤销改变了服务端草案状态：必须重拉模型/草案（视图回生效版，标记一起消失）
      await loadModel(model.id);
      toast.success(t('k_19ec113608'));
    } catch (e: any) {
      // 透传服务端错因（无草案 / 无权限），不换成通用文案
      toast.error(e?.response?.data?.detail || t('k_d66df9d32c'));
    } finally {
      setDiscarding(false);
    }
  };

  // ── 激活 ──
  const handleActivate = async () => {
    if (!model || model.kind === 'system') return;
    setActivating(true);
    try {
      await ontologyApi.activate(model.id);
      // 激活后写入 Oxigraph RDF
      try {
        await client.post(`/ontology/${model.id}/write-rdf`);
      } catch {
        // RDF 写入失败不阻塞激活
      }
      toast.success(t('k_c80ce8073f'));
      setActivateOpen(false);
      await loadModels();
      await loadModel(model.id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_912efdba9d'));
    } finally {
      setActivating(false);
    }
  };

  // ── 归档 / 删除 ──
  const handleArchive = async () => {
    if (!model || model.kind === 'system') return;
    try {
      await ontologyApi.archive(model.id);
      toast.success(t('k_e77d15aefc'));
      await loadModels();
      await loadModel(model.id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_2950599a85'));
    }
  };

  // ── 还原归档模型为草案（归档数据可逆） ──
  const handleRestore = async () => {
    if (!model || model.kind === 'system') return;
    try {
      const { data } = await ontologyApi.restore(model.id);
      setModel(data);
      setJsonDraft(data.json_content || '');
      loadModels();
      toast.success(t('k_3f3c6a8113'));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || t('k_7bd43c8055'));
    }
  };

  // ── 派生重建动作（图谱生成 / 同步知识库）就放在状态徽章旁：同看同建一处，做完立刻回显。

  const handleDelete = async () => {
    if (!model || model.kind === 'system') return;
    try {
      await ontologyApi.remove(model.id);
      toast.success(t('k_66ca4c11c7'));
      setModel(null);
      setSelectedId(null);
      setJsonDraft('');
      loadModels();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_acf0664a54'));
    }
  };

  // ── 版本管理：拉取同一模型（数据源 + 名称）的全部版本（含归档） ──
  const fetchVersions = useCallback(async (id: number) => {
    setVersionsLoading(true);
    try {
      const { data } = await ontologyApi.versions(id);
      setVersions(data.items || []);
    } catch {
      toast.error(t('k_44d85a531c'));
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
    if (model?.kind === 'system') return;
    try {
      await ontologyApi.activate(id);
      try {
        await client.post(`/ontology/${id}/write-rdf`);
      } catch {
        // RDF 写入失败不阻塞激活
      }
      toast.success(t('k_69ec3a9599'));
      await loadModels();
      await loadModel(id);
      await fetchVersions(id);
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_912efdba9d'));
    }
  };

  // 工作区当前展示的正文：草案视图用草案内容（所以对象清单/属性/关系就是草案的真实形状）
  const activeJson = viewMode === 'draft' ? draftJson : jsonDraft;
  const { objects, issues: objectIssues } = useMemo(
    () => model ? parseObjects(activeJson) : { objects: [], issues: [] },
    [model, activeJson]);
  const hubObjects = objects as HubObject[];
  /** 只读文档面板：生效版版本号（doc.model_version，缺省不显 v 号）与对象定位清单 */
  const liveDocVersion = useMemo(() => {
    try { return String(JSON.parse(jsonDraft || '{}').model_version || ''); } catch { return ''; }
  }, [jsonDraft]);
  const docObjectOptions = useMemo(
    () => hubObjects.filter((o) => !!o.key).map((o) => ({ key: o.key, name: o.display_name || o.key })),
    [hubObjects]);
  const editable = !!model && model.status !== 'archived' && !systemReadOnly;
  /** 草案视图下对象清单的引导文案：说清“此处编辑落在哪”，避免用户以为在改线上口径 */
  const draftHint = t('k_f8f48c2e2b');
  /** 生效版视图且已有草案：编辑入口关闭（要改必须切草案，否则整份提交会顶掉草案） */
  const liveLocked = !systemReadOnly && viewMode === 'live' && draftPending;
  /** 编辑入口是否可用：系统本体由平台版本发布维护，任何人工维护入口均关闭 */
  const canEditDoc = editable && model.kind !== 'system' && hasPerm('ontology:save') && !liveLocked;
  /** 草案里待发布的变更数（顶部标记与页签标记） */
  const pendingChangeCount = draftPending
    ? (draftStatus?.added.length || 0) + (draftStatus?.changed.length || 0) + (draftStatus?.removed.length || 0)
    : 0;
  // 行为/规则/场景页签：所有 kind 都展示（showsM234 恒真），不再按分域隐藏，
  // 口径集中在 lib/ontologyObjects.showsM234，调用方不自己判 kind。

// ── 工作台外壳：分区 / 画布头 / 草案托盘都在这里算清，外壳只做布局，不做第二套权限判断 ──
  const docCounts = useMemo(() => {
    try {
      const d = JSON.parse(activeJson || '{}');
      const objs = Array.isArray(d.objects) ? d.objects : [];
      return {
        actions: objs.reduce((n: number, o: any) => n + (Array.isArray(o?.actions) ? o.actions.length : 0), 0),
        rules: Array.isArray(d.rules) ? d.rules.length : 0,
        scenarios: Array.isArray(d.scenarios) ? d.scenarios.length : 0,
      };
    } catch {
      return { actions: 0, rules: 0, scenarios: 0 };
    }
  }, [activeJson]);

  const m234 = !!model && showsM234(model.kind);
  const siteScope = !!model && showsSiteScope(model.kind);

  const handleNav = useCallback((k: string) => {
    if (k === 'doc') {
      // 只读文档：本地面板，不占页签、不跳路由（它不是一份可编辑视图）
      setDocOpen(true);
      return;
    }
    if (k === 'health') {
      // 建模体检：**本模型**对象级缺口（未绑表 / 列漂移 / 孤儿关系 / 缺 key），每条可定位回对象画布
      setTab('health');
      return;
    }
    if (k === 'evolution') {
      // 进化中心（原「建模待办」位置，2026-11-21 拍板）：候选分诊 + 知识更新 + 字典待办
      setTab('evolution');
      return;
    }
    // 关系图谱不跳外层菜单：它就在本页画布里（跟随当前模型，切模型即换图）
    setTab(k);
  }, []);

  // 选中态与可见性守卫：草案丢弃/形态切换后，失效的页签回落到对象视图；
  // 还没选模型时落在「全部模型」总览 —— 模型名是入口（按名字选，不按 id 选），不另造左侧模型列表。
  // 进化中心是跨模型视图，未选模型时也必须可达（优先于其它回落）；无 evolution:read 回落总览
  const canViewEvolution = hasPerm('evolution:read');
  // 能力流程页签（P3-5）：仅系统能力本体 + capability:run 可见（编排入口权限）
  const canRunFlows = hasPerm('capability:run');
  const activeTab = tab === 'evolution' ? (canViewEvolution ? 'evolution' : 'models')
    : tab === 'flows' ? ((systemReadOnly && canRunFlows) ? 'flows' : 'models')
    : !model ? 'models'
      : (tab === 'changes' && (!draftPending || systemReadOnly)) || (tab === 'sites' && !siteScope) ? 'hub' : tab;

  // 跨模型入口（全部模型 / 进化中心）：未选模型时也必须可达
  const crossItems: WbRailSection['items'] = [
    { key: 'models', label: t('k_688facc5f5'), icon: <LayoutGrid className="h-3.5 w-3.5" />, count: models.length },
    // 入口显隐按权限码：没 evolution:read 就不渲染这个页签（不造点了必 403 的死入口）
    ...(canViewEvolution ? [{
      key: 'evolution', label: t('k_849e449611'), icon: <Dna className="h-3.5 w-3.5" />,
      count: poolTotal, warn: true,
    }] : []),
  ];

  // 体检问题数：由体检页签上报（本页签未挂载时保留上一次的值，切模型时清零重算）
  const [healthCount, setHealthCount] = useState(0);
  const [healthFocus, setHealthFocus] = useState<{ key: string; tab?: InspTab; seq: number } | null>(null);
  useEffect(() => {
    setHealthCount(0);
    setHealthFocus(null);
  }, [model?.id]);
  const locateFromHealth = useCallback((key: string, tab?: InspTab) => {
    setHealthFocus((f) => ({ key, tab, seq: (f?.seq || 0) + 1 }));
    setTab('hub');
  }, []);

  const railSections: WbRailSection[] = !model ? [{ title: t('k_41e04e5dda'), items: crossItems }] : [
    {
      title: t('k_d2a837c9b1'),
      items: [
        { key: 'hub', label: t('k_539cb3b0a6'), icon: <Layers className="h-3.5 w-3.5" />, count: hubObjects.length },
        { key: 'graph', label: t('k_2ea6798094'), icon: <Share2 className="h-3.5 w-3.5" />, title: t('k_bbd833b001') },
        ...(m234 ? [
          { key: 'm234', label: t('k_c09395949f'), icon: <Zap className="h-3.5 w-3.5" />, count: docCounts.actions },
          { key: 'rules', label: t('k_ed904c685a'), icon: <ScrollText className="h-3.5 w-3.5" />, count: docCounts.rules },
          { key: 'scenarios', label: t('k_625b392c7b'), icon: <Sparkles className="h-3.5 w-3.5" />, count: docCounts.scenarios },
        ] : []),
        // 能力流程（运行台账）：只在系统能力本体视图 + 有 capability:run 时出现
        ...(systemReadOnly && canRunFlows ? [
          { key: 'flows', label: t('k_5d1e9a7c50'), icon: <Workflow className="h-3.5 w-3.5" /> },
        ] : []),
        ...(siteScope ? [{ key: 'sites', label: t('k_c308156eae'), icon: <MapPin className="h-3.5 w-3.5" /> }] : []),
      ],
    },
    {
      title: t('k_cebe02952f'),
      items: [
        { key: 'terms', label: t('k_779b13e3d9'), icon: <BookOpen className="h-3.5 w-3.5" /> },
        { key: 'tags', label: t('k_ae0a7afece'), icon: <Tags className="h-3.5 w-3.5" /> },
      ],
    },
    {
      title: t('k_82f516820c'),
      items: [
        { key: 'health', label: t('k_6dca588fdc'), icon: <Activity className="h-3.5 w-3.5" />, count: healthCount || undefined, warn: healthCount > 0, title: t('k_f06c664875') },
      ],
    },
    {
      title: t('k_82bf9384b6'),
      items: draftPending && !systemReadOnly ? [
        { key: 'changes', label: t('k_8d535108e6'), icon: <GitCompare className="h-3.5 w-3.5" />, count: pendingChangeCount, warn: true },
      ] : [],
    },
    {
      title: t('k_1069127253'),
      items: [
        { key: 'doc', label: t('k_27eb0d14f8'), icon: <FileText className="h-3.5 w-3.5" />, action: true, title: t('k_85ca756d04') },
      ],
    },
    { title: t('k_41e04e5dda'), items: crossItems },
  ];

  const tray: WbTray | null = !systemReadOnly && draftPending ? {
    pending: pendingChangeCount,
    added: draftStatus?.added.length || 0,
    changed: draftStatus?.changed.length || 0,
    removed: draftStatus?.removed.length || 0,
    summary: draftSummary || t('k_f576516de9'),
    // 阻断原因常驻可见：只写在按钮 title 里等于把风险藏起来
    blockHint: draftStatus?.base_stale ? t('k_8b2f6ff8c9') : undefined,
    canPublish: editable && hasPerm('ontology:publish'),
    publishDisabled: publishing || !!draftStatus?.base_stale || !!draftStatus?.draft_parse_error,
    publishTitle: draftStatus?.base_stale
      ? t('k_107765e1d2')
      : draftStatus?.draft_parse_error
        ? t('k_374f7b7caf')
        : t('k_a7711f40b9', { p0: draftSummary || t('k_3f445de47a') }),
    publishing,
    onPublish: handlePublish,
    onViewChanges: () => { setTab('changes'); if (viewMode !== 'draft') void switchView('draft'); },
    // 撤销与「变更对比」里的是同一个动作（同一确认弹窗），生效版/草案视图都可达
    canDiscard: editable && hasPerm('ontology:save'),
    discarding,
    onRequestDiscard: requestDiscardDraft,
  } : null;

  const headTitle = (label: string, count?: number, note?: string) => (
    <div className="flex flex-wrap items-center gap-2">
      <h2 className="text-sm font-semibold">{label}</h2>
      {count !== undefined && <span className="text-xs text-muted-foreground tabular-nums">{count}</span>}
      <div className="flex-1" />
      {note && <span className="text-[11px] text-muted-foreground">{note}</span>}
    </div>
  );

  const hubHead = (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">{t('k_539cb3b0a6')}</h2>
        {/* 当前视图对象数由画布工具条负责（同一数字不在页头重复一份） */}
        <span className="text-[11px] text-muted-foreground">
          {t('k_013a59c4bf', { p0: model?.object_count ?? 0 })}
        </span>
        <div className="flex-1" />
        {canEditDoc && model && (
          <Button size="sm" className="h-7 text-xs" onClick={() => setCreatingObject(true)}>
            <Plus className="h-3.5 w-3.5 mr-1" />{t('k_fab82bea2e')}
          </Button>
        )}
        {liveLocked && editable && (
          <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => void switchView('draft')}>
            <Pencil className="h-3 w-3 mr-1" />{t('k_417166e66b')}
          </Button>
        )}
      </div>
      {systemReadOnly && <div className="text-[11px] text-sky-700">{t('k_system_ontology_readonly')}</div>}
      {liveLocked && <div className="text-[11px] text-muted-foreground">{t('k_4652cc5256')}</div>}
      {viewMode === 'draft' && (
        <div className="text-[11px] text-amber-600">{t('k_4385768c31')}</div>
      )}
    </>
  );

  const canvasHead = activeTab === 'evolution' ? null
    : activeTab === 'health' ? headTitle(t('k_6dca588fdc'), healthCount || undefined, t('k_3e9ea37e19'))
    : activeTab === 'hub' ? hubHead
    : activeTab === 'graph' ? headTitle(t('k_2ea6798094'), undefined, t('k_bbd833b001'))
      : activeTab === 'changes' ? headTitle(t('k_8d535108e6'), pendingChangeCount, t('k_dc832ca790'))
      : activeTab === 'terms' ? headTitle(t('k_779b13e3d9'), undefined, draftHint)
        : activeTab === 'tags' ? headTitle(t('k_ae0a7afece'))
          : activeTab === 'm234' ? headTitle(t('k_c09395949f'), docCounts.actions)
            : activeTab === 'rules' ? headTitle(t('k_ed904c685a'), docCounts.rules)
              : activeTab === 'scenarios' ? headTitle(t('k_625b392c7b'), docCounts.scenarios)
                : activeTab === 'flows' ? headTitle(t('k_5d1e9a7c50'))
                : activeTab === 'sites' ? headTitle(t('k_c308156eae'))
                  : activeTab === 'models' ? headTitle(t('k_688facc5f5'), models.length, t('k_bd92232894'))
                    : null;

  // 全部模型总览：模型 = 领域 = 发布单元，这里只做总览与切换（未选模型时的落地面）
  const modelGrid = (
    <>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {models.map((m) => {
          const meta = STATUS_META[m.status] || STATUS_META.draft;
          return (
            <button key={m.id} type="button"
              onClick={() => { loadModel(m.id); setTab('hub'); }}
              className={`rounded-xl border p-3 text-left transition-colors hover:border-primary/50 hover:bg-muted/30
                ${selectedId === m.id ? 'border-primary bg-primary/5' : 'border-border'}`}>
              <div className="flex items-start justify-between gap-2">
                <span className="min-w-0 truncate text-sm font-medium">{m.name}</span>
                <Badge variant="outline" className={`shrink-0 text-[10px] ${meta.cls}`}>{meta.label}</Badge>
              </div>
              <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{m.domain || t('k_b28f13ea3e')}</div>
              <div className="mt-1 text-[11px] text-muted-foreground">
                {t('k_013a59c4bf', { p0: m.object_count })}
                {' · '}{m.kind === 'system' ? t('k_f6b932be1d') : m.kind === 'source' ? t('k_f5db121235') : t('k_d381fb12f2')}
                {' · '}{m.updated_at?.slice(0, 16).replace('T', ' ')}
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-1">
                {(m.deployment_count ?? 0) >= 2 && (
                  <Badge variant="outline" className="text-[10px] border-indigo-300 text-indigo-600">
                    {t('k_dbd9450a62', { p0: m.deployment_count })}</Badge>
                )}
                {m.has_draft && (
                  <Badge variant="outline" className="text-[10px] border-amber-500/40 text-amber-600">
                    {t('k_0737ae75a2')}</Badge>
                )}
                {selectedId === m.id && <span className="text-[10px] text-primary">{t('k_e18ae87522')}</span>}
              </div>
            </button>
          );
        })}
      </div>
      {!models.length && (
        <div className="py-10 text-center text-sm text-muted-foreground">{t('k_08f1e09ffd')}</div>
      )}
    </>
  );

  const canvas = activeTab === 'health' ? (
    // 建模体检：本模型对象级缺口，逐条「定位」回对象画布就地修
    <div className="h-full p-2">
      <ModelHealthTab modelId={model!.id} objects={hubObjects} issues={objectIssues}
        draftStates={draftStates} modelKind={model!.kind} viewMode={viewMode}
        onLocate={locateFromHealth} onSummary={setHealthCount} />
    </div>
  ) : activeTab === 'evolution' ? (
    // 进化中心（含字典待办）：候选分诊 / 知识更新 / 历史大盘就地处理，不跳独立页（2026-11-21 拍板并入）
    <div className="h-full overflow-auto p-2">
      <EvolutionCenter embedded onOpenModel={() => setTab('hub')} />
    </div>
  ) : activeTab === 'models' ? (
    <div className="h-full overflow-auto p-3">{modelGrid}</div>
  ) : !model ? (
    <div className="h-full overflow-auto p-3">{modelGrid}</div>
  ) : activeTab === 'hub' ? (
    <div className="h-full p-2">
      <ObjectHubTab modelId={model.id} objects={hubObjects} issues={objectIssues}
        jsonContent={activeJson} onSave={saveDocFromTab}
        canEdit={canEditDoc} viewMode={viewMode} draftStates={draftStates}
        draftHint={draftHint} modelKind={model.kind}
        creating={creatingObject}
        onCreateCancel={() => setCreatingObject(false)}
        onCreateDone={() => setCreatingObject(false)}
        focus={healthFocus || undefined} />
    </div>
  ) : activeTab === 'graph' ? (
    // 图谱就在画布里：按当前模型（kind/domain/model_id）取图，不跳外层菜单
    <div className="h-full min-h-0">
      <ModelGraphTab key={`graph-${graphTick}`} datasourceId={model.datasource_id ?? 0} kind={model.kind}
        domain={model.domain} modelId={model.id} fill />
    </div>
  ) : activeTab === 'changes' ? (
    <div className="h-full overflow-auto p-2">
      <ModelDraftReview modelId={model.id} draft={draftStatus}
        canPublish={editable && hasPerm('ontology:publish')}
        canEdit={editable && hasPerm('ontology:save')}
        publishing={publishing}
        discarding={discarding}
        onPublish={handlePublish}
        onRequestDiscard={requestDiscardDraft}
        onEditInDraft={() => void switchView('draft')} />
    </div>
  ) : activeTab === 'terms' ? (
    <div className="h-full overflow-auto p-2">
      <Glossary embedded datasourceId={model.datasource_id}
        tables={hubObjects.map((o: any) => String(o.primary_table || '').toLowerCase()).filter(Boolean)} />
    </div>
  ) : activeTab === 'tags' ? (
    <div className="h-full overflow-auto p-2">
      <ObjectTagsTab objects={hubObjects as any} />
    </div>
  ) : activeTab === 'm234' ? (
    <div className="h-full overflow-hidden p-2">
      <ModelActionsTab jsonContent={activeJson} onSave={saveDocFromTab} readonly={systemReadOnly} />
    </div>
  ) : activeTab === 'rules' ? (
    <div className="h-full overflow-hidden p-2">
      <ModelRulesTab jsonContent={activeJson} onSave={saveDocFromTab} readonly={systemReadOnly} />
    </div>
  ) : activeTab === 'scenarios' ? (
    <div className="h-full overflow-hidden p-2">
      <ModelScenariosTab jsonContent={activeJson} onSave={saveDocFromTab} readonly={systemReadOnly} />
    </div>
  ) : activeTab === 'flows' ? (
    <div className="h-full overflow-hidden">
      <CapabilityFlowsTab />
    </div>
  ) : activeTab === 'sites' ? (
    <div className="h-full overflow-hidden p-2">
      <ModelSitesTab modelId={model.id} readonly={!editable} />
    </div>
  ) : (
    <div className="h-full overflow-auto p-3">{modelGrid}</div>
  );

  return (
    <div className="space-y-4">
      <OntologyWorkbench
        title={t('k_ea36677e56')}
        current={model}
        models={models}
        modelKw={modelKw}
        onModelKw={setModelKw}
        onPickModel={loadModel}
        showArchived={showArchived}
        onShowArchived={(v) => { setShowArchived(v); loadModels(v); }}
        view={{
          mode: viewMode,
          liveCount: draftStatus?.effective_object_count ?? objects.length,
          draftCount: draftStatus?.draft_object_count ?? 0,
          hasDraft: draftPending,
          draftTitle: draftPending
            ? t('k_85f0dcdf2c', {
              p0: draftStatus?.draft_object_count,
              p1: draftStatus?.draft_updated_by || t('k_1622dc9b6b'),
              p2: draftStatus?.draft_updated_at?.slice(0, 16).replace('T', ' ') || '—',
            }) + t('k_3ea200ef76')
            : t('k_30257fb09d'),
          onSwitch: (m) => void switchView(m),
        }}
        tools={(
          <>
            {systemReadOnly && <div className="text-[11px] text-sky-700">{t('k_system_ontology_readonly')}</div>}
            {!systemReadOnly && <>
            <Select value={datasourceId ? String(datasourceId) : ''}
              onValueChange={(v) => setDatasourceId(Number(v))}>
              <SelectTrigger className="h-8 w-56 text-xs">
                <SelectValue placeholder={t('k_422b9e314a')} />
              </SelectTrigger>
              <SelectContent>
                {datasources.map((ds) => (
                  <SelectItem key={ds.id} value={String(ds.id)}>
                    {ds.name}（{ds.db_type || '-'}）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Popover>
              <PopoverTrigger asChild>
                <Button variant="outline" size="sm" className="h-8 font-normal">
                  <BookMarked className="h-3.5 w-3.5 mr-1 text-muted-foreground" />
                  {t('k_66928ed465')}
                  <Badge variant="secondary" className="ml-1.5 text-[10px] px-1.5">
                    {kbIds.length ? t('k_481be38f76', { p0: kbIds.length }) : t('k_55dc6ec014')}
                  </Badge>
                </Button>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-72 p-2">
                <div className="text-xs text-muted-foreground px-1 pb-1.5">{t('k_c4c7395856')}</div>
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
                    <div className="px-1.5 py-1 text-xs text-muted-foreground">{t('k_309f7b3872')}</div>)}
                </div>
              </PopoverContent>
            </Popover>
            </>}
            {/* 生成/进度/取消都按 ontology:generate 裁决（无码不渲染点了必 403 的按钮） */}
            {!systemReadOnly && hasPerm('ontology:generate') && (
              <>
                <Button size="sm" className="h-8" onClick={handleGenerate} disabled={running || !datasourceId}>
                  {running ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <Sparkles className="h-3.5 w-3.5 mr-1" />}
                  {running ? t('k_5e59fed789') : t('k_2113dcf9f2')}
                </Button>
                {running && (
                  <Button size="sm" variant="outline" className="h-8" onClick={() => genTask && openTaskConversation(genTask.conversation_id)}>
                    {t('k_9600c91810')}
                  </Button>
                )}
                {running && (
                  <Button size="sm" variant="ghost" className="h-8 text-red-500 hover:text-red-600" onClick={handleCancelGenerate}>
                    {t('k_d926e2f58e')}
                  </Button>
                )}
              </>
            )}
            {/* 导入 YAML 属独立写码 ontology:import（不能蹭保存码） */}
            {!systemReadOnly && hasPerm('ontology:import') && (
              <Button size="sm" variant="outline" className="h-8" onClick={() => setYamlOpen(true)} disabled={!datasourceId}>
                <Upload className="h-3.5 w-3.5 mr-1" />{t('k_2916bd30b8')}
              </Button>
            )}
            <Button size="sm" variant="outline" className="h-8" onClick={() => loadModels()}>
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
            <div className="flex-1" />
            {model && (model.datasource_id || 0) > 0 && (
              <Badge variant="outline" className="h-8 px-3 text-muted-foreground">{t('k_36e30e1540')}</Badge>
            )}
            {model && (
              <span title={t('k_4af21def1e')}>
                <SyncBadges modelId={model.id} kind={model.kind} refreshKey={graphTick} />
              </span>
            )}
            {/* 图谱重建入口就放在「图谱 未生成 · 需重建」旁边：徽章说缺，按钮给处置（同看同建一处） */}
            {!systemReadOnly && model && (
              <GraphRebuildButton kind={model.kind} datasourceId={model.datasource_id ?? undefined}
                className="h-8" onRebuilt={() => setGraphTick((v) => v + 1)} />
            )}
            {/* 同步知识库与图谱生成同屏：徽章说「待同步」，这里给处置（kb:manage 把关、仅生效模型可点） */}
            {!systemReadOnly && model && (
              <KbSyncButton modelId={model.id} active={model.status === 'active'}
                onSynced={() => setGraphTick((v) => v + 1)} />
            )}
          </>
        )}
        actions={model ? (
          <>
            <Button size="sm" variant="outline" className="h-8" onClick={handleOpenVersions}>
              <History className="h-3.5 w-3.5 mr-1" />{t('k_fe86275d4d')}
            </Button>
            {!systemReadOnly && <>
            {(model.status === 'draft' || model.status === 'archived') && hasPerm('ontology:activate') && (
              <Button size="sm" className="h-8" onClick={() => setActivateOpen(true)}>
                <CheckCircle2 className="h-3.5 w-3.5 mr-1" />{t('k_4366cd37a7')}
              </Button>
            )}
            {model.status === 'archived' && hasPerm('ontology:activate') && (
              <Button size="sm" variant="outline" className="h-8" onClick={handleRestore}>
                <Undo2 className="h-3.5 w-3.5 mr-1" />{t('k_3c01021917')}
              </Button>
            )}
            {model.status === 'active' && hasPerm('ontology:activate') && (
              <Button size="sm" variant="outline" className="h-8" onClick={handleArchive}>
                <Archive className="h-3.5 w-3.5 mr-1" />{t('k_2f51c18f49')}
              </Button>
            )}
            {/* 删除模型在服务端只由 catalog 写域把关（本体码无 DELETE 落点），按同码显隐 */}
            {model.status !== 'active' && hasPerm('catalog:manage') && (
              <Button size="sm" variant="ghost" className="h-8 text-red-500 hover:text-red-600" onClick={handleDelete}
                title={t('k_2f51c18f49')}>
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            )}
            </>}
          </>
        ) : undefined}
        railSections={railSections}
        nav={activeTab}
        onNav={handleNav}
        canvasHead={canvasHead}
        tray={tray}
      >
        {canvas}
      </OntologyWorkbench>

      <CanonicalDocPanel open={docOpen} onOpenChange={setDocOpen} modelId={model?.id} modelName={model?.name}
        modelKind={model?.kind} datasourceId={model?.datasource_id ?? undefined}
        liveVersion={liveDocVersion} draftCount={!systemReadOnly && draftPending ? pendingChangeCount : 0}
        objectOptions={docObjectOptions}
        onOpenGraph={() => { setDocOpen(false); setTab('graph'); }}
        onOpenDraft={() => { setDocOpen(false); setTab('changes'); if (viewMode !== 'draft') void switchView('draft'); }} />

      {/* 撤销草案确认（托盘与「变更对比」共用）：撤销后草案不可恢复，必须显式确认 */}
      <Dialog open={discardOpen} onOpenChange={setDiscardOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t('k_a0eb9acff4')}</DialogTitle></DialogHeader>
          <div className="text-sm text-muted-foreground space-y-2">
            <p>
              {t('k_5d04331aae', {
                p0: draftStatus?.draft_updated_by || t('k_30b927775e'),
                p1: draftStatus?.draft_updated_at?.slice(0, 16).replace('T', ' ') || '—',
                p2: draftStatus?.added.length || 0,
                p3: draftStatus?.changed.length || 0,
                p4: draftStatus?.removed.length || 0,
              })}</p>
            <p className="text-red-600">{t('k_3b3fcfb34d')}</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDiscardOpen(false)} disabled={discarding}>
              {t('k_4d0b4688c7')}</Button>
            <Button variant="destructive" onClick={confirmDiscardDraft} disabled={discarding}>
              {discarding ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Undo2 className="h-4 w-4 mr-1" />}
              {t('k_55bfb65260')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 导入 YAML 弹窗 */}
      <Dialog open={yamlOpen} onOpenChange={setYamlOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t("k_30c443af4b")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <div>
              <label className="text-sm font-medium block mb-1">{t("k_d3ba8eca71")}</label>
              <Input value={yamlDir} onChange={(e) => setYamlDir(e.target.value)}
                placeholder={t('k_7eedd66ca4')} />
              <p className="text-xs text-muted-foreground mt-1">
                {t('k_8a178518df', { p0: datasources.find((d) => d.id === datasourceId)?.name || '-' })}</p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setYamlOpen(false)}>{t("k_4d0b4688c7")}</Button>
            <Button onClick={handleImportYaml} disabled={importing}>
              {importing ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Upload className="h-4 w-4 mr-1" />}
              {importing ? t('k_9b1a865cd8') : t('k_7d2ff42cf9')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 激活确认弹窗 */}
      <Dialog open={activateOpen} onOpenChange={setActivateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("k_717d2e4a5e")}</DialogTitle>
          </DialogHeader>
          <div className="text-sm text-muted-foreground space-y-2">
            <p>{t("k_afc1e1cf46")}</p>
            <ul className="list-disc pl-5 space-y-1">
              <li>{t("k_93bc92d2ab")}</li>
              <li>{t("k_ee23325510")}</li>
              <li>{t('k_c13c1d4d54', { p0: objects.length })}</li>
              <li>{t('k_577cb94670')}</li>
            </ul>
            <p>{t("k_f610b131c2")}</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setActivateOpen(false)} disabled={activating}>
              {t('k_4d0b4688c7')}
            </Button>
            <Button onClick={handleActivate} disabled={activating}>
              {activating && <Loader2 className="h-4 w-4 mr-1 animate-spin" />}
              {t('k_4366cd37a7')}
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
            <DialogTitle>{t('k_d86ec1fbdb', { p0: model?.name })}</DialogTitle>
          </DialogHeader>
          <div className="text-xs text-muted-foreground">
            {t('k_808c6be7af')}
          </div>
          <ScrollArea className="h-[50vh] mt-2">
            {versionsLoading ? (
              <div className="py-6 text-center text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 mr-1 inline animate-spin" />{t('k_300ee3dee4')}
              </div>
            ) : versions.length === 0 ? (
              <div className="py-6 text-center text-sm text-muted-foreground">{t("k_e65efc0072")}</div>
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
                          {t('k_4c2c4be63a', { p0: v.object_count, p1: v.created_by || '—', p2: v.updated_at?.slice(0, 16).replace('T', ' ') })}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => { loadModel(v.id); setVersionsOpen(false); }}
                        >
                          {t('k_f7acefd2d4')}
                        </Button>
                        {!systemReadOnly && v.status !== 'active' && (
                          <Button size="sm" variant="outline" onClick={() => handleRestoreVersion(v.id)}>
                            {t('k_83a991d77c')}
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
            <Button variant="outline" onClick={() => setVersionsOpen(false)}>{t("k_6c14bd7f6f")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
