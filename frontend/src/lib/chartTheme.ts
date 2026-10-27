/**
 * 图表主题工具 — 主题令牌读取与系列色板(DashboardChart / AnimatedTimeChart 共用单一真值源)。
 */

/**
 * 兜色板（仅在 CSS 变量读不到时使用，如 SSR/离屏 canvas）：
 * 与 AS 风格 tokens.js 的 pie 8 色板同源（主色取 bar/line 强调色），
 * 不得回到 ECharts 默认盘——那会让「无 CSS 变量」与「有 CSS 变量」两种场合穿不同的衣。
 */
export const LIGHT_COLORS = ['#0037cb', '#38ad2a', '#e56f22', '#08997e', '#7c4dff', '#d81b60', '#00a3c4', '#8a939f', '#0b6ae5', '#2aad95'];
export const DARK_COLORS = ['#809be5', '#5fbf4a', '#e57d39', '#65c2b1', '#a48bff', '#ec5b90', '#4fc3de', '#a0a2a5', '#3c7fd6', '#3ead99'];

/** 运行时读取主题 CSS 变量为可用颜色(canvas 无法解析 var(),需取实际值)。 */
export function readToken(name: string, fallback: string): string {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    if (!v) return fallback;
    // G2 的颜色解析器不支持 CSS Color 4 空格语法，转为兼容的逗号语法。
    const [channels, alpha] = v.split('/').map(part => part.trim());
    const hsl = channels.split(/[\s,]+/).join(', ');
    return alpha ? `hsla(${hsl}, ${alpha})` : `hsl(${hsl})`;
  } catch { return fallback; }
}

/** 图表系列色板: 读取 --chart-1..10 主题令牌,随主题联动;缺失时回退经典盘。 */
export function readChartPalette(isDark: boolean): string[] {
  const fb = isDark ? DARK_COLORS : LIGHT_COLORS;
  return Array.from({ length: 10 }, (_, i) => readToken(`--chart-${i + 1}`, fb[i % fb.length]));
}
