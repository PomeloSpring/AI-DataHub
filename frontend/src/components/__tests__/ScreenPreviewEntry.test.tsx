/**
 * 「预览大屏」入口口径回归（2026-11-17 反馈）：
 *
 * ① 最初没有任何入口（只把 `/screen/<id>` 当纯文本念）；
 * ② 补完后挂到了**每条** MCP 工具结果下 —— 模型逐图 `update_data_screen_chart`
 *    时一条回答里冒出好几个「预览大屏」，看着像每个中间步骤各交付了一屏。
 *
 * 现行口径：预览属于**整块看板**（任务终点），一个回合只给一个，挂在最终回答末尾；
 * 目标从该回合工具调用里倒序取最后一个成功的建屏/改图结果。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TurnScreenPreview, screenPreviewTarget, screenPreviewTargets } from '../ScreenPreviewEntry';

const SCREEN = 'mcp__datahub_screen__create_data_screen';
const UPDATE = 'mcp__datahub_screen__update_data_screen_chart';

const ok = (tool: string, body: string) => ({ tool, result: body } as any);
const failed = (tool: string, error: string) => ({ tool, error } as any);

const created = ok(SCREEN, JSON.stringify({
  dashboard_id: 1794910593641,
  name: '今年案例数量总览 · 五站合一',
  url: '/screen/1794910593641',
  actions: [{
    type: 'open_screen', label: '预览大屏',
    dashboard_id: 1794910593641, url: '/screen/1794910593641',
  }],
}));

const updated = (chartId: number, body = '') => ok(UPDATE, body || JSON.stringify({
  dashboard_id: 1794910593641, chart_id: chartId, url: '/screen/1794910593641',
}));

describe('screenPreviewTarget', () => {
  it('逐图改屏 + 建屏混在一个回合里，只出一个目标且取最后一个成功结果', () => {
    const target = screenPreviewTarget([
      created,
      updated(11),
      updated(12),
      updated(13),
    ] as any);
    expect(target).toEqual({ dashboardId: 1794910593641, name: '今年案例数量总览 · 五站合一' });
  });

  it('失败的工具结果不产出入口（建屏失败不能给一个“预览一块不存在的板”）', () => {
    expect(screenPreviewTarget([
      failed(SCREEN, '权限、资源或执行环境不满足要求'),
    ] as any)).toBeNull();
    // 建屏失败但之前已建过：仍用前面那条成功的（用户确实有那块板）
    expect(screenPreviewTarget([
      created,
      failed(UPDATE, 'chart_type 不支持'),
    ] as any)?.dashboardId).toBe(1794910593641);
  });

  it('结果被截断成半截 JSON 时按 dashboard_id 兜底解析（落库只留前 4000 字）', () => {
    const truncated = ok(SCREEN, '{"dashboard_id": 1794910593641, "name": "五站合一", "charts": 11, "data_aud');
    expect(screenPreviewTarget([truncated] as any)).toEqual({ dashboardId: 1794910593641 });
  });

  it('只有纯路径的历史结果也能识别', () => {
    expect(screenPreviewTarget([
      ok(UPDATE, '已更新图表 /screen/1794910593641'),
    ] as any)).toEqual({ dashboardId: 1794910593641 });
  });

  it('非建屏工具 / 空回合不产出入口', () => {
    expect(screenPreviewTarget([ok('mcp__datahub_semantic__run_semantic_query', '{"rows": 3}')] as any)).toBeNull();
    expect(screenPreviewTarget([])).toBeNull();
    expect(screenPreviewTarget(undefined)).toBeNull();
  });

  it('同一回合真的建了两块不同的板：各一个入口，全部堆在末尾且去重', () => {
    const boardB = ok(SCREEN, JSON.stringify({
      dashboard_id: 2, name: '另一块板',
      actions: [{ type: 'open_screen', dashboard_id: 2 }],
    }));
    // 事件序：建 A → 建 B → 改 A；入口序按“最后一次出现”排（A 在 B 后）
    const targets = screenPreviewTargets([created, boardB, updated(11)] as any);
    expect(targets.map(t => t.dashboardId)).toEqual([1794910593641, 2]);
    expect(targets.map(t => t.name)).toEqual(['今年案例数量总览 · 五站合一', '另一块板']);
  });
});

describe('TurnScreenPreview 渲染口径', () => {
  it('任务完成后只渲染一个「预览大屏」按钮（逐图多条工具结果不重复）', () => {
    render(<TurnScreenPreview toolCalls={[created, updated(11), updated(12)] as any} />);
    expect(screen.getAllByText('预览大屏')).toHaveLength(1);
    // 按钮上带看板名，用户知道要预览哪一块
    expect(screen.getByText(/今年案例数量总览/)).toBeInTheDocument();
  });

  it('回合还在跑（streaming）时不给入口：中间态不是成品', () => {
    render(<TurnScreenPreview toolCalls={[created]} streaming />);
    expect(screen.queryByText('预览大屏')).not.toBeInTheDocument();
  });

  it('没有建屏结果时整块不渲染（普通取数回合不该冒出一个预览按钮）', () => {
    const { container } = render(
      <TurnScreenPreview toolCalls={[ok('mcp__datahub_semantic__run_semantic_query', '{"rows": 3}')] as any} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});