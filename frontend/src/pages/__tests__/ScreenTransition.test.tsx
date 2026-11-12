import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { DEFAULT_MOTION, type MotionSpec } from '@/lib/motion';
import { applyScreenComponent } from '@/lib/dashboardDesign';
import type { VisComponent } from '@/api/visLibrary';
import { useAuthStore } from '@/stores/authStore';
import { useMotionPrefStore } from '@/stores/motionPrefStore';
import client from '@/api/client';
import { TooltipProvider } from '@/components/ui/tooltip';
import Screen from '../Screen';

/**
 * 大屏播放的切板转场（P2）。
 *
 * 本期播放范围只有**切板转场**（不含刷新渐入与 KPI 数字滚动），所以这里守的是：
 * - 未配置 / `mp_none` → 外壳不加任何 class，硬切与今天完全一致（默认零变化）；
 * - 板上挂载 > 个人偏好（分面裁决），时长与缓动由 CSS 变量下发；
 * - 转场不短于轮播间隔时**显式报错**，而不是让转场被下一块板静默截断；
 * - 预设失效（禁用且无快照）→ 回退默认并把原因说在明处。
 */

const library = vi.hoisted(() => ({ items: [] as VisComponent[], error: null as string | null, loaded: true, refresh: vi.fn() }));
const catalog = vi.hoisted(() => ({ groups: [] as any[] }));

vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => library }));
vi.mock('@/lib/dashboardCatalog', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/dashboardCatalog')>()),
  fetchVisibleGroups: () => Promise.resolve(catalog.groups),
}));
// 画布本体不进本用例（图表引擎由 DashboardCanvasMotion 覆盖）：只留一个位置探针
vi.mock('@/components/DashboardCanvas', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/components/DashboardCanvas')>()),
  ResponsiveDashboardCanvas: ({ dashboard }: any) => <div data-testid="canvas" data-board={dashboard?.id} />,
}));

/** 与迁移 seed 同值：切板横向推入 550ms */
const PRESENTATION: MotionSpec = {
  ...DEFAULT_MOTION,
  enter: { ...DEFAULT_MOTION.enter, effect: 'slideDown', staggerMs: 140, maxStaggerMs: 900 },
  chart: { appear: 'growInY', durationMs: 700, easing: 'standard' },
  transition: { effect: 'pushLeft', durationMs: 550, easing: 'standard' },
  reveal: { effect: 'sectionStagger', staggerMs: 120 },
};
const NONE: MotionSpec = {
  enter: { effect: 'none', durationMs: 0, easing: 'standard', staggerMs: 0, maxStaggerMs: 0, offsetPx: 0 },
  chart: { appear: 'none', durationMs: 0, easing: 'linear' },
  transition: { effect: 'none', durationMs: 0, easing: 'linear' },
  reveal: { effect: 'none', staggerMs: 0 },
};
const comp = (code: string, name: string, style: MotionSpec, over: Partial<VisComponent> = {}): VisComponent => ({
  id: 1, code, name, category: 'motion_preset', chart_type: null, style_config: style,
  source: 'system', is_builtin: 1, is_active: 1, is_enabled: 1, sort_order: 85, description: '', ...over,
} as VisComponent);
const presentation = () => comp('mp_presentation', '演示播报', PRESENTATION);
const none = () => comp('mp_none', '关闭动效', NONE, { sort_order: 81 });

const BASE_CLASS = 'relative min-h-0 flex-1 overflow-y-auto overflow-x-hidden';
const boardFilters = (motion?: VisComponent) => (motion ? applyScreenComponent({}, motion) : {});
function boards(motionA?: VisComponent, motionB?: VisComponent) {
  return [
    { id: 7, name: '销售总览', status: 'enabled', is_default: 1, owner_id: 1 },
    { id: 8, name: '运维监控', status: 'enabled', is_default: 0, owner_id: 1 },
  ].map((d, i) => ({ ...d, filters: boardFilters(i === 0 ? motionA : motionB) }));
}

function renderScreen(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      {/* 顶栏刷新控件用了 Tooltip：应用里有全局 Provider，这里自己补上 */}
      <TooltipProvider delayDuration={0}>
        <Routes><Route path="/screen/:dashboardId" element={<Screen />} /></Routes>
        <Jump />
      </TooltipProvider>
    </MemoryRouter>,
  );
}
/** 站内跳板（真实切板就是这条路：轮播 / 上下板 / 下拉切换都走 navigate） */
function Jump() {
  const navigate = useNavigate();
  return <button onClick={() => navigate('/screen/8?group=1')}>去 8 号板</button>;
}

beforeEach(() => {
  library.items = [comp('mp_default', '默认（现网）', DEFAULT_MOTION, { id: 2, sort_order: 80 })];
  library.error = null;
  catalog.groups = [{ id: 1, name: '经营分析组', description: '', sort: 0, dashboards: boards() }];
  vi.spyOn(client, 'get').mockImplementation(async (url: string) => {
    const id = Number(/dashboard\/(\d+)/.exec(url)?.[1]);
    const [a, b] = catalog.groups[0].dashboards;
    return { data: id === 8 ? b : a } as any;
  });
  useMotionPrefStore.setState({ presetCode: null, override: null, loaded: true, error: '', fetching: false });
  useAuthStore.setState({ user: { id: 1, username: 'tester', role: 'admin' } as any });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('大屏切板转场', () => {
  it('未挂字模、未落偏好：画布外壳没有任何转场 class（硬切如旧，DOM 逐字节一致）', async () => {
    const { container } = renderScreen('/screen/7?group=1');
    await screen.findByText('销售总览');
    expect(container.querySelector('[data-testid="canvas"]')!.parentElement!.getAttribute('class')).toBe(BASE_CLASS);
  });

  it('板上挂了「演示播报」：切板外壳出现推入转场，时长走 CSS 变量', async () => {
    catalog.groups = [{ id: 1, name: '经营分析组', description: '', sort: 0, dashboards: boards(presentation(), presentation()) }];
    const { container } = renderScreen('/screen/7?group=1');
    await screen.findByText('销售总览');
    const shell = container.querySelector('[data-testid="canvas"]')!.parentElement!;
    expect(shell.getAttribute('class')).toContain('motion-transition motion-transition-pushLeft');
    expect(shell.getAttribute('style')).toContain('--motion-transition-dur: 550ms');
    // 入场段同样下发（错峰时长供画布读取），但转场不得把入场 class 混进来
    expect(shell.getAttribute('style')).toContain('--motion-enter-dur: 450ms');
    expect(shell.getAttribute('class')).not.toContain('motion-enter');
  });

  it('板上没挂 → 跟随个人预设；挂了 → 板上优先（分面裁决）', async () => {
    useMotionPrefStore.setState({ presetCode: 'mp_presentation' });
    library.items = [...library.items, presentation()];
    const mounted = renderScreen('/screen/7?group=1');
    await screen.findByText('销售总览');
    // 板上未挂：个人预设生效
    expect(mounted.container.querySelector('[data-testid="canvas"]')!.parentElement!.className).toContain('motion-transition-pushLeft');
    cleanup();
    // 板上挂了「关闭动效」：观看者的个人预设不得改掉别人的板子
    catalog.groups = [{ id: 1, name: '经营分析组', description: '', sort: 0, dashboards: boards(none(), none()) }];
    library.items = [...library.items, none()];
    const second = renderScreen('/screen/7?group=1');
    await screen.findByText('销售总览');
    expect(second.container.querySelector('[data-testid="canvas"]')!.parentElement!.className).toBe(BASE_CLASS);
  });

  it('切到新看板 = 外壳重挂载一次，转场这才该重播（同位置也不是复用旧节点）', async () => {
    catalog.groups = [{ id: 1, name: '经营分析组', description: '', sort: 0, dashboards: boards(presentation(), presentation()) }];
    const { container } = renderScreen('/screen/7?group=1');
    await screen.findByText('销售总览');
    const before = container.querySelector('[data-testid="canvas"]')!.parentElement!;
    fireEvent.click(screen.getByText('去 8 号板'));
    await screen.findByText('运维监控');
    const after = container.querySelector('[data-testid="canvas"]')!.parentElement!;
    expect(after).not.toBe(before);
    expect(after.getAttribute('class')).toContain('motion-transition-pushLeft');
  });

  it('重渲染不重播：转场只挂在切板容器上，同一块板重渲染仍复用同一节点', async () => {
    catalog.groups = [{ id: 1, name: '经营分析组', description: '', sort: 0, dashboards: boards(presentation(), presentation()) }];
    const { container } = renderScreen('/screen/7?group=1');
    await screen.findByText('销售总览');
    const before = container.querySelector('[data-testid="canvas"]')!.parentElement!;
    // 偏好回读/数据覆盖层变更都会重渲染整页：只要不是切板，就不该换掉正在动画的那一层
    act(() => useMotionPrefStore.setState({ presetCode: 'mp_presentation' }));
    await screen.findByText('销售总览');
    expect(container.querySelector('[data-testid="canvas"]')!.parentElement!).toBe(before);
  });

  it('转场不短于轮播间隔 → 显式告警给出路（不静默被下一块板截断）', async () => {
    // 轮播下限 3s（Screen 的链接护栏），所以要拿一个长转场预设才能踩到冲突
    const slow = comp('mp_slow', '慢动作', { ...PRESENTATION, transition: { effect: 'pushLeft', durationMs: 3000, easing: 'standard' } });
    library.items = [...library.items, slow];
    catalog.groups = [{ id: 1, name: '经营分析组', description: '', sort: 0, dashboards: boards(slow, slow) }];
    renderScreen('/screen/7?group=1&interval=3');
    await screen.findByText('销售总览');
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('切板转场 3000ms 不短于轮播间隔 3s');
    expect(alert).toHaveTextContent('缩短转场时长');
  });

  it('关闭动效档（mp_none）与未配置一样：不加 class', async () => {
    catalog.groups = [{ id: 1, name: '经营分析组', description: '', sort: 0, dashboards: boards(none(), none()) }];
    library.items = [...library.items, none()];
    const { container } = renderScreen('/screen/7?group=1');
    await screen.findByText('销售总览');
    expect(container.querySelector('[data-testid="canvas"]')!.parentElement!.getAttribute('class')).toBe(BASE_CLASS);
  });

  it('挂载的字模被禁用且无快照可兜 → 回退默认并写明原因（不许"挂了没反应"）', async () => {
    const retired = { ...presentation(), is_enabled: 0 };
    catalog.groups = [{ id: 1, name: '经营分析组', description: '', sort: 0, dashboards: [
      { id: 7, name: '销售总览', status: 'enabled', is_default: 1, owner_id: 1,
        filters: { components: { motion: 'mp_presentation' } } },
      { id: 8, name: '运维监控', status: 'enabled', is_default: 0, owner_id: 1, filters: {} },
    ] }];
    library.items = [...library.items, retired];
    const { container } = renderScreen('/screen/7?group=1');
    await screen.findByText('销售总览');
    expect(container.querySelector('[data-testid="canvas"]')!.parentElement!.getAttribute('class')).toBe(BASE_CLASS);
    expect(screen.getByRole('status')).toHaveTextContent('动效预设「mp_presentation」不在字模库');
  });

  it('系统开启减少动态效果：转场同样归零，并说明原因', async () => {
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: /reduced-motion/.test(query), media: query, onchange: null,
      addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
    }) as any);
    catalog.groups = [{ id: 1, name: '经营分析组', description: '', sort: 0, dashboards: boards(presentation(), presentation()) }];
    library.items = [...library.items, presentation()];
    const { container } = renderScreen('/screen/7?group=1');
    await screen.findByText('销售总览');
    expect(container.querySelector('[data-testid="canvas"]')!.parentElement!.getAttribute('class')).toBe(BASE_CLASS);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole('status')).toHaveTextContent('减少动态效果');
  });
});
