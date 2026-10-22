import { describe, it, expect } from 'vitest';
import { detectGeoColumns, unwrapLongitudes } from '../geoColumns';

describe('detectGeoColumns', () => {
  it('中文列名(纬度/经度)明确识别,nameMatched=true', () => {
    const rows = [
      { 城市: '北京', 纬度: 39.9, 经度: 116.4, 案件数: 12 },
      { 城市: '上海', 纬度: 31.2, 经度: 121.5, 案件数: 8 },
    ];
    const g = detectGeoColumns(['城市', '纬度', '经度', '案件数'], rows);
    expect(g.latCol).toBe('纬度');
    expect(g.lngCol).toBe('经度');
    expect(g.valueCol).toBe('案件数');
    expect(g.nameMatched).toBe(true);
    expect(g.rangeGuessed).toBe(false);
  });

  it('英文列名(lat/lng)识别', () => {
    const rows = [{ lat: 39.9, lng: 116.4, count: 3 }];
    const g = detectGeoColumns(['lat', 'lng', 'count'], rows);
    expect(g.latCol).toBe('lat');
    expect(g.lngCol).toBe('lng');
    expect(g.nameMatched).toBe(true);
  });

  it('列名无线索时按取值范围兜底,但 nameMatched=false(不自动切地图)', () => {
    const rows = [
      { a: 39.9, b: 116.4, n: 5 },
      { a: 31.2, b: 121.5, n: 2 },
    ];
    const g = detectGeoColumns(['a', 'b', 'n'], rows);
    expect(g.latCol).toBe('a');
    expect(g.lngCol).toBe('b');
    expect(g.nameMatched).toBe(false);
  });

  it('指标列落进 ±90/±180 时标 rangeGuessed=true(消费方必须拒绘并提醒)', () => {
    // 回归:官网流量按国家分布 —— 独立用户数=1/0、页面浏览量=38/0 被当成经纬度,
    // 热力点落在非洲几内亚湾(0,0)与东非外海(1,38)
    const rows = [
      { 国家: '中国', 独立用户数: 1, 页面浏览量: 38 },
      { 国家: '美国', 独立用户数: 0, 页面浏览量: 0 },
    ];
    const g = detectGeoColumns(['国家', '独立用户数', '页面浏览量'], rows);
    expect(g.nameMatched).toBe(false);
    expect(g.rangeGuessed).toBe(true);
  });

  it('非数值列/无经纬度时不误判', () => {
    const rows = [{ 城市: '北京', 产品: 'A' }];
    const g = detectGeoColumns(['城市', '产品'], rows);
    expect(g.latCol).toBe('');
    expect(g.lngCol).toBe('');
    expect(g.nameMatched).toBe(false);
    expect(g.rangeGuessed).toBe(false);
  });
});

describe('unwrapLongitudes 跨反经线视图展开', () => {
  it('中国+美国:以最大间隔为切口展开,视图中心落在太平洋而非非洲', () => {
    const out = unwrapLongitudes([104.19, -95.71]);
    expect(out[0]).toBeCloseTo(104.19, 6);      // 中国不动
    expect(out[1]).toBeCloseTo(264.29, 6);      // 美国 +360
    const span = Math.max(...out) - Math.min(...out);
    expect(span).toBeLessThan(180);              // 跨度取最小
    expect((Math.min(...out) + Math.max(...out)) / 2).toBeGreaterThan(180);  // 中心在太平洋
  });

  it('同侧点(中国+日本、德国+中国)不偏移', () => {
    expect(unwrapLongitudes([104.19, 138.25])).toEqual([104.19, 138.25]);
    expect(unwrapLongitudes([10.45, 104.19])).toEqual([10.45, 104.19]);
  });

  it('单点/重复经度原样返回', () => {
    expect(unwrapLongitudes([104.19])).toEqual([104.19]);
    expect(unwrapLongitudes([])).toEqual([]);
    expect(unwrapLongitudes([12, 12, 12])).toEqual([12, 12, 12]);
  });
});
