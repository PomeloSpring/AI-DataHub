import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ThemeMenuItems, { THEMES, themeOfPackCode, useThemePackGuard } from '../ThemeMenuItems';
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { useThemeStore } from '@/stores/themeStore';
import { toast } from '@/lib/toast';
import type { VisComponent } from '@/api/visLibrary';

vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: vi.fn() }));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

const pack = (code: string, over: Partial<VisComponent> = {}): VisComponent => ({
  id: 1, code, name: `主题包 ${code}`, category: 'theme_pack', chart_type: null,
  style_config: { mode: 'light', palette: ['#111'] },
  source: 'system', is_builtin: 1, is_active: 1, is_enabled: 1, sort_order: 0, ...over,
} as VisComponent);

/** 九个系统主题都有同名 tp_<theme> 包（与 lib/dashboardDesign 的 themePackFor 同一约定） */
const allPacks = () => THEMES.map(t => pack(`tp_${t.id}`));

function renderMenu(items: VisComponent[], loaded = true) {
  vi.mocked(useVisLibrary).mockReturnValue(
    { items, loading: false, error: null, loaded, refresh: vi.fn() } as any);
  render(
    <DropdownMenu open onOpenChange={() => {}}>
      <DropdownMenuTrigger asChild><button>主题风格</button></DropdownMenuTrigger>
      <DropdownMenuContent forceMount><ThemeMenuItems /></DropdownMenuContent>
    </DropdownMenu>,
  );
}

beforeEach(() => { vi.clearAllMocks(); useThemeStore.setState({ theme: 'light', isDark: false }); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); useThemeStore.setState({ theme: 'light', isDark: false }); });

describe('系统主题菜单跟随 UI 字模库禁用态', () => {
  it('主题包都在用时九个主题都可选，点击即切换', () => {
    renderMenu(allPacks());
    expect(screen.getAllByRole('menuitem')).toHaveLength(THEMES.length);
    fireEvent.click(screen.getByRole('menuitem', { name: /科技风/ }));
    expect(useThemeStore.getState().theme).toBe('tech');
  });

  // 禁用=停用但保留：对应主题从菜单里摘掉 —— 留一个点不动的灰项等于把停用决定做成界面噪声
  it('主题包被禁用 → 对应主题从菜单里消失，其它主题不受影响', () => {
    renderMenu(allPacks().map(c => (c.code === 'tp_tech' ? { ...c, is_enabled: 0 } : c)));
    expect(screen.queryByRole('menuitem', { name: /科技风/ })).toBeNull();
    expect(screen.getAllByRole('menuitem')).toHaveLength(THEMES.length - 1);
    fireEvent.click(screen.getByRole('menuitem', { name: /金融风/ }));
    expect(useThemeStore.getState().theme).toBe('finance');
  });

  it('主题包下架同样从菜单里摘掉（两种不可用同口径）', () => {
    renderMenu(allPacks().map(c => (c.code === 'tp_glass' ? { ...c, is_active: 0 } : c)));
    expect(screen.queryByRole('menuitem', { name: /玻璃拟态/ })).toBeNull();
    expect(screen.getByRole('menuitem', { name: /医疗平台/ })).toBeInTheDocument();
  });

  it('清单未加载/库里没有该包时不改变现状（不得凭空少项）', () => {
    renderMenu(allPacks(), false);
    expect(screen.getAllByRole('menuitem')).toHaveLength(THEMES.length);
    cleanup();
    renderMenu(allPacks().filter(c => c.code !== 'tp_medical'));
    fireEvent.click(screen.getByRole('menuitem', { name: /医疗平台/ }));
    expect(useThemeStore.getState().theme).toBe('medical');
  });

  it('主题包编码可反查系统主题（管理页提示连带影响用）', () => {
    expect(themeOfPackCode('tp_finance')?.label).toBe('金融风');
    expect(themeOfPackCode('tp_as')).toBeUndefined();
  });
});

describe('当前主题的主题包被禁用后的显式收口', () => {
  function GuardProbe() { useThemePackGuard(); return null; }

  it('切到仍可用的主题并把原因提示出来（不静默换皮）', () => {
    useThemeStore.setState({ theme: 'tech' });
    vi.mocked(useVisLibrary).mockReturnValue({
      items: allPacks().map(c => (c.code === 'tp_tech' ? { ...c, is_enabled: 0 } : c)),
      loading: false, error: null, loaded: true, refresh: vi.fn(),
    } as any);
    render(<GuardProbe />);
    expect(useThemeStore.getState().theme).toBe('dark');
    expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining('已在 UI 字模库禁用'),
      expect.objectContaining({ description: expect.stringContaining('重新启用') }));
  });

  it('没有其它可用主题包时报错说明将用默认样式，而不是静默凑一套', () => {
    useThemeStore.setState({ theme: 'tech' });
    vi.mocked(useVisLibrary).mockReturnValue({
      items: allPacks().map(c => ({ ...c, is_enabled: 0 })),
      loading: false, error: null, loaded: true, refresh: vi.fn(),
    } as any);
    render(<GuardProbe />);
    expect(useThemeStore.getState().theme).toBe('tech');
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('图表将使用默认样式'),
      expect.objectContaining({ description: expect.stringContaining('启用至少一套主题包') }));
  });

  it('主题包都在用时不动用户的主题', () => {
    useThemeStore.setState({ theme: 'medical' });
    vi.mocked(useVisLibrary).mockReturnValue({
      items: allPacks(), loading: false, error: null, loaded: true, refresh: vi.fn(),
    } as any);
    render(<GuardProbe />);
    expect(useThemeStore.getState().theme).toBe('medical');
    expect(toast.warning).not.toHaveBeenCalled();
  });
});
