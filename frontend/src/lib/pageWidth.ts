/**
 * 页面正文容器的宽度口径（唯一一份，门户阅读页 / 内容中心预览页 / 报告查看页共用）。
 *
 * 为什么不能全站一个 max-w-[900px]：窄栏是为**连续长文**服务的（一行 60—75 字最好读），
 * 但富版式内容不是连续长文——公司介绍的卡片栅格、时间线，数据报告的宽表格与图表，
 * 被塞进中间一列会出现两种事故：栅格塌成一列、宽表横向滚动条常驻。
 * 反过来让长文铺满 2K 屏，一行三百字，眼睛跟不上。
 *
 * 所以按内容形态分流，而不是给每页各写一套：
 * - `html` 版式（卡片/时间轴/栅格自己决定占位）与报告产物 → `WIDE`，尽量充满页面；
 * - `markdown` 长文、外链与附件稿的导读 → `READING`，保持窄栏。
 *
 * 上限取 2200px 而不是无限，宽度主体用百分比（94%）而不是单一像素值：
 * 只写 max-w-[Npx] 时，若 N 大于当前视口就永远不触发（改了等于没改）；百分比保证
 * 1440 与 2560 上都真的“填满可用宽度”，像素值只做超宽屏的兜底，min-w-0 避免 flex 子项撑破。
 * 需要更宽的图（大屏、桑基图）由内容自己控制，不该由外层容器负责。
 */

/** 富版式与报告：按百分比填满可用页面，超宽屏靠像素上限兜底 */
export const WIDE = 'mx-auto w-[94%] min-w-0 max-w-[2200px]';
/** 连续长文：窄栏阅读 */
export const READING = 'mx-auto max-w-[900px] px-5';

/** 内容形态 → 容器类名（html 走宽版，其余按长文口径） */
export function containerForKind(kind?: string | null): string {
  return kind === 'html' ? WIDE : READING;
}

/** 同一口径的机器可读标记，供用例断言（避免测试去匹配 Tailwind 类名字符串） */
export function widthModeForKind(kind?: string | null): 'wide' | 'reading' {
  return kind === 'html' ? 'wide' : 'reading';
}
