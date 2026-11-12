// 磁盘配额的"显示值 = 生效值"回归（2026-11 存储治理 P0-2）。
//
// 历史缺陷是双口径：拦截门把"无配额登记行"读成 0=不限制，而管理员页 COALESCE 显示 5GB，
// 资产页显示"未设配额"—— 页面写着有配额、实际从不拦（配了没用）。后端已收口到唯一裁决点
// （role_service.get_user_workspace_quota）并下发 quota_is_default，本组用例锁前端把
// "系统缺省 / 显式配置 / 不设限"三种语义显示得可区分，不得再出现"未设配额"这种含糊文案。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import client from '@/api/client';
import WorkspaceManagerV2 from '../WorkspaceManagerV2';
import WorkspaceAssets from '../WorkspaceAssets';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const MB = 1024 ** 2;
const DEFAULT_BYTES = 500 * MB;

const QUOTAS = [
  // 无登记行 → 后端下发系统缺省 + quota_is_default
  { user_id: 1, username: 'zhangsan', max_workspaces: 5, disk_quota_bytes: DEFAULT_BYTES,
    quota_is_default: true, workspaces: [{ id: 301, name: '默认空间', is_default: 1, disk_usage_bytes: 4 * MB }] },
  // 管理员显式配置 → 不标缺省
  { user_id: 2, username: 'lisi', max_workspaces: 7, disk_quota_bytes: 88 * MB,
    quota_is_default: false, workspaces: [] },
];

beforeEach(() => {
  vi.mocked(client.get).mockImplementation(async (url: any) => {
    if (String(url).startsWith('/workspaces/quotas')) return { data: QUOTAS } as any;
    if (String(url).startsWith('/assets')) return { data: [] } as any;
    if (String(url).includes('/disk-status')) {
      return { data: { workspace_id: 301, usage_bytes: 4 * MB, quota_bytes: DEFAULT_BYTES,
                       quota_is_default: true, sessions: [] } } as any;
    }
    return { data: {} } as any;
  });
});
afterEach(() => cleanup());

describe('管理员页 · 用户工作空间统管', () => {
  it('缺省配额显示实际生效值并标"系统缺省"，显式配置不标', async () => {
    render(<MemoryRouter><WorkspaceManagerV2 /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('zhangsan')).toBeInTheDocument());

    expect(screen.getByText(/每空间 500 MB（系统缺省）/)).toBeInTheDocument();
    expect(screen.getByText(/每空间 88 MB/)).toBeInTheDocument();
    expect(screen.queryByText(/88 MB（系统缺省）/)).not.toBeInTheDocument();
  });
});

describe('用户侧 · 资产清单磁盘用量条', () => {
  it('配额来自后端生效值并标缺省；不得再出现含糊的"未设配额"', async () => {
    render(
      <MemoryRouter initialEntries={['/workspace-assets/301']}>
        <Routes>
          <Route path="/workspace-assets/:workspaceId" element={<WorkspaceAssets />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('工作空间磁盘用量')).toBeInTheDocument());

    expect(screen.getByText(/4\.0 MB \/ 500 MB（系统缺省）/)).toBeInTheDocument();
    expect(screen.queryByText('未设配额')).not.toBeInTheDocument();
  });

  it('显式"不设限"（行值 0）显示"不设限"而不是把 0 当缺省', async () => {
    vi.mocked(client.get).mockImplementation(async (url: any) => {
      if (String(url).includes('/disk-status')) {
        return { data: { workspace_id: 301, usage_bytes: 4 * MB, quota_bytes: 0,
                         quota_is_default: false, sessions: [] } } as any;
      }
      return { data: [] } as any;
    });
    render(
      <MemoryRouter initialEntries={['/workspace-assets/301']}>
        <Routes>
          <Route path="/workspace-assets/:workspaceId" element={<WorkspaceAssets />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('工作空间磁盘用量')).toBeInTheDocument());
    expect(screen.getByText(/不设限/)).toBeInTheDocument();
  });
});
