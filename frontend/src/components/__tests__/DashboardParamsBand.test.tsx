/**
 * 顶部参数带（遗留 params + 未被控件驱动的页面参数）的收口口径。
 *
 * 锁四件事（2026-11-10 用户反馈：那行"开始日期/结束日期"不是日期选择器、没有搜索/重置、
 * 编辑器里也看不见，本质是同一参数三处登记 + 顶栏那份被 pageParamValues 覆盖）：
 *  1. 画布控件已经驱动的键（含 `bind_param` 拆出的变量名）不得再长出第二个输入口；
 *  2. 声明的形状必须真的决定控件（date → 日期选择器，date_range → 起止两个日期框），
 *     不得"存了不生效"退化成文本框；
 *  3. 选择框没配候选值要显式说出来，不静默给一个能填但没意义的框；
 *  4. 这一带必须自带「搜索」「重置」，且重置走 store 的一份口径（两个命名空间一起清）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DashboardRuntimeParams } from '../DashboardParams';
import { uncontrolledParams } from '@/lib/dashboardParamAssembly';
import { useDashboardStore } from '@/stores/dashboardStore';

const ID = 1793969443211;

function dashboard(over: Record<string, any> = {}) {
  return { id: ID, name: '异常上报', params: [], page_params: [], charts: [], filters: {}, ...over } as any;
}

function mount(dash: any) {
  useDashboardStore.setState({ currentId: dash.id, dashboards: [dash] as any, paramValues: {}, pageParamValues: {} });
  return render(<DashboardRuntimeParams dashboard={dash} />);
}

beforeEach(() => {
  useDashboardStore.setState({ currentId: null, dashboards: [], paramValues: {}, pageParamValues: {} } as any);
});

describe('顶部参数带', () => {
  it('控件已驱动 start_time/end_time 时整带不出现（不再有两套输入口）', () => {
    mount(dashboard({
      params: [{ name: 'start_time', type: 'string', label: '开始日期', default: '' },
               { name: 'end_time', type: 'string', label: '结束日期', default: '' }],
      page_params: [{ name: 'start_time', type: 'string', label: '开始日期', default: '' }],
      charts: [{ chart_type: 'widget_daterange', config: { paramKey: 'date_range', bind_param: 'start_time,end_time' } }],
    }));
    expect(screen.queryByLabelText('开始日期')).toBeNull();
    expect(screen.queryByRole('button', { name: /搜索/ })).toBeNull();
  });

  it('未驱动的日期参数渲染成日期选择器，不是文本框', () => {
    mount(dashboard({ params: [{ name: 'stat_date', type: 'date', label: '统计日期', default: '2026-11-01' }] }));
    const input = screen.getByLabelText('统计日期') as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.type).toBe('date');
    expect(input.value).toBe('2026-11-01');
  });

  it('date_range 参数渲染起止两个日期框，值落 {start,end}', () => {
    mount(dashboard({ page_params: [{ name: 'window', type: 'date_range', label: '统计区间', default: '2026-10-01,2026-10-09' }] }));
    fireEvent.change(screen.getByLabelText('统计区间 起始日期'), { target: { value: '2026-10-05' } });
    expect(useDashboardStore.getState().pageParamValues.window).toEqual({ start: '2026-10-05', end: '2026-10-09' });
  });

  it('选择框没配候选值时显式说明，不给"看着能选其实是空"的框', () => {
    mount(dashboard({ params: [{ name: 'level', type: 'select', label: '级别', default: '', options: [] }] }));
    expect(screen.getByText(/未配置候选值/)).toBeInTheDocument();
  });

  it('遗留 params 与页面参数都未驱动时合成一行，两处各写各的命名空间', () => {
    mount(dashboard({
      params: [{ name: 'serial_number', type: 'string', label: '设备序列号', default: '' }],
      page_params: [{ name: 'region', type: 'string', label: '区域', default: '' }],
    }));
    fireEvent.change(screen.getByLabelText('设备序列号'), { target: { value: 'SN-1' } });
    fireEvent.change(screen.getByLabelText('区域'), { target: { value: '华东' } });
    expect(useDashboardStore.getState().paramValues.serial_number).toBe('SN-1');
    expect(useDashboardStore.getState().pageParamValues.region).toBe('华东');
  });

  it('带上有搜索与重置，重置按看板 id 走 store 的一份口径', () => {
    const reset = vi.spyOn(useDashboardStore.getState(), 'resetParams').mockResolvedValue(true);
    const refresh = vi.spyOn(useDashboardStore.getState(), 'refreshCharts').mockResolvedValue(true);
    mount(dashboard({ params: [{ name: 'stat_date', type: 'date', label: '统计日期', default: '' }] }));
    fireEvent.click(screen.getByRole('button', { name: /搜索/ }));
    fireEvent.click(screen.getByRole('button', { name: /重置/ }));
    expect(refresh).toHaveBeenCalledWith(ID);
    expect(reset).toHaveBeenCalledWith(ID);
  });

  it('大屏「筛选条件」入口与参数带共用 uncontrolledParams（不开空面板）', () => {
    const dash = dashboard({
      params: [{ name: 'start_time', type: 'string', label: '开始日期', default: '' }],
      page_params: [{ name: 'start_time', type: 'string', label: '开始日期', default: '' }],
      charts: [{ chart_type: 'widget_daterange', config: { paramKey: 'date_range', bind_param: 'start_time' } }],
    });
    expect(uncontrolledParams(dash, dash.charts)).toEqual([]);
    // 同名登在两套里时只留 page 那一份（下发时 pageParamValues 赢，另一份是第二个死输入口）
    expect(uncontrolledParams(dash, []).map(r => `${r.via}:${r.param.name}`)).toEqual(['page:start_time']);
  });
});
