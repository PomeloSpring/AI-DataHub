// 工作空间「决策建议」消费页（/ws/:id/decisions ↔ workspace:decisions）的三条硬口径：
//  1. 无权限就如实说无权限：显示服务端 403 的裁决结果，不渲染成"没有风险的空清单"
//     （no-silent-degradation §1：把"没评估"演成"数据正常"是这个功能最坏的失败）；
//  2. partial/skipped 必须带原因显示，且缺指标要点名（否则用户以为评估过且没问题）；
//  3. 这一页只是消费面：不出现编辑/发布入口（配置在数据中台，两个面各写一套就成双口径）；
//     草案状态的树不给"评估"（草案未生效，拿它回答用户等于报未上线的判断标准）。
import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import DecisionCenter from '../DecisionCenter';
import client from '@/api/client';
import { toast } from '@/lib/toast';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));

const ACTIVE_TREE = {
  id: 1, tree_key: 'accuracy-risk', name: '设备精度风险判定', description: '校准超差先看',
  workspace_id: 7, status: 'active', version: 3, is_active: 1,
  tree_doc: { root: { type: 'branch', label: '精度风险判定', op: 'all', children: [] } },
  required_metrics: ['扫描精度偏差均值'], apply_sites: [{ site_code: 'cn', datasource: '华东仓' }],
  updated_at: '2026-11-12 09:00:00',
};
const DRAFT_TREE = { ...ACTIVE_TREE, id: 2, tree_key: 'case-backlog', name: '积压风险（草案）', status: 'draft' };

const HIT_RUN = {
  id: 91, tree_key: 'accuracy-risk', tree_name: '设备精度风险判定', tree_version: 3,
  trigger_type: 'manual', site_code: 'cn', verdict: 'hit', verdict_reason: '',
  hit_path: ['精度风险判定', '校准超差'],
  advice_snapshot: [{ advice_key: 'adv_recalibrate', action: '安排现场校准', rationale: '连续 7 日超差', severity: 'high', kb_refs: ['calibration-sop'] }],
  evidence: [{ metric: '扫描精度偏差均值', comparator: '>=', threshold: 0.05, measured: 0.08, unit: 'mm', time_window: '7d', judged: true }],
  created_at: '2026-11-12 09:10:00',
};
const PARTIAL_OUTCOME = {
  tree_key: 'accuracy-risk', tree_name: '设备精度风险判定', tree_version: 3, verdict: 'partial',
  reason: '本树还需指标「校准记录数」的取数，未完整评估',
  hit_path: [], advice: [], missing_metrics: ['校准记录数'],
  evidence: [{ metric: '扫描精度偏差均值', comparator: '>=', threshold: 0.05, measured: null, judged: false, note: '实测值未能取得，本条未判定' }],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/ws/7/decisions']}>
      <Routes>
        <Route path="/ws/:workspaceId/decisions" element={<DecisionCenter />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url.endsWith('/query/trees')) return { data: { items: [ACTIVE_TREE, DRAFT_TREE], total: 2 } };
    if (url.endsWith('/query/runs')) return { data: { items: [HIT_RUN], total: 1, page: 1, size: 100 } };
    return { data: { items: [], total: 0 } };
  });
  vi.mocked(client.post).mockResolvedValue({ data: PARTIAL_OUTCOME } as any);
});
afterEach(() => cleanup());

describe('决策建议消费页', () => {
  it('清单显示树名/状态/适用站点/最近判定，且不提供编辑入口', async () => {
    renderPage();
    expect(await screen.findByText('设备精度风险判定')).toBeInTheDocument();
    expect(screen.getByText('已发布')).toBeInTheDocument();
    expect(screen.getByText('草案')).toBeInTheDocument();
    expect(screen.getAllByText('cn').length).toBeGreaterThan(0);
    // 最近判定列显示上一次结论与出口，不是空白
    expect(screen.getByText('命中')).toBeInTheDocument();
    expect(screen.getByText(/手动评估/)).toBeInTheDocument();
    // 消费面没有编辑/发布：配置面在数据中台，这里出现就是第二个配置入口
    expect(screen.queryByRole('button', { name: /发布/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /新建决策树/ })).not.toBeInTheDocument();
  });

  it('草案树不给评估按钮（草案未生效不参与判定）', async () => {
    renderPage();
    await screen.findByText('设备精度风险判定');
    const buttons = screen.getAllByRole('button', { name: '评估' });
    expect(buttons).toHaveLength(2);
    expect(buttons[1]).toBeDisabled();
    expect(buttons[0]).toBeEnabled();
  });

  it('从未评估的树显式说明"不代表无风险"，不演成没有风险', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/query/trees')) return { data: { items: [ACTIVE_TREE], total: 1 } };
      return { data: { items: [], total: 0, page: 1, size: 100 } };
    });
    renderPage();
    expect(await screen.findByText('从未评估（不代表无风险）')).toBeInTheDocument();
  });

  it('partial 结论显示原因与缺失指标名，并给「完整评估」入口', async () => {
    renderPage();
    await screen.findByText('设备精度风险判定');
    fireEvent.click(screen.getAllByRole('button', { name: '评估' })[0]);
    expect(await screen.findByText('本树还需指标「校准记录数」的取数，未完整评估')).toBeInTheDocument();
    expect(screen.getByText(/本树还需指标：校准记录数/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '完整评估' })).toBeInTheDocument();
    // 未判定的依据行不得显示成空值（伪装成"这一条没问题"）
    expect(screen.getByText('未判定')).toBeInTheDocument();
  });

  it('403 时显示不可见提示而不是空清单（权限口径唯一，前端不自建判断）', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/query/trees')) {
        const err: any = new Error('forbidden');
        err.response = { status: 403, data: { detail: '无权限查看决策建议（decision:read）' } };
        throw err;
      }
      return { data: { items: [], total: 0 } };
    });
    renderPage();
    expect(await screen.findByText('当前角色不可见决策建议')).toBeInTheDocument();
    expect(screen.getByText(/decision:read/)).toBeInTheDocument();
    expect(screen.queryByText('重新评估全部')).not.toBeInTheDocument();
    expect(vi.mocked(toast.error)).not.toHaveBeenCalled();
  });

  it('命中详情按 label 面包屑 + 业务名依据渲染，不出现物理表列名', async () => {
    renderPage();
    await screen.findByText('设备精度风险判定');
    fireEvent.click(screen.getByText('命中'));
    expect(await screen.findByLabelText('命中路径')).toBeInTheDocument();
    expect(screen.getByText('校准超差')).toBeInTheDocument();
    const panel = screen.getByLabelText('评估详情');
    expect(panel.textContent).toContain('扫描精度偏差均值');
    expect(panel.textContent).not.toMatch(/equipment_metrics|datasource_id|SELECT /i);
    // 严重级与建议正文都来自文案库快照
    expect(screen.getByText('安排现场校准')).toBeInTheDocument();
    expect(screen.getByText('高')).toBeInTheDocument();
  });
});
