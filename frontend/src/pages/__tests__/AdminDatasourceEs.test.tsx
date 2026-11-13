/** ES 型数据源在数据源管理页的录入与显示（阶段一：ES 数据源化）。

锁定五件事：
1. ES 源的编辑表单以**别名清单**录入开放范围（chips 可增删），且不出现「数据库名称」
   （ES 没有库这一层，"库"由索引别名承担）；
2. 提交时组装成 `extra_config`，`es_*` 辅助字段不得发给后端
   （ui-resource-display §2：不让管理员手拼 JSON、也不往后端塞无用字段）；
3. 开放范围为空时前端即拒且不发请求（与后端 `_validated_es_extra` 同口径，fail-closed）；
4. 卡片次要行显示「N 个索引别名（如 名字）」，不拿 `-` 充数；存量类型（mysql）行为零变化；
5. **高级配置按类型呈现且真实往返**（超时/最大行数/连接池 = ES；连接超时/查询超时 = SQL 型）：
   只给自己的旋钮摆入口（给不相干类型摆一个没消费方的输入框 = 空承诺），
   并且“清空回到默认”必须是可表达的（两项都留空 → 传 null 而不是不提交）。
*/
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DatasourceTab } from '../Admin';
import client from '@/api/client';
import { toast } from '@/lib/toast';

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const ES_DS_ID = 1794535612029;

const esDatasource = (over: Record<string, any> = {}) => ({
  id: ES_DS_ID,
  name: 'cloud-platform-es',
  db_type: 'es',
  host: 'https://es.example.internal',
  port: 9200,
  username: 'elastic',
  database_name: '',
  site_code: '',
  is_default: 0,
  ssl: 0,
  extra_config: {
    allowed_aliases: ['t_software_app_operation_log', 'sys_thrid_request_log'],
    allowed_index_patterns: ['t-t_equipment_operation_log*'],
    timeout_sec: 20,
  },
  ...over,
});

async function renderTab(rows: any[] = [esDatasource()]) {
  vi.mocked(client.get).mockImplementation(async (url: any) => {
    if (url === '/datasources') return { data: rows };
    return { data: [] };
  });
  render(<DatasourceTab />);
  await waitFor(() => expect(screen.getByText(rows[0].name)).toBeInTheDocument());
}

/** 打开某条数据源的编辑弹窗（图标按钮有 aria-label，不靠 DOM 顺序定位） */
async function openEdit(name: string) {
  fireEvent.click(screen.getByRole('button', { name: `编辑数据源 ${name}` }));
  expect(await screen.findByText('编辑数据源')).toBeInTheDocument();
}

const removeChip = (value: string) =>
  fireEvent.click(screen.getByLabelText(`移除 ${value}`));

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('ES 型数据源表单', () => {
  it('卡片用别名数量替代库名（不显示裸 -）', async () => {
    await renderTab();
    expect(screen.getByText(/2 个索引别名（如 t_software_app_operation_log）/)).toBeInTheDocument();
  });

  it('编辑 ES 源：出现别名清单与策略支持说明，且不出现数据库名称输入', async () => {
    await renderTab();
    await openEdit('cloud-platform-es');
    expect(screen.getByText('可查索引别名 *')).toBeInTheDocument();
    expect(screen.getByText('t_software_app_operation_log')).toBeInTheDocument();
    expect(screen.getByText('t-t_equipment_operation_log*')).toBeInTheDocument();
    expect(screen.getByText(/Elasticsearch 源支持行级\/列级策略/)).toBeInTheDocument();
    // ES 没有库名这一层：该输入必须不在表单里
    expect(screen.queryByText('数据库名称')).not.toBeInTheDocument();
  });

  it('提交把别名/模式组装成 extra_config，且不把 es_* 辅助字段发给后端', async () => {
    await renderTab();
    await openEdit('cloud-platform-es');

    const aliasInput = screen.getByPlaceholderText('如：t_software_app_operation_log');
    fireEvent.change(aliasInput, { target: { value: 't_equipment_warning_log' } });
    fireEvent.keyDown(aliasInput, { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('t_equipment_warning_log')).toBeInTheDocument());

    vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());

    const [url, payload] = vi.mocked(client.put).mock.calls[0] as any[];
    expect(url).toBe(`/datasources/${ES_DS_ID}`);
    expect(payload.db_type).toBe('es');
    expect(payload.es_aliases).toBeUndefined();
    expect(payload.es_patterns).toBeUndefined();
    expect(payload.es_timeout_sec).toBeUndefined();
    const extra = JSON.parse(payload.extra_config);
    expect([...extra.allowed_aliases].sort()).toEqual(
      ['sys_thrid_request_log', 't_equipment_warning_log', 't_software_app_operation_log']);
    expect(extra.allowed_index_patterns).toEqual(['t-t_equipment_operation_log*']);
    expect(extra.timeout_sec).toBe(20);
  });

  it('开放范围清空时前端直接拒保存（不发请求）', async () => {
    await renderTab();
    await openEdit('cloud-platform-es');

    removeChip('t_software_app_operation_log');
    removeChip('sys_thrid_request_log');
    removeChip('t-t_equipment_operation_log*');

    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining('请至少登记一个可查索引别名')));
    expect(client.put).not.toHaveBeenCalled();
  });

  it('非 ES 源仍是数据库名称输入（本次改动不影响存量类型）', async () => {
    const mysql = esDatasource({
      id: 1, name: 'prd-cn', db_type: 'mysql', database_name: 'adh', extra_config: null,
    });
    await renderTab([mysql]);
    await openEdit('prd-cn');
    expect(screen.getByText('数据库名称')).toBeInTheDocument();
    expect(screen.queryByText('可查索引别名 *')).not.toBeInTheDocument();

    vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    const [, payload] = vi.mocked(client.put).mock.calls[0] as any[];
    // 没填任何高级项 = 传 null（清空该列回代码缺省），而不是不提交这个字段
    expect(payload.extra_config).toBeNull();
    expect(payload.sql_connect_timeout_sec).toBeUndefined();
  });

  it('ES 源：连接池数量可录入并随 extra_config 往返', async () => {
    await renderTab([esDatasource({
      extra_config: { allowed_aliases: ['a_log'], timeout_sec: 30, max_rows: 1000, pool_size: 24 },
    })]);
    await openEdit('cloud-platform-es');

    const pool = screen.getByPlaceholderText('留空用默认 10');
    expect(pool).toHaveValue(24);                       // 已配的值必须回填，不能只存不显
    expect(screen.getByText('查询超时（秒）')).toBeInTheDocument();
    expect(screen.getByText('单次最大行数')).toBeInTheDocument();
    // 各类型只给自己的旋钮摆入口：ES 源不应看到 SQL 型的连接超时/查询超时成对输入
    expect(screen.queryByPlaceholderText('留空用默认 30')).not.toBeInTheDocument();

    fireEvent.change(pool, { target: { value: '8' } });
    vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    const [, payload] = vi.mocked(client.put).mock.calls[0] as any[];
    const extra = JSON.parse(payload.extra_config);
    expect(extra.pool_size).toBe(8);
    expect(extra.timeout_sec).toBe(30);
    expect(extra.max_rows).toBe(1000);
  });

  it('SQL 型：两个超时可录入，清空即传 null（配上去改得回来）', async () => {
    const mysql = esDatasource({
      id: 1, name: 'prd-cn', db_type: 'mysql', database_name: 'adh',
      extra_config: { connect_timeout_sec: 5, read_timeout_sec: 120 },
    });
    await renderTab([mysql]);
    await openEdit('prd-cn');

    expect(screen.getByText('连接超时（秒）')).toBeInTheDocument();
    expect(screen.getByText('查询超时（秒）')).toBeInTheDocument();
    expect(screen.queryByText('连接池数量')).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('留空用默认 10')).toHaveValue(5);
    expect(screen.getByPlaceholderText('留空用默认 30')).toHaveValue(120);

    vi.mocked(client.put).mockResolvedValue({ data: { success: true } } as any);
    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(client.put).toHaveBeenCalled());
    let extra = JSON.parse((vi.mocked(client.put).mock.calls[0][1] as any).extra_config);
    expect(extra).toEqual({ connect_timeout_sec: 5, read_timeout_sec: 120 });

    // 清空两项 → 传 null（只按 `is not None` 判会跳过该列，等于预算只能加不能减）
    await openEdit('prd-cn');            // 保存成功会关窗（这是对的），重新打开再改
    fireEvent.change(screen.getByPlaceholderText('留空用默认 10'), { target: { value: '' } });
    fireEvent.change(screen.getByPlaceholderText('留空用默认 30'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(vi.mocked(client.put).mock.calls.length).toBe(2));
    expect(vi.mocked(client.put).mock.calls[1][1]).toHaveProperty('extra_config', null);
  });
});
