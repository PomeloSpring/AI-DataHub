// 报告详情工具条的排版纪律：常驻只留主操作，次要动作收进「更多」（用户实拍：按钮越加越挤）。
//
// 这组用例锁两件事，防止回退成"每加一个能力就多一个常驻按钮"：
// * 头栏不再平铺 创建为定时任务 / 另存为模板 / 编辑 / 删除，只有一个「更多」开关；
// * 收纳不等于藏起来 —— 四项都在菜单里、文案一字不差、点下去真的能用，
//   置灰原因仍写进 title（能看见的入口不能点下去才知道不行）。
// 只有一版时不再给一个"只能选中自己"的版本下拉（多版场景由 Layout/Versions 用例覆盖）。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ReportsCenter from '../ReportsCenter';
import { fetchReportContent, listReports } from '@/api/report';

const TITLE = '全国页面访问统计';
const BODY_TEXT = '## 核心结论\n\n近 7 天 PV 1,234,567';

const REPORT = {
  id: 900, task_id: null, log_id: null, title: TITLE, format: 'markdown',
  access_mode: 'private', view_count: 3, created_at: '2026-11-01T09:00:00',
  generation_status: 'ready', source_type: 'conversation', workspace_id: 3,
  series_key: 'report:900',
  origin: { channel: 'chat', question: '统计全国页面访问' },
  refresh: { available: true, channel: 'report', action_label: '用最新数据重新生成', hint: '' },
};

vi.mock('@/api/report', () => ({
  listReports: vi.fn(() => Promise.resolve([REPORT])),
  getReportDetail: vi.fn(() => Promise.resolve({ ...REPORT, content_url: 'https://oss.test/900' })),
  fetchReportContent: vi.fn(() => Promise.resolve(BODY_TEXT)),
  updateReport: vi.fn(), deleteReport: vi.fn(),
  refreshReportStream: vi.fn(() => Promise.resolve()), createTaskFromReport: vi.fn(),
  listReportTemplates: vi.fn(() => Promise.resolve([])),
  fetchReportTemplateSkeleton: vi.fn(),
  saveReportAsTemplate: vi.fn(() => Promise.resolve({ component_id: 77, name: 't', layout: {} })),
  isEditableReport: () => true, isDeletableReport: () => true,
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
const openMore = async () => {
  fireEvent.click(await waitFor(() => screen.getByTestId('action-overflow-menu')));
  return screen.findByRole('menu');
};

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); });

describe('报告详情工具条', () => {
  it('次要动作不再各占一位：常驻只剩重跑与导出，其余在「更多」里', async () => {
    await renderPage();
    ['创建为定时任务', '另存为模板', '删除'].forEach(name =>
      expect(screen.queryByRole('button', { name })).toBeNull());
    // 单版本不给版本下拉（一个只能选中自己的「版本管理」是噪声）
    expect(screen.queryByTitle('版本管理')).toBeNull();

    const menu = await openMore();
    ['创建为定时任务', '另存为模板', '编辑', '删除'].forEach(label =>
      expect(menu).toHaveTextContent(label));
  });

  it('「更多」里的动作真的能办事（收纳不是把功能关掉）', async () => {
    await renderPage();
    await openMore();
    fireEvent.click(screen.getByRole('menuitem', { name: /另存为模板/ }));
    expect(await screen.findByPlaceholderText('如：周报版式模板')).toBeInTheDocument();
  });

  it('正文没加载出来时，菜单项置灰并写明原因（不得让人以为是按钮坏了）', async () => {
    vi.mocked(fetchReportContent).mockResolvedValueOnce('');
    vi.mocked(listReports).mockResolvedValueOnce([REPORT] as never);
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    const menu = await openMore();
    const btn = screen.getByRole('menuitem', { name: /另存为模板/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute('title')).toContain('正文还没加载');
    expect(menu).toBeInTheDocument();
  });
});
