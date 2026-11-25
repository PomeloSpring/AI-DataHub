import { describe, expect, it } from 'vitest';
import {
  buildLegend,
  computeSearchHits,
  computeVisibleIds,
  displayLabel,
  expandNeighbors,
  isHubType,
  pickFocusSeed,
  typeMeta,
  type LineageEdgeData,
  type LineageNodeData,
} from '../lineageView';

const nodes: LineageNodeData[] = [
  { id: 1, name: 't_case_records', node_id: 'prd-cn.t_case_records', node_type: 'table', datasource_name: 'prd-cn' },
  { id: 2, name: 'prd-cn.t_case_records', node_id: 'prd-cn.t_case_records', node_type: 'product' },
  { id: 3, name: '案例数量', node_id: '案例数量', node_type: 'metric' },
  { id: 4, name: '月度打开次数', node_id: 'chart:11', node_type: 'chart' },
  { id: 5, name: 'prd-cn.t_case_records.case_id', node_id: 'prd-cn.t_case_records.case_id', node_type: 'column' },
  { id: 6, name: '联耀案例域 · 月度趋势', node_id: '联耀案例域 · 月度趋势', node_type: 'dataset' },
];

const edges: LineageEdgeData[] = [
  { id: 1, source_id: 1, target_id: 2, edge_type: 'produces' },
  { id: 2, source_id: 2, target_id: 3, edge_type: 'defines' },
  { id: 3, source_id: 1, target_id: 6, edge_type: 'derive' },
  { id: 4, source_id: 6, target_id: 4, edge_type: 'visualizes' },
];

describe('displayLabel', () => {
  it('表用限定名（数据源.表），不用裸表名', () => {
    expect(displayLabel(nodes[0])).toBe('prd-cn.t_case_records');
  });

  it('图表展示业务名，不暴露内部 key', () => {
    expect(displayLabel(nodes[3])).toBe('月度打开次数');
    expect(displayLabel(nodes[3])).not.toContain('chart:');
  });

  it('资产层（产品/指标/数据集）用业务名', () => {
    expect(displayLabel(nodes[2])).toBe('案例数量');
    expect(displayLabel(nodes[5])).toBe('联耀案例域 · 月度趋势');
  });
});

describe('typeMeta', () => {
  it('已知类型映射到图谱基准色板 key 与中文名', () => {
    expect(typeMeta('ontology_object')).toEqual({ key: 'Object', label: '本体对象' });
    expect(typeMeta('chart').key).toBe('Chart');
  });

  it('未知类型回退到表口径，不抛错', () => {
    expect(typeMeta('weird_type')).toEqual({ key: 'Table', label: 'weird_type' });
  });
});

describe('computeSearchHits', () => {
  it('大小写不敏感，空关键词返回空集合', () => {
    expect(computeSearchHits(nodes, '').size).toBe(0);
    expect(computeSearchHits(nodes, '   ').size).toBe(0);
    expect(computeSearchHits(nodes, 'T_CASE_RECORDS')).toEqual(new Set([1, 2, 5]));
  });
});

describe('computeVisibleIds', () => {
  it('字段节点默认隐藏（列节点会把图打碎），开关打开后可见', () => {
    const hidden = computeVisibleIds({
      nodes, edges, hiddenTypes: new Set(), showColumns: false, searchHits: new Set(),
    });
    expect(hidden.has(5)).toBe(false);

    const shown = computeVisibleIds({
      nodes, edges, hiddenTypes: new Set(), showColumns: true, searchHits: new Set(),
    });
    expect(shown.has(5)).toBe(true);
  });

  it('图例点选隐藏的类型不再出现在可见集', () => {
    const ids = computeVisibleIds({
      nodes, edges, hiddenTypes: new Set(['chart']), showColumns: false, searchHits: new Set(),
    });
    expect(ids.has(4)).toBe(false);
    expect(ids.has(1)).toBe(true);
  });

  it('搜索命中节点保留直接上下游，无关节点被裁掉', () => {
    // 命中「案例数量」(3)：上游 2（定义它的产品），再上游 1 是 2 的上游但只保留 1 跳
    const hits = computeSearchHits(nodes, '案例数量');
    const ids = computeVisibleIds({
      nodes, edges, hiddenTypes: new Set(), showColumns: false, searchHits: hits,
    });
    expect(ids.has(3)).toBe(true);
    expect(ids.has(2)).toBe(true);   // 1 跳邻居（上游）
    expect(ids.has(1)).toBe(false);  // 2 跳不展开
    expect(ids.has(4)).toBe(false);  // 无关图表
  });

  it('搜索命中的节点若被类型筛选隐藏，结果里也不保留', () => {
    const hits = computeSearchHits(nodes, '月度打开次数');
    const ids = computeVisibleIds({
      nodes, edges, hiddenTypes: new Set(['chart']), showColumns: false, searchHits: hits,
    });
    expect(ids.has(4)).toBe(false);
    // 1 跳上游（数据集）不应因下游被隐藏而凭空出现
    expect(ids.size).toBe(0);
  });
});

describe('buildLegend', () => {
  it('按实际出现的类型计数并按数量倒序', () => {
    const legend = buildLegend(nodes);
    expect(legend[0].count).toBeGreaterThanOrEqual(legend[legend.length - 1].count);
    const chart = legend.find((i) => i.type === 'chart');
    expect(chart).toEqual({ type: 'chart', label: '图表', count: 1 });
    expect(legend.map((i) => i.type).sort()).toEqual(
      ['chart', 'column', 'dataset', 'metric', 'product', 'table'],
    );
  });
});

describe('isHubType', () => {
  it('主干（表/数据产品/本体对象）走 hub 常显名，明细走小圆点', () => {
    expect(['table', 'product', 'ontology_object'].every(isHubType)).toBe(true);
    expect(['metric', 'dimension', 'dataset', 'chart', 'column'].some(isHubType)).toBe(false);
  });
});

describe('expandNeighbors', () => {
  const all = new Set(nodes.map((n) => n.id));

  it('2 跳内上下游都在，超出的不展开', () => {
    // 1(表) -2(产品) -3(指标) 是一条链；1 到 3 是 2 跳
    const ids = expandNeighbors(all, edges, 1, 2);
    expect([...ids].sort()).toEqual([1, 2, 3, 4, 6]);
  });

  it('1 跳只留直接邻居（上下游都算）', () => {
    const ids = expandNeighbors(all, edges, 2, 1);
    expect([...ids].sort()).toEqual([1, 2, 3]);
  });

  it('结果始终限定在 baseIds 内（被筛选隐藏的节点不会被展开找回）', () => {
    const base = new Set([1, 2]);
    expect([...expandNeighbors(base, edges, 1, 2)].sort()).toEqual([1, 2]);
  });

  it('root 不在可见集时原样返回，不制造空图', () => {
    const base = new Set([6, 4]);
    expect([...expandNeighbors(base, edges, 1, 2)].sort()).toEqual([4, 6]);
  });
});

describe('pickFocusSeed', () => {
  it('取连接最多的表（度数优先，tie 按名称，结果确定）', () => {
    // 表节点只有 1；若没有表则退化为全节点度数最高
    expect(pickFocusSeed(nodes, edges)).toBe(1);
    const noTable = nodes.filter((n) => n.node_type !== 'table');
    // 去掉表后：边 2-3、6-4，度数 1 最高的一批按名称/ id 确定
    const seed = pickFocusSeed(noTable, edges);
    expect(seed).not.toBeNull();
    expect(noTable.some((n) => n.id === seed)).toBe(true);
  });

  it('空图返回 null', () => {
    expect(pickFocusSeed([], [])).toBeNull();
  });

  it('扇出过大的表不当种子（2 跳超上限就选较小的那条链）', () => {
    // 表 1 挂 200 张图表（2 跳 201 节点 > 上限）；表 10 只连一个产品
    const wide: LineageNodeData[] = [
      { id: 1, name: 'wide', node_id: 'src.wide', node_type: 'table' },
      { id: 10, name: 'small', node_id: 'src.small', node_type: 'table' },
      { id: 11, name: 'small产品', node_id: 'small产品', node_type: 'product' },
      ...Array.from({ length: 200 }, (_, i) => ({
        id: 100 + i,
        name: `图${i}`,
        node_id: `chart:${i}`,
        node_type: 'chart' as const,
      })),
    ];
    const wideEdges: LineageEdgeData[] = [
      { id: 1, source_id: 10, target_id: 11, edge_type: 'produces' },
      ...Array.from({ length: 200 }, (_, i) => ({
        id: 1000 + i,
        source_id: 1,
        target_id: 100 + i,
        edge_type: 'derive',
      })),
    ];
    expect(pickFocusSeed(wide, wideEdges)).toBe(10);
  });
});