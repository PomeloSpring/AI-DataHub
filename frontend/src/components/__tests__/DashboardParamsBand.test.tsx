/**
 * 顶部参数带（遗留 params + 未被控件驱动的页面参数）的收口口径。
 *
 * 锁四件事（2026-11-10 用户反馈：那行"开始日期/结束日期"不是日期选择器、没有搜索/重置、
 * 编辑器里也看不见，本质是同一参数三处登记 + 顶栏那份被 pageParamValues 覆盖）：
 *  1. 画布控件已经驱动的键（含 `bind_param` 拆出的变量名）不得再长出第二个输入口；
 *  2. 声明的形状必须真的决定控件（date → 日期选择器，date_range → 起止两个日期框），
 *     不得"存了不生效"退化成文本框；
 *  3. 选择框没配候选值要显式说出来，不静默给一个能填但没意义的框；
 *  4. 这一带必须自带「搜索」「重置」，且重置走 store 的一份口径（两个命名空间一起清）；
 *  5. 标了 `readonly` 的看板参数（单 App 看板的分析对象）只呈现不开输入口，但值照旧进
 *     `pageParamValues` 参与取数 —— 「不给改」不等于「不下发」；
 *  6. 只剩只读常数时整带直接不出现：那一行改不了任何东西却带一套「搜索/重置」，
 *     与画布上的日期区间/站点/查询/重置重复（空承诺）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DashboardRuntimeParams } from '../DashboardParams';
import { uncontrolledParams, editableParamRows } from '@/lib/dashboardParamAssembly';
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

  it('大屏「筛选条件」入口与参数带共用同一判据（不开空面板）', () => {
    const dash = dashboard({
      params: [{ name: 'start_time', type: 'string', label: '开始日期', default: '' }],
      page_params: [{ name: 'start_time', type: 'string', label: '开始日期', default: '' }],
      charts: [{ chart_type: 'widget_daterange', config: { paramKey: 'date_range', bind_param: 'start_time' } }],
    });
    expect(uncontrolledParams(dash, dash.charts)).toEqual([]);
    // 同名登在两套里时只留 page 那一份（下发时 pageParamValues 赢，另一份是第二个死输入口）
    expect(uncontrolledParams(dash, []).map(r => `${r.via}:${r.param.name}`)).toEqual(['page:start_time']);
    // 入口存在与否看 editableParamRows（与组件内部同一函数），否则只剩只读常数时
    // 大屏会渲出一个点开是空的「筛选条件」按钮
    const onlyReadonly = dashboard({
      page_params: [{ name: 'app_code', type: 'string', label: '分析对象', default: 'HeyGearsIntegration', readonly: true }],
    });
    expect(uncontrolledParams(onlyReadonly, []).length).toBe(1);
    expect(editableParamRows(uncontrolledParams(onlyReadonly, []))).toEqual([]);
  });

  // 单 App 看板的 app_code：留一个能改的文本框等于允许“标题写着 A、图上算的是 B”。
  it('只读参数与可改参数并存时，只读那行渲成静态呈现（不是输入口）', () => {
    mount(dashboard({
      page_params: [
        { name: 'app_code', type: 'string', label: '分析对象', default: 'MeshCompare', readonly: true },
        { name: 'region', type: 'string', label: '区域', default: '' },
      ],
    }));
    const shown = screen.getByLabelText('分析对象');
    expect(shown.tagName).toBe('OUTPUT');
    expect(shown.textContent).toContain('MeshCompare');
    expect(screen.queryByRole('textbox', { name: '分析对象' })).toBeNull();
    // 同带里可改的那项照常（不能因为只读行把整带关掉）
    expect(screen.getByRole('textbox', { name: '区域' })).toBeTruthy();
  });

  it('只读参数没值时说出来（（未设置）），不留个看着像能填的空框', () => {
    mount(dashboard({
      page_params: [
        { name: 'app_code', type: 'string', label: '分析对象', default: '', readonly: true },
        { name: 'region', type: 'string', label: '区域', default: '' },
      ],
    }));
    expect(screen.getByLabelText('分析对象').textContent).toContain('（未设置）');
    expect(screen.queryByRole('textbox', { name: '分析对象' })).toBeNull();
  });

  // 用户拍板：单 App 看板详情里不要“分析对象 + 单独一套搜索/重置”那一行——
  // 画布已经有日期区间/站点/查询/重置，只读常数既改不了又占一行，按钮点了也不改变任何条件。
  it('只剩只读常数时整带不出现（没分析对象行，也没第二套搜索/重置），但值照旧下发', () => {
    const dash = dashboard({
      name: 'App·HeyGears',
      page_params: [
        { name: 'app_code', type: 'string', label: '分析对象', default: 'HeyGearsIntegration', readonly: true },
        { name: 'start_time', type: 'string', label: '开始日期', default: '' },
      ],
      // 日期区间控件已经驱动 start_time → 参数带里只剩只读的 app_code
      charts: [{ chart_type: 'widget_daterange', config: { paramKey: 'date_range', bind_param: 'start_time' } }],
    });
    useDashboardStore.setState({ dashboards: [dash] as any, currentId: null });
    useDashboardStore.getState().setCurrent(dash.id);
    render(<DashboardRuntimeParams dashboard={dash} />);
    expect(screen.queryByText('分析对象')).toBeNull();
    expect(screen.queryByRole('button', { name: /搜索/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /重置/ })).toBeNull();
    // 不显示 ≠ 不下发：取数仍按本看板的固定分析对象过滤
    expect(useDashboardStore.getState().pageParamValues.app_code).toBe('HeyGearsIntegration');
  });
});
