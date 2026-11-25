/**
 * 质量概览「问题 TOP 10」必须能点进去看失败原因：
 * - 点行 → 拉该规则最近检查结果（/quality/results?rule_id=），显式渲染失败原因（执行异常文案或失败行数/通过率）；
 * - 明细查询失败 → 报错，不得退化成「暂无检查记录」；
 * - 失败样本按需实时重跑（/quality/rules/{id}/samples），点按钮才取，样本按列渲染。
 */
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import QualityOverview from '../QualityOverview';
import { qualityApi } from '@/api/quality';

vi.mock('@/api/quality', () => ({
  qualityApi: {
    getDashboard: vi.fn(),
    getResults: vi.fn(),
    getRuleSamples: vi.fn(),
    generateReport: vi.fn(),
    executeAll: vi.fn(),
    deleteReport: vi.fn(),
    deleteResultsBatch: vi.fn(),
  },
}));

const DASHBOARD = {
  overall_score: 88.5,
  pass_rate: 92.3,
  total_rules: 14,
  active_rules: 14,
  recent_reports: [],
  top_issues: [{
    rule_id: 7,
    rule_name: 'dws_case_metric_d 数据存在性',
    rule_type: 'custom_sql',
    target_table: 'dws_case_metric_d',
    severity: 'medium',
    failure_count: 1,
    last_failure_at: '2026-11-25 10:00:00',
  }],
};

const FAILED_RESULT = {
  id: 1,
  check_time: '2026-11-25T10:00:00',
  passed: 0,
  total_rows: 100,
  failed_rows: 3,
  pass_rate: 97,
  detail: { status: 'failed' },
};

const openTopIssue = async () => {
  render(<QualityOverview />);
  // 问题行是 role=button 的可点行（aria-label 带「失败原因」）
  await userEvent.click(await screen.findByRole('button', { name: /失败原因$/ }));
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(qualityApi.getDashboard).mockResolvedValue({ data: DASHBOARD } as never);
  vi.mocked(qualityApi.getResults).mockResolvedValue({
    data: { total: 1, page: 1, page_size: 10, items: [FAILED_RESULT] },
  } as never);
  vi.mocked(qualityApi.deleteReport).mockResolvedValue({ data: { id: 2 } } as never);
  vi.mocked(qualityApi.deleteResultsBatch).mockResolvedValue({
    data: { deleted_count: 1, deleted: [1], failed: [], message: '' },
  } as never);
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('质量概览 — 问题 TOP10 可点开查看失败原因', () => {
  it('点行拉该规则最近结果，并渲染数据失败原因（失败行数/总行数/通过率）', async () => {
    await openTopIssue();

    expect(qualityApi.getResults).toHaveBeenCalledWith(
      expect.objectContaining({ rule_id: 7, page: 1 }),
    );
    expect(await screen.findByText(/3 行不符合规则（共 100 行，通过率 97\.00%）/)).toBeInTheDocument();
  });

  it('执行异常保留后端错误文案（detail.error），不笼统显示为「失败」', async () => {
    vi.mocked(qualityApi.getResults).mockResolvedValue({
      data: {
        total: 1,
        items: [{
          ...FAILED_RESULT,
          passed: 0,
          total_rows: 0,
          failed_rows: 0,
          pass_rate: 0,
          detail: { status: 'unknown', error: '质量检查未完成，请检查权限或联系管理员' },
        }],
      },
    } as never);

    await openTopIssue();

    expect(await screen.findByText('质量检查未完成，请检查权限或联系管理员')).toBeInTheDocument();
    // 执行异常单独标记，不与数据失败混为一谈
    expect(screen.getAllByText('执行异常').length).toBeGreaterThan(0);
  });

  it('明细查询失败显式报错，不退化成「暂无检查记录」', async () => {
    vi.mocked(qualityApi.getResults).mockRejectedValue(new Error('boom'));

    await openTopIssue();

    expect(await screen.findByText('查询检查明细失败')).toBeInTheDocument();
    expect(screen.queryByText('暂无检查记录')).not.toBeInTheDocument();
  });

  it('失败样本按需实时重跑，样本行按列渲染', async () => {
    vi.mocked(qualityApi.getRuleSamples).mockResolvedValue({
      data: { samples: [{ case_id: 'C-1', amount: 0 }], error: null },
    } as never);

    await openTopIssue();
    expect(qualityApi.getRuleSamples).not.toHaveBeenCalled();

    await userEvent.click(await screen.findByRole('button', { name: '查看失败样本' }));

    expect(qualityApi.getRuleSamples).toHaveBeenCalledWith(7);
    expect(await screen.findByText('C-1')).toBeInTheDocument();
    expect(screen.getByText('amount')).toBeInTheDocument();
  });

  it('失败样本取数异常时显式提示（含样本端点返回的 error）', async () => {
    vi.mocked(qualityApi.getRuleSamples).mockResolvedValue({
      data: { samples: [], error: '质量检查未完成，请检查权限或联系管理员' },
    } as never);

    await openTopIssue();
    await userEvent.click(await screen.findByRole('button', { name: '查看失败样本' }));

    expect(
      await screen.findByText('质量检查未完成，请检查权限或联系管理员'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/未取到失败样本/)).not.toBeInTheDocument();
  });
});

const DASHBOARD_WITH_REPORT = {
  ...DASHBOARD,
  recent_reports: [{
    id: 2,
    report_date: '2026-11-25',
    overall_score: 90,
    pass_rate: 92.3,
    total_checks: 14,
    passed_checks: 9,
    failed_checks: 5,
  }],
};

describe('质量概览 — 质量报告与问题记录可删除', () => {
  it('质量报告：确认后调 DELETE 并重新拉概览', async () => {
    vi.mocked(qualityApi.getDashboard).mockResolvedValue({ data: DASHBOARD_WITH_REPORT } as never);
    render(<QualityOverview />);

    await userEvent.click(await screen.findByRole('button', { name: /删除报告$/ }));
    expect(await screen.findByText('确认删除该质量报告？删除后不可恢复。')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '删除' }));

    expect(qualityApi.deleteReport).toHaveBeenCalledWith(2);
    expect(vi.mocked(qualityApi.getDashboard).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('问题记录：只拉失败结果（passed=0），默认勾选可取消', async () => {
    render(<QualityOverview />);

    await userEvent.click(await screen.findByRole('button', {
      name: '删除问题记录 - dws_case_metric_d 数据存在性',
    }));

    expect(qualityApi.getResults).toHaveBeenCalledWith(expect.objectContaining({
      rule_id: 7, passed: 0, page: 1,
    }));
    const checkbox = await screen.findByRole('checkbox', { name: /删除$/ });
    expect(checkbox).toBeChecked();

    await userEvent.click(screen.getByRole('button', { name: '删除选中（1 条）' }));

    expect(qualityApi.deleteResultsBatch).toHaveBeenCalledWith([1]);
  });

  it('部分记录删除失败：保留弹窗并可重试，不静默当成功', async () => {
    vi.mocked(qualityApi.deleteResultsBatch).mockResolvedValue({
      data: { deleted_count: 0, deleted: [], failed: [{ id: 1, reason: '问题记录不存在' }] },
    } as never);
    render(<QualityOverview />);

    await userEvent.click(await screen.findByRole('button', {
      name: '删除问题记录 - dws_case_metric_d 数据存在性',
    }));
    await userEvent.click(await screen.findByRole('button', { name: '删除选中（1 条）' }));

    // 弹窗不关、失败项仍在，可直接重试
    expect(await screen.findByRole('button', { name: '删除选中（1 条）' })).toBeInTheDocument();
    expect(screen.getByText('删除问题记录')).toBeInTheDocument();
  });
});