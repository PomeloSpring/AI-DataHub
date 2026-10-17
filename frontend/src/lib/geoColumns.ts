/**
 * 经纬度/权重列识别(纯函数) — 地图热力图(MapHeatmap)与图表卡共用。
 *
 * 列名线索(纬度/lat、经度/lng…)优先;无线索时按取值范围兜底识别(纬度 ±90、经度 ±180)。
 * nameMatched 标注是否由列名明确识别 —— 自动切换地图热力只信列名线索,
 * 范围兜底仅用于给手动选择框提供初值,避免普通数值列被误当经纬度。
 */

export interface GeoColumns {
  latCol: string;
  lngCol: string;
  /** 权重列(可空,缺省每点权重一致) */
  valueCol: string;
  /** 经纬度是否由列名(纬度/lat/经度/lng…)明确识别;范围兜底识别不算 */
  nameMatched: boolean;
}

export const LAT_NAME = /纬度|(^|[^a-z])lat(itude)?([^a-z]|$)/i;
export const LNG_NAME = /经度|(^|[^a-z])(lng|lon|long|longitude)([^a-z]|$)/i;

export function isNum(v: any): boolean {
  return typeof v === 'number' ? Number.isFinite(v) : v !== '' && v != null && Number.isFinite(Number(v));
}

/** 按列名(纬度/lat…)与取值范围识别经纬度列,再挑首个数值列作权重。 */
export function detectGeoColumns(columns: string[], rows: any[]): GeoColumns {
  const numericCols = columns.filter(c => rows.length > 0 && rows.every(r => isNum(r[c])));
  const latByName = columns.find(c => LAT_NAME.test(c)) || '';
  const lngByName = columns.find(c => LNG_NAME.test(c)) || '';
  let latCol = latByName;
  let lngCol = lngByName;
  // 列名不含线索时按取值范围兜底识别:纬度 ±90、经度 ±180
  if (!latCol) {
    latCol = numericCols.find(c => rows.every(r => Math.abs(Number(r[c])) <= 90)) || '';
  }
  if (!lngCol) {
    lngCol = numericCols.find(c => c !== latCol && rows.every(r => Math.abs(Number(r[c])) <= 180)) || '';
  }
  const valueCol = numericCols.find(c => c !== latCol && c !== lngCol) || '';
  return { latCol, lngCol, valueCol, nameMatched: !!(latByName && lngByName) };
}
