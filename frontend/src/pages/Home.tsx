import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  MessageSquare, Database, Settings, LayoutDashboard, Sparkles,
  ArrowRight, BarChart3, Layers, ShieldCheck, Wand2,
  Palette, UserCircle, LogOut, Moon, Sun, Gem, Zap, TrendingUp,
  Grid3x3, GlassWater, Brain, Heart, type LucideIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useAuthStore } from '@/stores/authStore';
import { useThemeStore, type ThemeId } from '@/stores/themeStore';
import { useVisConfig } from '@/lib/visStyle';
import { useBrandStore } from '@/stores/brandStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { isMenuAllowed, isMenuPrefixAllowed } from '@/stores/permissionStore';
import { fetchVisibleGroups } from '@/pages/Analysis';
import client from '@/api/client';

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  analyst: '分析师',
  viewer: '查看者',
};

// 主题清单(与 WorkspaceLayout/SystemLayout 等保持同一套选项与文案)
const THEMES: { id: ThemeId; label: string; icon: LucideIcon; desc: string }[] = [
  { id: 'dark', label: '暗色', icon: Moon, desc: '深色背景，适合长时间使用' },
  { id: 'light', label: '亮色', icon: Sun, desc: '浅色背景，清晰明亮' },
  { id: 'datafoundry', label: 'DataFoundry', icon: Gem, desc: '克制优雅，近黑主色 + 宝石色调' },
  { id: 'tech', label: '科技风', icon: Zap, desc: '深蓝底色，霓虹高亮' },
  { id: 'finance', label: '金融风', icon: TrendingUp, desc: '深色海军蓝，金色主调' },
  { id: 'bento', label: 'Bento Grid', icon: Grid3x3, desc: '柔和圆角卡片，彩色区块布局' },
  { id: 'glass', label: '玻璃拟态', icon: GlassWater, desc: '深色半透明毛玻璃质感' },
  { id: 'ainative', label: 'AI-Native', icon: Brain, desc: '深空神经网络，动态光效边框' },
  { id: 'medical', label: '医疗平台', icon: Heart, desc: '清爽蓝绿，专业可信' },
];

function greetingOf(): string {
  const h = new Date().getHours();
  if (h < 5) return '夜深了';
  if (h < 12) return '早上好';
  if (h < 18) return '下午好';
  return '晚上好';
}

// ── 字模驱动的样式默认值(库中缺失该字模时回退,不阻断渲染) ──
// 背景(screen_background): 与 seed bg_home_ambient 同值
const DEFAULT_BG = {
  backgroundColor: 'hsl(var(--background))',
  backgroundImage: 'radial-gradient(hsl(var(--muted-foreground) / 0.10) 1px, transparent 1px)',
  backgroundSize: '22px 22px',
  overlay: 'radial-gradient(circle at 8% 12%, hsl(var(--primary) / 0.12), transparent 42%), radial-gradient(circle at 95% 35%, hsl(var(--accent) / 0.12), transparent 45%), radial-gradient(circle at 25% 105%, hsl(var(--primary) / 0.08), transparent 45%)',
};
// 装饰边框(decoration_frame): 与 seed frame_tech_corner 同构
const DEFAULT_FRAME = {
  cornerAccent: 'hsl(var(--primary))',
  showCornerBrackets: true,
  borderStyle: '1px solid hsl(var(--border))',
};
// 统计瓦片(kpi_card): 与 seed card_profile_stat 同构
const DEFAULT_KPI = {
  cardBg: 'hsl(var(--card))',
  cardBorder: '1px solid hsl(var(--border))',
  borderRadius: '10px',
  valueColor: 'hsl(var(--foreground))',
  labelColor: 'hsl(var(--muted-foreground))',
};

/** 数据概览小卡:图标 + 数值 + 标签(样式取字模库 kpi_card·card_profile_stat) */
function StatCard({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  const cfg = useVisConfig('card_profile_stat', DEFAULT_KPI);
  return (
    <div className="flex items-center gap-3 px-4 py-3"
      style={{ background: cfg.cardBg, border: cfg.cardBorder, borderRadius: cfg.borderRadius }}>
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0">
        <div className="truncate text-base font-semibold leading-tight" style={{ color: cfg.valueColor }}>{value}</div>
        <div className="truncate text-xs" style={{ color: cfg.labelColor }}>{label}</div>
      </div>
    </div>
  );
}

/**
 * 默认首页('/' 恒展示本页,模块切换选「首页」即进首页,不再因有看板而跳走):
 * - 登录落地由 Login 决定: 有已启用看板直达数据看板,否则落本首页;
 * - 内容: 问候 Hero + 数据概览 + 看板入口/未分配提示 + 权限过滤的快捷入口 + 推荐问题。
 * 全部使用主题变量配色,亮/暗主题下均随系统默认主题生效。
 */
export default function Home() {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();
  const { theme, setTheme } = useThemeStore();
  const { brand, fetchBrand } = useBrandStore();
  const workspaces = useWorkspaceStore(s => s.workspaces);
  const loadWorkspaces = useWorkspaceStore(s => s.loadWorkspaces);
  const wsLoaded = useWorkspaceStore(s => s.loaded);
  const wsLoading = useWorkspaceStore(s => s.loading);
  const getDefaultWorkspaceId = useWorkspaceStore(s => s.getDefaultWorkspaceId);
  const [checking, setChecking] = useState(true);
  const [dashCount, setDashCount] = useState(0);
  const [questions, setQuestions] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchVisibleGroups()
      .then(gs => {
        if (cancelled) return;
        setDashCount(gs.reduce((n, g) => n + g.dashboards.filter(d => d.status === 'enabled').length, 0));
      })
      .catch(() => { /* 看板清单取不到按 0 处理,未分配提示文案仍成立 */ })
      .finally(() => { if (!cancelled) setChecking(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { void fetchBrand(); }, [fetchBrand]);
  useEffect(() => {
    if (!wsLoaded && !wsLoading) void loadWorkspaces();
  }, [wsLoaded, wsLoading, loadWorkspaces]);

  // 推荐问题:点击带入问数输入框(Chat 挂载时消费)
  useEffect(() => {
    let cancelled = false;
    client.get('/admin/knowledge/random', {
      params: { knowledge_type: 'recommend_question', limit: 4, workspace_id: 0 },
    })
      .then(({ data }) => {
        if (cancelled) return;
        const qs = (data.items || []).map((it: any) => it.title).filter(Boolean);
        setQuestions(qs);
      })
      .catch(() => { /* 推荐问题为增强项,失败静默不阻断 */ });
    return () => { cancelled = true; };
  }, []);

  const askQuestion = (q: string) => {
    sessionStorage.setItem('home_question', q);
    goAsk();
  };

  const goProfile = () => {
    const wsId = getDefaultWorkspaceId();
    navigate(wsId ? `/ws/${wsId}/profile` : '/data/profile');
  };

  // 与模块切换的「工作空间」同一落点(默认工作空间聊天 /ws/{id}/chat),两处入口保持一致;
  // 无默认工作空间时回落 /workspace 由入口页解析,不走 /ask 独立精简布局
  const goAsk = () => {
    const wsId = getDefaultWorkspaceId();
    navigate(wsId ? `/ws/${wsId}/chat` : '/workspace');
  };

  const isAdmin = user?.role === 'admin';
  const appName = brand.app_name || 'AI-DataHub';
  const roleLabel = ROLE_LABELS[user?.role || ''] || user?.role || '未知';
  const dateText = new Date().toLocaleDateString('zh-CN', {
    year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
  });

  // 全幅背景点缀:字模库 screen_background(bg_home_ambient)驱动,管理员换字模即换首页氛围
  const bgCfg = useVisConfig('bg_home_ambient', DEFAULT_BG);
  const frameCfg = useVisConfig('frame_tech_corner', DEFAULT_FRAME);
  const kpiCfg = useVisConfig('card_profile_stat', DEFAULT_KPI);
  const decor = (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 z-0 overflow-hidden"
      style={{ backgroundColor: bgCfg.backgroundColor }}
    >
      {bgCfg.backgroundImage ? (
        <div className="absolute inset-0"
          style={{ backgroundImage: bgCfg.backgroundImage, backgroundSize: bgCfg.backgroundSize || undefined }} />
      ) : null}
      {bgCfg.overlay ? (
        <div className="absolute inset-0"
          style={{ backgroundImage: bgCfg.overlay, backgroundRepeat: 'no-repeat' }} />
      ) : null}
    </div>
  );

  // 超宽屏两侧留白的悬浮点缀标签(纯装饰,窄屏不显示,避免挤占内容列)
  const gutterChips = (
    <>
      <div aria-hidden className="pointer-events-none absolute inset-y-8 -left-[140px] hidden w-[124px] flex-col items-end gap-3 pt-28 min-[1480px]:flex">
        {['自然语言问数', '本体语义建模', 'SQL 治理审计'].map(t => (
          <span key={t} className="rounded-full border border-dashed bg-card/60 px-3 py-1.5 text-xs text-muted-foreground/70">{t}</span>
        ))}
      </div>
      <div aria-hidden className="pointer-events-none absolute inset-y-8 -right-[140px] hidden w-[124px] flex-col items-start gap-3 pt-52 min-[1480px]:flex">
        {['行级权限管控', '智能图表推荐', '报告一键交付'].map(t => (
          <span key={t} className="rounded-full border border-dashed bg-card/60 px-3 py-1.5 text-xs text-muted-foreground/70">{t}</span>
        ))}
      </div>
    </>
  );

  // 顶栏:品牌 + 切换主题 + 个人中心/退出登录(首页独立于各布局,需自带账号入口)
  const header = (
    <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1200px] items-center justify-between px-6 py-3">
        <div className="flex items-center gap-2.5">
          {brand.show_icon && (brand.logo_url ? (
            <img src={brand.logo_url} alt="Logo" className="h-7 w-7 rounded object-contain" />
          ) : (
            <span className="flex h-7 w-7 items-center justify-center rounded bg-primary text-xs font-bold text-primary-foreground">AD</span>
          ))}
          {brand.show_text && <span className="text-lg font-semibold tracking-tight">{appName}</span>}
        </div>
        <div className="flex items-center gap-1.5">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="h-9 w-9 p-0" aria-label="切换主题" title="切换主题">
                <Palette className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              {THEMES.map(t => {
                const Icon = t.icon;
                return (
                  <DropdownMenuItem key={t.id} onClick={() => setTheme(t.id)} className={`flex items-center gap-3 ${theme === t.id ? 'bg-accent' : ''}`}>
                    <Icon className="h-4 w-4" />
                    <div className="min-w-0 flex-1">
                      <div className={`text-sm ${theme === t.id ? 'font-medium' : ''}`}>{t.label}</div>
                      <div className="truncate text-xs text-muted-foreground">{t.desc}</div>
                    </div>
                    {theme === t.id && <span className="text-xs text-primary">✓</span>}
                  </DropdownMenuItem>
                );
              })}
            </DropdownMenuContent>
          </DropdownMenu>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button aria-label="用户菜单" className="flex min-h-[36px] items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-muted">
                <Avatar className="h-6 w-6">
                  <AvatarFallback className="text-xs">{user?.username?.charAt(0)?.toUpperCase() || 'U'}</AvatarFallback>
                </Avatar>
                <span className="hidden text-sm font-medium sm:inline">{user?.username}</span>
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={goProfile}>
                <UserCircle className="mr-2 h-4 w-4" />
                个人设置
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => { logout(); navigate('/login'); }}>
                <LogOut className="mr-2 h-4 w-4" />
                退出登录
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  );

  if (checking) {
    return (
      <div className="relative h-full overflow-auto">
        {decor}
        {header}
        <div className="relative z-10 mx-auto max-w-[1200px] px-6 py-10">
          <Skeleton className="h-52 w-full rounded-2xl" />
          <div className="mt-6 grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Skeleton className="h-[68px] rounded-xl" />
            <Skeleton className="h-[68px] rounded-xl" />
            <Skeleton className="h-[68px] rounded-xl" />
          </div>
          <div className="mt-10 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            <Skeleton className="h-40 rounded-2xl" />
            <Skeleton className="h-40 rounded-2xl" />
            <Skeleton className="h-40 rounded-2xl" />
          </div>
        </div>
      </div>
    );
  }

  // 快速开始按权限码裁决显隐(与 SectionSwitcher/各布局菜单同口径):无对应功能的入口不展示,
  // 避免点进去只剩空布局。工作空间 → workspace:chat 菜单; 数据中台 → data:* 任一可见菜单;
  // 系统配置 → 仅 admin(SystemLayout 对非 admin 硬跳转,故不单列权限判断)。
  const showAsk = isMenuAllowed('workspace:chat');
  const showData = isMenuPrefixAllowed('data:');

  const entries: Array<{ key: string; icon: LucideIcon; title: string; desc: string; go: () => void; tag?: string }> = [
    ...(showAsk ? [{
      key: 'ask', icon: MessageSquare, title: '工作空间',
      desc: '用自然语言探索数据，自动生成图表、分析结论与报告产出', go: goAsk, tag: '推荐',
    }] : []),
    ...(showData ? [{
      key: 'data', icon: Database, title: '数据中台',
      desc: '数据源、元数据、本体建模与数据安全策略的统一配置入口', go: () => navigate('/data'),
    }] : []),
    ...(isAdmin ? [{
      key: 'system', icon: Settings, title: '系统配置',
      desc: '模型、AS-BOT、看板组合、执行层与用户工作空间统管', go: () => navigate('/system'),
    }] : []),
  ];

  return (
    <div className="relative h-full overflow-auto">
      {decor}
      {header}
      <div className="relative z-10 mx-auto max-w-[1200px] px-6 py-10">
        {gutterChips}
        {/* Hero:问候 + 品牌定位 + 主行动 */}
        <section className="relative overflow-hidden rounded-2xl border bg-card">
          <div aria-hidden className="pointer-events-none absolute -right-20 -top-24 h-72 w-72 rounded-full bg-primary/15 blur-3xl" />
          <div aria-hidden className="pointer-events-none absolute -left-16 bottom-[-7rem] h-64 w-64 rounded-full bg-accent/15 blur-3xl" />
          <div aria-hidden className="pointer-events-none absolute right-[22%] bottom-[-9rem] h-52 w-52 rounded-full bg-primary/10 blur-3xl" />
          {/* 四角装饰(字模 decoration_frame·frame_tech_corner 驱动,showCornerBrackets=false 可在字模库关闭) */}
          {frameCfg.showCornerBrackets && [
            'left-2 top-2 border-l-2 border-t-2',
            'right-2 top-2 border-r-2 border-t-2',
            'left-2 bottom-2 border-l-2 border-b-2',
            'right-2 bottom-2 border-r-2 border-b-2',
          ].map(pos => (
            <span key={pos} aria-hidden
              className={`pointer-events-none absolute h-4 w-4 ${pos}`}
              style={{ borderColor: frameCfg.cornerAccent }} />
          ))}
          <div className="relative px-8 py-9 sm:px-10 sm:py-10">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <LayoutDashboard className="h-4 w-4 text-primary" />
              <span>{appName}</span>
              <span className="text-border">·</span>
              <span>{dateText}</span>
            </div>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight">
              {greetingOf()}，{user?.username || '用户'}
              <Badge variant="secondary" className="ml-3 align-middle text-xs font-normal">{roleLabel}</Badge>
            </h1>
            <p className="mt-2.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
              欢迎来到你的数据工作台 —— 从一句提问开始，直达取数、图表、分析结论与报告交付；
              也可以从下方快捷入口进入数据建模与系统管理。
            </p>
            <div className="mt-6 flex flex-wrap items-center gap-3">
              <Button size="lg" onClick={goAsk}>
                <MessageSquare className="mr-1.5 h-4 w-4" />
                进入工作空间
                <ArrowRight className="ml-1.5 h-4 w-4" />
              </Button>
              <Button size="lg" variant="outline" onClick={() => navigate('/dashboards')}>
                <BarChart3 className="mr-1.5 h-4 w-4" />
                浏览数据看板
              </Button>
            </div>
          </div>
        </section>

        {/* 数据概览 */}
        <section className="mt-5 grid grid-cols-1 sm:grid-cols-3 gap-3">
          <StatCard icon={Layers} label="我的工作空间" value={`${workspaces.length} 个`} />
          <StatCard icon={BarChart3} label="可见数据看板" value={`${dashCount} 个`} />
          <StatCard icon={ShieldCheck} label="当前角色" value={roleLabel} />
        </section>

        {/* 看板入口 / 未分配提示(登录直达看板的判定在 Login,首页恒可访问) */}
        <section className="mt-5 rounded-xl border bg-card p-5">
          {dashCount > 0 ? (
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <BarChart3 className="h-4 w-4" />
              </div>
              <div>
                <h2 className="font-medium">已为你分配 {dashCount} 个数据看板</h2>
                <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                  登录时会直达数据看板；也可以随时从模块切换回到首页，或从这里再次进入。
                </p>
                <Button size="sm" className="mt-3" onClick={() => navigate('/dashboards')}>
                  <BarChart3 className="mr-1 h-4 w-4" />
                  进入数据看板
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <Sparkles className="h-4 w-4" />
              </div>
              <div>
                <h2 className="font-medium">还没有分配仪表盘</h2>
                <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                  当你的角色被分配仪表盘（看板组合）后，登录将直接进入数据看板。
                  {isAdmin ? '可在「系统配置 → 仪表盘」中创建看板并编排组合、分配角色。' : '可联系管理员为你的角色分配看板组合。'}
                </p>
                {isAdmin && (
                  <Button size="sm" className="mt-3" onClick={() => navigate('/system/dashboards')}>
                    <LayoutDashboard className="mr-1 h-4 w-4" />
                    去配置仪表盘
                  </Button>
                )}
              </div>
            </div>
          )}
        </section>

        {/* 快速开始(全部入口被权限裁掉时不渲染整段,避免空标题空栅格) */}
        {entries.length > 0 && (
          <section className="mt-10">
            <h2 className="mb-4 text-sm font-medium text-muted-foreground">快速开始</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {entries.map(e => {
                const Icon = e.icon;
                return (
                  <button
                    key={e.key}
                    onClick={e.go}
                    className="group relative overflow-hidden p-6 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/50 hover:shadow-lg"
                    style={{ background: kpiCfg.cardBg, border: kpiCfg.cardBorder, borderRadius: kpiCfg.borderRadius }}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10 text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                        <Icon className="h-5 w-5" />
                      </div>
                      <div className="flex items-center gap-2">
                        {e.tag && <Badge variant="secondary" className="text-[10px] font-normal">{e.tag}</Badge>}
                        <ArrowRight className="h-4 w-4 text-muted-foreground opacity-0 transition-all group-hover:translate-x-0.5 group-hover:opacity-100" />
                      </div>
                    </div>
                    <div className="mt-4 font-medium">{e.title}</div>
                    <div className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{e.desc}</div>
                  </button>
                );
              })}
            </div>
          </section>
        )}

        {/* 推荐问题:点击带入问数输入框 */}
        {questions.length > 0 && (
          <section className="mt-10">
            <div className="flex items-center gap-2">
              <Wand2 className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-medium">试试这样问</h2>
              <span className="text-xs text-muted-foreground">点击带入问数输入框</span>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {questions.map(q => (
                <button
                  key={q}
                  onClick={() => askQuestion(q)}
                  className="rounded-full border bg-card px-4 py-2 text-left text-sm text-muted-foreground transition-colors hover:border-primary/60 hover:bg-primary/5 hover:text-foreground"
                >
                  {q}
                </button>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
