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
      { target: TOUR.homeOverview, title: '数据概览', body: '这里是你登录后的总览：可见看板数量与工作空间状态，先确认自己找的数据范围对不对。' },
      { target: TOUR.homeQuick, title: '快捷入口', body: '按你的权限展示常用入口，点卡片直达；看不到的条目代表当前角色无权限。' },
      { target: TOUR.headerReleaseNotes, title: '版本更新公告', body: '右上角这里看每次版本的新增与优化，红点代表还有没读过的公告。' },
      { target: TOUR.headerModuleSwitcher, title: '模块切换', body: '首页 / 工作空间 / 数据中台 / 系统配置在这里切换，不必回退页面找入口。' },
      { target: TOUR.headerTheme, title: '主题风格', body: '配色和图表字模跟随主题，切换后全平台（问数、看板、大屏）一起变。' },
    ],
  },
  workspace: {
    label: '工作空间（日常用数）',
    module: 'workspace',
    steps: [
      { target: TOUR.wsSelector, title: '工作空间', body: '工作空间是你的个人工作站，会话与产物都归在这里，可以新建或按项目切换。' },
      { target: navTour('ws', 'dashboards'), title: '数据看板', body: '日常消费入口：这里列出你有权查看的看板组合，点卡片下钻具体仪表盘。' },
      { target: navTour('ws', 'chat'), title: '智能问数', body: '用业务语言直接提问取数，不用写 SQL；口径以平台已建模的指标为准。' },
      { target: TOUR.chatInput, route: '/ws/:wsId/chat', title: '提问框', body: '在这里输入数据问题，可附文件；上方选择模型、执行层与站点后再发送。' },
      { target: navTour('ws', 'reports'), title: '报表中心', body: '会话与定时任务产出的报表按版本归集在这里，可继续编辑或分享。' },
    ],
  },
  data: {
    label: '数据中台（建模与治理）',
    module: 'data',
    steps: [
      { target: navTour('dp', 'datasources'), title: '数据源', body: '接入的数据源与物理元数据在这里维护，是建模的前置条件。' },
      { target: navTour('dp', 'ontology'), title: '本体建模', body: '业务对象、属性与关系在这里建模，是 ChatBI 的唯一权威语义层。' },
      { target: navTour('dp', 'metrics'), title: '指标与维度', body: '指标口径与维度字典的权威清单，别名与枚举在这里统一维护。' },
      { target: navTour('dp', 'datasets'), title: '数据集', body: '把已建模的对象整理成可复用的数据产品，供看板与报表引用。' },
      { target: navTour('dp', 'sensitive'), title: '数据安全配置', body: '敏感字段基线与行级安全策略在这里配置，对所有人生效。' },
    ],
  },
  system: {
    label: '系统配置（管理员）',
    module: 'system',
    steps: [
      { target: navTour('sys', 'models'), title: '模型中心', body: '可用模型与执行层配置在这里，决定问数和 AS-BOT 用哪个模型跑。' },
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
 * 路由 → 所属模块引导 id(欢迎总览据此决定「开始引导」的落点)。
 * 前缀口径与 App.tsx 的顶级 Route 保持一致: /ws、/ask 归工作空间。
 */
export const guideIdForPath = (pathname: string): string => {
  if (pathname.startsWith('/system')) return 'system';
  if (pathname.startsWith('/data')) return 'data';
  if (pathname.startsWith('/ws') || pathname.startsWith('/ask')) return 'workspace';
  return 'home';
};
