// 报告中心 ↔ 报告模板的闭环回归：报告中心只**生产**模板（另存为模板），不再消费（新建时套用已退役）。
//
// 这组用例锁的是"模板配置功能到底有没有被用起来 + 入口退役后不留空承诺"：
// * 好报告要能一键沉淀成模板（服务端只抽结构，数字不进模板）；
// * 正文没加载出来时按钮要置灰并说明原因，不能看着能点；
// * 「新建报告」入口已下线：页面不得再留按钮或把出路指向它（报告=分析产物）。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ReportsCenter from '../ReportsCenter';
import { fetchReportContent, listReports, saveReportAsTemplate } from '@/api/report';
import { toast } from '@/lib/toast';

const REPORT = {
  id: 900, task_id: null, log_id: null, title: '全国页面访问统计', format: 'markdown',
  access_mode: 'private', view_count: 3, created_at: '2026-11-01T09:00:00',
  generation_status: 'ready', source_type: 'conversation', workspace_id: 3,
  origin: { channel: 'chat', conversation_id: 12, question: '统计全国页面访问' },
  refresh: { available: true, channel: 'report', action_label: '用最新数据重新生成', hint: '' },
};
const BODY_TEXT = '## 核心结论\n\n近 7 天 PV 1,234,567';

vi.mock('@/api/report', () => ({
  listReports: vi.fn(() => Promise.resolve([REPORT])),
  getReportDetail: vi.fn(() => Promise.resolve({ ...REPORT, content_url: 'https://oss.test/900' })),
  fetchReportContent: vi.fn(() => Promise.resolve(BODY_TEXT)),
  updateReport: vi.fn(), deleteReport: vi.fn(), refreshReportStream: vi.fn(() => Promise.resolve()),
  createTaskFromReport: vi.fn(),
  isEditableReport: () => true, isDeletableReport: () => true,
  saveReportAsTemplate: vi.fn(() => Promise.resolve({
    component_id: 77, name: '全国页面访问统计 版式模板', layout: {},
  })),
}));
vi.mock('@/api/scheduledTask', () => ({ triggerScheduledTask: vi.fn() }));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('@/components/ReportContent', () => ({
  default: ({ content }: { content: string }) => <div data-testid="body">{content}</div>,
}));
vi.mock('@/components/ReportTaskDialog', () => ({ default: () => null }));

const renderPage = async () => {
  render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
  await waitFor(() => expect(screen.getByTestId('body').textContent).toBe(BODY_TEXT));
};

/** 次要动作现在收在「更多」里（头栏按钮太多挤掉了标题），点开才算入口到位 */
const openMore = async () => {
  fireEvent.click(await waitFor(() => screen.getByTestId('action-overflow-menu')));
  return screen.findByRole('menu');
};

// 收进「更多」后动作的 role 从 button 变成 menuitem（title 仍写在这一项自己上，置灰原因依旧可查）
const templateItem = () => screen.getByRole('menuitem', { name: /另存为模板/ });

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); });

describe('报告中心与报告模板的闭环', () => {
  it('「更多」里有「另存为模板」，填名后把本报告版式沉淀给服务端', async () => {
    await renderPage();
    await openMore();
    fireEvent.click(templateItem());
    const nameInput = await screen.findByPlaceholderText('如：周报版式模板');
    expect(nameInput).toHaveValue('全国页面访问统计 版式模板');
    fireEvent.change(nameInput, { target: { value: '运营周报版式' } });
    fireEvent.click(screen.getByRole('button', { name: '存为模板' }));
    await waitFor(() => expect(saveReportAsTemplate).toHaveBeenCalledWith(900, {
      name: '运营周报版式', description: '',
    }));
    // 沉淀后的消费方要说准：新建报告入口已退役，剩下的是任务表单与报告重跑
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('任务表单选模板'));
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('报告重跑'));
  });

  it('正文没加载出来时「另存为模板」不可用，并说明原因（不得让用户以为是按钮坏了）', async () => {
    (fetchReportContent as any).mockResolvedValueOnce('');
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await openMore();
    const btn = await waitFor(() => templateItem());
    await waitFor(() => expect(btn).toBeDisabled());
    expect(btn.getAttribute('title')).toContain('正文还没加载');
  });

  it('「新建报告」入口已退役：不留按钮，也不把出路指向它', async () => {
    await renderPage();
    expect(screen.queryByRole('button', { name: /新建报告/ })).toBeNull();
    expect(screen.queryByText('套用报告模板（可选）')).toBeNull();
    // 存量内容型报告仍可编辑（退役的只是创建口）：卡片图标按钮 + 「更多」里的编辑项，两处都可点
    await openMore();
    const editEntries = screen.getAllByTitle('重命名 / 编辑正文');
    expect(editEntries.length).toBeGreaterThanOrEqual(2);
    editEntries.forEach(btn => expect(btn).toBeEnabled());
  });

  it('清单为空时的出路只指向分析产物（会话存为报告 / 定时任务）', async () => {
    (listReports as any).mockResolvedValueOnce([]);
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    const hint = await screen.findByText(/暂无报告/);
    expect(hint).toHaveTextContent('存为报告');
    expect(hint).not.toHaveTextContent('新建报告');
  });
});
