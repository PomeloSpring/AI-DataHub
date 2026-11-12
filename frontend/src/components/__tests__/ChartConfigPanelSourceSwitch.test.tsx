/**
 * 图表配置面板「数据」页的执行数据源切换（看板级选源，2026-11-12）。
 *
 * 数据集声明「可用数据源范围」（可用范围 ∩ 角色授权源 = usable_now）时，图表可钉选一个执行源：
 *  - 仅当 usable_now 多于一个源才提供切换器（单源没得切，渲出来是空承诺）；
 *  - 选项一律业务名，内部 datasource_id 不外泄（护栏 §7）；
 *  - 预览请求带上钉选源（服务端在范围内解析）；
 *  - 保存把钉选写进 config.datasource（随刷新下递服务端）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import ChartConfigPanel from '../ChartConfigPanel';
import client from '@/api/client';

const multiSource = {
  id: 8, name: 'ARMS·用户与会话趋势', source_type: 'sql', datasource_name: 'arms-prd-cn',
  available_source_names: ['arms-prd-cn', 'arms-prd-sg'], usable_now: ['arms-prd-cn', 'arms-prd-sg'],
  field_count: 3, field_config: '[]',
};
const singleSource = {
  id: 9, name: '单源数据集', source_type: 'sql', datasource_name: 'prd-cn',
  available_source_names: ['prd-cn'], usable_now: ['prd-cn'], field_count: 2, field_config: '[]',
};

const chartOf = (over: Record<string, any> = {}) => ({
  id: 700, dashboard_id: 10, name: '回归图', chart_type: 'bar',
  source_type: 'dataset', source_id: 8, config: { xCol: 'stat_date', yCol: 'uv' }, ...over,
});

async function openPanel(chart: any, items = [multiSource, singleSource], onSave: (cfg: any) => void = () => {}) {
  vi.spyOn(client, 'get').mockImplementation(((url: string) =>
    Promise.resolve({ data: url === '/datasets/' ? { items } : [] })) as any);
  render(<MemoryRouter><ChartConfigPanel open chart={chart} onClose={() => {}} onSave={onSave} /></MemoryRouter>);
  await userEvent.click(screen.getByRole('tab', { name: '数据' }));
}

let posts: Array<[string, any]>;
beforeEach(() => {
  posts = [];
  vi.spyOn(client, 'post').mockImplementation(((url: string, body: any) => {
    posts.push([url, body]);
    return Promise.resolve({ data: { columns: ['a'], rows: [{ a: 1 }] } }) as any;
  }) as any);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('执行数据源切换器', () => {
  it('数据集可在多个已授权源上执行时提供切换器', async () => {
    await openPanel(chartOf());
    expect(await screen.findByText(/该数据集可在 2 个已授权数据源上执行/)).toBeTruthy();
  });

  it('单源数据集不提供切换器（没得切，不渲空承诺）', async () => {
    await openPanel(chartOf({ source_id: 9 }), [singleSource]);
    await screen.findByRole('button', { name: /预览数据/ });
    expect(screen.queryByText(/个已授权数据源上执行/)).toBeNull();
  });

  it('已钉选源时预览请求带上业务名（不带内部 id）', async () => {
    await openPanel(chartOf({ config: { datasource: 'arms-prd-sg' } }));
    fireEvent.click(await screen.findByRole('button', { name: /预览数据/ }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0][0]).toBe('/datasets/8/preview');
    expect(posts[0][1].datasource).toBe('arms-prd-sg');
    expect(JSON.stringify(posts[0][1])).not.toMatch(/datasource_id/);
  });

  it('保存把钉选源写进 config.datasource', async () => {
    const saved: any[] = [];
    await openPanel(chartOf({ config: { datasource: 'arms-prd-sg' } }), [multiSource],
      (cfg: any) => { saved.push(cfg); });
    fireEvent.click(await screen.findByRole('button', { name: '保存' }));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].datasource).toBe('arms-prd-sg');
  });

  it('切换器选项来自 usable_now，选择后写回 config', async () => {
    const saved: any[] = [];
    await openPanel(chartOf(), [multiSource], (cfg: any) => { saved.push(cfg); });
    const trigger = await screen.findByRole('combobox', { name: '' } as any);
    // 打开执行数据源下拉（面板里可能有多个 combobox：数据集是 search-select，此处取带"自动"选项的那个）
    const sourceTrigger = [...document.querySelectorAll('[role="combobox"]')]
      .find(el => (el.textContent || '').includes('自动')) as HTMLElement;
    fireEvent.click(sourceTrigger || trigger);
    fireEvent.click(await screen.findByRole('option', { name: 'arms-prd-sg' }));
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].datasource).toBe('arms-prd-sg');
  });
});
