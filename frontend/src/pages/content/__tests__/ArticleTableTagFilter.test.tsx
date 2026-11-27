/**
 * 内容管理页标签筛（plan：筛选器补齐标签维度）
 *
 * 关键判据：
 *  - 选项来自受控词表 `contentApi.tags()`（不硬编码标签名，ui-resource-display §2）；
 *  - 选中后事实源名通过 `contentApi.list({ tag })` 上送（服务端 LIKE 精确元素匹配）；
 *  - 哨兵「全部标签」不外传（传空串 = 不筛）；
 *  - 改筛选即回第 1 页（停在旧页码 + 新筛选会拿到错位清单，list-pagination §3）。
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
      tags: vi.fn().mockResolvedValue({
        data: { items: [{ id: 1, tag: '知识库', sort: 1, is_active: 1 }] },
      }),
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
  category_icon: '', category_color: '#000', tags: ['知识库'], kind: 'markdown', kind_label: 'Markdown',
  link_url: '', link_label: '', refs: [], pinned: 0, featured: 0, status: 'published',
  is_live: true, versioning: 'keep_history', versioning_label: '', release: 1,
  release_label: '', release_count: 1, history_hidden: false, can_rollback: false,
  has_pending_change: false, can_republish: false, head_changed_since_archive: false,
  review_by: '', published_at: '2026-11-27T00:00:00', archived_at: '', view_count: 0,
  author: 'admin', updated_by: 'admin', updated_at: '2026-11-27T00:00:00', version: 1,
  ...over,
});

describe('内容管理 · 标签筛选', () => {
  beforeEach(() => {
    listMock.mockReset();
    navigateMock.mockReset();
    listMock.mockResolvedValue({ data: { items: [baseArticle()], total: 1, page: 1, page_size: 12 } });
  });

  it('标签下拉存在，选项来自受控词表（不硬编码）', async () => {
    render(<MemoryRouter><ArticleTable scope="all" /></MemoryRouter>);
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    const trigger = screen.getByLabelText('标签筛选');
    await userEvent.click(trigger);
    // 用 role=option 定位（行内 TagChips 也有同名文本，findByText 会撞多个）
    expect(await screen.findByRole('option', { name: '知识库' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '全部标签' })).toBeInTheDocument();
  });

  it('选中标签后把事实源名 tag 上送；默认（全部标签）传空串', async () => {
    render(<MemoryRouter><ArticleTable scope="all" /></MemoryRouter>);
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    // 默认不筛：tag 空串
    expect((listMock.mock.calls as any[][])[0][0]?.tag).toBe('');

    await userEvent.click(screen.getByLabelText('标签筛选'));
    await userEvent.click(await screen.findByRole('option', { name: '知识库' }));
    await waitFor(() => {
      const calls = listMock.mock.calls as any[][];
      const last = calls[calls.length - 1][0];
      expect(last?.tag).toBe('知识库');
      expect(last?.page).toBe(1);
    });
  });
});
