/**
 * 地名→坐标解析(纯函数) — 地图类图型复用 UI 字模库地图瓦片时用。
 *
 * 查询结果常带"省份/城市名"或"国家名"而非经纬度(如 案件按省份分布、流量按国家分布),
 * 这里用内置坐标表把地名转成热力点:
 *  - CN_PLACES:中国省级行政区 + 主要城市;
 *  - WORLD_PLACES:国家(含中英文与两位码别名,geo.country 常返回中文名)。
 * 未命中的地名显式回抛(fail-loud,不静默丢行)。名称匹配做后缀/大小写归一
 * (北京/北京市、China/CN/中国 都命中)。
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

/** 国家(中心点) — 覆盖 geo.country / 国家维度常见取值;别名含英文名与两位码 */
export const WORLD_PLACES: PlacePointDef[] = [
  { name: '中国', aliases: ['中华人民共和国', 'China', 'CN'], lat: 35.86, lng: 104.19 },
  { name: '美国', aliases: ['美利坚', 'United States', 'USA', 'US'], lat: 37.09, lng: -95.71 },
  { name: '日本', aliases: ['Japan', 'JP'], lat: 36.2, lng: 138.25 },
  { name: '韩国', aliases: ['大韩民国', 'Korea', 'South Korea', 'KR'], lat: 35.9, lng: 127.77 },
  { name: '朝鲜', aliases: ['North Korea', 'KP'], lat: 40.34, lng: 127.51 },
  { name: '蒙古', aliases: ['蒙古国', 'Mongolia', 'MN'], lat: 46.86, lng: 103.85 },
  { name: '越南', aliases: ['Vietnam', 'VN'], lat: 14.06, lng: 108.28 },
  { name: '老挝', aliases: ['Laos', 'LA'], lat: 19.86, lng: 102.5 },
  { name: '柬埔寨', aliases: ['Cambodia', 'KH'], lat: 12.57, lng: 104.99 },
  { name: '泰国', aliases: ['Thailand', 'TH'], lat: 15.87, lng: 100.99 },
  { name: '缅甸', aliases: ['Myanmar', 'MM'], lat: 21.91, lng: 95.96 },
  { name: '马来西亚', aliases: ['Malaysia', 'MY'], lat: 4.21, lng: 101.98 },
  { name: '新加坡', aliases: ['Singapore', 'SG'], lat: 1.35, lng: 103.82 },
  { name: '印度尼西亚', aliases: ['印尼', 'Indonesia', 'ID'], lat: -0.79, lng: 113.92 },
  { name: '菲律宾', aliases: ['Philippines', 'PH'], lat: 12.88, lng: 121.77 },
  { name: '文莱', aliases: ['Brunei', 'BN'], lat: 4.54, lng: 114.73 },
  { name: '东帝汶', aliases: ['Timor-Leste', 'TL'], lat: -8.87, lng: 125.73 },
  { name: '尼泊尔', aliases: ['Nepal', 'NP'], lat: 28.39, lng: 84.12 },
  { name: '不丹', aliases: ['Bhutan', 'BT'], lat: 27.51, lng: 90.43 },
  { name: '印度', aliases: ['India', 'IN'], lat: 20.59, lng: 78.96 },
  { name: '巴基斯坦', aliases: ['Pakistan', 'PK'], lat: 30.38, lng: 69.35 },
  { name: '孟加拉国', aliases: ['Bangladesh', 'BD'], lat: 23.68, lng: 90.36 },
  { name: '斯里兰卡', aliases: ['Sri Lanka', 'LK'], lat: 7.87, lng: 80.77 },
  { name: '马尔代夫', aliases: ['Maldives', 'MV'], lat: 3.2, lng: 73.22 },
  { name: '阿富汗', aliases: ['Afghanistan', 'AF'], lat: 33.94, lng: 67.71 },
  { name: '哈萨克斯坦', aliases: ['Kazakhstan', 'KZ'], lat: 48.02, lng: 66.92 },
  { name: '乌兹别克斯坦', aliases: ['Uzbekistan', 'UZ'], lat: 41.38, lng: 64.59 },
  { name: '土库曼斯坦', aliases: ['Turkmenistan', 'TM'], lat: 38.97, lng: 59.56 },
  { name: '吉尔吉斯斯坦', aliases: ['Kyrgyzstan', 'KG'], lat: 41.2, lng: 74.77 },
  { name: '塔吉克斯坦', aliases: ['Tajikistan', 'TJ'], lat: 38.86, lng: 71.28 },
  { name: '伊朗', aliases: ['Iran', 'IR'], lat: 32.43, lng: 53.69 },
  { name: '伊拉克', aliases: ['Iraq', 'IQ'], lat: 33.22, lng: 43.68 },
  { name: '沙特阿拉伯', aliases: ['沙特', 'Saudi Arabia', 'SA'], lat: 23.89, lng: 45.08 },
  { name: '阿联酋', aliases: ['阿拉伯联合酋长国', 'United Arab Emirates', 'UAE', 'AE'], lat: 23.42, lng: 53.85 },
  { name: '卡塔尔', aliases: ['Qatar', 'QA'], lat: 25.35, lng: 51.18 },
  { name: '科威特', aliases: ['Kuwait', 'KW'], lat: 29.31, lng: 47.48 },
  { name: '巴林', aliases: ['Bahrain', 'BH'], lat: 26.07, lng: 50.56 },
  { name: '阿曼', aliases: ['Oman', 'OM'], lat: 21.47, lng: 55.98 },
  { name: '也门', aliases: ['Yemen', 'YE'], lat: 15.55, lng: 48.52 },
  { name: '以色列', aliases: ['Israel', 'IL'], lat: 31.05, lng: 34.85 },
  { name: '巴勒斯坦', aliases: ['Palestine', 'PS'], lat: 31.95, lng: 35.21 },
  { name: '约旦', aliases: ['Jordan', 'JO'], lat: 30.59, lng: 36.24 },
  { name: '黎巴嫩', aliases: ['Lebanon', 'LB'], lat: 33.85, lng: 35.86 },
  { name: '叙利亚', aliases: ['Syria', 'SY'], lat: 34.8, lng: 38.99 },
  { name: '土耳其', aliases: ['Turkey', 'Türkiye', 'TR'], lat: 38.96, lng: 35.24 },
  { name: '格鲁吉亚', aliases: ['Georgia', 'GE'], lat: 42.32, lng: 43.36 },
  { name: '亚美尼亚', aliases: ['Armenia', 'AM'], lat: 40.07, lng: 45.04 },
  { name: '阿塞拜疆', aliases: ['Azerbaijan', 'AZ'], lat: 40.14, lng: 47.58 },
  { name: '俄罗斯', aliases: ['俄国', 'Russia', 'RU'], lat: 61.52, lng: 105.32 },
  { name: '乌克兰', aliases: ['Ukraine', 'UA'], lat: 48.38, lng: 31.17 },
  { name: '白俄罗斯', aliases: ['Belarus', 'BY'], lat: 53.71, lng: 27.95 },
  { name: '波兰', aliases: ['Poland', 'PL'], lat: 51.92, lng: 19.15 },
  { name: '德国', aliases: ['Germany', 'DE'], lat: 51.17, lng: 10.45 },
  { name: '法国', aliases: ['France', 'FR'], lat: 46.6, lng: 1.89 },
  { name: '英国', aliases: ['大不列颠', 'United Kingdom', 'UK', 'GB', 'England'], lat: 52.36, lng: -1.18 },
  { name: '爱尔兰', aliases: ['Ireland', 'IE'], lat: 53.14, lng: -8.24 },
  { name: '荷兰', aliases: ['Netherlands', 'NL'], lat: 52.13, lng: 5.29 },
  { name: '比利时', aliases: ['Belgium', 'BE'], lat: 50.5, lng: 4.47 },
  { name: '卢森堡', aliases: ['Luxembourg', 'LU'], lat: 49.82, lng: 6.13 },
  { name: '瑞士', aliases: ['Switzerland', 'CH'], lat: 46.8, lng: 8.23 },
  { name: '奥地利', aliases: ['Austria', 'AT'], lat: 47.52, lng: 14.55 },
  { name: '捷克', aliases: ['捷克共和国', 'Czechia', 'Czech Republic', 'CZ'], lat: 49.82, lng: 15.47 },
  { name: '斯洛伐克', aliases: ['Slovakia', 'SK'], lat: 48.67, lng: 19.7 },
  { name: '匈牙利', aliases: ['Hungary', 'HU'], lat: 47.16, lng: 19.5 },
  { name: '罗马尼亚', aliases: ['Romania', 'RO'], lat: 45.94, lng: 24.97 },
  { name: '保加利亚', aliases: ['Bulgaria', 'BG'], lat: 42.73, lng: 25.49 },
  { name: '塞尔维亚', aliases: ['Serbia', 'RS'], lat: 44.02, lng: 21.01 },
  { name: '克罗地亚', aliases: ['Croatia', 'HR'], lat: 45.1, lng: 15.2 },
  { name: '斯洛文尼亚', aliases: ['Slovenia', 'SI'], lat: 46.16, lng: 14.99 },
  { name: '波黑', aliases: ['波斯尼亚', 'Bosnia and Herzegovina', 'BA'], lat: 43.92, lng: 17.68 },
  { name: '北马其顿', aliases: ['马其顿', 'North Macedonia', 'MK'], lat: 41.61, lng: 21.75 },
  { name: '阿尔巴尼亚', aliases: ['Albania', 'AL'], lat: 41.15, lng: 20.17 },
  { name: '黑山', aliases: ['Montenegro', 'ME'], lat: 42.71, lng: 19.37 },
  { name: '希腊', aliases: ['Greece', 'GR'], lat: 39.07, lng: 21.82 },
  { name: '意大利', aliases: ['Italy', 'IT'], lat: 41.87, lng: 12.57 },
  { name: '西班牙', aliases: ['Spain', 'ES'], lat: 40.46, lng: -3.75 },
  { name: '葡萄牙', aliases: ['Portugal', 'PT'], lat: 39.4, lng: -8.22 },
  { name: '冰岛', aliases: ['Iceland', 'IS'], lat: 64.96, lng: -19.02 },
  { name: '挪威', aliases: ['Norway', 'NO'], lat: 60.47, lng: 8.47 },
  { name: '瑞典', aliases: ['Sweden', 'SE'], lat: 60.13, lng: 18.64 },
  { name: '芬兰', aliases: ['Finland', 'FI'], lat: 61.92, lng: 25.75 },
  { name: '丹麦', aliases: ['Denmark', 'DK'], lat: 56.26, lng: 9.5 },
  { name: '爱沙尼亚', aliases: ['Estonia', 'EE'], lat: 58.6, lng: 25.01 },
  { name: '拉脱维亚', aliases: ['Latvia', 'LV'], lat: 56.88, lng: 24.6 },
  { name: '立陶宛', aliases: ['Lithuania', 'LT'], lat: 55.17, lng: 23.88 },
  { name: '摩尔多瓦', aliases: ['Moldova', 'MD'], lat: 47.41, lng: 28.37 },
  { name: '加拿大', aliases: ['Canada', 'CA'], lat: 56.13, lng: -106.35 },
  { name: '墨西哥', aliases: ['Mexico', 'MX'], lat: 23.63, lng: -102.55 },
  { name: '古巴', aliases: ['Cuba', 'CU'], lat: 21.52, lng: -77.78 },
  { name: '巴西', aliases: ['Brazil', 'BR'], lat: -14.24, lng: -51.93 },
  { name: '阿根廷', aliases: ['Argentina', 'AR'], lat: -38.42, lng: -63.62 },
  { name: '智利', aliases: ['Chile', 'CL'], lat: -35.68, lng: -71.54 },
  { name: '秘鲁', aliases: ['Peru', 'PE'], lat: -9.19, lng: -75.02 },
  { name: '哥伦比亚', aliases: ['Colombia', 'CO'], lat: 4.57, lng: -74.3 },
  { name: '委内瑞拉', aliases: ['Venezuela', 'VE'], lat: 6.42, lng: -66.59 },
  { name: '厄瓜多尔', aliases: ['Ecuador', 'EC'], lat: -1.83, lng: -78.18 },
  { name: '玻利维亚', aliases: ['Bolivia', 'BO'], lat: -16.29, lng: -63.59 },
  { name: '澳大利亚', aliases: ['澳洲', 'Australia', 'AU'], lat: -25.27, lng: 133.78 },
  { name: '新西兰', aliases: ['New Zealand', 'NZ'], lat: -40.9, lng: 172.69 },
  { name: '斐济', aliases: ['Fiji', 'FJ'], lat: -17.71, lng: 178.07 },
  { name: '埃及', aliases: ['Egypt', 'EG'], lat: 26.82, lng: 30.8 },
  { name: '南非', aliases: ['South Africa', 'ZA'], lat: -30.56, lng: 22.94 },
  { name: '尼日利亚', aliases: ['Nigeria', 'NG'], lat: 9.08, lng: 8.68 },
  { name: '肯尼亚', aliases: ['Kenya', 'KE'], lat: -0.02, lng: 37.91 },
  { name: '埃塞俄比亚', aliases: ['Ethiopia', 'ET'], lat: 9.15, lng: 40.49 },
  { name: '摩洛哥', aliases: ['Morocco', 'MA'], lat: 31.79, lng: -7.09 },
  { name: '阿尔及利亚', aliases: ['Algeria', 'DZ'], lat: 28.03, lng: 1.66 },
  { name: '突尼斯', aliases: ['Tunisia', 'TN'], lat: 33.89, lng: 9.54 },
  { name: '加纳', aliases: ['Ghana', 'GH'], lat: 7.95, lng: -1.02 },
  { name: '坦桑尼亚', aliases: ['Tanzania', 'TZ'], lat: -6.37, lng: 34.89 },
  { name: '乌干达', aliases: ['Uganda', 'UG'], lat: 1.37, lng: 32.29 },
  { name: '刚果民主共和国', aliases: ['刚果金', 'DR Congo', 'CD'], lat: -4.04, lng: 21.76 },
  { name: '安哥拉', aliases: ['Angola', 'AO'], lat: -11.2, lng: 17.87 },
  { name: '赞比亚', aliases: ['Zambia', 'ZM'], lat: -13.13, lng: 27.85 },
  { name: '津巴布韦', aliases: ['Zimbabwe', 'ZW'], lat: -19.02, lng: 29.15 },
  { name: '莫桑比克', aliases: ['Mozambique', 'MZ'], lat: -18.67, lng: 35.53 },
  { name: '喀麦隆', aliases: ['Cameroon', 'CM'], lat: 7.37, lng: 12.35 },
  { name: '塞内加尔', aliases: ['Senegal', 'SN'], lat: 14.5, lng: -14.45 },
  { name: '苏丹', aliases: ['Sudan', 'SD'], lat: 12.86, lng: 30.22 },
  { name: '利比亚', aliases: ['Libya', 'LY'], lat: 26.34, lng: 17.23 },
  { name: '留尼汪', aliases: ['Reunion', 'RE'], lat: -21.12, lng: 55.54 },
];

/** 全量地名坐标(中国省市 + 国家) — 索引与命中率均以此为准 */
export const ALL_PLACES: PlacePointDef[] = [...CN_PLACES, ...WORLD_PLACES];

/** 地名归一:去空白与行政后缀(省/市/自治区/特别行政区/民族名),北京/北京市/…都归一到"北京" */
export function normalizePlace(name: string): string {
  let s = String(name ?? '').trim().replace(/\s+/g, '');
  let prev = '';
  while (s !== prev) {
    prev = s;
    s = s.replace(/(特别行政区|自治区|维吾尔|回族|壮族|省|市|地区|共和国)$/g, '');
  }
  return s;
}

let indexCache: Map<string, PlacePointDef> | null = null;

/** 索引键:归一名 + 小写形(英文名 China/china/CHINA 同命) */
function indexKeys(name: string): string[] {
  const n = normalizePlace(name);
  return n ? [n, n.toLowerCase()] : [];
}

function placeIndex(): Map<string, PlacePointDef> {
  if (indexCache) return indexCache;
  const m = new Map<string, PlacePointDef>();
  // 中国省市在前、国家在后:同名时(如“吉林”省与不存在的市级)保留先登记的细粒度项
  for (const p of ALL_PLACES) {
    for (const k of indexKeys(p.name)) if (!m.has(k)) m.set(k, p);
    for (const a of p.aliases || []) for (const k of indexKeys(a)) if (!m.has(k)) m.set(k, p);
  }
  indexCache = m;
  return m;
}

/** 按地名查坐标(后缀/大小写归一);未命中返回 undefined */
export function lookupPlace(raw: string): PlacePointDef | undefined {
  const s = normalizePlace(raw);
  if (!s) return undefined;
  const m = placeIndex();
  return m.get(s) ?? m.get(s.toLowerCase());
}

export interface PlaceResolution {
  points: { lat: number; lng: number; v: number; label: string }[];
  /** 未能定位的地名(去重,原样回抛,不静默丢行) */
  unresolved: string[];
}

/** 把"地区名+权重"行解析为坐标点;nameCol 未命中坐标表的进 unresolved。 */
export function resolvePlacePoints(rows: any[], nameCol: string, valueCol?: string): PlaceResolution {
  const points: PlaceResolution['points'] = [];
  const unresolvedSet = new Set<string>();
  for (const r of rows) {
    const raw = String(r?.[nameCol] ?? '').trim();
    if (!raw) continue;
    const hit = lookupPlace(raw);
    if (!hit) {
      unresolvedSet.add(raw);
      continue;
    }
    const v = valueCol ? Number(r[valueCol]) : 1;
    points.push({ lat: hit.lat, lng: hit.lng, v: Number.isFinite(v) && v > 0 ? v : 0, label: raw });
  }
  return { points, unresolved: [...unresolvedSet] };
}

/** 命中率:该列非空值中能定位到坐标表的比例 */
export function placeHitRate(rows: any[], col: string): number {
  let total = 0; let hit = 0;
  for (const r of rows) {
    const raw = String(r?.[col] ?? '').trim();
    if (!raw) continue;
    total += 1;
    if (lookupPlace(raw)) hit += 1;
  }
  return total === 0 ? 0 : hit / total;
}

const PLACE_NAME_HINT = /省|市|地区|区域|城市|地名|国家|国别|洲|place|province|city|region|district|country|nation|continent/i;

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
