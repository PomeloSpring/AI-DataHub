import { describe, it, expect } from 'vitest';
import {
  readTileCache, writeTileCache, isTileCacheFresh,
  tileUrlOf, isUsableTile, isDefaultTile, sampleTileUrl,
  MAP_TILE_CACHE_KEY, MAP_TILE_CACHE_TTL_MS,
} from '../mapTileCache';
import type { VisComponent } from '../../api/visLibrary';

function fakeStorage(init: Record<string, string> = {}): Storage {
  const m = new Map(Object.entries(init));
  return {
    getItem: k => m.get(k) ?? null,
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: k => { m.delete(k); },
    clear: () => m.clear(),
    key: i => [...m.keys()][i] ?? null,
    get length() { return m.size; },
  } as Storage;
}

const tile = {
  id: 1, code: 'osm', name: 'OSM 瓦片', category: 'map_tile', style_config: { tileUrl: 'https://x/{z}/{x}/{y}.png' },
  source: 'builtin', is_builtin: 1, is_active: 1, sort_order: 0,
} as unknown as VisComponent;

describe('mapTileCache 瓦片配置持久化缓存', () => {
  it('写入后可读回,TTL 内判定为新鲜', () => {
    const s = fakeStorage();
    const entry = writeTileCache([tile], s);
    const read = readTileCache(s);
    expect(read?.items).toHaveLength(1);
    expect(read?.items[0].name).toBe('OSM 瓦片');
    expect(isTileCacheFresh(read, entry.fetchedAt + 1000)).toBe(true);
    expect(isTileCacheFresh(read, entry.fetchedAt + MAP_TILE_CACHE_TTL_MS + 1)).toBe(false);
  });

  it('损坏的缓存内容读出 null(不抛错)', () => {
    const s = fakeStorage({ [MAP_TILE_CACHE_KEY]: '{bad json' });
    expect(readTileCache(s)).toBeNull();
    const s2 = fakeStorage({ [MAP_TILE_CACHE_KEY]: '{"fetchedAt":"x"}' });
    expect(readTileCache(s2)).toBeNull();
  });

  it('storage 不可用(隐私模式)不抛错,缓存语义退化为内存级', () => {
    const entry = writeTileCache([tile], null);
    expect(entry.items).toHaveLength(1);
    expect(readTileCache(null)).toBeNull();
    expect(isTileCacheFresh(null)).toBe(false);
  });

  it('瓦片可用性:缺地址/缺密钥不可用,tileUrl 展开 {apiKey}', () => {
    const mk = (style: Record<string, any>) => ({ ...tile, style_config: style }) as unknown as VisComponent;
    expect(isUsableTile(mk({ tileUrl: 'https://x/{z}/{x}/{y}.png' }))).toBe(true);
    expect(isUsableTile(mk({ tileUrl: '' }))).toBe(false);
    expect(isUsableTile(mk({ tileUrl: 'https://x?tk={apiKey}' }))).toBe(false);       // 需密钥未配
    expect(isUsableTile(mk({ tileUrl: 'https://x?tk={apiKey}', apiKey: 'k1' }))).toBe(true);
    expect(tileUrlOf(mk({ tileUrl: 'https://x?tk={apiKey}', apiKey: 'k1' }))).toBe('https://x?tk=k1');
    // 探测样例 URL:占位符替换,{r} 视网膜置空
    expect(sampleTileUrl('https://a.{s}.x/{z}/{x}/{y}{r}.png')).toBe('https://a.a.x/4/12/6.png');
    // 默认瓦片标记:style_config.is_default
    expect(isDefaultTile(mk({ tileUrl: 'https://x/{z}/{x}/{y}.png', is_default: true }))).toBe(true);
    expect(isDefaultTile(mk({ tileUrl: 'https://x/{z}/{x}/{y}.png' }))).toBe(false);
  });
});
