import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import client from '@/api/client';
import { ES_SOURCE_LABEL } from '@/lib/datasetPickerFilter';
import Datasets from '../Datasets';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

const ES_ROW = {
  id: 900, name: '设备告警按天', description: '', source_type: 'es',
  es_pattern: 't-t_equipment_warning_log*', object_key: '', datasource_id: 0,
  datasource_name: '内置 Elasticsearch 通道',
  available_source_names: ['t-t_equipment_warning_log*'],
  usable_now: ['t-t_equipment_warning_log*'],
  chart_type: 'line', owner_id: 1, field_count: 2, references: 1, updated_at: '',
};

const PATTERNS_OK = {
  ready: true, state: 'ready', status_message: '',
  patterns: ['t-t_equipment_warning_log*', 'logs-*'],
  sql_hint: 'Elasticsearch SQL 是受限方言（以下均经本集群实测）',
};

// 内置 MCP 通道清单（GET /datasets/channels 的下发形状）：下拉里的这一项必须来自它，
// 而不是前端硬编码（否则新增一个能承载数据集的 MCP 服务就得改前端）。
const ES_CHANNEL = {
  value: 'mcp:elasticsearch', channel: 'es', server: 'elasticsearch',
  label: ES_SOURCE_LABEL, group: 'MCP 服务（内置）',
  ready: true, state: 'ready', status_message: '', open_view_count: 4,
};
const channelsOf = (...items: any[]) => ({ items: items.length ? items : [ES_CHANNEL] });
const CHANNELS_OK = channelsOf();

/** 按 URL 分派的假后端：数据集清单 + 数据源清单 + ES 通道事实（配置面与筛选面各自接口） */
/** 页面依赖 useNavigate，必须套 Router 上下文 */
function renderPage() {
  return render(<MemoryRouter><Datasets /></MemoryRouter>);
}

function route(options: any = PATTERNS_OK, channels: any = CHANNELS_OK) {
  (client.get as any).mockImplementation(async (url: string) => {
    if (url === '/datasets/') return { data: { items: [ES_ROW] } };
    if (url === '/datasources/') return { data: [] };
    if (url === '/datasets/es-patterns') return { data: options };
    if (url === '/datasets/channels') return { data: channels };
    if (url === '/dashboard/') return { data: [] };
    throw new Error(`unexpected GET ${url}`);
  });
}

beforeEach(() => { vi.clearAllMocks(); route(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('数据集页 — ES 型来源的显示与配置入口', () => {
  it('ES 数据集卡片显示业务名「ES 索引」与已选范围，不出现裸枚举 es', async () => {
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    expect(screen.getByText('ES 索引')).toBeInTheDocument();
    // 内部枚举不得当名称展示（ui-resource-display §1）
    expect(container.textContent).not.toMatch(/\bes\b/);
    expect(screen.getByText('t-t_equipment_warning_log*')).toBeInTheDocument();
  });

  it('ES 数据集的执行位显示通道名，而不是数据源 id 或"默认引擎"', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    expect(screen.getByText('内置 Elasticsearch 通道')).toBeInTheDocument();
    expect(screen.queryByText('默认引擎')).not.toBeInTheDocument();
  });

  it('新建弹窗选 ES 来源后：索引范围是选择框且选项来自后端清单（不是手输框）', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '新建数据集' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /Elasticsearch 索引/ }));

    // 选择框：必须能点开并从清单里选，页面不得要求手输模式名
    const trigger = within(dialog).getByRole('combobox', { name: /索引范围/ });
    expect(within(dialog).queryByRole('textbox', { name: /索引范围/ })).toBeNull();

    // Radix Select 在 jsdom 下要 pointer 事件序列才弹开（fireEvent.click 不生效，仓内同口径用 userEvent）
    await userEvent.click(trigger);
    expect(await screen.findByRole('option', { name: 'logs-*' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 't-t_equipment_warning_log*' })).toBeInTheDocument();
    // 选中后下拉收起，正文区仍能看到后端下发的方言提示（前端不另写一份实机校准结果）
    await userEvent.click(screen.getByRole('option', { name: 'logs-*' }));
    expect(within(dialog).getByRole('combobox', { name: /索引范围/ })).toHaveTextContent('logs-*');
    expect(within(dialog).getByText(/Elasticsearch SQL 是受限方言/)).toBeInTheDocument();
  });

  it('通道未就绪：显式给出原因，而不是渲成"没有索引"的空态', async () => {
    route({ ready: false, state: 'incomplete', status_message: '已启用但未开放任何索引范围（白名单为空，默认不开放）', patterns: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '新建数据集' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /Elasticsearch 索引/ }));

    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('未开放任何索引范围');
    expect(within(dialog).getByRole('combobox', { name: /索引范围/ })).toBeDisabled();
  });

  it('清单接口失败：提醒可重试，不静默留空下拉（配置缺口不能被读成"没数据"）', async () => {
    (client.get as any).mockImplementation(async (url: string) => {
      if (url === '/datasets/es-patterns') throw new Error('boom');
      if (url === '/datasets/') return { data: { items: [ES_ROW] } };
      if (url === '/datasources/') return { data: [] };
      if (url === '/dashboard/') return { data: [] };
      throw new Error(`unexpected GET ${url}`);
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '新建数据集' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /Elasticsearch 索引/ }));

    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('ES 通道状态获取失败');
    expect(within(dialog).getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('ES 模式不渲染"数据源/默认引擎"选择（它不挂在数据源上）', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '新建数据集' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /Elasticsearch 索引/ }));

    expect(within(dialog).queryByRole('combobox', { name: /数据源/ })).toBeNull();
    // 未脱敏/未套策略这件事必须在配置面说出来（不是在看板卡片上贴徽标）
    expect(within(dialog).getByText(/不施加列级\/行级数据权限/)).toBeInTheDocument();
  });
});

describe('筛选器要按取数通道（MCP）分维度，并能选到 ES 通道', () => {
  it('选中「内置 Elasticsearch 通道」后请求带 channel=mcp:elasticsearch（datasource_id 归 0，后端两者互斥）', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    (client.get as any).mockClear();
    await userEvent.click(screen.getByRole('combobox', { name: '按数据源或 MCP 通道筛选数据集' }));
    await userEvent.click(await screen.findByRole('option', { name: ES_SOURCE_LABEL }));
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/datasets/', {
      params: { keyword: '', datasource_id: 0, channel: 'mcp:elasticsearch' },
    }));
  });

  it('下拉按「MCP 服务」与「数据源」分组，选项来自后端清单而不是写死', async () => {
    // 再下发一项（本仓尚未登记的）MCP 服务：前端不得需要改动就能渲出这一项
    const EXTRA = { value: 'mcp:log_search', channel: 'mcp:log_search', server: 'log_search',
      label: '日志检索服务', group: 'MCP 服务（内置）',
      ready: true, state: 'ready', status_message: '', open_view_count: 2 };
    (client.get as any).mockImplementation(async (url: string) => {
      if (url === '/datasets/') return { data: { items: [] } };
      if (url === '/datasources/') return { data: [{ id: 11, name: 'prd-cn' }] };
      if (url === '/datasets/es-patterns') return { data: PATTERNS_OK };
      if (url === '/datasets/channels') return { data: channelsOf(ES_CHANNEL, EXTRA) };
      if (url === '/dashboard/') return { data: [] };
      throw new Error(`unexpected GET ${url}`);
    });
    renderPage();
    await userEvent.click(await screen.findByRole('combobox', { name: '按数据源或 MCP 通道筛选数据集' }));

    expect(await screen.findByText('MCP 服务（内置）')).toBeInTheDocument();
    expect(screen.getByText('数据源')).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: '日志检索服务' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'prd-cn' })).toBeInTheDocument();

    (client.get as any).mockClear();
    await userEvent.click(screen.getByRole('option', { name: '日志检索服务' }));
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/datasets/', {
      params: { keyword: '', datasource_id: 0, channel: 'mcp:log_search' },
    }));
  });

  it('选具体数据源时仍按 datasource_id 筛，不带 channel', async () => {
    (client.get as any).mockImplementation(async (url: string) => {
      if (url === '/datasets/') return { data: { items: [] } };
      if (url === '/datasources/') return { data: [{ id: 11, name: 'prd-cn' }] };
      if (url === '/datasets/es-patterns') return { data: PATTERNS_OK };
      if (url === '/datasets/channels') return { data: CHANNELS_OK };
      if (url === '/dashboard/') return { data: [] };
      throw new Error(`unexpected GET ${url}`);
    });
    renderPage();
    await userEvent.click(await screen.findByRole('combobox', { name: '按数据源或 MCP 通道筛选数据集' }));
    await userEvent.click(await screen.findByRole('option', { name: 'prd-cn' }));
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/datasets/', {
      params: { keyword: '', datasource_id: 11, channel: '' },
    }));
  });

  it('通道未就绪：选项禁用并给出原因，不让人选了拿到永远 0 的清单', async () => {
    route(
      { ready: false, state: 'incomplete', status_message: '已启用但未开放任何索引范围', patterns: [] },
      channelsOf({ ...ES_CHANNEL, ready: false, state: 'incomplete',
                   status_message: '已启用但未开放任何索引范围' }),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('已启用但未开放任何索引范围');
    await userEvent.click(screen.getByRole('combobox', { name: '按数据源或 MCP 通道筛选数据集' }));
    expect((await screen.findByRole('option', { name: ES_SOURCE_LABEL })).getAttribute('aria-disabled'))
      .toBe('true');
  });

  it('通道清单拉失败：显式可重试，不把选项静默抽掉（那会被读成“这个部署没有 ES”）', async () => {
    (client.get as any).mockImplementation(async (url: string) => {
      if (url === '/datasets/es-patterns') throw new Error('boom');
      if (url === '/datasets/channels') throw new Error('boom');
      if (url === '/datasets/') return { data: { items: [ES_ROW] } };
      if (url === '/datasources/') return { data: [] };
      if (url === '/dashboard/') return { data: [] };
      throw new Error(`unexpected GET ${url}`);
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('ES 通道状态获取失败');
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });
});
