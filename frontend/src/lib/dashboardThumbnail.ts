import { getCanvas } from './dashboardDesign';
import { pageCanvasSize, resolvePageLayout, type PageItem } from './dashboardPageLayout';
import type { Dashboard } from '@/stores/dashboardStore';

/**
 * 缩略图几何的唯一算点：是否 page 模式 + 画布尺寸 + 截断后的框高。
 *
 * 渲染器（DashboardThumbnail）与固定框（DashboardThumbnailFrame）必须调同一个函数：
 * 两套算法一旦分叉（page 模式用网格算、大屏用画布算），缩放系数就不准，
 * 要么裁掉大半内容、要么留一大圈空白。
 *
 * page 模式必须走 resolvePageLayout：这是真实画布的唯一布局源，用 chartRect 像素另算一套
 * 会与画布对不上（曾出现缩略图 4 列溢出、大片空白）。网格布局的判据是“有没有网格数据”：
 * 看板级 design.page 只是其中一种来源，脚本/接口生成的看板可以只写每图表 config.pageLayout
 * （只看 design.page 会误判成大屏，缩略图按默认 1920×1080 画，内容只占顶部一小块）。
 */
export function thumbnailGeometry(dashboard: Pick<Dashboard, 'filters' | 'charts'>) {
  const charts = dashboard.charts || [];
  const isPage = dashboard.filters?.design?.page !== undefined
    || charts.some(c => (c.config as any)?.pageLayout !== undefined);
  let pageItems: PageItem[] = [];
  if (isPage) { try { pageItems = resolvePageLayout(dashboard, 'desktop'); } catch { pageItems = []; } }
  const canvas = isPage ? pageCanvasSize(pageItems, 'desktop') : getCanvas(dashboard.filters, charts);
  const width = canvas.width || 16;
  // 框高最多取 2 倍宽：超长看板按真实比例裁顶部（不留大片空白，也不竖向压扁）
  return { isPage, pageItems, canvas, box: { width, height: Math.min(canvas.height || 9, width * 2) } };
}
