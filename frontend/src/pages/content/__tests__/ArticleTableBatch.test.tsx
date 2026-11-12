// 内容管理/我的草稿的批量操作契约。
//
// 锁四件事（都是这个页面最容易偷偷做错的方向）：
// 1. 分组判据与服务端一致：已上线的算「下架」、草稿算「提交审核」，按钮上直接给条数；
// 2. 状态不适用的勾选项**显式列在确认框里**说"本次不处理"，不静默剔掉（no-silent-degradation）；
// 3. 部分失败不假装全成功：回执点名到标题并转述服务端原话，失败项保持勾选可直接重试；
// 4. 批量入口按权限码给：只勾编辑权拿不到批量发布/下架（裁决仍在服务端，这里只藏注定 403 的入口）。
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import client from '@/api/client';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';
import ContentManage from '../ContentManage';
import type { BatchResult, ContentArticle } from '@/api/content';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const article = (over: Partial<ContentArticle>): ContentArticle => ({
  id: 1, slug: 'a1', title: '种植导板精度复盘', summary: '', category_id: 2,
  category_label: '技术白皮书', category_icon: '', category_color: '', tags: [],
  kind: 'markdown', kind_label: '站内正文', link_url: '', link_label: '', refs: [],
  pinned: 0, featured: 0, status: 'draft', is_live: false, has_pending_change: false,
  can_republish: false, head_changed_since_archive: false,
  review_by: '', published_at: '', archived_at: '', view_count: 0,
  author: 'alice', updated_by: 'alice', updated_at: '2026-11-01T10:00:00', version: 3,
  ...over,
} as ContentArticle);

/** 四种状态各一篇：草稿 / 在线 / 已通过待发布 / 已下架可上架 */
const LIST = {
  items: [
    article({ slug: 'a1', title: '种植导板精度复盘' }),
    article({ slug: 'a2', title: '口扫数据合规红线', status: 'published', is_live: true }),
    article({ slug: 'a3', title: '固件发布说明', status: 'approved' }),
    article({ slug: 'a4', title: '诊室四步精度自查', status: 'archived', can_republish: true }),
    article({ slug: 'a5', id: 5, title: '湿表面与出血反光处置' }),
  ],
  total: 5, page: 1, page_size: 20,
};

const batchResult = (over: Partial<BatchResult> = {}): BatchResult => ({
  action: 'archive', succeeded: [], failed: [], success_count: 0, failed_count: 0,
  message: '', ...over,
}) as BatchResult;

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ user: { id: 1, username: 'alice', role: 'user' } });
  vi.mocked(client.get).mockImplementation(((url: string) => {
    if (url === '/content/articles') return Promise.resolve({ data: LIST });
    if (url === '/content/categories' || url === '/content/tags') return Promise.resolve({ data: { items: [] } });
    throw new Error(`未登记的 GET ${url}`);
  }) as never);
});

afterEach(() => {
  cleanup();
  useAuthStore.getState().logout();
  usePermissionStore.setState({ loaded: false, unrestricted: true, perms: null, allowedMenus: null });
});

const grant = (perms: string[]) =>
  usePermissionStore.setState({ loaded: true, unrestricted: false, perms, allowedMenus: [] });

const renderPage = () => render(<MemoryRouter><ContentManage /></MemoryRouter>);

const enterBatch = async () => {
  fireEvent.click(await screen.findByRole('button', { name: /批量操作/ }));
};

const pick = (title: string) => fireEvent.click(screen.getByLabelText(`选择 ${title}`));

describe('内容管理 — 批量操作', () => {
  it('按钮按状态分组计数：草稿算提交审核，在线算下架，已通过算发布', async () => {
    grant(['article:manage', 'article:publish']);
    renderPage();
    await screen.findByText('种植导板精度复盘');
    await enterBatch();

    pick('种植导板精度复盘');
    pick('口扫数据合规红线');
    pick('固件发布说明');

    expect(await screen.findByText('已选 3 项')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '批量提交审核 (1)' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '批量发布 (1)' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '批量下架 (1)' })).toBeEnabled();
    // 没有可上架的：按钮禁用并说明判据，不让人点了才知道不行
    expect(screen.getByRole('button', { name: '批量上架' })).toBeDisabled();
  });

  it('批量发布只收「已通过待发布」：在线但在改下一版的算下架，不算发布', async () => {
    grant(['article:manage', 'article:publish']);
    vi.mocked(client.get).mockImplementation(((url: string) => {
      if (url === '/content/articles') {
        return Promise.resolve({
          data: {
            items: [article({ slug: 'b1', title: '口内扫描精度怎么衡量',
                              status: 'draft', is_live: true, has_pending_change: true })],
            total: 1, page: 1, page_size: 20,
          },
        });
      }
      if (url === '/content/categories' || url === '/content/tags') return Promise.resolve({ data: { items: [] } });
      throw new Error(`未登记的 GET ${url}`);
    }) as never);
    renderPage();
    await screen.findByText('口内扫描精度怎么衡量');
    await enterBatch();
    pick('口内扫描精度怎么衡量');

    // 行上是「下架」，批量同判据；这一版没过审，要发它得逐篇走审核或免审直发，
    // 所以不能算进批量发布（否则点一次就换来一串拒因）
    expect(screen.getByRole('button', { name: '批量下架 (1)' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '批量发布' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '批量提交审核 (1)' })).toBeEnabled();
  });

  it('状态不适用的勾选项显式列为「本次不处理」，不静默剔掉', async () => {
    grant(['article:manage', 'article:publish']);
    renderPage();
    await screen.findByText('种植导板精度复盘');
    await enterBatch();

    pick('口扫数据合规红线');   // 在线 → 适用下架
    pick('种植导板精度复盘');   // 草稿 → 不适用下架
    fireEvent.click(screen.getByRole('button', { name: /批量下架/ }));

    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByText('本次处理 1 篇')).toBeInTheDocument();
    expect(within(dlg).getByText('· 口扫数据合规红线')).toBeInTheDocument();
    expect(within(dlg).getByText(/另有 1 篇当前状态不适用，本次不处理/)).toBeInTheDocument();
    expect(within(dlg).getByText('· 种植导板精度复盘')).toBeInTheDocument();
    expect(within(dlg).getByRole('button', { name: '确认下架 1 篇' })).toBeInTheDocument();
  });

  it('批量下架把整批共用原因发给 publish-batch 端点', async () => {
    grant(['article:manage', 'article:publish']);
    vi.mocked(client.post).mockResolvedValue({
      data: batchResult({
        succeeded: [{ slug: 'a2', title: '口扫数据合规红线', status: 'archived', message: '已下架，门户不再展示。' }],
        success_count: 1, message: '已下架 1 篇',
      }),
    } as never);
    renderPage();
    await screen.findByText('口扫数据合规红线');
    await enterBatch();
    pick('口扫数据合规红线');
    fireEvent.click(screen.getByRole('button', { name: /批量下架/ }));

    const dlg = await screen.findByRole('dialog');
    fireEvent.change(within(dlg).getByLabelText('下架原因（选填，写入每篇的审核流水）'),
      { target: { value: '法规条款已更新' } });
    fireEvent.click(within(dlg).getByRole('button', { name: '确认下架 1 篇' }));

    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/content/publish-batch', {
      action: 'archive', slugs: ['a2'], reason: '法规条款已更新',
    }));
    expect(await screen.findByText('已下架 1 篇')).toBeInTheDocument();
  });

  it('部分失败不假装全成功：回执点名标题与服务端原话，失败项保持勾选', async () => {
    grant(['article:manage', 'article:publish']);
    vi.mocked(client.post).mockResolvedValue({
      data: batchResult({
        action: 'submit',
        succeeded: [{ slug: 'a1', title: '种植导板精度复盘', status: 'in_review', message: '已提交审核' }],
        failed: [{ slug: 'a5', title: '湿表面与出血反光处置',
                   reason: '不能提交审核：正文还有【待填】未清零（第 12 行）' }],
        success_count: 1, failed_count: 1, message: '已提交审核 1 篇；1 篇未处理（原因逐条列出）',
      }),
    } as never);
    renderPage();
    await screen.findByText('种植导板精度复盘');
    await enterBatch();
    pick('种植导板精度复盘');
    pick('湿表面与出血反光处置');
    fireEvent.click(screen.getByRole('button', { name: '批量提交审核 (2)' }));
    fireEvent.click(await screen.findByRole('button', { name: '确认提交审核 2 篇' }));

    // 两条都发给服务端（逐条裁决在库里，不在前端预筛掉）
    await waitFor(() => expect(client.post).toHaveBeenCalledWith(
      '/content/articles/batch-submit', { slugs: ['a1', 'a5'] }));
    expect(await screen.findByText('已提交审核 1 篇；1 篇未处理（原因逐条列出）')).toBeInTheDocument();
    expect(screen.getByText(/「湿表面与出血反光处置」不能提交审核/)).toBeInTheDocument();
    // 失败项留在勾选里（可直接重试），成功项从勾选里移除
    await waitFor(() => expect(screen.getByLabelText('选择 湿表面与出血反光处置')).toBeChecked());
    expect(screen.getByLabelText('选择 种植导板精度复盘')).not.toBeChecked();
  });

  it('已上线内容不进批量删除名单：必须先下架（服务端同样逐条拒）', async () => {
    grant(['article:manage']);
    renderPage();
    await screen.findByText('口扫数据合规红线');
    await enterBatch();
    pick('口扫数据合规红线');
    pick('种植导板精度复盘');

    const del = screen.getByRole('button', { name: /批量删除/ });
    expect(del).toBeEnabled();   // 草稿可删，所以按钮可用
    fireEvent.click(del);

    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByText('本次处理 1 篇')).toBeInTheDocument();
    expect(within(dlg).getByText(/另有 1 篇当前状态不适用/)).toBeInTheDocument();
    expect(within(dlg).getByText(/此操作不可撤销/)).toBeInTheDocument();
  });

  it('只有编辑权时不给批量发布/下架入口；有发布权才出现', async () => {
    grant(['article:manage']);
    const view = renderPage();
    await screen.findByText('种植导板精度复盘');
    await enterBatch();
    expect(screen.queryByRole('button', { name: /批量发布/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /批量下架/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /批量提交审核/ })).toBeInTheDocument();
    view.unmount();

    grant(['article:manage', 'article:publish']);
    renderPage();
    await screen.findByText('种植导板精度复盘');
    await enterBatch();
    expect(screen.getByRole('button', { name: /批量发布/ })).toBeInTheDocument();
  });

  it('换筛选条件即清空勾选：不把看不见的条目带进下一次批量', async () => {
    grant(['article:manage', 'article:publish']);
    renderPage();
    await screen.findByText('种植导板精度复盘');
    await enterBatch();
    pick('种植导板精度复盘');
    expect(await screen.findByText('已选 1 项')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('combobox', { name: '状态筛选' }));
    fireEvent.click(await screen.findByRole('option', { name: '已发布' }));

    await waitFor(() => expect(screen.queryByText('已选 1 项')).not.toBeInTheDocument());
  });
});

describe('切换展示版本（回滚到已发布期次）', () => {
  /** 把清单里的 a2 换成“已发布两期、可切”的那一篇 */
  const useRollbackList = (over: Partial<ContentArticle> = {}) => {
    vi.mocked(client.get).mockImplementation(((url: string) => {
      if (url === '/content/articles') {
        return Promise.resolve({
          data: {
            items: [article({
              slug: 'a2', title: '合规红线自查', status: 'published', is_live: true,
              release: 2, release_label: '2026-11', release_count: 2, can_rollback: true,
              versioning: 'keep_history', history_hidden: false, ...over,
            })],
            total: 1, page: 1, page_size: 20,
          },
        });
      }
      if (url === '/content/articles/a2/releases') {
        return Promise.resolve({
          data: {
            items: [
              { release: 2, label: '2026-11', released_at: '2026-11-01T12:00:00', is_current: true },
              { release: 1, label: '2026-10', released_at: '2026-10-01T12:00:00', is_current: false },
            ],
            versioning: 'keep_history', versioning_label: '保留历史期次',
            history_hidden: false, attachments_shared: true,
          },
        });
      }
      if (url === '/content/categories' || url === '/content/tags') return Promise.resolve({ data: { items: [] } });
      throw new Error(`未登记的 GET ${url}`);
    }) as never);
  };

  it('批量工具条里没有“切换展示版本”：切到哪一期必须逐条指名（同免审直发不进批量）', async () => {
    grant(['article:manage', 'article:publish']);
    useRollbackList();
    renderPage();
    await screen.findByText('合规红线自查');
    await enterBatch();
    pick('合规红线自查');

    expect(await screen.findByRole('button', { name: /批量发布/ })).toBeInTheDocument();
    // 五个批量动作就是服务端 _BATCH_KINDS 那五份：rollback 不得混进来
    expect(screen.queryByRole('button', { name: /切换展示版本 \(/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /批量切换/ })).not.toBeInTheDocument();
  });

  it('只勾编辑权不给切换入口：能改草稿不等于能把门户换成旧的一期', async () => {
    grant(['article:manage']);
    useRollbackList();
    renderPage();
    await screen.findByText('合规红线自查');
    expect(screen.queryByRole('button', { name: /切换展示版本/ })).not.toBeInTheDocument();
  });

  it('can_rollback=false（只有一期）时不给入口：只能选中自己的开关比没有更坑', async () => {
    grant(['article:manage', 'article:publish']);
    useRollbackList({ can_rollback: false, release_count: 1, release: 1 });
    renderPage();
    await screen.findByText('合规红线自查');
    // 发布入口照给（下架可用），但“切到哪一期”没得选就不画
    expect(screen.getByRole('button', { name: '下架' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /切换展示版本/ })).not.toBeInTheDocument();
  });

  it('候选只从 releases 接口拿；原因未填不能确认；确认后按 rollback 发到发布端点', async () => {
    grant(['article:manage', 'article:publish']);
    useRollbackList();
    vi.mocked(client.post).mockResolvedValue({
      data: { status: 'published', message: '已把门户展示的版本切到第 1 期〈2026-10〉。', release: 1 },
    } as never);
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: '切换展示版本 合规红线自查' }));

    fireEvent.click(await screen.findByRole('combobox', { name: '选择要切回的期次' }));
    // 当前那一期不进候选：选中自己不是“切了一次”，而是一个看似成功的空动作
    expect(await screen.findByRole('option', { name: /第 1 期·2026-10/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /第 2 期·2026-11/ })).not.toBeInTheDocument();
    expect(client.get).toHaveBeenCalledWith('/content/articles/a2/releases');
    fireEvent.click(screen.getByRole('option', { name: /第 1 期·2026-10/ }));

    // 附件不进版本链：这句取舍得在弹框里看见，不能静默让人以为附件也换了一期
    expect(screen.getByText(/附件不进版本链/)).toBeInTheDocument();

    const confirm = () => screen.getByRole('button', { name: '确认切换' });
    expect(confirm()).toBeDisabled();          // 没写原因之前不给提交
    fireEvent.change(screen.getByLabelText(/切换原因/), { target: { value: '11 月版式有误，先切回上月那版' } });
    fireEvent.click(confirm());

    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/content/publish/a2', {
      action: 'rollback', release: 1, reason: '11 月版式有误，先切回上月那版', expected_version: 3,
    }));
    expect(await screen.findByText(/已把门户展示的版本切到第 1 期/)).toBeInTheDocument();
  });
});
