/**
 * 数据集清单检索/过滤口径（组件库「数据集」页 + 图表配置面板共用）。
 *
 * 锁定：
 *  - 检索是多词 AND 命中，且命中范围含 名称/描述/业务对象/字段业务名/源名/索引模式；
 *  - 数据源筛选语义 = 「该数据集能在所选源上执行」（读 usable_now，不是执行源），
 *    ES 通道不属于任何数据源 → 选了源就不命中（与后端 datasource_id 筛选同口径）；
 *  - 筛选项只从清单里出现过的值生成，按业务名排序（ui-resource-display §1/§2）；
 *  - 本模块不得长第二套权限判断：它只吃已授权清单（no-silent-degradation）。
 */
import { describe, expect, it } from 'vitest';
import {
  DATASET_KIND_LABELS, EMPTY_DATASET_FILTER, ES_SOURCE_VALUE, datasetKindOf, datasetKindOptions, datasetPreviewPayload,
  datasetSearchText, datasetSourceOptions, filterDatasets, isDatasetFilterActive,
} from '@/lib/datasetPickerFilter';

const sqlDs = {
  id: 8, name: 'ARMS·用户与会话趋势', description: '按日统计 UV/PV', source_type: 'sql',
  datasource_name: 'arms-prd-cn', available_source_names: ['arms-prd-cn', 'arms-prd-jp'],
  usable_now: ['arms-prd-cn'], field_count: 5,
  field_config: JSON.stringify([{ field: 'stat_date', label: '统计日期' }, { field: 'uv', label: '独立访客' }]),
};
const semanticDs = {
  id: 2, name: '案件分析数据集', source_type: 'semantic', object_key: 'case',
  datasource_name: 'prd-cn', available_source_names: ['prd-cn'], usable_now: ['prd-cn'],
  field_config: [{ label: '案件类型' }],
};
const esDs = {
  id: 40, name: '网关访问日志', source_type: 'es', es_pattern: 'nginx-access-*',
  datasource_name: 'ES 检索通道', available_source_names: ['nginx-access-*'], usable_now: ['nginx-access-*'],
};
const noSourceDs = {
  id: 9, name: '待授权数据集', source_type: 'sql', datasource_name: 'prd-eu',
  available_source_names: ['prd-eu'], usable_now: [],
};
const ALL = [sqlDs, semanticDs, esDs, noSourceDs];
const f = (over: Partial<any>) => ({ ...EMPTY_DATASET_FILTER, ...over });

describe('datasetKindOf / 搜索文本', () => {
  it('通道类型判别与后端同口径：source_type=es 或登记了索引模式都算 ES', () => {
    expect(datasetKindOf(sqlDs)).toBe('sql');
    expect(datasetKindOf(semanticDs)).toBe('semantic');
    expect(datasetKindOf(esDs)).toBe('es');
    expect(datasetKindOf({ source_type: 'sql', es_pattern: 'x-*' })).toBe('es');
    expect(datasetKindOf({})).toBe('sql');            // 缺字段不炸，退到 SQL
  });

  it('搜索文本含字段业务名与源名，field_config 字符串/数组两种形态都吃', () => {
    expect(datasetSearchText(sqlDs)).toContain('独立访客');
    expect(datasetSearchText(semanticDs)).toContain('案件类型');
    expect(datasetSearchText(esDs)).toContain('nginx-access-*');
    expect(datasetSearchText({ field_config: 'not-json' })).toBe('');   // 脏配置不污染命中
  });

  it('类型给业务名，不把枚举裸露给用户', () => {
    expect(DATASET_KIND_LABELS.es).toBe('日志/ES 数据集');
    expect(Object.values(DATASET_KIND_LABELS).every(v => !/^(sql|es)$/i.test(v))).toBe(true);
  });
});

describe('datasetPreviewPayload（预览只给行数上限）', () => {
  it('不传 dimensions/measures/datasource：字段投影会编译出非法 SQL，生效源由服务端定', () => {
    const payload = datasetPreviewPayload(20);
    expect(payload).toEqual({ limit: 20 });
    expect(JSON.stringify(payload)).not.toMatch(/dimension|measure|datasource|site/i);
  });

  it('各通道类型同一份预览口径（不按类型分叉，不拿图表列名猜字段）', () => {
    [sqlDs, semanticDs, esDs].forEach(row => {
      expect(datasetSearchText(row).length).toBeGreaterThan(0);   // 清单行形状合法
      expect(datasetPreviewPayload(5)).toEqual({ limit: 5 });
    });
  });
});

describe('filterDatasets', () => {
  it('关键词：空白分词按 AND 命中，大小写不敏感', () => {
    expect(filterDatasets(ALL, f({ keyword: '会话' })).map(d => d.id)).toEqual([8]);
    expect(filterDatasets(ALL, f({ keyword: 'arms 独立访客' })).map(d => d.id)).toEqual([8]);
    expect(filterDatasets(ALL, f({ keyword: 'arms 不存在' }))).toEqual([]);
    expect(filterDatasets(ALL, f({ keyword: ' UV ' })).map(d => d.id)).toEqual([8]);
  });

  it('数据源筛选 = 能在该源上执行（usable_now），执行源不算', () => {
    // semanticDs 执行源 prd-cn 且在 usable_now 内 → 命中；sqlDs 执行源是 arms-prd-cn
    expect(filterDatasets(ALL, f({ source: 'prd-cn' })).map(d => d.id)).toEqual([2]);
    expect(filterDatasets(ALL, f({ source: 'arms-prd-cn' })).map(d => d.id)).toEqual([8]);
    // prd-eu 只是待授权集的执行源、不在 usable_now 内 → 选它不命中任何行
    expect(filterDatasets(ALL, f({ source: 'prd-eu' }))).toEqual([]);
  });

  it('选 ES 通道 = 只看 ES 型数据集（ES 不挂在数据源上，拿源名永远筛不到它）', () => {
    expect(filterDatasets(ALL, f({ source: ES_SOURCE_VALUE })).map(d => d.id)).toEqual([40]);
    // 与类型筛选项互不矛盾：ES 通道 + sql 类型 = 无命中（不静默退成其中一项）
    expect(filterDatasets(ALL, f({ source: ES_SOURCE_VALUE, kind: 'sql' }))).toEqual([]);
  });

  it('数据源筛选项：清单里有 ES 行时补上 ES 通道一项（排最后），没有就不凭空出现', () => {
    expect(datasetSourceOptions(ALL)).toEqual(['arms-prd-cn', 'prd-cn', ES_SOURCE_VALUE]);
    expect(datasetSourceOptions([sqlDs, semanticDs])).toEqual(['arms-prd-cn', 'prd-cn']);
  });

  it('选了数据源时 ES 通道不命中（选了具体源就按能不能在该源上执行判）', () => {
    expect(filterDatasets(ALL, f({ source: 'arms-prd-cn' })).some(d => d.id === 40)).toBe(false);
    expect(filterDatasets(ALL, f({ kind: 'es' })).map(d => d.id)).toEqual([40]);
  });

  it('仅看可引入：无可用源的行被筛掉（挂上必报错的不给入口）', () => {
    expect(filterDatasets(ALL, f({ onlyUsable: true })).map(d => d.id)).toEqual([8, 2, 40]);
    expect(filterDatasets(ALL, f({}))).toHaveLength(4);
  });

  it('空条件原样返回清单（不重排、不裁剪）', () => {
    expect(filterDatasets(ALL, EMPTY_DATASET_FILTER)).toEqual(ALL);
    expect(filterDatasets([], EMPTY_DATASET_FILTER)).toEqual([]);
    expect(filterDatasets(undefined as any, EMPTY_DATASET_FILTER)).toEqual([]);
  });

  it('关键词 + 类型可叠加', () => {
    expect(filterDatasets(ALL, f({ keyword: '日志', kind: 'es' })).map(d => d.id)).toEqual([40]);
    expect(filterDatasets(ALL, f({ keyword: '日志', kind: 'sql' }))).toEqual([]);
  });
});

describe('筛选项与激活态', () => {
  it('源选项只取 usable_now、排除 ES 通道名、按业务名排序', () => {
    expect(datasetSourceOptions([sqlDs, semanticDs])).toEqual(['arms-prd-cn', 'prd-cn']);
    expect(datasetSourceOptions([sqlDs, sqlDs])).toEqual(['arms-prd-cn']);   // 去重
    expect(datasetSourceOptions([])).toEqual([]);
  });

  it('类型选项只列清单里出现的，顺序固定 sql→semantic→es', () => {
    expect(datasetKindOptions(ALL)).toEqual(['sql', 'semantic', 'es']);
    expect(datasetKindOptions([sqlDs, esDs])).toEqual(['sql', 'es']);
  });

  it('激活态判定：只有全空条件才算未激活', () => {
    expect(isDatasetFilterActive(EMPTY_DATASET_FILTER)).toBe(false);
    expect(isDatasetFilterActive(f({ keyword: '   ' }))).toBe(false);   // 纯空白不算条件
    expect(isDatasetFilterActive(f({ onlyUsable: true }))).toBe(true);
    expect(isDatasetFilterActive(f({ source: 'prd-cn' }))).toBe(true);
  });
});
