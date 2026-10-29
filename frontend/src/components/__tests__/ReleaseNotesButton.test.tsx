import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import ReleaseNotesButton from '../ReleaseNotesButton';
import SectionSwitcher from '../SectionSwitcher';
import { TooltipProvider } from '../ui/tooltip';
import client from '@/api/client';
import { noteKey, type ReleaseNote } from '@/api/releaseNotes';
import { GUIDE_ALL_KEY, useGuideStore } from '@/stores/guideStore';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

/**
 * 顶栏公告按钮的口径回归。
 *
 * 盯三件事: 红点数必须等于未读条数(不能"看过一次就永远红着"或"有公告却不提示")、
 * 更新项按 新增/优化/修复 归组而不是照抄后端数组顺序、加载失败必须转告警态而不是
 * 静默显示"还没有已发布的公告"。
 */

const NOTE = (over: Partial<ReleaseNote> = {}): ReleaseNote => ({
  id: 1, version: '1.1.0', title: '新手引导与版本公告', summary: '顶栏多了公告入口',
  sections: [
    { kind: 'feature', items: ['新增新手引导', '新增版本更新公告按钮'] },
    { kind: 'fix', items: ['修复窄屏下引导锚点误判为缺失'] },
    { kind: 'improvement', items: ['优化顶栏入口间距'] },
  ],
  guide_key: 'workspace', is_major: 1, published_at: '2026-01-05 10:00:00', ...over,
});

const putCalls = () => vi.mocked(client.put).mock.calls.map(c => c[1] as { keys: string[]; status: string });

function renderButton() {
  return render(
    <TooltipProvider>
      <MemoryRouter initialEntries={['/']}><ReleaseNotesButton /></MemoryRouter>
    </TooltipProvider>,
  );
}

const button = () => screen.getByRole('button', { name: '版本更新公告' });

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
  vi.mocked(client.get).mockResolvedValue({ data: { items: [NOTE()], states: {} } } as any);
  useAuthStore.setState({ user: { id: 1, username: 'tester', role: 'analyst' } as any });
  usePermissionStore.setState({ loaded: true, unrestricted: true, allowedMenus: null, perms: null });
  useGuideStore.setState({
    notes: [NOTE(), NOTE({ id: 2, version: '1.0.0', title: '首个正式版', guide_key: '', is_major: 0 })],
    states: {}, loading: false, loaded: true, error: '', activeGuide: null, welcomeOpen: false,
  });
});

afterEach(cleanup);

describe('未读红点', () => {
  it('红点数 = 未读公告条数，逐条读完后消失', () => {
    renderButton();
    expect(screen.getByTestId('release-notes-dot')).toHaveTextContent('2');

    cleanup();
    useGuideStore.setState({ states: { [noteKey('1.1.0')]: { status: 'read' } } });
    renderButton();
    expect(screen.getByTestId('release-notes-dot')).toHaveTextContent('1');

    cleanup();
    useGuideStore.setState({
      states: { [noteKey('1.1.0')]: { status: 'read' }, [noteKey('1.0.0')]: { status: 'read' } },
    });
    renderButton();
    expect(screen.queryByTestId('release-notes-dot')).not.toBeInTheDocument();
    // 按钮本体不隐藏: 顶栏常驻入口，已读也要能回看历史公告
    expect(button()).toBeInTheDocument();
  });

  it('排在模块切换左侧(四套 header 同一口径)', () => {
    render(
      <TooltipProvider>
        <MemoryRouter initialEntries={['/']}>
          <ReleaseNotesButton />
          <SectionSwitcher current="workspace" />
        </MemoryRouter>
      </TooltipProvider>,
    );
    const switcher = screen.getByRole('button', { name: '切换模块' });
    // 引导锚点同批到位
    expect(button()).toHaveAttribute('data-tour', 'header-release-notes');
    expect(switcher).toHaveAttribute('data-tour', 'header-module-switcher');
    expect(button().compareDocumentPosition(switcher) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('公告面板', () => {
  it('更新项按 新增/优化/修复 归组，不照抄后端返回顺序', async () => {
    renderButton();
    await userEvent.click(button());
    const [feature, improvement, fix] = ['新增', '优化', '修复'].map(l => screen.getByText(l));
    expect(feature.compareDocumentPosition(improvement) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(improvement.compareDocumentPosition(fix) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText('新增新手引导')).toBeInTheDocument();
    expect(screen.getByText('优化顶栏入口间距')).toBeInTheDocument();
    // 版本清单用 version+标题(业务名)展示，不是裸 id
    expect(screen.getByRole('button', { name: /新手引导与版本公告/ })).toBeInTheDocument();
    expect(screen.getAllByText('v1.1.0').length).toBeGreaterThan(0);
  });

  it('关闭面板即把公告标已读(红点不能赖着不走)', async () => {
    renderButton();
    await userEvent.click(button());
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await waitFor(() => expect(putCalls()).toEqual([
      { keys: [noteKey('1.1.0'), noteKey('1.0.0')], status: 'read' },
    ]));
  });

  it('「全部标记已读」一次覆盖全部已发布版本', async () => {
    renderButton();
    await userEvent.click(button());
    await userEvent.click(await screen.findByRole('button', { name: '全部标记已读' }));
    expect(putCalls()).toEqual([
      { keys: [noteKey('1.1.0'), noteKey('1.0.0')], status: 'read' },
    ]);
  });

  it('关联了引导的版本给直达入口，且即使全局静音也能显式启动', async () => {
    useGuideStore.setState({ states: { [GUIDE_ALL_KEY]: { status: 'skipped' } } });
    renderButton();
    await userEvent.click(button());
    await userEvent.click(await screen.findByRole('button', { name: '查看新功能引导' }));
    expect(useGuideStore.getState().activeGuide).toBe('workspace');
    expect(putCalls()[0]?.status).toBe('read');
  });

  it('没关联引导的版本不给引导按钮', async () => {
    useGuideStore.setState({ notes: [NOTE({ guide_key: '' })] });
    renderButton();
    await userEvent.click(button());
    expect(screen.queryByRole('button', { name: '查看新功能引导' })).not.toBeInTheDocument();
  });
});

describe('加载失败必须可见', () => {
  it('按钮转告警态图标，面板给重试而不是"没有公告"', async () => {
    useGuideStore.setState({ error: '公告列表加载失败', notes: [], loaded: true });
    renderButton();
    // 面板打开后 Radix 会把触发器标 aria-hidden, 所以下面的断言拿同一个节点引用
    const btn = button();
    expect(btn.querySelector('.text-destructive')).not.toBeNull();

    await userEvent.click(btn);
    expect(await screen.findByText(/公告加载失败/)).toBeInTheDocument();
    expect(screen.queryByText('还没有已发布的公告')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '重试' }));
    await waitFor(() => expect(screen.getByText('新增新手引导')).toBeInTheDocument());
    expect(useGuideStore.getState().error).toBe('');
    expect(btn.querySelector('.text-destructive')).toBeNull();
  });
});
