import { describe, it, expect } from 'vitest';
import { normalizePlace, resolvePlacePoints, detectPlaceColumn } from '../geoPoints';

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
});
