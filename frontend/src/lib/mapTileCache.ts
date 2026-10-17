/**
 * 地图瓦片配置缓存 — 字模库 map_tile 清单的本地持久化(TTL)。
 *
 * 瓦片配置的权威源是字模库(adh_vis_components,DB);渲染地图不应每次都打
 * /vis-library/components 接口,这里做两级缓存:
 *   内存(MapHeatmap 模块级)→ localStorage(本文件,TTL 内免请求)→ 接口。
 * 接口不可用时可回退到过期缓存,但调用方必须显式标注(stale),不静默用陈旧配置。
 */

import type { VisComponent } from '../api/visLibrary';

// v2:新增 is_default 默认瓦片标记后升级键名,旧缓存立即失效(一次性)
export const MAP_TILE_CACHE_KEY = 'adh_map_tiles_cache_v2';
/** 缓存有效期:10 分钟内(含页面刷新)直接用缓存,不打接口 */
export const MAP_TILE_CACHE_TTL_MS = 10 * 60 * 1000;

export interface TileCacheEntry {
  fetchedAt: number;
  items: VisComponent[];
}

function defaultStorage(): Storage | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null;
  } catch {
    return null; // 隐私模式等场景 localStorage 不可读写
  }
}

export function readTileCache(storage: Storage | null = defaultStorage()): TileCacheEntry | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(MAP_TILE_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed.fetchedAt !== 'number' || !Array.isArray(parsed.items)) return null;
    return parsed as TileCacheEntry;
  } catch {
    return null;
  }
}

export function writeTileCache(items: VisComponent[], storage: Storage | null = defaultStorage()): TileCacheEntry {
  const entry: TileCacheEntry = { fetchedAt: Date.now(), items };
  if (storage) {
    try {
      storage.setItem(MAP_TILE_CACHE_KEY, JSON.stringify(entry));
    } catch {
      // 写不进 localStorage(容量/隐私模式)不影响主链路:内存缓存仍生效
    }
  }
  return entry;
}

export function isTileCacheFresh(entry: TileCacheEntry | null, now: number = Date.now()): boolean {
  return !!entry && now - entry.fetchedAt < MAP_TILE_CACHE_TTL_MS;
}

// ── 瓦片可用性:配置合法 + 网络可达探测(选一个能用的作默认) ──

/** 展开 {apiKey} 后的瓦片地址(其余占位符 {s}/{r}/{x}/{y}/{z} 由 Leaflet 处理) */
export function tileUrlOf(c: VisComponent): string {
  const sc = (c.style_config || {}) as any;
  return String(sc.tileUrl || '').replace('{apiKey}', String(sc.apiKey || ''));
}

/** 配置级可用:有瓦片地址,且需要密钥时已配密钥(否则拼出的 URL 不可用) */
export function isUsableTile(c: VisComponent): boolean {
  const sc = (c.style_config || {}) as any;
  const raw = String(sc.tileUrl || '');
  if (!raw) return false;
  if (raw.includes('{apiKey}') && !String(sc.apiKey || '').trim()) return false;
  return true;
}

/** 默认瓦片标记:字模库 style_config.is_default(管理页「设为默认瓦片」写入);多个标记者取清单首个 */
export function isDefaultTile(c: VisComponent): boolean {
  const sc = (c.style_config || {}) as any;
  return sc?.is_default === true;
}

/** 探测用样例瓦片(中国区域 z=4)替换占位符,{r} 视网膜占位置空 */
export function sampleTileUrl(url: string): string {
  return url
    .replace(/\{s\}/g, 'a')
    .replace(/\{r\}/g, '')
    .replace(/\{z\}/g, '4')
    .replace(/\{x\}/g, '12')
    .replace(/\{y\}/g, '6');
}

// 探测结果内存缓存:同一次会话不重复探测同一瓦片
const probeCache = new Map<number, boolean>();

/** 样例瓦片图片能否加载(限时);可达=true。结果按瓦片 id 缓存。 */
export function probeTile(c: VisComponent, timeoutMs = 3500): Promise<boolean> {
  const cached = probeCache.get(c.id);
  if (cached !== undefined) return Promise.resolve(cached);
  return new Promise(resolve => {
    const url = sampleTileUrl(tileUrlOf(c));
    if (!url) { probeCache.set(c.id, false); resolve(false); return; }
    const img = new Image();
    const finish = (ok: boolean) => {
      clearTimeout(timer);
      probeCache.set(c.id, ok);
      resolve(ok);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    img.onload = () => finish(true);
    img.onerror = () => finish(false);
    img.src = url;
  });
}

/** 并发探测候选瓦片,取最先可达者作为默认;全部不可达返回 null(调用方须显式提示) */
export async function pickReachableTile(candidates: VisComponent[]): Promise<VisComponent | null> {
  if (candidates.length === 0) return null;
  return new Promise(resolve => {
    let pending = candidates.length;
    let done = false;
    for (const c of candidates) {
      probeTile(c).then(ok => {
        if (done) return;
        if (ok) { done = true; resolve(c); return; }
        pending -= 1;
        if (pending === 0) { done = true; resolve(null); }
      });
    }
  });
}
