import type { ComponentProps } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DashboardAutoRefresh, { type DashboardRefreshConfig } from '../DashboardAutoRefresh';
import { TooltipProvider } from '../ui/tooltip';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn() } }));

const config: DashboardRefreshConfig = {
  dashboard_id: 10,
  schedule_type: 'interval',
  interval_seconds: 60,
  cron_expression: null,
  timezone: 'Asia/Shanghai',
  cache_ttl_seconds: 60,
  is_active: true,
  version: 1,
  last_run_at: null,
  last_status: null,
  run_count: 0,
};

const openMenu = () => fireEvent.keyDown(screen.getByRole('button', { name: '自动刷新与更多操作' }), { key: 'Enter' });
const renderControl = (props: Partial<ComponentProps<typeof DashboardAutoRefresh>> = {}) =>
  render(<TooltipProvider><DashboardAutoRefresh dashboardId={10} refreshManageable config={config} {...props} /></TooltipProvider>);

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => cleanup());

describe('服务端看板刷新控制', () => {
  it('无管理权限不渲染整板刷新，但保留更多操作菜单', () => {
    renderControl({ refreshManageable: false, menuItems: <div>大屏切板</div> });
    expect(screen.queryByRole('button', { name: '刷新数据' })).not.toBeInTheDocument();
    openMenu();
    expect(screen.getByText('大屏切板')).toBeInTheDocument();
  });

  it('固定周期移除 5/10 秒，包含 1/7/30 天与 Cron', () => {
    renderControl();
    openMenu();
    expect(screen.queryByText('每 5秒')).not.toBeInTheDocument();
    expect(screen.queryByText('每 10秒')).not.toBeInTheDocument();
    expect(screen.getByText('每 1天')).toBeInTheDocument();
    expect(screen.getByText('每 7天')).toBeInTheDocument();
    expect(screen.getByText('每 30天')).toBeInTheDocument();
    expect(screen.getByText('自定义 Cron')).toBeInTheDocument();
  });

  it('保存 Cron 周期时提交服务端配置接口', async () => {
    vi.mocked(client.put).mockResolvedValue({ data: { ...config, schedule_type: 'cron', cron_expression: '0 9 * * *', is_active: true } });
    renderControl();
    openMenu();
    fireEvent.change(screen.getByLabelText('刷新周期类型'), { target: { value: 'cron' } });
    fireEvent.change(screen.getByPlaceholderText('例如：*/5 * * * *'), { target: { value: '0 9 * * *' } });
    fireEvent.click(screen.getByRole('button', { name: '保存周期' }));
    await waitFor(() => expect(client.put).toHaveBeenCalledWith('/dashboard/10/refresh-config', expect.objectContaining({
      schedule_type: 'cron', cron_expression: '0 9 * * *', cache_ttl_seconds: 60,
    })));
  });

  it('TTL 命中只提示缓存，不轮询任务', async () => {
    vi.mocked(client.post).mockResolvedValue({ data: { run_id: null, status: 'cache_hit', cache_hit: true } });
    renderControl();
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/dashboard/10/refresh'));
    expect(client.get).not.toHaveBeenCalled();
  });

  it('手动刷新按 run_id 轮询，成功后回读看板缓存', async () => {
    vi.mocked(client.post).mockResolvedValue({ data: { run_id: 1, status: 'queued' } });
    vi.mocked(client.get).mockResolvedValue({ data: { run_id: 1, status: 'success' } });
    const onCompleted = vi.fn();
    renderControl({ onCompleted });
    fireEvent.click(screen.getByRole('button', { name: '刷新数据' }));
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/dashboard/10/refresh-runs/1'));
    await waitFor(() => expect(onCompleted).toHaveBeenCalledTimes(1));
  });
});
