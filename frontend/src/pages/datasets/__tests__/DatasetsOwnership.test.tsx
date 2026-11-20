/**
 * 数据集归属与可见性展示（2026-11-20 口径）。
 *
 * 锁定：
 *  - 卡片展示「创建者」业务名与「可见范围」业务名（不出现裸 id）；
 *  - 私有数据集显示「仅创建者」，workspace 显示「按角色共享」；
 *  - 管理权限 ∧ editable 才出现共享入口，点开走 GET /{id}/sharing + /roles/；
 *  - 他人私有（editable=false）不出现共享/删除入口（不呈空承诺）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Datasets from '../Datasets';
import client from '@/api/client';

vi.mock('@/stores/permissionStore', () => ({ hasPerm: () => true }));
vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

const row = (id: number, name: string, over: Record<string, any> = {}) => ({
  id, name, description: '', source_type: 'sql', object_key: '', datasource_id: 11,
  datasource_name: 'prd-cn', available_source_names: ['prd-cn'], usable_now: ['prd-cn'],
  chart_type: '', owner_id: 1, owner_name: '张三', visibility: 'workspace',
  visibility_label: '按角色共享', editable: true, is_owner: true,
  field_count: 2, references: 0, updated_at: '',
  ...over,
});

beforeEach(() => {
  (client.get as any).mockImplementation(async (url: string) => {
    if (url === '/datasets/') return { data: { items: [
      row(1, '私有集', { owner_name: '张三', visibility: 'private', visibility_label: '仅创建者' }),
      row(2, '共享集', { owner_name: '李四', editable: false, is_owner: false }),
    ] } };
    if (url === '/datasources/') return { data: [{ id: 11, name: 'prd-cn', site_code: 'cn', db_type: 'mysql' }] };
    if (url === '/datasets/1/sharing') return { data: {
      dataset_id: 1, name: '私有集', visibility: 'workspace', visibility_label: '按角色共享',
      owner_name: '张三', is_owner: true, roles: [], references: [],
    } };
    if (url === '/roles/') return { data: [{ id: 2, name: 'analyst', display_name: '数据分析师' }] };
    throw new Error(`unexpected GET ${url}`);
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function renderPage() { return render(<MemoryRouter><Datasets /></MemoryRouter>); }

describe('数据集归属展示', () => {
  it('展示创建者与可见范围业务名', async () => {
    renderPage();
    await screen.findByText('私有集');
    expect(screen.getAllByText('张三').length).toBeGreaterThan(0);
    expect(screen.getAllByText('仅创建者').length).toBeGreaterThan(0);
    expect(screen.getAllByText('李四').length).toBeGreaterThan(0);
    expect(screen.getAllByText('按角色共享').length).toBeGreaterThan(0);
    // 不得把裸 id 当名称展示
    expect(screen.queryByText(/ID:\s*1/)).toBeNull();
  });

  it('可编辑行有共享入口，他人行没有', async () => {
    renderPage();
    await screen.findByText('私有集');
    const shareBtns = screen.getAllByRole('button', { name: '共享' });
    expect(shareBtns.length).toBe(1);          // 只有「私有集」那一行
  });

  it('点共享打开对话框并拉取共享配置', async () => {
    renderPage();
    await screen.findByText('私有集');
    fireEvent.click(screen.getByRole('button', { name: '共享' }));
    expect(await screen.findByText('数据集共享')).toBeTruthy();
    expect(client.get).toHaveBeenCalledWith('/datasets/1/sharing');
  });
});
