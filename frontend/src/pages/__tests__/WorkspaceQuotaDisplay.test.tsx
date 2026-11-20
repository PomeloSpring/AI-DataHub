// 磁盘配额的"显示值 = 生效值"回归（2026-11 存储治理 P0-2）+ 统管页服务端真分页。
//
// 历史缺陷是双口径：拦截门把"无配额登记行"读成 0=不限制，而管理员页 COALESCE 显示 5GB，
// 资产页显示"未设配额"—— 页面写着有配额、实际从不拦（配了没用）。后端已收口到唯一裁决点
// （role_service.get_user_workspace_quota）并下发 quota_is_default，本组用例锁前端把
// "系统缺省 / 显式配置 / 不设限"三种语义显示得可区分，不得再出现"未设配额"这种含糊文案。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

// 服务端契约 {items,total,page,page_size}：total 是过滤后的全量行数，与当前页条数无关。
// disk_usage_bytes = 该用户单个空间的最大用量（与"每空间配额"同口径才可比），由 API 层按页内用户算。
const LIST = {
  items: [
    // 无登记行 → 后端下发系统缺省 + quota_is_default
    { user_id: 1, username: 'zhangsan', max_workspaces: 5, disk_quota_bytes: DEFAULT_BYTES,
      disk_usage_bytes: 4 * MB, quota_is_default: true },
    // 管理员显式配置 → 不标缺省
    { user_id: 2, username: 'lisi', max_workspaces: 7, disk_quota_bytes: 88 * MB,
      disk_usage_bytes: 0, quota_is_default: false },
    // 显式登记 0 = 管理员"不设限"逃生口（不是缺省，也不能画成 0% 空条冒充没用过）
    { user_id: 3, username: 'wangwu', max_workspaces: 5, disk_quota_bytes: 0,
      disk_usage_bytes: 12 * MB, quota_is_default: false },
  ],
  total: 137, page: 1, page_size: 20,
};

// 逐空间磁盘用量在详情接口里给（清单只给单空间最大值口径的汇总用量）
const DETAIL_1 = {
  user_id: 1, username: 'zhangsan', max_workspaces: 5, disk_quota_bytes: DEFAULT_BYTES,
  disk_usage_bytes: 4 * MB, quota_is_default: true,
  workspaces: [{ id: 301, name: '默认空间', is_default: 1, disk_usage_bytes: 4 * MB }],
};

beforeEach(() => {
  vi.mocked(client.get).mockImplementation(async (url: any) => {
    if (String(url).startsWith('/workspaces/quotas/')) return { data: DETAIL_1 } as any;
    if (String(url).startsWith('/workspaces/quotas')) return { data: LIST } as any;
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
  it('配额列显示进度条 + 已用/配额，缺省与显式配置可区分', async () => {
    const { container } = render(<MemoryRouter><WorkspaceManagerV2 /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('zhangsan')).toBeInTheDocument());

    expect(screen.getByText('4.0 MB / 500 MB')).toBeInTheDocument();
    expect(screen.getByText('0 B / 88 MB')).toBeInTheDocument();
    // 不设限：只报已用，不编一个配额出来
    expect(screen.getByText('12 MB / 不设限')).toBeInTheDocument();
    // 缺省带后缀、显式配置不带；来源细节落 title ——「配额来源」不再单列
    // （没人登记过配额时，整列 9 行都是同一个"系统缺省"，属噪音）
    expect(screen.getByText('（系统缺省）')).toBeInTheDocument();
    expect(screen.getAllByTitle('管理员显式配置')).toHaveLength(2);
    expect(screen.queryByText('配额来源')).not.toBeInTheDocument();

    // 三条用户行只画两条条：配额 0（不设限）没有比例可画，不渲空槽冒充"没用过"
    const fills = Array.from(container.querySelectorAll<HTMLElement>(
      'table tbody .bg-primary, table tbody .bg-amber-500, table tbody .bg-destructive',
    ));
    expect(fills).toHaveLength(2);
    // 小比例（此处 4/500 = 0.8%）曾经渲成看不见的空槽，被读成"没用过"
    expect(parseFloat(fills[0].style.width)).toBeLessThan(1);
    expect(fills[0].style.minWidth).toBe('6px');
    // 真没用过的行仍是 0 宽，不谎报用量
    expect(fills[1].style.minWidth).toBe('0px');
  });

  it('不再展示工作空间数（用户侧自建入口已关）', async () => {
    render(<MemoryRouter><WorkspaceManagerV2 /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('zhangsan')).toBeInTheDocument());

    expect(screen.queryByText('工作空间数')).not.toBeInTheDocument();
    expect(screen.queryByText('5/5')).not.toBeInTheDocument();
  });

  it('分页总数来自服务端 total，清单按 page/page_size/q 请求（不得前端切片）', async () => {
    render(<MemoryRouter><WorkspaceManagerV2 /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('zhangsan')).toBeInTheDocument());

    // 共 137 条来自 total，不是当前页的 2 条
    expect(screen.getByText(/共 137 条/)).toBeInTheDocument();
    expect(client.get).toHaveBeenCalledWith('/workspaces/quotas', {
      params: { page: 1, page_size: 20, q: '' },
    });
  });

  it('翻页带 page 重新请求，不重算本地切片', async () => {
    render(<MemoryRouter><WorkspaceManagerV2 /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('zhangsan')).toBeInTheDocument());

    fireEvent.click(screen.getByText('下一页'));
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/workspaces/quotas', {
      params: { page: 2, page_size: 20, q: '' },
    }));
  });

  it('点行打开详情：取该用户空间清单与磁盘用量，默认空间不给删', async () => {
    render(<MemoryRouter><WorkspaceManagerV2 /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('zhangsan')).toBeInTheDocument());

    fireEvent.click(screen.getAllByText('详情')[0]);
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/workspaces/quotas/1'));
    await waitFor(() => expect(screen.getByText('默认空间')).toBeInTheDocument());

    // 清单行与详情用的是同一口径数字，故按弹窗范围取逐空间明细
    expect(within(screen.getByRole('dialog')).getByText(/4\.0 MB \/ 500 MB/)).toBeInTheDocument();
    expect(screen.queryByTitle('删除工作空间')).not.toBeInTheDocument();
  });

  it('详情加载失败必须显式可重试，不得渲成"该用户没有空间"', async () => {
    vi.mocked(client.get).mockImplementation(async (url: any) => {
      if (String(url).startsWith('/workspaces/quotas/')) throw new Error('boom');
      return { data: LIST } as any;
    });
    render(<MemoryRouter><WorkspaceManagerV2 /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('zhangsan')).toBeInTheDocument());

    fireEvent.click(screen.getAllByText('详情')[0]);
    await waitFor(() => expect(screen.getByText(/加载工作空间清单失败/)).toBeInTheDocument());
    expect(screen.queryByText(/该用户还没有工作空间/)).not.toBeInTheDocument();
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
