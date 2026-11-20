import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ObjectRefsPanel from '../ObjectRefsPanel';
import { metricsApi } from '@/api/metrics';

/**
 * 对象检查器「指标」/「维度」页签：展示并**就地编辑**该对象挂接的口径。
 *
 * 钉住五件事：
 * 1. 指标与维度是**两个页签**（各占一段），不在一列里上下堆叠；
 * 2. 过滤由服务端按 `bound_object_key` 下推（不得前端拉全量再筛），停用行可见；
 * 3. 按段懒加载：切到指标页签不请求维度，反之亦然；
 * 4. 就地编辑复用字典页的同一套弹窗与同一写接口（metricsApi.update），
 *    保存后重拉当前段 —— 不存在第二套写入口；新建时归属对象预填当前对象；
 * 5. 加载失败显式可见 + 可重试，不得伪装成"该对象没有挂口径"。
 */

vi.mock('@/api/metrics', () => ({
  metricsApi: {
    list: vi.fn(), filters: vi.fn(), listAllDimensions: vi.fn(),
    create: vi.fn(), update: vi.fn(),
    createDimension: vi.fn(), updateDimension: vi.fn(),
  },
}));

const mockList = vi.mocked(metricsApi.list);
const mockFilters = vi.mocked(metricsApi.filters);
const mockDims = vi.mocked(metricsApi.listAllDimensions);
const mockUpdate = vi.mocked(metricsApi.update);
const mockCreate = vi.mocked(metricsApi.create);

const metric = (over: Record<string, unknown> = {}) => ({
  id: 1, name: '事件总数', display_name: '事件总数', name_en: 'event_count',
  description: '', category: '可观测', tags: ['基础'], calculation_type: 'COUNT',
  expression: '', unit: '个', target_table: '', target_column: '', granularity: '',
  owner: '', bound_object_key: 'rum_event', aliases: '[]', certified: 0, is_active: 1,
  ...over,
});

const dimension = (over: Record<string, unknown> = {}) => ({
  id: 11, name: '事件类型', name_en: 'event_type', category: '属性', level: 0,
  hierarchy: '', target_table: 'rum_event', target_column: 'type',
  bound_object_key: 'rum_event', aliases: null, value_labels: null,
  certified: 0, is_active: 1, datasource_id: 0, description: '',
  ...over,
});

const OBJECTS = [
  { key: 'rum_event', display_name: 'RUM观测事件' },
  { key: 'case', display_name: '案例' },
];

const renderPanel = (section: 'metrics' | 'dimensions' = 'metrics', canEdit = true) => render(
  <MemoryRouter>
    <ObjectRefsPanel section={section} objectKey="rum_event" modelId={7} objects={OBJECTS} canEdit={canEdit} />
  </MemoryRouter>,
);

beforeEach(() => {
  mockList.mockResolvedValue({
    data: { total: 2, items: [metric(), metric({ id: 2, name: '退号数', display_name: '退号数', is_active: 0 })] },
  } as never);
  mockDims.mockResolvedValue({
    data: { total: 1, items: [dimension()] },
  } as never);
  // 弹窗自拉词表（调用方未传 facets）：归属对象候选来自本次传入的 objects，此处只需空词表不报错
  mockFilters.mockResolvedValue({ data: { metrics: { categories: [], tags: [] }, objects: [] } } as never);
  mockUpdate.mockResolvedValue({ data: {} } as never);
  mockCreate.mockResolvedValue({ data: {} } as never);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('ObjectRefsPanel — 指标/维度分两个页签，各拉各的', () => {
  it('指标页签：按 bound_object_key 下推拉指标，不请求维度', async () => {
    renderPanel('metrics');
    expect(await screen.findByText('事件总数')).toBeTruthy();
    await waitFor(() => expect(mockList).toHaveBeenCalledWith({ bound_object_key: 'rum_event', size: 200 }));
    expect(mockDims).not.toHaveBeenCalled();
    expect(screen.getByText('指标（2）')).toBeTruthy();
    // 另一段不得同屏（分页签而非堆一列）
    expect(screen.queryByText(/^维度（/)).toBeNull();
  });

  it('维度页签：按 bound_object_key + 含停用行拉维度，不请求指标', async () => {
    renderPanel('dimensions');
    expect(await screen.findByText('事件类型')).toBeTruthy();
    await waitFor(() => expect(mockDims).toHaveBeenCalledWith({ bound_object_key: 'rum_event', is_active: 'all' }));
    expect(mockList).not.toHaveBeenCalled();
    expect(screen.getByText('维度（1）')).toBeTruthy();
    expect(screen.queryByText(/^指标（/)).toBeNull();
  });

  it('行内显示业务名 + 停用徽标（指标段），维度段副标题给物理列', async () => {
    renderPanel('metrics');
    expect(await screen.findByText('退号数')).toBeTruthy();
    // 停用行必须看得见（否则“停用了”在建模视角等于“消失了”）
    expect(screen.getByText('已停用')).toBeTruthy();

    cleanup();
    renderPanel('dimensions');
    expect(await screen.findByText('事件类型')).toBeTruthy();
    // 主展示是业务名，物理列只作次要信息（排查用）
    expect(screen.getByText('rum_event.type')).toBeTruthy();
  });

  it('无编辑权限时只读展示，不给假按钮', async () => {
    renderPanel('metrics', false);
    await screen.findByText('事件总数');
    expect(screen.queryByRole('button', { name: /编辑指标/ })).toBeNull();
    expect(screen.queryByRole('button', { name: '新建指标' })).toBeNull();

    cleanup();
    renderPanel('dimensions', false);
    await screen.findByText('事件类型');
    expect(screen.queryByRole('button', { name: /编辑维度/ })).toBeNull();
    expect(screen.queryByRole('button', { name: '新建维度' })).toBeNull();
  });
});

describe('ObjectRefsPanel — 就地编辑复用字典同一份表单/接口', () => {
  it('编辑指标：弹窗回填后保存走 metricsApi.update，并重拉当前段', async () => {
    renderPanel('metrics');
    await screen.findByText('事件总数');
    await userEvent.click(screen.getByRole('button', { name: '编辑指标 事件总数' }));
    expect(await screen.findByRole('dialog', { name: '编辑指标' })).toBeTruthy();

    // 显示名（表单里 name 与 display_name 同值，取第二列）
    const inputs = screen.getAllByDisplayValue('事件总数') as HTMLInputElement[];
    await userEvent.clear(inputs[inputs.length - 1]);
    await userEvent.type(inputs[inputs.length - 1], '事件总数（新）');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    const [id, payload] = mockUpdate.mock.calls[0];
    expect(id).toBe(1);
    expect(payload).toMatchObject({ display_name: '事件总数（新）', bound_object_key: 'rum_event' });
    // 保存后重拉（不是本地改一行了事）
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });

  it('新建指标：归属对象预填当前对象，未手选也写对 bound_object_key', async () => {
    renderPanel('metrics');
    await screen.findByText('事件总数');
    await userEvent.click(screen.getByRole('button', { name: '新建指标' }));
    // 归属对象选择框显示业务名 + key，且已预填当前对象（不必再选一次）
    expect(await screen.findByText('RUM观测事件 (rum_event)')).toBeTruthy();
    await userEvent.type(screen.getByPlaceholderText('如: gmv'), '退款率');
    await userEvent.type(screen.getByPlaceholderText('如: 交易总额'), '退款率');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(1));
    expect(mockCreate.mock.calls[0][0]).toMatchObject({
      name: '退款率', display_name: '退款率', bound_object_key: 'rum_event',
    });
  });

  it('编辑维度：复用字典页弹窗（标题含业务名）', async () => {
    renderPanel('dimensions');
    await screen.findByText('事件类型');
    await userEvent.click(screen.getByRole('button', { name: '编辑维度 事件类型' }));
    expect(await screen.findByRole('dialog', { name: '编辑维度 — 事件类型' })).toBeTruthy();
  });
});

describe('ObjectRefsPanel — 失败显式暴露（不伪装成空）', () => {
  it('维度页签加载失败：显式报错 + 可重试，不清空成“没有挂接维度”', async () => {
    mockDims.mockRejectedValueOnce({ response: { data: { detail: '连接超时' } } } as never);
    renderPanel('dimensions');
    expect(await screen.findByText(/连接超时/)).toBeTruthy();
    expect(screen.queryByText('该对象还没有挂接维度')).toBeNull();

    await userEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('事件类型')).toBeTruthy();
  });
});