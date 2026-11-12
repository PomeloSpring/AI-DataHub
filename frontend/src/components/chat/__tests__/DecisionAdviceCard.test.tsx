// Chat 决策建议卡（计划 §出口 1 的前端消费面）。
//
// 卡片的存在意义只有一条：**服务端已经判完，前端不得再判一遍**。因此这里的
// 回归全在"如实渲染"上：
//   1. 无 items 且无原因 → 整块不渲染（闲聊轮不该出现任何决策痕迹）；
//   2. skipped/partial → 原因原样显示，且不得显示成"数据正常/没有问题"；
//   3. 依据只有业务名：物理表列名、datasource_id、SQL 出现即为泄露（护栏 §7）；
//   4. partial 给「完整评估」入口（否则用户只看到一句无法行动的公告）。
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import DecisionAdviceCard from '../DecisionAdviceCard';
import type { DecisionAdvicePayload } from '@/api/decisions';

const HIT: DecisionAdvicePayload = {
  items: [{
    tree_key: 'accuracy-risk', tree_name: '设备精度风险判定', tree_version: 4,
    verdict: 'hit', reason: '', hit_path: ['精度风险判定', '校准超差'],
    advice: [{ advice_key: 'adv_recalibrate', action: '安排一次现场校准',
               rationale: '连续 7 日偏差均值高于出厂标称', severity: 'high',
               kb_refs: ['calibration-sop'] }],
    evidence: [{ metric: '扫描精度偏差均值', comparator: '>=', threshold: 0.05,
                 measured: 0.081, unit: 'mm', time_window: '7d', judged: true }],
    site_code: 'cn', mode: 'turn',
  }],
  skipped_reason: '',
};

const PARTIAL: DecisionAdvicePayload = {
  items: [{
    tree_key: 'case-backlog', tree_name: '积压风险判定', tree_version: 2,
    verdict: 'partial', reason: '本树还需指标「近7天案例数」的取数，未完整评估',
    hit_path: [], advice: [], missing_metrics: ['近7天案例数'],
    evidence: [{ metric: '未结案数', comparator: '>', threshold: 50, measured: 81, judged: true }],
  }],
  skipped_reason: '',
};

beforeEach(() => vi.clearAllMocks());
afterEach(() => cleanup());

function renderCard(advice: DecisionAdvicePayload | undefined, onEvaluate?: (k: string) => void) {
  return render(
    <MemoryRouter>
      <DecisionAdviceCard advice={advice} onEvaluate={onEvaluate} />
    </MemoryRouter>,
  );
}

describe('DecisionAdviceCard', () => {
  it('无建议也无原因时整块不渲染（前端不自行制造决策痕迹）', () => {
    const { container } = renderCard(undefined);
    expect(container).toBeEmptyDOMElement();
    renderCard({ items: [], skipped_reason: '' });
    expect(screen.queryByText('决策建议')).not.toBeInTheDocument();
  });

  it('命中卡显示树名、路径面包屑、建议正文、severity 与依据表', () => {
    renderCard(HIT);
    expect(screen.getByLabelText('决策建议')).toBeInTheDocument();
    expect(screen.getByText('设备精度风险判定')).toBeInTheDocument();
    expect(screen.getByLabelText('命中路径')).toHaveTextContent('精度风险判定');
    expect(screen.getByText('安排一次现场校准')).toBeInTheDocument();
    expect(screen.getByText('高')).toBeInTheDocument();
    const table = screen.getByLabelText('判定依据');
    expect(table).toHaveTextContent('扫描精度偏差均值');
    expect(table).toHaveTextContent('0.081');
    expect(table).toHaveTextContent('0.05');
    // 知识出处指向内容中心已发布文章
    expect(screen.getByRole('link', { name: /calibration-sop/ })).toHaveAttribute(
      'href', '/content/preview/calibration-sop');
  });

  it('卡片里一个物理标识都不出现（依据只回业务名）', () => {
    const { container } = renderCard(HIT);
    expect(container.textContent).not.toMatch(
      /datasource_id|equipment_metrics|physical_table|catalog_ref|provenance|SELECT /i);
  });

  it('partial 显示未完整评估原因并给「完整评估」入口', () => {
    const onEvaluate = vi.fn();
    renderCard(PARTIAL, onEvaluate);
    expect(screen.getByText('本树还需指标「近7天案例数」的取数，未完整评估')).toBeInTheDocument();
    // 结论徽标上也写“未完整评估”：状态与解释两处都能看到，不只有一个可忽略的原因
    expect(screen.getAllByText(/未完整评估/).length).toBeGreaterThanOrEqual(2);
    fireEvent.click(screen.getByRole('button', { name: '完整评估' }));
    expect(onEvaluate).toHaveBeenCalledWith('case-backlog');
  });

  it('skipped_reason 即使有 items 也照样显示（不折叠成"没问题"）', () => {
    renderCard({
      items: [HIT.items[0]],
      skipped_reason: '本轮有 1 次取数结果为展示而截断，无法完整还原，这部分未做判定',
    });
    expect(screen.getByRole('status')).toHaveTextContent('截断');
  });

  it('多棵树命中时默认只展开一条，其余按数量给入口', () => {
    renderCard({
      items: [HIT.items[0], { ...PARTIAL.items[0], tree_name: '另一棵树' }],
      skipped_reason: '',
    });
    expect(screen.getByText('设备精度风险判定')).toBeInTheDocument();
    expect(screen.queryByText('另一棵树')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '再看 1 棵树的判定' }));
    expect(screen.getByText('另一棵树')).toBeInTheDocument();
  });
});
