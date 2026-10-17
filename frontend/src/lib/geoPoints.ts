/**
 * 地名→坐标解析(纯函数) — 地图类图型复用 UI 字模库地图瓦片时用。
 *
 * 查询结果常带"省份/城市名"而非经纬度(如 案件按省份分布),这里用内置
 * 中国省级行政区 + 主要城市坐标表把地名转成热力点;未命中的地名显式回抛
 * (fail-loud,不静默丢行)。名称匹配做后缀归一(北京/北京市/…都命中)。
 */

export interface PlacePointDef {
  /** 规范名(展示用) */
  name: string;
  /** 别名(精确匹配用,如简称) */
  aliases?: string[];
  lat: number;
  lng: number;
}

/** 中国省级行政区(省会/中心点)+ 主要城市 */
export const CN_PLACES: PlacePointDef[] = [
  // 直辖市
  { name: '北京市', aliases: ['北京'], lat: 39.9042, lng: 116.4074 },
  { name: '上海市', aliases: ['上海'], lat: 31.2304, lng: 121.4737 },
  { name: '天津市', aliases: ['天津'], lat: 39.0842, lng: 117.2009 },
  { name: '重庆市', aliases: ['重庆'], lat: 29.5630, lng: 106.5516 },
  // 省(省会/中心点)
  { name: '河北省', aliases: ['河北'], lat: 38.0428, lng: 114.5149 },
  { name: '山西省', aliases: ['山西'], lat: 37.8706, lng: 112.5489 },
  { name: '辽宁省', aliases: ['辽宁'], lat: 41.8057, lng: 123.4315 },
  { name: '吉林省', aliases: ['吉林'], lat: 43.8171, lng: 125.3235 },
  { name: '黑龙江省', aliases: ['黑龙江'], lat: 45.8038, lng: 126.5349 },
  { name: '江苏省', aliases: ['江苏'], lat: 32.0603, lng: 118.7969 },
  { name: '浙江省', aliases: ['浙江'], lat: 30.2741, lng: 120.1551 },
  { name: '安徽省', aliases: ['安徽'], lat: 31.8206, lng: 117.2272 },
  { name: '福建省', aliases: ['福建'], lat: 26.0745, lng: 119.2965 },
  { name: '江西省', aliases: ['江西'], lat: 28.6820, lng: 115.8579 },
  { name: '山东省', aliases: ['山东'], lat: 36.6512, lng: 117.1201 },
  { name: '河南省', aliases: ['河南'], lat: 34.7466, lng: 113.6254 },
  { name: '湖北省', aliases: ['湖北'], lat: 30.5928, lng: 114.3055 },
  { name: '湖南省', aliases: ['湖南'], lat: 28.2282, lng: 112.9388 },
  { name: '广东省', aliases: ['广东'], lat: 23.1291, lng: 113.2644 },
  { name: '海南省', aliases: ['海南'], lat: 20.0442, lng: 110.1999 },
  { name: '四川省', aliases: ['四川'], lat: 30.5728, lng: 104.0668 },
  { name: '贵州省', aliases: ['贵州'], lat: 26.6470, lng: 106.6302 },
  { name: '云南省', aliases: ['云南'], lat: 25.0389, lng: 102.7183 },
  { name: '陕西省', aliases: ['陕西'], lat: 34.3416, lng: 108.9398 },
  { name: '甘肃省', aliases: ['甘肃'], lat: 36.0611, lng: 103.8343 },
  { name: '青海省', aliases: ['青海'], lat: 36.6171, lng: 101.7782 },
  { name: '台湾省', aliases: ['台湾'], lat: 25.0330, lng: 121.5654 },
  // 自治区
  { name: '内蒙古自治区', aliases: ['内蒙古'], lat: 40.8424, lng: 111.7490 },
  { name: '广西壮族自治区', aliases: ['广西'], lat: 22.8170, lng: 108.3665 },
  { name: '西藏自治区', aliases: ['西藏'], lat: 29.6520, lng: 91.1721 },
  { name: '宁夏回族自治区', aliases: ['宁夏'], lat: 38.4872, lng: 106.2309 },
  { name: '新疆维吾尔自治区', aliases: ['新疆'], lat: 43.8256, lng: 87.6168 },
  // 特别行政区
  { name: '香港特别行政区', aliases: ['香港'], lat: 22.3193, lng: 114.1694 },
  { name: '澳门特别行政区', aliases: ['澳门'], lat: 22.1987, lng: 113.5439 },
  // 主要城市(与省同名城市只留规范名,如广州市避免与广东省混淆靠归一化区分)
  { name: '广州市', aliases: ['广州'], lat: 23.1291, lng: 113.2644 },
  { name: '深圳市', aliases: ['深圳'], lat: 22.5431, lng: 114.0579 },
  { name: '杭州市', aliases: ['杭州'], lat: 30.2741, lng: 120.1551 },
  { name: '南京市', aliases: ['南京'], lat: 32.0603, lng: 118.7969 },
  { name: '成都市', aliases: ['成都'], lat: 30.5728, lng: 104.0668 },
  { name: '武汉市', aliases: ['武汉'], lat: 30.5928, lng: 114.3055 },
  { name: '西安市', aliases: ['西安'], lat: 34.3416, lng: 108.9398 },
  { name: '苏州市', aliases: ['苏州'], lat: 31.2989, lng: 120.5853 },
  { name: '郑州市', aliases: ['郑州'], lat: 34.7466, lng: 113.6254 },
  { name: '长沙市', aliases: ['长沙'], lat: 28.2282, lng: 112.9388 },
  { name: '青岛市', aliases: ['青岛'], lat: 36.0671, lng: 120.3826 },
  { name: '宁波市', aliases: ['宁波'], lat: 29.8683, lng: 121.5440 },
  { name: '合肥市', aliases: ['合肥'], lat: 31.8206, lng: 117.2272 },
  { name: '福州市', aliases: ['福州'], lat: 26.0745, lng: 119.2965 },
  { name: '厦门市', aliases: ['厦门'], lat: 24.4798, lng: 118.0894 },
  { name: '济南市', aliases: ['济南'], lat: 36.6512, lng: 117.1201 },
  { name: '大连市', aliases: ['大连'], lat: 38.9140, lng: 121.6147 },
  { name: '沈阳市', aliases: ['沈阳'], lat: 41.8057, lng: 123.4315 },
  { name: '哈尔滨市', aliases: ['哈尔滨'], lat: 45.8038, lng: 126.5349 },
  { name: '长春市', aliases: ['长春'], lat: 43.8171, lng: 125.3235 },
  { name: '昆明市', aliases: ['昆明'], lat: 25.0389, lng: 102.7183 },
  { name: '贵阳市', aliases: ['贵阳'], lat: 26.6470, lng: 106.6302 },
  { name: '南宁市', aliases: ['南宁'], lat: 22.8170, lng: 108.3665 },
  { name: '兰州市', aliases: ['兰州'], lat: 36.0611, lng: 103.8343 },
  { name: '太原市', aliases: ['太原'], lat: 37.8706, lng: 112.5489 },
  { name: '石家庄市', aliases: ['石家庄'], lat: 38.0428, lng: 114.5149 },
  { name: '南昌市', aliases: ['南昌'], lat: 28.6820, lng: 115.8579 },
  { name: '无锡市', aliases: ['无锡'], lat: 31.4912, lng: 120.3119 },
  { name: '东莞市', aliases: ['东莞'], lat: 23.0207, lng: 113.7518 },
  { name: '佛山市', aliases: ['佛山'], lat: 23.0215, lng: 113.1214 },
  { name: '温州市', aliases: ['温州'], lat: 27.9938, lng: 120.6994 },
  { name: '泉州市', aliases: ['泉州'], lat: 24.8741, lng: 118.6757 },
  { name: '洛阳市', aliases: ['洛阳'], lat: 34.6197, lng: 112.4540 },
  { name: '乌鲁木齐市', aliases: ['乌鲁木齐'], lat: 43.8256, lng: 87.6168 },
  { name: '拉萨市', aliases: ['拉萨'], lat: 29.6520, lng: 91.1721 },
  { name: '西宁市', aliases: ['西宁'], lat: 36.6171, lng: 101.7782 },
  { name: '银川市', aliases: ['银川'], lat: 38.4872, lng: 106.2309 },
  { name: '呼和浩特市', aliases: ['呼和浩特'], lat: 40.8424, lng: 111.7490 },
];

/** 地名归一:去空白与行政后缀(省/市/自治区/特别行政区/民族名),北京/北京市/…都归一到"北京" */
export function normalizePlace(name: string): string {
  let s = String(name ?? '').trim().replace(/\s+/g, '');
  let prev = '';
  while (s !== prev) {
    prev = s;
    s = s.replace(/(特别行政区|自治区|维吾尔|回族|壮族|省|市|地区)$/g, '');
  }
  return s;
}

let indexCache: Map<string, PlacePointDef> | null = null;

function placeIndex(): Map<string, PlacePointDef> {
  if (indexCache) return indexCache;
  const m = new Map<string, PlacePointDef>();
  for (const p of CN_PLACES) {
    m.set(normalizePlace(p.name), p);
    for (const a of p.aliases || []) m.set(normalizePlace(a), p);
  }
  indexCache = m;
  return m;
}

export interface PlaceResolution {
  points: { lat: number; lng: number; v: number; label: string }[];
  /** 未能定位的地名(去重,原样回抛,不静默丢行) */
  unresolved: string[];
}

/** 把"地区名+权重"行解析为坐标点;nameCol 未命中坐标表的进 unresolved。 */
export function resolvePlacePoints(rows: any[], nameCol: string, valueCol?: string): PlaceResolution {
  const idx = placeIndex();
  const points: PlaceResolution['points'] = [];
  const unresolvedSet = new Set<string>();
  for (const r of rows) {
    const raw = String(r?.[nameCol] ?? '').trim();
    if (!raw) continue;
    const hit = idx.get(normalizePlace(raw));
    if (!hit) {
      unresolvedSet.add(raw);
      continue;
    }
    const v = valueCol ? Number(r[valueCol]) : 1;
    points.push({ lat: hit.lat, lng: hit.lng, v: Number.isFinite(v) && v > 0 ? v : 0, label: raw });
  }
  return { points, unresolved: [...unresolvedSet] };
}

const PLACE_NAME_HINT = /省|市|地区|区域|城市|地名|place|province|city|region|district/i;

/** 命中率:该列非空值中能定位到坐标表的比例 */
export function placeHitRate(rows: any[], col: string): number {
  const idx = placeIndex();
  let total = 0; let hit = 0;
  for (const r of rows) {
    const raw = String(r?.[col] ?? '').trim();
    if (!raw) continue;
    total += 1;
    if (idx.has(normalizePlace(raw))) hit += 1;
  }
  return total === 0 ? 0 : hit / total;
}

/** 识别"地区名"列:列名线索优先,再按坐标表命中率;hitRate<0.3 不算识别到 */
export function detectPlaceColumn(columns: string[], rows: any[]): { col: string; hitRate: number } {
  const strCols = columns.filter(c => rows.length > 0 && rows.some(r => typeof r?.[c] === 'string' && String(r[c]).trim()));
  if (strCols.length === 0) return { col: '', hitRate: 0 };
  const byName = strCols.find(c => PLACE_NAME_HINT.test(c)) || '';
  let best = { col: byName, hitRate: byName ? placeHitRate(rows, byName) : 0 };
  for (const c of strCols) {
    const rate = placeHitRate(rows, c);
    if (rate > best.hitRate) best = { col: c, hitRate: rate };
  }
  return best.hitRate >= 0.3 ? best : { col: '', hitRate: best.hitRate };
}
