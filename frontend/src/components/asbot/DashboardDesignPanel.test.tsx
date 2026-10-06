import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import DashboardDesignPanel from './DashboardDesignPanel';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), getDesign: vi.fn() }));
vi.mock('../../api/client', () => ({ default: mocks }));
vi.mock('../../api/dashboardDesign', async importOriginal => ({ ...await importOriginal<object>(), getDesign: mocks.getDesign }));
vi.mock('../DashboardChart', () => ({ default: () => <div>真实图表预览</div>, CHART_TYPES: [{ value: 'bar', label: '柱状图' }] }));
vi.mock('../../stores/dashboardStore', () => ({ useDashboardStore: { getState: () => ({ loadDashboards: vi.fn(), refreshCharts: vi.fn() }) } }));

const design = {
  design_id: 'a'.repeat(32), version: 3, status: 'designing', name: 'test', request: '近7天案例数量', operation: 'append',
  selection: { datasource_name: '业务域', knowledge_base_ids: [4], dashboard_id: 10 },
  widgets: [{ key: '0', title: '案例数量', chart_type: 'bar', query: { object: 'case', metrics: ['案例数量'] },
    query_source: 'semantic', config: {}, position: { x: 0, y: 0, w: 640, h: 360 } }],
  steps: ['统计案例，按日期聚合'], questions: [], answers: {}, preview_valid: false,
};
const props = { designId: design.design_id, onClose: vi.fn(), onChanged: vi.fn(), onContinue: vi.fn() };
beforeEach(() => {
  vi.clearAllMocks();
  mocks.getDesign.mockResolvedValue(structuredClone(design));
  mocks.get.mockResolvedValue({ data: { workspaces: [], dashboards: [], domains: [] } });
});

describe('仪表盘设计确认', () => {
  it('没有当前预览不能发布，查询预览不等于发布', async () => {
    render(<DashboardDesignPanel {...props} />);
    const publish = await screen.findByRole('button', { name: '确认并发布' });
    expect(publish).toBeDisabled();
    mocks.post.mockResolvedValueOnce({ data: { design: { ...design, version: 4, status: 'pending_confirmation', preview_valid: true },
      generated_at: '2026-09-22 10:00:00', charts: [{ key: '0', columns: ['数量'], rows: [{ 数量: 3 }], row_count: 1 }] } });
    fireEvent.click(screen.getByRole('button', { name: '重新校验并预览' }));
    await waitFor(() => expect(publish).toBeEnabled());
    expect(mocks.post).toHaveBeenCalledWith(expect.stringContaining('/preview'), { expected_version: 3 });
    // 查询预览不等于发布：未点确认前不得调用发布端点
    expect(mocks.post).not.toHaveBeenCalledWith(expect.stringContaining('/publish'), expect.anything());
  });

  it('修改 SQL 后旧预览不可批准，SQL 不发送到聊天', async () => {
    render(<DashboardDesignPanel {...props} />);
    await screen.findByText('统计案例，按日期聚合');
    // Radix Tabs 由键盘或鼠标 pointer 激活；先让加载按钮所在 tab 可见。
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'SQL 编辑' }));
    fireEvent.keyDown(screen.getByRole('tab', { name: 'SQL 编辑' }), { key: 'Enter' });
    mocks.get.mockResolvedValueOnce({ data: { version: 3, queries: [{ key: '0', sql: 'SELECT 1 AS n' }] } });
    fireEvent.click(await screen.findByRole('button', { name: '加载当前 SQL' }));
    fireEvent.change(await screen.findByRole('textbox', { name: 'SQL 案例数量' }), { target: { value: 'SELECT 2 AS n' } });
    expect(screen.getByRole('button', { name: '确认并发布' })).toBeDisabled();
    expect(props.onContinue).not.toHaveBeenCalled();
    mocks.put.mockResolvedValueOnce({ data: { ...design, version: 4, widgets: [{ ...design.widgets[0], query_source: 'raw_sql' }] } });
    fireEvent.click(screen.getByRole('button', { name: '保存 SQL 并校验' }));
    await waitFor(() => expect(mocks.put).toHaveBeenCalledWith(expect.stringContaining('/sql'), {
      expected_version: 3, widget_key: '0', sql: 'SELECT 2 AS n',
    }));
  });

  it('预览失败可见，不沿用旧结果', async () => {
    mocks.post.mockRejectedValueOnce({ response: { data: { detail: { message: '业务绑定已变化' } } } });
    render(<DashboardDesignPanel {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: '重新校验并预览' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('业务绑定已变化');
    expect(screen.getByRole('button', { name: '确认并发布' })).toBeDisabled();
  });
});
