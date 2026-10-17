/**
 * MapHeatmap — 地图瓦片热力图(chat 图表卡)。
 *
 * 复用 UI 字模库「地图瓦片」(map_tile) 的瓦片配置(style_config.tileUrl/apiKey),
 * 在 Leaflet 底图上以 canvas 热力层展示点的权重分布(重叠越密越亮)。
 * 两种定位模式:
 *  - 经纬度模式:结果含 纬度/经度 列;
 *  - 地区名模式:结果含省份/城市名(如 案件按省份分布),经内置坐标表转点。
 *
 * 显式失败(fail-loud):字模库未配置地图瓦片/瓦片加载失败时给明确提示与配置指引,
 * 不静默换用第三方底图源;经纬度无效行与未定位地名均显式标注,不悄悄丢数据。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { Loader2, MapPin, AlertTriangle } from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { fetchVisComponents } from '../api/visLibrary';
import type { VisComponent } from '../api/visLibrary';
import { detectGeoColumns } from '../lib/geoColumns';
import { detectPlaceColumn, resolvePlacePoints } from '../lib/geoPoints';
import { readTileCache, writeTileCache, isTileCacheFresh, tileUrlOf, isUsableTile, isDefaultTile, probeTile, pickReachableTile, type TileCacheEntry } from '../lib/mapTileCache';

// 列识别为纯函数,抽在 lib/(单测不拉 leaflet);这里转出便于就近引用
export { detectGeoColumns } from '../lib/geoColumns';
export type { GeoColumns } from '../lib/geoColumns';

interface Props {
  data: { columns: string[]; rows: any[] };
  height?: number;
  /** 外部可指定列(缺省自动识别地区/经纬度/权重列,并提供选择框手动改) */
  config?: { latCol?: string; lngCol?: string; valueCol?: string; placeCol?: string };
}

// ── 字模库地图瓦片(内存 + localStorage 两级缓存,免每次渲染都查接口) ──

interface TileLoadResult {
  items: VisComponent[];
  /** true = 接口不可用,回退用了本地缓存(显式标注,不静默降级) */
  stale?: boolean;
  fetchedAt?: number;
}

let memTiles: TileCacheEntry | null = null;

async function loadMapTiles(): Promise<TileLoadResult> {
  // 1) 内存 → 2) localStorage:TTL 内直接用,不打接口
  const cached = memTiles ?? readTileCache();
  if (isTileCacheFresh(cached)) {
    memTiles = cached;
    return { items: cached!.items, fetchedAt: cached!.fetchedAt };
  }
  try {
    const all = await fetchVisComponents();
    const items = all.filter(c => c.category === 'map_tile' && c.is_active !== 0);
    memTiles = writeTileCache(items);
    return { items, fetchedAt: memTiles.fetchedAt };
  } catch (e) {
    // 3) 接口不可用:有过期缓存则显式降级使用(stale),无缓存才抛给上层 fail-loud
    if (cached) {
      memTiles = cached;
      return { items: cached.items, stale: true, fetchedAt: cached.fetchedAt };
    }
    throw e;
  }
}

// ── 热力配色(蓝→青→黄→红) ─────────────────────────────────────

function heatRGB(t: number): [number, number, number] {
  const ramp: { t: number; c: [number, number, number] }[] = [
    { t: 0, c: [40, 110, 255] },
    { t: 0.35, c: [0, 210, 220] },
    { t: 0.6, c: [90, 220, 60] },
    { t: 0.8, c: [255, 210, 40] },
    { t: 1, c: [255, 70, 40] },
  ];
  const x = Math.min(1, Math.max(0, t));
  for (let i = 1; i < ramp.length; i++) {
    if (x <= ramp[i].t) {
      const a = ramp[i - 1]; const b = ramp[i];
      const k = (x - a.t) / (b.t - a.t || 1);
      return [
        Math.round(a.c[0] + (b.c[0] - a.c[0]) * k),
        Math.round(a.c[1] + (b.c[1] - a.c[1]) * k),
        Math.round(a.c[2] + (b.c[2] - a.c[2]) * k),
      ];
    }
  }
  return ramp[ramp.length - 1].c;
}

export default function MapHeatmap({ data, height = 360, config }: Props) {
  const columns = data?.columns || [];
  const rows = data?.rows || [];

  const detected = useMemo(() => detectGeoColumns(columns, rows), [columns, rows]);
  const detectedPlace = useMemo(() => detectPlaceColumn(columns, rows), [columns, rows]);
  const [latCol, setLatCol] = useState('');
  const [lngCol, setLngCol] = useState('');
  const [valueCol, setValueCol] = useState('');
  const [placeCol, setPlaceCol] = useState('');
  useEffect(() => {
    // 默认优先级:列名明确含经纬度 → 经纬度模式;否则地区名可定位 → 地区名模式
    const preferCoords = detected.nameMatched;
    setLatCol(config?.latCol || detected.latCol);
    setLngCol(config?.lngCol || detected.lngCol);
    setValueCol(config?.valueCol || detected.valueCol);
    setPlaceCol(config?.placeCol || (preferCoords ? '' : detectedPlace.col));
  }, [detected, detectedPlace, config?.latCol, config?.lngCol, config?.valueCol, config?.placeCol]);

  // 字模库地图瓦片
  const [tiles, setTiles] = useState<VisComponent[]>([]);
  const [tilesLoading, setTilesLoading] = useState(true);
  const [tilesError, setTilesError] = useState('');
  const [tilesStale, setTilesStale] = useState(false);
  const [tilesFetchedAt, setTilesFetchedAt] = useState<number | undefined>(undefined);
  const [tileId, setTileId] = useState<number | null>(null);
  const [tileProbeFailed, setTileProbeFailed] = useState(false);
  const [tileSkippedDefault, setTileSkippedDefault] = useState('');
  useEffect(() => {
    let alive = true;
    setTilesLoading(true);
    loadMapTiles()
      .then(res => {
        if (!alive) return;
        setTiles(res.items);
        setTileId(prev => prev ?? null);
        setTilesError('');
        setTilesStale(!!res.stale);
        setTilesFetchedAt(res.fetchedAt);
      })
      .catch((e: any) => {
        if (!alive) return;
        // fail-loud:瓦片清单拉不到就显式报错,不静默换底图源
        setTilesError(e?.response?.data?.detail || e?.message || '地图瓦片清单加载失败');
      })
      .finally(() => { if (alive) setTilesLoading(false); });
    return () => { alive = false; };
  }, []);
  const tile = useMemo(() => tiles.find(t => t.id === tileId) || tiles.find(isUsableTile), [tiles, tileId]);

  // 默认瓦片:字模库标记 is_default 的优先(探测确认可达);无标记或默认不可达则探测竞速取可用者
  const autoPickedRef = useRef(false);
  useEffect(() => {
    if (autoPickedRef.current || tilesLoading || tiles.length === 0 || tileId != null) return;
    autoPickedRef.current = true;
    const candidates = tiles.filter(isUsableTile);
    if (candidates.length === 0) return;
    const preferred = candidates.find(isDefaultTile);
    const fallback = () => {
      const pool = preferred ? candidates.filter(t => t !== preferred) : candidates;
      void pickReachableTile(pool).then(hit => {
        if (hit) {
          setTileId(hit.id);
          if (preferred) setTileSkippedDefault(preferred.name); // 默认瓦片不可达,已显式换用可达者
        } else {
          setTileId((preferred || candidates[0]).id);
          setTileProbeFailed(true);
        }
      });
    };
    if (preferred) {
      void probeTile(preferred).then(ok => {
        if (ok) setTileId(preferred.id);
        else fallback();
      });
    } else {
      fallback();
    }
  }, [tilesLoading, tiles, tileId]);

  // 有效点 + 无效行/未定位地名统计(不悄悄丢数据)
  const usePlace = !!placeCol;
  const { points, invalidCount, unresolved } = useMemo(() => {
    if (usePlace) {
      const r = resolvePlacePoints(rows, placeCol, valueCol || undefined);
      return {
        points: r.points.map(p => ({ lat: p.lat, lng: p.lng, v: p.v })),
        invalidCount: 0,
        unresolved: r.unresolved,
      };
    }
    const pts: { lat: number; lng: number; v: number }[] = [];
    let bad = 0;
    if (!latCol || !lngCol) return { points: pts, invalidCount: 0, unresolved: [] as string[] };
    for (const r of rows) {
      const lat = Number(r[latCol]); const lng = Number(r[lngCol]);
      const v = valueCol ? Number(r[valueCol]) : 1;
      if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) {
        bad += 1;
        continue;
      }
      pts.push({ lat, lng, v: Number.isFinite(v) && v > 0 ? v : 0 });
    }
    return { points: pts, invalidCount: bad, unresolved: [] as string[] };
  }, [rows, usePlace, placeCol, latCol, lngCol, valueCol]);

  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mapRef = useRef<L.Map | null>(null);

  // Leaflet 底图 + 热力画布
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !tile || !isUsableTile(tile) || points.length === 0) return;

    const map = L.map(el, {
      center: [points[0].lat, points[0].lng],
      zoom: 9,
      zoomControl: true,
      attributionControl: false,
    });
    mapRef.current = map;
    L.tileLayer(tileUrlOf(tile), { maxZoom: 18, minZoom: 3 }).addTo(map);

    const bounds = L.latLngBounds(points.map(p => [p.lat, p.lng] as [number, number]));
    const doFit = () => {
      if (points.length > 1) map.fitBounds(bounds, { padding: [28, 28], maxZoom: 12 });
    };
    // 初始布局未就绪(全屏弹层/抽屉展开中容器可能为 0 尺寸)时延迟 fitBounds
    let fitted = el.clientHeight > 10;
    if (fitted) doFit();

    const draw = () => {
      const canvas = canvasRef.current;
      if (!canvas || !mapRef.current) return;
      const size = mapRef.current.getSize();
      canvas.width = size.x;
      canvas.height = size.y;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.globalCompositeOperation = 'lighter';
      const maxV = Math.max(1, ...points.map(p => p.v));
      for (const p of points) {
        const pt = mapRef.current.latLngToContainerPoint([p.lat, p.lng]);
        const w = p.v > 0 ? p.v / maxV : 0.15;
        const radius = 24;
        const [r, g, b] = heatRGB(w);
        const grad = ctx.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, radius);
        grad.addColorStop(0, `rgba(${r},${g},${b},${0.35 + 0.45 * w})`);
        grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(pt.x, pt.y, radius, 0, Math.PI * 2);
        ctx.fill();
      }
    };
    draw();
    map.on('move zoom resize moveend zoomend', draw);
    // 容器尺寸变化(全屏展开/抽屉/窗口缩放)后必须 invalidateSize,
    // 否则 Leaflet 按旧尺寸排瓦片 → 瓦片残影重叠/控件错位/底部留白
    const ro = new ResizeObserver(() => {
      map.invalidateSize();
      if (!fitted && el.clientHeight > 10) {
        fitted = true;
        doFit();
      }
      draw();
    });
    ro.observe(el);
    // 布局稳定后再校正一次(兜底动画期间的中间态尺寸)
    const timer = setTimeout(() => {
      map.invalidateSize();
      if (!fitted && el.clientHeight > 10) {
        fitted = true;
        doFit();
      }
      draw();
    }, 120);
    return () => {
      clearTimeout(timer);
      ro.disconnect();
      map.off('move zoom resize moveend zoomend', draw);
      map.remove();
      mapRef.current = null;
    };
  }, [tile, points]);

  const controls = (
    <div className="flex items-center gap-2 flex-wrap text-xs">
      <span className="text-muted-foreground">瓦片</span>
      <Select value={tileId != null ? String(tileId) : '__none__'} onValueChange={(v) => setTileId(v === '__none__' ? null : Number(v))}>
        <SelectTrigger className="w-[160px] h-7 text-xs"><SelectValue placeholder="选择地图瓦片" /></SelectTrigger>
        <SelectContent>
          {tiles.map(t => (
            <SelectItem key={t.id} value={String(t.id)} disabled={!isUsableTile(t)}>
              {t.name}{isDefaultTile(t) ? '（默认）' : ''}{!isUsableTile(t) ? '（缺瓦片地址/密钥）' : ''}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <span className="text-muted-foreground">地区</span>
      <Select value={placeCol || '__none__'} onValueChange={(v) => setPlaceCol(v === '__none__' ? '' : v)}>
        <SelectTrigger className="w-[120px] h-7 text-xs"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="__none__">无(用经纬度)</SelectItem>
          {columns.filter(c => c !== valueCol).map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
        </SelectContent>
      </Select>
      {!usePlace && (
        <>
          <span className="text-muted-foreground">纬度</span>
          <Select value={latCol || '__none__'} onValueChange={(v) => setLatCol(v === '__none__' ? '' : v)}>
            <SelectTrigger className="w-[110px] h-7 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">请选择</SelectItem>
              {columns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
            </SelectContent>
          </Select>
          <span className="text-muted-foreground">经度</span>
          <Select value={lngCol || '__none__'} onValueChange={(v) => setLngCol(v === '__none__' ? '' : v)}>
            <SelectTrigger className="w-[110px] h-7 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">请选择</SelectItem>
              {columns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
            </SelectContent>
          </Select>
        </>
      )}
      <span className="text-muted-foreground">权重</span>
      <Select value={valueCol || '__none__'} onValueChange={(v) => setValueCol(v === '__none__' ? '' : v)}>
        <SelectTrigger className="w-[110px] h-7 text-xs"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="__none__">无(均匀)</SelectItem>
          {columns.filter(c => c !== latCol && c !== lngCol && c !== placeCol).map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  );

  return (
    <div className="mt-3">
      {controls}

      {/* 瓦片缺失/加载失败:显式提示(不静默换底图源) */}
      {(tilesError || (!tilesLoading && tiles.length === 0)) && (
        <div className="mt-2 rounded-lg border border-amber-300/60 bg-amber-50 dark:bg-amber-950/20 px-3 py-2 text-xs text-amber-700 dark:text-amber-400 flex items-start gap-2">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>
            {tilesError
              ? `地图瓦片加载失败：${tilesError}`
              : 'UI 字模库尚未配置「地图瓦片」(map_tile)，无法渲染热力底图。请在「UI 字模库 → 地图瓦片」中配置瓦片地址后重试。'}
          </span>
        </div>
      )}

      {/* 接口不可用回退本地缓存:显式标注(可诊断的降级,不静默) */}
      {tilesStale && tiles.length > 0 && (
        <div className="mt-2 rounded-lg border border-blue-300/60 bg-blue-50 dark:bg-blue-950/20 px-3 py-2 text-xs text-blue-700 dark:text-blue-400">
          字模库接口暂不可用，当前使用本地缓存的瓦片配置
          {tilesFetchedAt ? `（${new Date(tilesFetchedAt).toLocaleTimeString()} 拉取）` : ''}，缓存过期后会重新拉取。
        </div>
      )}

      {/* 瓦片均不可用(缺地址/密钥):显式提示 */}
      {tiles.length > 0 && tiles.filter(isUsableTile).length === 0 && (
        <div className="mt-2 rounded-lg border border-amber-300/60 bg-amber-50 dark:bg-amber-950/20 px-3 py-2 text-xs text-amber-700 dark:text-amber-400 flex items-start gap-2">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>字模库中的地图瓦片均不可用（缺少瓦片地址或密钥），请在「UI 字模库 → 地图瓦片」补全配置后重试。</span>
        </div>
      )}

      {/* 自动探测未找到可达瓦片:显式提示(已默认选中首个配置可用项) */}
      {tileProbeFailed && (
        <div className="mt-2 rounded-lg border border-amber-300/60 bg-amber-50 dark:bg-amber-950/20 px-3 py-2 text-xs text-amber-700 dark:text-amber-400 flex items-start gap-2">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>
            自动探测未找到可达的瓦片（可能被网络拦截），已默认选中{tile ? `「${tile.name}」` : '首个可用瓦片'}；可手动切换瓦片或检查网络。
          </span>
        </div>
      )}

      {/* 默认瓦片不可达已换用其他瓦片:显式提示(不静默换) */}
      {tileSkippedDefault && (
        <div className="mt-2 rounded-lg border border-blue-300/60 bg-blue-50 dark:bg-blue-950/20 px-3 py-2 text-xs text-blue-700 dark:text-blue-400">
          默认瓦片「{tileSkippedDefault}」当前不可达，已自动改用{tile ? `「${tile.name}」` : '其他可达瓦片'}；可手动切换。
        </div>
      )}

      {/* 定位列未识别:显式引导选列 */}
      {!usePlace && (!latCol || !lngCol) && (
        <div className="mt-2 rounded-lg border bg-muted/40 px-3 py-2 text-xs text-muted-foreground flex items-start gap-2">
          <MapPin className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>未能从结果列中识别定位列，请在上方「地区」（省份/城市名）或「纬度/经度」选择框中指定。</span>
        </div>
      )}

      {/* 未定位地名:显式列出(不静默丢行) */}
      {unresolved.length > 0 && (
        <div className="mt-2 rounded-lg border border-amber-300/60 bg-amber-50 dark:bg-amber-950/20 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          {unresolved.length} 个地区未能定位到坐标，未在热力图展示（明细视图仍可查看全部行）：
          {unresolved.slice(0, 6).join('、')}{unresolved.length > 6 ? ' 等' : ''}
        </div>
      )}

      {invalidCount > 0 && (
        <div className="mt-2 text-[11px] text-amber-600">
          {invalidCount} 行经纬度无效或超出范围，未在热力图中展示（明细视图仍可查看全部行）
        </div>
      )}

      <div className="relative mt-2 rounded-lg border overflow-hidden" style={{ height }}>
        {tilesLoading ? (
          <div className="absolute inset-0 flex items-center justify-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />正在加载地图瓦片…
          </div>
        ) : (
          <>
            <div ref={containerRef} className="absolute inset-0" />
            <canvas ref={canvasRef} className="absolute inset-0 pointer-events-none" style={{ zIndex: 450 }} />
            {/* 热力图例 */}
            {points.length > 0 && tile && (
              <div className="absolute bottom-2 left-2 z-[500] rounded bg-card/90 border px-2 py-1 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                <span>低</span>
                <span className="h-2 w-24 rounded-sm" style={{ background: 'linear-gradient(90deg,#286eff,#00d2dc,#5adc3c,#ffd228,#ff4628)' }} />
                <span>高</span>
              </div>
            )}
            {/* 点数提示 */}
            {points.length > 0 && (
              <div className="absolute top-2 right-2 z-[500] rounded bg-card/90 border px-2 py-1 text-[10px] text-muted-foreground tabular-nums">
                {points.length} 个热力点
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
