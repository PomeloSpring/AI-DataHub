import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ChartPicker from '../ChartPicker';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { useThemeStore } from '@/stores/themeStore';
import type { VisComponent } from '@/api/visLibrary';

// 图表引擎/重依赖不进本用例: 只断言 ChartPicker 交给 DashboardChart 的样式契约
vi.mock('../DashboardChart', () => ({
  default: (props: any) => (
    <div data-testid="chart"
      data-visual={JSON.stringify(props.visual || {})}
      data-config={JSON.stringify(props.config || {})} />
  ),
  ChartIcon: () => null,
  CHART_TYPES: [
    { value: 'line', label: '折线图', icon: 'LineChart', category: 'basic' },
    { value: 'bar', label: '柱状图', icon: 'BarChart3', category: 'basic' },
  ],
  CHART_TYPE_CATEGORIES: [{ key: 'basic', label: '基础' }],
}));
vi.mock('../MapHeatmap', () => ({ default: () => null, detectGeoColumns: () => ({ nameMatched: false }) }));
vi.mock('../SqlCard', () => ({ default: () => null }));
vi.mock('html2canvas', () => ({ default: vi.fn() }));
vi.mock('xlsx', () => ({ utils: {} }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: vi.fn() }));

const data = { columns: ['周', '数值'], rows: [{ 周: 'W1', 数值: 10 }, { 周: 'W2', 数值: 20 }] };

// 科技风主题包(与库内 tp_tech 同构: 色板/卡片/图表线型)
const techPack = {
  id: 1, code: 'tp_tech', name: '科技霓虹', category: 'theme_pack', is_active: true, updated_at: '',
  style_config: {
    mode: 'dark',
    palette: ['#22d3ee', '#3b82f6', '#8b5cf6'],
    card: { cardBg: 'rgba(15,23,42,0.65)', labelColor: '#94a3b8' },
    charts: {
      default: { gradient: true, smooth: true, lineWidth: 2, showLabel: false, axisStyle: { gridColor: 'rgba(148,163,184,0.15)' } },
      line: { smooth: true, lineWidth: 2.5, areaFill: true },
    },
  },
} as unknown as VisComponent;

function mockLibrary(items: VisComponent[], error: string | null = null) {
  vi.mocked(useVisLibrary).mockReturnValue({
    items, loading: false, error, loaded: !error, refresh: vi.fn(),
  } as any);
}

function chartProps() {
  const el = screen.getByTestId('chart');
  return {
    visual: JSON.parse(el.getAttribute('data-visual') || '{}'),
    config: JSON.parse(el.getAttribute('data-config') || '{}'),
  };
}

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe('Chat 图表样式跟随 UI 字模库主题包', () => {
  it('选中主题后图表吃 tp_<theme> 的色板/卡片/图表样式', () => {
    useThemeStore.setState({ theme: 'tech' });
    mockLibrary([techPack]);
    render(<ChartPicker data={data} defaultType="line" />);
    const { visual, config } = chartProps();
    // 色板/模式来自主题包(不是主题 CSS 令牌的默认盘)
    expect(visual.palette).toEqual(['#22d3ee', '#3b82f6', '#8b5cf6']);
    expect(visual.mode).toBe('dark');
    // 图表画布背景取主题包卡片底, 深色主题下与文字/网格同套配色
    expect(visual.viewBg).toBe('rgba(15,23,42,0.65)');
    // 图表样式(线型/轴线/标签)来自主题包 charts.default + charts.line
    expect(config.smooth).toBe(true);
    expect(config.lineWidth).toBe(2.5);          // charts.line 覆盖 charts.default
    expect(config.showLabel).toBe(false);
    expect(config.axisStyle.gridColor).toBe('rgba(148,163,184,0.15)');
    // 数据映射仍随用户选择
    expect(config.xCol).toBe('周');
    expect(config.yCol).toBe('数值');
  });

  it('换主题即换字模: tp_datafoundry 的色板生效', () => {
    useThemeStore.setState({ theme: 'datafoundry' });
    mockLibrary([techPack, {
      ...techPack, code: 'tp_datafoundry',
      style_config: { mode: 'light', palette: ['#111827', '#374151'], card: { cardBg: '#ffffff' }, charts: { default: { smooth: false, lineWidth: 2 } } },
    } as VisComponent]);
    render(<ChartPicker data={data} />);
    const { visual, config } = chartProps();
    expect(visual.palette).toEqual(['#111827', '#374151']);
    expect(visual.viewBg).toBe('#ffffff');
    expect(config.smooth).toBe(false);
  });

  it('字模库未加载/无对应主题包时不崩(回退默认渲染)', () => {
    useThemeStore.setState({ theme: 'light' });
    mockLibrary([]);
    render(<ChartPicker data={data} />);
    expect(screen.getByTestId('chart')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();  // 空库不报错(默认样式即为预期)
  });

  it('字模库加载失败不静默降级：显式提示并可重试', () => {
    useThemeStore.setState({ theme: 'light' });
    mockLibrary([], '字模库加载失败，请重试');
    render(<ChartPicker data={data} />);
    expect(screen.getByRole('alert')).toHaveTextContent('字模库未加载，图表暂用默认样式');
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(screen.getByTestId('chart')).toBeInTheDocument();
  });
});
