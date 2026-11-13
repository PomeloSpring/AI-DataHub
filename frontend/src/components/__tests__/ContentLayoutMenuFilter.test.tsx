import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ContentLayout from '../ContentLayout';
import { TooltipProvider } from '../ui/tooltip';
import client from '@/api/client';
import { usePermissionStore } from '@/stores/permissionStore';
import { useAuthStore } from '@/stores/authStore';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
// 页面主体与本页用例无关（侧栏显隐才是被测面），挂占位避免拉真实页面依赖
vi.mock('@/pages/content/ContentHome', () => ({ default: () => <div>内容首页</div> }));
vi.mock('@/pages/content/ContentManage', () => ({ default: () => <div>内容管理主体</div> }));
vi.mock('@/pages/content/MyDrafts', () => ({ default: () => <div>我的草稿主体</div> }));

const grant = (perms: string[], menus: string[]) =>
  usePermissionStore.setState({ loaded: true, unrestricted: false, perms, allowedMenus: menus });

function renderLayout(path = '/content/manage') {
  return render(
    <TooltipProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/content" element={<ContentLayout />}>
            <Route index element={<div>内容首页</div>} />
            <Route path="manage" element={<div>内容管理主体</div>} />
            <Route path="drafts" element={<div>我的草稿主体</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </TooltipProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockImplementation(async (url: string) => ({
    data: url === '/admin/brand'
      ? { show_icon: true, show_text: true, app_name: 'AI-DataHub' }
      : url === '/content/reviews/pending'
        ? { items: [], total: 3, page: 1, page_size: 1, review_expired: [] }
        : [],
  }));
  useAuthStore.setState({ user: { id: 1, username: 'tester', role: 'viewer' } } as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('ContentLayout 侧栏：审核发布已并入内容管理', () => {
  it('不再渲染「审核发布」入口（页面与菜单登记同批退役）', () => {
    grant(['article:manage', 'article:review', 'article:publish'],
      ['content:drafts', 'content:manage', 'content:taxonomy', 'content:kb-docs']);
    renderLayout();
    expect(screen.getByText('内容管理')).toBeInTheDocument();
    expect(screen.queryByText('审核发布')).not.toBeInTheDocument();
  });

  it('只持 article:review 的审核人看得见「内容管理」（菜单归属反查，不是靠编辑权）', () => {
    // 服务端把 content:manage 下发给该角色（依据：article:review.menu_key=content:manage）
    grant(['article:review'], ['content:manage']);
    renderLayout();
    expect(screen.getByText('内容管理')).toBeInTheDocument();
    expect(screen.queryByText('我的草稿')).not.toBeInTheDocument();
  });

  it('待审角标挂在内容管理上，且只有审核权的人才会去拉队列', async () => {
    grant(['article:review'], ['content:manage']);
    renderLayout();
    const item = await waitFor(() => screen.getByText('内容管理').closest('button')!);
    expect(item).toHaveTextContent('3');
    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/content/reviews/pending',
      expect.objectContaining({ params: { page: 1, page_size: 1 } })));
  });

  it('没有审核权时不拉待审队列，也不给角标（不能拿菜单 key 当条件白跑一次）', async () => {
    grant(['article:manage'], ['content:drafts', 'content:manage']);
    renderLayout();
    await waitFor(() => expect(screen.getByText('内容管理')).toBeInTheDocument());
    expect(vi.mocked(client.get).mock.calls.some(([url]) => url === '/content/reviews/pending')).toBe(false);
    expect(screen.getByText('内容管理').closest('button')).not.toHaveTextContent(/\d/);
  });
});
