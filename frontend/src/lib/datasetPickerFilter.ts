/**
 * 数据集清单的检索 / 过滤口径 —— 组件库「数据集」页与图表配置面板共用。
 *
 * 事实源纪律：清单永远来自 `GET /datasets/`，后端已按
 * 「角色数据集授权 ∧（可用数据源范围 ∩ 角色授权源 ≠ ∅）」过滤
 * （backend/modules/viz/services/dataset_service.py: list_datasets）。本模块只在
 * **已授权清单内**做本地筛选，不得在这里另造一套权限判断（防双口径），
 * 也不得把"筛完为空"和"没权限"混成同一句话（no-silent-degradation：两种空态必须可区分）。
 *
 * 清单已在一次请求内全量拿到（后端上限 200 行），所以检索走本地过滤：即时响应，
 * 且不会因为边打字边发请求而让权限裁决点出现"半截清单"。
 */

/** 通道类型业务名（枚举不外露给用户，措辞与数据集页一致） */
export const DATASET_KIND_LABELS: Record<string, string> = {
  sql: 'SQL 数据集',
  semantic: '语义数据集',
  es: '日志/ES 数据集',
};

const KIND_ORDER = ['sql', 'semantic', 'es'];

export interface DatasetFilterState {
  /** 关键词：空白分词，多词按 AND 命中 */
  keyword: string;
  /** 数据源业务名（''=全部）。ES 型数据集就挂在 ES 型数据源上，因此源选项天然涵盖它，
   *  不再有“内置通道”这种不挂源的第二维度（那一维随阶段三退役） */
  source: string;
  /** 通道类型 sql|semantic|es（''=全部） */
  kind: string;
  /** 只看当前角色真能执行（usable_now 非空）的数据集 */
  onlyUsable: boolean;
}

export const EMPTY_DATASET_FILTER: DatasetFilterState = { keyword: '', source: '', kind: '', onlyUsable: false };

/**
 * 通道类型判别：只看后端下发的**执行通道位** `is_es`（= 取数分派用的 `execution_is_es`，
 * 事实源是执行数据源的 db_type）。
 *
 * 旧写法是 `source_type==='es' || es_pattern 非空`：前者是已退役内置通道的行形状标记，
 * 后者是它的残留字段——拿它们当判据会把 ES 数据源型数据集认成 SQL 集，
 * 于是预览照旧发维度/度量重聚合而被护栏显式拒（同一份数据在看板上能出、在这里必失败）。
 */
export function datasetKindOf(ds: any): string {
  if (ds?.is_es) return 'es';
  const st = String(ds?.source_type || '').toLowerCase();
  if (st === 'semantic' || st === 'both') return st;
  // 'es' 这个已退役的枚举取值故意不承认：否则前端就又长出一套“拿标记当通道”的判据，
  // 与取数分派不同源（那种行现在根本存不进来：写入被后端显式拒）
  return 'sql';
}

const fieldLabelsOf = (ds: any): string[] => {
  const raw = ds?.field_config;
  const fields = typeof raw === 'string'
    ? (() => { try { return JSON.parse(raw); } catch { return []; } })()
    : raw;
  if (!Array.isArray(fields)) return [];
  return fields.flatMap((f: any) => (f && typeof f === 'object')
    ? [f.label, f.name, f.field].filter(Boolean) : [f]);
};

/** 参与关键词命中的全部业务文本（名称/描述/业务对象/预设类型/字段业务名/源名） */
export function datasetSearchText(ds: any): string {
  return [
    ds?.name, ds?.description, ds?.object_key, ds?.chart_type, ds?.datasource_name,
    ...(ds?.available_source_names || []), ...(ds?.usable_now || []), ...fieldLabelsOf(ds),
  ].filter(Boolean).map(v => String(v).toLowerCase()).join(' ');
}

/**
 * 预览口径：只给行数上限，**不传 dimensions/measures**。
 *
 * 这是活库实测结论（不是推测）：
 *  - SQL 型数据集：传字段会被编译器当成投影/聚合改写，编译出非法 SQL
 *    （实测：`"uv" must be an aggregate expression or appear in GROUP BY clause`），不传才正常回样本行；
 *  - 语义型数据集：不传时按数据集自身口径回行；拿 `field_config` 的指标名去传反而会被
 *    解析护栏拒（字典权威名与字段目录名不一致）。
 * 所以预览 = “该数据集当前口径的前 N 行”，字段映射属于图表渲染层，不塞进预览请求。
 * 以后要加字段参数，先在两类数据集上各跑一次实测再写。
 */
export const datasetPreviewPayload = (limit: number): Record<string, any> => ({
  // 不传 dimensions/measures：后端缺省即空集，且传了会编译出非法 SQL（见上）。
  // 生效数据源也不由前端指定，服务端在“可用范围 ∩ 角色授权集”内定（多义则回抛候选）。
  limit,
});

export function filterDatasets(items: any[], f: DatasetFilterState): any[] {
  const tokens = f.keyword.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return (items || []).filter(ds => {
    if (f.kind && datasetKindOf(ds) !== f.kind) return false;
    // 选具体源 = 该数据集能在该源上执行（与后端 `datasource_id` 筛选同口径：
    // 都看“可用范围”里有没有这个源，ES 型数据集不再被排除在外）
    if (f.source && !(ds.usable_now || []).includes(f.source)) return false;
    if (f.onlyUsable && !(ds.usable_now || []).length) return false;
    if (tokens.length) {
      const hay = datasetSearchText(ds);
      if (!tokens.every(t => hay.includes(t))) return false;
    }
    return true;
  });
}

/** 数据源筛选项：取自已授权清单里出现过的源业务名，按名排序 */
export function datasetSourceOptions(items: any[]): string[] {
  const names = new Set<string>();
  (items || []).forEach(ds => (ds.usable_now || []).forEach((n: string) => n && names.add(n)));
  return [...names].sort((a, b) => a.localeCompare(b, 'zh-CN'));
}

/** 类型筛选项：只列清单里出现的通道类型，固定 sql→semantic→es 顺序 */
export function datasetKindOptions(items: any[]): string[] {
  const present = new Set((items || []).map(datasetKindOf));
  return KIND_ORDER.filter(k => present.has(k));
}

export const isDatasetFilterActive = (f: DatasetFilterState): boolean =>
  !!(f.keyword.trim() || f.source || f.kind || f.onlyUsable);
