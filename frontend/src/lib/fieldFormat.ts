/**
 * 字段显示格式 —— 时间列怎么给人看，全站唯一口径。
 *
 * 为什么需要这一层：唯一取数出口 `backend/common/df_serialize.py: df_to_columns_rows`
 * 用 pandas `to_json(date_format="iso", date_unit="ms")` 序列化，MySQL 的 DATE 与
 * DATETIME 都变成 `2026-10-12T00:00:00.000`（实测连 `datetime.date` 也会被补零点+毫秒，
 * 所以 SQL 里写 `CAST(x AS DATE)` 没用）。这串直接当类目轴标签就会长到被截成 `2026⋯`，
 * 明细表格里也是一片噪声。
 *
 * 分工（不要反过来）：
 * - **数据层恒存 ISO 原值**：可解析、可比较、可再格式化，导出/下钻拿的还是原值；
 * - **显示层按声明归一**：格式令牌声明在数据集字段（`field_config.display_format`），
 *   由服务端随取数结果下发 `field_formats`（数据集是权威源，不复制进每张图表 config）。
 *
 * 所有渲染面必须走本文件的同一实现（`ui-vis-library-style.md` §2「存了就要真生效」）：
 * 图表 X 轴标签与 tooltip、表值图 HTML 表格、数据集预览表、统一数据网格。
 * 任何一处另写一套 `slice(0, 10)` 就是双口径 —— 改了格式只有一半页面跟着变。
 */

/**
 * 格式令牌清单（选择框的唯一选项来源，不得让用户手输 pattern 字符串）。
 * 与后端 `dataset_registry.FIELD_DISPLAY_FORMATS` 一一对应，新增要两侧同名改。
 * `example` 同时作为下拉项文案，让配置的人所见即所得。
 */
export const FIELD_DISPLAY_FORMATS: ReadonlyArray<{ value: string; label: string; example: string }> = [
  { value: '', label: '不格式化（原样）', example: '2026-10-12T00:00:00.000' },
  { value: 'date', label: '按日', example: '2026-10-12' },
  { value: 'datetime', label: '到分', example: '2026-10-12 14:30' },
  { value: 'datetime_s', label: '到秒', example: '2026-10-12 14:30:05' },
  { value: 'time', label: '仅时刻', example: '14:30:05' },
  { value: 'month', label: '按月', example: '2026-10' },
  { value: 'year', label: '按年', example: '2026' },
  { value: 'md', label: '月-日（跨年对比）', example: '10-12' },
];

const FORMAT_LABELS: Record<string, string> = Object.fromEntries(
  FIELD_DISPLAY_FORMATS.filter(f => f.value).map(f => [f.value, f.label]),
);

/** `2026-10-12T00:00:00.000` / `2026-10-12 14:30:05` / `2026-10-12`（秒可缺） */
const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/;

const pad = (v: unknown, n = 2) => String(v).padStart(n, '0');

interface Parts { y: string; m: string; d: string; hh: string; mm: string; ss: string }

/**
 * 从值里取年月日时分秒。
 *
 * 只做**字符串手术**、不经过 `new Date()` + `toISOString()`：后者会把本地时区掺进来，
 * UTC+8 下 `2026-10-12T00:00:00` 会被算成 `2026-10-11`（日期整体前移一天）。
 * 服务端下发的本来就是朴素本地时间串，按字面取段就是数据库说的那个值。
 * 只有纪元秒/毫秒这类数字形态才不得不过 Date（此时按本地时区读，与用户直觉一致）。
 */
function partsOf(value: unknown): Parts | null {
  if (value === null || value === undefined) return null;
  const str = String(value).trim();
  const hit = ISO_RE.exec(str);
  if (hit) {
    return { y: hit[1], m: hit[2], d: hit[3], hh: hit[4] ?? '00', mm: hit[5] ?? '00', ss: hit[6] ?? '00' };
  }
  if (/^\d{10}$/.test(str) || /^\d{13}$/.test(str)) {
    const date = new Date(Number(str.length === 10 ? Number(str) * 1000 : Number(str)));
    if (Number.isNaN(date.getTime())) return null;
    return {
      y: String(date.getFullYear()), m: pad(date.getMonth() + 1), d: pad(date.getDate()),
      hh: pad(date.getHours()), mm: pad(date.getMinutes()), ss: pad(date.getSeconds()),
    };
  }
  return null;
}

/**
 * 按格式令牌渲染单个值。认不出的格式、或值本身不是可识别的时间形态时
 * **原样返回**（不猜、不编一个日期出来）—— 宁可让用户看见旧值，也不要看见错值。
 */
export function formatFieldValue(value: unknown, fmt?: string): string {
  const raw = value === null || value === undefined ? '' : String(value);
  if (!fmt || !FORMAT_LABELS[fmt]) return raw;
  const p = partsOf(value);
  if (!p) return raw;
  switch (fmt) {
    case 'date': return `${p.y}-${p.m}-${p.d}`;
    case 'datetime': return `${p.y}-${p.m}-${p.d} ${p.hh}:${p.mm}`;
    case 'datetime_s': return `${p.y}-${p.m}-${p.d} ${p.hh}:${p.mm}:${p.ss}`;
    case 'time': return `${p.hh}:${p.mm}:${p.ss}`;
    case 'month': return `${p.y}-${p.m}`;
    case 'year': return p.y;
    case 'md': return `${p.m}-${p.d}`;
    default: return raw;
  }
}

/** 列名 → 格式令牌（服务端下发的 `field_formats`）；非对象/空值一律当没配。 */
export function normalizeFormats(formats: unknown): Record<string, string> {
  if (!formats || typeof formats !== 'object' || Array.isArray(formats)) return {};
  const out: Record<string, string> = {};
  for (const [col, fmt] of Object.entries(formats as Record<string, unknown>)) {
    const token = String(fmt ?? '').trim();
    if (token && FORMAT_LABELS[token]) out[col] = token;
  }
  return out;
}

/**
 * 把带格式的列在**渲染副本**里换成显示值（不改调用方传入的原对象）。
 *
 * 为什么在进渲染面之前整体换值、而不是往各图型分支塞 `labelFormatter`：
 * 轴标签、tooltip、图例、表值图、下钻条件读的都是同一批行值，逐分支挂格式化器
 * 必然漏掉某一面（历史缺陷就是「字模挂了没反应」那一类）。整体换一次，各处同源。
 * 行形态兼容对象数组与「数组的数组」（```chart 块在归一化前也可能是后者）。
 */
export function applyFieldFormats<T extends object>(data: T): T {
  const formats = normalizeFormats((data as { field_formats?: unknown }).field_formats);
  const columns: string[] = (data as { columns?: string[] }).columns || [];
  const rows: unknown[] = (data as { rows?: unknown[] }).rows || [];
  const hit = columns.filter(c => formats[c]);
  if (!hit.length || !rows.length) return data;
  const nextRows = rows.map((row: any) => {
    if (Array.isArray(row)) {
      const copy = [...row] as unknown[];
      columns.forEach((c, i) => {
        if (formats[c]) copy[i] = formatFieldValue(row[i], formats[c]);
      });
      return copy;
    }
    if (!row || typeof row !== 'object') return row;
    const copy = { ...row } as Record<string, unknown>;
    hit.forEach(c => {
      if (c in copy) copy[c] = formatFieldValue(copy[c], formats[c]);
    });
    return copy;
  });
  return { ...(data as object), rows: nextRows } as T;
}
