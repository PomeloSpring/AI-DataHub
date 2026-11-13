/**
 * 引入数据集时的「参数自动装配」计划（纯函数，无 React、无请求）。
 *
 * 输入是三份事实源：数据集清单里的 `param_summary`（服务端 `semantics.param_plan` 算的，
 * 前端**不另推一套**）、看板现有 `page_params`、画布上已有控件绑定的键。
 * 输出是一份可勾选、可确认、可幂等重跑的计划。
 *
 * 边界（与 2026-11-06 拍板一致）：
 *  - 参数的口径权威在数据集声明，这里只决定"看板上要开哪些逻辑参数、放哪些控件"；
 *  - 判不出形状的（pending）与口径常数（constant）**不自动建控件**，只在弹窗里说明原因，
 *    不得为了"看起来自动"塞一个文本框进去（no-silent-degradation）；
 *  - 已有同名参数一律复用，不重复新建；控件按 `boundKeys` 去重，可以反复点「引入」而不长出一排控件。
 */
import type { DashboardParam, PageParam } from '@/stores/dashboardStore';

/** `GET /datasets/` 行上的 `param_summary` 项（后端 param_plan.param_summary 同形） */
export interface DatasetParamSummary {
  key: string;
  label?: string;
  kind?: string;               // time_window | scalar | range | constant | unknown
  value_type?: string;         // date_range | date | number | enum | list | boolean | string
  control?: string;            // widget_*（空 = 不自动建控件）
  options?: string[];
  required?: boolean;
  default?: any;
  referenced?: boolean;
  conflicts?: string[];
  pending?: boolean;
  suggested_label?: string;
  fallback_days?: number;
  vars?: { var: string; role?: string; type?: string; default?: any; dataset_id?: number }[];
}

export interface DatasetWithParams {
  id: number;
  name: string;
  param_summary?: DatasetParamSummary[];
}

export interface ControlSpec {
  /** 逻辑参数键（控件写入看板参数的键，与后端 bound_args 的取值键一致） */
  key: string;
  label: string;
  control: string;             // widget_*
  value_type: string;
  options: string[];
  datasetNames: string[];
  /** 数据集正文在没有看板值时的回退窗口（天数），用于给"近 N 天"预设做默认勾选 */
  fallback_days?: number;
}

export interface AssemblyPlan {
  addParams: PageParam[];
  reuseParams: { key: string; label: string; type: PageParam['type'] }[];
  /** 已有同名参数但类型与数据集要的形状不一致 → 需要改判（不自动改，弹窗里显式列出） */
  mismatchParams: { key: string; label: string; current: PageParam['type']; want: PageParam['type']; datasetNames: string[] }[];
  addControls: ControlSpec[];
  /** 画布上已有控件在驱动的键 → 不再重复放控件 */
  keptControls: string[];
  /**
   * 要一起补上的「搜索 / 重置」按钮（2026-11-10 拍板）：控件只负责改值，
   * 取数靠这两个按钮——引入带参数的图表时若画布上没有，参数就“填了不会生效”。
   */
  addButtons: { control: 'widget_search' | 'widget_reset'; label: string }[];
  /** 画布上已有同类按钮 → 不重复放 */
  keptButtons: string[];
  pending: { key: string; label: string; reason: string; datasetNames: string[] }[];
  constants: { key: string; label: string; datasetNames: string[] }[];
  conflicts: string[];
  notes: string[];
}

export const EMPTY_ASSEMBLY_PLAN: AssemblyPlan = {
  addParams: [], reuseParams: [], mismatchParams: [], addControls: [], keptControls: [],
  addButtons: [], keptButtons: [], pending: [], constants: [], conflicts: [], notes: [],
};

/** 形状的业务名（装配弹窗与数据集页共用一份，两处各自翻译就会漂移） */
export const KIND_LABELS: Record<string, string> = {
  time_window: '统计时间窗口',
  scalar: '单值筛选',
  range: '区间（形态待确认）',
  constant: '口径常数（函数实参）',
  unknown: '未识别形状',
};

export const VALUE_TYPE_LABELS: Record<string, string> = {
  date_range: '日期区间', date: '日期', number: '数字', enum: '枚举候选',
  list: '多选候选', boolean: '是/否', string: '文本', '': '形态待定',
};

/** 变量在正文里扮演的角色（数据集页解释"这个入参是怎么用的"，不说术语） */
export const ROLE_LABELS: Record<string, string> = {
  range_start: '区间起点', range_end: '区间终点', window_len: '相对窗口长度',
  optional_eq: '可空等值筛选', eq: '必填等值筛选', func_arg: '函数实参（口径常数）',
  other: '用法未识别',
};

/** 逻辑参数值类型 → 看板参数类型（enum/list 落选择框，不能降级成文本框） */
export function pageParamTypeOf(valueType: string): PageParam['type'] {
  if (valueType === 'date_range') return 'date_range';
  if (valueType === 'date') return 'date';
  if (valueType === 'number') return 'number';
  if (valueType === 'enum' || valueType === 'list') return 'select';
  return 'string';
}

/**
 * 看板参数的形状（两套命名空间 `params` / `page_params` 收敛为一份词汇）。
 * `text` 是遗留 `params` 的历史别名，`enum` 是数据集声明里的叫法，都归一到同一形状；
 * 判不准的一律按文本，不猜一个日期控件（选了不生效比不给控件更坑）。
 */
export type ParamShape = 'string' | 'number' | 'date' | 'date_range' | 'select';

export const PARAM_SHAPES: ParamShape[] = ['string', 'number', 'date', 'date_range', 'select'];

export const PARAM_SHAPE_LABELS: Record<ParamShape, string> = {
  string: '文本', number: '数字', date: '日期', date_range: '日期区间', select: '选择框',
};

/** 形状 → 画布控件（遗留参数“转成控件”时用，与 CONTROL_LABELS 同源） */
export const CONTROL_BY_PARAM_SHAPE: Record<ParamShape, string> = {
  string: 'widget_text', number: 'widget_number', date: 'widget_date',
  date_range: 'widget_daterange', select: 'widget_select',
};

export function shapeOfParam(param: { type?: string }): ParamShape {
  switch (String(param?.type || 'string')) {
    case 'text': case 'string': return 'string';
    case 'number': return 'number';
    case 'date': return 'date';
    case 'date_range': return 'date_range';
    case 'select': case 'enum': case 'list': return 'select';
    default: return 'string';
  }
}

/** 画布控件类型 → 参数形状（CONTROL_BY_PARAM_SHAPE 的反向，保证两处用同一门词汇） */
export function shapeOfControl(control: string): ParamShape {
  const hit = (Object.keys(CONTROL_BY_PARAM_SHAPE) as ParamShape[])
    .find(shape => CONTROL_BY_PARAM_SHAPE[shape] === control);
  return hit || 'string';
}

/**
 * 占位提示的唯一文案：“动作 + 业务名”。看板顶部参数带与画布控件共用，两处各写一套就会漂移。
 * label 为空时只给动作词，不编造名字。
 */
export function placeholderOfShape(shape: ParamShape, label: string): string {
  const suffix = String(label || '').trim() ? ` ${String(label).trim()}` : '';
  switch (shape) {
    case 'select': return `请选择${suffix}`;
    case 'date': return `选择日期${suffix}`;
    case 'date_range': return `选择日期区间${suffix}`;
    default: return `请输入${suffix}`;
  }
}

/**
 * 待确认/口径常数行用的"身份名"：这类内容属于数据集内部，变量名本身才是有用信息，
 * 不得替换成形状建议名（否则一排「口径常数」看不出到底是谁）。
 */
export function identityLabelOf(p: DatasetParamSummary): string {
  return String(p.label || '').trim() || p.key;
}

/** 显示名：数据集声明的 label 优先，其次形状建议名，最后才是键名（不裸显内部标识） */
export function paramLabelOf(p: DatasetParamSummary): string {
  const declared = String(p.label || '').trim();
  if (declared && declared !== p.key) return declared;
  return String(p.suggested_label || '').trim() || p.key;
}

/** 控件类型 → 业务名（与 `CHART_TYPES` 里的 widget 项一致，有用例比对防漂移） */
export const CONTROL_LABELS: Record<string, string> = {
  widget_daterange: '日期范围',
  widget_date: '日期选择器',
  widget_number: '数字输入框',
  widget_select: '下拉选择框',
  widget_multi_select: '多选下拉框',
  widget_text: '文本输入框',
};

const PENDING_REASONS: Record<string, string> = {
  range: '这是一对区间参数（如 min/max），平台无法确定它是时间还是数值，请在数据集页指定值形态后再装配',
  unknown: '正文里的用法无法判定形状，不自动建控件（建错了会给出一个不生效的筛选器）',
  constant: '',
};

/**
 * 汇总多个数据集的入参 → 装配计划。
 *
 * @param datasets   本次引入的数据集（含 param_summary）
 * @param pageParams 看板现有参数（`page_params`）
 * @param boundKeys  画布上已有控件绑定的键（`config.paramKey` / `config.bind_param`）
 * @param existingControls 画布上已有的控件类型（`chart_type`），用于按钮去重
 */
export function buildAssemblyPlan(datasets: DatasetWithParams[],
                                  pageParams: PageParam[] = [],
                                  boundKeys: string[] = [],
                                  existingControls: string[] = []): AssemblyPlan {
  const plan: AssemblyPlan = {
    addParams: [], reuseParams: [], mismatchParams: [], addControls: [], keptControls: [],
    addButtons: [], keptButtons: [], pending: [], constants: [], conflicts: [], notes: [],
  };
  const byKey = new Map<string, DatasetParamSummary>();

  for (const ds of datasets || []) {
    for (const p of ds.param_summary || []) {
      const merged = byKey.get(p.key);
      if (!merged) {
        byKey.set(p.key, { ...p, options: [...(p.options || [])] });
      } else {
        // 同一逻辑参数被多个数据集用到：候选值取并集，分歧按后端算好的 conflicts 收集
        for (const opt of p.options || []) {
          if (!merged.options?.includes(opt)) merged.options = [...(merged.options || []), opt];
        }
        if (!merged.control && p.control) merged.control = p.control;
        if (merged.value_type !== p.value_type && p.value_type) {
          plan.conflicts.push(`「${paramLabelOf(p)}」在 ${ds.name} 里的值类型是 ${p.value_type}，`
            + `与另一份声明（${merged.value_type || '未定'}）不一致，取数时按各数据集自身声明校验`);
        }
      }
      (p.conflicts || []).forEach(c => plan.conflicts.push(`${paramLabelOf(p)}：${c}`));
    }
  }

  const datasetNamesOf = (key: string) => (datasets || [])
    .filter(ds => (ds.param_summary || []).some(p => p.key === key))
    .map(ds => ds.name);

  for (const p of byKey.values()) {
    const existing = (pageParams || []).find(x => x.name === p.key);
    // 看板上已有的叫法优先：改数据集显示名是数据集页的事，装配不该顺手换掉用户熟悉的词
    const label = String(existing?.label || '').trim() || paramLabelOf(p);
    const names = datasetNamesOf(p.key);

    if (p.kind === 'constant') {
      plan.constants.push({ key: p.key, label: identityLabelOf(p), datasetNames: names });
      continue;
    }
    // 正文没引用的入参（如只读常量）：登记参数但不放控件，否则是个“填了也不参与取数”的控件
    const unreferencedOnly = p.referenced === false && !p.pending && p.kind !== 'constant';
    if (p.pending || !p.control) {
      const reason = PENDING_REASONS[p.kind || 'unknown']
        || (p.referenced === false ? '数据集正文未引用该入参，无需在看板上给值' : '值形态待定，请在数据集页确认后再生成控件');
      plan.pending.push({ key: p.key, label: paramLabelOf(p), reason, datasetNames: names });
      continue;
    }

    const want = pageParamTypeOf(p.value_type || 'string');
    if (existing) {
      if (existing.type !== want) {
        plan.mismatchParams.push({
          key: p.key, label, current: existing.type, want, datasetNames: names,
        });
      } else {
        plan.reuseParams.push({ key: p.key, label, type: existing.type });
      }
    } else {
      plan.addParams.push({ name: p.key, label, type: want, default: defaultTextOf(p, want) });
    }

    if ((boundKeys || []).includes(p.key) || unreferencedOnly) {
      if ((boundKeys || []).includes(p.key)) plan.keptControls.push(p.key);
      continue;
    }
    plan.addControls.push({
      key: p.key, label, control: p.control, value_type: p.value_type || 'string',
      options: p.options || [], datasetNames: names, fallback_days: p.fallback_days,
    });
  }

  // 归一事实要说出来：用户最关心"我一个日期控件是不是真的管住了这几张图"
  const tw = plan.addControls.filter(c => c.key === 'time_window');
  if (tw.length) {
    plan.notes.push('统计时间窗口按各数据集正文的形状落地：写成起止的按端点、写成相对天数的按天数派生，两者口径一致');
  }
  const reused = new Set((pageParams || []).map(x => x.name));
  const shared = [...byKey.values()].filter(p => datasetNamesOf(p.key).length > 1 && reused.has(p.key));
  shared.forEach(p => plan.notes.push(`「${paramLabelOf(p)}」已由看板上现有参数提供，多个数据集共用同一个控件`));

  // 站点这类“看着像筛选值、其实决定去哪个源取数”的通道必须说清，否则用户会以为下拉框只是个普通筛选
  const siteParam = [...byKey.values()].find(p => p.key === 'site' && p.control);
  if (siteParam) {
    plan.notes.push('「站点」决定去哪个站点取数（业务值，不是内部数据源标识）：留空按当前站分别呈现，不跨站合计');
  }
  // 正文没引用的入参同样要说人话：用户会奇怪“为什么登记了参数却没控件”
  const unreferenced = [...byKey.values()].filter(p => p.referenced === false && p.kind !== 'constant' && !p.pending);
  if (unreferenced.length) {
    plan.notes.push(`只读入参「${unreferenced.map(p => paramLabelOf(p)).join('」「')}」正文并未引用，只登记参数、不放控件（值不会参与取数）`);
  }

  // 「搜索」「重置」是参数的生效入口：控件只改值，取数靠这两个按钮。
  // 本次涉及参数（新建、放控件，或已有控件在驱动）而画布上没按钮时，参数就是“填了不会生效”——跟着一起补（可勾掉）。
  if (plan.addControls.length + plan.addParams.length + plan.mismatchParams.length + plan.keptControls.length > 0) {
    const has = (t: string) => (existingControls || []).includes(t);
    ([['widget_search', '搜索'], ['widget_reset', '重置']] as const).forEach(([control, label]) => {
      if (has(control)) plan.keptButtons.push(label);
      else plan.addButtons.push({ control, label });
    });
  }
  return plan;
}

function defaultTextOf(p: DatasetParamSummary, want: PageParam['type']): string {
  // 时间窗口不给"假装已选"的默认值：没值时后端会报未绑定，图卡上看得见，比默认近 30 天诚实
  if (want === 'date_range') return '';
  const d = p.default;
  if (d === null || d === undefined) return '';
  return String(d);
}

/** 控件摆放：从锚点开始一行排开，超出画布宽度自动换行（幂等重跑时不会叠在同一格） */
export function suggestControlPositions(count: number, anchor: { x: number; y: number },
                                        size: { w: number; h: number } | { w: number; h: number }[],
                                        canvasWidth = 1200) {
  // 每个控件用自己的自然宽（日期区间比文本框宽得多），否则一排里区间控件会被挤到只显一半
  const sizes = Array.isArray(size) ? size : new Array(Math.max(0, count)).fill(size);
  const out: { x: number; y: number }[] = [];
  let x = anchor.x, y = anchor.y;
  for (let i = 0; i < count; i++) {
    const w = sizes[i]?.w ?? anchor.x;
    if (i > 0 && x + w > canvasWidth) { x = anchor.x; y += (sizes[i]?.h ?? 32) + 12; }
    out.push({ x, y });
    x += w + 12;
  }
  return out;
}

/** 从图表行上收集"已有控件在驱动哪些键"（装配去重用，命名空间与看板参数一致） */
export function boundParamKeys(charts: { chart_type?: string; config?: any }[]): string[] {
  const keys = new Set<string>();
  for (const c of charts || []) {
    if (!String(c?.chart_type || '').startsWith('widget_')) continue;
    const pk = String(c?.config?.paramKey || '').trim();
    if (pk) keys.add(pk);
    String(c?.config?.bind_param || '').split(',').map(s => s.trim()).filter(Boolean)
      .forEach(k => keys.add(k));
  }
  return [...keys];
}

/**
 * 顶部参数带要呈现的参数：两套定义（遗留 `params` + `page_params`）里**没被
 * 画布控件驱动**的那部分。返回空数组就意味着这个入口不该存在。
 *
 * 大屏「筛选条件」按钮与参数带共用同一判定，否则会出现“按钮在、点开是空面板”
 * 的空承诺（现网 7 个看板的 start_time/end_time 全由日期区间控件 bind_param 驱动）。
 *
 * 同一个名字在两套命名空间里都登记过时只留 `page` 那一份：下发是
 * `{...paramValues, ...pageParamValues}`，page 赢，再渲染一份 params 的框就是第二个死输入口。
 */
export function uncontrolledParams(
  dashboard: { params?: DashboardParam[]; page_params?: PageParam[] },
  charts: { chart_type?: string; config?: any }[],
): { param: DashboardParam | PageParam; via: 'param' | 'page' }[] {
  const driven = boundParamKeys(charts || []);
  const page = (dashboard.page_params || []).filter(p => !driven.includes(p.name));
  const legacy = (dashboard.params || []).filter(p => !driven.includes(p.name) && !page.some(q => q.name === p.name));
  return [
    ...legacy.map(param => ({ param, via: 'param' as const })),
    ...page.map(param => ({ param, via: 'page' as const })),
  ];
}

/**
 * 参数行里**可编辑**的那部分（排除只读的看板级常数）。
 *
 * 判定“参数带/筛选入口要不要存在”必须看这个，而不是 `uncontrolledParams` 的全量：
 * 单 App 看板上 app_code 作为看板级常数登记，页面上不开输入口，于是那一行只剩
 * 一个改不了的呈现 + 一套「搜索/重置」—— 而画布已经自带日期区间/站点/查询/重置。
 * 点了也不会改变任何条件，属于空承诺（与上面“按钮在、点开是空面板”同一类）。
 */
export function editableParamRows(
  rows: { param: DashboardParam | PageParam; via: 'param' | 'page' }[],
): { param: DashboardParam | PageParam; via: 'param' | 'page' }[] {
  return rows.filter(r => !(r.param as PageParam).readonly);
}

/** 后端下发的未接参数项（与 `dashboardStore.ChartParamUnbound` 同形） */
export interface ParamUnboundLike {
  key: string; label?: string; vars?: string[]; partial?: boolean;
}

/** 角标诊断上下文：本次提交了哪些条件、未接参数在本看板上叫什么业务名 */
export interface ParamDiagnosticCtx {
  filledKeys?: string[];
  nameOf?: (u: ParamUnboundLike) => string;
}

/**
 * 未接参数在**本看板上**的业务名（显示用 name，ui-resource-display）。
 *
 * 后端给的 label 常常就是变量名（site / serial_number / time_window），用户读不懂；
 * 而看板上这个条件其实叫「区域」「设备序列号」「日期区间」。优先级：
 * 控件 label → 控件 placeholder（很多输入框只填了占位词）→ 看板参数 label → 后端 label → 键名。
 * 控件可能用 `bind_param` 直接绑变量名（逻辑键 time_window → start_time,end_time），所以 vars 也要参与匹配。
 */
export function paramBusinessName(
  u: ParamUnboundLike,
  sources: { charts?: { chart_type?: string; config?: any }[]; params?: { name?: string; label?: string }[] },
): string {
  const wanted = new Set([u.key, ...(u.vars || [])].map(k => String(k || '').trim()).filter(Boolean));
  for (const chart of sources.charts || []) {
    if (!String(chart.chart_type || '').startsWith('widget_')) continue;
    const cfg: any = chart.config || {};
    const bound = new Set<string>([
      String(cfg.paramKey || '').trim(),
      ...String(cfg.bind_param || '').split(',').map(v => String(v).trim()),
    ].filter(Boolean));
    if (![...wanted].some(k => bound.has(k))) continue;
    const name = String(cfg.label || '').trim() || String(cfg.placeholder || '').trim();
    if (name) return name;
  }
  for (const param of sources.params || []) {
    if (wanted.has(String(param?.name || '').trim())) {
      const name = String(param?.label || '').trim();
      if (name) return name;
    }
  }
  return String(u.label || u.key || '');
}

/**
 * 图卡角标用的诊断文案。
 *
 * 判据只有一条：**用户这次真给了值、而这张图没接住**才告警。
 * 旧版把“未绑定”一律挂成「未按看板筛选」，于是首屏（什么条件都没填、本就该出全量）
 * 每张带参图都顶个橙色角标，用户读到的是行话而不是问题（2026-11-10 反馈）。
 * `filledKeys` 缺省 = 调用方没给提交上下文（如编辑器试运行），保守提示全部未接项。
 */
export function paramDiagnosticHint(diag?: {
  unbound?: ParamUnboundLike[]; notes?: string[] },
  ctx?: ParamDiagnosticCtx
): { text: string; title: string; partial: boolean } | null {
  const unbound = diag?.unbound || [];
  if (!unbound.length) return null;
  const filled = ctx?.filledKeys;
  const hit = filled
    ? unbound.filter(u => filled.includes(u.key) || (u.vars || []).some(v => filled.includes(v)))
    : unbound;
  if (!hit.length) return null;      // 没给值 = 按数据集自身口径出全量，是预期态，不挂告警
  const names = hit.map(u => `「${ctx?.nameOf?.(u) || u.label || u.key}」`);
  const partial = hit.some(u => u.partial);
  const title = partial
    ? `${names.join('、')}的区间只给到一端，另一端按数据集自身默认值补齐，本次口径不完整`
    : `${names.join('、')}已经填了值，但这张图没接住，本次仍按数据集自身口径出数（没按你看板选的条件筛）`;
  return { text: partial ? '筛选区间不完整' : '已填条件未生效', title, partial };
}
