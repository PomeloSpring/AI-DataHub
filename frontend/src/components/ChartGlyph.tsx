/**
 * 图表类型缩影符号（列表缩略图 / 模板预览共用）。
 *
 * 单独成文件：以前挂在 DashboardCanvas 上，模板弹窗要复用它就得 import DashboardCanvas，
 * 而模板弹窗又被 DashboardCanvas 所在的编辑器页面引用 —— 模块之间绕成环，
 * 页面测试 mock 掉 DashboardCanvas 时模板预览就跟着空掉。
 * 颜色取自字模库主题包（visual.palette），与真实渲染同源（ui-vis-library-style.md）。
 */
export function ChartGlyph({ chartType, palette }: { chartType: string; palette: string[] }) {
  const color = (i: number) => palette[i % (palette.length || 1)] || '#475569';
  const type = chartType.toLowerCase();
  if (type.startsWith('widget_')) {
    return <div className="flex h-full items-center"><div className="h-1/3 w-2/3 rounded-full" style={{ background: color(0), opacity: 0.55 }} /></div>;
  }
  if (/big_number|statistic|kpi|indicator/.test(type)) {
    return <div className="flex h-full flex-col justify-center gap-0.5"><div className="h-2 w-2/3 rounded-sm" style={{ background: color(0), opacity: 0.75 }} /><div className="h-1 w-1/3 rounded-sm" style={{ background: color(1), opacity: 0.4 }} /></div>;
  }
  if (/table|list/.test(type)) {
    return <div className="flex h-full flex-col justify-center gap-0.5">{[0, 1, 2].map(r => <div key={r} className="h-1 rounded-sm" style={{ background: color(r), opacity: 0.4, width: `${90 - r * 18}%` }} />)}</div>;
  }
  if (/pie|donut|ring|rose/.test(type)) {
    return <div className="flex h-full items-center justify-center"><div className="aspect-square h-3/4 rounded-full" style={{ background: `conic-gradient(${color(0)} 0 40%, ${color(1)} 40% 70%, ${color(2)} 70% 100%)`, opacity: 0.75 }} /></div>;
  }
  if (/line|area|trend|time|stock|scatter/.test(type)) {
    return <div className="flex h-full items-end"><svg viewBox="0 0 40 20" preserveAspectRatio="none" className="h-full w-full"><polyline points="2,15 12,8 22,11 38,3" fill="none" stroke={color(0)} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" opacity="0.8" /></svg></div>;
  }
  if (/gauge/.test(type)) {
    return <div className="flex h-full items-end justify-center"><div className="h-2/3 w-2/3 rounded-t-full" style={{ background: `conic-gradient(from 0.75turn, ${color(0)} 0 25%, ${color(1)} 25% 50%, transparent 50% 100%)`, opacity: 0.7 }} /></div>;
  }
  // 柱状/条形/其余默认：竖向柱阵
  return <div className="flex h-full items-end gap-1">{[40, 75, 55, 90].map((h, j) => <span key={j} className="flex-1 rounded-sm" style={{ height: `${h}%`, background: color(j), opacity: 0.6 }} />)}</div>;
}
