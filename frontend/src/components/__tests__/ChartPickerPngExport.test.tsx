/**
 * 「图表 PNG」导出的两个易错点（都是历史缺陷，用例把门）：
 * 1. html2canvas 必须开 useCORS —— 默认 allowTaint 会把跨域瓦片画进画布，
 *    画布随即被污染，下一句 toDataURL 抛 SecurityError，表现为"点了没反应"。
 * 2. 失败必须可见 —— 只 console.error 用户无从区分"导出坏了"和"没这个功能"。
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ChartPicker from '../ChartPicker';
import { toast } from '@/lib/toast';

vi.mock('../DashboardChart', () => ({
  default: () => <div data-testid="chart" />,
  ChartIcon: () => null,
  CHART_TYPES: [{ value: 'bar', label: '柱状图', icon: 'BarChart3', category: 'basic' }],
  CHART_TYPE_CATEGORIES: [{ key: 'basic', label: '基础' }],
}));
vi.mock('../MapHeatmap', () => ({ default: () => null, detectGeoColumns: () => ({ nameMatched: false }) }));
vi.mock('../SqlCard', () => ({ default: () => null }));
vi.mock('xlsx', () => ({ utils: {} }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: () => ({ items: [], loading: false, error: '' }) }));

const h2c = vi.hoisted(() => vi.fn());
vi.mock('html2canvas', () => ({ default: h2c }));
vi.mock('@/lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
  AppToaster: () => null,
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

// Radix 的下拉内容是异步挂载的，fireEvent 点完立刻查会查不到 → 用 userEvent
async function openExportMenu() {
  render(<ChartPicker data={{ columns: ['周', '数值'], rows: [{ 周: 'W1', 数值: 10 }] }} />);
  await userEvent.click(screen.getByRole('button', { name: /导出/ }));
  await userEvent.click(await screen.findByText('图表 PNG'));
}

// jsdom 里 <a download>.click() 会走导航未实现告警，桩掉即可
const anchorClick = () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  return click;
};

describe('ChartPicker 图表 PNG 导出', () => {
  it('拍图必须带 useCORS，否则跨域瓦片会污染画布让 toDataURL 抛错', async () => {
    const click = anchorClick();
    h2c.mockResolvedValue({ toDataURL: () => 'data:image/png;base64,AA' });
    await openExportMenu();
    await waitFor(() => expect(h2c).toHaveBeenCalled());
    expect(h2c.mock.calls[0][1]).toMatchObject({ useCORS: true });
    await waitFor(() => expect(click).toHaveBeenCalled());
    click.mockRestore();
  });

  it('拍图失败时给出可见提示，不静默什么都不做', async () => {
    anchorClick();
    h2c.mockRejectedValue(new Error('SecurityError: tainted'));
    await openExportMenu();
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(vi.mocked(toast.error).mock.calls[0][0]).toContain('导出失败');
  });
});
