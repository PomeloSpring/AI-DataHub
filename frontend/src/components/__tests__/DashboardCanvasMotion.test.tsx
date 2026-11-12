import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';
import { DEFAULT_MOTION, type MotionSpec } from '@/lib/motion';
import { applyScreenComponent } from '@/lib/dashboardDesign';
import type { VisComponent } from '@/api/visLibrary';
import { useDashboardStore, type DashboardChart as ChartModel } from '@/stores/dashboardStore';
import { useMotionPrefStore } from '@/stores/motionPrefStore';
import type { PageItem } from '@/lib/dashboardPageLayout';
import { DashboardCanvas, ResponsiveDashboardCanvas } from '../DashboardCanvas';

/**
 * 看板/大屏的「出现效果」接线用例（P2）。
 *
 * 守三条口径：
 * 1. **默认零变化**：板上没挂字模、用户没落偏好时，画布一个动效 class 都不加、G2 spec 不写 `animate`；
 * 2. **只在出现这一刻播**：卡片节点跨重渲染复用（拖动/缩放/刷新不重挂 = CSS 动画不重播），图表实例不重建；
 * 3. **无障碍压过一切**：prefers-reduced-motion 命中时板上挂了字模也不加 class，并把图表动画关掉。
 */

const library = vi.hoisted(() => ({ items: [] as VisComponent[], error: null as string | null, loaded: true, refresh: vi.fn() }));
const probes = vi.hoisted(() => ({ mounts: 0, animate: {} as Record<string, unknown> }));

vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => library }));
// 图表引擎不进本用例：只记录「有没有把 animate 传下去」与「实例是否被重建」
vi.mock('@/components/DashboardChart', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/DashboardChart')>();
  const { useEffect } = await import('react');
  return {
    ...actual,
    default: function Probe({ chartId, motionAnimate }: any) {
      useEffect(() => {
        probes.mounts += 1;
        return () => { probes.mounts -= 1; };
      }, []);
      probes.animate[String(chartId)] = motionAnimate;
      return <div data-testid="chart" />;
    },
  };
});

/** 与迁移 seed 同值的 `mp_soft`（入场 700ms / 错峰 120ms / 上限 600ms / 图表淡入） */
const SOFT: MotionSpec = {
  enter: { effect: 'fadeUp', durationMs: 700, easing: 'standard', staggerMs: 120, maxStaggerMs: 600, offsetPx: 16 },
  chart: { appear: 'fadeIn', durationMs: 700, easing: 'standard' },
  transition: { effect: 'fadeIn', durationMs: 700, easing: 'standard' },
  reveal: { effect: 'fadeIn', staggerMs: 120 },
};
const comp = (code: string, name: string, style: MotionSpec, over: Partial<VisComponent> = {}): VisComponent => ({
  id: 1, code, name, category: 'motion_preset', chart_type: null, style_config: style,
  source: 'system', is_builtin: 1, is_active: 1, is_enabled: 1, sort_order: 82, description: '', ...over,
} as VisComponent);
const soft = () => comp('mp_soft', '柔和舒缓', SOFT);
const defaultPreset = () => comp('mp_default', '默认（现网）', DEFAULT_MOTION, { id: 2, sort_order: 80 });

const chart = (id: number, name = `图 ${id}`): ChartModel => ({
  id, name, chart_type: 'bar', config: {}, position: { x: 0, y: 0, w: 4, h: 2 },
  data_cache: JSON.stringify({ columns: ['name', 'value'], rows: [{ name: 'A', value: 1 }] }),
} as ChartModel);
const pageItem = (c: ChartModel, x: number, y: number): PageItem => ({ chart: c, rect: { x, y, w: 4, h: 2 }, automatic: false });
/** DOM 顺序刻意与画布位置相反：错峰必须按位置排，不能按数组下标排 */
const CHARTS = [chart(13), chart(11), chart(12)];
const LAYOUT = [pageItem(CHARTS[0], 0, 8), pageItem(CHARTS[1], 0, 0), pageItem(CHARTS[2], 6, 0)];

function board(motion?: VisComponent) {
  const filters = motion ? applyScreenComponent({}, motion) : {};
  return { id: 88, filters, charts: CHARTS };
}
const cardNode = (container: HTMLElement, id: number) => container.querySelector<HTMLElement>(`[data-chart-id="${id}"]`);
const canvasNode = (container: HTMLElement) => container.querySelector<HTMLElement>('.adh-page-canvas')!;

beforeEach(() => {
  probes.mounts = 0;
  probes.animate = {};
  library.items = [];
  library.error = null;
  useMotionPrefStore.setState({ presetCode: null, override: null, loaded: true, error: '', fetching: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('看板卡片入场（板上挂载才播，默认一档零变化）', () => {
  it('未挂字模、未落偏好：卡片不加任何动效 class，图表 spec 不写 animate 键', () => {
    library.items = [defaultPreset()];
    const { container } = render(<ResponsiveDashboardCanvas dashboard={board()} items={library.items} breakpoint="desktop" layout={LAYOUT} />);
    const card = cardNode(container, 11)!;
    expect(card.getAttribute('class')).toBe('min-h-0 min-w-0');   // 连尾随空格都不多（DOM 与改造前一致）
    expect(card.getAttribute('data-enter')).toBeNull();
    expect(card.style.animationDelay).toBe('');
    expect(probes.animate['11']).toBeUndefined();
    expect(probes.animate['13']).toBeUndefined();
  });

  it('像素画布同理：默认档下卡片外壳仍是 "absolute min-h-0"', () => {
    library.items = [defaultPreset()];
    const { container } = render(<DashboardCanvas dashboard={board()} items={library.items} />);
    const wrapper = container.querySelectorAll<HTMLElement>('.adh-canvas > div')[0];
    expect(wrapper.getAttribute('class')).toContain('absolute min-h-0');
    expect(wrapper.getAttribute('class')!.trim()).toBe('absolute min-h-0');
  });

  it('板上挂了字模：按画布位置错峰（左上先出现），DOM 顺序不参与排序', () => {
    const preset = soft();
    library.items = [defaultPreset(), preset];
    const { container } = render(
      <ResponsiveDashboardCanvas dashboard={board(preset)} items={library.items} breakpoint="desktop" layout={LAYOUT} />,
    );
    expect(canvasNode(container).getAttribute('style')).toContain('--motion-enter-dur: 700ms');
    expect(canvasNode(container).getAttribute('style')).toContain('--motion-enter-offset: 16px');
    // 11 在最上（y=0）→ rank 0 无延迟；12 与其同行更靠右 → rank 1；13 在下面 → rank 2
    expect(cardNode(container, 11)!.getAttribute('class')).toContain('motion-enter motion-enter-fadeUp');
    expect(cardNode(container, 11)!.style.animationDelay).toBe('');
    expect(cardNode(container, 12)!.style.animationDelay).toBe('120ms');
    expect(cardNode(container, 13)!.style.animationDelay).toBe('240ms');
    // 图表入场同一段解析：显式预设才写 animate
    expect(probes.animate['11']).toEqual({ enter: { type: 'fadeIn', duration: 700, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' } });
  });

  it('错峰受 maxStaggerMs 封顶：长看板尾部卡片不必等太久', () => {
    const charts = Array.from({ length: 8 }, (_, i) => chart(20 + i));
    const layout = charts.map((c, i) => pageItem(c, 0, i * 3));
    library.items = [soft()];
    const { container } = render(
      <ResponsiveDashboardCanvas dashboard={{ id: 88, filters: applyScreenComponent({}, soft()), charts }}
        items={library.items} breakpoint="desktop" layout={layout} />,
    );
    // 7 × 120 = 840 > 600 → 落在上限
    expect(cardNode(container, 27)!.style.animationDelay).toBe('600ms');
  });

  it('编辑态不重播整版：拖一张图不该让全屏再闪一遍', () => {
    const preset = soft();
    library.items = [preset];
    const { container } = render(
      <ResponsiveDashboardCanvas dashboard={board(preset)} items={library.items} breakpoint="desktop" layout={LAYOUT} draft />,
    );
    for (const id of [11, 12, 13]) expect(cardNode(container, id)!.getAttribute('class')).toBe('min-h-0 min-w-0');
  });

  it('个人预设只在未挂载的看板上生效（分面裁决：板上挂载 > 用户偏好）', () => {
    library.items = [defaultPreset(), soft(), comp('mp_presentation', '演示播报',
      { ...DEFAULT_MOTION, enter: { ...DEFAULT_MOTION.enter, effect: 'slideDown' } })];
    useMotionPrefStore.setState({ presetCode: 'mp_presentation' });
    const mounted = render(<ResponsiveDashboardCanvas dashboard={board(soft())} items={library.items} breakpoint="desktop" layout={LAYOUT} />);
    expect(cardNode(mounted.container, 11)!.getAttribute('class')).toContain('motion-enter-fadeUp');
    const plain = render(<ResponsiveDashboardCanvas dashboard={board()} items={library.items} breakpoint="desktop" layout={LAYOUT} />);
    // 没挂字模的看板：来源=个人预设（显式选过），按我的滑杆呈现
    expect(cardNode(plain.container, 11)!.getAttribute('class')).toContain('motion-enter-slideDown');
  });

  it('重渲染（拖动改坐标、刷新换数据）复用同一批卡片节点，图表实例不重建', () => {
    const preset = soft();
    library.items = [preset];
    const dashboard = board(preset);
    const { container, rerender } = render(
      <ResponsiveDashboardCanvas dashboard={dashboard} items={library.items} breakpoint="desktop" layout={LAYOUT} />,
    );
    const before = cardNode(container, 11)!;
    const mounts = probes.mounts;
    // 模拟拖拽 + 自动刷新：坐标变了、data_cache 也被 store 覆盖层换掉
    act(() => useDashboardStore.setState({ chartCache: { 11: JSON.stringify({ columns: ['name', 'value'], rows: [{ name: 'A', value: 9 }] }) } } as any));
    rerender(<ResponsiveDashboardCanvas dashboard={{ ...board(preset), charts: CHARTS.map(c => ({ ...c, config: { moved: true } })) }}
      items={library.items} breakpoint="desktop" layout={[pageItem(CHARTS[0], 0, 8), pageItem(CHARTS[1], 2, 2), pageItem(CHARTS[2], 6, 0)]} />);
    expect(cardNode(container, 11)).toBe(before);                       // 同一节点 = CSS 动画不会重播
    expect(before.getAttribute('class')).toContain('motion-enter-fadeUp');
    expect(probes.mounts).toBe(mounts);                                 // 图表实例数不变（不销毁重建）
  });

  it('换一块看板 = 重挂载一次，入场这才该重播', () => {
    const preset = soft();
    library.items = [preset];
    const { container, rerender } = render(
      <ResponsiveDashboardCanvas dashboard={board(preset)} items={library.items} breakpoint="desktop" layout={LAYOUT} />,
    );
    const before = cardNode(container, 11)!;
    const other = { id: 90, filters: applyScreenComponent({}, preset), charts: [chart(31), chart(32), chart(33)] };
    rerender(<ResponsiveDashboardCanvas dashboard={other} items={library.items} breakpoint="desktop"
      layout={other.charts.map((c, i) => pageItem(c, 0, i * 3))} />);
    expect(cardNode(container, 11)).toBeNull();
    expect(cardNode(container, 31)).not.toBe(before);
    expect(cardNode(container, 31)!.getAttribute('class')).toContain('motion-enter-fadeUp');
  });

  it('prefers-reduced-motion 压过板上挂载：不加 class，且把图表动画关掉', () => {
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: /reduced-motion/.test(query), media: query, onchange: null,
      addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
    }) as any);
    const preset = soft();
    library.items = [preset];
    const { container } = render(
      <ResponsiveDashboardCanvas dashboard={board(preset)} items={library.items} breakpoint="desktop" layout={LAYOUT} />,
    );
    for (const id of [11, 12, 13]) expect(cardNode(container, id)!.getAttribute('class')).toBe('min-h-0 min-w-0');
    expect(probes.animate['11']).toBe(false);   // 不是 undefined：G2 明确关掉 enter/update/exit
  });

  it('编辑器 draft 态平时不播，但「重新播放」信号会临时开一次入场（不重建图表）', async () => {
    vi.useFakeTimers();
    try {
      const preset = soft();
      library.items = [preset];
      const canvas = (nonce: number) => (
        <ResponsiveDashboardCanvas dashboard={board(preset)} items={library.items} breakpoint="desktop" layout={LAYOUT} draft entranceReplay={nonce} />
      );
      const { container, rerender } = render(canvas(0));
      // draft 平时：无入场 class（零变化）
      expect(cardNode(container, 11)!.getAttribute('class')).toBe('min-h-0 min-w-0');
      const mounts = probes.mounts;
      // 点「重新播放」：nonce 递增
      rerender(canvas(1));
      // 双帧后进入 replay 窗口 → 卡片临时拿到入场 class（class 从无到有 → CSS 动画播一次）
      await act(async () => { vi.advanceTimersByTime(48); });
      expect(cardNode(container, 11)!.getAttribute('class')).toContain('motion-enter-fadeUp');
      expect(probes.mounts).toBe(mounts);   // 只改 class，不重挂图表
      // 窗口结束（时长 700 + 错峰上限 600 + 缓冲 200）后自动撤 class
      await act(async () => { vi.advanceTimersByTime(700 + 600 + 200 + 100); });
      expect(cardNode(container, 11)!.getAttribute('class')).toBe('min-h-0 min-w-0');
    } finally {
      vi.useRealTimers();
    }
  });
});
