import { useRef } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DashboardAutoRefresh from '../DashboardAutoRefresh';
import { TooltipProvider } from '../ui/tooltip';
import { useDashboardFullscreen } from '@/hooks/useDashboardFullscreen';

beforeEach(() => { vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] }); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
const openMenu = () => fireEvent.keyDown(screen.getByRole('button', { name: '自动刷新与更多操作' }), { key: 'Enter' });

describe('紧凑刷新操作', () => {
  it('异步请求未结束不重入，失败不显示成功时间，重试成功才更新', async () => {
    let finish!: (value: boolean) => void;
    const refresh = vi.fn(() => new Promise<boolean>(resolve => { finish = resolve; }));
    render(<TooltipProvider><DashboardAutoRefresh onRefresh={refresh} /></TooltipProvider>);
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    expect(refresh).toHaveBeenCalledTimes(1);
    await act(async () => finish(false));
    expect(screen.getByRole('button', { name: '刷新失败，重试' })).toBeEnabled();
    openMenu();
    expect(screen.queryByText(/最近成功刷新/)).not.toBeInTheDocument();
    expect(screen.getByText('部分图表刷新失败，请重试')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: '刷新失败，重试' }));
    await act(async () => finish(true));
    openMenu(); expect(screen.getByText(/最近成功刷新/)).toBeInTheDocument();
  });
  it('自动刷新防重入、暂停生效、卸载释放计时器', async () => {
    let finish!: (value: boolean) => void;
    const refresh = vi.fn(() => new Promise<boolean>(resolve => { finish = resolve; }));
    const view = render(<TooltipProvider><DashboardAutoRefresh onRefresh={refresh} /></TooltipProvider>);
    openMenu(); fireEvent.change(screen.getByLabelText('自动刷新间隔'), { target: { value: '5' } });
    await act(async () => { vi.advanceTimersByTime(16000); });
    expect(refresh).toHaveBeenCalledTimes(1);
    await act(async () => finish(true));
    fireEvent.click(screen.getByRole('menuitem', { name: '暂停自动刷新' }));
    await act(async () => { vi.advanceTimersByTime(15000); });
    expect(refresh).toHaveBeenCalledTimes(1);
    view.unmount();
    await act(async () => { vi.advanceTimersByTime(30000); });
    expect(refresh).toHaveBeenCalledTimes(1);
  });
  it('全屏容器异步挂载后仍能追踪状态，卸载只退出自己的全屏', async () => {
    let toggle!: () => Promise<void>;
    function Harness({ ready }: { ready: boolean }) {
      const ref = useRef<HTMLDivElement>(null);
      const state = useDashboardFullscreen(ref); toggle = state.toggleFullscreen;
      return ready ? <div ref={ref} data-testid="root">{state.fullscreen ? '全屏' : '普通'}</div> : null;
    }
    const view = render(<Harness ready={false} />);
    view.rerender(<Harness ready />);
    const element = screen.getByTestId('root');
    Object.defineProperty(element, 'requestFullscreen', { configurable: true, value: vi.fn(async () => {
      Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: element });
      document.dispatchEvent(new Event('fullscreenchange'));
    }) });
    const exit = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: exit });
    await act(async () => toggle());
    expect(screen.getByTestId('root')).toHaveTextContent('全屏');
    view.unmount(); expect(exit).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null });
  });
});
