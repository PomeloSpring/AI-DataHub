/**
 * 经纬度/权重列识别(纯函数) — 地图热力图(MapHeatmap)与图表卡共用。
 *
 * 列名线索(纬度/lat、经度/lng…)优先;无线索时按取值范围兜底给出**候选**。
 * - nameMatched:经纬度由列名明确识别 —— 只有这一档能自动当经纬度用;
 * - rangeGuessed:经纬度来自取值范围兜底 —— 普通指标列(如 UV=1、PV=38)同样会
 *   落进 ±90/±180,拿它直接画图会把指标值当地球坐标(历史缺陷:流量按国家分布
 *   渲染到非洲几内亚湾)。消费方必须把它当“待确认候选”并显式提示用户。
 */

export interface GeoColumns {
  latCol: string;
  lngCol: string;
  /** 权重列(可空,缺省每点权重一致) */
  valueCol: string;
  /** 经纬度是否由列名(纬度/lat/经度/lng…)明确识别 */
  nameMatched: boolean;
  /** 经纬度是否仅为取值范围兜底猜测(需用户确认,不得静默渲染) */
  rangeGuessed: boolean;
}

export const LAT_NAME = /纬度|(^|[^a-z])lat(itude)?([^a-z]|$)/i;
export const LNG_NAME = /经度|(^|[^a-z])(lng|lon|long|longitude)([^a-z]|$)/i;

export function isNum(v: any): boolean {
  return typeof v === 'number' ? Number.isFinite(v) : v !== '' && v != null && Number.isFinite(Number(v));
}

/**
 * 把经度展开为“最小跨度”的视图坐标。
 *
 * 点分布跨反经线时(如 中国 lng=104 + 美国 lng=-96),直接取 [min,max] 区间
 * 会得到 199° 的矩形、视图中心落在 lng≈4(大西洋/非洲),与数据分布完全不符;
 * 这里以经度环上的**最大间隔**为切口展开,使视图中心落在点集真正密集的一侧
 * (上例→太平洋为中心)。返回与入参同序的视图经度,可能 > 180
 * (Leaflet 按世界副本投影,可正常 latLngToContainerPoint / fitBounds)。
 */
export function unwrapLongitudes(lngs: number[]): number[] {
  if (lngs.length < 2) return [...lngs];
  const sorted = [...lngs].sort((a, b) => a - b);
  let maxGap = -1;
  let cut = sorted[0];
  for (let i = 0; i < sorted.length - 1; i++) {
    const g = sorted[i + 1] - sorted[i];
    if (g > maxGap) { maxGap = g; cut = sorted[i]; }
  }
  const wrapGap = 360 - (sorted[sorted.length - 1] - sorted[0]);
  if (wrapGap >= maxGap) return [...lngs];  // 切口在首尾之间 → 原序已是最小跨度
  return lngs.map(l => (l <= cut ? l + 360 : l));
}

/** 按列名(纬度/lat…)与取值范围识别经纬度列,再挑首个数值列作权重。 */
export function detectGeoColumns(columns: string[], rows: any[]): GeoColumns {
  const numericCols = columns.filter(c => rows.length > 0 && rows.every(r => isNum(r[c])));
  const latByName = columns.find(c => LAT_NAME.test(c)) || '';
  const lngByName = columns.find(c => LNG_NAME.test(c)) || '';
  let latCol = latByName;
  let lngCol = lngByName;
  // 列名不含线索时按取值范围兜底给出候选:纬度 ±90、经度 ±180
  if (!latCol) {
    latCol = numericCols.find(c => rows.every(r => Math.abs(Number(r[c])) <= 90)) || '';
  }
  if (!lngCol) {
    lngCol = numericCols.find(c => c !== latCol && rows.every(r => Math.abs(Number(r[c])) <= 180)) || '';
  }
  const rangeGuessed = !!(latCol && lngCol) && !(latByName && lngByName);
  const valueCol = numericCols.find(c => c !== latCol && c !== lngCol) || '';
  return {
    latCol, lngCol, valueCol,
    nameMatched: !!(latByName && lngByName),
    rangeGuessed,
  };
}
