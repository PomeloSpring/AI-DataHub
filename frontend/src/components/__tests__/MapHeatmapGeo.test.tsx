import { cleanup, render, screen, waitFor } from '@testing-library/react';
import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

// Leaflet 在 jsdom 里只留“被怎么调用”的记录:本用例锁的是定位/视图几何契约,不是瓦片渲染
const mapCalls: any = { mapOpts: null, bounds: null, fitArgs: null, center: null, fitOk: true };
const fakeMap = {
  setView: vi.fn(),
  getSize: () => ({ x: 800, y: 400 }),
  latLngToContainerPoint: (ll: [number, number]) => { mapCalls.center = ll; return { x: 10, y: 10 }; },
  fitBounds: vi.fn((b: any) => { mapCalls.fitArgs = b; }),
  getBounds: () => ({ contains: () => mapCalls.fitOk }),
  on: vi.fn(), off: vi.fn(), remove: vi.fn(), invalidateSize: vi.fn(),
};
vi.mock('leaflet', () => ({
  default: {
    map: (_el: any, opts: any) => { mapCalls.mapOpts = opts; return fakeMap; },
    tileLayer: () => ({ addTo: () => {} }),
    latLngBounds: (pts: [number, number][]) => { mapCalls.bounds = pts; return { isValid: () => true }; },
  },
}));

// 字模库瓦片清单 + 可达性探测(网络)不进本用例；瓦片缩放区间走真实 tileZoomOf
const tileState = vi.hoisted(() => ({ provider: 'amap' as string, id: 1 }));
vi.mock('../../api/visLibrary', () => ({
  fetchVisComponents: async () => ([{
    id: 1, code: 'mt_amap', name: '高德地图标准', category: 'map_tile',
    is_active: 1, style_config: { tileUrl: 'https://tile.example/{z}/{x}/{y}.png', provider: tileState.provider },
  }]),
}));
vi.mock('../../lib/mapTileCache', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/mapTileCache')>()),
  readTileCache: () => null,
  writeTileCache: (items: any) => ({ items, fetchedAt: 0 }),
  isTileCacheFresh: () => false,
  tileUrlOf: () => 'https://tile.example/{z}/{x}/{y}.png',
  isUsableTile: () => true,
  isDefaultTile: () => true,
  probeTile: async () => true,
  pickReachableTile: async (pool: any[]) => pool[0],
}));

import MapHeatmap from '../MapHeatmap';

// jsdom 容器高度恒 0 → 组件会跳过 fitBounds;给出真实高度以覆盖拟合路径
Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, value: 400 });
// jsdom 无 canvas 实现(热力层在真实浏览器里跑):静音 getContext 噪声,本用例只锁几何契约
HTMLCanvasElement.prototype.getContext = (() => null) as any;

afterEach(() => {
  cleanup();
  mapCalls.mapOpts = null; mapCalls.bounds = null; mapCalls.fitArgs = null; mapCalls.center = null;
  mapCalls.fitOk = true;
  tileState.provider = 'amap';
  vi.clearAllMocks();
});

describe('MapHeatmap 地理定位与视图几何', () => {
  it('国家分布按地名定位:中国/美国落在各自坐标,不再被指标列当经纬度', async () => {
    // 回归缺陷:结果列为 [国家, 独立用户数, 页面浏览量],坐标表无国家名 →
    // 回退把 独立用户数 当纬度、页面浏览量 当经度 → 热力点画在非洲几内亚湾
    render(<MapHeatmap data={{
      columns: ['国家', '独立用户数', '页面浏览量'],
      rows: [
        { 国家: '中国', 独立用户数: 1, 页面浏览量: 38 },
        { 国家: '美国', 独立用户数: 5, 页面浏览量: 20 },
      ],
    }} />);
    expect(await screen.findByText('2 个热力点')).toBeInTheDocument();
    expect(screen.queryByText(/结果里没有真正的经纬度列/)).toBeNull();
    // 纬度/经度选择框不出现(走地名定位),且不再有“无(用经纬度)”的空档
    expect(screen.queryByText('纬度')).toBeNull();
    const pts = mapCalls.bounds as [number, number][];
    expect(pts).toHaveLength(2);
    expect(pts.some(p => Math.abs(p[0] - 35.86) < 1 && Math.abs(p[1] - 104.19) < 1)).toBe(true);
  });

  it('跨太平洋分布展开经度:视图中心不再落在非洲', async () => {
    render(<MapHeatmap data={{
      columns: ['国家', '独立用户数'],
      rows: [{ 国家: '中国', 独立用户数: 1 }, { 国家: '美国', 独立用户数: 5 }],
    }} />);
    await screen.findByText('2 个热力点');
    const lngs = (mapCalls.bounds as [number, number][]).map(p => p[1]);
    // 美国被 +360 展开 → 跨度 160°(太平洋居中),而不是 200°(大西洋/非洲居中)
    expect(Math.max(...lngs) - Math.min(...lngs)).toBeLessThan(180);
    expect(Math.max(...lngs)).toBeGreaterThan(180);
    expect((Math.min(...lngs) + Math.max(...lngs)) / 2).toBeGreaterThan(180);
  });

  it('缩放下限跟随瓦片源:高德 z=3(硬写 2 会让 z<3 整块空白)', async () => {
    render(<MapHeatmap data={{
      columns: ['国家', '独立用户数'],
      rows: [{ 国家: '中国', 独立用户数: 1 }, { 国家: '美国', 独立用户数: 5 }],
    }} />);
    await screen.findByText('2 个热力点');
    expect(mapCalls.mapOpts?.minZoom).toBe(3);        // 高德 z<3 不出图
    expect(mapCalls.mapOpts?.maxZoom).toBe(18);
  });

  it('换可到全球的瓦片源(Carto)时下限放宽,可缩到整颗地球', async () => {
    tileState.provider = 'carto';
    render(<MapHeatmap data={{
      columns: ['国家', '独立用户数'],
      rows: [{ 国家: '中国', 独立用户数: 1 }, { 国家: '美国', 独立用户数: 5 }],
    }} />);
    await screen.findByText('2 个热力点');
    expect(mapCalls.mapOpts?.minZoom).toBe(0);
  });

  it('瓦片源最小层级装不下全部点时显式提示,不默默裁视野', async () => {
    mapCalls.fitOk = false;   // fitBounds 后仍有热力点在视野外
    render(<MapHeatmap data={{
      columns: ['国家', '独立用户数'],
      rows: [{ 国家: '中国', 独立用户数: 1 }, { 国家: '美国', 独立用户数: 5 }],
    }} />);
    expect(await screen.findByText(/已缩到它支持的最小层级/)).toBeInTheDocument();
    expect(screen.getByText(/支持更小层级的瓦片/)).toBeInTheDocument();
  });

  it('无地名列且经纬度只是取值范围猜出时:显式提示并拒绘(不画错答案)', async () => {
    render(<MapHeatmap data={{
      columns: ['独立用户数', '页面浏览量'],
      rows: [{ 独立用户数: 1, 页面浏览量: 38 }, { 独立用户数: 0, 页面浏览量: 0 }],
    }} />);
    expect(await screen.findByText(/结果里没有真正的经纬度列/)).toBeInTheDocument();
    expect(screen.getByText(/确认这两列就是经纬度/)).toBeInTheDocument();
    expect(mapCalls.mapOpts).toBeNull();       // 未创建地图 = 没有误导性图面
    expect(screen.queryByText(/个热力点/)).toBeNull();
  });

  it('列名含纬度/经度时按真实经纬度渲染(不受猜列保护影响)', async () => {
    render(<MapHeatmap data={{
      columns: ['城市', '纬度', '经度', '案件数'],
      rows: [
        { 城市: '北京', 纬度: 39.9, 经度: 116.4, 案件数: 12 },
        { 城市: '上海', 纬度: 31.2, 经度: 121.5, 案件数: 8 },
      ],
    }} />);
    await screen.findByText('2 个热力点');
    expect(screen.queryByText(/结果里没有真正的经纬度列/)).toBeNull();
    await waitFor(() => expect(mapCalls.mapOpts).not.toBeNull());
  });

  it('「中国地图」图型含境外点:不隐去,显式提示可换世界地图', async () => {
    render(<MapHeatmap scope="china" data={{
      columns: ['国家', '独立用户数'],
      rows: [{ 国家: '中国', 独立用户数: 1 }, { 国家: '美国', 独立用户数: 5 }],
    }} />);
    expect(await screen.findByText(/个点位于中国范围外/)).toBeInTheDocument();
  });
});

describe('层叠上下文护栏(下拉框被遮/全屏残影)', () => {
  const src = fs.readFileSync(path.resolve(__dirname, '..', 'MapHeatmap.tsx'), 'utf-8');

  it('地图容器必须建独立层叠上下文(isolate)', () => {
    // 历史缺陷：Leaflet pane/control 的 z-index(200~1000) 逸出到页面，
    // 把 z-50 的图表类型下拉、全屏弹窗都压在地图下面
    expect(src).toMatch(/overflow-hidden\s+isolate/);
  });

  it('内部叠层只用小 z，禁止再次写高位 z-index', () => {
    // isolate 之内，热力层/图例/角标只跟自己人比；写 450/500/1000 就会逸出
    expect(src).not.toMatch(/zIndex:\s*(?:[2-9]\d\d|1000)/);
    expect(src).not.toMatch(/z-\[\s*(?:[2-9]\d\d|1000)\s*\]/);
    expect(src).toMatch(/zIndex:\s*Z_CANVAS/);
    expect(src).toMatch(/zIndex:\s*Z_BADGE/);
  });
});
