import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import RoleManagement from '../RoleManagement';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));

// 「菜单与功能」页签数据: 菜单分组 + 无菜单归属(standalone) + 只读接口绑定
const REGISTRY = {
  groups: [],
  menu_groups: [
    {
      menu_key: 'data:ontology', label: '本体管理', section: '数据资产', module: 'data', module_label: '数据中台', sort: 1,
      permissions: [
        { perm_code: 'ontology:save', label: '保存本体', description: '保存本体模型', menu_key: 'data:ontology', api_pattern: '/api/ontology/*', api_method: 'POST' },
        { perm_code: 'ontology:delete', label: '删除本体', description: '', menu_key: 'data:ontology', api_pattern: '/api/ontology/models/*', api_method: 'DELETE' },
      ],
    },
    {
      menu_key: 'data:reports', label: '报表中心', section: '数据资产', module: 'data', module_label: '数据中台', sort: 2,
      permissions: [
        { perm_code: 'report:view', label: '查看报表', description: '', menu_key: 'data:reports', api_pattern: '/api/reports/*,/api/scheduled/*', api_method: 'GET' },
      ],
    },
  ],
  standalone: [
    { perm_code: 'legacy:code', label: '无菜单权限', description: '', menu_key: '', api_pattern: '/api/legacy', api_method: '*' },
  ],
};

// 「数据安全策略」页签数据: 行/列策略清单(含停用) + 已绑定回显
const POLICIES = {
  total: 2,
  items: [
    { id: 10, name: '华东过滤', description: '', workspace_id: 0, datasource_id: 1, table_name: 'orders',
      policy_type: 'both', filter_type: 'user_attribute', filter_expr: 'region = :user_region',
      user_attribute: 'region', is_active: 1, created_by: 1, created_at: '', updated_at: '' },
    { id: 11, name: '已停用策略', description: '', workspace_id: 0, datasource_id: 1, table_name: 'users',
      policy_type: 'row', filter_type: 'condition', filter_expr: "status = 'active'",
      user_attribute: '', is_active: 0, created_by: 1, created_at: '', updated_at: '' },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url === '/roles/') return { data: [{ id: 1, name: 'viewer', display_name: '查看角色', description: '', is_system: 1, is_active: 1 }] };
    if (url === '/roles/perm-registry') return { data: REGISTRY };
    if (url === '/roles/1/permissions') return { data: { role_id: 1, permissions: ['ontology:save'] } };
    if (url === '/admin/rls-policies') return { data: POLICIES };
    if (url === '/roles/1/rls-policies') return { data: { role_id: 1, policy_ids: [10] } };
    return { data: [] };
  });
  vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function openPermTab(tabName: RegExp, waitText: string) {
  render(<MemoryRouter><RoleManagement /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: '权限配置' }));
  // Radix Tabs 由 mousedown 选中
  const tab = await screen.findByRole('tab', { name: tabName });
  fireEvent.mouseDown(tab);
  fireEvent.click(tab);
  await screen.findByText(waitText);
}

async function openFunctionsTab() {
  await openPermTab(/菜单与功能/, '本体管理');
}

async function openPoliciesTab() {
  await openPermTab(/数据安全策略/, '华东过滤');
}

async function openDashboardsTab() {
  await openPermTab(/看板/, '数字化大屏');
}

describe('角色权限 — 菜单与功能页签', () => {
  it('按菜单分组渲染功能项，无菜单归属归「通用功能」', async () => {
    await openFunctionsTab();
    // 菜单卡片组头: 菜单名 + 已选计数 + section
    expect(screen.getByText('本体管理')).toBeInTheDocument();
    expect(screen.getByText('1/2')).toBeInTheDocument();
    expect(screen.getByText('报表中心')).toBeInTheDocument();
    expect(screen.getByText('0/1')).toBeInTheDocument();
    // 功能项: label + perm_code + 描述
    expect(screen.getByText('保存本体')).toBeInTheDocument();
    expect(screen.getByText('ontology:save')).toBeInTheDocument();
    expect(screen.getByText('保存本体模型')).toBeInTheDocument();
    // menu_key 为空/指向不存在菜单 → 通用功能
    expect(screen.getByText('通用功能')).toBeInTheDocument();
    expect(screen.getByText('无菜单权限')).toBeInTheDocument();
    // fail-closed 语义文案
    expect(screen.getByText(/fail-closed/)).toBeInTheDocument();
  });

  it('已授权功能项显示勾选，组头勾选整组全选/取消', async () => {
    await openFunctionsTab();
    expect((screen.getByRole('checkbox', { name: /保存本体/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('checkbox', { name: /删除本体/ }) as HTMLInputElement).checked).toBe(false);
    const group = screen.getByRole('checkbox', { name: /本体管理/ }) as HTMLInputElement;
    expect(group.checked).toBe(false);
    fireEvent.click(group); // 全选
    expect((screen.getByRole('checkbox', { name: /保存本体/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('checkbox', { name: /删除本体/ }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText('2/2')).toBeInTheDocument();
    fireEvent.click(group); // 全部取消
    expect((screen.getByRole('checkbox', { name: /保存本体/ }) as HTMLInputElement).checked).toBe(false);
    expect((screen.getByRole('checkbox', { name: /删除本体/ }) as HTMLInputElement).checked).toBe(false);
  });

  it('功能项可展开只读查看绑定接口（method + api_pattern）', async () => {
    await openFunctionsTab();
    const row = screen.getByText('保存本体').closest('.border') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: '接口(1)' }));
    // 只读展示: code 元素, 非可编辑输入
    const pattern = screen.getByText('/api/ontology/*');
    expect(pattern.tagName).toBe('CODE');
    expect(within(row).getByText('POST')).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: '收起接口' })).toBeInTheDocument();
    // 多接口绑定按逗号拆分计数
    expect(screen.getByRole('button', { name: '接口(2)' })).toBeInTheDocument();
  });

  it('菜单分组支持展开/收起，一键全部展开/收起（方便浏览长列表）', async () => {
    await openFunctionsTab();
    // 默认展开: 功能项可见
    expect(screen.getByText('保存本体')).toBeInTheDocument();
    // 收起单组 → 功能项隐藏(组头保留，计数仍在)
    fireEvent.click(screen.getByRole('button', { name: '收起 本体管理' }));
    expect(screen.queryByText('保存本体')).not.toBeInTheDocument();
    expect(screen.getByText('1/2')).toBeInTheDocument();
    // 展开恢复
    fireEvent.click(screen.getByRole('button', { name: '展开 本体管理' }));
    expect(screen.getByText('保存本体')).toBeInTheDocument();
    // 一键全部收起(含通用功能)
    fireEvent.click(screen.getByRole('button', { name: '全部收起' }));
    expect(screen.queryByText('保存本体')).not.toBeInTheDocument();
    expect(screen.queryByText('无菜单权限')).not.toBeInTheDocument();
    // 一键全部展开
    fireEvent.click(screen.getByRole('button', { name: '全部展开' }));
    expect(screen.getByText('保存本体')).toBeInTheDocument();
    expect(screen.getByText('无菜单权限')).toBeInTheDocument();
  });

  it('保存功能权限按勾选码全量替换提交', async () => {
    await openFunctionsTab();
    fireEvent.click(screen.getByRole('checkbox', { name: /删除本体/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存功能权限' }));
    await waitFor(() => expect(client.put).toHaveBeenCalledWith('/roles/1/permissions', {
      permissions: expect.arrayContaining(['ontology:save', 'ontology:delete']),
    }));
    const payload = vi.mocked(client.put).mock.calls[0][1] as { permissions: string[] };
    expect(payload.permissions.sort()).toEqual(['ontology:delete', 'ontology:save']);
  });
});

describe('角色权限 — 数据安全策略页签', () => {
  it('渲染策略清单（类型徽标/目标表/过滤表达式），已绑定策略回显勾选', async () => {
    await openPoliciesTab();
    // 策略名 + 类型徽标 + 目标表 + 过滤表达式(只读 code)
    expect(screen.getByText('华东过滤')).toBeInTheDocument();
    expect(screen.getByText('行+列')).toBeInTheDocument();
    expect(screen.getByText('orders')).toBeInTheDocument();
    expect(screen.getByText('region = :user_region').tagName).toBe('CODE');
    expect(screen.getByText('已停用策略')).toBeInTheDocument();
    expect(screen.getByText('已停用')).toBeInTheDocument();
    // 回显: 绑定 [10] → 华东过滤勾选, 其余未勾选
    const boundRow = screen.getByText('华东过滤').closest('.border') as HTMLElement;
    expect((within(boundRow).getByRole('switch') as HTMLInputElement).getAttribute('aria-checked')
      ?? String((within(boundRow).getByRole('switch') as HTMLInputElement).checked)).toBeTruthy();
    const offRow = screen.getByText('已停用策略').closest('.border') as HTMLElement;
    const offSwitch = within(offRow).getByRole('switch') as HTMLInputElement;
    expect(offSwitch.getAttribute('aria-checked') ?? String(offSwitch.checked)).toBe('false');
  });

  it('勾选策略即全量保存绑定（勾选才生效语义）', async () => {
    await openPoliciesTab();
    const row = screen.getByText('已停用策略').closest('.border') as HTMLElement;
    fireEvent.click(within(row).getByRole('switch'));
    await waitFor(() => expect(client.put).toHaveBeenCalledWith('/roles/1/rls-policies', {
      policy_ids: expect.arrayContaining([10, 11]),
    }));
    const payload = vi.mocked(client.put).mock.calls[0][1] as { policy_ids: number[] };
    expect(payload.policy_ids.sort()).toEqual([10, 11]);
  });

  it('取消勾选后绑定中移除该策略', async () => {
    await openPoliciesTab();
    const row = screen.getByText('华东过滤').closest('.border') as HTMLElement;
    fireEvent.click(within(row).getByRole('switch'));
    await waitFor(() => expect(client.put).toHaveBeenCalledWith('/roles/1/rls-policies', {
      policy_ids: [],
    }));
  });

  it('无策略时提示先在数据安全策略页配置', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) => {
      if (url === '/roles/') return { data: [{ id: 1, name: 'viewer', display_name: '查看角色', description: '', is_system: 1, is_active: 1 }] };
      if (url === '/admin/rls-policies') return { data: { total: 0, items: [] } };
      return { data: [] };
    });
    render(<MemoryRouter><RoleManagement /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '权限配置' }));
    const tab = await screen.findByRole('tab', { name: /数据安全策略/ });
    fireEvent.mouseDown(tab);
    fireEvent.click(tab);
    expect(await screen.findByText(/暂无策略，请先在「数据安全策略」页配置/)).toBeInTheDocument();
  });
});

describe('角色权限 — 看板页签', () => {
  // 看板不按工作空间归属: 平铺勾选, 显示看板名而非裸 ID/工作空间分组
  const DASHBOARDS = [
    { id: 21, name: '数字化大屏', status: 'enabled' },
    { id: 22, name: 'Test', status: 'designing' },
  ];

  beforeEach(() => {
    vi.mocked(client.get).mockImplementation(async (url: string) => {
      if (url === '/roles/') return { data: [{ id: 1, name: 'viewer', display_name: '查看角色', description: '', is_system: 1, is_active: 1 }] };
      if (url === '/roles/dashboard-catalog') return { data: DASHBOARDS };
      if (url === '/roles/1/dashboards') return { data: [{ dashboard_id: 21 }] };
      return { data: [] };
    });
  });

  it('看板平铺展示且无工作空间分组/裸 ID 补位', async () => {
    await openDashboardsTab();
    expect(screen.queryByText(/工作空间/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^\d+$/)).not.toBeInTheDocument();
    expect(screen.getByText('数字化大屏')).toBeInTheDocument();
    expect(screen.getByText('Test')).toBeInTheDocument();
    expect(screen.getByText('未启用')).toBeInTheDocument();
  });

  it('已授权看板回显开关，勾选即全量替换保存', async () => {
    await openDashboardsTab();
    const onRow = screen.getByText('数字化大屏').closest('.border') as HTMLElement;
    expect((within(onRow).getByRole('switch') as HTMLInputElement).getAttribute('aria-checked')
      ?? String((within(onRow).getByRole('switch') as HTMLInputElement).checked)).toBeTruthy();
    const offRow = screen.getByText('Test').closest('.border') as HTMLElement;
    expect((within(offRow).getByRole('switch') as HTMLInputElement).getAttribute('aria-checked')
      ?? String((within(offRow).getByRole('switch') as HTMLInputElement).checked)).toBe('false');
    fireEvent.click(within(offRow).getByRole('switch'));
    await waitFor(() => expect(client.put).toHaveBeenCalledWith('/roles/1/dashboards', {
      dashboard_ids: expect.arrayContaining([21, 22]),
    }));
  });
});
