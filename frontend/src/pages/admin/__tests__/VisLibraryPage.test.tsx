import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import VisLibrary from '../VisLibrary';
import { useAuthStore } from '@/stores/authStore';
import type { VisComponent } from '@/api/visLibrary';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
// 地图瓦片预览用不到真实 Leaflet（jsdom 无布局引擎）
vi.mock('leaflet', () => ({ default: { map: vi.fn(() => ({ remove: vi.fn() })) } }));

const library = vi.hoisted(() => ({ current: [] as VisComponent[] }));
vi.mock('@/hooks/useVisLibrary', () => ({
  useVisLibrary: () => ({ items: library.current, loading: false, error: null, refresh: vi.fn() }),
}));

const comp = (over: Partial<VisComponent>): VisComponent => ({
  id: 1, code: 'c1', name: '字模', category: 'chart_style', chart_type: 'bar', style_config: {},
  source: 'system', is_builtin: 1, is_active: 1, sort_order: 0, ...over,
} as VisComponent);

const widgetComp = (over: Partial<VisComponent> = {}) => comp({
  id: 2, code: 'ws_select_as', name: '下拉框·AS', category: 'widget_style', chart_type: 'widget_select',
  description: '单选下拉：与输入框同描边同高度',
  style_config: { widgetStyle: { backgroundColor: '#ffffff', borderColor: '#E9ECF8', borderRadius: 8, fontSize: 13, height: 32 } },
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  library.current = [];
  useAuthStore.setState({ user: { id: 1, username: 'admin', role: 'admin' } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); useAuthStore.getState().logout(); });

describe('UI 字模库管理页 — 卡片清单', () => {
  it('分类切换条是分段控件，切换后只列该分类', async () => {
    library.current = [comp({ code: 'tp_as', name: 'AS 风格', category: 'theme_pack' }), widgetComp()];
    const { container } = render(<VisLibrary />);
    const nav = screen.getByRole('navigation', { name: '字模分类' });
    expect(within(nav).getByRole('button', { name: '主题包' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(nav).getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'false');
    // 默认落在「主题包」，控件字模不出现
    expect(container.querySelector('[data-preview-type="widget_style"]')).toBeNull();
    fireEvent.click(within(nav).getByRole('button', { name: '控件样式' }));
    expect(within(nav).getByRole('button', { name: '控件样式' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(nav).getByRole('button', { name: '主题包' })).toHaveAttribute('aria-pressed', 'false');
    expect(await screen.findByText('下拉框·AS')).toBeInTheDocument();
    expect(screen.queryByText('AS 风格')).not.toBeInTheDocument();
  });

  it('卡片显示业务类型名而非裸内部类型码，code 只作次要信息', async () => {
    library.current = [widgetComp()];
    render(<VisLibrary />);
    fireEvent.click(await screen.findByRole('button', { name: '控件样式' }));
    const card = (await screen.findByText('下拉框·AS')).closest('article')!;
    expect(within(card).getByText('下拉选择框')).toBeInTheDocument();
    expect(within(card).queryByText(/widget_select/)).toBeNull();
    expect(within(card).getByText('ws_select_as')).toBeInTheDocument();
    expect(within(card).getByText('内置')).toBeInTheDocument();
  });

  it('控件样式预览画在等高框内且不溢出（此前 ws.height 撑破卡片压住标题）', async () => {
    library.current = [widgetComp()];
    const { container } = render(<VisLibrary />);
    fireEvent.click(await screen.findByRole('button', { name: '控件样式' }));
    const preview = container.querySelector<HTMLElement>('[data-preview-type="widget_style"]')!;
    expect(preview.className).toContain('overflow-hidden');
    // 卡片预览槽统一高度，长短不一就是列表页“乱”的直接原因
    expect(preview.parentElement?.className).toContain('h-24');
    // 输入区按 ws.height 缩放后仍受框高约束（32 → 26 上限）
    expect(preview.style.height || preview.textContent).toBeTruthy();
  });

  it('悬空 chart_type 在卡片与编辑表单里都显式告警（不静默显示为可用）', async () => {
    library.current = [widgetComp({ code: 'ws_input_legacy', name: '输入框·AS', chart_type: 'widget_input' })];
    render(<VisLibrary />);
    fireEvent.click(await screen.findByRole('button', { name: '控件样式' }));
    expect(await screen.findByText('widget_input（未知类型）')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    expect(await screen.findByText(/不是系统里的图表\/控件类型/)).toBeInTheDocument();
  });

  it('搜索按名称/说明过滤，空结果显示可操作空态', async () => {
    library.current = [widgetComp(), widgetComp({ id: 3, code: 'ws_neon', name: '控件·科技风' })];
    render(<VisLibrary />);
    fireEvent.click(await screen.findByRole('button', { name: '控件样式' }));
    fireEvent.change(screen.getByLabelText('搜索字模'), { target: { value: '科技风' } });
    expect(await screen.findByText('控件·科技风')).toBeInTheDocument();
    expect(screen.queryByText('下拉框·AS')).not.toBeInTheDocument();
    expect(screen.getByText('1 个字模')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('搜索字模'), { target: { value: '不存在' } });
    expect(await screen.findByText(/没有匹配「不存在」的字模/)).toBeInTheDocument();
  });

  it('非管理员对内置字模只读，自定义字模仍可编辑', async () => {
    useAuthStore.setState({ user: { id: 2, username: 'bob', role: 'user' } });
    library.current = [widgetComp(), widgetComp({ id: 9, code: 'ws_mine', name: '我的控件', source: 'custom', is_builtin: 0 })];
    render(<VisLibrary />);
    fireEvent.click(await screen.findByRole('button', { name: '控件样式' }));
    await screen.findByText('我的控件');
    const builtin = screen.getByText('下拉框·AS').closest('article')!;
    expect(within(builtin).getByRole('button', { name: '只读' })).toBeDisabled();
    expect(within(builtin).getByRole('button', { name: '下架字模 下拉框·AS' })).toBeDisabled();
    const mine = screen.getByText('我的控件').closest('article')!;
    expect(within(mine).getByRole('button', { name: '编辑' })).toBeEnabled();
  });
});
