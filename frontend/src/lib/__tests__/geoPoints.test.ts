import { describe, it, expect } from 'vitest';
import { normalizePlace, resolvePlacePoints, detectPlaceColumn, lookupPlace } from '../geoPoints';

describe('geoPoints 地名→坐标', () => {
  it('行政后缀归一:北京/北京市/内蒙古自治区/新疆维吾尔自治区', () => {
    expect(normalizePlace('北京市')).toBe('北京');
    expect(normalizePlace('北京')).toBe('北京');
    expect(normalizePlace('河北省')).toBe('河北');
    expect(normalizePlace('内蒙古自治区')).toBe('内蒙古');
    expect(normalizePlace('新疆维吾尔自治区')).toBe('新疆');
    expect(normalizePlace('香港特别行政区')).toBe('香港');
    expect(normalizePlace('广西壮族自治区')).toBe('广西');
  });

  it('省份+权重解析为坐标点,未命中地名显式回抛(不静默丢行)', () => {
    const rows = [
      { 省份: '北京市', 企业数: 120 },
      { 省份: '广东省', 企业数: 90 },
      { 省份: '火星省', 企业数: 5 },
    ];
    const r = resolvePlacePoints(rows, '省份', '企业数');
    expect(r.points).toHaveLength(2);
    expect(r.points[0].v).toBe(120);
    expect(r.unresolved).toEqual(['火星省']);
  });

  it('detectPlaceColumn 列名线索优先并校验命中率', () => {
    const rows = [
      { 省份: '广东省', 企业数: 90 },
      { 省份: '江苏省', 企业数: 60 },
    ];
    expect(detectPlaceColumn(['省份', '企业数'], rows).col).toBe('省份');
    // 无地名列(值定位不了)→ 不识别
    expect(detectPlaceColumn(['产品', '销量'], [{ 产品: 'A', 销量: 1 }]).col).toBe('');
  });

  it('国家名可定位(中文/英文/全称/两位码均命中)', () => {
    expect(lookupPlace('中国')?.lat).toBeCloseTo(35.86, 1);
    expect(lookupPlace('美国')?.lng).toBeCloseTo(-95.71, 1);
    expect(lookupPlace('China')?.name).toBe('中国');
    expect(lookupPlace('china')?.name).toBe('中国');
    expect(lookupPlace('USA')?.name).toBe('美国');
    expect(lookupPlace('中华人民共和国')?.name).toBe('中国');
    expect(lookupPlace('CN')?.name).toBe('中国');
    // 中国省市仍优先按细粒度解析(不被国家表抢走)
    expect(lookupPlace('广东省')?.name).toBe('广东省');
  });

  it('回归:流量按国家分布 —— 国家列被识别为地区列,指标列不当经纬度', () => {
    // 历史缺陷:坐标表只有中国省市,“国家”列命中率 0 → 回退拿
    // 独立用户数当纬度、页面浏览量当经度 → 热力点落在非洲几内亚湾
    const rows = [
      { 国家: '中国', 独立用户数: 1, 页面浏览量: 38 },
      { 国家: '美国', 独立用户数: 5, 页面浏览量: 20 },
    ];
    const d = detectPlaceColumn(['国家', '独立用户数', '页面浏览量'], rows);
    expect(d.col).toBe('国家');
    const r = resolvePlacePoints(rows, '国家', '独立用户数');
    expect(r.unresolved).toEqual([]);
    expect(r.points).toHaveLength(2);
    const cn = r.points.find(p => p.label === '中国')!;
    const us = r.points.find(p => p.label === '美国')!;
    expect(cn.lng).toBeGreaterThan(100);   // 中国 104°E，不再是几内亚湾
    expect(us.lng).toBeLessThan(0);        // 美国 96°W
    expect(us.v).toBe(5);
  });

  it('未收录地名依旧显式回抛(不因加了国家表而静默丢行)', () => {
    const r = resolvePlacePoints([{ 国家: '亚特兰蒂斯' }], '国家');
    expect(r.points).toEqual([]);
    expect(r.unresolved).toEqual(['亚特兰蒂斯']);
  });
});
