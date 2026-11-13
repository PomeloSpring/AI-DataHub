/** ES 执行通道的预览契约（2026-11-13 现网缺陷收尾 + 内置通道退役）。
 *
 * 现象：同一份数据在看板上出得来，在数据集详情页「预览取数」必失败 ——
 * 前端拿 `source_type === 'es'`（内置通道标记）判 ES，而 ES 早已数据源化
 * （`source_type='sql'` + ES 型数据源），该标记恒为 false，于是预览照旧把
 * 维度/度量当"运行时二次聚合"发出去，被后端 ES 护栏显式拒。
 *
 * 锁四件事：
 *  1. 请求体形状：ES 执行通道只发 `{limit, datasource}`，不得带 dimensions/measures；
 *     选源入参与其他通道**同一条路**（服务端 resolve_effective_source 解析），
 *     ES 不因通道而少一个能力；
 *  2. 不开空承诺：ES 集不渲染维度/度量选择控件（传了也不生效），但多源时必须给出选源框；
 *  3. 治理标注从服务端 `governance` 读，且不再有"内置通道/未施加"这一支
 *     ——内置通道已退役，那句陈述现在恒假；
 *  4. 非 ES（MySQL 源）行为零变化：仍发 dimensions/measures。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import client from '@/api/client';
import { PreviewPanel } from '../DatasetDetail';
import { useVisLibrary } from '@/hooks/useVisLibrary';

vi.mock('@/api/client', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn() } }));
vi.mock('@/hooks/useVisLibrary', () => ({ useVisLibrary: vi.fn() }));
// 图表渲染不是本用例要验的东西（它自带 G2 依赖），只把默认导出换 null；
// CHART_TYPES 等具名导出必须保留真值（DatasetDetail 模块级就消费它们）
vi.mock('@/components/DashboardChart', async (importOriginal) => ({
  ...(await importOriginal() as object),
  default: () => null,
}));

const FIELDS = [
  { field: 'ym', role: 'dimension', label: '月份' },
  { field: 'opens', role: 'measure', label: '打开次数' },
  { field: 'users', role: 'measure', label: '触达用户' },
] as any;

/** 阶段二/三终态：ES 数据集 = source_type 'sql' + ES 型数据源 + 服务端下发的 is_es */
const ES_ON_DATASOURCE = {
  id: 82, name: 'App 使用·单 App 月度趋势', source_type: 'sql',
  datasource_id: 1794535612029, is_es: true, chart_type: 'line', chart_preset: {},
  usable_now: ['cloud-platform-es'], available_source_names: ['cloud-platform-es'],
};

/** 多站 ES：可用范围里有两个 ES 数据源 → 必须能选源（与 SQL 通道同一形状） */
const ES_MULTI_SOURCE = {
  ...ES_ON_DATASOURCE,
  usable_now: ['cloud-platform-es', 'cloud-platform-es-eu'],
  available_source_names: ['cloud-platform-es', 'cloud-platform-es-eu'],
};

const MYSQL_ROW = {
  id: 41, name: '版本统计·App 版本分布', source_type: 'sql',
  datasource_id: 5, is_es: false, chart_type: 'bar', chart_preset: {},
  usable_now: ['prd-cn'], available_source_names: ['prd-cn'],
};

function renderPanel(ds: any) {
  return render(<PreviewPanel datasetId={ds.id} fields={FIELDS} ds={ds} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useVisLibrary).mockReturnValue({ items: [], loading: false, error: '', loaded: true, refresh: vi.fn() } as any);
  (client.post as any).mockResolvedValue({ data: { columns: ['ym'], rows: [{ ym: '2026-11' }], row_count: 1 } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('数据集预览 — ES 执行通道', () => {
  it('ES 数据源型：请求体带 limit + datasource，不带 dimensions/measures', async () => {
    renderPanel(ES_ON_DATASOURCE);
    fireEvent.click(screen.getByRole('button', { name: /预览取数/ }));
    await waitFor(() => expect(client.post).toHaveBeenCalled());
    const [url, body] = vi.mocked(client.post).mock.calls[0] as any[];
    expect(url).toBe('/datasets/82/preview');
    expect(body.dimensions).toBeUndefined();
    expect(body.measures).toBeUndefined();
    expect(body.datasource).toBe('');                 // 单源：交服务端定源
    expect(body.limit).toBe(100);
  });

  it('ES 多源：渲染选源框并把业务名递交出去（选源不是 SQL 通道独享的能力）', async () => {
    renderPanel(ES_MULTI_SOURCE);
    const trigger = await screen.findByRole('combobox', { name: /执行数据源/ });
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole('option', { name: 'cloud-platform-es-eu' }));
    fireEvent.click(screen.getByRole('button', { name: /预览取数/ }));
    await waitFor(() => expect(client.post).toHaveBeenCalled());
    const [, body] = vi.mocked(client.post).mock.calls[0] as any[];
    expect(body.datasource).toBe('cloud-platform-es-eu');
    expect(body.dimensions).toBeUndefined();
  });

  it('ES 数据源型：不渲染维度/度量选择控件（传了不生效就是空承诺）', () => {
    renderPanel(ES_ON_DATASOURCE);
    expect(screen.queryByText('维度')).not.toBeInTheDocument();
    expect(screen.queryByText('度量')).not.toBeInTheDocument();
    // 但执行通道本身要说清：正文直跑 + 治理已施加
    expect(screen.getByText(/ES 执行通道直接跑已保存正文/)).toBeInTheDocument();
    expect(screen.getByText(/列级脱敏\/屏蔽与行级策略已编译进待执行 SQL/)).toBeInTheDocument();
  });

  it('治理标注从服务端读：只有"已施加"这一支，内置通道说法不得回到页面', async () => {
    (client.post as any).mockResolvedValue({
      data: {
        columns: ['ym'], rows: [{ ym: '2026-11' }], row_count: 1, execution_mode: 'es',
        es_indexes: ['t_software_app_operation_log'],
        governance: { channel: 'elasticsearch_datasource', note: '列级/行级已编译进 SQL' },
      },
    });
    renderPanel(ES_ON_DATASOURCE);
    fireEvent.click(screen.getByRole('button', { name: /预览取数/ }));
    await waitFor(() => expect(screen.getByText(/已施加列级\/行级策略/)).toBeInTheDocument());
    expect(screen.queryByText(/未施加列级\/行级策略/)).not.toBeInTheDocument();
    expect(screen.queryByText(/内置/)).not.toBeInTheDocument();
    expect(screen.getByText(/命中索引: t_software_app_operation_log/)).toBeInTheDocument();
  });

  it('非 ES（MySQL 源）行为零变化：仍按维度/度量取数', async () => {
    renderPanel(MYSQL_ROW);
    expect(screen.getByText('维度')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /预览取数/ }));
    await waitFor(() => expect(client.post).toHaveBeenCalled());
    const [, body] = vi.mocked(client.post).mock.calls[0] as any[];
    expect(body.dimensions).toEqual(['ym']);
    expect(body.measures).toEqual(['opens', 'users']);
  });
});
