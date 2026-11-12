import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { toast } from '@/lib/toast';
import DashboardChart from '../DashboardChart';
import PropertyPanel from '../editor/PropertyPanel';
import ChartConfigPanel from '../ChartConfigPanel';
import { useDashboardStore, type Dashboard, type DashboardChart as ChartModel } from '@/stores/dashboardStore';
import { useEditorCharts } from '@/hooks/useEditorCharts';
import client from '@/api/client';
import { pagePixelRect } from '@/lib/dashboardPageLayout';

const bar = { id: 7, name: '省份用户分布', chart_type: 'bar', config: {}, position: { x: 0, y: 0, w: 400, h: 300 },
  data_cache: JSON.stringify({ columns: ['province', 'uv'], rows: [{ province: '上海市', uv: 1 }] }) } as ChartModel;
const widget = (type: string, config: Record<string, any> = {}) => ({ id: 21, name: '控件', chart_type: type, config,
  position: { x: 0, y: 0, w: 300, h: 60 } }) as unknown as ChartModel;
const widgetChart = (type: string, config: Record<string, any> = {}) =>
  <DashboardChart chartType={type} data={{ columns: [], rows: [] }} config={config} draft={false} />;
const restore = () => useDashboardStore.getState();

function panelProps(selectedChart: any) {
  return {
    isOpen: true, onClose: () => {}, selectedChart, selectedElementType: 'control' as const,
    canvasSize: { width: 1920, height: 1080 }, setCanvasSize: () => {}, canvasBgColor: '', setCanvasBgColor: () => {},
    gridSize: 20, setGridSize: () => {}, scale: 1, setScale: () => {}, allCharts: [bar, selectedChart],
    isNewChart: (id: number) => id < 0, onPropertyChange: () => {}, onPositionChange: () => {},
    onWidgetConfigChange: () => {}, onDelete: () => {}, onChartConfig: () => {}, tab: 'data', onTab: () => {},
  };
}

beforeEach(() => {
  vi.spyOn(toast, 'error').mockReturnValue(0);
  vi.spyOn(client, 'get').mockResolvedValue({ data: {} });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('控件配置唯一入口', () => {
  it('日期范围控件可配最大跨度、绑定参数与标签位置', () => {
    render(<PropertyPanel {...panelProps(widget('widget_daterange', { paramKey: 'd' }))} />);
    expect(screen.getByText('最大跨度（天）')).toBeTruthy();
    expect(screen.getByText('绑定开始参数')).toBeTruthy();
    expect(screen.getByText('绑定结束参数')).toBeTruthy();
    expect(screen.getByText('标签位置')).toBeTruthy();
    expect(screen.getByRole('switch')).toBeTruthy();
  });

  it('下拉控件可配标签文字与显示开关', () => {
    render(<PropertyPanel {...panelProps(widget('widget_select', { paramKey: 'site' }))} />);
    expect(screen.getByRole('switch')).toBeTruthy();
    expect(screen.getByText('显示标签')).toBeTruthy();
    expect(screen.getByText('标签文字')).toBeTruthy();
  });

  it('选中参数后把业务名带进标签（没手填过标签时），画布上不再是内部键名', () => {
    const onWidgetConfigChange = vi.fn();
    render(<PropertyPanel {...panelProps(widget('widget_select', { paramKey: '' }))}
      pageParams={[{ name: 'region', type: 'select', default: '', label: '区域' } as never]}
      onWidgetConfigChange={onWidgetConfigChange} />);
    const trigger = screen.getByText('参数 Key').parentElement!.querySelector('[role="combobox"]')!;
    fireEvent.click(trigger);
    fireEvent.click(screen.getByText('区域'));
    expect(onWidgetConfigChange).toHaveBeenCalledWith(21, 'paramKey', 'region');
    expect(onWidgetConfigChange).toHaveBeenCalledWith(21, 'label', '区域');
  });

  it('标签已手填时选参数不覆盖用户写的名字', () => {
    const onWidgetConfigChange = vi.fn();
    render(<PropertyPanel {...panelProps(widget('widget_select', { paramKey: '', label: '大区' }))}
      pageParams={[{ name: 'region', type: 'select', default: '', label: '区域' } as never]}
      onWidgetConfigChange={onWidgetConfigChange} />);
    const trigger = screen.getByText('参数 Key').parentElement!.querySelector('[role="combobox"]')!;
    fireEvent.click(trigger);
    fireEvent.click(screen.getByText('区域'));
    expect(onWidgetConfigChange).not.toHaveBeenCalledWith(21, 'label', '区域');
  });

  it('日期区间宽度不够时显式报“只展示一半”并给一键补宽（大屏模式按 px）', () => {
    const onPositionChange = vi.fn();
    render(<PropertyPanel {...panelProps(widget('widget_daterange', { paramKey: 'd' }))}
      tab="position" onPositionChange={onPositionChange} />);
    expect(screen.getByRole('alert').textContent).toMatch(/只展示一半/);
    fireEvent.click(screen.getByRole('button', { name: '补宽到 420px' }));
    expect(onPositionChange).toHaveBeenCalledWith(21, 'w', 420);
  });

  it('页面网格模式下补宽给列数（换算后真能装下）', () => {
    const onPositionChange = vi.fn();
    render(<PropertyPanel {...panelProps(widget('widget_daterange', { paramKey: 'd' }))}
      tab="position" pageBreakpoint="desktop" pageRect={{ x: 0, y: 0, w: 11, h: 8 }}
      onPositionChange={onPositionChange} />);
    const btn = screen.getByRole('button', { name: /补宽到 \d+ 列/ });
    fireEvent.click(btn);
    const span = Number(btn.textContent!.match(/(\d+)/)![1]);
    expect(onPositionChange).toHaveBeenCalledWith(21, 'w', span);
    // 补完的列数换算回像素必须 ≥ 可读下限 420
    expect(pagePixelRect({ x: 0, y: 0, w: span, h: 1 }, 'desktop').w).toBeGreaterThanOrEqual(420);
  });

  it('宽度够用时不报（不挂假告警）', () => {
    const wide = { id: 21, name: '控件', chart_type: 'widget_daterange', config: { paramKey: 'd' },
      position: { x: 0, y: 0, w: 620, h: 32 } } as never;
    render(<PropertyPanel {...panelProps(wide)} tab="position" />);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('导出控件的目标图表用 name 显示，未选择时给出可见告警', () => {
    render(<PropertyPanel {...panelProps(widget('widget_export', {}))} />);
    expect(screen.getByText('导出目标图表')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toMatch(/未选择目标图表/);
  });

  it('目标图表已被删除时提示重新选择，而不是回退到别的图表', () => {
    render(<PropertyPanel {...panelProps(widget('widget_export', { targetChartId: 999 }))} />);
    expect(screen.getByRole('alert').textContent).toMatch(/已不在当前看板/);
  });

  it('ChartConfigPanel 不再接收控件（控件配置单入口）', () => {
    // 配置面板内的「在数据集页查看口径」需要 Router 上下文（跳数据集详情）
    const { container } = render(
      <MemoryRouter>
        <ChartConfigPanel open chart={widget('widget_select')} onClose={() => {}} onSave={() => {}} />
      </MemoryRouter>);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('导出按钮去静默', () => {
  it('未配置目标图表时按钮置灰，不导出任何数据', () => {
    const state = restore();
    useDashboardStore.setState({ dashboards: [{ id: 1, charts: [bar] } as unknown as Dashboard], currentId: 1 });
    const view = render(widgetChart('widget_export', {}));
    const button = view.getByRole('button');
    expect(button).toBeDisabled();
    expect(button.getAttribute('title')).toMatch(/属性面板/);
    expect(toast.error).not.toHaveBeenCalled();
    useDashboardStore.setState(state);
  });

  it('目标图表不存在时明确报错，不回退到第一个图表', () => {
    const state = restore();
    useDashboardStore.setState({ dashboards: [{ id: 1, charts: [bar] } as unknown as Dashboard], currentId: 1 });
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x');
    const view = render(widgetChart('widget_export', { targetChartId: 999 }));
    fireEvent.click(view.getByRole('button'));
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('999'));
    expect(create).not.toHaveBeenCalled();
    useDashboardStore.setState(state);
  });

  it('目标图表无缓存数据时提示先刷新，不静默产出空文件', () => {
    const state = restore();
    const empty = { ...bar, id: 8, data_cache: null } as ChartModel;
    useDashboardStore.setState({ dashboards: [{ id: 1, charts: [empty] } as unknown as Dashboard], currentId: 1 });
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x');
    const view = render(widgetChart('widget_export', { targetChartId: 8 }));
    fireEvent.click(view.getByRole('button'));
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('暂无数据'));
    expect(create).not.toHaveBeenCalled();
    useDashboardStore.setState(state);
  });
});

describe('控件样式面板 — 生效值与来源', () => {
  // 历史缺陷：面板只读本图 config.widgetStyle，显示的是写死的回退值（圆角 8/字号 14），
  // 与画布上字模的真实生效值不一致；且本图覆盖优先于字模，用户选完字模看不出为何“没反应”。
  const styleProps = (selectedChart: any, extra: Record<string, any> = {}) =>
    ({ ...panelProps(selectedChart), tab: 'appearance', ...extra });

  it('未覆盖时按字模基线显示生效值，并标明基线来源', () => {
    render(<PropertyPanel {...styleProps(widget('widget_select', { paramKey: 'site' }),
      { widgetStyleBase: { fontSize: 13, borderRadius: 8 }, widgetVisName: '下拉框·AS' })} />);
    expect(screen.getByText(/基线来源：控件字模「下拉框·AS」/)).toBeTruthy();
    expect(screen.getByText('13px')).toBeTruthy();          // 字号取字模的 13，不是面板写死的 14
    expect(screen.getByText(/本图未覆盖任何项/)).toBeTruthy();
  });

  it('本图覆盖的键被显式列出，可逐项释放回跟随字模', () => {
    const onWidgetConfigChange = vi.fn();
    render(<PropertyPanel {...styleProps(widget('widget_select', { paramKey: 'site', widgetStyle: { fontSize: 20 } }),
      { widgetStyleBase: { fontSize: 13 }, widgetVisName: '下拉框·AS', onWidgetConfigChange })} />);
    expect(screen.getByText(/本图覆盖（优先于字模）/)).toBeTruthy();
    expect(screen.getByText('20px')).toBeTruthy();
    fireEvent.click(screen.getByTitle('点击让该项跟随字模'));
    expect(onWidgetConfigChange).toHaveBeenCalledWith(21, 'widgetStyle', {});
  });

  it('无字模时按主题包基线说明，并保留一键清除本图覆盖', () => {
    const onWidgetConfigChange = vi.fn();
    render(<PropertyPanel {...styleProps(widget('widget_text', { paramKey: 'kw', widgetStyle: { padding: 12 } }),
      { widgetStyleBase: { fontSize: 13 }, onWidgetConfigChange })} />);
    expect(screen.getByText(/基线来源：主题包控件基线/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '清除本图覆盖（全部跟随字模）' }));
    expect(onWidgetConfigChange).toHaveBeenCalledWith(21, 'widgetStyle', {});
  });
});

describe('控件样式旋钮被真实消费（挂了字模要看得见）', () => {
  const styleOf = (el: HTMLElement) => el.getAttribute('style') || '';
  it('描边/圆角/字号落在控件内联样式上', () => {
    const view = render(widgetChart('widget_text', { paramKey: 'kw', showLabel: false,
      widgetStyle: { backgroundColor: '#ffffff', borderColor: '#E9ECF8', borderRadius: 8, fontSize: 13 } }));
    const style = styleOf(view.getByRole('textbox'));
    expect(style).toContain('border-radius: 8px');
    expect(style).toContain('font-size: 13px');
    expect(style).toContain('border: 1px solid rgb(233, 236, 248)');   // #E9ECF8
    expect(style).toContain('background: rgb(255, 255, 255)');
  });
  it('「控件高」不再只给多选框用：无标签控件按高度居中，不拉满卡片', () => {
    const view = render(widgetChart('widget_select', { paramKey: 'site', showLabel: false, widgetStyle: { height: 32 } }));
    const style = styleOf(view.getByRole('combobox'));
    expect(style).toContain('height: 32px');
    expect(style).toContain('margin: auto 0');
  });
  it('有标签时高度落在内层输入上', () => {
    const view = render(widgetChart('widget_text', { paramKey: 'kw', label: '关键词', widgetStyle: { height: 28 } }));
    expect(styleOf(view.getByRole('textbox'))).toContain('height: 28px');
  });
  it('决策建议卡真实消费控件旋钮（背景/文字/边框/圆角/内边距/字号/高）', () => {
    // 计划 §四出口 4：widget_decision 走 widget_style 命名空间，存了不生效就是缺陷
    const view = render(widgetChart('widget_decision', {
      treeKey: '',
      widgetStyle: { backgroundColor: '#0037CB', textColor: '#ffffff', borderColor: '#E9ECF8',
        borderRadius: 12, padding: '10px 14px', fontSize: 15, height: 180 },
    }));
    const style = styleOf(view.getByRole('status'));
    expect(style).toContain('background: rgb(0, 55, 203)');
    expect(style).toContain('color: rgb(255, 255, 255)');
    expect(style).toContain('border: 1px solid rgb(233, 236, 248)');
    expect(style).toContain('border-radius: 12px');
    expect(style).toContain('padding: 10px 14px');
    expect(style).toContain('font-size: 15px');
    expect(style).toContain('height: 180px');
  });
  it('决策建议卡没写旋钮时走主题基线，不是自带另一套色', () => {
    const withKnob = styleOf(render(widgetChart('widget_decision', {
      treeKey: '', widgetStyle: { backgroundColor: '#0037CB' },
    })).getByRole('status'));
    expect(withKnob).toContain('background: rgb(0, 55, 203)');
    cleanup();
    const bare = styleOf(render(widgetChart('widget_decision', { treeKey: '' })).getByRole('status'));
    expect(bare).not.toContain('background: rgb(0, 55, 203)');
  });
  it('搜索按钮吃主色与控件高', () => {
    const view = render(widgetChart('widget_search', { label: '查询', widgetStyle: { backgroundColor: '#0037CB', height: 32 } }));
    const style = styleOf(view.getByRole('button'));
    expect(style).toContain('height: 32px');
    expect(style).toContain('background: rgb(0, 55, 203)');            // 主色 #0037CB
  });
});

describe('决策建议控件的配置面', () => {
  const trees = [{ tree_key: 'accuracy-risk', name: '设备精度风险判定', version: 3, status: 'active' }];

  beforeEach(() => {
    vi.spyOn(client, 'get').mockImplementation(async (url: string) => ({
      data: String(url).includes('/decisions/query/trees') ? { items: trees } : {},
    }) as any);
  });

  it('只配「哪棵树 + 哪个参数喂给哪类条件」，不出现无消费方的标签/参数旋钮', () => {
    render(<PropertyPanel {...panelProps(widget('widget_decision', {}))}
      pageParams={[{ name: 'd', type: 'date_range', default: '', label: '统计区间' } as never]} />);
    expect(screen.getByText('决策树（只列已发布的）')).toBeTruthy();
    expect(screen.queryByText('标签文字')).toBeNull();
    expect(screen.queryByText('绑定页面参数')).toBeNull();
    expect(screen.queryByText('参数 Key')).toBeNull();
  });

  it('选树只写 treeKey，绑定只写 paramBindings（前端不拼 filters）', async () => {
    const onWidgetConfigChange = vi.fn();
    // 绑定行预置一条：面板自已在 config 里，父级 handlePropertyChange 才会回写（这里只验写出的形状）
    const chart = widget('widget_decision', { treeKey: '', paramBindings: [{ key: '', slot: 'time_window' }] });
    render(<PropertyPanel {...panelProps(chart)}
      pageParams={[{ name: 'd', type: 'date_range', default: '', label: '统计区间' } as never]}
      onWidgetConfigChange={onWidgetConfigChange} />);
    // 树清单是拉回来的：选项没到位就 change 会选出一个空值（假成功）
    await screen.findByText('设备精度风险判定（v3）');
    fireEvent.change(screen.getByLabelText('决策树'), { target: { value: 'accuracy-risk' } });
    expect(onWidgetConfigChange).toHaveBeenCalledWith(21, 'treeKey', 'accuracy-risk');

    fireEvent.change(screen.getByLabelText('第 1 条绑定的参数'), { target: { value: 'd' } });
    const widgetCalls = onWidgetConfigChange.mock.calls as any[][];
    const last = widgetCalls[widgetCalls.length - 1];
    expect(last[1]).toBe('paramBindings');
    expect(last[2]).toEqual([{ key: 'd', slot: 'time_window' }]);
    expect(JSON.stringify(last[2])).not.toContain('filters');

    fireEvent.click(screen.getByLabelText('加一条筛选绑定'));
    // 本例里父级不回写 config，所以新一行仍基于原 prop（空键）：真实编辑器会先
    // 经过 handlePropertyChange 落 config.paramBindings，行数与形状同一口径
    expect(onWidgetConfigChange).toHaveBeenLastCalledWith(21, 'paramBindings',
      [{ key: '', slot: 'time_window' }, { key: '', slot: 'time_window' }]);
  });

  it('绑到维度槽位时必须补维度业务名（不填服务端会说未套用）', () => {
    const onWidgetConfigChange = vi.fn();
    const chart = widget('widget_decision', {
      treeKey: 'accuracy-risk',
      paramBindings: [{ key: 'region', slot: 'dimension', dimension: '区域' }],
    });
    render(<PropertyPanel {...panelProps(chart)}
      pageParams={[{ name: 'region', type: 'select', default: '', label: '大区' } as never]}
      onWidgetConfigChange={onWidgetConfigChange} />);
    expect((screen.getByLabelText('第 1 条绑定的参数') as HTMLSelectElement).value).toBe('region');
    expect((screen.getByLabelText('第 1 条绑定的槽位') as HTMLSelectElement).value).toBe('dimension');
    expect((screen.getByLabelText('第 1 条绑定的维度业务名') as HTMLInputElement).value).toBe('区域');
    fireEvent.click(screen.getByLabelText('删除第 1 条绑定'));
    expect(onWidgetConfigChange).toHaveBeenLastCalledWith(21, 'paramBindings', []);
  });

  it('没有已发布树时说得清原因，不给一个选不出东西的空下拉', () => {
    vi.spyOn(client, 'get').mockImplementation(async () => ({ data: { items: [] } }) as any);
    render(<PropertyPanel {...panelProps(widget('widget_decision', {}))} />);
    expect(screen.getByText(/没有已发布的决策树/)).toBeTruthy();
  });

  it('清单读不到时报错（不把“读失败”当成“没树”）', async () => {
    vi.spyOn(client, 'get').mockImplementation(async () => { throw new Error('网络中断'); }) as any;
    render(<PropertyPanel {...panelProps(widget('widget_decision', {}))} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('决策树清单读不到');
  });
});

describe('图表卡与控件卡禁止跨类改型', () => {
  it('把图表改成 widget_ 类型被拒并提示，类型不变', () => {
    const state = restore();
    useDashboardStore.setState({ dashboards: [{ id: 88, charts: [bar], filters: {} } as unknown as Dashboard] });
    const h = renderHook(() => useEditorCharts(88));
    act(() => h.result.current.handlePropertyChange(7, 'chart_type', 'widget_select'));
    expect(h.result.current.allCharts[0].chart_type).toBe('bar');
    expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('同类内换型'));
    // 同类内换型仍然放行
    act(() => h.result.current.handlePropertyChange(7, 'chart_type', 'line'));
    expect(h.result.current.allCharts[0].chart_type).toBe('line');
    h.unmount(); useDashboardStore.setState(state);
  });
});
