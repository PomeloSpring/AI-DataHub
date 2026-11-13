import { isVisUsable, type VisComponent } from '@/api/visLibrary';

/**
 * 界面动效契约 —— 「出现效果」的唯一事实源（入场 / 图表 / 切板 / 正文）。
 *
 * 为什么与主题包正交：主题包（`tp_*`）管颜色与版式，动效管时间维度，二者互不隶属 ——
 * 每套主题都能搭配任意动效预设。所以本模块**不参与** `sanitizePack`，也不从主题包里取段值，
 * 动效字模走独立分类 `motion_preset`（见 api/visLibrary 的 VIS_CATEGORIES）。
 *
 * 三面消费（Chat 图表、看板/大屏播放、内容详情）一律经 `resolveMotion` 解析，
 * 禁止各页面自写 @keyframes（对齐 .qoder/rules/ui-vis-library-style.md：唯一样式源 + 字段必须真实消费）。
 *
 * 默认零变化（硬约束）：`DEFAULT_MOTION` 逐段等于现网实现 ——
 * `enter` = 0.45s cubic-bezier(0.22,1,0.36,1) + 10px 位移（= globals.css 的 `stageSlideIn`，
 * 入场 keyframes 也复用同一条，保证「默认预设」与历史观感逐字节同源）、
 * `chart.appear='default'` = **不往 G2 spec 写 `animate` 键**、
 * `transition/reveal='none'` = 今天切板硬换、正文一次性出现。
 */

// ── 效果枚举（值名即落地实现名，不另造翻译层）──────────────────────────────

/**
 * 入场效果：CSS class 后缀 = 这里的名字（`.motion-enter-fadeUp`），`none` = 不加 class。
 * 命名按「元素从哪来」而不是「位移方向」（slideUp 同时能读出两种意思，历史上已反复歧义）：
 * fadeUp=自下而上淡入（现网默认）、slideDown=自上而下、slideLeft=自右向左、scale=缩放渐入、
 * blur=模糊渐显、wipeDown=擦除展开（clip-path）、fade=纯淡入。
 * pop / flipUp 是为「预设之间肉眼可辨」补的两条强通道：前者位移+缩放+回弹（卡片拍上台），
 * 后者透视翻入（与所有 2D 平移完全不同）。只动 20px 位移的淡入之间确实分不出差，
 * 那不是预设该长的样子（用户反馈）。
 */
export const ENTER_EFFECTS = ['none', 'fadeUp', 'fade', 'slideDown', 'slideLeft', 'scale', 'blur', 'wipeDown', 'pop', 'flipUp'] as const;
/**
 * 图表自身入场：`default` = 不写 animate 键（现状）；其余值为 @antv/g2 动画注册表名
 * （`animation.fadeIn` → spec 里写 `'fadeIn'`），未知值会让 G2 静默不播，所以按清单校验。
 */
export const CHART_APPEARS = ['default', 'none', 'fadeIn', 'pathIn', 'growInY', 'waveIn', 'zoomIn'] as const;
/** 切板转场（看板/大屏播放只本期这一档，不含刷新渐入与数字滚动） */
export const TRANSITION_EFFECTS = ['none', 'fadeIn', 'pushLeft', 'zoomIn', 'wipeDown'] as const;
/** 正文出现（内容详情/编排预览）；`sectionStagger` = 同 fadeIn 但按分段错峰延迟 */
export const REVEAL_EFFECTS = ['none', 'fadeIn', 'sectionStagger'] as const;
/** 缓动令牌（一处定义，CSS 与 G2 共用，避免两套曲线口径） */
export const MOTION_EASINGS = ['standard', 'snappy', 'linear'] as const;

export type EnterEffect = typeof ENTER_EFFECTS[number];
export type ChartAppear = typeof CHART_APPEARS[number];
export type TransitionEffect = typeof TRANSITION_EFFECTS[number];
export type RevealEffect = typeof REVEAL_EFFECTS[number];
export type MotionEasing = typeof MOTION_EASINGS[number];

export const EASING_CSS: Record<MotionEasing, string> = {
  standard: 'cubic-bezier(0.22, 1, 0.36, 1)',   // 现网 stageSlideIn 同款（先快后缓）
  snappy: 'cubic-bezier(0.16, 1, 0.3, 1)',
  linear: 'linear',
};

export interface MotionEnter {
  effect: EnterEffect; durationMs: number; easing: MotionEasing;
  staggerMs: number; maxStaggerMs: number; offsetPx: number;
}
export interface MotionChart { appear: ChartAppear; durationMs: number; easing: MotionEasing }
export interface MotionTransition { effect: TransitionEffect; durationMs: number; easing: MotionEasing }
export interface MotionReveal { effect: RevealEffect; staggerMs: number }
export interface MotionSpec { enter: MotionEnter; chart: MotionChart; transition: MotionTransition; reveal: MotionReveal }

/** 用户差量偏好：只覆盖填了的键（来自 adh_user_preferences.motion_override） */
export type MotionOverride = Partial<Record<keyof MotionSpec, Record<string, unknown>>>;

/** 内置预设编码：逐段等于现网观感（库里 seed 的同名行是它的可编辑副本） */
export const DEFAULT_PRESET_CODE = 'mp_default';
/** 未跑迁移/字模缺失时的兜底名（渲染面显示来源用，遵守 ui-resource-display：显示名而非裸编码） */
export const DEFAULT_PRESET_NAME = '默认（现网）';

export const DEFAULT_MOTION: MotionSpec = {
  enter: { effect: 'fadeUp', durationMs: 450, easing: 'standard', staggerMs: 90, maxStaggerMs: 450, offsetPx: 10 },
  chart: { appear: 'default', durationMs: 600, easing: 'standard' },
  transition: { effect: 'none', durationMs: 500, easing: 'standard' },
  reveal: { effect: 'none', staggerMs: 90 },
};

// ── 白名单与数值护栏（漏登记 = 入库即剥，属 ui-vis-library-style §2 那类缺陷）──

export const MOTION_SECTION_KEYS = {
  enter: ['effect', 'durationMs', 'easing', 'staggerMs', 'maxStaggerMs', 'offsetPx'],
  chart: ['appear', 'durationMs', 'easing'],
  transition: ['effect', 'durationMs', 'easing'],
  reveal: ['effect', 'staggerMs'],
} as const satisfies Record<keyof MotionSpec, readonly string[]>;

const NUMBER_BOUNDS: Record<string, [number, number]> = {
  durationMs: [0, 5000], staggerMs: [0, 1000], maxStaggerMs: [0, 5000], offsetPx: [0, 200],
};

const inList = <T extends string>(list: readonly T[], v: unknown): v is T =>
  typeof v === 'string' && (list as readonly string[]).includes(v);

/** 段内某个键的合法值（非法→默认值），同时给写入口留错误文案 */
function coerce(section: keyof MotionSpec, key: string, raw: unknown, fallback: any, invalid: string[]) {
  if (key === 'effect' || key === 'appear') {
    const list = section === 'enter' ? ENTER_EFFECTS : section === 'chart' ? CHART_APPEARS
      : section === 'transition' ? TRANSITION_EFFECTS : REVEAL_EFFECTS;
    if (inList(list, raw)) return raw;
    if (raw !== undefined && raw !== null) invalid.push(`${section}.${key}="${String(raw)}" 不是可选效果`);
    return fallback;
  }
  if (key === 'easing') {
    if (inList(MOTION_EASINGS, raw)) return raw;
    if (raw !== undefined && raw !== null) invalid.push(`${section}.easing="${String(raw)}" 不是可选缓动`);
    return fallback;
  }
  const [lo, hi] = NUMBER_BOUNDS[key] || [0, 5000];
  if (raw === undefined || raw === null) return fallback;
  const n = typeof raw === 'number' ? raw : Number(raw);
  // 给了值却不是一个数（'fast' / NaN / Infinity）= 配置写错，必须报出来：
  // 当“没设”静默回落，用户会以为旋钮生效了（存了不生效同类）
  if (!Number.isFinite(n)) { invalid.push(`${section}.${key}="${String(raw)}" 不是数字`); return fallback; }
  if (n < lo || n > hi) { invalid.push(`${section}.${key}=${n} 超出 ${lo}~${hi}`); return fallback; }
  return n;
}

function mergeSection<T extends Record<string, any>>(
  defaults: T, section: keyof MotionSpec, raw: any, invalid: string[],
): T {
  const out = { ...defaults };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    if (raw !== undefined && raw !== null) invalid.push(`${section} 段必须是对象`);
    return out;
  }
  const allowed = MOTION_SECTION_KEYS[section] as readonly string[];
  // 白名单外的键（自己拼一个 easingCurve）也要显式报出来：静默剥掉就是“存了不生效”
  for (const key of Object.keys(raw)) {
    if (!allowed.includes(key)) invalid.push(`${section}.${key} 不是可调配额，可选：${allowed.join(', ')}`);
  }
  for (const key of allowed) {
    out[key as keyof T] = coerce(section, key, raw[key], out[key as keyof T], invalid);
  }
  return out;
}

/**
 * 净化动效配置（渲染面用）：只认白名单段/键，非法值回落默认并记入 `invalid`。
 * 返回的 `invalid` 非空时，写入口（字模库表单、看板属性面板）必须显式报错；
 * 渲染面不因此静默假装「已按配置」—— 来源与回退事实由 `resolveMotion` 一并回传。
 */
export function sanitizeMotion(raw: any): { spec: MotionSpec; invalid: string[] } {
  const invalid: string[] = [];
  const spec: MotionSpec = {
    enter: mergeSection(DEFAULT_MOTION.enter, 'enter', raw?.enter, invalid),
    chart: mergeSection(DEFAULT_MOTION.chart, 'chart', raw?.chart, invalid),
    transition: mergeSection(DEFAULT_MOTION.transition, 'transition', raw?.transition, invalid),
    reveal: mergeSection(DEFAULT_MOTION.reveal, 'reveal', raw?.reveal, invalid),
  };
  return { spec, invalid };
}

/** 写入口校验（管理页/属性面板保存前调用）：返回可直接展示的错误清单，空数组=可保存 */
export function validateMotion(raw: any): string[] {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return ['动效配置必须是对象（含 enter / chart / transition / reveal 段）'];
  const unknown = Object.keys(raw).filter(k => !(k in MOTION_SECTION_KEYS));
  const errors = unknown.map(k => `未知段「${k}」，可选段：${Object.keys(MOTION_SECTION_KEYS).join(' / ')}`);
  return [...errors, ...sanitizeMotion(raw).invalid];
}

// ── 预设解析 ──────────────────────────────────────────────────────────────

/** 字模行/挂载快照的最小形状（不依赖 dashboardDesign，避免双向耦合） */
export interface MotionRefLike { category?: string; code?: string; revision?: string; style_config?: Record<string, any> }
/** `override` = 这块板自己的时长/错峰微调（存 `filters.design.motionOverride`）：
 *  预设是共用的，但“这块板想快一点”不该去改全局字模。 */
export interface MotionBoardRef { ref?: string | null; snapshot?: MotionRefLike | null; override?: MotionOverride | null }
export interface MotionUserPref { presetCode?: string | null; override?: MotionOverride | null }

export interface MotionResolution {
  motion: MotionSpec;
  /** 生效预设的业务名（UI 显示用）与编码（调试/title 用） */
  preset: { code: string; name: string };
  /** 生效来源：reduced=无障碍压制 > board=板上挂载 > user=用户偏好 > default=内置默认 */
  origin: 'reduced' | 'board' | 'user' | 'default';
  /** 被引用但已下架/禁用的预设编码（与 dashboardDesign 的 missing 同口径） */
  missing: string[];
  /** 段内非法键（渲染按默认值走，写入口必须拒绝保存） */
  invalid: string[];
  /** 可诊断说明：禁用回退 / 未 seed / reduced-motion 生效的事实，渲染面与配置面都要显示 */
  reason?: string;
}

/** 在用预设清单（顺序即下拉顺序：默认档排首，其余按 sort_order） */
export function motionPresets(items: VisComponent[]): VisComponent[] {
  // 不用三元嵌套的比较函数（不自洽会让排序结果依赖引擎实现）：给默认档一个足够小的排名
  const rank = (c: VisComponent) => (c.code === DEFAULT_PRESET_CODE ? -1e9 : 0) + (c.sort_order || 0);
  return items
    .filter(c => c.category === 'motion_preset' && isVisUsable(c))
    .sort((a, b) => rank(a) - rank(b) || a.code.localeCompare(b.code));
}

function usablePreset(items: VisComponent[], code: string) {
  const hit = items.find(c => c.category === 'motion_preset' && c.code === code);
  return hit && isVisUsable(hit) ? hit : undefined;
}

/** 库优先、快照只兜底：拿到在用字模就用最新定义，只有下架/禁用才回退挂载时快照 */
function resolveBoardStyle(board: MotionBoardRef, items: VisComponent[]) {
  const code = typeof board.ref === 'string' ? board.ref : '';
  const component = code ? usablePreset(items, code) : undefined;
  const snap = board.snapshot;
  const snapshotUsable = !!snap && snap.category === 'motion_preset' && !!snap.style_config;
  return {
    style: component?.style_config || (snapshotUsable ? snap!.style_config : undefined),
    code: component?.code || snap?.code || code,
    name: component?.name,
    // 引用失效=字模已下架/禁用（仍可能有快照），missing 只在这种情况下记账
    unusableRef: !!code && !component,
    hasSnapshot: snapshotUsable,
  };
}

/** 逐键合并在预设之上（不逐段合并：否则差量只写 durationMs 会把预设的 effect 一起打回默认值） */
function applyOverride(spec: MotionSpec, override: MotionOverride, invalid: string[]): MotionSpec {
  const merged = sanitizeMotion({
    enter: { ...spec.enter, ...(override.enter || {}) },
    chart: { ...spec.chart, ...(override.chart || {}) },
    transition: { ...spec.transition, ...(override.transition || {}) },
    reveal: { ...spec.reveal, ...(override.reveal || {}) },
  });
  invalid.push(...merged.invalid);
  return merged.spec;
}

/**
 * 三面统一解析入口。
 *
 * 优先级（分面裁决）：
 * - `reducedMotion` 永远压过一切（无障碍优先，且必须可见标注，不静默换一套）；
 * - 传入 `board` 即为「看板/大屏播放」面：板上挂载 > 用户偏好 > 内置默认；
 * - 不传 `board` 即为「Chat 图表 / 内容详情」面：用户偏好 > 内置默认；
 * - 用户差量（时长/错峰）**只**在生效预设来自用户偏好时叠加 ——
 *   否则管理员精心挂好的板上动效会被观看者本地差量改掉，与分面裁决相违。
 */
export function resolveMotion(opts: {
  items: VisComponent[];
  userPref?: MotionUserPref | null;
  board?: MotionBoardRef | null;
  reducedMotion?: boolean;
}): MotionResolution {
  const { items, userPref = null, board = null, reducedMotion = false } = opts;
  const missing: string[] = [];
  const invalid: string[] = [];
  let fallback = '';
  let spec: MotionSpec | undefined;
  let preset = { code: DEFAULT_PRESET_CODE, name: DEFAULT_PRESET_NAME };
  let origin: MotionResolution['origin'] = 'default';
  let fromUser = false;

  // 1) 板上挂载（看板/大屏面）：引用失效要同时留 missing 与可诊断说明，不得静默换一套
  if (board && (board.ref || board.snapshot)) {
    const resolved = resolveBoardStyle(board, items);
    if (resolved.style) {
      const merged = sanitizeMotion(resolved.style);
      spec = merged.spec;
      invalid.push(...merged.invalid);
      // 板级差量叠在挂载预设之上（与个人差量同一套逐键口径）
      if (board.override) spec = applyOverride(spec, board.override, invalid);
      preset = { code: resolved.code, name: resolved.name || '挂载时快照' };
      origin = 'board';
      if (resolved.unusableRef) {
        missing.push(resolved.code);
        fallback = `动效预设「${resolved.code}」已下架或禁用，已按挂载时快照呈现`;
      }
    } else if (resolved.code) {
      missing.push(resolved.code);
      fallback = `动效预设「${resolved.code}」不在字模库（已下架且无挂载快照），已回退默认动效`;
    }
  }

  // 2) 用户偏好预设
  const userCode = userPref?.presetCode || '';
  if (!spec && userCode) {
    const component = usablePreset(items, userCode);
    if (component) {
      const merged = sanitizeMotion(component.style_config);
      spec = merged.spec;
      invalid.push(...merged.invalid);
      preset = { code: component.code, name: component.name };
      origin = 'user';
      fromUser = true;
    } else {
      missing.push(userCode);
      fallback = `你选的动效预设「${userCode}」已下架或禁用，已回退默认动效`;
    }
  }

  // 3) 内置默认（库里有 mp_default 就读它 —— 管理员改 seed 即全端生效；没有则用代码常量）
  if (!spec) {
    const component = usablePreset(items, DEFAULT_PRESET_CODE);
    const merged = sanitizeMotion(component?.style_config ?? DEFAULT_MOTION);
    spec = merged.spec;
    invalid.push(...merged.invalid);
    if (component) preset = { code: component.code, name: component.name };
    else if (!fallback) fallback = `字模库未登记 ${DEFAULT_PRESET_CODE}（动效迁移未应用），已按代码内置默认动效呈现`;
  }

  // 4) 用户差量：仅当生效预设来自用户偏好（板上挂载不得被观看者本地差量改掉）
  if (fromUser && userPref?.override) spec = applyOverride(spec, userPref.override, invalid);

  // 5) 无障碍压过一切：它不是「静默关掉」，必须把事实回传给配置入口显示
  if (reducedMotion) {
    spec = {
      enter: { ...spec.enter, effect: 'none' },
      chart: { ...spec.chart, appear: 'none' },
      transition: { ...spec.transition, effect: 'none' },
      reveal: { ...spec.reveal, effect: 'none' },
    };
    origin = 'reduced';
    fallback = '系统已开启「减少动态效果」（prefers-reduced-motion），动效按无障碍设置关闭';
  }

  return {
    motion: spec,
    preset,
    origin,
    missing,
    invalid,
    reason: fallback || undefined,
  };
}

// ── 渲染落地：CSS class / CSS 变量 / 错峰延迟 / G2 animate ──────────────────

/** 入场 class：`none` 返回空串（不加 class = 与今天零差别） */
export const enterClass = (enter: MotionEnter) =>
  enter.effect === 'none' ? '' : `motion-enter motion-enter-${enter.effect}`;
export const transitionClass = (transition: MotionTransition) =>
  transition.effect === 'none' ? '' : `motion-transition motion-transition-${transition.effect}`;
/**
 * 画布/播放面的入场开关（「默认零变化」的面级判定）。
 *
 * `mp_default.enter` 钉的是 **Chat 结果卡**今天的 fadeUp；看板卡片今天本就一次性落位，
 * 所以画布上的「逐块出现节奏」只在**有人显式选过**（板上挂字模 / 用户选了预设）时才开 ——
 * 未配置时不加 class，看板渲染与改造前逐字节相同（硬约束）。
 * `entrance` 为调用方强制口径（演示面开、编辑态关），给了就照它走。
 */
export function canvasEntrance(opts: { origin: MotionResolution['origin']; enter: MotionEnter; draft?: boolean; entrance?: boolean }): boolean {
  if (opts.entrance !== undefined) return opts.entrance;
  if (opts.draft) return false;   // 编辑器里拖一张图不该让整版重新闪一遍
  return (opts.origin === 'board' || opts.origin === 'user') && opts.enter.effect !== 'none';
}

/** 正文出现：fadeIn 与 sectionStagger 同一套 keyframes，差别在是否按分段错峰（见 sectionStaggerStyle） */
export const revealClass = (reveal: MotionReveal) =>
  reveal.effect === 'none' ? '' : 'motion-reveal motion-reveal-fadeIn';

/**
 * 动效变量挂在容器上（一处写入，子元素继承）：时长/缓动/位移全走 CSS 变量，
 * globals.css 的 keyframes 不写死数值 —— 否则改字模要改 CSS，就不是「库优先」了。
 */
export function motionCssVars(motion: MotionSpec): Record<string, string> {
  const { enter, transition } = motion;
  const offset = `${enter.offsetPx}px`;
  return {
    '--motion-enter-dur': `${enter.durationMs}ms`,
    '--motion-enter-ease': EASING_CSS[enter.easing],
    '--motion-enter-offset': offset,
    '--motion-transition-dur': `${transition.durationMs}ms`,
    '--motion-transition-ease': EASING_CSS[transition.easing],
    '--motion-transition-offset': transition.effect === 'pushLeft' ? '32px' : offset,
    '--motion-reveal-dur': `${enter.durationMs}ms`,
    '--motion-reveal-ease': EASING_CSS[enter.easing],
  };
}

/** 入场错峰：上限受 `maxStaggerMs` 约束（长看板尾部卡片不能等 5s 才出现） */
export function enterStaggerStyle(index: number, enter: MotionEnter): Record<string, string> | undefined {
  if (enter.effect === 'none' || index <= 0 || enter.staggerMs <= 0) return undefined;
  return { animationDelay: `${Math.min(index * enter.staggerMs, enter.maxStaggerMs)}ms` };
}

/** 正文分段错峰（`reveal.effect === 'sectionStagger'` 时才有延迟） */
export function sectionStaggerStyle(index: number, motion: MotionSpec): Record<string, string> | undefined {
  const { reveal, enter } = motion;
  if (reveal.effect === 'none' || index <= 0) return undefined;
  if (reveal.effect !== 'sectionStagger') return undefined;
  return { animationDelay: `${Math.min(index * reveal.staggerMs, enter.maxStaggerMs)}ms` };
}

/**
 * 图表自身入场 → G2 mark 的 `animate` 值。
 * - `default` → `undefined`：调用方**不得**写这个键，spec 与现状逐字节相同（默认零变化的门禁点）；
 * - `none` → `false`：G2 的 `maybeNonAnimate` 据此关掉 enter/update/exit；
 * - 其余 → `{ enter: { type, duration, easing } }`，type 用 G2 注册表名。
 */
export function g2Animate(chart: MotionChart): Record<string, any> | false | undefined {
  if (chart.appear === 'default') return undefined;
  if (chart.appear === 'none') return false;
  return { enter: { type: chart.appear, duration: chart.durationMs, easing: EASING_CSS[chart.easing] } };
}

/**
 * 四段生效值的一句话摘要（配置面共用一份文案口径）：
 * Profile 偏好卡与看板属性面板不得各写一套“生效值”说辞 —— 两处不一致就是“哪个是真的”这类提问的源头。
 * 文案同时是用例的断言面（默认档必须读得出“现状”），改动要连带动 motion / Profile 卡用例。
 */
export function motionSummary(motion: MotionSpec): { label: string; text: string }[] {
  return [
    { label: '卡片入场', text: motion.enter.effect === 'none' ? '不做入场动画'
      : `${motion.enter.effect} · ${motion.enter.durationMs}ms · 错峰 ${motion.enter.staggerMs}ms` },
    { label: '图表动画', text: motion.chart.appear === 'default' ? '跟随现状（不额外指定）'
      : motion.chart.appear === 'none' ? '关闭图表动画' : `${motion.chart.appear} · ${motion.chart.durationMs}ms` },
    { label: '看板切板', text: motion.transition.effect === 'none' ? '直接切换（现状）'
      : `${motion.transition.effect} · ${motion.transition.durationMs}ms` },
    { label: '内容正文', text: motion.reveal.effect === 'none' ? '一次性出现（现状）' : motion.reveal.effect },
  ];
}

/**
 * 把 animate 落到 spec 上：只写「看起来是 mark」的节点（有 encode），
 * 不碰 axis/legend/title 这类组件节点 —— G2 的 animate 是 mark 级属性，
 * 组件节点带上会被 props 校验当对象吃掉但无意义，view 包装层则不吃继承。
 */
const RADIAL_COORDINATES = new Set(['theta', 'polar', 'radial', 'helix']);
/**
 * 极坐标/theta 的 interval（饼/环/玫瑰）不吃笛卡尔 enter 动画（growInY/pathIn/waveIn/growInX…）：
 * 那些动画按直角坐标算“生长/描画”，套到 theta 上 G2 会把切片角度/半径算错→部分切片直接不画（环图丢内容，用户反馈）。
 * 这类 mark 只保留坐标无关的淡入（fadeIn）；`none`（false）原样保留。
 */
function enterForMark(node: any, animate: Record<string, any> | false): Record<string, any> | false {
  if (animate === false) return animate;
  const coord = node?.coordinate?.type;
  const enterType = animate.enter?.type as string | undefined;
  if (coord && RADIAL_COORDINATES.has(coord) && node?.type === 'interval' && enterType && enterType !== 'fadeIn') {
    return { ...animate, enter: { ...animate.enter, type: 'fadeIn' } };
  }
  return animate;
}
export function applyG2Animate<T extends Record<string, any>>(spec: T, animate: Record<string, any> | false | undefined): T {
  if (animate === undefined) return spec;
  const isMark = (n: any) => !!n && typeof n === 'object' && typeof n.type === 'string' && !!n.encode;
  const out: any = { ...spec, animate: enterForMark(spec, animate) };
  if (Array.isArray(out.children)) out.children = out.children.map((child: any) => (isMark(child) ? { ...child, animate: enterForMark(child, animate) } : child));
  return out;
}