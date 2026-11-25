/**
 * 数据集标签 + 口径来源筛选 + 查看取数 SQL（2026-11-24 新增能力）。
 *
 * 锁定五件事：
 *  1. 标签是自由文本分类（业务域/物化目的），录入控件去重、可移除，上限与后端同源；
 *  2. 清单页「按标签筛」「按取数方式筛」「按来源筛」必须把值下推给服务端
 *     （请求带 `tags`/`source_type`/`origin`），不在前端全量 filter；选项由服务端枚举下发，
 *     前端不硬编码值域；
 *  3. 语义型数据集此前看不到七闸门实际编译的 SQL，「查看 SQL」只编译不执行（POST explain-sql），
 *     同时展示 base_sql 与按当前身份治理改写后的 secured_sql；
 *  4. 闸门拒绝时显式告知拒在哪一步/为什么（不拿空 SQL 冒充“没配置”）；
 *     且「查看 SQL」只对有编辑权（dataset:manage ∧ 属主）的人出现（护栏 §7 例外）；
 *  5. 筛选项拉取失败必须显式提示 + 可重试（空选项不得冒充“库里没有标签/来源”）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import client from '@/api/client';
import { PreviewPanel } from '../DatasetDetail';
import TagsEditor, { normalizeTagList } from '../TagsEditor';
import Datasets from '../Datasets';
import { useVisLibrary } from '@/hooks/useVisLibrary';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: vi.fn() }));
vi.mock('@/stores/permissionStore', () => ({ hasPerm: () => true }));
// 图表渲染自带 G2 依赖，本组用例只验取数契约；具名导出保留真值
vi.mock('@/components/DashboardChart', async (importOriginal) => ({
  ...(await importOriginal() as object),
  default: () => null,
}));

const FIELDS = [
  { field: 'ym', role: 'dimension', label: '月份' },
  { field: 'opens', role: 'measure', label: '打开次数' },
  { field: 'users', role: 'measure', label: '触达用户' },
] as any;

const SEMANTIC_DS = {
  id: 41, name: '版本统计·App 版本分布', source_type: 'semantic',
  datasource_id: 5, is_es: false, chart_type: 'bar', chart_preset: {},
  usable_now: ['prd-cn'], available_source_names: ['prd-cn'], object_key: 'app_version',
};

const EXPLAIN_OK = {
  dataset: { id: 41, name: SEMANTIC_DS.name, source_type: 'semantic' },
  datasource: 'prd-cn',
  execution_path: 'semantic',
  sql: 'SELECT ym, SUM(opens) AS opens FROM app_version_t GROUP BY ym',
  secured_sql: "SELECT ym, SUM(opens) AS opens FROM app_version_t WHERE site = 'cn' GROUP BY ym",
  allowed: true, blocked_at: '', reason: '', needs_approval: false,
  applied_rls: ['site=cn'], masked_columns: ['user_phone'], scopes_applied: 2, warnings: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useVisLibrary).mockReturnValue({
    items: [], loading: false, error: '', loaded: true, refresh: vi.fn(),
  } as any);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('标签录入控件', () => {
  it('读路径归一：JSON 串/数组/单串都能还原成去重列表', () => {
    expect(normalizeTagList('["售后域","大屏物化"]')).toEqual(['售后域', '大屏物化']);
    expect(normalizeTagList(['售后域', '售后域', ' 大屏物化 '])).toEqual(['售后域', '大屏物化']);
    expect(normalizeTagList('售后域')).toEqual(['售后域']);
    expect(normalizeTagList(null)).toEqual([]);
  });

  it('回车确认新增、点 X 移除；重复标签不重复入列', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <TagsEditor value={[]} onChange={onChange} suggestions={['售后域']} />,
    );
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: '售后域' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenLastCalledWith(['售后域']);

    // 受控值回填后，再输同一个不产生第二项
    rerender(<TagsEditor value={['售后域']} onChange={onChange} suggestions={[]} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '售后域' } });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1];
    expect(lastCall?.[0]).toEqual(['售后域']);

    fireEvent.click(screen.getByRole('button', { name: '移除标签 售后域' }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it('只读态（无编辑权）不渲染输入框与移除按钮', () => {
    render(<TagsEditor value={['售后域']} onChange={() => {}} disabled />);
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByRole('button', { name: '移除标签 售后域' })).toBeNull();
  });
});

describe('清单页 — 按标签筛 / 按来源筛', () => {
  const ROW = {
    id: 7, name: '版本统计集', description: '', source_type: 'sql', object_key: '',
    datasource_id: 11, datasource_name: 'prd-cn',
    available_source_names: ['prd-cn'], usable_now: ['prd-cn'],
    chart_type: '', owner_id: 1, field_count: 2, references: 0, updated_at: '',
    tags: ['售后域', '大屏物化'], origin: 'chart_backfill', origin_label: '看板收编',
  };

  const ORIGINS = [
    { value: 'manual', label: '人工定义', count: 3 },
    { value: 'chart_backfill', label: '看板收编', count: 1 },
    { value: 'ai_session', label: 'AI 会话产物', count: 0 },
  ];

  const SOURCE_TYPES = [
    { value: 'sql', label: 'SQL', count: 1 },
    { value: 'semantic', label: '语义对象', count: 4 },
    { value: 'both', label: '语义 + SQL', count: 0 },
  ];

  beforeEach(() => {
    (client.get as any).mockImplementation(async (url: string) => {
      if (url === '/datasets/') return { data: { items: [ROW], total: 1, page: 1, page_size: 20 } };
      if (url === '/datasets/tags') return { data: { items: [{ name: '售后域', count: 2 }] } };
      if (url === '/datasets/source-types') return { data: { items: SOURCE_TYPES } };
      if (url === '/datasets/origins') return { data: { items: ORIGINS } };
      if (url === '/datasources/') return { data: [] };
      throw new Error(`unexpected GET ${url}`);
    });
  });

  it('选标签后带 `tags` 重新请求（服务端下推，不在前端切片）', async () => {
    render(<MemoryRouter><Datasets /></MemoryRouter>);
    await screen.findByText('版本统计集');
    // 卡片上直接显示标签（≤3 个全显）
    expect(screen.getAllByText('售后域').length).toBeGreaterThan(0);

    (client.get as any).mockClear();
    await userEvent.click(screen.getByRole('combobox', { name: '按标签筛选' }));
    await userEvent.click(await screen.findByRole('option', { name: '售后域（2）' }));

    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/datasets/', {
      params: expect.objectContaining({ tags: '售后域' }),
    }));
  });

  it('选来源后带 `origin` 重新请求；选项值域来自服务端枚举（含计数）', async () => {
    render(<MemoryRouter><Datasets /></MemoryRouter>);
    await screen.findByText('版本统计集');
    // 行上可见口径来源（筛完能用它核验结果）
    expect(screen.getByText('看板收编')).toBeInTheDocument();

    (client.get as any).mockClear();
    await userEvent.click(screen.getByRole('combobox', { name: '按来源筛选' }));
    await userEvent.click(await screen.findByRole('option', { name: '人工定义（3）' }));

    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/datasets/', {
      params: expect.objectContaining({ origin: 'manual' }),
    }));
  });

  it('选取数方式后带 `source_type` 重新请求；选项值域来自服务端枚举', async () => {
    render(<MemoryRouter><Datasets /></MemoryRouter>);
    await screen.findByText('版本统计集');

    (client.get as any).mockClear();
    await userEvent.click(screen.getByRole('combobox', { name: '按取数类型筛选' }));
    await userEvent.click(await screen.findByRole('option', { name: '语义对象（4）' }));

    await waitFor(() => expect(client.get).toHaveBeenCalledWith('/datasets/', {
      params: expect.objectContaining({ source_type: 'semantic' }),
    }));
  });

  it('筛选项拉取失败：显式提示 + 可重试（不得静默退成“库里没标签/没来源”）', async () => {
    (client.get as any).mockImplementation(async (url: string) => {
      if (url === '/datasets/') return { data: { items: [ROW], total: 1, page: 1, page_size: 20 } };
      if (url === '/datasets/tags') throw new Error('boom');
      if (url === '/datasets/source-types') throw new Error('boom');
      if (url === '/datasets/origins') throw new Error('boom');
      if (url === '/datasources/') return { data: [] };
      throw new Error(`unexpected GET ${url}`);
    });
    render(<MemoryRouter><Datasets /></MemoryRouter>);
    await screen.findByText('版本统计集');
    expect(await screen.findByText('筛选项加载失败，标签/类型/来源筛选暂不可用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });
});

describe('详情页 — 查看本次取数 SQL', () => {
  it('无编辑权不出现「查看 SQL」（护栏 §7 例外只对编辑者开放）', () => {
    render(<PreviewPanel datasetId={41} fields={FIELDS} ds={SEMANTIC_DS} />);
    expect(screen.queryByRole('button', { name: /查看 SQL/ })).toBeNull();
  });

  it('语义型：调 explain-sql 并把 base/secured SQL 与治理信息都摆出来', async () => {
    (client.post as any).mockResolvedValue({ data: EXPLAIN_OK });
    render(<PreviewPanel datasetId={41} fields={FIELDS} ds={SEMANTIC_DS} canExplain />);

    await userEvent.click(screen.getByRole('button', { name: /查看 SQL/ }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/datasets/41/explain-sql', {
      dimensions: ['ym'], measures: ['opens', 'users'], limit: 100, datasource: '',
    }));
    // base 与 secured 各一块（secured 多出 WHERE），两段都得真出现
    expect(await screen.findAllByText(/GROUP BY ym/)).toHaveLength(2);
    expect(screen.getByText(/WHERE site = 'cn'/)).toBeInTheDocument();
    expect(screen.getByText('语义取数')).toBeInTheDocument();
    expect(screen.getByText('prd-cn')).toBeInTheDocument();
    expect(screen.getByText(/site=cn/)).toBeInTheDocument();
    expect(screen.getByText(/user_phone/)).toBeInTheDocument();
  });

  it('闸门拒绝：显式告知拒在哪一步与原因，仍能看到编译产物', async () => {
    (client.post as any).mockResolvedValue({
      data: {
        ...EXPLAIN_OK, secured_sql: '', allowed: false,
        blocked_at: 'permission', reason: '当前身份无该对象取数权限',
      },
    });
    render(<PreviewPanel datasetId={41} fields={FIELDS} ds={SEMANTIC_DS} canExplain />);
    await userEvent.click(screen.getByRole('button', { name: /查看 SQL/ }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('permission');
    expect(alert.textContent).toContain('当前身份无该对象取数权限');
    expect(screen.getAllByText(/GROUP BY ym/).length).toBeGreaterThan(0);
  });
});