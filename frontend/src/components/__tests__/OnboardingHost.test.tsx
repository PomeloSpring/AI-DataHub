import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import OnboardingHost from '../onboarding/OnboardingHost';
import client from '@/api/client';
import {
  GUIDE_ALL_KEY, GUIDE_WELCOME_KEY, noteKey,
  type GuideStateItem, type ReleaseNote,
} from '@/api/releaseNotes';
import { useGuideStore } from '@/stores/guideStore';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

/**
 * 自动弹出的判定口径（宿主只做"该不该弹"这一件事）。
 *
 * 三条红线: 权限没就绪就先去服务端取状态会拿到空清单 → 误判成"没公告可弹"；
 * 静音标记只能压制自动弹，压不住手动入口；登录/大屏等消费态页面不盖向导。
 */

const NOTE = (over: Partial<ReleaseNote> = {}): ReleaseNote => ({
  id: 1, version: '1.1.0', title: '新手引导与版本公告', summary: '',
  sections: [{ kind: 'feature', items: ['新增新手引导'] }],
  guide_key: 'workspace', is_major: 1, ...over,
});

/** 按 URL 分流桩数据: /release-notes 回公告, /release-notes/state 回本人状态 */
function backend(notes: ReleaseNote[], states: Record<string, GuideStateItem> = {}) {
  vi.mocked(client.get).mockImplementation(async (url: string) => (
    url === '/release-notes'
      ? { data: { items: notes, latest_version: notes[0]?.version || '' } }
      : { data: { states } }
  ) as any);
}

const read = () =>
  waitFor(() => expect(useGuideStore.getState().loaded).toBe(true));

function renderHost(path = '/') {
  return render(<MemoryRouter initialEntries={[path]}><OnboardingHost /></MemoryRouter>);
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as never);
  backend([], {});
  useAuthStore.setState({ token: 'fake-token', user: { id: 1, username: 'tester', role: 'analyst' } as never });
  usePermissionStore.setState({ loaded: true, unrestricted: true, allowedMenus: null, perms: null });
  useGuideStore.setState({
    notes: [], states: {}, loading: false, loaded: false, error: '', activeGuide: null, welcomeOpen: false,
  });
});

afterEach(cleanup);

describe('取数时机', () => {
  it('权限未就绪时不去服务端取状态（否则会按空清单误判成无需引导）', async () => {
    usePermissionStore.setState({ loaded: false });
    renderHost();
    await new Promise(r => setTimeout(r, 10));
    expect(vi.mocked(client.get)).not.toHaveBeenCalled();
    expect(useGuideStore.getState().welcomeOpen).toBe(false);

    act(() => { usePermissionStore.setState({ loaded: true }); });
    await read();
    expect(vi.mocked(client.get).mock.calls.map(c => c[0])).toEqual(['/release-notes', '/release-notes/state']);
  });
});

describe('首次登录', () => {
  it('没有 guide:welcome 就自动弹欢迎总览', async () => {
    backend([NOTE()], {});
    renderHost('/ws/7/chat');
    expect(await screen.findByText('欢迎使用 AI-DataHub')).toBeInTheDocument();
  });

  it('欢迎已处理且无未读重大公告 → 不弹，但公告红点口径不变', async () => {
    backend([NOTE({ is_major: 0 })], { [GUIDE_WELCOME_KEY]: { status: 'skipped' } });
    renderHost('/ws/7/chat');
    await read();
    expect(screen.queryByText('欢迎使用 AI-DataHub')).not.toBeInTheDocument();
    expect(useGuideStore.getState().welcomeOpen).toBe(false);
    expect(useGuideStore.getState().states[noteKey('1.1.0')]).toBeUndefined();
  });

  it('一次登录只骚扰一遍：关掉后不因状态回流再弹', async () => {
    backend([], {});
    renderHost('/');
    await screen.findByText('欢迎使用 AI-DataHub');
    act(() => { useGuideStore.getState().closeWelcome(); });
    await new Promise(r => setTimeout(r, 20));
    // selectAutoPrompt 仍是 "first"(状态没落库), 但本次会话不能再弹第二遍
    expect(useGuideStore.getState().welcomeOpen).toBe(false);
  });
});

describe('升级重触发', () => {
  it('有未读重大公告时即使欢迎已处理也再弹，并给「查看新功能引导」', async () => {
    backend([NOTE()], { [GUIDE_WELCOME_KEY]: { status: 'completed' } });
    renderHost('/ws/7/chat');
    expect(await screen.findByText('系统已更新，这些变化值得关注')).toBeInTheDocument();
    expect(screen.getByText(/新增新手引导/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看新功能引导' })).toBeInTheDocument();
  });

  it('重大公告已读过 → 不再按升级弹（否则每次登录都弹一遍）', async () => {
    backend([NOTE()], { [GUIDE_WELCOME_KEY]: { status: 'completed' }, [noteKey('1.1.0')]: { status: 'read' } });
    renderHost('/ws/7/chat');
    await read();
    expect(screen.queryByText('系统已更新，这些变化值得关注')).not.toBeInTheDocument();
  });
});

describe('全局静音(guide:all)', () => {
  it('静音后任何情况都不自动弹，但手动入口照样能启动引导', async () => {
    backend([NOTE()], { [GUIDE_ALL_KEY]: { status: 'skipped' }, [GUIDE_WELCOME_KEY]: { status: 'skipped' } });
    renderHost('/ws/7/chat');
    await read();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // 用户菜单「新手引导」/公告面板的显式动作绕过静音
    act(() => { useGuideStore.getState().startGuide('workspace'); });
    expect(await screen.findByTestId('tour-engine')).toBeInTheDocument();
  });

  it('静音状态下点欢迎总览的手动入口仍然打开弹窗', async () => {
    backend([], { [GUIDE_ALL_KEY]: { status: 'skipped' } });
    renderHost('/');
    await read();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    act(() => { useGuideStore.getState().startGuide('welcome'); });
    expect(await screen.findByText('欢迎使用 AI-DataHub')).toBeInTheDocument();
  });
});

describe('不该盖向导的页面', () => {
  it('分享报告/大屏等消费态路由不自动弹', async () => {
    backend([], {});
    renderHost('/report/abc123');
    await read();
    await new Promise(r => setTimeout(r, 20));
    expect(screen.queryByText('欢迎使用 AI-DataHub')).not.toBeInTheDocument();
  });
});

describe('模块卡片按权限过滤', () => {
  it('只有数据中台权限时不给工作空间与系统配置入口', async () => {
    usePermissionStore.setState({
      loaded: true, unrestricted: false, allowedMenus: ['data:datasources', 'data:metrics'], perms: null,
    });
    backend([], {});
    renderHost('/data/datasources');
    expect(await screen.findByText('数据中台（建模与治理）')).toBeInTheDocument();
    expect(screen.queryByText('工作空间（日常用数）')).not.toBeInTheDocument();
    expect(screen.queryByText('系统配置（管理员）')).not.toBeInTheDocument();
    expect(screen.getByText('首页总览')).toBeInTheDocument();
  });
});
