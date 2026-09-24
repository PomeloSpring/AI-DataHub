import type { VisComponent } from '@/api/visLibrary';
import { sanitizeStyle, sanitizePack } from '@/lib/dashboardDesign';

const FALLBACK = ['#334155', '#64748b', '#94a3b8'];
const colorsOf = (value: unknown): string[] => {
  const colors = Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string' && !!v) : [];
  return colors.length ? colors : FALLBACK;
};

// 轻量、不挂 G2 的形状预览: 让 line/pie/radar/gauge/funnel 等不再都显示成柱状图。
export function ShapePreview({ type, palette, cfg }: { type: string; palette: string[]; cfg: any }) {
  palette = colorsOf(palette);
  const c = (i: number) => palette[i % palette.length];
  if (type.startsWith('widget_')) {
    const text = ({ widget_label: '文本标签', widget_text: '请输入…', widget_number: '123', widget_date: '年 / 月 / 日',
      widget_daterange: '开始日期 — 结束日期', widget_select: '请选择 ▾', widget_multi_select: '☑ 选项一  ☑ 选项二',
      widget_search: '搜索', widget_reset: '重置', widget_export: '导出' } as Record<string, string>)[type];
    return <div data-preview-type={type} className="flex h-16 items-center justify-center p-2"><span className="flex h-8 w-full items-center justify-center border px-2 text-[10px]" style={{ background: cfg.backgroundColor, color: cfg.textColor || c(0), borderColor: cfg.borderColor || c(1), borderRadius: cfg.borderRadius ?? 6 }}>{text || '控件'}</span></div>;
  }
  const line = '0,30 18,12 36,22 54,6 72,16 100,9';
  switch (type) {
    case 'line': case 'timeseries_line':
      return <svg viewBox="0 0 100 40" className="h-16 w-full" preserveAspectRatio="none">
        {cfg.areaFill && <polygon points={`0,40 ${line} 100,40`} fill={c(0)} opacity={cfg.areaOpacity ?? 0.2} />}
        {cfg.smooth ? <path d="M0 30 C8 30 10 12 18 12 S28 22 36 22 S46 6 54 6 S64 16 72 16 S90 9 100 9" fill="none" stroke={c(0)} strokeWidth={cfg.lineWidth || 2} />
          : <polyline points={line} fill="none" stroke={c(0)} strokeWidth={cfg.lineWidth || 2} strokeLinejoin="round" />}
      </svg>;
    case 'area': case 'timeseries_area':
      return <svg viewBox="0 0 100 40" className="h-16 w-full" preserveAspectRatio="none">
        <polygon points={`0,40 ${line} 100,40`} fill={c(0)} opacity={cfg.areaOpacity || 0.25} />
        <polyline points={line} fill="none" stroke={c(0)} strokeWidth={2} />
      </svg>;
    case 'pie':
      return <div className="mx-auto h-16 w-16 rounded-full" style={{
        background: `conic-gradient(${palette.slice(0, 5).map((p, i, a) => `${p} ${i * (100 / Math.min(a.length, 5))}% ${(i + 1) * (100 / Math.min(a.length, 5))}%`).join(',')})`,
        maskImage: `radial-gradient(circle, transparent ${((cfg.innerRadius ?? 0.55) * 100).toFixed(0)}%, #000 ${((cfg.innerRadius ?? 0.55) * 100 + 1).toFixed(0)}%)`,
        WebkitMaskImage: `radial-gradient(circle, transparent ${((cfg.innerRadius ?? 0.55) * 100).toFixed(0)}%, #000 ${((cfg.innerRadius ?? 0.55) * 100 + 1).toFixed(0)}%)`,
      }} />;
    case 'radar':
      return <svg viewBox="0 0 100 40" className="h-16 w-full">
        <polygon points="50,4 88,20 70,36 30,36 12,20" fill={c(0)} opacity={cfg.areaOpacity || 0.25} stroke={c(0)} strokeWidth={1.5} />
        <polygon points="50,12 74,22 62,32 38,32 26,22" fill="none" stroke={c(1)} strokeWidth={1} opacity={0.6} />
      </svg>;
    case 'gauge':
      return <svg viewBox="0 0 100 50" className="h-16 w-full"><path d="M15 42 A35 35 0 0 1 85 42" fill="none" stroke={c(1)} strokeWidth="8" opacity="0.25" /><path d="M15 42 A35 35 0 0 1 74 17" fill="none" stroke={c(0)} strokeWidth="8" /><path d="M50 42 L72 18" stroke={c(0)} strokeWidth="2" /><circle cx="50" cy="42" r="3" fill={c(0)} /></svg>;
    case 'funnel':
      return <div className="flex h-16 flex-col items-center justify-center gap-1">
        {[100, 78, 56, 34].map((w, i) => <span key={i} style={{ width: `${w}%`, height: 12, background: c(i), clipPath: 'polygon(0 0, 100% 0, 85% 100%, 15% 100%)' }} />)}
      </div>;
    case 'scatter': case 'bubble':
      return <svg viewBox="0 0 100 40" className="h-16 w-full">
        {[[15, 28, 3], [35, 14, 4], [52, 24, 2.5], [70, 10, 5], [86, 20, 3.5]].map(([x, y, r], i) =>
          <circle key={i} cx={x} cy={y} r={r} fill={c(i)} opacity={0.85} />)}
      </svg>;
    case 'heatmap': case 'calendar_heatmap':
      return <div className="grid h-16 grid-cols-5 gap-1 p-1">
        {Array.from({ length: 15 }, (_, i) => <span key={i} className="rounded-sm" style={{ background: c(Math.floor(i / 5)), opacity: 0.35 + (i % 5) * 0.15 }} />)}
      </div>;
    case 'word_cloud':
      return <div className="flex h-16 flex-wrap items-center justify-center gap-2 p-1 text-[10px] font-semibold">
        {['数据', '分析', '指标', '趋势', '维度'].map((w, i) => <span key={w} style={{ color: c(i), fontSize: 9 + i * 2 }}>{w}</span>)}
      </div>;
    case 'sankey':
      return <svg viewBox="0 0 100 40" className="h-16 w-full">
        {[0, 1, 2].map(i => <path key={i} d={`M0,${8 + i * 10} C40,${8 + i * 10} 60,${20 - i * 6} 100,${20 - i * 6}`} fill="none" stroke={c(i)} strokeWidth={4} opacity={0.7} />)}
      </svg>;
    case 'tree':
      return <svg viewBox="0 0 100 50" className="h-16 w-full"><path d="M50 8 V24 H15 V40 M50 24 V40 M50 24 H85 V40" fill="none" stroke={c(1)} />{[[50, 8], [15, 40], [50, 40], [85, 40]].map(([x, y], i) => <circle key={i} cx={x} cy={y} r="5" fill={c(i)} />)}</svg>;
    case 'treemap':
      return <svg viewBox="0 0 100 50" className="h-16 w-full">{[[0, 0, 56, 50], [58, 0, 42, 28], [58, 30, 24, 20], [84, 30, 16, 20]].map(([x, y, w, h], i) => <rect key={i} x={x} y={y} width={w} height={h} fill={c(i)} />)}</svg>;
    case 'boxplot':
      return <svg viewBox="0 0 100 50" className="h-16 w-full">{[20, 50, 80].map((x, i) => <g key={x} stroke={c(i)}><path d={`M${x} 4 V46 M${x - 5} 4 H${x + 5} M${x - 5} 46 H${x + 5}`} /><rect x={x - 9} y={13 + i * 2} width="18" height="22" fill={c(i)} fillOpacity="0.3" /><path d={`M${x - 9} 25 H${x + 9}`} /></g>)}</svg>;
    case 'waterfall':
      return <svg viewBox="0 0 100 50" className="h-16 w-full">{[[2, 25, 22], [22, 15, 10], [42, 8, 7], [62, 8, 14], [82, 22, 25]].map(([x, y, h], i) => <g key={i}><rect x={x} y={y} width="15" height={h} fill={c(i)} />{i < 4 && <path d={`M${x + 15} ${y} h5`} stroke={c(1)} strokeDasharray="2 1" />}</g>)}</svg>;
    case 'rose':
      return <svg viewBox="0 0 60 60" className="h-16 w-full">{[24, 18, 28, 16, 22, 26].map((r, i) => <path key={i} d={`M30 30 L30 ${30 - r} A${r} ${r} 0 0 1 ${30 + r * 0.866} ${30 - r * 0.5} Z`} transform={`rotate(${i * 60} 30 30)`} fill={c(i)} stroke="white" strokeWidth="0.5" />)}</svg>;
    case 'radial_bar':
      return <svg viewBox="0 0 60 60" className="h-16 w-full">{[24, 17, 10].map((r, i) => <circle key={r} cx="30" cy="30" r={r} fill="none" stroke={c(i)} strokeWidth="5" strokeDasharray={`${r * (4.8 - i)} ${r * 7}`} transform="rotate(-90 30 30)" />)}</svg>;
    case 'table_value': case 'table':
      return <div className="grid h-16 grid-cols-3 gap-px overflow-hidden rounded border text-center text-[9px]" style={{ color: c(0) }}>{['维度', '指标', '占比', '华东', '128', '48%', '华南', '96', '36%'].map((v, i) => <span key={i} className="border-b p-1" style={{ background: i < 3 ? `${c(0)}18` : undefined }}>{v}</span>)}</div>;
    case 'text_display': case 'big_number_trend':
      return <div className="flex h-16 items-center justify-around text-xs" style={{ color: cfg.valueColor || c(0) }}><div><small>核心指标</small><strong className="block text-xl">1,234</strong></div>{type === 'big_number_trend' && <svg viewBox="0 0 100 40" className="h-10 w-1/2"><polyline points={line} fill="none" stroke={c(0)} strokeWidth="3" /></svg>}</div>;
    case 'china_map': case 'world_map':
      return <svg viewBox="0 0 100 50" className="h-16 w-full"><path d={type === 'china_map' ? 'M6 15 L18 12 24 5 33 14 42 13 47 18 60 13 71 4 82 8 93 4 90 17 78 22 81 29 69 35 62 43 50 39 42 44 30 37 24 27 12 29 Z' : 'M3 10 L25 4 40 12 32 23 21 20 18 28 8 22 Z M26 27 L42 31 38 44 31 48 Z M48 10 L67 4 95 12 89 23 73 24 65 17 57 23 49 20 Z M51 25 L67 25 64 41 55 43 Z M79 35 L94 33 98 42 83 45 Z'} fill={c(0)} fillOpacity="0.65" stroke={c(1)} /></svg>;
    case 'bar': case 'timeseries_bar':
      return <div className="flex h-16 items-end gap-1 rounded-md p-1">
        {[45, 80, 55, 95, 65].map((h, i) => <span key={i} className="flex-1 rounded-sm" style={{
          height: `${h}%`, background: c(i), borderRadius: cfg.borderRadius ?? 3,
          backgroundImage: cfg.gradient ? `linear-gradient(180deg, ${c(i)}, ${c(i + 1)})` : undefined,
        }} />)}
      </div>;
    default:
      return <div className="flex h-16 items-center justify-center text-xs text-muted-foreground">{type || '通用样式'}</div>;
  }
}

export default function VisComponentPreview({ c }: { c: VisComponent }) {
  const sc = sanitizeStyle(c.style_config), cfg = sc.config || {};
  const palette = colorsOf(sc.palette || cfg.colorScheme);
  if (c.category === 'theme_pack') {
    const pk = sanitizePack(c.style_config);
    const pal = colorsOf(pk.palette);
    return <div className="overflow-hidden rounded-md border p-2" style={{
      backgroundColor: pk.background?.backgroundColor, backgroundImage: pk.background?.backgroundImage, backgroundSize: pk.background?.backgroundSize,
    }}>
      <div className="flex gap-1">{pal.slice(0, 6).map((col: string, i: number) => <span key={i} className="h-1.5 flex-1 rounded-sm" style={{ background: col }} />)}</div>
      <div className="mt-2 grid grid-cols-3 gap-1">{['bar', 'line', 'pie'].map(type => <div key={type} className="overflow-hidden rounded p-1" style={{ background: pk.card?.cardBg, border: pk.card?.cardBorder, borderRadius: pk.card?.cardRadius }}><ShapePreview type={type} palette={pal} cfg={{ ...pk.charts?.default, ...pk.charts?.[type] }} /></div>)}</div>
    </div>;
  }
  if (c.category === 'screen_background') return <div className="h-16 rounded-md border" style={{
    backgroundColor: sc.backgroundColor, backgroundImage: sc.backgroundImage, backgroundSize: sc.backgroundSize,
  }} />;
  if (c.category === 'kpi_card') return <div className="flex h-16 flex-col justify-center rounded-md border px-3" style={{
    background: cfg.cardBg, border: cfg.cardBorder, boxShadow: cfg.glow, borderRadius: cfg.cardRadius ?? cfg.borderRadius,
  }}><span className="text-[10px]" style={{ color: cfg.labelColor }}>核心指标</span><strong style={{ color: cfg.valueColor }}>1,234</strong></div>;
  if (c.category === 'layout_template') return <div className="grid h-16 grid-cols-3 gap-1 rounded-md border bg-muted/20 p-2">
    {Array.from({ length: sc.grid?.columns ? 2 : 6 }, (_, i) => <span key={i} className={`rounded-sm bg-muted-foreground/25 ${sc.grid?.columns && i === 0 ? 'col-span-2' : ''}`} />)}
  </div>;
  if (c.category === 'decoration_frame') return <div className="flex h-16 items-center rounded-md border px-3 text-xs" style={{
    background: cfg.titleBarBg, border: cfg.borderStyle, color: cfg.titleColor,
    borderBottom: cfg.showUnderline ? `2px solid ${cfg.underlineColor}` : undefined,
  }}>标题与边框</div>;
  if (c.category === 'sql_template') return <div className="flex h-16 items-center justify-center rounded-md border text-xs text-muted-foreground">仅供人工数据配置</div>;
  if (c.category === 'color_theme') return <div className="flex h-16 gap-1 rounded border p-2">{palette.map((color, i) => <span key={i} className="flex-1 rounded" style={{ background: color }} />)}</div>;
  // chart_style / color_theme: 按图表类型绘制对应形状
  return <div data-preview-type={c.chart_type || 'bar'} className="rounded-md border p-1" style={{ background: sc.viewBg }}>
    <ShapePreview type={c.chart_type || 'bar'} palette={palette} cfg={cfg} />
  </div>;
}
