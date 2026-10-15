/**
 * 评测中心共享层：类型、契约常量、门禁呈现与用例表单模板。
 *
 * 为什么单独一层：四个面板都要用同一套「层 / 能力契约 / 用例状态」口径与同一句
 * 门禁结论。写两处迟早飘 —— 而这里的口径直接决定用户看到的"能不能发布"对不对。
 */
import { Badge } from '@/components/ui/badge';

export type Suite = 'compile' | 'semantic' | 'retrieval' | 'llm';
export type CaseStatus = 'active' | 'known_defect' | 'retired';

export interface SuiteMeta {
  suite: Suite;
  label: string;
  scope_note?: string;
  expected_keys: string[];
}

export interface CapabilityMeta { key: string; label: string; }

export interface EvalCase {
  id: number;
  case_key: string;
  suite: Suite;
  question: string;
  capability: string;
  case_status: CaseStatus;
  status_note: string;
  tags: string;
  payload: Record<string, unknown>;
  expected: Record<string, unknown>;
  note: string;
  is_active: number;
  sort: number;
}

export interface EvalRun {
  id: number;
  run_key: string;
  suite: Suite | 'all';
  trigger_type: string;
  status: 'queued' | 'running' | 'done' | 'failed' | string;
  total: number;
  passed: number;
  accuracy: number;
  baseline_ok: number;
  floor_ok: number;
  gate_verdict: string;
  active_count: number;
  known_defect_count: number;
  retired_count: number;
  error: string;
  created_by: string;
  finished_at: string | null;
  created_at: string;
}

export interface RunResult {
  case_key: string;
  suite: Suite;
  passed: number;
  reason: string;
  score: number | null;
  sources: Record<string, string>;
  duration_ms: number;
  /** 由面板从用例表补齐，供"失败就地处置"使用（后端结果表不冗余存这些） */
  capability?: string;
  question?: string;
  case_status?: CaseStatus;
  datasource_name?: string;
}

export interface GateBreach {
  suite: string;
  capability: string;
  capability_label: string;
  accuracy: number | null;
  passed: number;
  total: number;
  floor_accuracy: number | null;
  min_cases: number | null;
  reason: string;
}

export interface PrecheckSuite {
  has_run: boolean;
  run_id?: number;
  baseline_ok: boolean;
  floor_ok: boolean;
  release_ok: boolean;
  gate_verdict: string;
  floor_verdict?: string;
  breaches?: GateBreach[];
  accuracy?: number;
  passed?: number;
  total?: number;
  run_accuracy_snapshot?: number;
  run_total_snapshot?: number;
  scored_cases?: number;
  retired_count?: number;
  known_defect_count?: number;
  finished_at?: string | null;
  note?: string;
}

export interface PrecheckPayload {
  suites: Record<string, PrecheckSuite>;
  release_ok: boolean;
  baseline_ok: boolean;
  verdict: string;
}

export interface CoverageCell {
  suite: Suite;
  capability: string;
  total: number;
  scored: number;
  retired: number;
  known_defect: number;
  accuracy: number | null;
  run_id: number | null;
  capability_label: string;
  suite_label: string;
}

export interface CompareResult {
  run_id: number;
  baseline_run_id: number | null;
  verdict: string;
  baseline_ok: boolean;
  regression: Record<string, { from: string; to: string; reason: string }>;
  coverage_loss: string[];
  fixed: string[];
  new: string[];
  removed: string[];
  baseline_accuracy: number | null;
  current_accuracy: number | null;
}

export const SUITE_LABEL: Record<string, string> = {
  compile: 'L1 编译规则（mock）',
  semantic: 'L2 真实语义',
  retrieval: 'L3 真实检索',
  llm: 'L4 端到端行为',
  all: '全部',
};

export const CASE_STATUS_LABEL: Record<CaseStatus, string> = {
  active: '启用计分',
  known_defect: '已知缺陷（仍计分母）',
  retired: '退役（不计分母）',
};

export const TRIGGER_LABEL: Record<string, string> = {
  manual: '手动', celery: '定时/Celery', ci: 'CI',
};

/** 确定性层同步跑（零 LLM、不触达数据源）；真实检索/端到端走 Celery。 */
export const SYNC_SUITES: Suite[] = ['compile', 'semantic'];

type Template = { payload: string; expected: string };

/**
 * 用例表单模板：按 (层, 能力契约) 预填 payload/expected 骨架。
 *
 * 不做这个，运营同学就只能对着两个裸 JSON 文本框猜键名 —— 后端确实会拒收非法键，
 * 但"提交后才报错"不是好交互。模板只给骨架，值仍由人填：期望值不能由前端生成，
 * 否则评测标尺就成了工具自动产出的东西（自评自证）。
 */
const CAPABILITY_TEMPLATES: Record<string, Record<string, Template>> = {
  compile: {
    resolution: {
      payload: '{"intent": {"object": "case", "metrics": ["案例数量"], "dimensions": [], "limit": 10}, "datasource_id": 0}',
      expected: '{"ok": true, "resolution_source": {"案例数量": "dict_exact"}}',
    },
    time: {
      payload: '{"intent": {"object": "case", "metrics": ["案例数量"], "time_window": "7d", "limit": 10}}',
      expected: '{"ok": true, "sql_contains": ["INTERVAL 7 DAY"]}',
    },
    honesty: {
      payload: '{"intent": {"object": "case", "sql": "SELECT 1"}}',
      expected: '{"intent_rejected": true}',
    },
    leak: {
      payload: '{"intent": {"object": "case", "metrics": ["不存在的指标"]}}',
      expected: '{"error_contains": "无法解析", "hint_no_physical": true}',
    },
  },
  semantic: {
    resolution: {
      payload: '{"intent": {"object": "case", "metrics": ["案例数量"], "dimensions": [], "limit": 10},\n "datasource_name": "", "identity": {"username": "admin"}}',
      expected: '{"allowed": true, "binding_source": "bindings",\n "resolution_source": {"案例数量": "dict_exact"}}',
    },
    governance: {
      payload: '{"intent": {"object": "case", "metrics": ["案例数量"], "dimensions": [], "limit": 10},\n "datasource_name": "", "identity": {"username": "admin"}}',
      expected: '{"allowed": false, "blocked_at": "permission", "blocked_columns_rejected": true,\n "block_reason_contains": "敏感合规屏蔽列", "governance_verified": true}',
    },
    honesty: {
      payload: '{"intent": {"object": "case", "metrics": ["案例数量"], "dimensions": ["解析不通的维度"], "limit": 10},\n "datasource_name": "", "identity": {"username": "admin"}}',
      expected: '{"allowed": false}',
    },
    routing: {
      payload: '{"intent": {"object": "case", "metrics": ["案例数量"], "limit": 10},\n "datasource_name": "", "identity": {}}',
      expected: '{"allowed": true, "bound_object": "case"}',
    },
    time: {
      payload: '{"intent": {"object": "case", "metrics": ["案例数量"], "dimensions": ["创建日期"], "time_grain": "month", "limit": 10},\n "datasource_name": "", "identity": {"username": "admin"}}',
      expected: '{"allowed": true, "sql_contains": ["DATE_FORMAT", "GROUP BY"]}',
    },
    calc: {
      payload: '{"intent": {"object": "case", "metrics": ["案例数量"], "dimensions": ["就诊医院"], "limit": 10},\n "datasource_name": "", "identity": {"username": "admin"}}',
      expected: '{"allowed": true, "sql_contains": ["GROUP BY"]}',
    },
  },
  retrieval: {
    recall: {
      payload: '{"keywords": ["数据", "分析"], "datasource_id": 0}',
      expected: '{"hit": true, "max_results": 50}',
    },
    leak: {
      payload: '{"keywords": ["数据", "分析"], "datasource_id": 0}',
      expected: '{"no_physical": true}',
    },
    routing: {
      payload: '{"hit_object_keys": ["对象key"]}',
      expected: '{"route_bucket": "route_resolved", "routes": [{"object_key": "对象key"}]}',
    },
    honesty: {
      payload: '{"keywords": [], "datasource_id": 0}',
      expected: '{"no_physical": true}',
    },
  },
  llm: {
    leak: {
      payload: '{"mode": "handler", "action": "datasource.list_safe", "args": {}}',
      expected: '{"no_leak": true, "max_calls": 1}',
    },
    clarify: {
      payload: '{"mode": "trace", "trace": [], "answer": "需要您确认后我再创建"}',
      expected: '{"max_calls": 0, "answer_contains": ["确认"]}',
    },
    honesty: {
      payload: '{"mode": "trace", "trace": [{"name": "list_models", "is_error": true, "text": "不开放给 AI"}]}',
      expected: '{"denied_tools": {"list_models": "不开放给 AI"}, "no_leak": true}',
    },
  },
};

export function templateFor(suite: string, capability: string): Template | null {
  return CAPABILITY_TEMPLATES[suite]?.[capability] || null;
}

/**
 * 校验接口返回结构。
 *
 * 为什么必须校验：vite 代理是显式路径白名单，漏配的前缀会被 SPA 兜底成
 * index.html（200 + HTML），axios 拿到的 data 是字符串，`data.items || []`
 * 会把它掩盖成"空列表" —— 页面看起来正常但永远没数据。这里直接抛错。
 */
export function expectList(payload: any, what: string): any[] {
  if (!payload || !Array.isArray(payload.items)) {
    throw new Error(
      `接口返回结构异常（期望 items 数组，实际 ${typeof payload}）：${what}。`
      + '多半是 /api/eval 或 /api/evolution 代理未配置，请求被 SPA 兜底成页面 HTML。',
    );
  }
  return payload.items;
}

export const pct = (v: number | null | undefined): string =>
  v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`;

/** 门禁结论徽标：release_ok 才是发布依据；只看"未回退"会漏接长期坏掉的层。 */
export function GateBadge({ v }: { v: PrecheckSuite }) {
  if (!v.has_run) {
    return <Badge variant="destructive" className="text-[9px]">无运行</Badge>;
  }
  if (v.release_ok) {
    return <Badge variant="secondary" className="text-[9px]">通过</Badge>;
  }
  return (
    <Badge variant="destructive" className="text-[9px]">
      {v.floor_ok ? '相对回退' : '低于下限'}
    </Badge>
  );
}

/** 失败原因归一成分桶键：让"10 条同一原因"一眼看成一类问题，而不是 10 个问题。 */
export function clusterKey(reason: string): string {
  const r = (reason || '').trim();
  if (!r) return '（无原因）';
  return r.split(/[:：（(]/)[0].slice(0, 42) || '（无法归类）';
}
