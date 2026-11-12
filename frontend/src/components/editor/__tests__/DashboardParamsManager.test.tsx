/**
 * 「看板参数」管理区：两套命名空间在同一处可见、可改、可删。
 *
 * 起因（2026-11-10 用户反馈）：遗留 `params` 只能在列表页设置弹窗里改，编辑器里
 * 看不见也删不掉，于是页面上那行"开始日期/结束日期"既不是日期选择器又没法操作。
 * 这里锁六件事：
 *  1. 两套参数都渲染出来（不得"配了看不见"），并标出哪个键已由画布控件驱动；
 *  2. 形状词汇是一份（遗留参数也能选「日期区间」），改形状不丢默认值；
 *  3. 删除/添加如实回传；
 *  4. 「转成控件」只在有画布的入口出现（编辑器），列表页不给空承诺；
 *  5. 参数名非法或重复必须显式说出来（role=alert），不能静默写进去；
 *  6. 选择框形状没配候选值要提示，不留"看着能选其实是空"的框。
 */
import { describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import DashboardParamsManager, { fromParamRows, toParamRows } from '../DashboardParamsManager';
import { PARAM_SHAPES } from '@/lib/dashboardParamAssembly';
import type { DashboardParam, PageParam } from '@/stores/dashboardStore';

const PARAMS = [
  { name: 'start_time', label: '开始日期', type: 'string' as const, default: '' },
  { name: 'level', label: '级别', type: 'select' as const, default: '', options: ['高', '中'] },
];
const PAGE = [{ name: 'site', label: '区域', type: 'string' as const, default: '' }];
const CHARTS = [{ chart_type: 'widget_daterange', config: { paramKey: 'date_range', bind_param: 'start_time,end_time' } }];

function renderManager(over: Record<string, any> = {}) {
  const onChange = over.onChange ?? vi.fn();
  render(<DashboardParamsManager params={PARAMS} pageParams={PAGE} charts={CHARTS}
    onChange={onChange} {...over} />);
  return { onChange };
}

describe('DashboardParamsManager', () => {
  it('两套参数都呈现，控件已驱动的键带显式标注', () => {
    renderManager();
    expect(screen.getByText('遗留查询参数')).toBeInTheDocument();
    expect(screen.getByText('看板参数')).toBeInTheDocument();
    expect(screen.getByLabelText('参数名 开始日期')).toHaveValue('start_time');
    expect(screen.getByLabelText('参数名 区域')).toHaveValue('site');
    expect(screen.getByText('画布控件已驱动')).toBeInTheDocument();
  });

  it('形状下拉用统一业务名，遗留参数也能选「日期区间」', () => {
    renderManager();
    fireEvent.click(screen.getByLabelText('形状 start_time'));
    for (const label of ['日期区间', '日期', '选择框', '数字', '文本']) {
      expect(screen.getByRole('option', { name: label })).toBeInTheDocument();
    }
  });

  it('改形状只动 type，默认值与其余字段原样保留', () => {
    const { onChange } = renderManager();
    fireEvent.click(screen.getByLabelText('形状 start_time'));
    fireEvent.click(screen.getByRole('option', { name: '日期' }));
    const next = onChange.mock.calls[0][0];
    expect(next.params[0]).toEqual({ name: 'start_time', label: '开始日期', type: 'date', default: '' });
    expect(next.page_params).toEqual(PAGE);
  });

  it('删除与添加如实回传两套定义', () => {
    const { onChange } = renderManager();
    fireEvent.click(screen.getByRole('button', { name: '删除参数 级别' }));
    expect(onChange.mock.calls[0][0].params.map((p: any) => p.name)).toEqual(['start_time']);
    fireEvent.click(screen.getByRole('button', { name: /添加看板参数/ }));
    expect(onChange.mock.calls[1][0].page_params).toHaveLength(2);
  });

  it('编辑器入口给「转成控件」，回传参数与形状', () => {
    const onConvertToControl = vi.fn();
    renderManager({ onConvertToControl });
    fireEvent.click(screen.getAllByRole('button', { name: /转成控件/ })[0]);
    expect(onConvertToControl).toHaveBeenCalledWith(expect.objectContaining({ name: 'start_time' }), 'string');
  });

  it('列表页没有画布：不给按了没反应的「转成控件」按钮', () => {
    render(<DashboardParamsManager params={PARAMS} pageParams={PAGE} charts={CHARTS} onChange={() => {}} />);
    expect(screen.queryAllByRole('button', { name: /转成控件/ })).toHaveLength(0);
  });

  it('参数名重复或不合法时显式报错（role=alert）', () => {
    // 受控组件：校验要看用户刚打的字，所以测试里用一份真状态接住 onChange
    function Harness() {
      const [params, setParams] = useState<DashboardParam[]>(PARAMS);
      const [pageParams, setPageParams] = useState<PageParam[]>(PAGE);
      return <DashboardParamsManager params={params} pageParams={pageParams} charts={CHARTS}
        onChange={next => { setParams(next.params); setPageParams(next.page_params); }} />;
    }
    render(<Harness />);
    fireEvent.change(screen.getByLabelText('参数名 开始日期'), { target: { value: 'site' } });
    expect(screen.getByRole('alert')).toHaveTextContent('参数名需唯一');
    fireEvent.change(screen.getByLabelText('参数名 开始日期'), { target: { value: 'stat_window' } });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('选择框形状没配候选值要提示，并允许录入候选', () => {
    const onChange = vi.fn();
    render(<DashboardParamsManager pageParams={[]}
      params={[{ name: 'level', label: '级别', type: 'select', default: '', options: [] }]}
      charts={[]} onChange={onChange} />);
    expect(screen.getByText(/未配置候选值/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('候选值 level'), { target: { value: '高,中,低' } });
    expect(onChange.mock.calls[0][0].params[0].options).toEqual(['高', '中', '低']);
  });

  it('行模型与两套定义往返不丢字段（placeholder 等历史键保留）', () => {
    const legacy = [{ name: 'kw', label: '关键词', type: 'text' as const, default: '', placeholder: '输入序列号' }];
    const rows = toParamRows(legacy, []);
    expect(rows[0].shape).toBe('string');            // text 归一到统一词汇
    expect(fromParamRows(rows, legacy, []).params[0]).toEqual({ ...legacy[0], type: 'string', label: '关键词' });
    expect(PARAM_SHAPES).toHaveLength(5);
  });
});
