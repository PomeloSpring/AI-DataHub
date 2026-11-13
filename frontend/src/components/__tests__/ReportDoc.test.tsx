/**
 * ReportDoc（spec 形态）渲染口径：逐章交平台组件，失败章显式化。
 *
 * 锁住四条：
 * 1. chart 章节必须走 ChartPicker（样式管线 useChartDesign 与下钻都在里面），
 *    不得在报告内再造第二套画图代码（ui-vis-library-style §1）；
 * 2. 取数失败章节要显式「暂无数据 + 原因」，不静默删章、也不留空图；
 * 3. kpi/table 章节走 DataGrid（与结果卡片同一套表格面），不手写 table 标签；
 * 4. 正文坏形（缺 sections）显式报错，不渲染一份空报告糊弄用户。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

const pickerProps = vi.hoisted(() => ({ current: null as any }));
const gridProps = vi.hoisted(() => ({ current: null as any }));
vi.mock('@/components/ChartPicker', () => ({
  default: (p: any) => { pickerProps.current = p; return <div data-testid="picker" />; },
}));
vi.mock('@/components/DataGrid', () => ({
  default: (p: any) => { gridProps.current = p; return <div data-testid="grid" />; },
}));
vi.mock('@/components/MermaidBlock', () => ({ default: () => <div data-testid="mermaid" /> }));

import ReportDoc, { parseSpecDoc } from '../chat/ReportDoc';
import type { RenderDocument } from '@/api/renderDocument';

const specDoc = (sections: any[], over: Record<string, any> = {}): RenderDocument => ({
  id: 'd1', conversation_id: 42, kind: 'doc', body_kind: 'spec', title: '渠道分析报告',
  theme: 'tech', template_key: null, digest: 'x', size_chars: 100, warnings: [],
  row_counts: null, updated_at: '2026-11-13T00:00:00',
  body: JSON.stringify({ title: '渠道分析报告', theme: 'tech', sections }),
  ...over,
} as RenderDocument);

beforeEach(() => { pickerProps.current = null; gridProps.current = null; });
afterEach(() => cleanup());

describe('ReportDoc 结构化章节渲染', () => {
  it('chart 章节交平台图表组件渲染，图型按后端定值下发（不是前端猜的 bar）', () => {
    render(<ReportDoc doc={specDoc([{ title: '地域分布', kind: 'chart', chart_type: 'china_map',
      data: { columns: ['省份', '浏览量'], rows: [{ 省份: '江苏省', 浏览量: 251 }], row_count: 1 } }])} />);
    expect(screen.getByTestId('picker')).toBeInTheDocument();
    expect(pickerProps.current.defaultType).toBe('china_map');
    expect(pickerProps.current.data.columns).toEqual(['省份', '浏览量']);
  });

  it('kpi/table 章节走 DataGrid，与结果卡片同一套表格面', () => {
    render(<ReportDoc doc={specDoc([{ title: '核心指标', kind: 'kpi',
      data: { columns: ['渠道', '成交额'], rows: [{ 渠道: '线上', 成交额: 62 }], row_count: 1 } }])} />);
    expect(screen.getByTestId('grid')).toBeInTheDocument();
    expect(gridProps.current.columns).toEqual(['渠道', '成交额']);
  });

  it('取数失败章节显式写「暂无数据 + 原因」，不静默删章也不画空图', () => {
    render(<ReportDoc doc={specDoc([
      { title: '异常归因', kind: 'chart', chart_type: 'line', error: '会话未确定数据源' },
    ])} />);
    expect(screen.getByText(/暂无数据/)).toBeInTheDocument();
    expect(screen.getByText(/会话未确定数据源/)).toBeInTheDocument();
    expect(screen.queryByTestId('picker')).toBeNull();
    expect(screen.getByText(/不得沿用|不会沿用/)).toBeInTheDocument();
  });

  it('结果被截断时章内标注用了前 N 行（数字来源要可见）', () => {
    render(<ReportDoc doc={specDoc([{ title: '明细', kind: 'table',
      data: { columns: ['月', '额'], rows: [{ 月: '1', 额: 2 }], row_count: 137 } }])} />);
    expect(screen.getByText(/共 137 行/)).toBeInTheDocument();
  });

  it('mermaid 章节复用平台 mermaid 渲染面', () => {
    render(<ReportDoc doc={specDoc([{ title: '流程', kind: 'mermaid', code: 'graph TD;A-->B;' }])} />);
    expect(screen.getByTestId('mermaid')).toBeInTheDocument();
  });

  it('正文缺 sections 时报错而不是渲染一份空报告', () => {
    render(<ReportDoc doc={specDoc([]) as any} />);   // 空数组仍是合法 spec
    expect(screen.queryByRole('alert')).toBeNull();
    const broken = { ...specDoc([]), body: '{"title":"坏形状"}' } as RenderDocument;
    render(<ReportDoc doc={broken} />);
    expect(screen.getByRole('alert').textContent).toContain('读不出来');
  });

  it('parseSpecDoc 只认后端正文里的章节（坏 JSON 回 null）', () => {
    expect(parseSpecDoc({ body: '不是 JSON' } as any)).toBeNull();
    const ok = parseSpecDoc({ body: JSON.stringify({ sections: [{ kind: 'text' }] }) } as any);
    expect(ok?.sections).toHaveLength(1);
  });
});
