/** 数据源「取数并发闸门」录入（对应 `backend/core/query_gate.py`）。

需求原话是“最大连接数，避免多个图表/多个用户同时查询把资源打爆，耗尽时优雅提醒排队中、
请稍后再试”。落进 UI 就是三件事，这里逐条锁死：
1. **SQL 型也必须能配**：恰恰是 SQL 型每次取数新建连接、根本没有池，
   只给 ES 摆旋钮等于把需求做在了不需要它的那一侧；
2. 已配的值必须回填、改值必须写进 `extra_config`（不新增裸字段），否则“看得见但改不动”；
3. `queue_wait_sec = 0` 是真配置（不排队直接拒），不得被 `if (值)` 那样的真值判断吃掉
   —— 这类“填了 0 结果等于没填”是配置面最隐蔽的缺陷。
*/
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DatasourceTab } from '../Admin';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const row = (over: Record<string, any> = {}) => ({
  id: 1, name: 'prd-cn', db_type: 'mysql', host: '10.0.0.1', port: 3306,
  username: 'adh', database_name: 'adh', site_code: '', is_default: 0, ssl: 0,
  extra_config: { connect_timeout_sec: 5, read_timeout_sec: 120 },
  ...over,
});

async function renderTab(rows: any[] = [row()]) {
  vi.mocked(client.get).mockImplementation(async (url: any) => {
    if (url === '/datasources') return { data: rows };
    return { data: [] };
  });
  render(<DatasourceTab />);
  await waitFor(() => expect(screen.getByText(rows[0].name)).toBeInTheDocument());
}

async function openEdit(name: string) {
  fireEvent.click(screen.getByRole('button', { name: `编辑数据源 ${name}` }));
  expect(await screen.findByText('编辑数据源')).toBeInTheDocument();
}

const putPayload = (i = 0) => vi.mocked(client.put).mock.calls[i][1] as any;

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('数据源取数并发闸门录入', () => {
  it('SQL 型：两个旋钮都在，且已配的值回填', async () => {
    await renderTab([row({
      extra_config: { connect_timeout_sec: 5, max_concurrency: 12, queue_wait_sec: 30 },
    })]);
    await openEdit('prd-cn');
    expect(screen.getByText('最大并发查询数')).toBeInTheDocument();
    expect(screen.getByText('排队等待上限（秒）')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('默认 8，按源体量收紧或放宽')).toHaveValue(12);
    expect(screen.getByPlaceholderText('默认 10 秒；填 0 = 不排队直接拒')).toHaveValue(30);
  });

  it('SQL 型：改并发上限写进 extra_config，原有超时键不被抹掉', async () => {
    await renderTab();
    await openEdit('prd-cn');
    vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
    fireEvent.change(screen.getByPlaceholderText('默认 8，按源体量收紧或放宽'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    const extra = JSON.parse(putPayload().extra_config);
    expect(extra).toEqual({ connect_timeout_sec: 5, read_timeout_sec: 120, max_concurrency: 4 });
  });

  it('排队上限填 0 必须真传 0（0 = 不排队直接拒，不是“没填”）', async () => {
    await renderTab();
    await openEdit('prd-cn');
    vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
    fireEvent.change(screen.getByPlaceholderText('默认 10 秒；填 0 = 不排队直接拒'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    expect(JSON.parse(putPayload().extra_config).queue_wait_sec).toBe(0);
  });

  it('两项都留空 = 不写这些键（回到代码缺省，行为零变化）', async () => {
    await renderTab();
    await openEdit('prd-cn');
    vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    expect(JSON.parse(putPayload().extra_config)).not.toHaveProperty('max_concurrency');
  });

  it('ES 型同样能配并发上限，并与连接池数量并存（池管复用、上限管限流）', async () => {
    await renderTab([row({
      id: 2, name: 'cloud-platform-es', db_type: 'es', port: 9200, database_name: '',
      extra_config: { allowed_aliases: ['t_logs'], pool_size: 20, max_concurrency: 6 },
    })]);
    await openEdit('cloud-platform-es');
    expect(screen.getByText('连接池数量')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('默认 8，按源体量收紧或放宽')).toHaveValue(6);
    expect(screen.getByText(/池只管复用不管限流/)).toBeInTheDocument();

    vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
    fireEvent.change(screen.getByPlaceholderText('默认 8，按源体量收紧或放宽'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    const extra = JSON.parse(putPayload().extra_config);
    expect(extra.max_concurrency).toBe(2);
    expect(extra.pool_size).toBe(20);
    expect(extra.allowed_aliases).toEqual(['t_logs']);
  });
});
