/**
 * 参数装配确认弹窗：只验证"列得清楚、确认得干净"。
 *
 * 计划本身由 `lib/dashboardParamAssembly` 负责（有独立单测），这里是展示壳：
 *  1. 要补什么（参数 + 控件）必须逐项可读，动作用业务名说，不显示内部标识当主信息；
 *  2. 判不出形状 / 口径常数 / 分歧必须显式列出，不能悄悄略过；
 *  3. 取消勾选的项不回传（用户勾掉就真不做）；显示名以用户改过的为准。
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import DatasetParamAssemblyDialog from '../DatasetParamAssemblyDialog';
import { buildAssemblyPlan, type DatasetParamSummary } from '@/lib/dashboardParamAssembly';

const tw: DatasetParamSummary = {
  key: 'time_window', label: 'time_window', kind: 'time_window', value_type: 'date_range',
  control: 'widget_daterange', options: [], required: false, default: null, referenced: true,
  conflicts: [], pending: false, suggested_label: '统计时间窗口', fallback_days: 30,
  vars: [{ var: 'start_time', role: 'range_start', type: 'string' }],
};
const days: DatasetParamSummary = {
  key: 'range_days', label: 'range_days', kind: 'range', value_type: '', control: '',
  options: [], required: false, default: 30, referenced: true, conflicts: [], pending: true,
  suggested_label: '', vars: [{ var: 'days', role: 'range_start', type: 'int' }],
};
const constant: DatasetParamSummary = {
  key: 'window_sec', label: 'window_sec', kind: 'constant', value_type: 'number', control: '',
  options: [], required: false, default: 1800, referenced: true, conflicts: [], pending: false,
  suggested_label: '口径常数', vars: [{ var: 'window_sec', role: 'func_arg', type: 'int' }],
};

function renderDialog(plan = buildAssemblyPlan([{ id: 41, name: '版本统计·App 版本分布', param_summary: [tw, days, constant] }])) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(<DatasetParamAssemblyDialog open plan={plan} datasetNames={['版本统计·App 版本分布']}
    onCancel={onCancel} onConfirm={onConfirm} />);
  return { onConfirm, onCancel };
}

describe('DatasetParamAssemblyDialog', () => {
  it('列出要补的参数与控件，控件动作写业务名', () => {
    renderDialog();
    expect(screen.getByText('统计时间窗口')).toBeInTheDocument();
    expect(screen.getByText('time_window')).toBeInTheDocument();          // 键作为次要信息
    expect(screen.getByText(/放置「日期范围」/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /补齐 1 个参数并放置 1 个控件/ })).toBeInTheDocument();
  });

  it('判不出形状的不说成"已补齐"，而是列进待确认并给原因', () => {
    renderDialog();
    expect(screen.getByText(/1 项需要你先确认形态/)).toBeInTheDocument();
    expect(screen.getByText(/无法确定它是时间还是数值/)).toBeInTheDocument();
    expect(screen.queryByText(/放置.*数字输入框/)).toBeNull();
  });

  it('口径常数单独说明它不该做成控件', () => {
    renderDialog();
    expect(screen.getByText(/口径常数，不该做成看板控件/)).toBeInTheDocument();
    expect(screen.getByText(/「window_sec」（版本统计·App 版本分布）/)).toBeInTheDocument();
  });

  it('分歧随计划显示出来（不阻塞确认，但要看见）', () => {
    const plan = buildAssemblyPlan([
      { id: 1, name: 'A', param_summary: [tw] },
      { id: 2, name: 'B', param_summary: [{ ...tw, value_type: 'number', control: 'widget_number' }] },
    ]);
    renderDialog(plan);
    expect(screen.getByText(/值类型分歧|不一致/)).toBeInTheDocument();
  });

  it('取消勾选的项不回传，全勾光时不给空确认', () => {
    const { onConfirm } = renderDialog();
    fireEvent.click(screen.getByRole('checkbox', { name: '补齐参数 统计时间窗口' }));
    // 参数行全勾光但按钮还勾着 → 确认仍可用（只放按钮也是有效动作）
    expect(screen.getByRole('button', { name: /放置「搜索」「重置」按钮/ })).toBeEnabled();
    fireEvent.click(screen.getByRole('checkbox', { name: '同时放置搜索与重置按钮' }));
    const none = screen.getByRole('button', { name: /没有要补的内容/ });
    expect(none).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: '补齐参数 统计时间窗口' }));
    fireEvent.click(screen.getByRole('button', { name: /补齐 1 个参数并放置 1 个控件/ }));
    expect(onConfirm).toHaveBeenCalledWith(['time_window'], { time_window: '统计时间窗口' }, false);
  });

  it('改了显示名以用户输入为准（落参数与控件标签）', () => {
    const { onConfirm } = renderDialog();
    fireEvent.change(screen.getByLabelText('显示名 time_window'), { target: { value: '统计日期' } });
    fireEvent.click(screen.getByRole('button', { name: /补齐 1 个参数并放置 1 个控件/ }));
    expect(onConfirm).toHaveBeenCalledWith(['time_window'], { time_window: '统计日期' }, true);
  });

  it('缺「搜索/重置」时列出来并默认勾上，说明它们是参数的生效入口', () => {
    renderDialog();
    expect(screen.getByText(/同时放置「搜索」「重置」按钮/)).toBeInTheDocument();
    expect(screen.getByText(/没有这两个入口/)).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: '同时放置搜索与重置按钮' })).toBeChecked();
    expect(screen.getByRole('button', { name: /补齐 1 个参数并放置 1 个控件 \+ 2 个按钮/ })).toBeInTheDocument();
  });

  it('勾掉按钮后确认回传 withButtons=false（用户不要就不放）', () => {
    const { onConfirm } = renderDialog();
    fireEvent.click(screen.getByRole('checkbox', { name: '同时放置搜索与重置按钮' }));
    fireEvent.click(screen.getByRole('button', { name: /补齐 1 个参数并放置 1 个控件/ }));
    expect(onConfirm).toHaveBeenCalledWith(['time_window'], { time_window: '统计时间窗口' }, false);
  });

  it('画布已有按钮时只说明不重复放', () => {
    const plan = buildAssemblyPlan([{ id: 1, name: 'A', param_summary: [tw] }], [], [], ['widget_search', 'widget_reset']);
    render(<DatasetParamAssemblyDialog open plan={plan} datasetNames={['A']}
      onCancel={() => {}} onConfirm={() => {}} />);
    expect(screen.queryByRole('checkbox', { name: '同时放置搜索与重置按钮' })).toBeNull();
    expect(screen.getByText(/画布上已有「搜索」「重置」按钮，本次不重复放置/)).toBeInTheDocument();
  });

  it('只缺按钮时确认仍可用（参数已由控件驱动，不能只能取消）', () => {
    const plan = buildAssemblyPlan([{ id: 1, name: 'A', param_summary: [tw] }],
      [{ name: 'time_window', label: '统计时间窗口', type: 'date_range', default: '' }],
      ['time_window'], ['widget_daterange']);
    const onConfirm = vi.fn();
    render(<DatasetParamAssemblyDialog open plan={plan} datasetNames={['A']}
      onCancel={() => {}} onConfirm={onConfirm} />);
    const btn = screen.getByRole('button', { name: /放置「搜索」「重置」按钮/ });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledWith([], {}, true);
  });

  it('取消关闭时不写任何东西', () => {
    const { onConfirm, onCancel } = renderDialog();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(onCancel).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
