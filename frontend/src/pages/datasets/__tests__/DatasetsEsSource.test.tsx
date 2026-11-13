/** 数据集清单页 — ES 形态的显示与筛选（内置通道退役后）。
 *
 * 退役前这一页有两条 ES 口径：卡片显「内置 Elasticsearch 通道 + 索引范围」，筛选下拉从
 * `/datasets/channels` 拉内置 MCP 服务清单（就绪态、不可用原因都在那儿）。阶段三把内置通道
 * 整体下架后，ES 数据集就是一条普通数据源型数据集：
 *  - 显示：执行数据源业务名 + 服务端 `is_es` 给的「ES 通道」标注，不得再出现索引范围与"内置"字样；
 *  - 筛选：只有一个维度 = 按数据源（`datasource_id`）。“ES 取数通道”那一项已于 2026-11-13 从
 *    前端撤掉：现网只 1 个 ES 源时它与直接选 `cloud-platform-es` 结果逐字相同，而“取数通道”
 *    这个词会被读成“还有一条不走数据源的取数路”（实际发生过误读）；
 *  - 配置面端点 `/datasets/channels`·`/datasets/es-patterns` 不得再被请求（前端留一次调用
 *    就是给退役机件留一条复活路径）。
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import client from '@/api/client';
import Datasets from '../Datasets';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

/** 终态形状：source_type='sql' + ES 型数据源 + 服务端下发 is_es */
const ES_ROW = {
  id: 900, name: '设备告警按天', description: '', source_type: 'sql', is_es: true,
  object_key: '', datasource_id: 1794535612029, datasource_name: 'cloud-platform-es',
  available_source_names: ['cloud-platform-es'], usable_now: ['cloud-platform-es'],
  chart_type: 'line', owner_id: 1, field_count: 2, references: 1, updated_at: '',
};

const MYSQL_ROW = {
  id: 41, name: '版本统计·App 版本分布', description: '', source_type: 'sql', is_es: false,
  object_key: '', datasource_id: 11, datasource_name: 'prd-cn',
  available_source_names: ['prd-cn'], usable_now: ['prd-cn'],
  chart_type: 'bar', owner_id: 1, field_count: 3, references: 0, updated_at: '',
};

/** 按 URL 分派的假后端：数据集清单 + 数据源清单（内置通道时代的两条配置面 GET 已不存在） */
function route(items: any[] = [ES_ROW, MYSQL_ROW], datasources: any[] = []) {
  (client.get as any).mockImplementation(async (url: string) => {
    if (url === '/datasets/') return { data: { items } };
    if (url === '/datasources/') return { data: datasources };
    if (url === '/dashboard/') return { data: [] };
    throw new Error(`unexpected GET ${url}`);
  });
}

/** 页面依赖 useNavigate，必须套 Router 上下文 */
function renderPage() {
  return render(<MemoryRouter><Datasets /></MemoryRouter>);
}

beforeEach(() => { vi.clearAllMocks(); route(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('数据集卡片 — ES 型的显示口径', () => {
  it('ES 数据集执行位显示数据源业务名 + 「ES 通道」标注，不出现裸枚举与"内置"字样', async () => {
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    expect(screen.getByText('cloud-platform-es')).toBeInTheDocument();
    expect(screen.getByText('ES 通道')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/内置|Elasticsearch 通道/);
    // 内部枚举不得当名称展示（ui-resource-display §1）：两张卡的类型位都显业务名 SQL，
    // 不再为 ES 行单列一个“ES 索引”枚举名
    expect(screen.getAllByText('SQL')).toHaveLength(2);
    expect(screen.queryByText('ES 索引')).not.toBeInTheDocument();
  });

  it('索引范围（es_pattern）不再出现在清单上：那是退役通道的字段', async () => {
    route([{ ...ES_ROW, es_pattern: 't-t_equipment_warning_log*' }]);
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    expect(container.textContent).not.toMatch(/t-t_equipment_warning_log/);
  });

  it('非 ES 数据集不挂「ES 通道」标注（判据是服务端 is_es，不按正文文本猜）', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('版本统计·App 版本分布')).toBeInTheDocument());
    expect(screen.getAllByText('ES 通道')).toHaveLength(1);
  });
});

describe('新建弹窗 — 只剩一条通道', () => {
  it('不提供「Elasticsearch 索引」入口，也不拉内置通道配置面（不留点了必报错的死选项）', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    (client.get as any).mockClear();
    fireEvent.click(screen.getByRole('button', { name: '新建数据集' }));
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).queryByRole('button', { name: /Elasticsearch 索引/ })).toBeNull();
    expect(within(dialog).queryByText('数据来源')).toBeNull();
    expect(within(dialog).queryByRole('combobox', { name: /索引范围/ })).toBeNull();
    expect(within(dialog).getByText('执行 SQL')).toBeInTheDocument();
    expect(within(dialog).getByText('数据源')).toBeInTheDocument();

    const urls = (client.get as any).mock.calls.map(([u]: string[]) => u);
    expect(urls).not.toContain('/datasets/channels');
    expect(urls).not.toContain('/datasets/es-patterns');
  });

  it('新建文案不再宣传「不进治理域」（ES 已是正式数据源，旧口径已作废）', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '新建数据集' }));
    const dialog = await screen.findByRole('dialog');
    const text = dialog.textContent || '';
    expect(text).not.toMatch(/不进列级\/行级|不施加列级\/行级|不套行列级策略/);
    expect(text).toMatch(/经统一治理入口/);
  });
});

describe('筛选器 — 只有「数据源」一个维度（ES 取数通道项已撤）', () => {
  it('下拉不再出现「ES 取数通道」与“取数通道”分组，请求也不带 channel', async () => {
    route([], [{ id: 11, name: 'prd-cn', db_type: 'mysql' }]);
    renderPage();
    await userEvent.click(await screen.findByRole('combobox', { name: '按数据源筛选数据集' }));

    expect(screen.queryByRole('option', { name: 'ES 取数通道' })).not.toBeInTheDocument();
    expect(screen.queryByText('取数通道')).not.toBeInTheDocument();

    (client.get as any).mockClear();
    await userEvent.click(screen.getByRole('option', { name: 'prd-cn' }));
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/datasets/', {
      params: { keyword: '', datasource_id: 11 },
    }));
    expect((client.get as any).mock.calls.at(-1)?.[1]?.params).not.toHaveProperty('channel');
  });

  it('按 ES 型数据源筛就是按 datasource_id 筛（ES 集挂在源上，不需要特殊通道）', async () => {
    route([ES_ROW], [{ id: 1794535612029, name: 'cloud-platform-es', db_type: 'es' }]);
    renderPage();
    await userEvent.click(await screen.findByRole('combobox', { name: '按数据源筛选数据集' }));
    await userEvent.click(await screen.findByRole('option', { name: 'cloud-platform-es' }));
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/datasets/', {
      params: { keyword: '', datasource_id: 1794535612029 },
    }));
  });

  it('数据源选项来自 /datasources/ 而不是写死，且不出现任何“通道/服务”维度', async () => {
    route([], [{ id: 11, name: 'prd-cn', db_type: 'mysql' }, { id: 12, name: 'prd-jp', db_type: 'mysql' }]);
    renderPage();
    await userEvent.click(await screen.findByRole('combobox', { name: '按数据源筛选数据集' }));

    expect(await screen.findByRole('option', { name: 'prd-cn' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'prd-jp' })).toBeInTheDocument();
    // 内置 MCP 服务与“按通道分维度”的写法都已随通道退役：不得再被重新引入
    expect(screen.queryByText('MCP 服务（内置）')).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /mcp:/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'ES 取数通道' })).not.toBeInTheDocument();
  });

  it('整页不请求已退役的配置面端点', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('设备告警按天')).toBeInTheDocument());
    const urls: string[] = (client.get as any).mock.calls.map(([u]: string[]) => u);
    expect(urls.filter((u: string) => u.startsWith('/datasets/'))).toEqual(['/datasets/']);
  });
});
