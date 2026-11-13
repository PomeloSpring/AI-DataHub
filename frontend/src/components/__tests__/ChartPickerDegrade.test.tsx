/**
 * ChartPicker 的图型降级必须可见（no-silent-degradation）。
 *
 * 历史缺陷：`resolveChartType` 认不出的图型一律退成柱状图，模型写 `radar`（平台曾有）
 * 或 `chord`（从未支持）时用户看到的是一张柱子图，只会怀疑数据错了。
 * 新链路由服务端 render_chart 提前拒收；这里锁的是**存量消息回放**这一侧：
 * 兼容要保留（不能把历史消息渲染成空白），但替换这件事必须说出来。
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ChartPicker from '../ChartPicker';
import { resolveChartTypeSafe } from '../ChartPicker';
import { useVisLibrary } from '@/hooks/useVisLibrary';

vi.mock('../DashboardChart', () => ({
  default: () => <div data-testid="chart" />,
  ChartIcon: () => null,
  CHART_TYPES: [
    { value: 'bar', label: '柱状图', icon: 'BarChart3', category: 'basic' },
    { value: 'line', label: '折线图', icon: 'TrendingUp', category: 'basic' },
    { value: 'radar', label: '雷达图', icon: 'Webhook', category: 'basic' },
    { value: 'widget_select', label: '下拉选择框', icon: 'List', category: 'widget' },
  ],
  CHART_TYPE_CATEGORIES: [{ key: 'basic', label: '基础图表' }, { key: 'widget', label: '参数控件' }],
}));
vi.mock('../MapHeatmap', () => ({ default: () => null, detectGeoColumns: () => ({ nameMatched: false }) }));
vi.mock('../SqlCard', () => ({ default: () => null }));
vi.mock('html2canvas', () => ({ default: vi.fn() }));
vi.mock('xlsx', () => ({ utils: {} }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: vi.fn() }));

const data = { columns: ['周', '数值'], rows: [{ 周: 'W1', 数值: 10 }, { 周: 'W2', 数值: 20 }] };

function pick(over: Record<string, any> = {}) {
  vi.mocked(useVisLibrary).mockReturnValue({
    items: [], loading: false, error: null, loaded: true, refresh: vi.fn(), ...over,
  } as any);
}

beforeEach(() => pick());
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('图型降级的可见性', () => {
  it('未知图型：照样渲染（历史消息不空白）但明确说出已被换掉', () => {
    render(<ChartPicker data={data} defaultType="chord" />);
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('chord');
    expect(alert.textContent).toContain('平台不支持');
    expect(screen.getByTestId('chart')).toBeInTheDocument();
  });

  it('历史别名 table（旧契约里被静默退成柱状图）同样要说出来', () => {
    render(<ChartPicker data={data} defaultType="table" />);
    expect(screen.getByRole('alert').textContent).toContain('柱状图');
  });

  it('合法图型与未指定图型都不该出现降级提示（没替换就没噪音）', () => {
    render(<ChartPicker data={data} defaultType="radar" />);
    expect(screen.queryByRole('alert')).toBeNull();
    cleanup();
    render(<ChartPicker data={data} />);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('resolveChartTypeSafe 的判定与 resolveChartType 兼容（只多给 degraded 事实）', () => {
    expect(resolveChartTypeSafe('chord')).toEqual({ type: 'bar', requested: 'chord', degraded: true });
    expect(resolveChartTypeSafe('map')).toEqual({ type: 'map_heat', requested: 'map', degraded: true });
    expect(resolveChartTypeSafe(undefined).degraded).toBe(false);
    expect(resolveChartTypeSafe('radar')).toEqual({ type: 'radar', requested: 'radar', degraded: false });
  });
});
