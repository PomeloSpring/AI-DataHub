import { describe, it, expect } from 'vitest';
import { detectGeoColumns } from '../geoColumns';

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

  it('非数值列/无经纬度时不误判', () => {
    const rows = [{ 城市: '北京', 产品: 'A' }];
    const g = detectGeoColumns(['城市', '产品'], rows);
    expect(g.latCol).toBe('');
    expect(g.lngCol).toBe('');
    expect(g.nameMatched).toBe(false);
  });
});
