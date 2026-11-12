import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import WelcomeDialog from '../onboarding/WelcomeDialog';
import client from '@/api/client';
import { GUIDE_ALL_KEY, GUIDE_WELCOME_KEY, noteKey, type ReleaseNote } from '@/api/releaseNotes';
import { useGuideStore } from '@/stores/guideStore';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';
import { toast } from '@/lib/toast';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

/**
 * 欢迎总览的三档动作回归。
 *
 * 盯的是"跳过分层"不能串档: 稍后再说只处理欢迎总览; 跳过全部引导才写全局静音;
 * 而静音之前必须把本次升级公告标已读, 否则红点与升级提示会同时赖着不走。
 * 另一条红线: **只讲当前页面** —— 不在本页的引导内容（包括公告关联的跳页 guide_key）
 * 一律不进弹窗，因为锚点不在 DOM 里，播了就是点到空气。
 */

const NOTE = (over: Partial<ReleaseNote> = {}): ReleaseNote => ({
  id: 1, version: '1.1.0', title: '新手引导与版本公告', summary: '',
  sections: [{ kind: 'feature', items: ['新增新手引导'] }],
  guide_key: 'workspace', is_major: 1, ...over,
});

function renderDialog(upgradeNotes: ReleaseNote[] = [], path = '/ws/7/chat') {
  const onClose = vi.fn();
  render(
    <MemoryRouter initialEntries={[path]}>
      <WelcomeDialog open upgradeNotes={upgradeNotes} onClose={onClose} />
    </MemoryRouter>,
  );
  return onClose;
}

const putCalls = () => vi.mocked(client.put).mock.calls.map(c => c[1] as { keys: string[]; status: string });

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
  useAuthStore.setState({ user: { id: 1, username: 'tester', role: 'analyst' } as any });
  usePermissionStore.setState({ loaded: true, unrestricted: true, allowedMenus: null, perms: null });
  useGuideStore.setState({ notes: [NOTE()], states: {}, activeGuide: null, welcomeOpen: false, error: '' });
});
afterEach(cleanup);

describe('稍后再说', () => {
  it('只写 guide:welcome，不碰全局静音', async () => {
    const onClose = renderDialog();
    await userEvent.click(await screen.findByRole('button', { name: '稍后再说' }));
    expect(putCalls()).toEqual([{ keys: [GUIDE_WELCOME_KEY], status: 'skipped' }]);
    expect(onClose).toHaveBeenCalled();
  });

  it('升级场景下顺带把本次重大公告标已读(红点必须清)', async () => {
    renderDialog([NOTE()]);
    await userEvent.click(await screen.findByRole('button', { name: '稍后再说' }));
    expect(putCalls()).toEqual([
      { keys: [noteKey('1.1.0')], status: 'read' },
      { keys: [GUIDE_WELCOME_KEY], status: 'skipped' },
    ]);
  });
});

describe('跳过全部引导', () => {
  it('同时写 guide:welcome + guide:all + 未读重大公告已读', async () => {
    const onClose = renderDialog([NOTE()]);
    await userEvent.click(await screen.findByRole('button', { name: /跳过全部引导/ }));
    expect(putCalls()).toEqual([
      { keys: [noteKey('1.1.0')], status: 'read' },
      { keys: [GUIDE_WELCOME_KEY, GUIDE_ALL_KEY], status: 'skipped' },
    ]);
    expect(onClose).toHaveBeenCalled();
  });

  it('状态写失败时保留弹窗并显式报错，不假装已跳过', async () => {
    vi.mocked(client.put).mockRejectedValue({ response: { data: { detail: '服务不可用' } } });
    const onClose = renderDialog();
    await userEvent.click(await screen.findByRole('button', { name: /跳过全部引导/ }));
    expect(vi.mocked(toast.error)).toHaveBeenCalledWith(expect.stringContaining('服务不可用'), expect.anything());
    expect(screen.getByText('欢迎使用 AI-DataHub')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    // 本地镜像也没有被污染: 静音标记只来自服务端
    expect(useGuideStore.getState().states[GUIDE_ALL_KEY]).toBeUndefined();
  });
});

describe('开始引导', () => {
  it('按当前所在模块启动对应引导', async () => {
    renderDialog();
    await userEvent.click(await screen.findByRole('button', { name: /开始引导/ }));
    expect(useGuideStore.getState().activeGuide).toBe('workspace');
  });

  it('公告关联的引导就在本页时给「查看新功能引导」直达入口', async () => {
    renderDialog([NOTE({ guide_key: 'data' })], '/data/datasources');
    await userEvent.click(await screen.findByRole('button', { name: '查看新功能引导' }));
    expect(useGuideStore.getState().activeGuide).toBe('data');
  });

  it('公告关联的引导在别的页面时不给直达入口（点了只能高亮空气）', async () => {
    renderDialog([NOTE({ guide_key: 'data' })], '/ws/7/chat');
    await screen.findByText('系统已更新，这些变化值得关注');
    expect(screen.queryByRole('button', { name: '查看新功能引导' })).not.toBeInTheDocument();
    // 公告本身照弹，只是不拿跳页引导充数
    expect(screen.getByText(/新增新手引导/)).toBeInTheDocument();
  });

  it('guide_key=welcome 指的是本页自身，不再重复列一个按钮', async () => {
    renderDialog([NOTE({ guide_key: 'welcome' })]);
    expect(screen.queryByRole('button', { name: '查看新功能引导' })).not.toBeInTheDocument();
  });
});

describe('只讲当前页面', () => {
  it('在首页只给首页总览卡片，不列其他模块', async () => {
    renderDialog([], '/');
    expect(await screen.findByText('首页总览')).toBeInTheDocument();
    expect(screen.queryByText('工作空间（日常用数）')).not.toBeInTheDocument();
    expect(screen.queryByText('数据中台（建模与治理）')).not.toBeInTheDocument();
    expect(screen.queryByText('系统配置（管理员）')).not.toBeInTheDocument();
  });

  it('在工作空间页面只给工作空间卡片，不再列首页总览', async () => {
    renderDialog();
    expect(await screen.findByText('工作空间（日常用数）')).toBeInTheDocument();
    expect(screen.queryByText('首页总览')).not.toBeInTheDocument();
  });

  it('当前页面没有引导内容时一张卡都不画、开始引导不可点', async () => {
    renderDialog([], '/dashboards');
    await screen.findByText('欢迎使用 AI-DataHub');
    expect(screen.queryByText('首页总览')).not.toBeInTheDocument();
    expect(screen.queryByText('工作空间（日常用数）')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /开始引导/ })).toBeDisabled();
  });
});

describe('模块卡片按权限显隐', () => {
  it('只有数据中台权限时，在工作空间页面不给任何卡片', async () => {
    usePermissionStore.setState({
      loaded: true, unrestricted: false, allowedMenus: ['data:datasources'], perms: null,
    });
    renderDialog();
    await screen.findByText('欢迎使用 AI-DataHub');
    expect(screen.queryByText('工作空间（日常用数）')).not.toBeInTheDocument();
    expect(screen.queryByText('数据中台（建模与治理）')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /开始引导/ })).toBeDisabled();
  });

  it('有数据中台权限时在本页只给数据中台卡片（首页总览不越页出现）', async () => {
    usePermissionStore.setState({
      loaded: true, unrestricted: false, allowedMenus: ['data:datasources'], perms: null,
    });
    renderDialog([], '/data/datasources');
    expect(await screen.findByText('数据中台（建模与治理）')).toBeInTheDocument();
    expect(screen.queryByText('首页总览')).not.toBeInTheDocument();
  });

  it('非 admin 在系统配置页面不给系统配置卡片', async () => {
    renderDialog([], '/system/models');
    await screen.findByText('欢迎使用 AI-DataHub');
    expect(screen.queryByText('系统配置（管理员）')).not.toBeInTheDocument();
  });

  it('admin 在系统配置页面可见系统配置卡片', async () => {
    useAuthStore.setState({ user: { id: 2, username: 'root', role: 'admin' } as any });
    renderDialog([], '/system/models');
    expect(await screen.findByText('系统配置（管理员）')).toBeInTheDocument();
  });

  it('权限清单未就绪时一张卡都不画（防无权限入口闪现）', () => {
    usePermissionStore.setState({ loaded: false, unrestricted: false, allowedMenus: null, perms: null });
    renderDialog();
    expect(screen.queryByText('工作空间（日常用数）')).not.toBeInTheDocument();
  });
});
