import type { VisComponent } from '@/api/visLibrary';
import { sanitizeStyle } from '@/lib/dashboardDesign';
export default function VisComponentPreview({ c }: { c: VisComponent }) {
  const sc = sanitizeStyle(c.style_config), cfg = sc.config || {};
  const palette = sc.palette || cfg.colorScheme || ['#334155', '#64748b', '#94a3b8'];
  if (c.category === 'screen_background') return <div className="h-16 rounded-md border" style={{
    backgroundColor: sc.backgroundColor, backgroundImage: sc.backgroundImage, backgroundSize: sc.backgroundSize,
  }} />;
  if (c.category === 'kpi_card') return <div className="flex h-16 flex-col justify-center rounded-md border px-3" style={{
    background: cfg.cardBg, border: cfg.cardBorder, boxShadow: cfg.glow, borderRadius: cfg.borderRadius,
  }}><span className="text-[10px]" style={{ color: cfg.labelColor }}>核心指标</span><strong style={{ color: cfg.valueColor }}>1,234</strong></div>;
  if (c.category === 'layout_template') return <div className="grid h-16 grid-cols-3 gap-1 rounded-md border bg-muted/20 p-2">
    {Array.from({ length: sc.grid?.columns ? 2 : 6 }, (_, i) => <span key={i} className={`rounded-sm bg-muted-foreground/25 ${sc.grid?.columns && i === 0 ? 'col-span-2' : ''}`} />)}
  </div>;
  if (c.category === 'decoration_frame') return <div className="flex h-16 items-center rounded-md border px-3 text-xs" style={{
    background: cfg.titleBarBg, border: cfg.borderStyle, color: cfg.titleColor,
    borderBottom: cfg.showUnderline ? `2px solid ${cfg.underlineColor}` : undefined,
  }}>标题与边框</div>;
  if (c.category === 'sql_template') return <div className="flex h-16 items-center justify-center rounded-md border text-xs text-muted-foreground">仅供人工数据配置</div>;
  return <div className="flex h-16 items-end gap-1 rounded-md border p-2" style={{ background: sc.viewBg }}>
    {[45, 80, 55, 95, 65].map((h, i) => <span key={i} className="flex-1 rounded-sm" style={{ height: `${c.category === 'color_theme' ? 40 : h}%`, background: palette[i % palette.length] }} />)}
  </div>;
}
