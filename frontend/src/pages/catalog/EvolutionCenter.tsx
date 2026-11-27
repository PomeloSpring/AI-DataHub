/**
 * EvolutionCenter — 自进化闭环的人工审核台。
 *
 * 归属（2026-11-21 拍板）：**「本体建模」页的 evolution 页签**（替换原「建模待办」位置），
 * 侧栏不再单列入口（/data/evolution 仅保留重定向）。它是语义资产的**生产与验收面**
 * （候选 → 别名/指标/维度/术语 → verified/stale），与本体建模、指标与维度同族；
 * 系统配置只放 AI 模板/凭据/运维，不放资产生产。候选分诊只在本页一处，
 * 字典缺口（未归属/孤儿指标维度、未绑定术语）收进第 5 个页签「字典待办」。
 *
 * 一页看完五件事，缺一样这个闭环就不成立：
 *  1. 反馈大盘：赞/踩与待归纳负反馈有多少（采集不上来，后面全是空谈）；
 *  2. 候选队列：按处置方向分列（待开发指标/维度、待收编别名、待核绑定、术语），
 *     每条都显示**分类依据**与证据问句 —— 不显示依据的候选等于让人猜；
 *  3. **知识更新**：Chat 里由 LLM 或人工提出的"应该改什么"，落到知识库文档 /
 *     分析场景 / 本体对象说明三处。它只是备忘录，写入一律由人在这里用**目标域权限**
 *     执行（文档 kbdoc:write、场景 knowledge:manage、本体 ontology:save，发布另需
 *     ontology:publish）—— 提议本身永远不会自己改资产；
 *  4. 闭环成效：已转出的用例是真过了（verified）还是仍失败（stale）。
 *     "转出去了"不等于"修好了"，stale 必须红着显示，否则闭环自欺；
 *  5. **字典待办**：`bound_object_key` 为空/指向已归档对象的指标与维度、未绑定术语 ——
 *     出口回字典（归属/删除）与术语表，与候选分诊分属两个面（候选只在上面的待建/核绑定）。
 *
 * P2（2026-11-21）：候选进**按处置成本分档的队列**（D7），并带一条可审计的处置建议。
 * 判定与门槛的**唯一真源在服务端**（`evolution_service.suggest_action`，内部复用
 * `classify_term`）。前端只按 `suggestion.queue/tier` 渲染，不在本地另算一套
 * （R7：审核面不得长为第二套分类器，两处口径必然漂移）。
 *
 * 评测出口已下线（用户 2026-11-21 拍板"先不要管评测用例"）：后端的 `to-eval-case`
 * 端点保留，界面不再给入口 —— 「核绑定」页签现在指向「本体建模」核对绑定，
 * 而不是把绑定问题固化成一条注定还会失败的用例。
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { PaginationBar, DEFAULT_PAGE_SIZE } from '@/components/PaginationBar';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from '@/lib/toast';
import { Dna, RefreshCw, Play, Check, X, ExternalLink } from 'lucide-react';
import client from '@/api/client';
import { useHasPerm } from '@/stores/permissionStore';
import { useChatStore } from '@/stores/chatStore';
import { useTranslation } from 'react-i18next';
import { t as i18nTr, t as tr } from '@/i18n/runtime';
import ScenarioEditorDialog, {
  useScenarioFormOptions, type ScenarioPrefill,
} from '@/components/ScenarioEditorDialog';
import DictTodoTab from './DictTodoTab';
import KnowledgeUpdateDialog, { type KnowledgeProposal } from './evolution/KnowledgeUpdateDialog';

type Kind = 'new_metric' | 'new_dimension' | 'alias' | 'binding_gap' | 'term';

/** D7 队列：build=待建（收编/术语/新建）· binding=核绑定（已建档但取数不可达）。 */
type Queue = 'build' | 'binding';
/** 行内建议档位：act 可一键 · recommend 推荐建 · observe 待观察 · verify 核绑定排查。 */
type Tier = 'act' | 'recommend' | 'observe' | 'verify';

/** 服务端给的处置建议（唯一判据在 evolution_service.suggest_action）。 */
interface Suggestion {
  suggested_kind: Kind;
  queue: Queue;
  tier: Tier;
  confidence: 'high' | 'medium' | 'low';
  ambiguous: boolean;
  /** 落库 kind 与按当前证据重算的 kind 不一致：建议仍按重算给，但必须人复核。 */
  drift: boolean;
  hit_count: number;
  prefilled: {
    target_type?: string; target_ref?: string; display_name?: string;
    agg_type?: string; description?: string;
  };
  suggested_refs: string[];
  basis: string;
  classify_reason: string;
}

interface Candidate {
  id: number;
  term: string;
  kind: Kind;
  source: string;
  datasource_name: string;
  object_hint?: string;
  target_ref?: string;
  target_type?: string;
  classify_reason: string;
  hit_count: number;
  status: string;
  /** 决策版本：POST .../decisions 的 expected_version 来源（服务端条件更新仲裁）。 */
  expected_version?: number;
  converted_ref?: string;
  converted_kind?: string;
  decided_by?: string;
  decided_at?: string;
  decision_note?: string;
  evidence?: Record<string, unknown>;
  /** 仅 status=pending 时由服务端附上（已处置行再给建议会被误读成还有事要做）。 */
  suggestion?: Suggestion;
}

interface Overview {
  pending_by_kind: Record<string, number>;
  pending_by_source: Record<string, number>;
  proposal_counts: Record<string, number>;
  outcome: Record<string, number>;
  feedback: { up: number; down: number; pending_negative: number };
  reasons: string[];
  kinds: Kind[];
  sources: string[];
}

const KIND_LABEL: Record<Kind, string> = {
  new_metric: i18nTr('k_70ecbecf9e'),
  new_dimension: i18nTr('k_7386b3b37e'),
  alias: i18nTr('k_48f08d7815'),
  binding_gap: i18nTr('k_ab33ea20aa'),
  term: i18nTr('k_17ad7ca811'),
};

const KIND_HINT: Record<Kind, string> = {
  new_metric: i18nTr('k_acd1648500'),
  new_dimension: i18nTr('k_f4db52f0f9'),
  alias: i18nTr('k_9f522f0895'),
  binding_gap: i18nTr('k_6ded9d5bbc'),
  term: i18nTr('k_1ae14b5a15'),
};

const SOURCE_LABEL: Record<string, string> = {
  parse_fail: i18nTr('k_1ed09ddaab'),
  feedback: i18nTr('k_55f66363c6'),
  script: i18nTr('k_42684640cd'),
};

const STATUS_LABEL: Record<string, string> = {
  pending: i18nTr('k_047109def4'), converted: i18nTr('k_c6bc4767c4'), rejected: i18nTr('k_dbf36ddc55'),
  stale: i18nTr('k_512fdab3f8'), verified: i18nTr('k_579bd49bc6'), expired: i18nTr('k_5d1e9a7c4d'),
};

// 分组顺序 = 处置成本，与 evolution_service.KIND_WEIGHT 同序（alias 一键在前）。
// 后端已按 kind 权重排好整串，组内顺序由这里决定，两处不一致会把"便宜的先做"打乱。
const KIND_ORDER: Kind[] = ['alias', 'term', 'new_metric', 'new_dimension', 'binding_gap'];

const TIER_LABEL: Record<Tier, string> = {
  act: i18nTr('k_786cc5f0b1'),
  recommend: i18nTr('k_cb3cd14fb6'),
  observe: i18nTr('k_27b347033f'),
  verify: i18nTr('k_e0730d5ac1'),
};

const TIER_BADGE: Record<Tier, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  act: 'secondary', recommend: 'default', observe: 'outline', verify: 'outline',
};

/** 知识更新提议的目标类型 / 来源 / 状态（文案与后端枚举一一对应）。 */
const TARGET_LABEL: Record<string, string> = {
  kb_doc: i18nTr('k_e08c3272fc'),
  scenario: i18nTr('k_b2be89d785'),
  ontology_object: i18nTr('k_e5b5171f27'),
};

const PROPOSAL_SOURCE_LABEL: Record<string, string> = {
  chat: i18nTr('k_06e7b9cbcc'),
  manual: i18nTr('k_34211bce9d'),
  evolution: i18nTr('k_d1f867a7a5'),
};

const PROPOSAL_STATUS_LABEL: Record<string, string> = {
  pending: i18nTr('k_047109def4'),
  applied: i18nTr('k_5ad6056a2f'),
  rejected: i18nTr('k_dbf36ddc55'),
  superseded: 'superseded',
};

/** 校验接口返回结构：代理未配时 vite 会把请求兑底成 index.html，
 *  `data.items || []` 会把故障掩盖成"空列表"，这里显式抛错。 */
function expectItems(payload: any, what: string): any[] {
  if (!payload || !Array.isArray(payload.items)) {
    throw new Error(tr('k_247ea64781', { p0: typeof payload, p1: what }));
  }
  return payload.items;
}

export default function EvolutionCenter({ embedded = false, onOpenModel }: {
  /** 内嵌在「本体建模」页画布里（2026-11-21 拍板）：去掉页面级 padding 与大标题，只留工具条 */
  embedded?: boolean;
  /** 「去本体建模核对绑定」出口：内嵌时由外层切页签，独立访问时退回路由跳转 */
  onOpenModel?: () => void;
} = {}) {
  const { t } = useTranslation('ui');
  const navigate = useNavigate();
  // 写出口各自按权限码显隐：不渲染"点了必 403"的按钮（ontology-modeling §9.2）。
  const canSaveOntology = useHasPerm('ontology:save');
  const canManageMetrics = useHasPerm('metrics:manage');
  const canManageKnowledge = useHasPerm('knowledge:manage');
  const canDigest = useHasPerm('evolution:manage');

  // ── 筛选状态全部入 URL query（P1-1）：刷新/返回/分享不丢现场；
  //    任一筛选变化回第 1 页；搜索防抖后下推服务端（不在前端 filter/slice 全量）。
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get('tab') || 'build';
  const historyStatus = searchParams.get('hstatus') || 'converted';
  const kindFilter = searchParams.get('kind') || '';
  const proposalStatus = searchParams.get('pstatus') || 'pending';
  const q = searchParams.get('q') || '';
  const page = Math.max(1, parseInt(searchParams.get('page') || '1', 10) || 1);
  const pageSize = parseInt(searchParams.get('psize') || '', 10) === 50 ? 50 : DEFAULT_PAGE_SIZE;
  const setParams = useCallback((patch: Record<string, string>, keepPage = false) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    if (!keepPage) next.delete('page');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);
  const setTab = (v: string) => setParams({ tab: v });
  const setKindFilter = (v: string) => setParams({ kind: v });
  const setHistoryStatus = (v: string) => setParams({ hstatus: v });
  const setProposalStatus = (v: string) => setParams({ pstatus: v });
  const setPage = (v: number) => setParams({ page: String(v) }, true);
  const setPageSize = (v: number) => setParams({ psize: String(v) });
  // 搜索防抖（300ms）：停手才发请求，改搜索回第 1 页（停在旧页码 + 新关键词会拿到错位的空清单）
  const [qInput, setQInput] = useState(q);
  useEffect(() => {
    const timer = setTimeout(() => {
      if (qInput.trim() !== q) setParams({ q: qInput.trim() });
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qInput]);
  const [items, setItems] = useState<Candidate[]>([]);
  const [total, setTotal] = useState(0);
  const [ov, setOv] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [digesting, setDigesting] = useState(false);

  // 知识更新提议：服务端按状态/关键词真分页（P1-2）；跨页签计数用响应里的 counts
  const [proposals, setProposals] = useState<KnowledgeProposal[]>([]);
  const [propTotal, setPropTotal] = useState(0);
  const [propCounts, setPropCounts] = useState<Record<string, number> | null>(null);
  const [proposalsLoading, setProposalsLoading] = useState(false);
  const [proposalsError, setProposalsError] = useState('');
  const [expanded, setExpanded] = useState<number[]>([]);

  // 出口对话框
  const [target, setTarget] = useState<Candidate | null>(null);
  const [exitKind, setExitKind] = useState<'' | 'alias' | 'metric' | 'dimension' | 'term'>('');
  const [busy, setBusy] = useState(false);
  // 数据源不从选择框选：候选自带 datasource_name（来自解析失败时的会话源或反馈 tags），
  // 让人重选一次反而会造成"候选归属"与"审核人随手选"两个口径。
  const [objOptions, setObjOptions] = useState<{ key: string; display_name: string }[]>([]);
  const [propOptions, setPropOptions] = useState<{ property: string; column_name: string }[]>([]);
  // 别名收编目标（既有字典行/对象）：从清单选，不让人手输（ui-resource-display §2）。
  const [targetOptions, setTargetOptions] = useState<{ key: string; display_name: string }[]>([]);
  const [form, setForm] = useState<any>({});

  // 知识更新处置：三分支各自的入口与「标记 applied」编排
  const [activeProposal, setActiveProposal] = useState<KnowledgeProposal | null>(null);
  /** 从提议进入场景编辑时暂存的提议（保存成功后自动标记已处理） */
  const [proposalForScenario, setProposalForScenario] = useState<KnowledgeProposal | null>(null);
  const [rejectTarget, setRejectTarget] = useState<KnowledgeProposal | null>(null);
  const [rejectCandidate, setRejectCandidate] = useState<Candidate | null>(null);
  const [rejectNote, setRejectNote] = useState('');
  const [scenarioOpen, setScenarioOpen] = useState(false);
  const [scenarioAppend, setScenarioAppend] = useState(false);
  const [scenarioPrefill, setScenarioPrefill] = useState<ScenarioPrefill | null>(null);
  const scenarioOptions = useScenarioFormOptions();

  const load = useCallback(async () => {
    if (tab === 'dict' || tab === 'knowledge' || tab === 'outcome') {
      // 字典待办自带服务端分页，知识更新单独查；候选队列只在相关页签拉取
      return;
    }
    setLoading(true);
    setError('');
    try {
      const isHistory = tab === 'history';
      const { data } = await client.get('/evolution/candidates', {
        params: {
          status: isHistory ? historyStatus : 'pending',
          // 队列分流由服务端完成（queue 由 kind 的服务端映射生成，不在前端二次计算）
          ...(isHistory ? {} : { queue: tab === 'binding' ? 'binding' : 'build' }),
          ...(kindFilter ? { kind: kindFilter } : {}),
          ...(q ? { q } : {}),
          page, page_size: pageSize,
        },
      });
      setItems(expectItems(data, tr('k_ea540c8bf9')));
      setTotal(Number(data?.total) || 0);
    } catch (e: any) {
      const msg = e.message || e.response?.data?.detail || tr('k_22889ef6db');
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [tab, historyStatus, kindFilter, q, page, pageSize]);

  const loadOverview = useCallback(async () => {
    try {
      const { data } = await client.get('/evolution/overview');
      if (!data || typeof data.pending_by_kind !== 'object') {
        throw new Error(tr('k_e0f640a638'));
      }
      setOv(data);
    } catch (e: any) {
      toast.error(e.message || e.response?.data?.detail || t('k_3b2ade9517'));
    }
  }, []);

  const loadProposals = useCallback(async () => {
    if (tab !== 'knowledge' && tab !== 'outcome') return;
    setProposalsLoading(true);
    setProposalsError('');
    try {
      const { data } = await client.get('/evolution/proposals', {
        params: {
          status: proposalStatus, ...(q ? { q } : {}),
          page, page_size: pageSize,
        },
      });
      setProposals(expectItems(data, t('k_59b655627c')));
      setPropTotal(Number(data?.total) || 0);
      setPropCounts(data?.counts || null);
    } catch (e: any) {
      const msg = e.response?.data?.detail || e.message || t('k_866b795eae');
      setProposalsError(msg);
    } finally {
      setProposalsLoading(false);
    }
  }, [t, tab, proposalStatus, q, page, pageSize]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadOverview(); loadProposals(); }, [loadOverview, loadProposals]);

  const runDigest = async () => {
    setDigesting(true);
    try {
      // 异步作业：提交即返回 job_id，轮询状态；失败必须显式（“没有新待办”和“作业挂了”是两件事）
      const { data } = await client.post('/evolution/digests');
      const jobId = data?.job_id;
      toast.info(tr('k_5d1e9a7c3b', { p0: jobId }));
      for (let i = 0; i < 60; i += 1) {
        await new Promise(r => setTimeout(r, 500));
        const { data: job } = await client.get(`/evolution/digests/${jobId}`);
        if (job?.status === 'succeeded') {
          toast.success(t('k_b280571b32', { p0: job?.result?.summary || tr('k_44f7ba5fa8') }));
          break;
        }
        if (job?.status === 'failed') {
          toast.error(tr('k_5d1e9a7c3d', { p0: job?.error_message || t('k_d10b113b12') }));
          break;
        }
        if (i === 59) toast.info(tr('k_5d1e9a7c3c'));
      }
      load(); loadOverview();
    } catch (e: any) {
      toast.error(e.response?.data?.detail
        || tr('k_5d1e9a7c3d', { p0: e.message || t('k_d10b113b12') }));
    } finally {
      setDigesting(false);
    }
  };

  /** 处置统一走 POST .../decisions（版本 + 幂等键 subject:version:action），HTTP/AI 同链。 */
  const postDecision = async (
    subjectType: 'candidates' | 'proposals', id: number,
    body: { action_key: string; expected_version: number; note?: string; applied_ref?: string; input?: any },
  ) => {
    const version = body.expected_version;
    const { data } = await client.post(`/evolution/${subjectType}/${id}/decisions`, body, {
      headers: { 'Idempotency-Key': `${subjectType}:${id}:${version}:${body.action_key}` },
    });
    return data;
  };

  /** 409 = 被并发改过/幂等键撞车：不覆盖既有结论，刷新后按新版本重新处置。 */
  const handleDecisionError = (e: any) => {
    if (e.response?.status === 409) {
      toast.warning(tr('k_5d1e9a7c3e'));
      load(); loadOverview(); loadProposals();
      return;
    }
    toast.error(e.response?.data?.detail || e.message || t('k_3e20d5d1ce'));
  };

  /**
   * 标记提议已处理。失败不能静默：写目标资产可能已经成功，只是这一步没落库 ——
   * 用户必须知道"东西改了但待办还挂着"，否则同一件事会被做第二遍。
   */
  const markApplied = async (proposal: KnowledgeProposal, ref: string) => {
    try {
      const data = await postDecision('proposals', proposal.id, {
        action_key: 'evolution_proposal.applied',
        expected_version: proposal.expected_version || 1,
        applied_ref: ref,
      });
      if (data?.changed) toast.success(t('k_665ffef5b8'));
      else toast.warning(data?.message || t('k_71c30fa869'));
      loadProposals();
    } catch (e: any) {
      if (e.response?.status === 409) { handleDecisionError(e); return; }
      toast.error(`${t('k_2969373217')}（${e.response?.data?.detail || e.message || ''}）`);
    }
  };

  const rejectProposal = async () => {
    if (!rejectTarget) return;
    if (!rejectNote.trim()) { toast.error(tr('k_5d1e9a7c41')); return; }
    setBusy(true);
    try {
      const data = await postDecision('proposals', rejectTarget.id, {
        action_key: 'evolution_proposal.reject',
        expected_version: rejectTarget.expected_version || 1,
        note: rejectNote,
      });
      if (data?.changed) toast.success(t('k_dbf36ddc55'));
      else toast.warning(data?.message || t('k_71c30fa869'));
      setRejectTarget(null);
      setRejectNote('');
      loadProposals();
    } catch (e: any) {
      handleDecisionError(e);
    } finally {
      setBusy(false);
    }
  };

  /** 打开会话：会话 id 经 sessionStorage 一次性携带（与 AS-BOT 面板同惯例）。 */
  const openConversation = (convId: number) => {
    if (!convId) return;
    sessionStorage.setItem('asbot_open_conversation', String(convId));
    const wsId = useChatStore.getState().selectedWorkspaceId || 0;
    navigate(wsId ? `/ws/${wsId}/chat` : '/ask');
  };

  /** 载入收编目标清单（显示业务名、value 用 name/key）。目标类型切换必须重载。 */
  const loadTargets = async (targetType: string, datasourceName: string) => {
    setTargetOptions([]);
    try {
      const { data } = await client.get('/evolution/targets', {
        params: { target_type: targetType, datasource_name: datasourceName || '' },
      });
      setTargetOptions(expectItems(data, tr('k_4d9054b950')));
    } catch (e: any) {
      toast.error(e.message || e.response?.data?.detail || tr('k_9aa34a93cb'));
    }
  };

  const openExit = async (c: Candidate, exit: typeof exitKind) => {
    setTarget(c); setExitKind(exit); setObjOptions([]); setPropOptions([]);
    setTargetOptions([]);
    // 预填一律取服务端建议（不在这里重算判据）；无建议时回退到候选自带字段。
    const p = c.suggestion?.prefilled || {};
    setForm({
      target_type: p.target_type || c.target_type || (c.kind === 'new_metric' ? 'metric' : 'dimension'),
      target_ref: p.target_ref || c.target_ref || '',
      datasource_name: c.datasource_name || '',
      bound_object_key: c.object_hint || '',
      property_name: '', display_name: p.display_name || c.term, formula: '',
      agg_type: p.agg_type || 'COUNT', unit: '',
      category: '属性',
      note: p.description || t('k_dada6897bf', { p0: c.id, p1: c.term }),
    });
    if (exit === 'alias') {
      void loadTargets(p.target_type || c.target_type || 'dimension', c.datasource_name || '');
    }
    if (exit === 'metric' || exit === 'dimension') {
      try {
        const { data } = await client.get('/evolution/objects', {
          params: { datasource_name: c.datasource_name || '' },
        });
        setObjOptions(expectItems(data, tr('k_4d9054b950')));
      } catch (e: any) {
        toast.error(e.message || e.response?.data?.detail || tr('k_9aa34a93cb'));
      }
    }
  };

  const loadProps = async (objectKey: string) => {
    setForm((f: any) => ({ ...f, bound_object_key: objectKey, property_name: '' }));
    setPropOptions([]);
    if (!objectKey || !target) return;
    try {
      const { data } = await client.get('/evolution/properties', {
        params: { datasource_name: target.datasource_name || '', object_key: objectKey },
      });
      setPropOptions(expectItems(data, tr('k_eb47890f6b')));
    } catch (e: any) {
      toast.error(e.message || t('k_b33fcc9c4c'));
    }
  };

  const submit = async () => {
    if (!target) return;
    setBusy(true);
    try {
      const version = target.expected_version || 1;
      if (exitKind === 'alias') {
        const data = await postDecision('candidates', target.id, {
          action_key: 'evolution_candidate.adopt', expected_version: version,
          input: { target_type: form.target_type, target: form.target_ref },
        });
        // 目标字典行不存在不是错误，是"该走另一个出口"：据此改按钮而不是报错
        if (data?.changed === false && data?.next_action) {
          toast.warning(data.message || t('k_59e95054a8'));
          setExitKind('');
          openExit({ ...target, kind: data.next_action as Kind, suggestion: undefined },
            data.next_action === 'new_metric' ? 'metric' : 'dimension');
          return;
        }
        toast.success(t('k_c8586b2abd'));
      } else if (exitKind === 'metric') {
        const data = await postDecision('candidates', target.id, {
          action_key: 'evolution_metric.create', expected_version: version,
          input: { object: form.bound_object_key, display_name: form.display_name,
                   agg_type: form.agg_type, formula: form.formula, unit: form.unit },
        });
        toast.success(data.message || t('k_027c9e552e'));
        if (data?.needs_completion?.length) {
          toast.warning(t('k_1feeca1e18', { p0: data.needs_completion.join('、') }));
        }
      } else if (exitKind === 'dimension') {
        if (!form.property_name) { toast.error(t('k_fa85fe050c')); return; }
        const data = await postDecision('candidates', target.id, {
          action_key: 'evolution_dimension.create', expected_version: version,
          input: { object: form.bound_object_key, property: form.property_name,
                   display_name: form.display_name, category: form.category },
        });
        toast.success(data.message || t('k_37315326aa'));
      } else if (exitKind === 'term') {
        const data = await postDecision('candidates', target.id, {
          action_key: 'evolution_candidate.to_term', expected_version: version,
          input: { description: form.note || '' },
        });
        toast.success(data.message || t('k_c85d305d6d'));
      }
      setTarget(null); setExitKind('');
      load(); loadOverview();
    } catch (e: any) {
      handleDecisionError(e);
    } finally {
      setBusy(false);
    }
  };

  /** 驳回候选：必须填理由（服务层同口径拒绝空理由），经统一决策链落痕不删行。 */
  const rejectCandidateConfirm = async () => {
    if (!rejectCandidate) return;
    if (!rejectNote.trim()) { toast.error(tr('k_5d1e9a7c41')); return; }
    setBusy(true);
    try {
      await postDecision('candidates', rejectCandidate.id, {
        action_key: 'evolution_candidate.reject',
        expected_version: rejectCandidate.expected_version || 1,
        note: rejectNote,
      });
      toast.success(t('k_397ff0a184'));
      setRejectCandidate(null);
      setRejectNote('');
      load(); loadOverview();
    } catch (e: any) {
      handleDecisionError(e);
    } finally {
      setBusy(false);
    }
  };

  // 队列分流由服务端 queue 参数完成（P1-2）；跨页签计数取服务端 counts，不随当前页抖动
  const pendingProposalCount = propCounts?.pending ?? ov?.proposal_counts?.pending ?? 0;

  // 无权限时显示缺失权限码与原因（P1-1）：按钮不渲染（点了必 403），但要说清缺什么
  const missingActionPerms = [
    !canSaveOntology && 'ontology:save',
    !canManageMetrics && 'metrics:manage',
  ].filter(Boolean) as string[];

  /** 提议 → 场景编辑器：预填提议标题/正文，mode 决定新建还是追加到已有。 */
  const openScenarioFromProposal = (mode: 'create' | 'append') => {
    setScenarioPrefill({
      title: activeProposal?.title || '',
      content: activeProposal?.content || '',
    });
    setScenarioAppend(mode === 'append');
    // 记住这条提议：保存成功后才能把它标成已处理。不能只存在 activeProposal 里 ——
    // 弹窗一关它就 null 了，场景存完了待办却还挂着，同一件事会被做第二遍。
    setProposalForScenario(activeProposal);
    setActiveProposal(null);
    setScenarioOpen(true);
  };

  /**
   * 按 kind 分组渲染队列：组间按 KIND_ORDER（= 处置成本），组内顺序直接沿用服务端排序。
   * 「建议」badge 的文案、门槛与预填全部来自服务端 suggestion，前端不重算。
   */
  const renderGroups = (list: Candidate[], respectFilter = true) => (
    <>
      {KIND_ORDER
        .filter(k => (!respectFilter || !kindFilter || k === kindFilter) && list.some(i => i.kind === k))
        .map(kind => {
          const rows = list.filter(i => i.kind === kind);
          return (
            <div key={kind} className="border rounded-lg">
              <div className="px-3 py-2 border-b text-sm font-medium flex items-center gap-2">
                {KIND_LABEL[kind]}
                <Badge variant="outline" className="text-[11px]">{rows.length}</Badge>
                <span className="text-[11px] text-muted-foreground font-normal">{KIND_HINT[kind]}</span>
              </div>
              <div>
                {rows.map(c => (
                  <div key={c.id} className="px-3 py-2.5 border-b last:border-0 text-sm space-y-1.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium">{c.term}</span>
                      {c.suggestion && (
                        <Badge variant={TIER_BADGE[c.suggestion.tier]} className="text-[10px]"
                          title={`${tr('k_0e795f869b')}${c.suggestion.basis}`}>
                          {TIER_LABEL[c.suggestion.tier]}
                        </Badge>
                      )}
                      {c.suggestion?.drift && (
                        <Badge variant="destructive" className="text-[10px]"
                          title={c.suggestion.basis}>
                          {tr('k_93d5a92447')}
                        </Badge>
                      )}
                      <Badge variant="outline" className="text-[10px]">
                        {c.datasource_name || t('k_d881edb46e')}
                      </Badge>
                      <Badge variant="secondary" className="text-[10px]">{t('k_029b4888a0', { p0: c.hit_count })}</Badge>
                      <Badge variant="outline" className="text-[10px]">
                        {SOURCE_LABEL[c.source] || c.source}
                      </Badge>
                      {c.target_ref && (
                        <span className="text-[11px] text-muted-foreground">≈ {c.target_ref}</span>
                      )}
                      <div className="flex-1" />
                      {/* 详情抽屉入口：证据/建议/权限/历史决策与处置在同一上下文（P1-1） */}
                      <Button size="sm" variant="ghost" className="h-6 text-[11px]"
                        onClick={() => openExit(c, '')}>
                        {t('k_4f55ee1e68')}
                      </Button>
                      {/* 写出口按各自权限码显隐：没权限就不渲染（点了必 403 的 按钮是空承诺） */}
                      {kind === 'alias' && canSaveOntology && (
                        <Button size="sm" variant="outline" className="h-6 text-[11px]"
                          onClick={() => openExit(c, 'alias')}>
                          {c.suggestion ? tr('k_79fe0e4a52') : t('k_4d7b245f64')}
                        </Button>
                      )}
                      {kind === 'new_metric' && canManageMetrics && (
                        <Button size="sm" variant="outline" className="h-6 text-[11px]"
                          onClick={() => openExit(c, 'metric')}>
                          <Check className="h-3 w-3 mr-1" />{t('k_7b3561a815')}
                        </Button>
                      )}
                      {kind === 'new_dimension' && canManageMetrics && (
                        <Button size="sm" variant="outline" className="h-6 text-[11px]"
                          onClick={() => openExit(c, 'dimension')}>
                          <Check className="h-3 w-3 mr-1" />{t('k_ce353b2d85')}
                        </Button>
                      )}
                      {kind === 'term' && canSaveOntology && (
                        <Button size="sm" variant="outline" className="h-6 text-[11px]"
                          onClick={() => openExit(c, 'term')}>
                          {t('k_0e67e0c015')}
                        </Button>
                      )}
                      {kind === 'binding_gap' && (
                        // 绑定问题不是"要不要建"，是绑没绑对：去本体建模核对，
                        // 而不是把它固化成一条注定还会失败的用例
                        <Button size="sm" variant="outline" className="h-6 text-[11px]"
                          onClick={() => (onOpenModel ? onOpenModel() : navigate('/data/ontology'))}>
                          <ExternalLink className="h-3 w-3 mr-1" />{t('k_7254c7b001')}
                        </Button>
                      )}
                      {canSaveOntology && (
                        <Button size="sm" variant="ghost" className="h-6 text-[11px]"
                          onClick={() => { setRejectCandidate(c); setRejectNote(''); }}>
                          <X className="h-3 w-3 mr-1"/>{t('k_32525478cd')}
                        </Button>
                      )}
                    </div>
                    {c.suggestion?.ambiguous && (
                      <div className="text-[11px] text-amber-700">{tr('k_32badc1bb5')}</div>
                    )}
                    {(c.suggestion?.suggested_refs || []).length > 0 && (
                      <div className="text-[11px] text-muted-foreground">
                        {tr('k_cb3c082bb4')}{(c.suggestion?.suggested_refs || []).join('、')}
                      </div>
                    )}
                    <div className="text-[11px] text-muted-foreground">
                      <b>{t("k_d7d6020732")}</b>{c.classify_reason}
                    </div>
                    {Boolean((c.evidence as any)?.question) && (
                      <div className="text-[11px] text-muted-foreground">
                        {t('k_2361a0d37b', { p0: String((c.evidence as any).question) })}
                      </div>
                    )}
                    {Array.isArray((c.evidence as any)?.candidates) &&
                      ((c.evidence as any).candidates as string[]).length > 0 && (
                      <div className="text-[11px] text-muted-foreground">
                        {t('k_e556670542', { p0: ((c.evidence as any).candidates as string[]).slice(0, 5).join('、') })}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
    </>
  );

  return (
    <div className={embedded ? 'space-y-3' : 'p-6 space-y-4'}>
      <div className="flex items-center gap-2">
        {/* 内嵌时页签名已说明这是哪里，不再重复一个同名大标题（同一屏两个 h1 会打架） */}
        {!embedded && (
          <>
            <Dna className="h-5 w-5" />
            <h1 className="text-lg font-semibold">{t("k_849e449611")}</h1>
            <Badge variant="outline" className="text-[10px]">{t('k_46d94e86ab')}</Badge>
          </>
        )}
        <div className="flex-1" />
        <Input value={qInput} onChange={e => setQInput(e.target.value)}
          placeholder={tr('k_5d1e9a7c4c')}
          className="h-8 w-44 text-xs" aria-label={tr('k_5d1e9a7c4c')} />
        <Button variant="outline" size="sm" onClick={() => { load(); loadOverview(); loadProposals(); }}>
          <RefreshCw className="h-3.5 w-3.5 mr-1" />{t('k_38108eaa1d')}
        </Button>
        <Button size="sm" onClick={runDigest} disabled={digesting || !canDigest}>
          <Play className="h-3.5 w-3.5 mr-1" />{digesting ? t('k_4f2449291a') : t('k_e87e2177ca')}
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        {tr('k_1325d59b1c')}{' '}<code>evolution_digest</code>{tr('k_da74f8b5fc')}<b>{t("k_ccabc70a21")}</b>{tr('k_d816bfdaa6')}{' '}<b>{t("k_ab33ea20aa")}</b>{t('k_4271b04c1a')}
      </p>

      {ov && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div className="border rounded-md p-2.5">
            <div className="text-muted-foreground">{t("k_fcc974676e")}</div>
            <div className="mt-1 font-medium">
              👍 {ov.feedback.up} · 👎 {ov.feedback.down}
            </div>
            <div className={ov.feedback.pending_negative ? 'text-amber-600' : 'text-muted-foreground'}>
              {t('k_307eca81e4', { p0: ov.feedback.pending_negative })}</div>
          </div>
          <div className="border rounded-md p-2.5">
            <div className="text-muted-foreground">{t("k_9ab9b98fe5")}</div>
            {Object.keys(ov.pending_by_source).length === 0 && (
              <div className="text-muted-foreground mt-1">{t("k_0f61703c58")}</div>
            )}
            {Object.entries(ov.pending_by_source).map(([k, n]) => (
              <div key={k} className="mt-0.5">{SOURCE_LABEL[k] || k}：{n}</div>
            ))}
          </div>
          <div className="border rounded-md p-2.5">
            <div className="text-muted-foreground">{t("k_0ce8c74cda")}</div>
            {ov.kinds.map(k => (
              <div key={k} className="mt-0.5 flex items-center justify-between">
                <span>{KIND_LABEL[k as Kind]}</span>
                <span className="font-medium">{ov.pending_by_kind[k] ?? 0}</span>
              </div>
            ))}
          </div>
          <div className="border rounded-md p-2.5">
            <div className="text-muted-foreground">{t("k_a35dbe4317")}</div>
            <div className="mt-1 text-green-700">{t('k_25c0f09d35', { p0: ov.outcome.verified ?? 0 })}</div>
            <div className={ov.outcome.stale ? 'text-destructive font-medium' : 'text-muted-foreground'}>
              {t('k_0af6c4a530', { p0: ov.outcome.stale ?? 0 })}
            </div>
            <div className="text-muted-foreground">{t('k_5932b93d07', { p0: ov.outcome.converted ?? 0, p1: ov.outcome.rejected ?? 0 })}</div>
          </div>
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid grid-cols-6 w-full max-w-3xl">
          <TabsTrigger value="build">{tr('k_5563318aac')}</TabsTrigger>
          <TabsTrigger value="binding">{tr('k_e0730d5ac1')}</TabsTrigger>
          <TabsTrigger value="dict">{t('k_1361bb4b8d')}</TabsTrigger>
          <TabsTrigger value="knowledge">
            {t('k_59b655627c')}
            {pendingProposalCount > 0 && (
              <Badge variant="secondary" className="ml-1 h-4 px-1 text-[10px]">{pendingProposalCount}</Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="outcome">{tr('k_5d1e9a7c44')}</TabsTrigger>
          <TabsTrigger value="history">{t("k_8416c62f82")}</TabsTrigger>
        </TabsList>

        <TabsContent value="build" className="space-y-3">
          <p className="text-[11px] text-muted-foreground">{tr('k_413bdb2a10')}</p>
          <div className="flex items-center gap-2">
            <Label className="text-xs">{t("k_6bf1c6de44")}</Label>
            <Select value={kindFilter || 'all'} onValueChange={v => setKindFilter(v === 'all' ? '' : v)}>
              <SelectTrigger className="w-[180px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("k_778fc8f994")}</SelectItem>
                {Object.entries(KIND_LABEL).map(([k, label]) => (
                  <SelectItem key={k} value={k}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {loading && <div className="text-xs text-muted-foreground p-4">{t("k_300ee3dee4")}</div>}
          {!loading && error && (
            <div className="text-xs text-destructive p-4 border border-destructive/40 rounded">
              {t('k_fc3f31a5be', { p0: error })}
            </div>
          )}
          {!loading && !error && items.length === 0 && (
            <div className="text-xs text-muted-foreground p-4 border rounded text-center">
              {tr('k_5967365baf')}
            </div>
          )}

          {renderGroups(items)}
          {missingActionPerms.length > 0 && (
            <div className="text-[11px] text-amber-600">
              {tr('k_5d1e9a7c4b', { p0: missingActionPerms.join('、') })}
            </div>
          )}
          <PaginationBar page={page} pageSize={pageSize} total={total} busy={loading}
            onChange={setPage} onPageSizeChange={setPageSize} pageSizeOptions={[20, 50]} />
        </TabsContent>

        <TabsContent value="binding" className="space-y-3">
          <p className="text-[11px] text-muted-foreground">{tr('k_a3b43cad28')}</p>

          {loading && <div className="text-xs text-muted-foreground p-4">{t("k_300ee3dee4")}</div>}
          {!loading && error && (
            <div className="text-xs text-destructive p-4 border border-destructive/40 rounded">
              {t('k_fc3f31a5be', { p0: error })}
            </div>
          )}
          {!loading && !error && items.length === 0 && (
            <div className="text-xs text-muted-foreground p-4 border rounded text-center">
              {tr('k_eeafc9afbb')}
            </div>
          )}

          {renderGroups(items, false)}
          <PaginationBar page={page} pageSize={pageSize} total={total} busy={loading}
            onChange={setPage} onPageSizeChange={setPageSize} pageSizeOptions={[20, 50]} />
        </TabsContent>

        {/* ── 字典待办：未归属/孤儿指标维度 + 未绑定术语（原「建模待办」工作台）── */}
        <TabsContent value="dict" className="space-y-3">
          <DictTodoTab />
        </TabsContent>

        {/* ── 知识更新：提议只是一份待确认的备忘录 ─────────────────── */}
        <TabsContent value="knowledge" className="space-y-3">
          <p className="text-[11px] text-muted-foreground">{t('k_6eb840a1b9')}</p>
          <div className="flex items-center gap-2">
            <Label className="text-xs">{t("k_62e951a692")}</Label>
            <Select value={proposalStatus} onValueChange={setProposalStatus}>
              <SelectTrigger className="w-[180px] h-8 text-xs" aria-label={t("k_62e951a692")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {['pending', 'applied', 'rejected'].map(s => (
                  <SelectItem key={s} value={s}>{PROPOSAL_STATUS_LABEL[s]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            {!canManageKnowledge && (
              <span className="text-[11px] text-amber-600">{t('k_2a844cb5e6')}</span>
            )}
          </div>

          {proposalsLoading && <div className="text-xs text-muted-foreground p-4">{t("k_300ee3dee4")}</div>}
          {!proposalsLoading && proposalsError && (
            <div className="text-xs text-destructive p-4 border border-destructive/40 rounded">
              {t('k_fc3f31a5be', { p0: proposalsError })}
            </div>
          )}
          {!proposalsLoading && !proposalsError && proposals.length === 0 && (
            <div className="text-xs text-muted-foreground p-4 border rounded text-center">
              {proposalStatus === 'pending' ? t('k_031570527f') : t('k_36d5bbf0d4')}
            </div>
          )}

          {proposals.map(p => {
            const open = expanded.includes(p.id);
            const showAll = open || (p.content || '').length <= 200;
            return (
              <div key={p.id} className="border rounded-lg p-3 space-y-1.5 text-sm">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge variant="outline" className="text-[10px]">
                    {TARGET_LABEL[p.target_type] || p.target_type}
                  </Badge>
                  <span className="font-medium">{p.title}</span>
                  <Badge variant="secondary" className="text-[10px]">
                    {PROPOSAL_SOURCE_LABEL[p.source] || p.source}
                  </Badge>
                  <Badge variant={p.status === 'pending' ? 'default' : 'outline'} className="text-[10px]">
                    {PROPOSAL_STATUS_LABEL[p.status] || p.status}
                  </Badge>
                  {p.target_hint && (
                    <span className="text-[11px] text-muted-foreground">{t('k_d169148f67')}：{p.target_hint}</span>
                  )}
                  {!!p.datasource_name && (
                    <span className="text-[11px] text-muted-foreground">{p.datasource_name}</span>
                  )}
                  <div className="flex-1" />
                  {!!p.origin_conversation_id && (
                    <Button size="sm" variant="ghost" className="h-6 text-[11px]"
                      onClick={() => openConversation(p.origin_conversation_id)}>
                      <ExternalLink className="h-3 w-3 mr-1" />{t('k_a85ed23ca9')}
                    </Button>
                  )}
                  {p.status === 'pending' && canManageKnowledge && (
                    <>
                      <Button size="sm" variant="outline" className="h-6 text-[11px]"
                        onClick={() => setActiveProposal(p)}>
                        {t('k_bfdd85e099')}
                      </Button>
                      <Button size="sm" variant="ghost" className="h-6 text-[11px]"
                        onClick={() => setActiveProposal(p)}>
                        {t('k_8412b77794')}
                      </Button>
                      <Button size="sm" variant="ghost" className="h-6 text-[11px]"
                        onClick={() => { setRejectTarget(p); setRejectNote(''); }}>
                        <X className="h-3 w-3 mr-1" />{t('k_32525478cd')}
                      </Button>
                    </>
                  )}
                </div>

                {!!p.content && (
                  <div className="text-[12px] leading-5 whitespace-pre-wrap rounded bg-muted/30 p-2">
                    {showAll ? p.content : `${p.content.slice(0, 200)}…`}
                    {!showAll && (
                      <button type="button" className="ml-1 text-primary underline"
                        onClick={() => setExpanded(v => [...v, p.id])}>
                        {t('k_f1d192b54c')}
                      </button>
                    )}
                    {open && (p.content || '').length > 200 && (
                      <button type="button" className="ml-1 text-primary underline"
                        onClick={() => setExpanded(v => v.filter(x => x !== p.id))}>
                        {t('k_0a4a3daeff')}
                      </button>
                    )}
                  </div>
                )}
                {!!p.rationale && (
                  <div className="text-[11px] text-muted-foreground">
                    <b>{t('k_f24887d00d')}</b>{p.rationale}
                  </div>
                )}
                {p.status !== 'pending' && (
                  <div className="text-[11px] text-muted-foreground">
                    {t('k_4c8c9d4f5d')}：{p.decided_by || '—'}（{t('k_be2572d31f')} {p.decided_at || '—'}）
                    {p.applied_ref ? `｜${t('k_d169148f67')}：${p.applied_ref}` : ''}
                    {p.decided_note ? `｜${p.decided_note}` : ''}
                  </div>
                )}
                <div className="text-[11px] text-muted-foreground">{p.created_at}</div>
              </div>
            );
          })}
          <PaginationBar page={page} pageSize={pageSize} total={propTotal} busy={proposalsLoading}
            onChange={setPage} onPageSizeChange={setPageSize} pageSizeOptions={[20, 50]} />
        </TabsContent>

        {/* ── 闭环成效：只回答“转出去的东西到底生效了没”（与处置历史分开）── */}
        <TabsContent value="outcome" className="space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
            {([
              { key: 'converted', label: STATUS_LABEL.converted, desc: tr('k_5d1e9a7c45'), tone: 'text-amber-600' },
              { key: 'verified', label: STATUS_LABEL.verified, desc: tr('k_5d1e9a7c46'), tone: 'text-green-700' },
              { key: 'stale', label: STATUS_LABEL.stale, desc: tr('k_5d1e9a7c47'), tone: 'text-destructive' },
              { key: 'rejected', label: STATUS_LABEL.rejected, desc: tr('k_5d1e9a7c48'), tone: 'text-muted-foreground' },
              { key: 'expired', label: STATUS_LABEL.expired, desc: tr('k_5d1e9a7c49'), tone: 'text-muted-foreground' },
            ]).map(s => (
              <button key={s.key} type="button"
                className="border rounded-md p-2.5 text-left transition-colors hover:bg-muted/40"
                onClick={() => setParams({ tab: 'history', hstatus: s.key })}>
                <div className="text-muted-foreground">{s.label}</div>
                <div className={`mt-1 text-lg font-medium ${s.tone}`}>
                  {(ov?.outcome || {})[s.key] ?? 0}
                </div>
                <div className="mt-1 text-[10px] leading-4 text-muted-foreground">{s.desc}</div>
              </button>
            ))}
          </div>
          {!ov && <div className="text-xs text-muted-foreground p-4">{t('k_300ee3dee4')}</div>}
        </TabsContent>

        <TabsContent value="history" className="space-y-3">
          <div className="flex items-center gap-2">
            <Label className="text-xs">{t("k_62e951a692")}</Label>
            <Select value={historyStatus} onValueChange={setHistoryStatus}>
              <SelectTrigger className="w-[180px] h-8 text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(STATUS_LABEL).map(([k, label]) => (
                  <SelectItem key={k} value={k}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-[11px] text-muted-foreground">
              {t('k_7ea4f64c3e')}
            </span>
          </div>
          <div className="border rounded">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-muted/40 text-left">
                  <th className="p-2">{t("k_434eb25505")}</th>
                  <th className="p-2">{t("k_e4e46c7235")}</th>
                  <th className="p-2">{t("k_a3ccf702c5")}</th>
                  <th className="p-2">{t("k_62e951a692")}</th>
                  <th className="p-2">{t("k_f87c5ca889")}</th>
                  <th className="p-2">{t("k_7086f8f94a")}</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 && !loading && (
                  <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">{t("k_afaa05cdc4")}</td></tr>
                )}
                {items.map(c => (
                  <tr key={c.id} className="border-t">
                    <td className="p-2">{c.term}</td>
                    <td className="p-2">{KIND_LABEL[c.kind] || c.kind}</td>
                    <td className="p-2">{c.datasource_name || '—'}</td>
                    <td className="p-2">
                      <Badge variant={c.status === 'stale' ? 'destructive'
                        : c.status === 'verified' ? 'default' : 'outline'} className="text-[9px]">
                        {STATUS_LABEL[c.status] || c.status}
                      </Badge>
                      {c.status === 'converted' && (
                        <span className="ml-1 text-[9px] text-amber-600" title={tr('k_5d1e9a7c45')}>
                          {tr('k_5d1e9a7c45')}
                        </span>
                      )}
                    </td>
                    <td className="p-2 text-muted-foreground">
                      {c.converted_ref ? `${c.converted_kind}：${c.converted_ref}` : '—'}
                    </td>
                    <td className="p-2 text-muted-foreground">
                      {c.decided_by || '—'}
                      {c.decided_at ? `（${c.decided_at}）` : ''}
                      {c.decision_note ? <div className="mt-0.5">{c.decision_note}</div> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <PaginationBar page={page} pageSize={pageSize} total={total} busy={loading}
            onChange={setPage} onPageSizeChange={setPageSize} pageSizeOptions={[20, 50]} />
        </TabsContent>
      </Tabs>

      {/* ── 候选出口（四出口，已去掉评测）────────────────────────── */}
      {/* 统一详情抽屉（P1-1）：左侧队列可见，右侧详情 + 处置同上下文，替代多弹窗状态漂移 */}
      {target && (
        <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-label={t('k_4f55ee1e68')}>
          <div className="absolute inset-0 bg-black/30"
            onClick={() => { setExitKind(''); setTarget(null); }} />
          <div className="relative w-full max-w-xl h-full overflow-y-auto bg-background border-l shadow-xl">
            <div className="sticky top-0 z-10 bg-background border-b px-4 py-3 flex items-center gap-2">
              <span className="font-medium">{target.term}</span>
              <Badge variant="outline" className="text-[10px]">{KIND_LABEL[target.kind]}</Badge>
              <Badge variant="outline" className="text-[10px]">
                {target.datasource_name || t('k_d881edb46e')}
              </Badge>
              <Badge variant="secondary" className="text-[10px]">
                {STATUS_LABEL[target.status] || target.status}
              </Badge>
              <div className="flex-1" />
              <Button size="sm" variant="ghost" className="h-7"
                onClick={() => { setExitKind(''); setTarget(null); }}>{t("k_4d0b4688c7")}</Button>
            </div>
            <div className="p-4 space-y-3 text-sm">
              <div className="text-[11px] text-muted-foreground border-l-2 border-border pl-2">
                {target.classify_reason}
              </div>
              {(target.suggestion?.suggested_refs || []).length > 0 && (
                <div className="text-[11px] text-muted-foreground">
                  {tr('k_cb3c082bb4')}{(target.suggestion?.suggested_refs || []).join('、')}
                </div>
              )}
              {Boolean((target.evidence as any)?.question) && (
                <div className="text-[11px] text-muted-foreground">
                  {t('k_2361a0d37b', { p0: String((target.evidence as any).question) })}
                </div>
              )}
              {Array.isArray((target.evidence as any)?.candidates)
                && ((target.evidence as any).candidates as string[]).length > 0 && (
                <div className="text-[11px] text-muted-foreground">
                  {t('k_e556670542', { p0: ((target.evidence as any).candidates as string[]).slice(0, 8).join('、') })}
                </div>
              )}
              {(target.decided_by || target.decision_note || target.converted_ref) && (
                <div className="rounded border p-2 text-[11px] space-y-0.5">
                  <div className="font-medium">{tr('k_5d1e9a7c65')}</div>
                  {(target.decided_by || target.decided_at) && (
                    <div className="text-muted-foreground">
                      {target.decided_by || ''}{target.decided_at ? ` · ${target.decided_at}` : ''}
                    </div>
                  )}
                  {target.decision_note && <div>{target.decision_note}</div>}
                  {target.converted_ref && <div>→ {target.converted_ref}</div>}
                </div>
              )}
              {missingActionPerms.length > 0 && (
                <div className="text-[11px] text-amber-600">
                  {tr('k_5d1e9a7c4b', { p0: missingActionPerms.join('、') })}
                </div>
              )}
              {exitKind === '' ? (
                <div className="space-y-2 rounded border p-2">
                  <div className="text-[11px] font-medium">{tr('k_5d1e9a7c64')}</div>
                  <div className="flex flex-wrap gap-2">
                    {canSaveOntology && (
                      <Button size="sm" variant="outline" className="h-7 text-[11px]"
                        onClick={() => target && openExit(target, 'alias')}>{t('k_4d7b245f64')}</Button>
                    )}
                    {canManageMetrics && (
                      <Button size="sm" variant="outline" className="h-7 text-[11px]"
                        onClick={() => target && openExit(target, 'metric')}>{t('k_7b3561a815')}</Button>
                    )}
                    {canManageMetrics && (
                      <Button size="sm" variant="outline" className="h-7 text-[11px]"
                        onClick={() => target && openExit(target, 'dimension')}>{t('k_ce353b2d85')}</Button>
                    )}
                    {canSaveOntology && (
                      <Button size="sm" variant="outline" className="h-7 text-[11px]"
                        onClick={() => target && openExit(target, 'term')}>{t('k_9abf0019e8')}</Button>
                    )}
                    {canSaveOntology && (
                      <Button size="sm" variant="destructive" className="h-7 text-[11px]"
                        onClick={() => { if (target) { setRejectCandidate(target); setRejectNote(''); } }}>
                        <X className="h-3 w-3 mr-1" />{t('k_32525478cd')}
                      </Button>
                    )}
                  </div>
                </div>
              ) : (
                <div className="space-y-3 rounded border p-2">
                  <div className="text-[11px] font-medium">
                    {exitKind === 'alias' ? tr('k_4d7b245f64') : exitKind === 'metric' ? tr('k_7b3561a815')
                      : exitKind === 'dimension' ? tr('k_ce353b2d85') : tr('k_9abf0019e8')}
                  </div>

              {exitKind === 'alias' && (
                <>
                  <div>
                    <Label className="mb-1 block">{t("k_c44a4e3da1")}</Label>
                    <Select value={form.target_type} onValueChange={v => {
                      // 目标类型一换，目标清单也得换（度量/维度/对象是三张表）
                      setForm({ ...form, target_type: v, target_ref: '' });
                      void loadTargets(v, target?.datasource_name || '');
                    }}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="metric">{t("k_1dfc810a7b")}</SelectItem>
                        <SelectItem value="dimension">{t("k_72fa9ecdc4")}</SelectItem>
                        <SelectItem value="object">{t("k_e5b5171f27")}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label className="mb-1 block">{t("k_28223086a8")}</Label>
                    <Select value={form.target_ref || undefined}
                      onValueChange={v => setForm({ ...form, target_ref: v })}>
                      <SelectTrigger><SelectValue placeholder={t("k_47289b605f")} /></SelectTrigger>
                      <SelectContent>
                        {targetOptions.map(o => (
                          <SelectItem key={o.key} value={o.key}>{o.display_name || o.key}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {targetOptions.length === 0 && (
                      <p className="mt-1 text-[11px] text-amber-600">{tr('k_30a1d75025')}</p>
                    )}
                    {Boolean(form.target_ref) && !targetOptions.some(o => o.key === form.target_ref) && (
                      <p className="mt-1 text-[11px] text-amber-600">{tr('k_a78870bee8')}</p>
                    )}
                    {(target?.suggestion?.suggested_refs || []).length > 0 && (
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        {tr('k_cb3c082bb4')}{(target?.suggestion?.suggested_refs || []).join('、')}
                      </p>
                    )}
                  </div>
                </>
              )}

              {(exitKind === 'metric' || exitKind === 'dimension') && (
                <div>
                  <Label className="mb-1 block">{t("k_72317c17c2")}</Label>
                  <Select value={form.bound_object_key || undefined} onValueChange={loadProps}>
                    <SelectTrigger><SelectValue placeholder={t("k_63d7b41003")} /></SelectTrigger>
                    <SelectContent>
                      {objOptions.map(o => (
                        <SelectItem key={o.key} value={o.key}>{o.display_name || o.key}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {objOptions.length === 0 && (
                    <p className="mt-1 text-[11px] text-amber-600">{t('k_f67262fdb6')}</p>
                  )}
                </div>
              )}

              {exitKind === 'metric' && (
                <>
                  <div>
                    <Label className="mb-1 block">{t("k_a35ae2c7f1")}</Label>
                    <Input value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })} />
                  </div>
                  <div>
                    <Label className="mb-1 block">{t("k_7045c9b015")}</Label>
                    <div className="flex gap-2">
                      <Select value={form.agg_type} onValueChange={v => setForm({ ...form, agg_type: v })}>
                        <SelectTrigger className="w-[140px]"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {['COUNT', 'SUM', 'AVG', 'MAX', 'MIN', 'COUNT_DISTINCT'].map(a => (
                            <SelectItem key={a} value={a}>{a}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Input value={form.formula} onChange={e => setForm({ ...form, formula: e.target.value })}
                        placeholder={t('k_c3702db6c7')} />
                    </div>
                  </div>
                  <p className="text-[11px] text-muted-foreground">{t('k_480f9d5977')}</p>
                </>
              )}

              {exitKind === 'dimension' && (
                <>
                  <div>
                    <Label className="mb-1 block">{t("k_a35ae2c7f1")}</Label>
                    <Input value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })} />
                  </div>
                  <div>
                    <Label className="mb-1 block">{t("k_f4ec4a6499")}</Label>
                    <Select value={form.property_name || undefined}
                      onValueChange={v => setForm({ ...form, property_name: v })}>
                      <SelectTrigger><SelectValue placeholder={t("k_f6120079d3")} /></SelectTrigger>
                      <SelectContent>
                        {propOptions.map(p => (
                          <SelectItem key={p.property} value={p.property}>{p.property}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </>
              )}

              {exitKind === 'term' && (
                <div>
                  <Label className="mb-1 block">{t("k_f946bf0221")}</Label>
                  <Textarea rows={2} value={form.note} onChange={e => setForm({ ...form, note: e.target.value })}
                    placeholder={t("k_cf358d46dc")} />
                </div>
              )}
                  <div className="flex justify-end gap-2 pt-1">
                    <Button variant="outline" onClick={() => setExitKind('')}>{t("k_4d0b4688c7")}</Button>
                    <Button onClick={submit} disabled={busy}>{busy ? t('k_cf978c0252') : t('k_d575bfd288')}</Button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── 知识更新处置（三分支，写入走各域既有通道）────────────── */}
      <KnowledgeUpdateDialog
        open={!!activeProposal}
        onOpenChange={o => { if (!o) setActiveProposal(null); }}
        proposal={activeProposal}
        canMarkApplied={canManageKnowledge}
        onScenario={openScenarioFromProposal}
        onGoKb={p => {
          // 去目标域页面：业务名一次性携带（sessionStorage，与 AS-BOT 面板同惯例）
          sessionStorage.setItem('evo_kb_prefill', JSON.stringify({
            kbName: p.target_hint || '', docTitle: p.title || '', content: p.content || '',
          }));
          setActiveProposal(null);
          navigate('/system/knowledge-base');
        }}
        onGoModel={p => {
          sessionStorage.setItem('evo_object_prefill', JSON.stringify({
            objectName: p.target_hint || p.title || '', description: p.content || '',
          }));
          setActiveProposal(null);
          if (onOpenModel) onOpenModel(); else navigate('/data/ontology');
        }}
        onApplied={ref => { if (activeProposal) void markApplied(activeProposal, ref); }}
      />

      <Dialog open={!!rejectTarget || !!rejectCandidate}
        onOpenChange={o => { if (!o) { setRejectTarget(null); setRejectCandidate(null); } }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{rejectCandidate ? tr('k_5d1e9a7c3f') : t('k_7eb7620344')}</DialogTitle>
            <DialogDescription>{rejectCandidate ? tr('k_5d1e9a7c40') : t('k_f9ab7ea54f')}</DialogDescription>
          </DialogHeader>
          {(rejectTarget || rejectCandidate) && (
            <div className="space-y-2 text-sm">
              <div className="font-medium">{rejectTarget?.title || rejectCandidate?.term}</div>
              <div>
                <Label className="mb-1 block">{t('k_b8dabf9d56')}</Label>
                <Textarea rows={3} value={rejectNote} onChange={e => setRejectNote(e.target.value)} />
                {!rejectNote.trim() && (
                  <p className="mt-1 text-[11px] text-destructive">{tr('k_5d1e9a7c41')}</p>
                )}
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => { setRejectTarget(null); setRejectCandidate(null); }}>{t("k_4d0b4688c7")}</Button>
            <Button variant="destructive"
              onClick={() => (rejectTarget ? rejectProposal() : rejectCandidateConfirm())}
              disabled={busy || !rejectNote.trim()}>
              {t('k_32525478cd')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ScenarioEditorDialog
        open={scenarioOpen}
        onOpenChange={o => {
          setScenarioOpen(o);
          if (!o) {
            setScenarioPrefill(null); setScenarioAppend(false);
            // 直接关掉（没保存）就不要留在暂存里，下次同一条提议会误标
            setProposalForScenario(null);
          }
        }}
        options={scenarioOptions}
        appendMode={scenarioAppend}
        prefill={scenarioPrefill}
        onSaved={ref => {
          loadProposals();
          const p = proposalForScenario;
          setProposalForScenario(null);
          // 场景已存 → 把提议标成已处理（失败由 markApplied 显式提示「已写入但未标记」）
          if (p && ref) void markApplied(p, `场景 ${ref}`);
        }}
      />
    </div>
  );
}