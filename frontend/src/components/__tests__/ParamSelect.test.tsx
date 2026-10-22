import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ParamNameSelect, WidgetOptionsEditor } from '../editor/ParamSelect';
import type { PageParam } from '@/stores/dashboardStore';
import client from '@/api/client';

const params: PageParam[] = [
  { name: 'date_start', type: 'date', default: '', label: '开始日期' },
  { name: 'site', type: 'string', default: '', label: '站点' },
];

beforeEach(() => { vi.spyOn(client, 'get').mockResolvedValue({ data: { items: [] } }); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('页面参数选择化', () => {
  it('选项显示业务名，参数名作为次要标识', () => {
    render(<ParamNameSelect label="参数 Key" value="" params={params} onChange={() => {}} onCreate={() => {}} />);
    fireEvent.click(screen.getByRole('combobox'));
    expect(screen.getByText('开始日期')).toBeTruthy();
    expect(screen.getByText('date_start')).toBeTruthy();
  });

  it('选中后回写参数名（value 用 name 是实现细节）', () => {
    const onChange = vi.fn();
    render(<ParamNameSelect label="参数 Key" value="" params={params} onChange={onChange} onCreate={() => {}} />);
    fireEvent.click(screen.getByRole('combobox'));
    fireEvent.click(screen.getByText('站点'));
    expect(onChange).toHaveBeenCalledWith('site');
  });

  it('引用了未定义的参数必须红标提醒，不静默失效', () => {
    render(<ParamNameSelect label="参数 Key" value="typo_site" params={params} onChange={() => {}} onCreate={() => {}} />);
    expect(screen.getByRole('alert').textContent).toMatch(/不会生效/);
    // 原值保留可改，不被选择框吞掉
    expect(screen.getByLabelText('参数 Key（未定义）')).toHaveValue('typo_site');
  });

  it('可就地新建参数并自动选中', () => {
    const onChange = vi.fn(), onCreate = vi.fn();
    render(<ParamNameSelect label="参数 Key" value="" params={params} onChange={onChange} onCreate={onCreate} />);
    fireEvent.click(screen.getByRole('combobox'));
    fireEvent.click(screen.getByText('＋ 新建参数…'));
    fireEvent.change(screen.getByPlaceholderText('如 date_start'), { target: { value: 'region' } });
    fireEvent.click(screen.getByText('创建并选用'));
    expect(onCreate).toHaveBeenCalledWith({ name: 'region', type: 'string', default: '', label: 'region' });
    expect(onChange).toHaveBeenCalledWith('region');
  });

  it('参数名非法或重名时创建按钮不可用', () => {
    render(<ParamNameSelect label="参数 Key" value="" params={params} onChange={() => {}} onCreate={() => {}} />);
    fireEvent.click(screen.getByRole('combobox'));
    fireEvent.click(screen.getByText('＋ 新建参数…'));
    const create = screen.getByText('创建并选用').closest('button')!;
    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText('如 date_start'), { target: { value: '1abc' } });
    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText('如 date_start'), { target: { value: 'site' } });
    expect(screen.getByText('参数名已存在')).toBeTruthy();
    expect(create.disabled).toBe(true);
  });
});

describe('控件选项结构化', () => {
  it('手动录入支持显示名与取值不同', () => {
    const onChange = vi.fn();
    render(<WidgetOptionsEditor value={[{ label: '待处理', value: '0' }]} onChange={onChange} />);
    expect(screen.getByDisplayValue('待处理')).toBeTruthy();
    expect(screen.getByDisplayValue('0')).toBeTruthy();
    fireEvent.change(screen.getByDisplayValue('待处理'), { target: { value: '未开始' } });
    expect(onChange).toHaveBeenCalledWith([{ label: '未开始', value: '0' }]);
  });

  it('含逗号的选项不被截断（旧逗号串会静默丢数据）', () => {
    const value = [{ label: '北京市, 天津市', value: 'bj,tj' }];
    render(<WidgetOptionsEditor value={value} onChange={() => {}} />);
    expect(screen.getByDisplayValue('北京市, 天津市')).toBeTruthy();
    expect(screen.getByDisplayValue('bj,tj')).toBeTruthy();
  });

  it('可增删选项行', () => {
    const onChange = vi.fn();
    render(<WidgetOptionsEditor value={[{ label: 'A', value: 'A' }]} onChange={onChange} />);
    fireEvent.click(screen.getByText('添加选项'));
    expect(onChange).toHaveBeenCalledWith([{ label: 'A', value: 'A' }, { label: '', value: '' }]);
    fireEvent.click(screen.getByLabelText('删除选项 1'));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it('可从维度字典 value_labels 取候选', async () => {
    vi.mocked(client.get).mockResolvedValue({ data: [{ id: 3, name: '案件状态', value_labels: { '0': '待处理', '1': '已办结' } }] } as any);
    const onChange = vi.fn();
    render(<WidgetOptionsEditor value={[]} onChange={onChange} />);
    const source = screen.getByRole('combobox', { name: /选项来源/i });
    fireEvent.click(source);
    fireEvent.click(screen.getByText('维度字典'));
    const dict = await screen.findByRole('combobox', { name: /选择维度字典/i });
    fireEvent.click(dict);
    fireEvent.click(await screen.findByText('案件状态'));
    expect(onChange).toHaveBeenCalledWith([{ label: '待处理', value: '0' }, { label: '已办结', value: '1' }]);
  });
});
