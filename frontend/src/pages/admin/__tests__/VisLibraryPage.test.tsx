import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_MOTION, MOTION_SECTION_KEYS, type MotionSpec } from '@/lib/motion';
import VisLibrary from '../VisLibrary';
import { useAuthStore } from '@/stores/authStore';
import type { VisComponent } from '@/api/visLibrary';
import client from '@/api/client';

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

// 禁用=停用但保留（与"下架"分档）：管理页必须能停用一个主题包，并把连带影响说清
describe('UI 字模库管理页 — 禁用状态', () => {
  it('禁用主题包前确认影响面（系统主题里对应主题同时不可选）', async () => {
    library.current = [comp({ code: 'tp_tech', name: '科技霓虹', category: 'theme_pack', chart_type: null })];
    render(<VisLibrary />);
    fireEvent.click(await screen.findByRole('button', { name: '禁用字模 科技霓虹' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).toContain('不可被选用');
    expect(dialog.textContent).toContain('「科技风」将从系统主题菜单里移除');
    fireEvent.click(within(dialog).getByRole('button', { name: '禁用' }));
    expect(client.post).toHaveBeenCalledWith('/vis-library/components/1/enabled', { enabled: false });
  });

  it('已禁用的字模带「已禁用」标识并可一键重新启用', async () => {
    library.current = [comp({ code: 'tp_tech', name: '科技霓虹', category: 'theme_pack', chart_type: null, is_enabled: 0 })];
    render(<VisLibrary />);
    const card = (await screen.findByText('科技霓虹')).closest('article')!;
    expect(within(card).getByText('已禁用')).toBeInTheDocument();
    expect(within(card).queryByText('已下架')).toBeNull();
    fireEvent.click(within(card).getByRole('button', { name: '启用字模 科技霓虹' }));
    expect(client.post).toHaveBeenCalledWith('/vis-library/components/1/enabled', { enabled: true });
  });

  it('非管理员不能禁用内置字模（与编辑/下架同一 admin-only 口径）', async () => {
    useAuthStore.setState({ user: { id: 2, username: 'bob', role: 'user' } });
    library.current = [widgetComp()];
    render(<VisLibrary />);
    fireEvent.click(await screen.findByRole('button', { name: '控件样式' }));
    const card = (await screen.findByText('下拉框·AS')).closest('article')!;
    expect(within(card).getByRole('button', { name: '禁用字模 下拉框·AS' })).toBeDisabled();
  });
});

describe('UI 字模库管理页 — 动效预设', () => {
  const motion = (over: Partial<VisComponent> = {}): VisComponent => comp({
    id: 30, code: 'mp_presentation', name: '演示播报', category: 'motion_preset', chart_type: null,
    description: '逐块错峰 + 推入切板', source: 'system', is_builtin: 1, sort_order: 85,
    style_config: { ...DEFAULT_MOTION, transition: { effect: 'pushLeft', durationMs: 550, easing: 'standard' } } as MotionSpec,
    ...over,
  });

  it('新分类进切换条，卡片预览跑真实那套 class（管理页看到的节奏即渲染面的节奏）', async () => {
    library.current = [motion()];
    render(<VisLibrary />);
    const nav = screen.getByRole('navigation', { name: '字模分类' });
    fireEvent.click(within(nav).getByRole('button', { name: '动效预设' }));
    const card = (await screen.findByText('演示播报')).closest('article')!;
    expect(within(card).getByText(/切板 pushLeft/)).toBeInTheDocument();
    // 预览块用的就是 globals.css 里那套 utility，不是另画一张示意图
    expect(card.querySelector('.motion-enter.motion-enter-fadeUp')).not.toBeNull();
    expect(card.querySelector('[style*="--motion-enter-dur: 450ms"]')).not.toBeNull();
  });

  it('结构化旋钮改一笔就写回 JSON，且只落白名单里的键', async () => {
    library.current = [motion()];
    render(<VisLibrary />);
    fireEvent.click(await screen.findByRole('button', { name: '动效预设' }));
    const card = (await screen.findByText('演示播报')).closest('article')!;
    fireEvent.click(within(card).getByRole('button', { name: '编辑' }));
    const dialog = await screen.findByRole('dialog', {}, { timeout: 4000 });
    expect(within(dialog).getAllByText('卡片入场').length).toBeGreaterThan(0);   // 旋钮分组 + 预览小标同名
    fireEvent.click(within(dialog).getByRole('combobox', { name: '卡片入场effect' }));
    fireEvent.click(await screen.findByRole('option', { name: '擦除展开' }, { timeout: 4000 }));
    const json = JSON.parse((dialog.querySelector('textarea') as HTMLTextAreaElement).value);
    expect(json.enter.effect).toBe('wipeDown');
    // 段与键必须还是那四段白名单（多一个键就是渲染面读不到的"存了不生效"）
    expect(Object.keys(json).sort()).toEqual(['chart', 'enter', 'reveal', 'transition']);
    for (const section of Object.keys(MOTION_SECTION_KEYS) as (keyof typeof MOTION_SECTION_KEYS)[]) {
      expect(Object.keys(json[section]).sort()).toEqual([...MOTION_SECTION_KEYS[section]].sort());
    }
  });

  it('悬空效果名在写入口就标红，保存被拒（不许留到渲染面"按默认呈现"）', async () => {
    library.current = [motion({ style_config: { ...DEFAULT_MOTION, enter: { ...DEFAULT_MOTION.enter, effect: 'zoomSlide' as never } } })];
    render(<VisLibrary />);
    fireEvent.click(await screen.findByRole('button', { name: '动效预设' }));
    const card = (await screen.findByText('演示播报')).closest('article')!;
    fireEvent.click(within(card).getByRole('button', { name: '编辑' }));
    const dialog = await screen.findByRole('dialog', {}, { timeout: 4000 });
    expect(within(dialog).getByRole('alert')).toHaveTextContent('enter.effect="zoomSlide" 不是可选效果');
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(client.put).not.toHaveBeenCalled();
    expect(client.post).not.toHaveBeenCalled();
  });

  it('切到动效预设类别时给一份完整四段模板（不让新建项带着配色键一上来就非法）', async () => {
    library.current = [motion()];
    render(<VisLibrary />);
    fireEvent.click(await screen.findByRole('button', { name: '新建字模' }));
    const dialog = await screen.findByRole('dialog', {}, { timeout: 4000 });
    // 表单里首个 combobox = 「类别」（动效旋钮只有选了动效预设才会出现）
    fireEvent.click(dialog.querySelectorAll('[role="combobox"]')[0]);
    fireEvent.click(await screen.findByRole('option', { name: '动效预设' }, { timeout: 4000 }));
    const json = JSON.parse((dialog.querySelector('textarea') as HTMLTextAreaElement).value);
    expect(Object.keys(json).sort()).toEqual(['chart', 'enter', 'reveal', 'transition']);
    expect(within(dialog).queryByRole('alert')).toBeNull();
  });
});
