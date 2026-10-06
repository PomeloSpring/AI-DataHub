/**
 * 图表主题工具 — 主题令牌读取与系列色板(DashboardChart / AnimatedTimeChart 共用单一真值源)。
 */

export const LIGHT_COLORS = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc'];
export const DARK_COLORS = ['#4992ff', '#7cffb2', '#fddd60', '#ff6e76', '#58d9f9', '#05c091', '#ff8a45', '#8d48e3', '#dd79ff'];

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
