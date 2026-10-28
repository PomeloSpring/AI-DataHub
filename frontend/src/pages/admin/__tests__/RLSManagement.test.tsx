import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import RLSManagement from '../RLSManagement';
import client from '@/api/client';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));

const policy = (over: Record<string, any> = {}) => ({
  id: 1, name: '策略A', description: '', workspace_id: 0, datasource_id: 1,
  table_name: 'orders', policy_type: 'row', filter_type: 'condition',
  filter_expr: '', user_attribute: '', is_active: 1, created_by: 1,
  created_at: '', updated_at: '', ...over,
});

/**
 * 按 policy_types 查询参数过滤返回（行/列/表页签服务端分页下推的真实契约）。
 * 让 mock 尊重类型筛选，才能验证「每个页签只看到自己那一类」而非全量摊到三页签。
 */
function mockPolicyList(items: ReturnType<typeof policy>[]) {
  return (url: string, opts?: any) => {
    if (url === '/admin/rls-policies') {
      const types: string[] | undefined = opts?.params?.policy_types?.split(',');
      const filtered = types ? items.filter(p => types.includes(p.policy_type)) : items;
      return { data: { total: filtered.length, items: filtered } };
    }
    return undefined;
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(client.get).mockImplementation(async (url: string) => {
    if (url === '/admin/rls-policies') return { data: { total: 0, items: [] } };
    return { data: [] };
  });
  vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
  vi.mocked(client.post).mockResolvedValue({ data: { success: true, id: 99 } } as any);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('数据安全策略 — 资源显示用 name', () => {
  it('策略卡片显示数据源 name 而非裸 ID，悬空数据源标注已删除', async () => {
    const policies = [
      policy(),
      policy({ id: 2, name: '策略B', datasource_id: 325991609814964 }),
    ];
    vi.mocked(client.get).mockImplementation(async (url: string, opts?: any) => {
      if (url === '/datasources/') return { data: [{ id: 1, name: 'test-alb', db_type: 'mysql' }] };
      return mockPolicyList(policies)(url, opts) ?? { data: [] };
    });
    render(<RLSManagement />);
    await screen.findByText('test-alb');
    // 悬空数据源给可读标注, 不回退成裸 ID
    expect(screen.getByText('数据源已删除')).toBeInTheDocument();
    expect(screen.queryByText(/ID:/)).not.toBeInTheDocument();
  });
});

describe('数据安全策略 — 用户属性页签已退役', () => {
  it('属性统一由角色权限管理，不再提供按用户配置的属性页签', async () => {
    render(<RLSManagement />);
    await screen.findByRole('tab', { name: /行策略/ });
    expect(screen.queryByRole('tab', { name: /用户属性/ })).not.toBeInTheDocument();
    // 旧的按用户配置接口也不再被调用
    expect(client.get).not.toHaveBeenCalledWith('/admin/rls-user-attributes/10', expect.anything());
  });
});

describe('数据安全策略 — 行/列/表完全分开管理', () => {
  it('三个独立页面签; 列策略不再混入"行+列"混合入口, 新建表单无白名单模式选择器', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string, opts?: any) => {
      return mockPolicyList([
        policy({ id: 1, policy_type: 'row' }),
        policy({ id: 2, name: '列黑名单', policy_type: 'column' }),
        policy({ id: 3, name: '表白名单', policy_type: 'table' }),
      ])(url, opts) ?? { data: [] };
    });
    render(<RLSManagement />);
    await screen.findByRole('tab', { name: /行策略/ });
    expect(screen.getByRole('tab', { name: /列策略/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /表策略/ })).toBeInTheDocument();
    // 遗留 both 类型只读标注, 不再提供新建入口
    expect(screen.queryByText('行+列')).not.toBeInTheDocument();
    // 行策略页签：只看到 row 类，列/表类不得混入（分页类型下推的真实契约）
    expect(screen.getByText('策略A')).toBeInTheDocument();
    expect(screen.queryByText('列黑名单')).not.toBeInTheDocument();
    expect(screen.queryByText('表白名单')).not.toBeInTheDocument();
    // 列策略页签: 说明是黑名单语义, 且旧白名单/黑名单模式切换器已退役
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: /列策略/ }));
    expect(await screen.findByText('列黑名单')).toBeInTheDocument();
    // 切到列页签后行/表类不残留
    expect(screen.queryByText('策略A')).not.toBeInTheDocument();
    expect(screen.queryByText('表白名单')).not.toBeInTheDocument();
    expect(screen.queryByText(/白名单（仅允许指定列）/)).not.toBeInTheDocument();
  });
});

describe('数据安全策略 — 列策略(黑名单)', () => {
  it('保存列策略提交纯黑名单行, access_type 只允许 hidden/masked, 不出现 visible', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) => {
      if (url === '/datasources/') return { data: [{ id: 1, name: 'test-alb', db_type: 'mysql' }] };
      if (url === '/admin/rls-policies') return { data: { total: 0, items: [] } };
      if (url === '/datasources/1/tables') return { data: [{ TABLE_NAME: 'orders' }] };
      if (url.startsWith('/datasources/1/tables/orders/columns')) return { data: [{ COLUMN_NAME: 'salary', DATA_TYPE: 'decimal' }] };
      return { data: [] };
    });
    const user = userEvent.setup();
    render(<RLSManagement />);
    await user.click(await screen.findByRole('tab', { name: /列策略/ }));
    await user.click(screen.getByRole('button', { name: '新建列策略' }));
    fireEvent.change(screen.getByPlaceholderText('如: 仅看本区域数据'), { target: { value: '薪资电话黑名单' } });
    // 选数据源 → 选表 → 选列 → 添加 → 保存
    await user.click(screen.getByRole('combobox', { name: '数据源' }));
    await user.click(await screen.findByRole('option', { name: /test-alb/ }));
    await user.click(screen.getByRole('combobox', { name: '目标表' }));
    await user.click(await screen.findByRole('option', { name: 'orders' }));
    await user.click(screen.getByRole('combobox', { name: '列名' }));
    await user.click(await screen.findByRole('option', { name: /salary/ }));
    const addBtn = screen.getByRole('button', { name: '添加黑名单列' });
    await user.click(addBtn);
    expect(screen.getByRole('button', { name: '移除 salary' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '保存' }));
    await vi.waitFor(() => {
      const call = vi.mocked(client.put).mock.calls.find(c => String(c[0]).includes('/columns'));
      expect(call).toBeTruthy();
      const body = call![1] as any;
      expect(body.columns).toEqual([{ column_name: 'salary', access_type: 'hidden', mask_pattern: '' }]);
      expect(JSON.stringify(body)).not.toContain('visible');
    });
  });
});

describe('数据安全策略 — 选择搜索框(表/列/属性名不手填)', () => {
  const mockCatalog = () => {
    vi.mocked(client.get).mockImplementation(async (url: string) => {
      if (url === '/datasources/') return { data: [{ id: 1, name: 'test-alb', db_type: 'mysql' }] };
      if (url === '/admin/rls-policies') return { data: { total: 0, items: [] } };
      if (url === '/datasources/1/tables') return {
        data: [
          { TABLE_NAME: 'dwd_t_case_records_all', TABLE_COMMENT: '案件全量表' },
          { TABLE_NAME: 'orders', TABLE_COMMENT: '订单' },
        ],
      };
      if (url.startsWith('/datasources/1/tables/dwd_t_case_records_all/columns')) return {
        data: [
          { COLUMN_NAME: 'salary', DATA_TYPE: 'decimal', COLUMN_COMMENT: '薪酬' },
          { COLUMN_NAME: 'phone', DATA_TYPE: 'varchar', COLUMN_COMMENT: '电话' },
        ],
      };
      if (url === '/admin/role-attr-keys') return { data: { keys: ['region'] } };
      return { data: [] };
    });
  };

  it('目标表/列名从清单搜索选择; 旧手填输入框退役; 未选数据源时禁用', async () => {
    mockCatalog();
    const user = userEvent.setup();
    render(<RLSManagement />);
    await user.click(await screen.findByRole('tab', { name: /列策略/ }));
    await user.click(screen.getByRole('button', { name: '新建列策略' }));
    // 手填 placeholder 已退役(截图反馈的"目标表名 Input"问题)
    expect(screen.queryByPlaceholderText('如: orders')).not.toBeInTheDocument();
    const tableBox = screen.getByRole('combobox', { name: '目标表' });
    const colBox = screen.getByRole('combobox', { name: '列名' });
    // 级联禁用: 未选前置资源不得跳到后面手填
    expect(tableBox).toBeDisabled();
    expect(colBox).toBeDisabled();
    await user.click(screen.getByRole('combobox', { name: '数据源' }));
    await user.click(await screen.findByRole('option', { name: /test-alb/ }));
    // 表: 搜索 "case" 命中并点选
    await user.click(tableBox);
    fireEvent.change(screen.getByPlaceholderText('输入关键词过滤…'), { target: { value: 'case' } });
    await user.click(await screen.findByRole('option', { name: /dwd_t_case_records_all/ }));
    expect(tableBox).toHaveTextContent('dwd_t_case_records_all');
    // 列: 打开即见类型/注释说明, 选 salary 加入黑名单
    await user.click(colBox);
    expect(await screen.findByText('decimal · 薪酬')).toBeInTheDocument();
    await user.click(screen.getByRole('option', { name: /salary/ }));
    await user.click(screen.getByRole('button', { name: '添加黑名单列' }));
    expect(screen.getByRole('button', { name: '移除 salary' })).toBeInTheDocument();
    // 列名选择框复位可继续添加
    expect(colBox).toHaveTextContent('搜索并选择列');
  });

  it('行策略"角色属性"过滤的属性名从角色属性清单选择, 不手填', async () => {
    mockCatalog();
    const user = userEvent.setup();
    render(<RLSManagement />);
    await user.click(await screen.findByRole('tab', { name: /行策略/ }));
    await user.click(screen.getByRole('button', { name: '新建行策略' }));
    await user.click(screen.getByRole('combobox', { name: '过滤类型' }));
    await user.click(await screen.findByRole('option', { name: '角色属性' }));
    const attrBox = screen.getByRole('combobox', { name: '属性名' });
    await user.click(attrBox);
    await user.click(await screen.findByRole('option', { name: 'region' }));
    expect(attrBox).toHaveTextContent('region');
  });
});

describe('数据安全策略 — 审计日志详情', () => {
  it('拒绝访问记录同样提供查看详情（含拒绝原因），用户显示 name 而非裸 ID', async () => {
    vi.mocked(client.get).mockImplementation(async (url: string) => {
      if (url === '/admin/rls-policies') return { data: { total: 0, items: [] } };
      if (url === '/users/') return { data: { items: [{ id: 7778154925, username: 'alice' }] } };
      if (url === '/admin/rls-audit-logs') return {
        data: {
          total: 1,
          items: [{
            id: 1, user_id: 7778154925, workspace_id: 0, policy_id: null,
            policy_name: 'permission_enforcer', table_name: '', action: 'deny',
            original_sql: 'select 1', filtered_sql: '', deny_reason: '无权访问该数据源',
            created_at: '2026-10-04T17:06:15',
          }],
        },
      };
      return { data: [] };
    });
    render(<RLSManagement />);
    // Radix 页签切换响应 mousedown，需完整指针事件序列激活
    const user = userEvent.setup();
    await user.click(await screen.findByRole('tab', { name: /审计日志/ }));
    // 拒绝记录不再因 filtered_sql 为空而丢掉详情入口
    const summary = await screen.findByText('查看详情');
    fireEvent.click(summary);
    expect(screen.getByText(/无权访问该数据源/)).toBeInTheDocument();
    expect(screen.getByText(/select 1/)).toBeInTheDocument();
    // 用户显示 name，不裸显 id
    expect(screen.getByText(/用户: alice/)).toBeInTheDocument();
  });
});
