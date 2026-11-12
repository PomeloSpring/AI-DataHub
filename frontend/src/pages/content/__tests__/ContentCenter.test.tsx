import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import client from '@/api/client';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';
import ContentManage from '../ContentManage';
import MyDrafts from '../MyDrafts';
import ContentReview from '../ContentReview';
import ArticleEditor from '../ArticleEditor';
import { CONTENT_FILE_ACCEPT } from '@/api/content';
import type { ContentArticle, ContentFile } from '@/api/content';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
// 稿件正文渲染走 ReactMarkdown/图表分支，jsdom 无布局引擎且与本用例无关
vi.mock('@/components/MarkdownWithCharts', () => ({ default: () => <div>正文渲染</div> }));

const article = (over: Partial<ContentArticle> = {}): ContentArticle => ({
  id: 1, slug: 'a1', title: '种植导板精度复盘', summary: '摘要', category_id: 2,
  category_label: '技术白皮书', category_icon: '', category_color: '#333', tags: ['扫描精度'],
  kind: 'markdown', kind_label: '站内正文', link_url: '', link_label: '', refs: [],
  pinned: 0, featured: 0, status: 'draft', is_live: false, has_pending_change: false,
  can_republish: false, head_changed_since_archive: false,
  review_by: '', published_at: '', archived_at: '', view_count: 0,
  author: 'alice', updated_by: 'alice', updated_at: '2026-11-01T10:00:00', version: 3,
  ...over,
} as ContentArticle);

const LIST = { items: [article()], total: 1, page: 1, page_size: 20 };

const pdf: ContentFile = {
  file_id: 9, filename: '白皮书.pdf', label: 'PDF', mime: 'application/pdf',
  size: 1234, uploaded_at: '2026-11-04T10:00:00',
};

/** 按 URL 前缀分派的假后端（路径与 api/content.ts 的挂载保持一致，写错就当场报错） */
function route(handlers: Record<string, unknown>) {
  vi.mocked(client.get).mockImplementation(((url: string) => {
    const hit = Object.entries(handlers).find(([prefix]) => url.startsWith(prefix));
    // 未登记 = 用例与契约脱节，必须炸出来而不是返回空清单让页面"看着正常"
    if (!hit) throw new Error(`未登记的 GET ${url}`);
    return Promise.resolve({ data: hit[1] });
  }) as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ user: { id: 1, username: 'alice', role: 'user' } });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useAuthStore.getState().logout();
  usePermissionStore.setState({ loaded: false, unrestricted: true, perms: null, allowedMenus: null });
});

/** 内容页都用 useNavigate，脱离 Router 会直接抛错 */
const renderPage = (ui: React.ReactElement) => render(<MemoryRouter>{ui}</MemoryRouter>);

const grant = (perms: string[]) =>
  usePermissionStore.setState({ loaded: true, unrestricted: false, perms, allowedMenus: [] });

describe('内容管理 — 清单口径与权限门', () => {
  it('默认「全部状态」必须带 status=all，否则草稿会从管理清单消失', async () => {
    grant(['article:manage']);
    route({
      '/content/articles': LIST,
      '/content/categories': { items: [] },
      '/content/tags': { items: [] },
    });
    renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByText('种植导板精度复盘')).toBeInTheDocument());

    const call = vi.mocked(client.get).mock.calls.find(([url]) => url === '/content/articles');
    expect((call?.[1] as { params: Record<string, unknown> }).params.status).toBe('all');
  });

  it('只有编辑权时不给发布入口；有发布权才出现发布/下架按钮', async () => {
    grant(['article:manage']);
    route({
      '/content/articles': { ...LIST, items: [article({ status: 'approved' })] },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    const view = renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByText('种植导板精度复盘')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: '发布' })).not.toBeInTheDocument();
    view.unmount();

    grant(['article:manage', 'article:publish']);
    renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByRole('button', { name: '发布' })).toBeInTheDocument());
  });

  it('已上线内容不给删除按钮（必须先下架，避免线上内容被直接抹掉）', async () => {
    grant(['article:manage']);
    route({
      '/content/articles': { ...LIST, items: [article({ is_live: true, status: 'published' })] },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByText('种植导板精度复盘')).toBeInTheDocument());
    expect(screen.queryByLabelText('删除 种植导板精度复盘')).not.toBeInTheDocument();
    expect(screen.getByLabelText('编辑 种植导板精度复盘')).toBeInTheDocument();
  });

  it('标题列不放摘要，长标题靠自己的 max-width 截断并用 title 悬停看全名', async () => {
    grant(['article:manage']);
    const long = '口内扫描的法规与标准地图：ISO 20896 / GB/T 39111 / YY/T 1818 / 注册指导原则怎么配合使用';
    route({
      '/content/articles': {
        ...LIST,
        items: [article({ title: long, summary: '这段摘要不该出现在列表里' })],
      },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    renderPage(<ContentManage />);
    const cell = await screen.findByRole('button', { name: long });
    // 悬停看全名：截断了就必须给得回完整标题
    expect(cell).toHaveAttribute('title', long);
    // 约束要落在标题自己身上：td 在自动表格布局下不吃 max-width，写在 td 等于没写
    //（历史缺陷：长标题撑破列、盖到右边栏目/状态列）
    expect(cell.className).toMatch(/truncate/);
    expect(cell.className).toContain('max-w-[320px]');
    expect(cell.closest('td')).not.toHaveClass(/max-w-/);
    expect(screen.queryByText('这段摘要不该出现在列表里')).not.toBeInTheDocument();
  });

  it('新建入口只在「我的草稿」：内容管理是编排面，不给新建按钮', async () => {
    grant(['article:manage']);
    route({
      '/content/articles': LIST,
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    const view = renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByText('种植导板精度复盘')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /新建内容/ })).not.toBeInTheDocument();
    // 入口拿走了就得告诉用户去哪开工，不能让人以为功能丢了
    expect(screen.getByText(/新建与编写请在「我的草稿」进行/)).toBeInTheDocument();
    view.unmount();

    renderPage(<MyDrafts />);
    expect(await screen.findByRole('button', { name: /新建内容/ })).toBeInTheDocument();
  });

  it('内容管理空态不再指「点右上新建内容」（按钮已移走，指了就是空承诺）', async () => {
    grant(['article:manage']);
    route({
      '/content/articles': { items: [], total: 0, page: 1, page_size: 20 },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    renderPage(<ContentManage />);
    expect(await screen.findByText(/新建与编写在左侧「我的草稿」进行/)).toBeInTheDocument();
    expect(screen.queryByText(/点右上「新建内容」/)).not.toBeInTheDocument();
  });

  it('已下架内容给「上架」入口，确认后按 republish 发到发布端点（同一权限码、同一前缀）', async () => {
    grant(['article:manage', 'article:publish']);
    route({
      '/content/articles': {
        ...LIST,
        items: [article({ status: 'archived', can_republish: true, archived_at: '2026-11-10T10:09:35' })],
      },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    vi.mocked(client.post).mockResolvedValue({
      data: { status: 'published', message: '已重新上架，门户恢复展示下架前那一版。' },
    } as never);
    renderPage(<ContentManage />);

    fireEvent.click(await screen.findByRole('button', { name: '上架' }));
    // 上架与下架/发布同一个弹框口径：说清放回的是哪一版，并给可填的上架原因
    expect(await screen.findByRole('heading', { name: '重新上架' })).toBeInTheDocument();
    expect(screen.getByText(/当初是审核通过才上线的/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认上架' }));

    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/content/publish/a1', {
      action: 'republish', reason: '', expected_version: 3,
    }));
    // 结论以服务端文案为准（拼在横幅里，不是只弹个“操作完成”）
    expect(await screen.findByText(/已重新上架，门户恢复展示下架前那一版/)).toBeInTheDocument();
  });

  it('下架后又改过正文：不只给上架入口，同时给送审入口并说明新改动需重新审核', async () => {
    grant(['article:manage', 'article:publish']);
    route({
      '/content/articles': {
        ...LIST,
        items: [article({ status: 'archived', can_republish: true, head_changed_since_archive: true })],
      },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByRole('button', { name: '上架' })).toBeInTheDocument());
    expect(screen.getByLabelText('提交审核 种植导板精度复盘')).toBeInTheDocument();

    // 弹框里先说清后果：旧版可放回，但新改动不会跟着上线
    fireEvent.click(screen.getByRole('button', { name: '上架' }));
    expect(await screen.findByText(/这篇在下架之后又改过正文/)).toBeInTheDocument();
  });

  it('追溯不到下架那一版时不给上架（空承诺），只给送审入口', async () => {
    grant(['article:manage', 'article:publish']);
    route({
      '/content/articles': {
        ...LIST,
        items: [article({ status: 'archived', can_republish: false, head_changed_since_archive: true })],
      },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByText('种植导板精度复盘')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: '上架' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('提交审核 种植导板精度复盘')).toBeInTheDocument();
  });

  it('服务端拒因原样回显（上架被拒时不得转成“操作失败请重试”）', async () => {
    grant(['article:manage', 'article:publish']);
    route({
      '/content/articles': {
        ...LIST, items: [article({ status: 'archived', can_republish: true })],
      },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    vi.mocked(client.post).mockRejectedValue({
      response: { status: 409, data: { detail: '这篇内容已被他人改过，请刷新后重试' } },
    });
    renderPage(<ContentManage />);
    fireEvent.click(await screen.findByRole('button', { name: '上架' }));
    fireEvent.click(screen.getByRole('button', { name: '确认上架' }));
    await waitFor(() => expect(
      screen.getByText('这篇内容已被他人改过，请刷新后重试'),
    ).toBeInTheDocument());
  });

  it('服务端拒绝原因必须原样显示（门禁/权限都不许静默）', async () => {
    grant(['article:manage']);
    route({
      '/content/articles': LIST,
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    vi.mocked(client.post).mockRejectedValue({
      response: { status: 422, data: { detail: '不能提交审核：正文还有未填写的占位（【待填' } },
    });
    renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByText('种植导板精度复盘')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('提交审核 种植导板精度复盘'));
    await waitFor(() => expect(
      screen.getByText('不能提交审核：正文还有未填写的占位（【待填'),
    ).toBeInTheDocument());
  });
});

describe('审核发布 — 逐条确认与职责分离', () => {
  const warnings = [
    { code: 'refs_missing', message: '没有登记外部出处' },
    { code: 'small_sample', message: '样本量偏小' },
  ];
  const draft = article({
    status: 'in_review', slug: 'a1',
    gate_hard: [], gate_warnings: warnings, submitted_by: 'bob',
  });

  beforeEach(() => {
    route({
      '/content/reviews/pending': { items: [draft], total: 1, page: 1, page_size: 50, review_expired: [] },
      '/content/reviews/a1/draft': draft,
    });
  });

  it('提醒没逐条确认完之前，「审核通过」不可点', async () => {
    grant(['article:review']);
    renderPage(<ContentReview />);
    const approve = await waitFor(() => screen.getByRole('button', { name: /审核通过/ }));
    expect(approve).toBeDisabled();

    const box = screen.getByRole('checkbox', { name: '确认已看过提醒：没有登记外部出处' });
    fireEvent.click(box);
    expect(screen.getByRole('button', { name: /审核通过/ })).toBeDisabled();

    fireEvent.click(screen.getByRole('checkbox', { name: '确认已看过提醒：样本量偏小' }));
    await waitFor(() => expect(screen.getByRole('button', { name: /审核通过/ })).toBeEnabled());
  });

  it('驳回不写原因时前端就拦住并说明依据（服务端同样会拒）', async () => {
    grant(['article:review']);
    renderPage(<ContentReview />);
    await waitFor(() => expect(screen.getByRole('heading', { name: '种植导板精度复盘' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '驳回' }));
    expect(await screen.findByText(/驳回必须写原因/)).toBeInTheDocument();
    expect(client.post).not.toHaveBeenCalledWith('/content/reviews/a1/reject', expect.anything());
  });

  it('只有审核权时不出现发布按钮，并说明"改动不会自动上线"', async () => {
    grant(['article:review']);
    route({
      '/content/reviews/pending': {
        items: [{ ...draft, status: 'approved' }], total: 1, page: 1, page_size: 50, review_expired: [],
      },
      '/content/reviews/a1/draft': { ...draft, status: 'approved' },
    });
    renderPage(<ContentReview />);
    await waitFor(() => expect(screen.getByRole('heading', { name: '种植导板精度复盘' })).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: '发布上线' })).not.toBeInTheDocument();
    expect(screen.getByText(/发布需要「发布与下架内容」权限/)).toBeInTheDocument();
  });

  it('到期复核清单单独成区，不与待审队列混排', async () => {
    grant(['article:review']);
    route({
      '/content/reviews/pending': {
        items: [draft], total: 1, page: 1, page_size: 50,
        review_expired: [article({ slug: 'old1', title: '旧版合规解读', review_by: '2026-01-01', is_live: true, status: 'published' })],
      },
      '/content/reviews/a1/draft': draft,
    });
    renderPage(<ContentReview />);
    const zone = await waitFor(() => screen.getByText(/到期未复核/).closest('section')!);
    expect(within(zone).getByText('旧版合规解读')).toBeInTheDocument();
    expect(within(zone).getByText(/复核日 2026-01-01/)).toBeInTheDocument();
  });
});


describe('附件通道 — 只给下载、不做站内预览', () => {
  const fileDraft = article({
    kind: 'file', kind_label: '附件', status: 'in_review', body: '',
    files: [pdf], gate_hard: [], gate_warnings: [],
  });

  beforeEach(() => {
    route({
      '/content/reviews/pending': { items: [fileDraft], total: 1, page: 1, page_size: 50, review_expired: [] },
      '/content/reviews/a1/draft': fileDraft,
      '/content/reviews/a1/files/9/download-url': { url: 'https://oss.example/direct', filename: pdf.filename },
    });
  });

  it('审核人能下载附件，且走 reviews 前缀（只勾审核权拿不到 articles*）', async () => {
    grant(['article:review']);
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    renderPage(<ContentReview />);
    await waitFor(() => expect(screen.getByText(pdf.filename)).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /^下载$/ }));
    await waitFor(() => expect(openSpy).toHaveBeenCalledWith('https://oss.example/direct', '_blank', 'noopener'));
    expect(client.get).toHaveBeenCalledWith('/content/reviews/a1/files/9/download-url');
    openSpy.mockRestore();
  });

  it('附件卡不出现预览入口（服务端下载头恒为 attachment，画个预览按钮只会失灵）', async () => {
    grant(['article:review']);
    renderPage(<ContentReview />);
    await waitFor(() => expect(screen.getByText(pdf.filename)).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /预览/ })).not.toBeInTheDocument();
    // 只勾审核权不得出现上传/删除（编排动作属 article:manage）
    expect(screen.queryByRole('button', { name: /上传附件/ })).not.toBeInTheDocument();
  });

  it('编辑器里附件形态可选，且文案说清只给下载', async () => {
    grant(['article:manage']);
    route({ '/content/categories': { items: [] }, '/content/tags': { items: [] } });
    const user = userEvent.setup();
    renderPage(<ArticleEditor open slug={undefined} onOpenChange={() => {}} onSaved={() => {}} />);

    await user.click(screen.getByRole('combobox', { name: '选择内容形态' }));
    expect(await screen.findByRole('option', { name: /附件/ })).toBeTruthy();
    expect(screen.getByText(/附件只给下载、不做站内预览/)).toBeInTheDocument();
  });

  it('新建未落库时上传按钮禁用并说清为何（不假装能传，也不藏掉入口）', async () => {
    grant(['article:manage']);
    route({ '/content/categories': { items: [] }, '/content/tags': { items: [] } });
    renderPage(<ArticleEditor open slug={undefined} onOpenChange={() => {}} onSaved={() => {}} />);

    expect(screen.getByRole('button', { name: /上传附件/ })).toBeDisabled();
    expect(screen.getByText(/保存草稿后即可上传附件/)).toBeInTheDocument();
    expect(client.post).not.toHaveBeenCalled();
  });

  it('前端 accept 与服务端白名单必须同一份（两处各维护一定飘）', () => {
    const svc = path.resolve(__dirname, '../../../../../backend/modules/platform/services/content_service.py');
    expect(existsSync(svc)).toBe(true);
    const block = readFileSync(svc, 'utf8').split('CONTENT_FILE_TYPES = {')[1].split('\n}')[0];
    const serverExts = [...block.matchAll(/"(\.[a-z]+)":/g)].map(m => m[1]);
    expect([...CONTENT_FILE_ACCEPT.split(',')].sort()).toEqual(serverExts.sort());
  });
});

describe('内容 → 知识库语料同步（目标库必须能选）', () => {
  const kb = (name: string, over: Record<string, unknown> = {}) => ({
    name, description: '', kb_type: 'qmind', status: 'active',
    supports_docs: true, can_edit: true, reason: '', ...over,
  });
  const BASES = { items: [kb('云端库'), kb('另一个库'), kb('本地库', {
    kb_type: 'local', supports_docs: false, can_edit: false,
    reason: '文档树目前只支持云端知识库（qmind）',
  })] };
  const TREE = {
    nodes: [{ source_id: 'd1', title: '技术白皮书', is_dir: true, children: [] }],
    doc_count: 0, folder_count: 1, flat: false, base: { name: '云端库', description: '', kb_type: 'qmind', status: 'active' },
  };
  const syncable = (over: Record<string, unknown> = {}) => ({
    ...LIST,
    items: [article({ status: 'published', is_live: true, ...over })],
  });

  const openDialog = async (title = '种植导板精度复盘') => {
    const user = userEvent.setup();
    fireEvent.click(screen.getByLabelText(`同步到知识库 ${title}`));
    return user;
  };

  beforeEach(() => {
    // 树的前缀比库清单更长，必须排在前面（route 按登记序取首个前缀命中）
    route({
      '/content/knowledge-bases/': TREE,
      '/content/articles': syncable(),
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
      '/content/knowledge-bases': BASES,
    });
    vi.mocked(client.post).mockImplementation(((url: string, body?: unknown) => {
      if (!url.endsWith('/sync')) throw new Error(`未登记的 POST ${url}`);
      return Promise.resolve({ data: {
        kb_name: '云端库', doc_title: 'a1.md', source_id: 's1', unchanged: false, synced_at: '',
        warnings: [], message: '已同步到知识库「云端库」（a1.md）。云端要重新解析一遍，AS-BOT 检索稍后才用得到这一版。',
      }, body });
    }) as never);
  });

  it('同步入口只给 kbdoc:write：能编内容/能发布不等于能写 AS-BOT 的知识', async () => {
    grant(['article:manage', 'article:publish']);
    renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByText('种植导板精度复盘')).toBeInTheDocument());
    expect(screen.queryByLabelText(/同步到知识库/)).not.toBeInTheDocument();
    // 语料列也不画：没有语料权的角色不需要看一个自己碰不到的维度
    expect(screen.queryByRole('columnheader', { name: '语料' })).not.toBeInTheDocument();
  });

  it('目标库是选择框且不可写的库置灰带原因（不藏选项，也不让人手填库名）', async () => {
    grant(['article:manage', 'kbdoc:write']);
    renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByText('种植导板精度复盘')).toBeInTheDocument());
    const user = await openDialog();
    await user.click(await screen.findByRole('combobox', { name: '选择目标知识库' }));
    const cloud = await screen.findByRole('option', { name: '云端库' });
    expect(cloud).toHaveAttribute('data-state', 'unchecked');
    const local = screen.getByRole('option', { name: /本地库/ });
    expect(local.textContent).toContain('只支持云端知识库');
    expect(local).toHaveAttribute('data-disabled');
  });

  it('未发布内容不开库选择，直接说明为何进不了语料（不是选完库再被 409 打回）', async () => {
    grant(['article:manage', 'kbdoc:write']);
    route({
      '/content/knowledge-bases/': TREE,
      '/content/articles': { ...LIST, items: [article()] },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
      '/content/knowledge-bases': BASES,
    });
    renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByText('种植导板精度复盘')).toBeInTheDocument());
    await openDialog();

    expect(await screen.findByText(/只有「已发布」的内容能进知识库/)).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '选择目标知识库' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '开始同步' })).not.toBeInTheDocument();
    expect(client.post).not.toHaveBeenCalled();
  });

  it('同步请求按 slug 发到知识库前缀（裁决码是 kbdoc:write，不是内容前缀）', async () => {
    grant(['article:manage', 'kbdoc:write']);
    renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByText('种植导板精度复盘')).toBeInTheDocument());
    const user = await openDialog();

    await user.click(await screen.findByRole('combobox', { name: '选择目标知识库' }));
    await user.click(await screen.findByRole('option', { name: '云端库' }));
    fireEvent.click(screen.getByRole('button', { name: '开始同步' }));

    await waitFor(() => expect(client.post).toHaveBeenCalled());
    const [url, body] = vi.mocked(client.post).mock.calls.find(([u]) => u.endsWith('/sync'))!;
    expect(decodeURIComponent(url as string)).toBe('/content/knowledge-bases/云端库/sync');
    expect(body).toEqual({ article: 'a1', parent_id: '' });
    expect(await screen.findByText(/云端要重新解析一遍/)).toBeInTheDocument();
  });

  it('换目标库先说清旧库那篇会被撤掉；服务端 warnings 拼在结论后不静默', async () => {
    grant(['article:manage', 'kbdoc:write']);
    route({
      '/content/knowledge-bases/': TREE,
      '/content/articles': syncable({
        kb_sync: { kb_name: '云端库', doc_title: 'a1.md', state: 'synced', synced_at: '2026-11-04 10:00:00', stale: true, last_error: '' },
      }),
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
      '/content/knowledge-bases': BASES,
    });
    vi.mocked(client.post).mockImplementation((() => Promise.resolve({ data: {
      kb_name: '另一个库', doc_title: 'a1.md', source_id: 's2', unchanged: false, synced_at: '',
      message: '已同步到知识库「另一个库」（a1.md）。云端要重新解析一遍，AS-BOT 检索稍后才用得到这一版。；有 1 条需要处理，见下',
      warnings: ['新库已同步，但旧库「云端库」里的那篇没能撤掉（超时）。现在两个库都有这篇语料，检索会重复命中。'],
    } })) as never);

    renderPage(<ContentManage />);
    await waitFor(() => expect(screen.getByText('种植导板精度复盘')).toBeInTheDocument());
    // 有改动未同步 → 列表就得报出来（不是“已同步”一律绿）
    expect(screen.getByTitle(/还不是当前版本/)).toBeInTheDocument();

    const user = await openDialog();
    await user.click(await screen.findByRole('combobox', { name: '选择目标知识库' }));
    await user.click(await screen.findByRole('option', { name: '另一个库' }));
    expect(await screen.findByText(/一篇内容只能对一个库/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '开始同步' }));
    await waitFor(() => expect(
      screen.getByText(/检索会重复命中/),
    ).toBeInTheDocument());
    expect(screen.getByText(/云端要重新解析一遍/)).toBeInTheDocument();
  });
});

describe('期次（已发布版本）与读者可见性策略', () => {
  it('列表行把期次与形态分开报：第 N 期 / 共 N 期是发布事实，不是工作流态', async () => {
    grant(['article:manage']);
    route({
      '/content/articles': {
        ...LIST,
        items: [article({ status: 'published', is_live: true, release: 3, release_label: '2026-11',
                          release_count: 3, versioning: 'keep_history' })],
      },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    renderPage(<ContentManage />);
    expect(await screen.findByText(/第 3 期·2026-11/)).toBeInTheDocument();
    expect(screen.getByText(/共 3 期/)).toBeInTheDocument();
  });

  it('只保留最新的条目在列表上就把“历史不对读者展示”写明（不是静默少一个入口）', async () => {
    grant(['article:manage']);
    route({
      '/content/articles': {
        ...LIST,
        items: [article({ status: 'published', is_live: true, release: 4, release_label: '2026-11',
                          release_count: 4, versioning: 'latest_only', history_hidden: true })],
      },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    renderPage(<ContentManage />);
    expect(await screen.findByText(/历史期次不对读者展示/)).toBeInTheDocument();
  });

  it('发布弹框把期次名交给人填，随请求发出 release_label 并预告本期是第几期', async () => {
    grant(['article:manage', 'article:publish']);
    route({
      '/content/articles': {
        ...LIST,
        items: [article({ status: 'approved', release: 2, release_label: '2026-10', release_count: 2,
                          versioning: 'keep_history' })],
      },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    vi.mocked(client.post).mockResolvedValue({
      data: { status: 'published', message: '已发布：本期第 3 期〈2026-11〉，本篇共 3 期。',
              release: 3, release_label: '2026-11', release_count: 3 },
    } as never);
    renderPage(<ContentManage />);

    fireEvent.click(await screen.findByRole('button', { name: '发布' }));
    const input = await screen.findByLabelText(/期次名/);
    // 缺省口径在服务端（按当月），前端只给建议值，不把默认写死进请求体
    expect(String(input.getAttribute('placeholder'))).toContain('留空则自动用发布当月');
    fireEvent.change(input, { target: { value: '2026-11 月刊' } });
    expect(screen.getByText(/新的第 3 期/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '确认发布' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/content/publish/a1', {
      action: 'publish', expected_version: 3, waive_review: false, reason: '', release_label: '2026-11 月刊',
    }));
    expect(await screen.findByText(/本期第 3 期〈2026-11〉/)).toBeInTheDocument();
  });

  it('策略为只保留最新时，发布前就把“上一期不再对读者展示”写在点之前', async () => {
    grant(['article:manage', 'article:publish']);
    route({
      '/content/articles': {
        ...LIST,
        items: [article({ status: 'approved', is_live: false, release: 2, release_label: '2026-10',
                          release_count: 2, versioning: 'latest_only', history_hidden: true })],
      },
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
    });
    vi.mocked(client.post).mockResolvedValue({ data: { status: 'published', message: '已发布' } } as never);
    renderPage(<ContentManage />);
    fireEvent.click(await screen.findByRole('button', { name: '发布' }));
    expect(await screen.findByText(/发布后上一期〈2026-10〉不再对读者展示/)).toBeInTheDocument();
    // 同时说清“不删数据”：否则有人会以为勾上只留最新就是删了旧内容
    expect(screen.getByText(/历史期次仍可在编辑器版本清单/)).toBeInTheDocument();
  });

  it('编辑器有「版本策略」选择框，保存时随正文一起落库', async () => {
    grant(['article:manage']);
    route({ '/content/categories': { items: [] }, '/content/tags': { items: [] } });
    vi.mocked(client.post).mockResolvedValue({ data: article({ versioning: 'latest_only' }) } as never);
    const user = userEvent.setup();
    renderPage(<ArticleEditor open slug={undefined} onOpenChange={() => {}} onSaved={() => {}} />);

    fireEvent.change(screen.getByLabelText('标题'), { target: { value: '行业月报' } });
    await user.click(screen.getByRole('combobox', { name: '选择版本策略' }));
    await user.click(await screen.findByRole('option', { name: '只保留最新版本' }));
    expect(screen.getByText(/只把最新版本给读者看/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith(
      '/content/articles', expect.objectContaining({ versioning: 'latest_only' }),
    ));
  });

  it('版本清单分区「已发布期次」与「编辑历史」：混列会让人以为存一次盘读者就能看一次', async () => {
    grant(['article:manage']);
    route({
      '/content/categories': { items: [] }, '/content/tags': { items: [] },
      // 子路径要排在前面：route() 按前缀命中，先写 /articles/a1 会把 /revisions 一并吃掉
      '/content/articles/a1/revisions': {
        items: [
          { id: 12, change_note: '改错别字', created_by: 'alice', created_at: '2026-11-02T10:00:00',
            is_live: false, release: 0, release_label: '', is_released: false, released_at: '' },
          { id: 11, change_note: '十一月刊', created_by: 'alice', created_at: '2026-11-01T10:00:00',
            is_live: true, release: 2, release_label: '2026-11', is_released: true, released_at: '2026-11-01T12:00:00' },
        ],
      },
      '/content/articles/a1': article({ versioning: 'keep_history', release: 2, release_count: 2 }),
    });
    const user = userEvent.setup();
    renderPage(<ArticleEditor open slug="a1" onOpenChange={() => {}} onSaved={() => {}} />);
    await screen.findByLabelText('标题');
    await user.click(screen.getByRole('button', { name: /查看版本清单/ }));

    expect(await screen.findByText(/已发布期次（共 1 期）/)).toBeInTheDocument();
    expect(screen.getByText('编辑历史（未发布的改稿痕迹）')).toBeInTheDocument();
    expect(screen.getByText('第 2 期·2026-11')).toBeInTheDocument();
    // 回滚不再是“本批尚未开放”的口头承诺：指到列表行上的切换入口
    expect(screen.getByText(/用列表行上的「切换展示版本」/)).toBeInTheDocument();
    expect(screen.queryByText(/本批尚未开放回滚按钮/)).not.toBeInTheDocument();
  });
});
