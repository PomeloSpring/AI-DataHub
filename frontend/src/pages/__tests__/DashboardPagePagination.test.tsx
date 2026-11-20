/**
 * 看板管理页服务端真分页（禁止"拉全量再前端切片"的假分页）。
 *
 * 锁定（.qoder/rules/list-pagination-server-side.md）：
 *  - 进页/翻页/改档位都带 page/page_size 重新请求，分页态绝不发无 page_size 的全量请求；
 *  - 「共 N 条」与分页条的 total 渲染自服务端 COUNT，不是当前页长度；
 *  - 搜索防抖下推服务端，改任一筛选一律回第 1 页（停在旧页码 + 新条件会拿到错位的空清单）；
 *  - 上移/下移走 POST /dashboard/move（服务端同口径排序定位邻居），被拒时把服务端原因显示给用户。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import DashboardPage from '@/pages/Dashboard';
import { useDashboardStore } from '@/stores/dashboardStore';
import { toast } from '@/lib/toast';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => ({ items: [], error: '', refresh: vi.fn() }) }));
vi.mock('@/components/DashboardCanvas', () => ({ DashboardThumbnail: () => null }));
vi.mock('@/components/DashboardGroupsAdmin', () => ({ default: () => <div>组合管理占位</div> }));

const board = (id: number, name = `看板${id}`): any => ({
  id, name, status: 'enabled', is_default: false, editable: true, owner_id: 1,
  description: '', layout: [], filters: {}, params: [], page_params: [], tags: [], charts: [],
  carousel_interval: 0, sort_order: id, created_at: '', updated_at: '',
});

/** 46 块板，服务端按 sort_order 升序排出（第 1 页 = 1..20）。 */
const ALL = Array.from({ length: 46 }, (_, i) => board(i + 1));

const listUrls = () => vi.mocked(client.get).mock.calls
  .map((c) => String(c[0]))
  .filter((u) => u.startsWith('/dashboard/?'));
const lastListUrl = () => listUrls()[listUrls().length - 1] ?? '';

beforeEach(() => {
  vi.clearAllMocks();
  useDashboardStore.getState().reset();
  vi.mocked(client.get).mockImplementation(async (raw: string) => {
    const url = String(raw);
    if (url.startsWith('/dashboard/tags')) return { data: [] } as any;
    if (url.startsWith('/dashboard/?')) {
      const q = new URL(url, 'http://x').searchParams;
      const size = Number(q.get('page_size')) || 20;
      const page = Number(q.get('page')) || 1;
      let list = ALL;
      const search = q.get('search');
      if (search) list = list.filter((b) => b.name.includes(search));
      const status = q.get('status');
      if (status) list = list.filter((b) => b.status === status);
      return { data: { items: list.slice((page - 1) * size, page * size), total: list.length, page, page_size: size } } as any;
    }
    return { data: null } as any;
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function mount() { return render(<MemoryRouter><DashboardPage /></MemoryRouter>); }

describe('看板管理页 — 服务端真分页', () => {
  it('进页即带 page/page_size 请求第 1 页，计数渲染服务端 total 而非本页长度', async () => {
    mount();
    await screen.findByText('看板1');
    expect(listUrls()[0]).toContain('page=1');
    expect(listUrls()[0]).toContain('page_size=20');
    // 本页只有 20 行，「共 46 条」必须来自 COUNT
    expect(screen.getByText(/46 个看板/)).toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(20);
  });

  it('翻页带新页号重新请求（不在本地对全量切片）', async () => {
    mount();
    await screen.findByText('看板1');
    const bar = screen.getByRole('button', { name: '下一页' });
    fireEvent.click(bar);
    await waitFor(() => expect(screen.getByText('看板21')).toBeInTheDocument());
    expect(screen.queryByText('看板1')).not.toBeInTheDocument();
    expect(lastListUrl()).toContain('page=2');
    // 分页条的总数不随翻页抖动
    expect(screen.getByText(/46 个看板/)).toBeInTheDocument();
  });

  it('改每页档位带 page_size 重取并回到第 1 页', async () => {
    mount();
    await screen.findByText('看板1');
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(screen.getByText('看板21')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('combobox', { name: '每页条数' }));
    fireEvent.click(await screen.findByRole('option', { name: '50 条' }));
    await waitFor(() => expect(screen.getByText('看板1')).toBeInTheDocument());
    const url = lastListUrl();
    expect(url).toContain('page=1');
    expect(url).toContain('page_size=50');
  });

  it('搜索防抖后下推服务端，且一律回到第 1 页', async () => {
    mount();
    await screen.findByText('看板1');
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(screen.getByText('看板21')).toBeInTheDocument());

    fireEvent.change(screen.getByRole('textbox', { name: '搜索看板' }), { target: { value: '看板3' } });
    // 防抖 300ms：停手才发请求（每敲一个字符打一次服务端就是自我 DoS）
    await waitFor(() => expect(lastListUrl()).toContain('search=%E7%9C%8B%E6%9D%BF3'), { timeout: 1500 });
    expect(vi.mocked(client.get).mock.calls.filter((c) => String(c[0]).includes('search=')).length).toBe(1);
    const url = lastListUrl();
    expect(url).toContain('page=1');
    // 看板3 + 看板30~39 = 11 条，总数仍由服务端 COUNT 给
    await waitFor(() => expect(screen.getByText(/11 个看板/)).toBeInTheDocument());
    expect(screen.queryByText('看板21')).not.toBeInTheDocument();
  });

  it('状态筛选变化带 status 重新请求并回第 1 页', async () => {
    mount();
    await screen.findByText('看板1');
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(screen.getByText('看板21')).toBeInTheDocument());

    fireEvent.change(screen.getByRole('combobox', { name: '状态筛选' }), { target: { value: 'designing' } });
    await waitFor(() => expect(lastListUrl()).toContain('status=designing'));
    expect(lastListUrl()).toContain('page=1');
  });

  it('分页态不再发无 page_size 的全量清单请求（编辑器切板才要全量）', async () => {
    mount();
    await screen.findByText('看板1');
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(listUrls().length).toBeGreaterThan(1));
    fireEvent.change(screen.getByRole('combobox', { name: '状态筛选' }), { target: { value: 'enabled' } });
    await waitFor(() => expect(listUrls().length).toBeGreaterThan(2));
    expect(listUrls().every((u) => u.includes('page_size='))).toBe(true);
  });

  it('上移/下移走 POST /dashboard/move（服务端定位邻居，跨页也生效）', async () => {
    vi.mocked(client.post).mockResolvedValue({ data: { moved: true } } as any);
    mount();
    await screen.findByText('看板2');
    await userEvent.click(screen.getByRole('button', { name: '看板2 更多操作' }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '上移' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/dashboard/move', { dashboard_id: 2, direction: -1 }));
  });

  it('移动被服务端拒绝时把可读原因原样提示，不静默假成功', async () => {
    vi.mocked(client.post).mockRejectedValue({ response: { data: { detail: '已在列表末尾，无法继续下移' } } } as any);
    const error = vi.spyOn(toast, 'error').mockImplementation(() => '' as any);
    mount();
    await screen.findByText('看板1');
    await userEvent.click(screen.getByRole('button', { name: '看板1 更多操作' }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '下移' }));
    await waitFor(() => expect(error).toHaveBeenCalledWith('已在列表末尾，无法继续下移'));
  });

  it('本页首行不再被判成整份清单首行（跨页判定交给服务端 reason）', async () => {
    vi.mocked(client.post).mockResolvedValue({ data: { moved: true } } as any);
    mount();
    await screen.findByText('看板1');
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(screen.getByText('看板21')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: '看板21 更多操作' }));
    expect(await screen.findByRole('menuitem', { name: '上移' })).not.toBeDisabled();
  });
});
