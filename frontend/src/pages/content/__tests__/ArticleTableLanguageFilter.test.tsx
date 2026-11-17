/**
 * 内容管理页语言筛 + 行内徽标 + 译文跳转（plan §P6/§Test）
 *
 * 关键判据：
 *  - `lang` 通过 `contentApi.list({ lang })` 上送（服务端受控清单外值 400）；
 *  - 行上的语言徽标只在**非 zh-CN** 时出现（zh-CN 是默认，画出来是噪声）；
 *  - 译文跳转按钮**只在 available=true 时**出现（点了不能 404）。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';

const listMock = vi.fn();
const navigateMock = vi.fn();
vi.mock('@/api/content', async () => {
  const actual = await vi.importActual<any>('@/api/content');
  return {
    ...actual,
    contentApi: {
      ...(actual as any).contentApi,
      list: (...a: any[]) => listMock(...a),
      categories: vi.fn().mockResolvedValue({ data: { items: [] } }),
      tags: vi.fn().mockResolvedValue({ data: { items: [] } }),
    },
  };
});
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<any>('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ user: { username: 'admin', role: 'admin' }, hasPerm: () => true }),
}));

import ArticleTable from '../ArticleTable';

const baseArticle = (over: any = {}) => ({
  id: 1, slug: 's-1', title: 'T', summary: '', category_id: 0, category_label: 'c',
  category_icon: '', category_color: '#000', tags: [], kind: 'markdown', kind_label: 'Markdown',
  link_url: '', link_label: '', refs: [], pinned: 0, featured: 0, status: 'published',
  is_live: true, versioning: 'keep_history', versioning_label: '', release: 1,
  release_label: '', release_count: 1, history_hidden: false, can_rollback: false,
  has_pending_change: false, can_republish: false, head_changed_since_archive: false,
  review_by: '', published_at: '2026-11-16T00:00:00', archived_at: '', view_count: 0,
  author: 'admin', updated_by: 'admin', updated_at: '2026-11-16T00:00:00', version: 1,
  lang: 'zh-CN', translation_group: 's-1',
  translation: { zh: { slug: 's-1', available: true }, en: { slug: '', available: false } },
  ...over,
});

describe('内容管理 · 双语条目 UI 契约', () => {
  beforeEach(() => {
    listMock.mockReset();
    navigateMock.mockReset();
    listMock.mockResolvedValue({ data: { items: [baseArticle()], total: 1, page: 1, page_size: 12 } });
  });

  it('语言筛选下拉存在，选 en-US 时把 lang 上送', async () => {
    render(<MemoryRouter><ArticleTable scope="all" /></MemoryRouter>);
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    const trigger = screen.getByLabelText('语言筛选');
    await userEvent.click(trigger);
    await userEvent.click(await screen.findByText('English 正文'));
    await waitFor(() => {
      const calls = listMock.mock.calls as any[][];
      const last = calls.length ? calls[calls.length - 1][0] : undefined;
      expect(last?.lang).toBe('en-US');
    });
  });

  it('en-US 条目行画 EN 徽标；中文条目不画（默认语言画出来是噪声）', async () => {
    listMock.mockResolvedValue({
      data: { items: [baseArticle({ slug: 'e-1', lang: 'en-US' }), baseArticle({ slug: 'z-1', lang: 'zh-CN' })], total: 2, page: 1, page_size: 12 },
    });
    render(<MemoryRouter><ArticleTable scope="all" /></MemoryRouter>);
    await waitFor(() => expect(screen.getAllByText('EN').length).toBe(1));
  });

  it('同 group 有英文在线版时给"→ English"跳转；available=false 不显示', async () => {
    listMock.mockResolvedValue({
      data: {
        items: [
          baseArticle({ slug: 'a', translation: { zh: { slug: 'a', available: true }, en: { slug: 'a-en', available: true } } }),
          baseArticle({ id: 2, slug: 'b', translation: { zh: { slug: 'b', available: true }, en: { slug: 'b-en', available: false } } }),
        ],
        total: 2, page: 1, page_size: 12,
      },
    });
    render(<MemoryRouter><ArticleTable scope="all" /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('→ English')).toBeInTheDocument());
    // 未发布的译文条目不应出现跳转入口
    expect(screen.queryAllByText('→ English')).toHaveLength(1);
  });
});
