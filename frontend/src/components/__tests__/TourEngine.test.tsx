import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import TourEngine from '../onboarding/TourEngine';
import client from '@/api/client';
import { GUIDE_ALL_KEY, guideKey } from '@/api/releaseNotes';
import { TOUR } from '@/lib/onboarding/guides';
import { useGuideStore } from '@/stores/guideStore';
import { toast } from '@/lib/toast';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

/**
 * 自建 Tour 引擎的分档跳过与"找不到目标不静默"回归。
 *
 * jsdom 里所有元素 rect 都是 0，而引擎把 0 尺寸当作"未渲染"（否则会画一个空气高亮），
 * 所以要给带 data-tour 的锚点桩出真实尺寸，其余元素保持 0。
 */

const RECT = {
  top: 80, left: 40, width: 120, height: 36, bottom: 116, right: 160, x: 40, y: 80, toJSON: () => ({}),
};

const ALL_TARGETS = [
  TOUR.homeOverview, TOUR.homeQuick, TOUR.headerReleaseNotes, TOUR.headerModuleSwitcher, TOUR.headerTheme,
];

function mountFixture(targets: string[] = ALL_TARGETS) {
  document.getElementById('tour-fixture')?.remove();
  const host = document.createElement('div');
  host.id = 'tour-fixture';
  targets.forEach(t => {
    const el = document.createElement('div');
    el.setAttribute('data-tour', t);
    host.appendChild(el);
  });
  document.body.appendChild(host);
}

const putCalls = () => vi.mocked(client.put).mock.calls.map(c => c[1] as { keys: string[]; status: string });

function renderEngine() {
  return render(<MemoryRouter initialEntries={['/']}><TourEngine guideId="home" /></MemoryRouter>);
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
  useGuideStore.setState({ notes: [], states: {}, activeGuide: 'home', welcomeOpen: false, error: '' });
  mountFixture();
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    return (this.getAttribute?.('data-tour') ? { ...RECT } : { ...RECT, width: 0, height: 0, right: 40, bottom: 80 }) as any;
  });
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => window.setTimeout(() => cb(Date.now()), 0));
  vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id));
});

afterEach(() => {
  cleanup();
  mountFixture();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('步骤推进', () => {
  it('下一步/上一步按步序移动，高亮跟随锚点', async () => {
    renderEngine();
    expect(await screen.findByText('数据概览')).toBeInTheDocument();
    expect(await screen.findByText('第 1/5 步')).toBeInTheDocument();
    // 高亮层要等 rAF tick 拿到 rect 才渲染，同步查会漏
    expect(await screen.findByTestId('tour-highlight')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '下一步' }));
    expect(screen.getByText('快捷入口')).toBeInTheDocument();
    expect(screen.getByText('第 2/5 步')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '上一步' }));
    expect(screen.getByText('第 1/5 步')).toBeInTheDocument();
  });

  it('键盘 Enter 前进、← 回退', async () => {
    renderEngine();
    await screen.findByText('数据概览');
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(await screen.findByText('第 2/5 步')).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'ArrowLeft' });
    expect(await screen.findByText('第 1/5 步')).toBeInTheDocument();
  });
});

describe('落库口径', () => {
  it('走完最后一步点完成 → guide:<id>=completed', async () => {
    renderEngine();
    await screen.findByText('数据概览');
    for (let i = 0; i < 4; i++) {
      await userEvent.click(screen.getByRole('button', { name: '下一步' }));
    }
    await userEvent.click(await screen.findByRole('button', { name: '完成' }));
    expect(putCalls()).toEqual([{ keys: [guideKey('home')], status: 'completed' }]);
    expect(useGuideStore.getState().activeGuide).toBeNull();
  });

  it('「跳过本引导」按钮写 skipped 且只落一次库', async () => {
    renderEngine();
    await screen.findByText('数据概览');
    await userEvent.click(screen.getByRole('button', { name: '跳过本引导' }));
    expect(putCalls()).toEqual([{ keys: [guideKey('home')], status: 'skipped' }]);
  });

  it('Esc 等价于跳过本引导', async () => {
    renderEngine();
    await screen.findByText('数据概览');
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(putCalls()).toEqual([{ keys: [guideKey('home')], status: 'skipped' }]));
  });

  it('「不再自动弹出」写全局静音 guide:all 并退出', async () => {
    renderEngine();
    await screen.findByText('数据概览');
    await userEvent.click(screen.getByRole('button', { name: '不再自动弹出' }));
    expect(putCalls()).toEqual([{ keys: [GUIDE_ALL_KEY], status: 'skipped' }]);
    expect(useGuideStore.getState().activeGuide).toBeNull();
  });

  it('「跳过这一步」只推进，不落任何状态', async () => {
    renderEngine();
    await screen.findByText('数据概览');
    await userEvent.click(screen.getByRole('button', { name: '跳过这一步' }));
    expect(screen.getByText('第 2/5 步')).toBeInTheDocument();
    expect(vi.mocked(client.put)).not.toHaveBeenCalled();
  });

  it('写失败时不退出引导（不假装已跳过）', async () => {
    vi.mocked(client.put).mockRejectedValue({ response: { data: { detail: '登录状态已失效' } } });
    renderEngine();
    await screen.findByText('数据概览');
    await userEvent.click(screen.getByRole('button', { name: '跳过本引导' }));
    expect(vi.mocked(toast.error)).toHaveBeenCalledWith(expect.stringContaining('登录状态已失效'), expect.anything());
    expect(screen.getByText('数据概览')).toBeInTheDocument();
    expect(useGuideStore.getState().activeGuide).toBe('home');
  });
});

describe('目标缺失不静默', () => {
  it('锚点找不到时显式写明并自动前进', async () => {
    mountFixture(ALL_TARGETS.filter(t => t !== TOUR.homeOverview));
    renderEngine();
    expect(await screen.findByText(/未找到「数据概览」入口/, undefined, { timeout: 3000 })).toBeInTheDocument();
    // 提示驻留后自动进到下一步(而不是无声跳走)
    expect(await screen.findByText('快捷入口', undefined, { timeout: 3000 })).toBeInTheDocument();
  });

  it('最后一步缺失时中断且不写 completed', async () => {
    mountFixture(ALL_TARGETS.filter(t => t !== TOUR.headerTheme));
    renderEngine();
    await screen.findByText('数据概览');
    for (let i = 0; i < 4; i++) {
      await userEvent.click(screen.getByRole('button', { name: '下一步' }));
    }
    expect(await screen.findByText(/未找到「主题风格」入口/, undefined, { timeout: 3000 })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '完成' }));
    expect(vi.mocked(toast.warning)).toHaveBeenCalledWith(expect.stringContaining('本次引导未走完'));
    expect(putCalls().some(c => c.status === 'completed')).toBe(false);
  });
});
