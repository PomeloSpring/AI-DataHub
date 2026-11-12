import { useEffect, useRef, useState, type ReactNode } from 'react';
import { thumbnailGeometry } from '@/lib/dashboardThumbnail';
import type { Dashboard } from '@/stores/dashboardStore';

/**
 * 缩略图固定框：列表里的卡片预览必须等高，不能按看板画布比例撑高
 * （长画布的看板能把一张卡顶到一屏多，列表完全没法扫读）。
 *
 * 缩放口径 = **cover（铺满框，超出裁掉）**，不是 contain：
 * contain 把整张画布塞进矮框时，宽度也跟着缩（16:9 × h-24 约缩到 34%），预览变成中间一小条、
 * 左右全是空白，缩完的预览文字也已经不可读——不如按框宽铺满、只露顶部一瞥（实际有效尺寸反而更大）。
 * 比例取自 `thumbnailGeometry`，与 DashboardThumbnail 内部同一算点，否则缩放系数不准。
 *
 * 单独成文件（不放进 DashboardCanvas）：多个页面测试会 mock 掉 DashboardCanvas 模块，
 * 框必须保持真实，否则卡片整块渲染不出来、尺寸护栏也变成在断言 mock。
 */

/**
 * 预览槽高度的唯一口径：工作区「数据看板」与系统「看板管理」的列表卡共用同一个框，
 * 改这一处即两边同步（卡片高度里预览占大头，用户反馈卡片上下偏厚，逐次收敛到 h-24）。
 * 页面/占位分支一律引用本常量，不得再写第二套高度字面量。
 */
export const THUMB_FRAME_H = 'h-24';

export function DashboardThumbnailFrame({ dashboard, children }: {
  dashboard: Pick<Dashboard, 'filters' | 'charts'>; children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { box } = thumbnailGeometry(dashboard);
  const ratio = box.width / box.height;
  const [scale, setScale] = useState(1);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const natural = el.clientWidth / (Number.isFinite(ratio) && ratio > 0 ? ratio : 16 / 9);
      // cover：内容比框高（常见：16:9 看板 vs 矮框）就按框宽 1:1 铺满、只裁底部；
      // 只有内容比框还矮（超宽画布）才放大到填满高度、左右裁掉。natural 为 0/NaN 时不缩放
      setScale(natural > 0 && el.clientHeight > natural ? el.clientHeight / natural : 1);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    measure();
    return () => observer.disconnect();
  }, [ratio]);
  return <div ref={ref} className={`relative ${THUMB_FRAME_H} w-full overflow-hidden bg-muted/20`}>
    <div className="absolute inset-x-0 top-0 w-full" style={{ transform: `scale(${scale})`, transformOrigin: 'top center' }}>
      {children}
    </div>
    {/* 底部渐隐：裁切处读作“下面还有内容”，而不是切坏了 */}
    <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-6 bg-gradient-to-t from-card via-card/70 to-transparent" />
  </div>;
}
