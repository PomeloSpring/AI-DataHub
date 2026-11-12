// 报告「创建同款定时任务」弹窗：补一个执行周期（+ 可选的报告模板），
// 可复跑问题**预填但可改**（登记的句子口语化/归位不准时，属主是最终裁判）。
//
// 这里刻意没有「执行 AS-BOT」「数据源」两个下拉：任务每次执行都按属主（=报告属主）的角色
// 实时继承能力与可查范围，让用户挑一个他看不见也管不到的内部标识既不可读，也把平台职责
// 推给了使用者。弹窗自己也不得把 as_bot_key/datasource_id 塞进请求体（服务端会剥，但
// 前端先不传才叫口径一致）。
// 唯一的下拉是**报告模板**：它是版式资产不是资源（显示模板名），选了就像时任务表单一
// 样把 report_template_key 带下去，不选则不传（服务端不会静默挑一个模板）。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ReportTaskDialog from '@/components/ReportTaskDialog';
import * as reportApi from '@/api/report';
import * as taskApi from '@/api/scheduledTask';

vi.mock('@/api/report', () => ({
  createTaskFromReport: vi.fn(),
  listReportTemplates: vi.fn(),
}));
vi.mock('@/api/scheduledTask', () => ({ triggerScheduledTask: vi.fn() }));
vi.mock('@/components/CronInput', () => ({ default: () => null }));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const detail = (over: any = {}): reportApi.ReportDetail => ({
  id: 843, title: '最近7天的案例数据分布', origin: {
    channel: 'chat', conversation_id: 146, question: '查看最近7天的案例数量', site_code: 'cn',
    as_bot_key: 'nl2sql_expert', datasource_id: 5, ...over,
  },
} as unknown as reportApi.ReportDetail);

beforeEach(() => {
  vi.mocked(reportApi.createTaskFromReport).mockResolvedValue({ task_id: 77, name: '定时报告' });
  vi.mocked(taskApi.triggerScheduledTask).mockResolvedValue({ success: true } as any);
  // afterEach 的 resetAllMocks 会清掉 Mock 工厂里的实现，候选清单必须每例重新给
  vi.mocked(reportApi.listReportTemplates).mockResolvedValue([
    { id: '30', name: '周报版式', format: 'markdown', theme: 'finance', usable: true },
  ]);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function open(report: reportApi.ReportDetail | null) {
  return render(<ReportTaskDialog open report={report} workspaceId={3} onClose={() => {}} />);
}

describe('从报告创建定时任务', () => {
  it('没有资源选择器（AS-BOT/数据源都不出现），唯一下拉是报告模板', async () => {
    open(detail());
    expect(screen.queryByText(/执行 AS-BOT/)).not.toBeInTheDocument();
    expect(screen.queryByText('数据源')).not.toBeInTheDocument();
    expect(screen.getByText(/按你的角色与权限执行/)).toBeInTheDocument();
    // 报告模板是版式资产（不是数据源/AS-BOT 那类内部标识），选项显示模板名
    expect(screen.getByText('报告模板（可选）')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('combobox'));
    expect(screen.getByRole('option', { name: '不用模板（按平台通用版式输出）' })).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: '周报版式（MD）' })).toBeInTheDocument();
  });

  it('请求体只带任务名、周期与可复跑问题，内部标识不外传', async () => {
    open(detail());
    await userEvent.clear(screen.getByLabelText('任务名称'));
    await userEvent.type(screen.getByLabelText('任务名称'), '案例数量周报');
    await userEvent.click(screen.getByRole('button', { name: '创建任务' }));
    await waitFor(() => expect(reportApi.createTaskFromReport).toHaveBeenCalled());
    const [reportId, payload] = vi.mocked(reportApi.createTaskFromReport).mock.calls[0];
    expect(reportId).toBe(843);
    expect(payload).toEqual({
      name: '案例数量周报', cron_expression: '0 9 * * *', question: '查看最近7天的案例数量',
    });
    expect(payload).not.toHaveProperty('as_bot_key');
    expect(payload).not.toHaveProperty('datasource_id');
  });

  it('复用的分析问题预填登记值且可改，改后的那句才是任务要跑的', async () => {
    open(detail());
    const box = screen.getByLabelText('复用的分析问题');
    expect(box).toHaveValue('查看最近7天的案例数量');
    await userEvent.clear(box);
    await userEvent.type(box, '统计近7天各省份案例数，输出 HTML 报告');
    await userEvent.click(screen.getByRole('button', { name: '创建任务' }));
    await waitFor(() => expect(reportApi.createTaskFromReport).toHaveBeenCalled());
    const [, payload] = vi.mocked(reportApi.createTaskFromReport).mock.calls[0];
    expect(payload.question).toBe('统计近7天各省份案例数，输出 HTML 报告');
  });

  it('问题被归位过时如实标注（这句不是本轮原话）', async () => {
    open(detail({ question_note: '本轮只改交付形态/补充内容、没有重新取数' }));
    expect(await screen.findByText(/本轮只改交付形态/)).toBeInTheDocument();
  });

  it('报告没登记原始问题时：不再堵死入口，让人写一句；空着则不给提交', async () => {
    open(detail({ question: '' }));
    const box = screen.getByLabelText('复用的分析问题');
    expect(box).toHaveValue('');
    expect(screen.getByRole('button', { name: '创建任务' })).toBeDisabled();
    expect(screen.getByText(/未登记原始分析问题/)).toBeInTheDocument();
    await userEvent.type(box, '统计各区域案例数');
    await userEvent.click(screen.getByRole('button', { name: '创建任务' }));
    await waitFor(() => expect(reportApi.createTaskFromReport).toHaveBeenCalled());
    const [, payload] = vi.mocked(reportApi.createTaskFromReport).mock.calls[0];
    expect(payload.question).toBe('统计各区域案例数');
  });

  it('选了报告模板才带 report_template_key（不选不传，服务端不得静默挑一个）', async () => {
    open(detail());
    fireEvent.click(screen.getByRole('combobox'));
    fireEvent.click(await screen.findByText('周报版式（MD）'));
    await userEvent.click(screen.getByRole('button', { name: '创建任务' }));
    await waitFor(() => expect(reportApi.createTaskFromReport).toHaveBeenCalled());
    const [, payload] = vi.mocked(reportApi.createTaskFromReport).mock.calls[0];
    expect(payload.report_template_key).toBe('30');
  });

  it('勾选立即执行才派发一次触发', async () => {
    open(detail());
    await userEvent.click(screen.getByLabelText(/创建后立即执行一次/));
    await userEvent.click(screen.getByRole('button', { name: '创建任务' }));
    await waitFor(() => expect(taskApi.triggerScheduledTask).toHaveBeenCalledWith(77));
  });
});
