/**
 * 图表配置面板「数据」页的查看数据能力：预览已授权数据集的样本行。
 *
 * 边界（2026-11-06 拍板）：口径仍归数据集 —— 本面板不录入也不执行 SQL，
 * 预览走 `POST /datasets/{id}/preview`（服务端编译 + 角色权限/RLS/敏感屏蔽）。
 * 锁定：
 *  - 预览请求只带行数上限：实测发现 SQL 型数据集带 dimensions/measures 会编译出非法 SQL，
 *    语义型带 field_config 的指标名反而被解析护栏拒 —— 预览就按数据集自身口径取行；
 *  - 响应里的 SQL / 内部 id 一律不外泄（护栏 §7），即便服务端多回了字段；
 *  - 失败显式暴露 + 可重试，不得退成"没有数据"；
 *  - 换数据集必须丢掉上一份样本，避免旧响应被读成新数据集的数据；
 *  - 未选数据集时不给点（不开空承诺）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import ChartConfigPanel from '../ChartConfigPanel';
import client from '@/api/client';

const datasets = [
  { id: 8, name: 'ARMS·用户与会话趋势', source_type: 'sql', datasource_name: 'arms-prd-cn', field_count: 3,
    field_config: JSON.stringify([{ field: 'stat_date', label: '统计日期', role: 'dimension' },
      { field: 'uv', label: '独立访客', role: 'measure' }]) },
  { id: 9, name: '另一个数据集', source_type: 'sql', datasource_name: 'prd-cn', field_count: 2, field_config: '[]' },
];

const chartOf = (over: Record<string, any> = {}) => ({
  id: 700, dashboard_id: 10, name: '回归图', chart_type: 'bar',
  source_type: 'dataset', source_id: 8, config: { xCol: 'stat_date', yCol: 'uv' }, ...over,
});

// Radix Tabs 靠 pointer 事件切换，fireEvent.click 切不动（面板会停在「基础设置」）
async function openPanel(chart: any) {
  render(<MemoryRouter><ChartConfigPanel open chart={chart} onClose={() => {}} onSave={() => {}} /></MemoryRouter>);
  await userEvent.click(screen.getByRole('tab', { name: '数据' }));
}

let posts: Array<[string, any]>;

beforeEach(() => {
  posts = [];
  vi.spyOn(client, 'get').mockImplementation((url: string) =>
    Promise.resolve({ data: url === '/datasets/' ? { items: datasets } : [] } as any));
  vi.spyOn(client, 'post').mockImplementation(((url: string, body: any) => {
    posts.push([url, body]);
    return Promise.resolve({
      data: { columns: ['统计日期', '独立访客'], rows: [{ '统计日期': '2026-11-01', '独立访客': 12 }],
        // 服务端多回的内部细节也绝不能出现在面板上（护栏 §7）
        sql: 'SELECT stat_date FROM dwd_x', datasource_id: 8, execution_mode: 'sql',
      },
    }) as any;
  }) as any);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('预览数据', () => {
  it('按数据集口径取样本行，请求只带行数上限（不塞字段映射）', async () => {
    await openPanel(chartOf());
    fireEvent.click(await screen.findByRole('button', { name: /预览数据/ }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0][0]).toBe('/datasets/8/preview');
    // 带 dimensions/measures 会让 SQL 型编译出非法 SQL（实测），所以预览不传字段
    expect(posts[0][1]).toEqual({ limit: 20 });
    expect(await screen.findByText('2026-11-01')).toBeTruthy();
    expect(screen.getByText(/样本 1 行 · 2 列/)).toBeTruthy();
  });

  it('响应里的 SQL 与内部 id 不外泄（面板不展示任何口径文本）', async () => {
    await openPanel(chartOf());
    fireEvent.click(await screen.findByRole('button', { name: /预览数据/ }));
    await waitFor(() => expect(screen.getByText('2026-11-01')).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/SELECT|datasource_id|dwd_x/);
  });

  it('预览失败：显示服务端原因 + 重试，不伪装成空结果', async () => {
    vi.mocked(client.post).mockRejectedValueOnce({ response: { data: { detail: '当前角色无权执行该数据集的任何可用数据源' } } } as any);
    await openPanel(chartOf());
    fireEvent.click(await screen.findByRole('button', { name: /预览数据/ }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('当前角色无权执行该数据集的任何可用数据源');
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy();
    expect(screen.queryByText(/样本 0 行/)).toBeNull();
  });

  it('换数据集就丢掉上一份样本（旧响应不得冒充新数据集）', async () => {
    await openPanel(chartOf());
    fireEvent.click(await screen.findByRole('button', { name: /预览数据/ }));
    await waitFor(() => expect(screen.getByText('2026-11-01')).toBeTruthy());
    // 通过可搜索选择框改挂另一个数据集
    fireEvent.click(screen.getByRole('combobox', { name: '数据集' }));
    fireEvent.click(await screen.findByRole('option', { name: /另一个数据集/ }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByText('2026-11-01')).toBeNull();
    expect(screen.getByRole('button', { name: /预览数据/ })).toBeTruthy();
  });

  it('未选数据集时不给点（不开"点了必报错"的入口）', async () => {
    await openPanel(chartOf({ source_type: 'empty', source_id: null }));
    const btn = await screen.findByRole('button', { name: /预览数据/ });
    expect(btn).toBeDisabled();
  });

  it('换数据集后预览走新数据集（旧响应不会冒充新口径）', async () => {
    await openPanel(chartOf({ source_id: 9 }));
    fireEvent.click(await screen.findByRole('button', { name: /预览数据/ }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0][0]).toBe('/datasets/9/preview');
    expect(posts[0][1]).toEqual({ limit: 20 });
  });
});

describe('数据页不再有 SQL 通道', () => {
  it('没有 SQL 文本框，也没有旧的执行/复制入口', async () => {
    await openPanel(chartOf());
    await screen.findByRole('button', { name: /预览数据/ });
    expect(screen.queryByRole('textbox', { name: /SQL/i })).toBeNull();
    expect(document.body.textContent).not.toMatch(/执行 SQL|运行查询/);
  });
});
