// 报告中心 → 内容管理的「提交到内容管理审核」入口契约。
//
// 锁四件事（都做错过方向）：
// 1. **入口看得见**：动作收在「更多」里，但没权限也照样列出来并把原因写进 title
//    （menu-permission-sync / no-silent-degradation：藏掉入口等于让人以为功能没做）；
// 2. **归集预判在点下去之前**：弹框打开就调 /content/promotions/match，把服务端结论
//    原样显示（追加到《X》第 N 期 / 新建一篇），前端不自己猜同主题该不该新建；
// 3. **半成功两段说**：门禁没过时草稿确实已存 —— gate_blocked 逐条列出并说明没进审核；
// 4. **失败原样端出**：非本人报告、正文取不到、快照失效都由服务端给文案，这里不转述。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ReportsCenter from '../ReportsCenter';
import { usePermissionStore } from '@/stores/permissionStore';
import client from '@/api/client';

const TITLE = '口腔扫描行业月报';
const BODY_TEXT = '## 行业概览\n\n11 月出货量同比 +20%。';

const REPORT = {
  id: 900, task_id: 11, log_id: null, title: TITLE, format: 'markdown',
  access_mode: 'private', view_count: 3, created_at: '2026-11-01T09:00:00',
  generation_status: 'ready', source_type: 'scheduled', workspace_id: 3,
  series_key: 'task:11',
  origin: { channel: 'scheduled', question: '每月扫描行业概览' },
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
  saveReportAsTemplate: vi.fn(),
  isEditableReport: () => false, isDeletableReport: () => false,
}));
vi.mock('@/api/scheduledTask', () => ({ triggerScheduledTask: vi.fn() }));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock('@/components/ReportContent', () => ({
  default: ({ content }: { content: string }) => <div data-testid="body">{content}</div>,
}));
vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const matched = {
  title: TITLE, summary: '11 月出货量同比 +20%。', kind: 'markdown', kind_label: '站内正文',
  body_chars: BODY_TEXT.length,
  matched: { slug: 'kou-qiang-sao-miao-hang-ye-yue-bao', title: '口腔扫描行业月报',
             status: 'published', release_count: 2 },
  next_release_after: 2,
  // 服务端文案（不是 Markdown）：弹框里原样回显，前端不另写一套结论
  reason: '这份报告的历次版本已提升过，本次会作为新一期追加到同一篇内容里',
  versioning_options: [{ value: 'keep_history', label: '保留历史期次' },
                       { value: 'latest_only', label: '只保留最新版本' }],
  categories: [{ key: 'whitepaper', label: '技术白皮书' }],
};

const fresh = { ...matched, matched: null, next_release_after: 0,
  reason: '这份报告还没提升过，本次会新建一篇内容草稿' };

const grant = (perms: string[]) =>
  usePermissionStore.setState({ loaded: true, unrestricted: false, perms, allowedMenus: null });

const openMore = async () => {
  fireEvent.click(await waitFor(() => screen.getByTestId('action-overflow-menu')));
  return screen.findByRole('menu');
};

const openPromote = async () => {
  await openMore();
  fireEvent.click(await screen.findByRole('menuitem', { name: /提交到内容管理审核/ }));
  return screen.findByRole('heading', { name: '提交到内容管理审核' });
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockResolvedValue({ data: matched } as never);
});
afterEach(() => {
  cleanup();
  usePermissionStore.setState({ loaded: false, unrestricted: true, perms: null, allowedMenus: null });
});

describe('报告中心的「提交到内容管理审核」', () => {
  it('没有内容编辑权时入口照样列出并写明置灰原因（不是把功能藏掉）', async () => {
    grant([]);
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('body').textContent).toBe(BODY_TEXT));

    await openMore();
    const item = await screen.findByRole('menuitem', { name: /提交到内容管理审核/ });
    expect(item).toBeDisabled();
    expect(String(item.getAttribute('title'))).toContain('article:manage');
    expect(client.get).not.toHaveBeenCalledWith('/content/promotions/match', expect.anything());
  });

  it('有编辑权才开口：弹框先演归集预判，说清会追加到哪一篇的第几期之后', async () => {
    grant(['article:manage']);
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await openPromote();

    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/content/promotions/match',
      { params: { report_id: 900 } }));
    expect(await screen.findByText(/本次会作为新一期追加到同一篇内容里/)).toBeInTheDocument();
    expect(screen.getByText(/当前已发布 2 期/)).toBeInTheDocument();
    // 归集命中时栏目沿用原条目，不让人改出一个"同主题换栏目"的第二套编排
    expect(screen.getByRole('combobox', { name: '选择内容栏目' })).toBeDisabled();
  });

  it('没提升过时说明会新建一篇，并且必须先选栏目才能提交', async () => {
    grant(['article:manage']);
    vi.mocked(client.get).mockResolvedValue({ data: fresh } as never);
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await openPromote();

    expect(await screen.findByText(/本次会新建一篇内容草稿/)).toBeInTheDocument();
    const confirm = () => screen.getByRole('button', { name: /提交到内容管理/ });
    expect(confirm()).toBeDisabled();

    fireEvent.click(screen.getByRole('combobox', { name: '选择内容栏目' }));
    fireEvent.click(await screen.findByRole('option', { name: '技术白皮书' }));
    await waitFor(() => expect(confirm()).toBeEnabled());
  });

  it('提交带上报表标识与所选策略；成功回执给内容中心的跳转（只有 slug，没有内部 id）', async () => {
    grant(['article:manage']);
    vi.mocked(client.get).mockResolvedValue({ data: fresh } as never);
    vi.mocked(client.post).mockResolvedValue({
      data: { slug: 'kou-qiang-row-bao', title: TITLE, status: 'in_review', created: true,
              submitted: true, gate_blocked: [],
              warnings: [{ code: 'refs_missing', message: '正文里的数字没有外部出处' }],
              versioning: 'latest_only', versioning_label: '只保留最新版本',
              release: 0, release_label: '', release_count: 0, is_live: false,
              message: '已新建内容草稿《口腔扫描行业月报》并写入正文。' },
    } as never);
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await openPromote();

    fireEvent.click(screen.getByRole('combobox', { name: '选择内容栏目' }));
    fireEvent.click(await screen.findByRole('option', { name: '技术白皮书' }));
    fireEvent.click(screen.getByRole('combobox', { name: '选择版本策略' }));
    fireEvent.click(await screen.findByRole('option', { name: '只保留最新版本' }));
    fireEvent.click(screen.getByRole('button', { name: /提交到内容管理/ }));

    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/content/promotions', {
      report_id: 900, category: 'whitepaper', title: TITLE,
      summary: '11 月出货量同比 +20%。', versioning: 'latest_only', submit: true,
    }));
    expect(await screen.findByText(/已新建内容草稿并提交审核/)).toBeInTheDocument();
    // 软提醒不丢：审核人要逐条确认的东西不能只留在服务端回执里
    expect(screen.getByText(/正文里的数字没有外部出处/)).toBeInTheDocument();
    // 回执体里只有业务名：报表 id / 条目 id 这类内部标识不得出现在读者看到的文案里
    expect(screen.queryByText(/report_id/)).not.toBeInTheDocument();
  });

  it('门禁没过时把“草稿已存、未进审核”与逐条原因一起说清（不报成全成功也不报成失败）', async () => {
    grant(['article:manage']);
    vi.mocked(client.get).mockResolvedValue({ data: fresh } as never);
    vi.mocked(client.post).mockResolvedValue({
      data: { slug: 'a-b', title: TITLE, status: 'draft', created: true, submitted: false,
              gate_blocked: ['正文还有未填写的占位（【待填…）', '还没有选择栏目'],
              warnings: [], versioning: 'keep_history', versioning_label: '保留历史期次',
              release: 0, release_label: '', release_count: 0, is_live: false,
              message: '草稿已保存到内容中心，但未能提交审核：正文还有未填写的占位（【待填…）；还没有选择栏目' },
    } as never);
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await openPromote();

    fireEvent.click(screen.getByRole('combobox', { name: '选择内容栏目' }));
    fireEvent.click(await screen.findByRole('option', { name: '技术白皮书' }));
    fireEvent.click(screen.getByRole('button', { name: /提交到内容管理/ }));

    expect(await screen.findByText(/草稿已保存到内容中心，但未提交审核/)).toBeInTheDocument();
    expect(screen.getByText(/^正文还有未填写的占位/)).toBeInTheDocument();
    expect(screen.getByText('还没有选择栏目')).toBeInTheDocument();
    expect(screen.getByText(/改完这些再提交即可/)).toBeInTheDocument();
  });

  it('服务端拒因原样回显（正文取不到/快照失效/条目正在审核中都不转述成“操作失败”）', async () => {
    grant(['article:manage']);
    vi.mocked(client.get).mockResolvedValue({ data: fresh } as never);
    vi.mocked(client.post).mockRejectedValue({
      response: { status: 403, data: { detail: '只能提交本人的报告：这份报告不属于当前账号' } },
    } as never);
    render(<MemoryRouter><ReportsCenter /></MemoryRouter>);
    await openPromote();

    fireEvent.click(screen.getByRole('combobox', { name: '选择内容栏目' }));
    fireEvent.click(await screen.findByRole('option', { name: '技术白皮书' }));
    fireEvent.click(screen.getByRole('button', { name: /提交到内容管理/ }));

    expect(await screen.findByText(/这份报告不属于当前账号/)).toBeInTheDocument();
    expect(screen.queryByText(/操作失败/)).not.toBeInTheDocument();
  });
});
