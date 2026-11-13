/**
 * 组件库「数据集」页的检索与过滤（清单事实源 = GET /datasets/，已按角色权限过滤）。
 *
 * 锁定：
 *  - 关键词命中 + 计数可见；筛选项只列清单里出现过的业务名（不硬编码枚举清单）；
 *  - 三种空态必须可区分：加载失败（显式报错 + 重试）≠ 权限为空（两层授权指引）
 *    ≠ 筛完无命中（提示改词/清除筛选）——把失败说成"没有数据集"是掩盖；
 *  - 「引入」一律按数据集业务名呈现并可回传整行（ui-resource-display：不裸显 id）；
 *  - 无可用源的行保持禁用，而不是被静默藏掉（用户要知道它存在但挂不得）。
 */
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ComponentLibrary from '../ComponentLibrary';
import { resolveDashboardDesign } from '@/lib/dashboardDesign';
import client from '@/api/client';

vi.mock('@/hooks/useVisLibrary', () => ({
  useVisLibrary: () => ({ items: [], loading: false, error: null, refresh: vi.fn() }),
}));

const rows = [
  { id: 8, name: 'ARMS·用户与会话趋势', description: '按日统计 UV/PV', source_type: 'sql',
    datasource_name: 'arms-prd-cn', available_source_names: ['arms-prd-cn'], usable_now: ['arms-prd-cn'],
    field_count: 5, field_config: JSON.stringify([{ label: '统计日期' }, { label: '独立访客' }]) },
  { id: 40, name: '网关访问日志', source_type: 'sql', is_es: true,
    datasource_name: 'cloud-platform-es', available_source_names: ['cloud-platform-es'],
    usable_now: ['cloud-platform-es'] },
  { id: 9, name: '待授权数据集', source_type: 'sql', datasource_name: 'prd-eu',
    available_source_names: ['prd-eu'], usable_now: [] },
];

const onAddFromDataset = vi.fn();

function renderPanel() {
  render(<ComponentLibrary
    allCharts={[]} selectedChart={null} isOpen onClose={() => {}} onAddItem={() => {}}
    onSelectChart={() => {}} onApplyVis={() => {}} onAddFromDataset={onAddFromDataset}
    design={resolveDashboardDesign({}, [], {})} layoutMode="page" dirtyIds={[]}
    onLayerAction={() => {}} tab="datasets" onTab={() => {}} />);
}

const listNames = () => screen.queryAllByRole('button', { name: /引入/ })
  .map(b => b.closest('div.flex.items-center')?.textContent || '');

beforeEach(() => {
  onAddFromDataset.mockClear();
  vi.spyOn(client, 'get').mockImplementation((url: string) =>
    Promise.resolve({ data: url === '/datasets/' ? { items: rows } : [] } as any));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('检索与过滤', () => {
  it('关键词命中单个数据集，计数行显示「命中 1 / 共 3」', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('ARMS·用户与会话趋势')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('搜索数据集'), { target: { value: '会话' } });
    expect(screen.getByText(/命中 1 \/ 共 3/)).toBeTruthy();
    expect(screen.queryByText('网关访问日志')).toBeNull();
  });

  it('字段业务名也参与命中（不靠记住数据集名称）', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('ARMS·用户与会话趋势')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('搜索数据集'), { target: { value: '独立访客' } });
    expect(screen.getByText('ARMS·用户与会话趋势')).toBeTruthy();
  });

  it('数据源筛选按业务名给选项，选中后只留能在该源执行的数据集', async () => {
    renderPanel();
    const sourceSelect = await screen.findByLabelText('按数据源筛选数据集');
    fireEvent.change(sourceSelect, { target: { value: 'arms-prd-cn' } });
    expect(screen.getByText('ARMS·用户与会话趋势')).toBeTruthy();
    expect(screen.queryByText('网关访问日志')).toBeNull();
    expect(screen.queryByText('待授权数据集')).toBeNull();
    // 选项来自清单：prd-eu 不在任何行的 usable_now 里 → 不作为源选项；
    // ES 型数据集就挂在 ES 数据源上，与其余源同列一处（内置通道那一维已退役，不得凭空长一项）
    expect(Array.from(sourceSelect.querySelectorAll('option')).map(o => o.textContent))
      .toEqual(['全部数据源', 'arms-prd-cn', 'cloud-platform-es']);
  });

  it('按 ES 型数据源筛即命中 ES 数据集（不需要也不得有“不属于任何源”的通道项）', async () => {
    renderPanel();
    const sourceSelect = await screen.findByLabelText('按数据源筛选数据集');
    fireEvent.change(sourceSelect, { target: { value: 'cloud-platform-es' } });
    expect(screen.getByText('网关访问日志')).toBeTruthy();
    expect(screen.queryByText('ARMS·用户与会话趋势')).toBeNull();
    expect(screen.getByText(/命中 1 \/ 共 3/)).toBeTruthy();
  });

  it('类型筛选只列清单里真实出现的通道类型，且显示业务名', async () => {
    renderPanel();
    const kindSelect = await screen.findByLabelText('按类型筛选数据集');
    const labels = Array.from(kindSelect.querySelectorAll('option')).map(o => o.textContent);
    expect(labels).toEqual(['全部类型', 'SQL 数据集', '日志/ES 数据集']);   // 清单没有语义数据集
    fireEvent.change(kindSelect, { target: { value: 'es' } });
    expect(screen.getByText('网关访问日志')).toBeTruthy();
    expect(screen.queryByText('ARMS·用户与会话趋势')).toBeNull();
  });

  it('「仅看可引入」筛掉无可用源的行；未勾选时该行仍可见但按钮禁用（不静默藏缺陷）', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('待授权数据集')).toBeTruthy());
    const pending = screen.getByText('待授权数据集').closest('div.flex.items-center')!;
    expect(pending.querySelector('button')).toBeDisabled();
    fireEvent.click(screen.getByLabelText('仅看可引入'));
    expect(screen.queryByText('待授权数据集')).toBeNull();
    expect(screen.getByText(/命中 2 \/ 共 3/)).toBeTruthy();
  });

  it('清除筛选一键回到全量', async () => {
    renderPanel();
    fireEvent.change(await screen.findByLabelText('搜索数据集'), { target: { value: '会话' } });
    fireEvent.click(screen.getByRole('button', { name: /清除筛选条件/ }));
    expect(screen.getByText('网关访问日志')).toBeTruthy();
    expect((screen.getByLabelText('搜索数据集') as HTMLInputElement).value).toBe('');
  });

  it('点「引入」回传整行数据（含业务名），供编辑器生成图表卡片', async () => {
    renderPanel();
    fireEvent.change(await screen.findByLabelText('搜索数据集'), { target: { value: '网关' } });
    fireEvent.click(screen.getByRole('button', { name: /引入/ }));
    expect(onAddFromDataset).toHaveBeenCalledWith(expect.objectContaining({ id: 40, name: '网关访问日志' }));
  });

  it('清单显示业务名而不是裸 id', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('ARMS·用户与会话趋势')).toBeTruthy());
    expect(listNames().join(' ')).not.toMatch(/\b(id:?\s*)?8\b/);
  });
});

describe('三种空态可区分', () => {
  it('加载失败：显式报错 + 重试，不得伪装成「没有数据集」', async () => {
    vi.mocked(client.get).mockRejectedValueOnce(new Error('boom'));
    renderPanel();
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('数据集清单加载失败');
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy();
    expect(screen.queryByText(/当前角色没有可引入的数据集/)).toBeNull();
  });

  it('权限为空：给出「数据集授权 ∧ 数据源授权」两层指引，而不是「请先创建」', async () => {
    vi.mocked(client.get).mockImplementation((url: string) =>
      Promise.resolve({ data: url === '/datasets/' ? { items: [] } : [] } as any));
    renderPanel();
    const hint = await screen.findByText(/当前角色没有可引入的数据集/);
    expect(hint.textContent).toContain('角色权限 → 数据集');
    expect(hint.textContent).toContain('角色权限 → 数据源');
    expect(screen.queryByText(/无匹配结果/)).toBeNull();
  });

  it('筛完无命中：说明是筛选条件问题（带清单总数），不冒充权限结论', async () => {
    renderPanel();
    fireEvent.change(await screen.findByLabelText('搜索数据集'), { target: { value: '这个词不存在' } });
    const empty = await screen.findByText(/无匹配结果/);
    expect(empty.textContent).toContain('清单共 3 个');
    expect(screen.queryByText(/当前角色没有可引入的数据集/)).toBeNull();
  });
});
