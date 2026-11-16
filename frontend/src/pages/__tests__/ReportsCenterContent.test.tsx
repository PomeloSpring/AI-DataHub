// 报告中心混入内容中心已发布内容（2026-11-16 用户拍板：内容发布同样在报告中心展示）。
//
// 三条判据（缺一即回归缺陷）：
// 1. 可见性唯一由 `article:read` 裁决：没这把码 → 不发内容请求、不列内容条目（首页资讯区同一把码）；
// 2. 内容条目只读消费：点击跳 `/read/:slug`，不进右栏报告详情、不叠报告版本/重跑/编辑/删除语义；
// 3. 内容读取失败必须显式报错 + 重试，不得静默成「没有内容」（no-silent-degradation）。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
import ReportsCenter from '../ReportsCenter';
import { contentApi } from '@/api/content';
import { usePermissionStore } from '@/stores/permissionStore';

const REPORT = {
  id: 900, task_id: null, log_id: null, title: '全国页面访问统计', format: 'markdown',
  access_mode: 'private', view_count: 3, created_at: '2026-11-01T09:00:00',
  generation_status: 'ready', source_type: 'conversation', workspace_id: 3,
  series_key: 'report:900', origin: {}, refresh: { available: false, channel: '', hint: '' },
};
const ARTICLE = {
  id: 12, slug: 'monthly-insight', title: '10 月经营洞察', summary: '核心指标环比',
  category_label: '经营洞察', category_color: '#6366f1', published_at: '2026-11-10T00:00:00',
  updated_at: '', status: 'published', is_live: true, release: 3,
};

vi.mock('@/api/report', () => ({
  listReports: vi.fn(() => Promise.resolve([REPORT])),
  getReportDetail: vi.fn(() => Promise.resolve({ ...REPORT, content_url: 'https://oss.test/900' })),
  fetchReportContent: vi.fn(() => Promise.resolve('正文')),
  updateReport: vi.fn(), deleteReport: vi.fn(),
  refreshReportStream: vi.fn(() => Promise.resolve()),
  listReportTemplates: vi.fn(() => Promise.resolve([])),
  fetchReportTemplateSkeleton: vi.fn(), saveReportAsTemplate: vi.fn(),
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
vi.mock('@/components/ReportPromoteDialog', () => ({ default: () => null }));
vi.mock('@/api/content', () => ({
  contentApi: { list: vi.fn(() => Promise.resolve({ data: { items: [ARTICLE], total: 1 } })) },
  contentError: (e: any) => String(e),
}));
// 阅读正文组件只验“就地内嵌、不离开报告中心”；它自身的取数/渲染由 ReadArticle 用例覆盖
vi.mock('@/pages/home/ArticleReader', () => ({
  default: ({ slug, countView }: { slug: string; countView?: boolean }) => (
    <div data-testid="reader" data-countview={String(countView)}>内嵌阅读:{slug}</div>
  ),
}));

const ReadProbe = () => {
  const { slug = '' } = useParams();
  return <div data-testid="read-probe">阅读页:{slug}</div>;
};
const renderPage = () => render(
  <MemoryRouter initialEntries={['/ws/3/reports']}>
    <Routes>
      <Route path="/ws/:workspaceId/reports" element={<ReportsCenter />} />
      <Route path="/read/:slug" element={<ReadProbe />} />
    </Routes>
  </MemoryRouter>,
);
const grant = (...perms: string[]) =>
  usePermissionStore.setState({ loaded: true, unrestricted: false, perms, allowedMenus: [] });

beforeEach(() => { vi.clearAllMocks(); grant('report:read'); });
afterEach(() => { cleanup(); });

describe('报告中心混入已发布内容', () => {
  it('具备 article:read 才拉内容，报告与内容并排列出（内容显示标题/栏目，不露裸 slug）', async () => {
    grant('report:read', 'article:read');
    renderPage();
    await waitFor(() => expect(contentApi.list).toHaveBeenCalled());
    // 档位不得越服务端上限（/api/content/articles 的 page_size le=50）：越界会被 422 整拒 → 资讯条目不显示
    expect((vi.mocked(contentApi.list).mock.calls[0][0] as { page_size: number }).page_size).toBeLessThanOrEqual(50);
    // 报告卡片与内容卡片同现，内容只显业务名
    expect(await screen.findByRole('button', { name: '全国页面访问统计' })).toBeInTheDocument();
    const card = screen.getByRole('button', { name: /10 月经营洞察/ });
    expect(card).toHaveTextContent('经营洞察');
    expect(card).toHaveTextContent('资讯');
    expect(card).not.toHaveTextContent('monthly-insight');
  });

  it('点击内容条目就地内嵌阅读（不离开报告中心、不跳页）', async () => {
    grant('report:read', 'article:read');
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /10 月经营洞察/ }));
    // 右栏内嵌 ArticleReader（回显 slug），且仍留在报告中心（左栏报告卡片依旧在列）
    expect(await screen.findByTestId('reader')).toHaveTextContent('内嵌阅读:monthly-insight');
    expect(screen.queryByTestId('read-probe')).toBeNull();
    expect(screen.getByRole('button', { name: '全国页面访问统计' })).toBeInTheDocument();
    // 选中态：被点的内容条目高亮（text-primary），且默认选中 effect 不得把某报告重新选中高亮（回归护栏）
    const picked = screen.getByRole('button', { name: /10 月经营洞察/ });
    expect((picked.querySelector('span') as HTMLElement).className).toContain('text-primary');
    // 单高亮不变式：内容阅读态下报告卡片不得同时高亮（其标题 span 不应带 text-primary）
    const reportTitle = screen.getByRole('button', { name: '全国页面访问统计' }).querySelector('span') as HTMLElement;
    expect(reportTitle.className).not.toContain('text-primary');
  });

  it('落地自动展示首项不计阅读；用户主动点击内容才计（防顶高门户「最多阅读」）', async () => {
    grant('report:read', 'article:read');
    renderPage();
    // 首项是资讯（published 2026-11-10 晚于报告 2026-11-01）→ 落地自动内嵌展示，但不应计阅读
    const reader = await screen.findByTestId('reader');
    expect(reader).toHaveTextContent('内嵌阅读:monthly-insight');
    expect(reader.getAttribute('data-countview')).toBe('false');
    // 用户主动再点该资讯条目 → 切换为计阅读
    fireEvent.click(screen.getByRole('button', { name: /10 月经营洞察/ }));
    await waitFor(() => expect(screen.getByTestId('reader').getAttribute('data-countview')).toBe('true'));
  });

  it('报告先返回、内容后到时，落地仍展示内容首项（抢锁首项的竞态回归）', async () => {
    grant('report:read', 'article:read');
    // 内容延迟落定：先让报告渲染，模拟报告快于内容的真实竞态
    let resolveList: (v: unknown) => void = () => {};
    vi.mocked(contentApi.list).mockImplementationOnce(
      () => new Promise<unknown>(res => { resolveList = res; }) as never,
    );
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: '全国页面访问统计' })).toBeInTheDocument());
    // 内容尚未到达时不应抢先把首项锁成报告（无内嵌 reader）
    expect(screen.queryByTestId('reader')).toBeNull();
    // 内容随后到达
    await act(async () => { resolveList({ data: { items: [ARTICLE], total: 1 } }); });
    // 落地最终展示内容首项（内嵌 reader），而非先到的报告
    expect(await screen.findByTestId('reader')).toHaveTextContent('内嵌阅读:monthly-insight');
  });

  it('内嵌阅读点「全屏」才跳独立阅读页 /read/:slug', async () => {
    grant('report:read', 'article:read');
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /10 月经营洞察/ }));
    await screen.findByTestId('reader');
    fireEvent.click(screen.getByRole('button', { name: '全屏' }));
    expect(await screen.findByTestId('read-probe')).toHaveTextContent('阅读页:monthly-insight');
  });

  it('来源筛选选「内容资讯」只列内容、不列报告', async () => {
    grant('report:read', 'article:read');
    renderPage();
    await screen.findByRole('button', { name: /10 月经营洞察/ });
    const trigger = [...document.querySelectorAll('[role="combobox"]')]
      .find(el => (el.textContent || '').includes('全部来源')) as HTMLElement;
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByText('内容资讯'));
    // 左栏不再列报告卡片（右栏保留上一次选中报告的详情头，属正常行为）
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: '全国页面访问统计' })).toBeNull());
    expect(screen.getByRole('button', { name: /10 月经营洞察/ })).toBeInTheDocument();
  });

  it('没有 article:read：不发内容请求、不列内容条目（首页资讯区同口径）', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: '全国页面访问统计' })).toBeInTheDocument());
    expect(contentApi.list).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /10 月经营洞察/ })).toBeNull();
  });

  it('内容读取失败显式报错 + 重试，不静默成「没有内容」', async () => {
    grant('report:read', 'article:read');
    vi.mocked(contentApi.list).mockRejectedValueOnce(new Error('boom'));
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/内容读取失败/);
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    // 报告不受内容失败影响（旁路失败不拖垮主清单）
    expect(screen.getByRole('button', { name: '全国页面访问统计' })).toBeInTheDocument();
  });
});
