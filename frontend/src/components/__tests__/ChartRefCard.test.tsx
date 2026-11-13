/**
 * ChartRefCard 渲染口径回归：```chart {"chart_ref": ...} 必须"按引用取数 + 复用图表卡 + 失败可见"。
 *
 * 锁住四条同类风险：
 * 1. 引用取不到时渲染空白卡片（ChartPicker 在 columns<2 / rows=0 时就是 `return null`），
 *    用户只会以为"没图"，而不是"数据取不到"；
 * 2. 拿错形态的正文（spec/html）将就着画图；
 * 3. 服务端给的图型/截断提示被丢掉（用户不知道这张图只用了前 N 行）；
 * 4. 把内部 chart_id 当标题显示。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const get = vi.fn();
vi.mock('@/api/client', () => ({ default: { get: (...a: any[]) => get(...a) } }));

// ChartPicker 会拉起 G2/leaflet/xlsx 等重依赖：本用例只锁"引用数据是否原样交给它"
const pickerProps = vi.hoisted(() => ({ current: null as any }));
vi.mock('../ChartPicker', () => ({
  default: (props: any) => { pickerProps.current = props; return <div data-testid="picker" />; },
}));

import ChartRefCard from '../chat/ChartRefCard';

const CHART_BODY = JSON.stringify({
  chart_type: 'line', title: '月度订单量', columns: ['月份', '订单量'],
  rows: [{ 月份: '2026-01', 订单量: 12 }, { 月份: '2026-02', 订单量: 18 }],
  row_count: 2, sql: '',
});

const doc = (over: Record<string, any> = {}) => ({
  id: 'chart123', conversation_id: 42, kind: 'chart', body_kind: 'chart-data',
  title: '月度订单量', theme: 'datafoundry', template_key: null, digest: 'abc',
  size_chars: CHART_BODY.length, warnings: [], row_counts: { row_count: 2, returned: 2 },
  updated_at: '2026-11-13T00:00:00', body: CHART_BODY, ...over,
});

beforeEach(() => { get.mockReset(); pickerProps.current = null; });

describe('ChartRefCard 图表引用渲染', { timeout: 20000 }, () => {
  it('按引用取回数据后交给 ChartPicker，图型/列/行原样传递', async () => {
    get.mockResolvedValue({ data: doc() });
    render(<ChartRefCard chartRef="chart123" />);
    await waitFor(() => expect(pickerProps.current).toBeTruthy());
    expect(get).toHaveBeenCalledWith('/chat/render-document/chart123');
    expect(pickerProps.current.defaultType).toBe('line');
    expect(pickerProps.current.data.columns).toEqual(['月份', '订单量']);
    expect(pickerProps.current.data.rows).toHaveLength(2);
    expect(screen.getByText(/共 2 行/)).toBeInTheDocument();
  });

  it('块内声明的标题不覆盖后端标题（后端为事实源）', async () => {
    get.mockResolvedValue({ data: doc() });
    render(<ChartRefCard chartRef="chart123" title="旧标题" />);
    expect(await screen.findByText('月度订单量')).toBeInTheDocument();
  });

  it('不显示内部 chart_id 文本', async () => {
    get.mockResolvedValue({ data: doc() });
    render(<ChartRefCard chartRef="chart123" />);
    await waitFor(() => expect(pickerProps.current).toBeTruthy());
    expect(screen.queryByText(/chart123/)).toBeNull();
  });

  it('服务端的非阻断提示（图型推断/结果截断）要可见', async () => {
    const truncated = JSON.stringify({
      chart_type: 'line', title: '月度订单量', columns: ['月份', '订单量'],
      rows: [{ 月份: '2026-01', 订单量: 12 }, { 月份: '2026-02', 订单量: 18 }],
      row_count: 137, sql: '',
    });
    get.mockResolvedValue({ data: doc({ body: truncated, warnings: ['结果已被截断展示：取数 137 行，图表只用前 2 行'] }) });
    render(<ChartRefCard chartRef="chart123" />);
    expect(await screen.findByText(/取数 137 行/)).toBeInTheDocument();
    expect(screen.getByText(/共 137 行/)).toBeInTheDocument();
    expect(screen.getByText(/图上用前 2 行/)).toBeInTheDocument();
  });

  it('引用取不到（404）时显式报错并给重试，不渲染空白图表', async () => {
    get.mockImplementation(() => { throw { response: { status: 404 } }; });
    render(<ChartRefCard chartRef="gone" />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('已被清理');
    expect(screen.queryByTestId('picker')).toBeNull();
    expect(screen.getByRole('button', { name: /重试/ })).toBeInTheDocument();
  });

  it('无权访问与暂时失败给不同文案', async () => {
    get.mockImplementation(() => { throw { response: { status: 403 } }; });
    render(<ChartRefCard chartRef="other" />);
    expect((await screen.findByRole('alert')).textContent).toContain('无权访问');
  });

  it('形态不符（拿 html/spec 当图表数据）显式拒绝，不"将就画一张"', async () => {
    get.mockResolvedValue({ data: doc({ body_kind: 'html', body: '<html>:root{--primary:#1}</html>' }) });
    render(<ChartRefCard chartRef="doc123" />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('不是图表数据');
    expect(screen.queryByTestId('picker')).toBeNull();
  });

  it('正文缺行列时报错原因，不渲染空图（ChartPicker 空数据本就返回 null）', async () => {
    get.mockResolvedValue({ data: doc({ body: JSON.stringify({ chart_type: 'bar', columns: [], rows: [] }) }) });
    render(<ChartRefCard chartRef="broken" />);
    expect((await screen.findByRole('alert')).textContent).toContain('没有可用的行列');
  });
});
