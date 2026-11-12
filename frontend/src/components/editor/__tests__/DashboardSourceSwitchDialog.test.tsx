/**
 * 看板级「批量切换执行数据源」对话框（2026-11-12）。
 *
 * 锁定：
 *  - 候选源来自各数据集 usable_now 的并集（业务名，不接触内部 datasource_id）；
 *  - 只把目标源应用到「其数据集支持该源」的图表，不支持的显式跳过并说明原因（不静默改挂）；
 *  - 数据集不在当前清单内（撤权/停用）的图表无法判定 → 跳过并标注，不猜；
 *  - 应用回调只带业务名 + 命中的图表 id，由上层写草稿。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor, render } from '@testing-library/react';
import DashboardSourceSwitchDialog from '../DashboardSourceSwitchDialog';
import client from '@/api/client';

const datasets = [
  { id: 8, name: '趋势', usable_now: ['prd-cn', 'prd-jp'] },
  { id: 9, name: '明细', usable_now: ['prd-cn'] },
];
const charts = [
  { id: 1, name: '图A', chart_type: 'line', source_type: 'dataset', source_id: 8, config: {} },
  { id: 2, name: '图B', chart_type: 'bar', source_type: 'dataset', source_id: 9, config: {} },
  { id: 3, name: '图C', chart_type: 'widget_search', source_type: 'widget', source_id: null, config: {} },
  { id: 4, name: '图D', chart_type: 'pie', source_type: 'dataset', source_id: 999, config: {} },
];

let applyCalls: Array<[string, number[]]>;
beforeEach(() => {
  applyCalls = [];
  vi.spyOn(client, 'get').mockImplementation(((url: string) =>
    Promise.resolve({ data: url === '/datasets/' ? { items: datasets } : [] })) as any);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function open() {
  render(<DashboardSourceSwitchDialog open charts={charts} onCancel={() => {}}
    onApply={(t, ids) => applyCalls.push([t, ids])} />);
}

async function pickTarget(name: string) {
  const trigger = await screen.findByRole('combobox');
  fireEvent.click(trigger);
  fireEvent.click(await screen.findByRole('option', { name }));
}

describe('批量切换执行数据源', () => {
  it('控件与未绑数据集的图不参与，候选源为各数据集 usable_now 并集', async () => {
    open();
    await pickTarget('prd-jp');
    // 仅图A（数据集8）支持 prd-jp；图B(单源)、图D(数据集不在清单)、控件图C 都不参与命中
    expect(await screen.findByText(/将切换 1/)).toBeTruthy();
    expect(screen.getByText(/跳过 2/)).toBeTruthy();
  });

  it('应用只把目标源写到支持它的图表 id 上', async () => {
    open();
    await pickTarget('prd-jp');
    fireEvent.click(await screen.findByRole('button', { name: /切换到 1 个图表/ }));
    await waitFor(() => expect(applyCalls).toHaveLength(1));
    expect(applyCalls[0]).toEqual(['prd-jp', [1]]);
  });

  it('数据集不在清单内的图表被跳过并标注原因（不猜范围）', async () => {
    open();
    await pickTarget('prd-jp');
    expect(await screen.findByText('图D')).toBeTruthy();
    expect(screen.getByText(/数据集不在当前清单内/)).toBeTruthy();
  });

  it('清除全部钉选：回调带空目标 + 已钉选图表 id', async () => {
    const pinned = [
      { id: 1, name: '图A', chart_type: 'line', source_type: 'dataset', source_id: 8, config: { datasource: 'prd-cn' } },
      { id: 2, name: '图B', chart_type: 'bar', source_type: 'dataset', source_id: 9, config: {} },
    ];
    render(<DashboardSourceSwitchDialog open charts={pinned} onCancel={() => {}}
      onApply={(t, ids) => applyCalls.push([t, ids])} />);
    fireEvent.click(await screen.findByRole('button', { name: '全部改回自动定源' }));
    await waitFor(() => expect(applyCalls).toHaveLength(1));
    expect(applyCalls[0]).toEqual(['', [1]]);
  });

  it('清单加载失败显式报错 + 可重试，不退成"没有可切的源"', async () => {
    vi.spyOn(client, 'get').mockRejectedValueOnce({} as any);
    open();
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByText(/数据集清单加载失败/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy();
  });
});
