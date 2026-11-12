import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { DEFAULT_MOTION, type MotionSpec } from '@/lib/motion';
import { applyScreenComponent } from '@/lib/dashboardDesign';
import type { VisComponent } from '@/api/visLibrary';
import { useMotionPrefStore } from '@/stores/motionPrefStore';
import PropertyPanel from '../editor/PropertyPanel';

/**
 * 看板属性面板的「动效」分组（P2）。
 *
 * 要守的是配置面的老毛病（ui-vis-library-style）：
 * - **库优先、快照只兜底**：改了字模，已挂载的看板立刻跟着变，不需要重挂；
 * - **禁用/下架看得见**：回退到挂载时快照时说明白，而不是装作按配置呈现；
 * - **写入口唯一**：挂载走 `applyScreenComponent` 的形状、取消挂载连快照一起清（摘了就不再生效）；
 * - **显示业务名**：下拉与生效说明都用预设名，不裸露 `mp_*`。
 */

const library = vi.hoisted(() => ({ items: [] as VisComponent[], error: null as string | null, loaded: true, refresh: vi.fn() }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => library }));

const SOFT: MotionSpec = {
  ...DEFAULT_MOTION,
  enter: { effect: 'fadeUp', durationMs: 700, easing: 'standard', staggerMs: 120, maxStaggerMs: 600, offsetPx: 16 },
  chart: { appear: 'fadeIn', durationMs: 700, easing: 'standard' },
  transition: { effect: 'fadeIn', durationMs: 700, easing: 'standard' },
  reveal: { effect: 'fadeIn', staggerMs: 120 },
};
const comp = (code: string, name: string, style: MotionSpec, over: Partial<VisComponent> = {}): VisComponent => ({
  id: 1, code, name, category: 'motion_preset', chart_type: null, style_config: style,
  source: 'system', is_builtin: 1, is_active: 1, is_enabled: 1, sort_order: 82, description: '', ...over,
} as VisComponent);
const defaultPreset = () => comp('mp_default', '默认（现网）', DEFAULT_MOTION, { id: 2, sort_order: 80 });
const soft = (over: Partial<VisComponent> = {}) => comp('mp_soft', '柔和舒缓', SOFT, over);

function panel(filters: any, onFiltersChange = vi.fn()) {
  render(<PropertyPanel
    isOpen onClose={() => {}} selectedChart={null} selectedElementType="canvas"
    canvasSize={{ width: 1920, height: 1080 }} setCanvasSize={() => {}} canvasBgColor="" setCanvasBgColor={() => {}}
    gridSize={20} setGridSize={() => {}} scale={1} setScale={() => {}} allCharts={[]}
    isNewChart={() => false} onPropertyChange={() => {}} onPositionChange={() => {}} onWidgetConfigChange={() => {}}
    onDelete={() => {}} onChartConfig={() => {}} tab="appearance" onTab={() => {}}
    filters={filters} onFiltersChange={onFiltersChange} />);
  return onFiltersChange;
}
const choosePreset = async (name: RegExp) => {
  fireEvent.click(screen.getByRole('combobox', { name: '动效预设' }));
  fireEvent.click(await screen.findByRole('option', { name }));
};

beforeEach(() => {
  library.items = [defaultPreset(), soft()];
  library.error = null;
  useMotionPrefStore.setState({ presetCode: null, override: null, loaded: true, error: '', fetching: false });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('看板属性面板 · 动效分组', () => {
  it('未挂载：显示不挂载 + 来源是系统默认档，不给差量旋钮（没预设可改）', () => {
    panel({});
    expect(screen.getByText('动效（出现效果）')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '动效预设' })).toHaveTextContent('不挂载（跟随观看者偏好）');
    expect(screen.getByText('当前生效：默认（现网）（来源：系统默认档（现网观感））')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: '本看板入场时长滑杆' })).not.toBeInTheDocument();
  });

  it('选一套预设：写入 applyScreenComponent 的形状（引用 + 快照），别的 filters 键不动', async () => {
    const filters = { design: { canvas: { backgroundColor: '#0a1220' } }, layout: 'grid' };
    const onFiltersChange = panel(filters);
    await choosePreset(/柔和舒缓/);
    expect(onFiltersChange).toHaveBeenCalledTimes(1);
    const next = onFiltersChange.mock.calls[0][0];
    expect(next.components.motion).toBe('mp_soft');
    expect(next.design.componentSnapshots.motion).toMatchObject({ category: 'motion_preset', code: 'mp_soft', style_config: SOFT });
    expect(next.design.canvas).toMatchObject({ backgroundColor: '#0a1220' });   // 画布配置不被动效挂载覆盖（只会被补全尺寸）
    expect(next.layout).toBe('grid');
  });

  it('库优先：改字模即时反映到已挂载的看板（不必重挂），来源仍标本看板挂载', () => {
    library.items = [defaultPreset(), soft({ name: '柔和舒缓（已调慢）', style_config: { ...SOFT, enter: { ...SOFT.enter, durationMs: 980 } } })];
    panel(applyScreenComponent({}, soft()));
    expect(screen.getByText(/当前生效：柔和舒缓（已调慢）（来源：本看板挂载）/)).toBeInTheDocument();
    // 摘要读的是库里的新值，不是挂载时的快照值
    expect(screen.getByText('fadeUp · 980ms · 错峰 120ms')).toBeInTheDocument();
  });

  it('字模被禁用：回退挂载时快照并写明原因（不许"挂了没反应"）', () => {
    library.items = [defaultPreset(), soft({ is_enabled: 0 })];
    panel(applyScreenComponent({}, soft()));
    expect(screen.getByRole('status')).toHaveTextContent('动效预设「mp_soft」已下架或禁用，已按挂载时快照呈现');
    expect(screen.getByText('当前生效：挂载时快照（来源：本看板挂载）')).toBeInTheDocument();
    expect(screen.getByText('fadeUp · 700ms · 错峰 120ms')).toBeInTheDocument();   // 快照值仍在呈现
  });

  it('已禁用的预设不进下拉（挂上也不会生效，不给空承诺）', async () => {
    library.items = [defaultPreset(), soft({ is_enabled: 0 }), comp('mp_crisp', '利落短促', DEFAULT_MOTION, { id: 3, sort_order: 83 })];
    panel({});
    fireEvent.click(screen.getByRole('combobox', { name: '动效预设' }));
    await screen.findByRole('option', { name: /利落短促/ });
    expect(screen.queryByRole('option', { name: /柔和舒缓/ })).not.toBeInTheDocument();
  });

  it('取消挂载：引用与快照一起清掉（只删引用会留下"看不见的挂载"）', async () => {
    const mounted = applyScreenComponent({ theme: { backgroundColor: '#fff' } }, soft());
    const onFiltersChange = panel(mounted);
    await choosePreset(/不挂载/);
    const next = onFiltersChange.mock.calls[0][0];
    expect(next.components.motion).toBeUndefined();
    expect(next.design.componentSnapshots.motion).toBeUndefined();
    expect(next.theme).toEqual({ backgroundColor: '#fff' });
  });

  it('板级差量：松手才写 filters，来源仍是本看板挂载，并标注"本看板调整"', async () => {
    const onFiltersChange = panel(applyScreenComponent({}, soft()));
    const group = screen.getByRole('group', { name: '本看板入场时长滑杆' });
    expect(group).toBeInTheDocument();
    fireEvent.keyDown(group.querySelector('[role="slider"]') as HTMLElement, { key: 'ArrowRight' });
    expect(onFiltersChange).toHaveBeenCalledTimes(1);
    const next = onFiltersChange.mock.calls[0][0];
    expect(next.design.motionOverride).toEqual({ enter: { durationMs: 720 } });
    expect(next.components.motion).toBe('mp_soft');   // 差量不碰挂载本身
  });

  it('错峰调到 0 也落库：0 是真值，不能被当成"没改过"而静默丢掉', () => {
    const onFiltersChange = panel(applyScreenComponent({}, soft()));
    const group = screen.getByRole('group', { name: '本看板逐块错峰滑杆' });
    fireEvent.keyDown(group.querySelector('[role="slider"]') as HTMLElement, { key: 'Home' });   // → 最小值 0
    expect(onFiltersChange).toHaveBeenCalledTimes(1);
    expect(onFiltersChange.mock.calls[0][0].design.motionOverride).toEqual({ enter: { staggerMs: 0 } });
    expect(onFiltersChange.mock.calls[0][0].components.motion).toBe('mp_soft');
  });

  it('已有差量时显示"本看板调整"并给一键恢复', () => {
    const filters = { ...applyScreenComponent({}, soft()), design: { motionOverride: { enter: { durationMs: 300 } } } };
    const onFiltersChange = panel(filters);
    expect(screen.getByText(/300ms（本看板调整）/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '恢复预设值' }));
    expect(onFiltersChange.mock.calls[0][0].design.motionOverride).toBeUndefined();
  });

  it('个人预设不参与本看板的判定：挂了字模就只显示板上来源（分面裁决）', () => {
    useMotionPrefStore.setState({ presetCode: 'mp_default' });
    panel(applyScreenComponent({}, soft()));
    expect(screen.getByText(/来源：本看板挂载）$/)).toBeInTheDocument();
    expect(screen.queryByText(/观看者的个人预设/)).not.toBeInTheDocument();
  });

  it('系统减少动态效果：旋钮收起，原因写在明处', () => {
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: /reduced-motion/.test(query), media: query, onchange: null,
      addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
    }) as any);
    panel(applyScreenComponent({}, soft()));
    expect(screen.queryByRole('group', { name: '本看板入场时长滑杆' })).not.toBeInTheDocument();
    expect(screen.getByText('当前生效：无动效（预设「柔和舒缓」被系统「减少动态效果」压制）')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('系统已开启「减少动态效果」');
  });
});
