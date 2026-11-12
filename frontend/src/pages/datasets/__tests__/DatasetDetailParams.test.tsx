/**
 * 数据集页「入参」面板：入参声明的可见性与改判入口。
 *
 * 锁四件事：
 *  1. 声明了什么入参、在正文里干什么、看板上会长成什么，必须在这一页看得见
 *     （过去只能靠建模人员手打 JSON，看板侧只能猜名字）；
 *  2. 改判（归属逻辑参数 / 显示名）保存时**只动 dash_key 与 label**，
 *     type/default/values 等口径字段必须原样保留（入参声明是数据集的口径载体）；
 *  3. 分歧与"正文没声明入参"要显式说出来，不给"看起来没参数=不需要参数"的误读；
 *  4. 控件业务名与 `CHART_TYPES` 同源（漂移过一次：控件样式挂不上）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import client from '@/api/client';
import { ParamsPanel } from '../DatasetDetail';
import { CONTROL_LABELS } from '@/lib/dashboardParamAssembly';
import { CHART_TYPES } from '@/components/DashboardChart';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn() } }));

const PLAN = [{
  key: 'time_window', label: '统计时间窗口', kind: 'time_window', value_type: 'date_range',
  control: 'widget_daterange', options: [], required: false, default: null, referenced: true,
  conflicts: [], pending: false, suggested_label: '统计时间窗口', fallback_days: 30,
  targets: [
    { var: 'start_time', role: 'range_start', type: 'string', default: '', time_evidence: true, dash_key: '', label_declared: false },
    { var: 'end_time', role: 'range_end', type: 'string', default: '', time_evidence: true, dash_key: '', label_declared: false },
    { var: 'days', role: 'window_len', type: 'int', default: 30, time_evidence: true, dash_key: '', label_declared: false },
  ],
}, {
  key: 'window_sec', label: 'window_sec', kind: 'constant', value_type: 'number', control: '',
  options: [], required: false, default: 1800, referenced: true, conflicts: [], pending: false,
  suggested_label: '口径常数', fallback_days: null,
  targets: [{ var: 'window_sec', role: 'func_arg', type: 'int', default: 1800, time_evidence: false, dash_key: '', label_declared: false }],
}];

const VARIABLES = JSON.stringify({
  start_time: { type: 'string', default: '' },
  end_time: { type: 'string', default: '' },
  days: { type: 'int', default: 30 },
  window_sec: { type: 'int', default: 1800 },
});

function renderPanel(over: Record<string, any> = {}) {
  return render(<ParamsPanel datasetId={41} canManage onSaved={() => {}}
    ds={{ param_plan: PLAN, variables: VARIABLES, name: '版本统计·App 版本分布' }} {...over} />);
}

beforeEach(() => {
  (client.get as any).mockReset().mockResolvedValue({ data: { items: [] } });
  (client.put as any).mockReset().mockResolvedValue({ data: {} });
});
afterEach(() => vi.restoreAllMocks());

describe('DatasetDetail 入参面板', () => {
  it('显示每个入参在正文里的作用与看板形状（业务名，不是术语）', () => {
    renderPanel();
    expect(screen.getByText('start_time')).toBeInTheDocument();
    expect(screen.getByText('区间起点')).toBeInTheDocument();
    expect(screen.getByText('相对窗口长度')).toBeInTheDocument();
    // 组级信息只出现一次（显示名属于逻辑参数整组，不是每个变量一份）
    expect(screen.getAllByText('统计时间窗口')).toHaveLength(1);
    expect(screen.getByText(/日期区间 · 看板控件：日期范围/)).toBeInTheDocument();
    // 口径常数不冒充筛选器
    expect(screen.getByText('函数实参（口径常数）')).toBeInTheDocument();
    expect(screen.getByText(/不自动建控件/)).toBeInTheDocument();
  });

  it('未登记归属时说明"留空=按形状推断为哪个键"，不让人以为必须手填', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByLabelText('归属逻辑参数 days')).toHaveAttribute('placeholder', 'time_window'));
    expect(screen.getAllByText(/留空 = 按正文形状归到 time_window/).length).toBeGreaterThan(0);
  });

  it('保存只改 dash_key/label，其余口径字段原样保留', async () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText('归属逻辑参数 start_time'), { target: { value: 'stat_window' } });
    fireEvent.change(screen.getByLabelText('看板显示名 time_window'), { target: { value: '统计区间' } });
    fireEvent.click(screen.getByRole('button', { name: /保存入参归属（2 处改动）/ }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    const [path, payload] = (client.put as any).mock.calls[0];
    expect(path).toBe('/datasets/41');
    const saved = JSON.parse(payload.variables);
    expect(saved.start_time).toEqual({ type: 'string', default: '', dash_key: 'stat_window', label: '统计区间' });
    // 显示名落整组：end_time/days 同组也带上，window_sec 那组没登记就不写
    expect(saved.end_time.label).toBe('统计区间');
    expect(saved.days.label).toBe('统计区间');
    expect(saved.window_sec).toEqual({ type: 'int', default: 1800 });
    expect(Object.keys(saved)).toEqual(['start_time', 'end_time', 'days', 'window_sec']);
  });

  it('改回空值即撤销登记（回到"按形状推断"）', async () => {
    render(<ParamsPanel datasetId={41} canManage onSaved={() => {}}
      ds={{ param_plan: [{ ...PLAN[0], targets: [{ ...PLAN[0].targets[0], dash_key: 'other_key' }] }] ,
            variables: JSON.stringify({ start_time: { type: 'string', default: '', dash_key: 'other_key' } }) }} />);
    fireEvent.change(screen.getByLabelText('归属逻辑参数 start_time'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /保存入参归属/ }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    const saved = JSON.parse((client.put as any).mock.calls[0][1].variables);
    expect(saved.start_time).toEqual({ type: 'string', default: '' });
  });

  it('有入参没声明时给出可执行的空态（不写"无需参数"）', () => {
    render(<ParamsPanel datasetId={1} canManage onSaved={() => {}} ds={{ param_plan: [], variables: '{}' }} />);
    expect(screen.getByText(/该数据集没有声明入参/)).toBeInTheDocument();
    expect(screen.getByText(/取数会按正文默认值执行/)).toBeInTheDocument();
  });

  it('分歧显式暴露（role=alert），不只在计划里躺着', () => {
    const plan = [{ ...PLAN[0], conflicts: ['入参 start_time 登记的逻辑参数名「统计 区间」不合法'] }];
    render(<ParamsPanel datasetId={41} canManage onSaved={() => {}}
      ds={{ param_plan: plan, variables: VARIABLES }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('不合法');
  });

  it('无管理权限时只读：没有输入框也没有保存按钮', () => {
    render(<ParamsPanel datasetId={41} canManage={false} onSaved={() => {}}
      ds={{ param_plan: PLAN, variables: VARIABLES }} />);
    expect(screen.queryByLabelText('归属逻辑参数 start_time')).toBeNull();
    expect(screen.queryByRole('button', { name: /保存入参归属/ })).toBeNull();
    expect(screen.getAllByText('time_window').length).toBeGreaterThan(0);   // 推断出的键仍可见
  });

  it('候选逻辑参数来自清单聚合（可下拉可选也可新建）', async () => {
    (client.get as any).mockResolvedValue({ data: { items: [
      { param_summary: [{ key: 'site_region' }] }, { param_summary: [{ key: 'time_window' }] },
    ] } });
    renderPanel();
    await waitFor(() => expect(screen.getByLabelText('归属逻辑参数 days')).toHaveAttribute('list', 'adh-param-key-candidates'));
    const options = Array.from(document.querySelectorAll<HTMLSelectElement>('#adh-param-key-candidates option')).map(o => o.value);
    expect(options).toContain('site_region');
  });
});

describe('控件业务名与 CHART_TYPES 同源', () => {
  it('装配用的控件名必须就是清单项里的名字', () => {
    for (const [value, label] of Object.entries(CONTROL_LABELS)) {
      const item = CHART_TYPES.find(c => c.value === value);
      expect(item, `CHART_TYPES 缺少控件 ${value}`).toBeDefined();
      expect(item!.label).toBe(label);
    }
  });
});

describe('入参值域（站点 → 字段取值翻译的判据）', () => {
  // 现网形态：看板选「新加坡站点」（site_code=int），但业务表 site 列存的是 global
  const SITE_PLAN = [{
    key: 'site', label: '区域', kind: 'scalar', value_type: 'string', control: 'widget_select',
    options: [], required: false, default: '', referenced: true, conflicts: [], pending: false,
    suggested_label: '区域', fallback_days: null, value_domain: 'site',
    targets: [{ var: 'site', role: 'eq', type: 'string', default: '', time_evidence: false,
      dash_key: '', label_declared: false, value_domain: 'site' }],
  }];
  const SITE_VARS = JSON.stringify({ site: { type: 'string', default: '', value_domain: 'site' } });

  const renderSite = (canManage = true) => render(<ParamsPanel datasetId={33} canManage={canManage}
    onSaved={() => {}} ds={{ param_plan: SITE_PLAN, variables: SITE_VARS, name: '异常上报·CASE 明细' }} />);

  it('已登记为站点的入参在行上显示值域', async () => {
    renderSite();
    expect(await screen.findByRole('combobox', { name: '入参值域 site' })).toHaveTextContent('值域：站点');
  });

  it('改成站点后保存只加 value_domain，口径字段原样保留', async () => {
    render(<ParamsPanel datasetId={33} canManage onSaved={() => {}}
      ds={{ param_plan: [{ ...SITE_PLAN[0], value_domain: '', targets: [{ ...SITE_PLAN[0].targets[0], value_domain: '' }] }],
            variables: JSON.stringify({ site: { type: 'string', default: '' } }) }} />);
    const trigger = await screen.findByRole('combobox', { name: '入参值域 site' });
    await userEvent.click(trigger);
    await userEvent.click(await screen.findByRole('option', { name: '值域：站点' }));
    fireEvent.click(screen.getByRole('button', { name: /保存入参归属/ }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    const saved = JSON.parse((client.put as any).mock.calls[0][1].variables);
    expect(saved.site).toEqual({ type: 'string', default: '', value_domain: 'site' });
  });

  it('改回普通业务值即撤销登记（不给人留个隐式翻译开关）', async () => {
    renderSite();
    await userEvent.click(await screen.findByRole('combobox', { name: '入参值域 site' }));
    await userEvent.click(await screen.findByRole('option', { name: '值域：普通业务值' }));
    fireEvent.click(screen.getByRole('button', { name: /保存入参归属/ }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    const saved = JSON.parse((client.put as any).mock.calls[0][1].variables);
    expect(saved.site).toEqual({ type: 'string', default: '' });
  });

  it('无管理权限时值域只读展示（不出现可编辑控件）', async () => {
    renderSite(false);
    expect(screen.queryByRole('combobox', { name: '入参值域 site' })).toBeNull();
    expect(await screen.findByText('值域：站点')).toBeInTheDocument();
  });
});
