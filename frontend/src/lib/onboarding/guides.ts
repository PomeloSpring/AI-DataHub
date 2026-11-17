/**
 * 新手引导事实源：锚点常量 + 分模块引导步骤定义。
 *
 * 为什么在代码里而不在库里：步骤靠 `data-tour` 锚点定位 DOM，锚点和页面组件是
 * 同一批代码演进的东西；落库会让"库里的 selector"和"前端代码版本"错配，升级后
 * 引导点到空气。版本**公告文案**才落库（adh_release_notes，管理员可编辑）。
 *
 * 新增/改名页面入口时，同批在这里补锚点与步骤（改一侧漏另一侧 = 引导点不到元素）。
 */

// ── 锚点常量(页面组件引用同一份，防字符串漂移) ──────────────────────────────

export const TOUR = {
  headerModuleSwitcher: 'header-module-switcher',
  headerReleaseNotes: 'header-release-notes',
  headerTheme: 'header-theme',
  headerUser: 'header-user',
  wsSelector: 'ws-selector',
  chatInput: 'chat-input',
  chatModel: 'chat-model',
  chatSessions: 'chat-sessions',
  homeOverview: 'home-overview',
  homeFeed: 'home-feed',
  homeQuick: 'home-quick',
} as const;

/** 侧栏菜单项锚点: navTour('ws', 'dashboards') → 'ws-nav-dashboards' */
export const navTour = (scope: 'ws' | 'dp' | 'sys', routeKey: string) =>
  `${scope}-nav-${routeKey.split('/').pop()}`;

/** 取路由末段(与 navTour 同口径, 供布局直接生成锚点) */
export const routeSeg = (routeKey: string) => routeKey.split('/').pop() || routeKey;

// ── 引导定义 ────────────────────────────────────────────────────────────────

export interface TourStep {
  /** 高亮目标锚点; 省略 = 居中说明卡(用于纯讲解步骤) */
  target?: string;
  /**
   * 标题/正文的语言包 key（onboarding 命名空间 `guide.<guide>.steps.<step>.{title|body}`）。
   * 不在代码里存中文/英文字面量：切换语言要即时重渲染，字面量会停在建包时的语言。
   */
  titleKey: string;
  bodyKey: string;
  /** 进入该步前先跳转的路由; ':wsId' 由引擎替换为当前工作空间 id */
  route?: string;
  /** 锚点是按条件渲染的（如单工作空间时收起的切换下拉）：找不到就静默跳过，不报“未找到入口” */
  optional?: boolean;
  placement?: 'auto' | 'bottom' | 'top' | 'right';
}

export interface GuideDef {
  /** 业务名的语言包 key（管理页下拉与弹窗标题同源，渲染期解析） */
  labelKey: string;
  /** 该引导所属模块(用于按菜单权限决定是否提供入口) */
  module?: 'home' | 'workspace' | 'data' | 'system';
  steps: TourStep[];
}

/** 步骤 key 构造器：title/body 成对落在 onboarding 命名空间 */
const step = (
  guideId: string,
  stepKey: string,
  rest: Omit<TourStep, 'titleKey' | 'bodyKey'> = {},
): TourStep => ({
  titleKey: `guide.${guideId}.steps.${stepKey}.title`,
  bodyKey: `guide.${guideId}.steps.${stepKey}.body`,
  ...rest,
});

export const GUIDES: Record<string, GuideDef> = {
  home: {
    labelKey: 'guide.home.label',
    module: 'home',
    steps: [
      step('home', 'ask', { target: TOUR.homeOverview }),
      step('home', 'feed', { target: TOUR.homeFeed }),
      step('home', 'quick', { target: TOUR.homeQuick }),
      step('home', 'releaseNotes', { target: TOUR.headerReleaseNotes }),
      step('home', 'moduleSwitcher', { target: TOUR.headerModuleSwitcher }),
      step('home', 'theme', { target: TOUR.headerTheme }),
    ],
  },
  workspace: {
    labelKey: 'guide.workspace.label',
    module: 'workspace',
    steps: [
      // optional：只有一个工作空间时侧栏不挂切换下拉（新建入口未开放，无项可切），
      //   这一步就静默跳过；有多空间时照旧高亮。
      step('workspace', 'selector', { target: TOUR.wsSelector, optional: true }),
      step('workspace', 'chat', { target: navTour('ws', 'chat') }),
      step('workspace', 'dashboards', { target: navTour('ws', 'dashboards') }),
      step('workspace', 'input', { target: TOUR.chatInput, route: '/ws/:wsId/chat' }),
      step('workspace', 'reports', { target: navTour('ws', 'reports') }),
    ],
  },
  data: {
    labelKey: 'guide.data.label',
    module: 'data',
    steps: [
      step('data', 'datasources', { target: navTour('dp', 'datasources') }),
      step('data', 'ontology', { target: navTour('dp', 'ontology') }),
      step('data', 'metrics', { target: navTour('dp', 'metrics') }),
      step('data', 'datasets', { target: navTour('dp', 'datasets') }),
      step('data', 'sensitive', { target: navTour('dp', 'sensitive') }),
    ],
  },
  system: {
    labelKey: 'guide.system.label',
    module: 'system',
    steps: [
      step('system', 'models', { target: navTour('sys', 'models') }),
      step('system', 'roles', { target: navTour('sys', 'roles') }),
      step('system', 'releaseNotes', { target: navTour('sys', 'release-notes') }),
      step('system', 'settings', { target: navTour('sys', 'settings') }),
    ],
  },
};

/**
 * 语言解析函数（`useTranslation('onboarding').t` / 模块级 runtime t 均可传入）。
 *
 * 签名放宽为“任意字符串 key + 可选插值参数”：引导步骤 key 由上面的 `step()` 数据驱动，
 * 静态字面量类型只会逼调用方到处写断言（历史缺陷：改用 t(titleKey) 后 tsc 报 TFunction 不兼容）。
 */
export type Translate = (key: string, options?: Record<string, unknown>) => string;

/** 把 react-i18next 的严格 TFunction 适配为 `Translate`（动态 key 场景统一走这里） */
export const asTranslate = (t: unknown): Translate => t as Translate;

/** 引导 id → 业务名 key（welcome 是弹窗自身，不是分步引导） */
export const guideLabelKey = (id: string): string =>
  id === 'welcome' ? 'welcome.guideOption' : (GUIDES[id]?.labelKey || 'tour.defaultLabel');

/** 管理页「关联引导」选择框选项（业务名，渲染期解析；不手输 key） */
export const guideOptions = (t: Translate): { id: string; label: string }[] => [
  { id: 'welcome', label: t(guideLabelKey('welcome')) },
  ...Object.keys(GUIDES).map(id => ({ id, label: t(guideLabelKey(id)) })),
];

/** 引导 id → 业务名（公告面板/占位显示用） */
export const guideLabel = (id: string, t: Translate): string => t(guideLabelKey(id));

/**
 * 路由 → 当前页面上**真实存在引导内容**的模块引导 id（'' = 本页没有引导）。
 * 前缀口径与 App.tsx 的顶级 Route 保持一致: /ws、/ask 归工作空间；首页引导的锚点
 * 只挂在 Home 页，因此仅 '/' 命中 home。
 *
 * 命不中就返回 ''，**不得**回退成 home —— 回退会让弹窗去引导本页根本不存在的入口
 * （锚点不在 DOM 里 = 点到空气），这正是「不在当前页面的内容不要弹窗」要收敛的口径。
 */
export const guideIdForPath = (pathname: string): string => {
  const on = (prefix: string) => pathname === prefix || pathname.startsWith(`${prefix}/`);
  if (pathname === '/') return 'home';
  if (on('/system')) return 'system';
  if (on('/data')) return 'data';
  if (on('/ws') || on('/ask') || on('/workspace')) return 'workspace';
  return '';
};

/** 引导可见性判定所需的权限快照(响应式调用方用 useMenuAccess, 非响应式用 store 快照) */
export interface GuideAccessCtx {
  /** 权限清单是否已就绪(未就绪一律视为不可见, 防无权限入口闪现) */
  ready: boolean;
  /** 模块级一级入口: 该模块下有任一可见菜单才算有入口 */
  prefixAllowed: (prefix: string) => boolean;
  isAdmin: boolean;
}

/** 引导所属模块对当前用户是否可见(与侧栏一级入口同口径) */
export const guideModuleAllowed = (id: string, ctx: GuideAccessCtx): boolean => {
  if (!ctx.ready) return false;
  if (id === 'home') return true;
  if (id === 'workspace') return ctx.prefixAllowed('workspace:');
  if (id === 'data') return ctx.prefixAllowed('data:');
  if (id === 'system') return ctx.isAdmin;
  return false;
};

/** 由 useMenuAccess 的返回值 + 当前用户角色构造可见性上下文 */
export const guideAccessOf = (
  menuAccess: { ready: boolean; prefixAllowed: (prefix: string) => boolean },
  user: { role?: string } | null,
): GuideAccessCtx => ({
  ready: menuAccess.ready,
  prefixAllowed: menuAccess.prefixAllowed,
  isAdmin: user?.role === 'admin',
});

/** 当前页面**且当前用户**可播的引导 id('' = 没有可引导内容, 调用方据此不弹窗) */
export const pageGuideIdForPath = (pathname: string, ctx: GuideAccessCtx): string => {
  const id = guideIdForPath(pathname);
  return id && guideModuleAllowed(id, ctx) ? id : '';
};
