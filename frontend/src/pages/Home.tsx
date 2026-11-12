import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  MessageSquare, Database, Settings, Sparkles, ArrowRight, Palette, UserCircle, LogOut, Brain,
  Newspaper, Folder, LayoutDashboard, type LucideIcon, Compass
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger
} from '@/components/ui/dropdown-menu';
import { useAuthStore } from '@/stores/authStore';
import { useVisConfig } from '@/lib/visStyle';
import { useBrandStore } from '@/stores/brandStore';
import { DEFAULT_APP_NAME } from '@/lib/brand';
import BrandLogo from '@/components/BrandLogo';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useMenuAccess } from '@/hooks/useMenuAccess';
import SectionSwitcher from '@/components/SectionSwitcher';
import ReleaseNotesButton from '@/components/ReleaseNotesButton';
import { TOUR } from '@/lib/onboarding/guides';
import { useGuideStore } from '@/stores/guideStore';
import { fetchVisibleGroups, flattenGroupBoards } from '@/lib/dashboardCatalog';
import { contentApi } from '@/api/content';
import { useContentPerms } from '@/pages/content/shared';
import client from '@/api/client';
import WorkbenchPane from './home/WorkbenchPane';
import CompanyProfile from './home/CompanyProfile';
import type { RailStats } from './home/ActionRail';
import { stashQuestion } from './home/shared';
import ThemeMenuItems from '@/components/ThemeMenuItems';

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  analyst: '分析师',
  viewer: '查看者'};

// 主题清单(与 WorkspaceLayout/SystemLayout 等保持同一套选项与文案)

function greetingOf(): string {
  const h = new Date().getHours();
  if (h < 5) return '夜深了';
  if (h < 12) return '早上好';
  if (h < 18) return '下午好';
  return '晚上好';
}

// 全幅背景点缀(字模 screen_background·bg_home_ambient)：唯一保留的氛围件。
// 首页曾经有两侧悬浮能力标签、三个光斑与四角装饰边 —— 它们不回答任何用户问题，
// 本轮按"首屏每个区块都要能点"的原则移除；氛围由字模驱动，改字模即换观感。
const DEFAULT_BG = {
  backgroundColor: 'hsl(var(--background))',
  backgroundImage: 'radial-gradient(hsl(var(--muted-foreground) / 0.10) 1px, transparent 1px)',
  backgroundSize: '22px 22px',
  overlay: 'radial-gradient(circle at 8% 12%, hsl(var(--primary) / 0.12), transparent 42%), radial-gradient(circle at 95% 35%, hsl(var(--accent) / 0.12), transparent 45%)'};

/**
 * 首页 = 工作台 + 资讯流（2026-11-06 用户拍板：不单独建资讯模块，把首页用起来）。
 *
 * 四块，每块都必须是"点了会去干活"的东西：
 * 1. Hero：问候 + **可用提问框**（回车/「问数」带问题进智能问数并直接执行，
 *    与 Chat 的 home_question 契约一致）
 *    + 企业简介卡（事实源 = 内容中心 `about` 栏目的在线内容）；
 *    原先这里的"可见看板 / 未读资讯 / 待办"三个指标已撤：同一屏下方右栏各有一份，
 *    两处数字一起刷会不同步，用户只会当成数据不一致（2026-11-06 用户拍板换内容）；
 * 2. 左栏资讯流：只消费已发布内容，栏目/标签来自接口，不硬编码；
 * 3. 右栏行动栏：看板、待我处理、热门标签（按权限码出现，无权限不发请求）；
 * 4. 底部一行模块入口：取代原 4 张大卡（与顶栏「模块切换」重复的信息被压缩）。
 *
 * 资讯区的显隐真值源是菜单码 `home:feed`（⋈ `article:read`），与角色配置同一批对齐；
 * 不在这里硬编码"恒可见"，也不拿前端权限当裁决（服务端仍是唯一门）。
 */
export default function Home() {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();
  const { brand, fetchBrand } = useBrandStore();
  const loadWorkspaces = useWorkspaceStore(s => s.loadWorkspaces);
  const wsLoaded = useWorkspaceStore(s => s.loaded);
  const wsLoading = useWorkspaceStore(s => s.loading);
  const getDefaultWorkspaceId = useWorkspaceStore(s => s.getDefaultWorkspaceId);
  const menuAccess = useMenuAccess();
  const { has, loaded: permsLoaded } = useContentPerms();
  const startGuide = useGuideStore(s => s.startGuide);

  const [checking, setChecking] = useState(true);
  const [boards, setBoards] = useState<Array<ReturnType<typeof flattenGroupBoards>[number]>>([]);
  const [questions, setQuestions] = useState<string[]>([]);
  const [draft, setDraft] = useState('');
  const [tag, setTag] = useState('');
  const [stats, setStats] = useState<RailStats>({ drafts: null, pending: null, expired: null });
  const askRef = useRef<HTMLInputElement>(null);

  // 资讯区可见性：菜单码反查（与三套 Layout 侧栏同口径），未就绪时不给区块
  const showFeed = menuAccess.ready && menuAccess.allowed('home:feed');
  const canManage = has('article:manage');
  const canReview = has('article:review');
  const isAdmin = user?.role === 'admin';

  useEffect(() => {
    let cancelled = false;
    fetchVisibleGroups()
      .then(gs => { if (!cancelled) setBoards(flattenGroupBoards(gs).filter(b => b.status === 'enabled')); })
      .catch(() => { /* 看板清单取不到按空处理：右栏整块不渲染，不画"0 个看板"的假结论 */ })
      .finally(() => { if (!cancelled) setChecking(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => { void fetchBrand(); }, [fetchBrand]);
  useEffect(() => {
    if (!wsLoaded && !wsLoading) void loadWorkspaces();
  }, [wsLoaded, wsLoading, loadWorkspaces]);

  // 分析场景示例：走消费面 /knowledge/scenarios（管理面对非 admin 是 403，
  // 原先被 catch 吞成"没有示例"）；失败仍显式留在控制台，不影响其余区块。
  useEffect(() => {
    let cancelled = false;
    client.get('/knowledge/scenarios', { params: { limit: 4, workspace_id: 0 } })
      .then(({ data }) => {
        if (cancelled) return;
        setQuestions((data.items || []).map((it: { title?: string }) => it.title).filter(Boolean));
      })
      .catch((err) => { console.warn('[home] 分析场景示例未加载（不影响问数）:', err); });
    return () => { cancelled = true; };
  }, []);

  // 待办计数：只在具备对应权限时发请求。无权限的请求一定 403，
  // 把 403 画成 0 就是把"权限没配"伪装成"你没有待办"（no-silent-degradation）。
  useEffect(() => {
    if (!permsLoaded || !showFeed) return;
    let cancelled = false;
    const next: RailStats = { drafts: null, pending: null, expired: null };
    const jobs: Array<Promise<unknown>> = [];
    if (canManage && user?.username) {
      jobs.push(contentApi.list({ status: 'draft', mine: user.username, page_size: 1 })
        .then(({ data }) => { next.drafts = data.total ?? 0; })
        .catch(() => { next.drafts = null; }));
    }
    if (canReview) {
      jobs.push(contentApi.pendingReviews({ page_size: 1 })
        .then(({ data }) => {
          next.pending = data.total ?? 0;
          next.expired = (data.review_expired || []).length;
        })
        .catch(() => { next.pending = null; }));
    }
    if (!jobs.length) return;
    Promise.all(jobs).then(() => { if (!cancelled) setStats(next); });
    return () => { cancelled = true; };
  }, [permsLoaded, showFeed, canManage, canReview, user?.username]);

  const goAsk = useCallback(() => {
    const wsId = getDefaultWorkspaceId();
    navigate(wsId ? `/ws/${wsId}/chat` : '/workspace');
  }, [getDefaultWorkspaceId, navigate]);

  const ask = useCallback((text: string) => {
    const q = (text || '').trim();
    if (!q) { goAsk(); return; }
    // send：用户在首页已经按下发送，到问数页必须直接执行（只预填等于把同一个动作要他做两遍）
    stashQuestion(q, { send: true });
    goAsk();
  }, [goAsk]);

  const goWorkspace = () => {
    const wsId = getDefaultWorkspaceId();
    navigate(wsId ? `/ws/${wsId}/dashboards` : '/workspace');
  };
  const goProfile = () => {
    const wsId = getDefaultWorkspaceId();
    navigate(wsId ? `/ws/${wsId}/profile` : '/data/profile');
  };
  const goMemory = () => {
    const wsId = getDefaultWorkspaceId();
    navigate(wsId ? `/ws/${wsId}/memory` : '/data/memory');
  };
  const goBoard = useCallback((id: number) => {
    // -1 = 「全部」：落看板清单；具体看板落详情（与模块切换的默认页同一入口）
    navigate(id > 0 ? `/dashboards/${id}` : '/dashboards');
  }, [navigate]);
  const goContent = () => navigate('/content');
  const goRead = useCallback((slug: string) => navigate(`/read/${encodeURIComponent(slug)}`), [navigate]);

  const appName = brand.app_name || DEFAULT_APP_NAME;
  // 角色展示以服务端下发的可读名为准（自定义角色如「运营管理」不该露内部英文名）；
  // 登录瞬间 refreshUser 未回来前才用三档内置文案兜底
  const roleLabel = user?.role_name || ROLE_LABELS[user?.role || ''] || user?.role || '未知角色';
  const dateText = new Date().toLocaleDateString('zh-CN', {
    year: 'numeric', month: 'long', day: 'numeric', weekday: 'long'});
  const bgCfg = useVisConfig('bg_home_ambient', DEFAULT_BG);

  const decor = (
    <div aria-hidden className="pointer-events-none fixed inset-0 z-0 overflow-hidden"
      style={{ backgroundColor: bgCfg.backgroundColor }}>
      {bgCfg.backgroundImage ? (
        <div className="absolute inset-0"
          style={{ backgroundImage: bgCfg.backgroundImage, backgroundSize: bgCfg.backgroundSize || undefined }} />
      ) : null}
      {bgCfg.overlay ? (
        <div className="absolute inset-0" style={{ backgroundImage: bgCfg.overlay, backgroundRepeat: 'no-repeat' }} />
      ) : null}
    </div>
  );

  // 顶栏:品牌 + 切换主题 + 个人中心/退出登录(首页独立于各布局,需自带账号入口)
  const header = (
    <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1320px] items-center justify-between px-6 py-3">
        <div className="flex items-center gap-2.5">
          <BrandLogo brand={brand} size={28} />
          {brand.show_text && <span className="text-lg font-semibold tracking-tight">{appName}</span>}
          <span className="hidden text-sm text-muted-foreground sm:inline">· 首页</span>
        </div>
        <div className="flex items-center gap-1.5">
          {/* 版本公告在模块切换左侧(与三套布局一致), 首页也保留同一组锚点供引导命中 */}
          <ReleaseNotesButton />
          <SectionSwitcher current="home" />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="h-9 w-9 p-0" aria-label="切换主题" title="切换主题" data-tour={TOUR.headerTheme}>
                <Palette className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <ThemeMenuItems />
            </DropdownMenuContent>
          </DropdownMenu>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button aria-label="用户菜单" data-tour={TOUR.headerUser} className="flex min-h-[36px] items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-muted">
                <Avatar className="h-6 w-6">
                  <AvatarFallback className="text-xs">{user?.username?.charAt(0)?.toUpperCase() || 'U'}</AvatarFallback>
                </Avatar>
                <span className="hidden text-sm font-medium sm:inline">{user?.username}</span>
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {/* 手动重看入口: 不受 guide:all 静音影响, 永远可用 */}
              <DropdownMenuItem onClick={() => startGuide('welcome')}>
                <Compass className="mr-2 h-4 w-4" />
                新手引导
              </DropdownMenuItem>
              <DropdownMenuItem onClick={goProfile}>
                <UserCircle className="mr-2 h-4 w-4" />
                个人设置
              </DropdownMenuItem>
              <DropdownMenuItem onClick={goMemory}>
                <Brain className="mr-2 h-4 w-4" />
                我的记忆
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
        <div className="relative z-10 mx-auto max-w-[1320px] px-6 py-8">
          <Skeleton className="h-32 w-full rounded-2xl" />
          <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_316px]">
            <Skeleton className="h-72 rounded-2xl" />
            <div className="space-y-4">
              <Skeleton className="h-32 rounded-xl" />
              <Skeleton className="h-28 rounded-xl" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  // 底部模块入口：与顶栏「模块切换」同一份判定口径（前缀反查可见菜单），
  // 系统配置仍按 admin（SystemLayout 对非 admin 硬跳转，给入口等于空承诺）
  const showAsk = menuAccess.ready && menuAccess.allowed('workspace:chat');
  const showData = menuAccess.ready && menuAccess.prefixAllowed('data:');
  const showContent = menuAccess.ready && menuAccess.prefixAllowed('content:');
  const mods: Array<{ key: string; icon: LucideIcon; title: string; desc: string; go: () => void }> = [
    ...(showAsk ? [{ key: 'ws', icon: Folder, title: '工作空间', desc: '看板与智能问数', go: goWorkspace }] : []),
    ...(showData ? [{ key: 'data', icon: Database, title: '数据中台', desc: '数据源 / 本体建模 / 安全', go: () => navigate('/data') }] : []),
    ...(showContent ? [{ key: 'content', icon: Newspaper, title: '内容中心', desc: '创作 / 审核 / 发布', go: goContent }] : []),
    ...(isAdmin ? [{ key: 'sys', icon: Settings, title: '系统配置', desc: '模型 / AS-BOT / 用户', go: () => navigate('/system') }] : []),
  ];

  return (
    <div className="relative h-full overflow-auto">
      {decor}
      {header}
      <div className="relative z-10 mx-auto max-w-[1320px] px-6 pb-14 pt-5">

        {/* ① Hero：问候 + 提问框 + 企业简介卡
            右列固定 316px（与下方行动栏同宽，上下两栏右边缘对齐）且随左列等高拉伸：
            窄卡横排、贴顶或竖排三种写法都在实测里出现过死区，这里按同宽等高收口。 */}
        {/* 简介卡与资讯区同一把权限码（article:read → home:feed）：没码就不渲染、
            也不发内容请求；此时 Hero 收成单列，否则空右列会把提问条挤成半屏宽。 */}
        <section data-tour={TOUR.homeOverview}
          className={`grid gap-4 rounded-2xl border bg-card p-5${showFeed ? ' lg:grid-cols-[minmax(0,1fr)_316px]' : ''}`}>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <LayoutDashboard className="h-3.5 w-3.5 text-primary" />
              <span>{dateText}</span>
              <span className="text-border">·</span>
              <span>欢迎回到工作台</span>
            </div>
            <h1 className="mt-1.5 text-[22px] font-semibold leading-tight tracking-tight">
              {greetingOf()}，{user?.username || '用户'}
              <Badge variant="secondary" className="ml-2.5 align-middle text-[11px] font-normal">{roleLabel}</Badge>
            </h1>
            <div className="mt-3.5 flex w-full max-w-[900px] items-center gap-2">
              <div className="flex h-11 flex-1 items-center gap-2.5 rounded-xl border bg-background px-3.5 focus-within:border-ring focus-within:ring-2 focus-within:ring-primary/20">
                <Sparkles className="h-4 w-4 flex-none text-primary" />
                <input
                  ref={askRef}
                  value={draft}
                  onChange={e => setDraft(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') ask(draft); }}
                  placeholder="想查什么？直接问，例如：上个月各站点的活跃装机数同比"
                  aria-label="向智能问数提问"
                  className="min-w-0 flex-1 bg-transparent text-[13.5px] outline-none placeholder:text-muted-foreground"
                />
                <kbd className="hidden flex-none rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground sm:inline">Enter</kbd>
              </div>
              <Button className="h-11 px-4" onClick={() => ask(draft)}>
                <MessageSquare className="mr-1.5 h-4 w-4" />问数
              </Button>
            </div>
            {!!questions.length && (
              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                <span className="text-[11.5px] text-muted-foreground">试试这样问</span>
                {questions.map(q => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => { setDraft(q); askRef.current?.focus(); }}
                    className="rounded-full border px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/55 hover:bg-primary/5 hover:text-primary"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
          </div>

          {showFeed && <CompanyProfile
            canManage={canManage}
            onOpen={goRead}
            onManage={goContent}
          />}
        </section>

        {/* ② 双栏主体：资讯流（主内容位）+ 行动栏 */}
        <WorkbenchPane
          showFeed={showFeed}
          boards={boards}
          stats={stats}
          tag={tag}
          onTag={setTag}
          canManage={canManage}
          canReview={canReview}
          onOpenBoard={goBoard}
          onGoContent={goContent}
          onOpen={goRead}
          onAskAboutFeed={() => askRef.current?.focus()}
          onConfigureDashboards={isAdmin ? () => navigate('/system/dashboards') : undefined}
        />

        {/* ③ 底部一行模块入口 */}
        {!!mods.length && (
          <nav data-tour={TOUR.homeQuick} aria-label="模块入口"
            className="mt-5 grid gap-2.5 rounded-2xl border bg-card/70 p-3 sm:grid-cols-2 lg:grid-cols-4">
            {mods.map(m => {
              const Icon = m.icon;
              return (
                <button
                  key={m.key}
                  type="button"
                  onClick={m.go}
                  className="group flex items-center gap-2.5 rounded-xl px-2.5 py-2 text-left transition-colors hover:border hover:bg-muted/70"
                >
                  <span className="flex h-8 w-8 flex-none items-center justify-center rounded-lg bg-primary/12 text-primary transition-colors group-hover:bg-primary group-hover:text-primary-foreground">
                    <Icon className="h-4 w-4" />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-[13px] font-medium leading-tight">{m.title}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">{m.desc}</span>
                  </span>
                  <ArrowRight className="ml-auto h-3.5 w-3.5 flex-none text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                </button>
              );
            })}
          </nav>
        )}
      </div>
    </div>
  );
}
