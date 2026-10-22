/**
 * MapHeatmap — 地图瓦片热力图(chat 图表卡)。
 *
 * 复用 UI 字模库「地图瓦片」(map_tile) 的瓦片配置(style_config.tileUrl/apiKey),
 * 在 Leaflet 底图上以 canvas 热力层展示点的权重分布(重叠越密越亮)。
 * 两种定位模式(优先级从高到低):
 *  - 经纬度模式:结果含 纬度/经度 列(列名明确才自动启用);
 *  - 地区名模式:结果含国家/省份/城市名(如 案件按省份分布、流量按国家分布),
 *    经内置坐标表(中国省市 + 世界国家)转点。
 *
 * 视图几何:经度按环上最大间隔展开(unwrapLongitudes),中美分布以太平洋为中心,
 * 不会被 naive 矩形拉到非洲居中;minZoom=2 允许缩到看到整个地球。
 *
 * 显式失败(fail-loud):字模库未配置地图瓦片/瓦片加载失败时给明确提示与配置指引,
 * 不静默换用第三方底图源;经纬度无效行、未定位地名、以及“拿指标列当经纬度”
 * 均显式标注并拒绘(历史缺陷:国家流量被渲染到几内亚湾),不悄悄给出错答案。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { Loader2, MapPin, AlertTriangle } from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { fetchVisComponents } from '../api/visLibrary';
import type { VisComponent } from '../api/visLibrary';
import { detectGeoColumns, isNum, unwrapLongitudes } from '../lib/geoColumns';
import { detectPlaceColumn, resolvePlacePoints } from '../lib/geoPoints';
import { readTileCache, writeTileCache, isTileCacheFresh, tileUrlOf, tileZoomOf, isUsableTile, isDefaultTile, probeTile, pickReachableTile, type TileCacheEntry } from '../lib/mapTileCache';
import { Button } from '@/components/ui/button';

// 列识别为纯函数,抽在 lib/(单测不拉 leaflet);这里转出便于就近引用
export { detectGeoColumns } from '../lib/geoColumns';
export type { GeoColumns } from '../lib/geoColumns';

/** 热力点(权重 v) */
type GeoPoint = { lat: number; lng: number; v: number };

/**
 * 局部层级(仅在地图容器内比较)。
 *
 * 容器用 `isolate` 建独立层叠上下文后，这里的小 z 只跟自己人比；历史上写
 * 450/500/1000 会**逸出**到页面层叠上下文，把 z-50 的下拉框、全屏弹窗盖住
 * （现象：图表类型下拉被地图遮、全屏后卡片内地图残影盖在弹窗上无法消除）。
 */
const Z_CANVAS = 1;   // 热力层(在瓦片之上、角标之下)
const Z_BADGE = 2;    // 图例 / 点数角标
const Z_EMPTY = 3;    // 无有效点时的原因提示

/** 中国陆域外包框(含海南海/台),用于「中国地图」图型的拟合与范围外提示 */
const CHINA_BOX = { minLat: 3.5, maxLat: 53.6, minLng: 73, maxLng: 135.1 };
const inChinaBox = (lat: number, lng: number) =>
  lat >= CHINA_BOX.minLat && lat <= CHINA_BOX.maxLat && lng >= CHINA_BOX.minLng && lng <= CHINA_BOX.maxLng;

interface Props {
  data: { columns: string[]; rows: any[] };
  height?: number;
  /** 外部可指定列(缺省自动识别地区/经纬度/权重列,并提供选择框手动改) */
  config?: { latCol?: string; lngCol?: string; valueCol?: string; placeCol?: string };
  /** 地图范围:china_map 传 'china'(拟合中国,国境外的点显式提示换世界地图) */
  scope?: 'china' | 'world' | 'auto';
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

export default function MapHeatmap({ data, height = 360, config, scope = 'auto' }: Props) {
  const columns = data?.columns || [];
  const rows = data?.rows || [];

  const detected = useMemo(() => detectGeoColumns(columns, rows), [columns, rows]);
  const detectedPlace = useMemo(() => detectPlaceColumn(columns, rows), [columns, rows]);
  const numericCols = useMemo(
    () => (columns.length > 0 && rows.length > 0 ? columns.filter(c => rows.every(r => isNum(r[c]))) : []),
    [columns, rows],
  );
  const [latCol, setLatCol] = useState('');
  const [lngCol, setLngCol] = useState('');
  const [valueCol, setValueCol] = useState('');
  const [placeCol, setPlaceCol] = useState('');
  /** 经纬度列来源:name=列名明确 / guess=取值范围兜底(待用户确认) / manual=用户选定或外部指定 */
  const [coordSource, setCoordSource] = useState<'name' | 'guess' | 'manual'>('guess');
  useEffect(() => {
    // 默认优先级:列名明确含经纬度 → 经纬度模式;否则地区名可定位(省市/国家) → 地区名模式
    const preferCoords = detected.nameMatched;
    setLatCol(config?.latCol || detected.latCol);
    setLngCol(config?.lngCol || detected.lngCol);
    // 地区名模式下被“范围兜底”吃掉的指标列应回到权重(否则权重永远选不到)
    setValueCol(config?.valueCol || (preferCoords ? detected.valueCol : numericCols[0] || ''));
    setPlaceCol(config?.placeCol || (preferCoords ? '' : detectedPlace.col));
    setCoordSource(config?.latCol || config?.lngCol ? 'manual'
      : (detected.nameMatched ? 'name' : 'guess'));
  }, [detected, detectedPlace, numericCols, config?.latCol, config?.lngCol, config?.valueCol, config?.placeCol]);

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
  /** 经纬度列仅由取值范围兜底猜出且无地名列可用 → 指标值会被当成地球坐标,不得静默渲染 */
  const guessedCoords = !usePlace && coordSource === 'guess' && !!latCol && !!lngCol;
  const { points, invalidCount, unresolved } = useMemo(() => {
    if (guessedCoords) return { points: [] as GeoPoint[], invalidCount: 0, unresolved: [] as string[] };
    if (usePlace) {
      const r = resolvePlacePoints(rows, placeCol, valueCol || undefined);
      return {
        points: r.points.map(p => ({ lat: p.lat, lng: p.lng, v: p.v })),
        invalidCount: 0,
        unresolved: r.unresolved,
      };
    }
    const pts: GeoPoint[] = [];
    let bad = 0;
    if (!latCol || !lngCol) return { points: pts, invalidCount: 0, unresolved: [] as string[] };
    for (const r of rows) {
      const lat = Number(r[latCol]); const lng = Number(r[lngCol]);
      const v = valueCol ? Number(r[valueCol]) : 1;
      if (!Number.isFinite(lat) || Math.abs(lat) > 90 || !Number.isFinite(lng) || Math.abs(lng) > 180) {
        bad += 1;
        continue;
      }
      pts.push({ lat, lng, v: Number.isFinite(v) && v > 0 ? v : 0 });
    }
    return { points: pts, invalidCount: bad, unresolved: [] as string[] };
  }, [rows, usePlace, guessedCoords, placeCol, latCol, lngCol, valueCol]);

  /** 视图坐标:经度按环上最大间隔展开(跨反经线的数据不再被拉到非洲居中) */
  const viewPoints = useMemo(
    () => points.map((p, i) => ({ ...p, viewLng: unwrapLongitudes(points.map(q => q.lng))[i] })),
    [points],
  );
  /** 「中国地图」图型下落在国境外的点(不隐去,只提醒换世界地图) */
  const outsideChina = scope === 'china'
    ? viewPoints.filter(p => !inChinaBox(p.lat, p.viewLng)).length : 0;

  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  /** true = 已缩到瓦片源最小层级仍装不下全部点(显式提示,不静默裁视野) */
  const [zoomLimited, setZoomLimited] = useState(false);

  // Leaflet 底图 + 热力画布
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !tile || !isUsableTile(tile) || viewPoints.length === 0) return;
    setZoomLimited(false);
    // 缩放区间跟随瓦片源（见 tileZoomOf）
    const zoomOf = tileZoomOf(tile);

    const map = L.map(el, {
      center: [viewPoints[0].lat, viewPoints[0].viewLng],
      zoom: 4,
      minZoom: zoomOf.minZoom,
      maxZoom: zoomOf.maxZoom,
      zoomControl: true,
      attributionControl: false,
      worldCopyJump: true,
    });
    mapRef.current = map;
    // 缩放下限与瓦片源对齐（不是全局常数）：源不提供的层级会整块空白
    // （历史缺陷：下限写死 2，高德 z<3 无图，中美分布 fit 到 z=2 时画布全白）；
    // 高德 z=3 时 160° 跨度约 910px，普通卡片宽度仍可同框中美。
    L.tileLayer(tileUrlOf(tile), { minZoom: zoomOf.minZoom, maxZoom: zoomOf.maxZoom }).addTo(map);

    // 「中国地图」图型:优先拟合国境内的点(国境外的点不隐去,只提醒换世界地图)
    const inCn = scope === 'china' ? viewPoints.filter(p => inChinaBox(p.lat, p.viewLng)) : viewPoints;
    const fitPts = inCn.length > 0 ? inCn : viewPoints;
    const bounds = L.latLngBounds(fitPts.map(p => [p.lat, p.viewLng] as [number, number]));
    const doFit = () => {
      if (fitPts.length > 1) {
        map.fitBounds(bounds, { padding: [28, 28], maxZoom: 12 });
        // 瓦片源最小层级装不下全部点时不能默默裁视野：显式提示换可到更小层级的瓦片
        if (!map.getBounds().contains(bounds)) setZoomLimited(true);
      }
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
      const maxV = Math.max(1, ...viewPoints.map(p => p.v));
      for (const p of viewPoints) {
        const pt = mapRef.current.latLngToContainerPoint([p.lat, p.viewLng]);
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
  }, [tile, viewPoints, scope]);

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
          <Select value={latCol || '__none__'} onValueChange={(v) => { setLatCol(v === '__none__' ? '' : v); setCoordSource('manual'); }}>
            <SelectTrigger className="w-[110px] h-7 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">请选择</SelectItem>
              {columns.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
            </SelectContent>
          </Select>
          <span className="text-muted-foreground">经度</span>
          <Select value={lngCol || '__none__'} onValueChange={(v) => { setLngCol(v === '__none__' ? '' : v); setCoordSource('manual'); }}>
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
          {/* 地区名模式下纬度/经度只是待确认候选,不得把它们从权重选项里剔掉 */}
          {columns.filter(c => c !== placeCol && (usePlace || (c !== latCol && c !== lngCol)))
            .map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
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
          <span>未能从结果列中识别定位列，请在上方「地区」（国家/省份/城市名）或「纬度/经度」选择框中指定。</span>
        </div>
      )}

      {/* 经纬度列仅由取值范围猜出:画出来必然是错的(指标值当坐标)—— 显式阻断 + 给出出路 */}
      {guessedCoords && (
        <div className="mt-2 rounded-lg border border-amber-300/60 bg-amber-50 dark:bg-amber-950/20 px-3 py-2 text-xs text-amber-700 dark:text-amber-400 flex items-start gap-2 flex-wrap">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>
            结果里没有真正的经纬度列（只是按取值范围把「{latCol}」猜作纬度、「{lngCol}」猜作经度）。
            指标值不是地球坐标，按它画点会落在错误位置，因此暂不渲染热力点。
            请在上方「地区」选地名列（国家/省份/城市），或改选真实的纬度/经度列。
          </span>
          <Button variant="outline" size="sm" className="h-6 px-2 text-[11px]"
            onClick={() => setCoordSource('manual')}>确认这两列就是经纬度</Button>
        </div>
      )}

      {/* 「中国地图」图型但有点在国境外:不隐去,提醒换图型 */}
      {outsideChina > 0 && (
        <div className="mt-2 rounded-lg border border-blue-300/60 bg-blue-50 dark:bg-blue-950/20 px-3 py-2 text-xs text-blue-700 dark:text-blue-400">
          当前为「中国地图」，有 {outsideChina} 个点位于中国范围外（已画在图上但可能在视野外），
          切换图型为「世界地图」可看全貌。
        </div>
      )}

      {/* 已缩到瓦片源最小层级仍装不下:显式提示(换瓦片/缩小到更小层级的源) */}
      {zoomLimited && (
        <div className="mt-2 rounded-lg border border-amber-300/60 bg-amber-50 dark:bg-amber-950/20 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          当前瓦片已缩到它支持的最小层级，仍无法同框全部热力点（部分点在视野外）。
          可切换到支持更小层级的瓦片（如 Carto/OSM 可到全球），或点选放大查看。
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

      {/* isolate:给地图建独立层叠上下文 —— Leaflet 内部 pane/control 的 z-index 与
          热力层只在本容器内比较，不得逸出到页面去压 z-50 的下拉框/全屏弹窗 */}
      <div className="relative mt-2 rounded-lg border overflow-hidden isolate" style={{ height }}>
        {tilesLoading ? (
          <div className="absolute inset-0 flex items-center justify-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />正在加载地图瓦片…
          </div>
        ) : (
          <>
            <div ref={containerRef} className="absolute inset-0" />
            {/* 无有效点(待确认定位列/地名全未定位):图面不空转,给出可见原因 */}
            {points.length === 0 && (
              <div className="absolute inset-0 flex items-center justify-center px-6 text-center text-xs text-muted-foreground" style={{ zIndex: Z_EMPTY }}>
                {guessedCoords
                  ? '定位列待确认：上方选定地区列或真实经纬度列后展示热力分布'
                  : '无可定位的数据行：请检查「地区」列选得对不对（地名需能映射到坐标）'}
              </div>
            )}
            <canvas ref={canvasRef} className="absolute inset-0 pointer-events-none" style={{ zIndex: Z_CANVAS }} />
            {/* 热力图例 */}
            {points.length > 0 && tile && (
              <div className="absolute bottom-2 left-2 rounded bg-card/90 border px-2 py-1 flex items-center gap-1.5 text-[10px] text-muted-foreground" style={{ zIndex: Z_BADGE }}>
                <span>低</span>
                <span className="h-2 w-24 rounded-sm" style={{ background: 'linear-gradient(90deg,#286eff,#00d2dc,#5adc3c,#ffd228,#ff4628)' }} />
                <span>高</span>
              </div>
            )}
            {/* 点数提示 */}
            {points.length > 0 && (
              <div className="absolute top-2 right-2 rounded bg-card/90 border px-2 py-1 text-[10px] text-muted-foreground tabular-nums" style={{ zIndex: Z_BADGE }}>
                {points.length} 个热力点
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
