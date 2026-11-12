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
  title: string;
  body: string;
  /** 进入该步前先跳转的路由; ':wsId' 由引擎替换为当前工作空间 id */
  route?: string;
  /** 锚点是按条件渲染的（如单工作空间时收起的切换下拉）：找不到就静默跳过，不报“未找到入口” */
  optional?: boolean;
  placement?: 'auto' | 'bottom' | 'top' | 'right';
}

export interface GuideDef {
  /** 管理页下拉与弹窗标题用的业务名 */
  label: string;
  /** 该引导所属模块(用于按菜单权限决定是否提供入口) */
  module?: 'home' | 'workspace' | 'data' | 'system';
  steps: TourStep[];
}

export const GUIDES: Record<string, GuideDef> = {
  home: {
    label: '首页总览',
    module: 'home',
    steps: [
      { target: TOUR.homeOverview, title: '直接提问', body: '想查什么在这里打一句，回车就带着问题进智能问数；右边是企业简介，点开能看到我们的业务方向与平台已接入的站点、数据源范围。' },
      { target: TOUR.homeFeed, title: '最新资讯', body: '已发布的内容都在这里：按栏目或标签筛、搜索标题与摘要，点卡片进阅读页。这里只出现经过审核发布的内容，草稿不会露出来。' },
      { target: TOUR.homeQuick, title: '模块入口', body: '按你的权限展示常用入口，点这里直达；看不到的条目代表当前角色无权限（顶栏「模块切换」是同一份口径）。' },
      { target: TOUR.headerReleaseNotes, title: '版本更新公告', body: '右上角这里看每次版本的新增与优化，红点代表还有没读过的公告。' },
      { target: TOUR.headerModuleSwitcher, title: '模块切换', body: '首页 / 工作空间 / 数据中台 / 系统配置在这里切换，不必回退页面找入口。' },
      { target: TOUR.headerTheme, title: '主题风格', body: '配色和图表字模跟随主题，切换后全平台（问数、看板、大屏）一起变。' },
    ],
  },
  workspace: {
    label: '工作空间（日常用数）',
    module: 'workspace',
    steps: [
      // optional：只有一个工作空间时侧栏不挂切换下拉（新建入口未开放，无项可切），
      //   这一步就静默跳过；有多空间时照旧高亮。
      { target: TOUR.wsSelector, optional: true, title: '工作空间切换', body: '有多个工作空间时在这里切换（看板与会话按空间归集）；只有一个空间时这里不展示。对话产生的文件会随账号上云备份，不会因换节点而丢。' },
      { target: navTour('ws', 'dashboards'), title: '数据看板', body: '日常消费入口：这里列出你有权查看的看板组合，点卡片下钻具体仪表盘。' },
      { target: navTour('ws', 'chat'), title: '智能问数', body: '用业务语言直接提问取数，不用写 SQL；口径以平台已建模的指标为准。' },
      { target: TOUR.chatInput, route: '/ws/:wsId/chat', title: '提问框', body: '在这里输入数据问题，可附文件；上方选择模型、Harness 与站点后再发送。' },
      { target: navTour('ws', 'reports'), title: '报告中心', body: '会话与定时任务产出的报告按版本归集在这里，可继续编辑或分享。' },
    ],
  },
  data: {
    label: '数据中台（建模与治理）',
    module: 'data',
    steps: [
      { target: navTour('dp', 'datasources'), title: '数据源', body: '接入的数据源与物理元数据在这里维护，是建模的前置条件。' },
      { target: navTour('dp', 'ontology'), title: '本体建模', body: '业务对象、属性与关系在这里建模，是 ChatBI 的唯一权威语义层。' },
      { target: navTour('dp', 'metrics'), title: '指标与维度', body: '指标口径与维度字典的权威清单，别名与枚举在这里统一维护。' },
      { target: navTour('dp', 'datasets'), title: '数据集', body: '把已建模的对象整理成可复用的数据产品，供看板与报告引用。' },
      { target: navTour('dp', 'sensitive'), title: '数据安全配置', body: '敏感字段基线与行级安全策略在这里配置，对所有人生效。' },
    ],
  },
  system: {
    label: '系统配置（管理员）',
    module: 'system',
    steps: [
      { target: navTour('sys', 'models'), title: '模型中心', body: '可用模型与 Harness 配置在这里，决定问数和 AS-BOT 用哪个模型跑。' },
      { target: navTour('sys', 'roles'), title: '角色权限', body: '菜单与功能权限码、数据源授权都在这里配，页面入口随权限显隐。' },
      { target: navTour('sys', 'release-notes'), title: '版本公告', body: '发新版本时在这里登记功能与更新项，勾“重大版本”会给全体用户重触发引导。' },
      { target: navTour('sys', 'settings'), title: '系统设置', body: '应用名称与 Logo 等品牌信息在这里改，顶栏与登录页同步生效。' },
    ],
  },
};

/** 管理页「关联引导」选择框选项(业务名, 不手输 key) */
export const GUIDE_OPTIONS: { id: string; label: string }[] = [
  { id: 'welcome', label: '欢迎总览（首次登录/升级提示）' },
  ...Object.entries(GUIDES).map(([id, g]) => ({ id, label: g.label })),
];

/** 引导 id → 业务名(公告面板显示用) */
export const guideLabel = (id: string) =>
  GUIDE_OPTIONS.find(o => o.id === id)?.label || '新手引导';

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
