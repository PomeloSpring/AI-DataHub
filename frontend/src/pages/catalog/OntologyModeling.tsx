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

/** 模型列表分组(业务/系统能力): 只列本体模型 —— 指标/维度/术语是模型内容, 在模型工作区内配置 */
function ModelGroup({ title, list, selectedId, onPick }: {
  title: string;
  list: OntologyModelSummary[];
  selectedId: number | null;
  onPick: (id: number) => void;
}) {
  const { t } = useTranslation('ui');
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
                      {t('k_973147137c', { p0: m.deployment_count })}</Badge>
                  )}
                  <Badge variant="outline" className={`flex-shrink-0 ${meta.cls}`}>
                    {meta.label}
                  </Badge>
                  {/* 有未发布草案：在列表层就提醒“改了还没生效”，不让人到详情页才发现对象不见了 */}
                  {m.has_draft && (
                    <Badge variant="outline" className="flex-shrink-0 border-amber-500/40 text-amber-600">
                      {t('k_0737ae75a2')}
                    </Badge>
                  )}
                </span>
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {t('k_314bbc9736', { p0: m.domain ? `${m.domain} · ` : '', p1: m.object_count, p2: m.updated_at?.slice(0, 16).replace('T', ' ') })}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function OntologyModeling() {
  const { t } = useTranslation('ui');
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
  const saveDocFromTab = useCallback(async (nextJson: string) => {
    if (!model) return;
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
      setTab('changes');
      toast.success(t('k_58272cf7ae'));
    } catch (e: any) {
      toast.error(e.response?.data?.detail || t('k_2281d5a551'));
    } finally {
      setSaving(false);
    }
  }, [model, viewMode, draftPending, loadModels, loadDraftStatus, switchView]);

  // ── 发布（两段式第二段）：生效 + 级联，需 ontology:publish 权限 ──
  const handlePublish = async () => {
    if (!model) return;
    setPublishing(true);
    try {
      const { data } = await ontologyApi.publishDraft(model.id);
      setDraftJson('');
      setTab('hub');
      loadModels();
      // 发布改变了生效版：重拉模型正文（否则对象列表停在发布前的旧数量）与草案状态，并回生效版视图
      await loadModel(model.id);
      // 文案必须与级联真做的事一致：发布刷新对象表/部署绑定/口径字典/图谱；知识库不在这条链上
      // （由后台 kb_sync_reconcile 周期重推，或在本页「关系图谱」手动同步）。以前写成
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

  /** 丢弃草案（已由审阅面板调完 API，这里只负责把视图拉回一致状态） */
  const handleDiscarded = async () => {
    if (!model) return;
    setDraftJson('');
    setTab('hub');
    loadModels();
    await loadModel(model.id);
    toast.success(t('k_19ec113608'));
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
    if (!model) return;
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
    if (!model) return;
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

  // ── 手动同步知识库与图谱重建均在「关系图谱」页（派生重建与状态回显同屏），
  //    本页只保留状态徽章与保存/激活的自动级联。

  const handleDelete = async () => {
    if (!model) return;
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
  const draftHint = t('k_f8f48c2e2b');
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
          <h1 className="text-lg font-semibold">{t("k_ea36677e56")}</h1>
        </div>
        <div className="flex-1" />
        <Select
          value={datasourceId ? String(datasourceId) : ''}
          onValueChange={(v) => setDatasourceId(Number(v))}
        >
          <SelectTrigger className="w-56">
            <SelectValue placeholder={t("k_422b9e314a")} />
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
              {t('k_66928ed465')}
              <Badge variant="secondary" className="ml-1.5 text-[10px] px-1.5">
                {kbIds.length ? t('k_481be38f76', { p0: kbIds.length }) : t('k_55dc6ec014')}
              </Badge>
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72 p-2">
            <div className="text-xs text-muted-foreground px-1 pb-1.5">
              {t('k_c4c7395856')}
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
                <div className="px-1.5 py-1 text-xs text-muted-foreground">{t("k_309f7b3872")}</div>)}
            </div>
          </PopoverContent>
        </Popover>
        <Button onClick={handleGenerate} disabled={running || !datasourceId}>
          {running ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Sparkles className="h-4 w-4 mr-1" />}
          {running ? t('k_5e59fed789') : t('k_2113dcf9f2')}
        </Button>
        {running && (
          <Button variant="outline" onClick={() => genTask && openTaskConversation(genTask.conversation_id)}>
            {t('k_9600c91810')}
          </Button>
        )}
        {running && (
          <Button variant="ghost" className="text-red-500 hover:text-red-600" onClick={handleCancelGenerate}>
            {t('k_d926e2f58e')}
          </Button>
        )}
        <Button variant="outline" onClick={() => setYamlOpen(true)} disabled={!datasourceId}>
          <Upload className="h-4 w-4 mr-1" />{t('k_2916bd30b8')}
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
              <CardTitle className="text-sm">{t('k_7f3b6807d1', { p0: models.length })}</CardTitle>
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                <Switch checked={showArchived} onCheckedChange={(v) => {
                  setShowArchived(v);
                  loadModels(v);
                }} />
                {t('k_494afe2b9b')}
              </label>
            </div>
            <div className="relative mt-2">
              <Search className="h-3.5 w-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <Input value={modelKw} onChange={(e) => setModelKw(e.target.value)}
                placeholder={t("k_f23093e437")} className="h-8 pl-7 text-xs" />
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {models.length === 0 && (
              <div className="text-xs text-muted-foreground py-4 text-center">
                {t('k_08f1e09ffd')}
              </div>
            )}
            {models.length > 0 && !filteredBizModels.length && !filteredSourceModels.length
              && !filteredSysModels.length && !filteredLegacyModels.length && (
              <div className="text-xs text-muted-foreground py-3 text-center">{t('k_ab2ff2ce5f', { p0: modelKw })}</div>
            )}
            <ModelGroup
              title={t("k_d381fb12f2")} list={filteredBizModels}
              selectedId={selectedId} onPick={loadModel}
            />
            <ModelGroup
              title={t("k_f5db121235")} list={filteredSourceModels}
              selectedId={selectedId} onPick={loadModel}
            />
            <ModelGroup
              title={t("k_f6b932be1d")} list={filteredSysModels}
              selectedId={selectedId} onPick={loadModel}
            />
            <ModelGroup
              title={t("k_b28f13ea3e")} list={filteredLegacyModels}
              selectedId={selectedId} onPick={loadModel}
            />

            {/* 建模待办：已抽为独立页（不属于某个模型的全局缺口），本页只留入口与计数 */}
            <button
              onClick={() => navigate('/data/asset-pool')}
              className="w-full text-left rounded-lg border border-dashed px-3 py-2 text-sm transition-colors hover:bg-muted/50"
            >
              <div className="flex items-center gap-2">
                <Inbox className="h-4 w-4 text-amber-500" />
                {t('k_0f31c91cb2')}
                {poolTotal > 0 && (
                  <Badge variant="outline" className="ml-auto text-amber-500 border-amber-500/40">{t('k_397dbb2060', { p0: poolTotal })}</Badge>
                )}
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">{t("k_4cd3a552c0")}</div>
            </button>
          </CardContent>
        </Card>

        {/* 右主区：模型工作区（生效版/草案 双视图 + 变更对比） */}
        <Card className="xl:col-span-3">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
                {model ? model.name : t('k_4fa8c1a36b')}
                {model && (
                  <Badge variant="outline" className={`text-[10px] font-normal ${STATUS_META[model.status]?.cls}`}>
                    {STATUS_META[model.status]?.label}
                  </Badge>
                )}
                {model && viewMode === 'draft' && (
                  <Badge variant="outline" className="text-[10px] font-normal border-amber-500/40 text-amber-600">
                    {t('k_ce96c43684')}
                  </Badge>
                )}
                {saving && <span className="text-xs text-muted-foreground">{t("k_8dd685ca90")}</span>}
              </CardTitle>
              {model && (
                <div className="flex items-center gap-2 flex-wrap">
                  {/* 视图切换：生效版（线上只读）↔ 草案（可编辑）。
                      这就是“有存草案按钮却没有编辑草案的地方”的答案：草案本身就是工作区的一个视图。 */}
                  <div className="inline-flex items-center rounded-md border border-border bg-muted/40 p-0.5">
                    <button
                      onClick={() => switchView('live')}
                      title={t("k_2f0436bcbf")}
                      className={`h-7 rounded px-2.5 text-xs transition-colors ${
                        viewMode === 'live' ? 'bg-background shadow-sm font-medium' : 'text-muted-foreground hover:text-foreground'}`}
                    >
                      {t('k_3a49375c2d', { p0: draftStatus?.effective_object_count ?? objects.length })}
                    </button>
                    <button
                      onClick={() => switchView('draft')}
                      disabled={!draftPending}
                      title={draftPending
                        ? t('k_85f0dcdf2c', { p0: draftStatus?.draft_object_count, p1: draftStatus?.draft_updated_by || t('k_1622dc9b6b'), p2: draftStatus?.draft_updated_at?.slice(0, 16).replace('T', ' ') || '—' }) + t('k_3ea200ef76')
                        : t('k_30257fb09d')}
                      className={`h-7 rounded px-2.5 text-xs inline-flex items-center gap-1 transition-colors ${
                        !draftPending ? 'text-muted-foreground/50 cursor-not-allowed'
                          : viewMode === 'draft' ? 'bg-amber-500/15 text-amber-700 shadow-sm font-medium'
                            : 'text-amber-600 hover:bg-amber-500/10'}`}
                    >
                      {t('k_2b37a18790', { p0: draftStatus?.draft_object_count ?? 0 })}
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
                      {t('k_36e30e1540')}
                    </Badge>
                  )}
                  <span title={t("k_4af21def1e")}>
                    <SyncBadges modelId={model.id} kind={model.kind} />
                  </span>
                  <Button size="sm" variant="outline" onClick={handleOpenVersions}>
                    <History className="h-3.5 w-3.5 mr-1" />
                    {t('k_fe86275d4d')}
                  </Button>
                  {/* 发布 = 两段式第二段：按 ontology:publish 显隐（无码不渲染，不给死开关）；
                      可用性由服务端草案状态裁决，AS-BOT 写的草案同样可发 */}
                  {draftPending && editable && hasPerm('ontology:publish') && (
                    <Button size="sm" variant="default" onClick={handlePublish}
                      disabled={publishing || !!draftStatus?.base_stale || !!draftStatus?.draft_parse_error}
                      title={draftStatus?.base_stale
                        ? t('k_107765e1d2')
                        : draftStatus?.draft_parse_error
                          ? t('k_374f7b7caf')
                          : t('k_a7711f40b9', { p0: draftSummary || t('k_3f445de47a') })}
                    >
                      {publishing ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5 mr-1" />}
                      {t('k_d4768779cc', { p0: pendingChangeCount })}
                    </Button>
                  )}
                  {(model.status === 'draft' || model.status === 'archived') && (
                    <Button size="sm" variant="default" onClick={() => setActivateOpen(true)}>
                      <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
                      {t('k_4366cd37a7')}
                    </Button>
                  )}
                  {model.status === 'archived' && (
                    <Button size="sm" variant="outline" onClick={handleRestore}>
                      <Undo2 className="h-3.5 w-3.5 mr-1" />
                      {t('k_3c01021917')}
                    </Button>
                  )}
                  {model.status === 'active' && (
                    <Button size="sm" variant="outline" onClick={handleArchive}>
                      <Archive className="h-3.5 w-3.5 mr-1" />
                      {t('k_2f51c18f49')}
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
                      ? t('k_f35f5fb7ff', { p0: draftStatus?.draft_object_count })
                      : t('k_ccc18bda24', { p0: pendingChangeCount, p1: draftStatus?.effective_object_count, p2: draftStatus?.draft_object_count })}
                  </div>
                  <div className="text-muted-foreground">
                    {draftSummary || t('k_f576516de9')}
                    {' · '}{t('k_08445af37d', { p0: draftStatus?.draft_updated_by || t('k_1622dc9b6b'), p1: draftStatus?.draft_updated_at?.slice(0, 16).replace('T', ' ') || '—' })}
                    {draftStatus?.base_stale && (
                      <span className="ml-1 text-red-600">{t("k_8b2f6ff8c9")}</span>
                    )}
                  </div>
                  {viewMode === 'live' && (
                    <div className="text-muted-foreground">
                      {t('k_c2e9e0351c')}
                    </div>
                  )}
                </div>
                <div className="ml-auto flex items-center gap-2 shrink-0">
                  <Button size="sm" variant="outline" className="h-7 text-xs"
                    onClick={() => { setTab('changes'); if (viewMode !== 'draft') void switchView('draft'); }}>
                    <GitCompare className="h-3 w-3 mr-1" />{t('k_8d535108e6')}
                  </Button>
                  {viewMode === 'live' ? (
                    <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => void switchView('draft')}>
                      <Eye className="h-3 w-3 mr-1" />{t('k_983646f300')}
                    </Button>
                  ) : (
                    <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => void switchView('live')}>
                      {t('k_a2fe5d62d8')}
                    </Button>
                  )}
                </div>
              </div>
            )}
            {model && (
              <div className="text-xs text-muted-foreground mt-2">
                {viewMode === 'draft'
                  ? t('k_4385768c31')
                  : model.status === 'archived'
                    ? t('k_256ef3f80b')
                    : draftPending
                      ? t('k_4652cc5256')
                      : t('k_1dc730d887')}
              </div>
            )}
          </CardHeader>
          <CardContent>
            {!model ? (
              <div className="text-sm text-muted-foreground py-12 text-center">
                {t('k_774903319b')}
              </div>
            ) : (
              <Tabs key={model.id} value={tab} onValueChange={setTab}>
                <TabsList>
                  <TabsTrigger value="hub">{t("k_27424f442a")}</TabsTrigger>
                  {draftPending && (
                    <TabsTrigger value="changes">
                      {t('k_8d535108e6')}
                      <Badge variant="outline" className="ml-1 text-[10px] px-1 h-4 border-amber-500/40 text-amber-600">
                        {pendingChangeCount}
                      </Badge>
                    </TabsTrigger>
                  )}
                  <TabsTrigger value="terms">{t("k_779b13e3d9")}</TabsTrigger>
                  <TabsTrigger value="tags">{t("k_ae0a7afece")}</TabsTrigger>
                  {showsM234(model.kind) && <TabsTrigger value="m234">{t("k_c09395949f")}</TabsTrigger>}
                  {showsM234(model.kind) && <TabsTrigger value="rules">{t("k_ed904c685a")}</TabsTrigger>}
                  {showsM234(model.kind) && <TabsTrigger value="scenarios">{t("k_625b392c7b")}</TabsTrigger>}
                  {showsSiteScope(model.kind) && <TabsTrigger value="sites">{t("k_c308156eae")}</TabsTrigger>}
                </TabsList>
                {hiddenM234 && (
                  <div className="mt-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-1.5 text-[11px] text-amber-600">
                    <AlertTriangle className="h-3 w-3 mr-1 inline" />
                    {t('k_9a994413e8', { p0: model.kind === 'source' ? t('k_51ff5d00ed') : t('k_581cece70a'), p1: m234Declared })}
                  </div>
                )}
                <TabsContent value="hub" className="mt-2 h-[62vh] overflow-hidden">
                  <div className="flex items-center gap-2 mb-2">
                    {canEditDoc && (
                      <ObjectCreateButton jsonContent={activeJson} onSave={saveDocFromTab}
                        modelKind={model.kind} />
                    )}
                    {liveLocked && editable && (
                      <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => void switchView('draft')}>
                        <Pencil className="h-3 w-3 mr-1" />{t('k_417166e66b')}
                      </Button>
                    )}
                    {viewMode === 'draft' && (
                      <span className="text-[11px] text-muted-foreground">
                        {t('k_3a1c86f9b5')}
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
                        {v.status !== 'active' && (
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
