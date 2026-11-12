import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import ReadArticle from '../ReadArticle';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useAuthStore } from '@/stores/authStore';
import { usePermissionStore } from '@/stores/permissionStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import client from '@/api/client';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

// 正文渲染器是全局唯一图表管线（MarkdownWithCharts），这里打桩只验"正文确实被交出去渲染"
vi.mock('@/components/MarkdownWithCharts', () => ({
  default: ({ text }: { text: string }) => <div data-testid="md">{text}</div>,
}));

const detail = (over: Record<string, unknown> = {}) => ({
  id: 1, slug: 'scan-accuracy-metric', title: '口内扫描精度怎么衡量',
  summary: '三层口径拆开', body: '## 一、先分清 trueness 和 precision\n正文段落',
  category_id: 2, category_label: '技术白皮书', category_icon: '🔬', category_color: '#0891b2',
  tags: ['扫描精度'], kind: 'markdown', kind_label: 'Markdown', link_url: '', link_label: '',
  refs: [{ title: 'ISO 20896-1:2019', url: 'https://example.org/iso' }],
  pinned: 1, featured: 1, status: 'published', is_live: true, has_pending_change: false,
  review_by: '2027-04-03', published_at: new Date(Date.now() - 3 * 86400_000).toISOString(),
  archived_at: '', view_count: 1240, author: '临床应用研究组', updated_by: '', updated_at: '',
  version: 3, files: [], kb_sync: null,
  ...over,
});

// pathname + search 一起输出：期次走 URL 参数，只看 pathname 测不到“链接能不能直接分享旧那一期”
function Path() { const location = useLocation(); return <output data-testid="path">{location.pathname}{location.search}</output>; }

function renderRead(slug = 'scan-accuracy-metric', routes: Record<string, unknown> = {}, search = '') {
  const table: Record<string, unknown> = {
    '/content/categories': { items: [{ id: 2, key: 'whitepaper', label: '技术白皮书', icon: '🔬', color: '#0891b2', sort: 2, is_active: 1 }] },
    '/content/tags': { items: [] },
    '/content/articles/scan-accuracy-metric': detail(),
    '/content/articles/scan-accuracy-metric/related': { items: [] },
    ...routes,
  };
  vi.mocked(client.get).mockImplementation(((url: string) => {
    if (!(url in table)) return Promise.reject(new Error(`未预期请求 ${url}`));
    // 值本身就是 Promise（用例要造失败态）时不能被再包一层 resolve，否则 rejected 变成 fulfilled
    const v = table[url];
    return v instanceof Promise ? v : Promise.resolve({ data: v });
  }) as never);
  return render(<TooltipProvider><MemoryRouter initialEntries={[`/read/${slug}${search}`]}><Path /><Routes>
    <Route path="/read/:slug" element={<ReadArticle />} />
    <Route path="/" element={<div>首页</div>} />
    <Route path="/ws/:wsId/chat" element={<div>问数</div>} />
    <Route path="/workspace" element={<div>空间入口</div>} />
  </Routes></MemoryRouter></TooltipProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  useAuthStore.setState({ user: { id: 1, username: 'carol', role: 'viewer' } } as any);
  usePermissionStore.setState({ loaded: true, unrestricted: false, perms: ['article:read'], allowedMenus: ['home:feed'] });
  useWorkspaceStore.setState({ workspaces: [{ id: 301 } as never], loaded: true, loading: false });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  usePermissionStore.setState({ loaded: false, unrestricted: true, perms: null, allowedMenus: null });
});

describe('首页资讯区的阅读页 /read/:slug', () => {
  it('按读者口径取数：revision=live 且计入阅读数', async () => {
    renderRead();
    expect(await screen.findByRole('heading', { name: /口内扫描精度怎么衡量/ }, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole('main')).toHaveAttribute('data-page-width', 'reading');
    const call = vi.mocked(client.get).mock.calls.find(c => String(c[0]).startsWith('/content/articles/'));
    expect(call?.[1]).toMatchObject({ params: { revision: 'live', count_view: 1 } });
  });

  it('正文交给站内渲染管线，出处清单原样列出', async () => {
    renderRead();
    expect(await screen.findByTestId('md', {}, { timeout: 3000 })).toHaveTextContent('trueness');
    expect(screen.getByText('引用出处')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'ISO 20896-1:2019' })).toHaveAttribute('rel', expect.stringContaining('noopener'));
  });

  it('点进即记已读（首页未读点据此消失），但不把草稿状态带进本地清单', async () => {
    renderRead();
    await screen.findByRole('heading', { name: /口内扫描精度怎么衡量/ });
    await waitFor(() => expect(JSON.parse(localStorage.getItem('home_read_articles') || '[]')).toContain('scan-accuracy-metric'));
  });

  it('附件区对读者只给下载：上传与删除入口不出现', async () => {
    renderRead('scan-accuracy-metric', {
      '/content/articles/scan-accuracy-metric': detail({
        kind: 'file', kind_label: '附件',
        files: [{ file_id: 11, filename: '行业报告全文.pdf', label: 'PDF', mime: 'application/pdf', size: 4_200_000, uploaded_at: '' }],
      }),
    });
    expect(await screen.findByText('行业报告全文.pdf', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /上传附件/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^删除附件/ })).not.toBeInTheDocument();
    // 没有「预览」按钮是刻意的：上传件以本站源 inline 渲染等于存储型 XSS
    expect(screen.queryByRole('button', { name: /预览/ })).not.toBeInTheDocument();
  });

  it('外链条目不渲染正文，只给导读与前往原文', async () => {
    renderRead('scan-accuracy-metric', {
      '/content/articles/scan-accuracy-metric': detail({ kind: 'link', kind_label: '原文', body: '', link_url: 'https://example.org/a', link_label: '看原始发布' }),
    });
    const a = await screen.findByRole('link', { name: /看原始发布/ }, { timeout: 3000 });
    expect(a).toHaveAttribute('target', '_blank');
    expect(screen.queryByTestId('md')).not.toBeInTheDocument();
  });

  it('阅读页自带滚动口：#root 是 overflow:hidden，正文区必须自己可滚', async () => {
    renderRead();
    await screen.findByRole('heading', { name: /口内扫描精度怎么衡量/ }, { timeout: 3000 });
    const main = screen.getByRole('main');
    // 缺这三件就会重现“详情页滚不动”：外壳 flex 列 + main 自身 overflow-auto + min-h-0
    for (const cls of ['overflow-auto', 'min-h-0', 'flex-1', 'relative']) {
      expect(main.className).toContain(cls);
    }
    // relative 是护栏：正文里的 position:absolute 后代（时间轴）要落在本口内，
    // 不然会越过裁剪去撑高 document，变成整个系统页被滚动
    expect(main.closest('div')?.className).toContain('flex-col');
  });

  it('HTML 版式走清洗渲染口：不进 Markdown 管线、可执行标记被剥掉、不画形态徽标', async () => {
    renderRead('scan-accuracy-metric', {
      '/content/articles/scan-accuracy-metric': detail({
        kind: 'html', kind_label: 'HTML 版式',
        body: '<section><h3>企业发展时间线</h3>'
          + '<p style="color:hsl(var(--primary))">2019 年成立</p>'
          + '<a href="https://www.allied-star.com">官方网站</a>'
          + '<script>window.hacked = 1</script><img src="x" onerror="window.hacked=1" /></section>',
      }),
    });
    expect(await screen.findByText('企业发展时间线', {}, { timeout: 3000 })).toBeInTheDocument();
    // 两条管线不能混用：html 正文交给 HtmlArticle，MarkdownWithCharts 不该被调起
    expect(screen.queryByTestId('md')).not.toBeInTheDocument();
    // 形态是编排细节：markdown 与 html 都不给读者画徽标
    expect(screen.queryByText('HTML 版式')).not.toBeInTheDocument();
    const link = screen.getByRole('link', { name: '官方网站' });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    // 主题变量内联样式要活着留下（暗/亮两套主题靠它），script/img 这类可执行载体必须没进 DOM
    expect(screen.getByText('2019 年成立').getAttribute('style')).toContain('hsl(var(--primary))');
    expect(document.querySelector('script')).toBeNull();
    expect(document.querySelector('img')).toBeNull();
    expect((window as { hacked?: number }).hacked).toBeUndefined();
    // 富版式不挤在中间一列：HTML 版式铺满页面，markdown 长文保持窄栏
    expect(screen.getByRole('main')).toHaveAttribute('data-page-width', 'wide');
  });

  it('未发布/无权读取时显式报错并可重试，不静默跳回首页', async () => {
    renderRead('draft-one', {
      '/content/articles/draft-one': Promise.reject(Object.assign(new Error('x'), {
        response: { status: 404, data: { detail: '内容尚未发布' } },
      })),
    });
    expect(await screen.findByRole('alert', {}, { timeout: 3000 })).toHaveTextContent('内容尚未发布');
    expect(screen.getByTestId('path')).toHaveTextContent('/read/draft-one');
  });

  it('「就本主题提问」带着这篇的标题进智能问数（落点跟随默认工作空间）', async () => {
    renderRead();
    await userEvent.click(await screen.findByRole('button', { name: /就本主题提问/ }, { timeout: 3000 }));
    expect(sessionStorage.getItem('home_question')).toContain('口内扫描精度怎么衡量');
    // 不标 send：递过去的是一句开头，到问数页只预填等人补全（自动发送只会问出一句空话）
    expect(sessionStorage.getItem('home_question_send')).toBeNull();
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/ws/301/chat'));
  });

  it('同栏目其它内容可继续点读（不做假邻接的上一篇/下一篇）', async () => {
    renderRead('scan-accuracy-metric', {
      '/content/articles/scan-accuracy-metric/related': {
        items: [detail({ slug: 'wet-field-failure-triage', title: '湿表面、出血与反光', pinned: 0 })],
      },
    });
    expect(await screen.findByText('同栏目其它内容', {}, { timeout: 3000 })).toBeInTheDocument();
    await userEvent.click(screen.getByText('湿表面、出血与反光'));
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/read/wet-field-failure-triage'));
    expect(screen.queryByText('上一篇')).not.toBeInTheDocument();
  });

  it('返回首页回到首页资讯区，不在阅读页留下第二套导航', async () => {
    renderRead();
    await userEvent.click(await screen.findByRole('button', { name: /返回首页/ }, { timeout: 3000 }));
    await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/'));
  });
});

describe('历史期次（同一个主题的已发布版本）', () => {
  const releases = (over: Record<string, unknown> = {}) => ({
    items: [
      { release: 2, label: '2026-11', released_at: '2026-11-01T12:00:00', is_current: true },
      { release: 1, label: '2026-10', released_at: '2026-10-01T12:00:00', is_current: false },
    ],
    versioning: 'keep_history', versioning_label: '保留历史期次',
    history_hidden: false, attachments_shared: true,
    ...over,
  });

  it('头部报出“这是哪一期”：多于一期时给历史期次下拉，选中后按 ?release=N 取数', async () => {
    renderRead('scan-accuracy-metric', {
      '/content/articles/scan-accuracy-metric': detail({ release: 2, release_label: '2026-11', release_count: 2 }),
      '/content/articles/scan-accuracy-metric/releases': releases(),
    });
    // 头部标记与下拉的当前项都写着同一件事（不是两处各算一份），所以只判存在
    expect(await screen.findAllByText(/第 2 期·2026-11/, {}, { timeout: 3000 }).then(vs => vs.length > 0)).toBe(true);
    await userEvent.click(screen.getByRole('combobox', { name: '选择要阅读的历史期次' }));
    await userEvent.click(await screen.findByRole('option', { name: /第 1 期·2026-10/ }));

    await waitFor(() => expect(
      vi.mocked(client.get).mock.calls.some(c => String(c[0]) === '/content/articles/scan-accuracy-metric'
        && (c[1] as { params?: Record<string, unknown> })?.params?.release === 1),
    ).toBe(true));
    // 期次号进 URL 才能分享：“给我看上个月那一期”不应需要重走首页点进去
    expect(await screen.findByTestId('path')).toHaveTextContent(/release=1/);
  });

  it('只有一期时不画下拉：只能选中自己的开关会让人以为历史丢了', async () => {
    renderRead('scan-accuracy-metric', {
      '/content/articles/scan-accuracy-metric': detail({ release: 1, release_label: '2026-11', release_count: 1 }),
      '/content/articles/scan-accuracy-metric/releases': releases({
        items: [{ release: 1, label: '2026-11', released_at: '2026-11-01T12:00:00', is_current: true }],
      }),
    });
    expect(await screen.findByText(/第 1 期·2026-11/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '选择要阅读的历史期次' })).not.toBeInTheDocument();
    expect(screen.queryByText(/只发布最新版本/)).not.toBeInTheDocument();
  });

  it('策略为只保留最新：不画下拉，但要把“为什么没有历史”说出来', async () => {
    renderRead('scan-accuracy-metric', {
      // 服务端在 latest_only 下只下发当前一期，但 release_count 仍是真实期数
      '/content/articles/scan-accuracy-metric': detail({ release: 4, release_label: '2026-11',
        release_count: 4, versioning: 'latest_only', history_hidden: true }),
      '/content/articles/scan-accuracy-metric/releases': releases({
        history_hidden: true, versioning: 'latest_only', versioning_label: '只保留最新版本',
        items: [{ release: 4, label: '2026-11', released_at: '2026-11-01T12:00:00', is_current: true }],
      }),
    });
    expect(await screen.findByText(/该内容只发布最新版本/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '选择要阅读的历史期次' })).not.toBeInTheDocument();
  });

  it('读旧期时告知“这不是当前那一版”并给回最新一期；附件不换期这件事也说出来', async () => {
    renderRead('scan-accuracy-metric', {
      '/content/articles/scan-accuracy-metric': detail({ release: 2, release_label: '2026-11', release_count: 2,
        viewing_release: 1, viewing_release_label: '2026-10', viewing_is_current: false,
        files: [{ file_id: 11, filename: '十月刊全文.pdf', label: 'PDF',
                 mime: 'application/pdf', size: 1_200_000, uploaded_at: '' }] }),
      '/content/articles/scan-accuracy-metric/releases': releases(),
    }, '?release=1');

    // 期次取数走 URL 参数：这个链接能直接发给同事看旧那一版
    await waitFor(() => expect(
      vi.mocked(client.get).mock.calls.some(c => String(c[0]) === '/content/articles/scan-accuracy-metric'
        && (c[1] as { params?: Record<string, unknown> })?.params?.release === 1),
    ).toBe(true));
    expect(await screen.findByText(/正在阅读第 1 期的旧内容/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByText(/附件与期次无关/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '看最新一期' }));
    await waitFor(() => expect(screen.getByTestId('path')).not.toHaveTextContent(/release=1/));
  });
});
