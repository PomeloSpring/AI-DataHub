import { useEffect, useRef, useState, type ReactNode } from 'react';
import { thumbnailGeometry } from '@/lib/dashboardThumbnail';
import type { Dashboard } from '@/stores/dashboardStore';

/**
 * 缩略图固定框：列表里的卡片预览必须等高，不能按看板画布比例撑高
 * （长画布的看板能把一张卡顶到一屏多，列表完全没法扫读）。
 *
 * 按框宽取自然高度，超高时整体等比缩到框内居中；只靠 overflow-hidden 裁会切掉大半，
 * 用 max-height 会压扁比例 —— 两者都不行。比例取自 `thumbnailGeometry`，
 * 与 DashboardThumbnail 内部同一算点，否则缩放系数不准。
 *
 * 单独成文件（不放进 DashboardCanvas）：多个页面测试会 mock 掉 DashboardCanvas 模块，
 * 框必须保持真实，否则卡片整块渲染不出来、尺寸护栏也变成在断言 mock。
 */
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
      setScale(natural > el.clientHeight ? el.clientHeight / natural : 1);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    measure();
    return () => observer.disconnect();
  }, [ratio]);
  return <div ref={ref} className="relative h-40 w-full overflow-hidden bg-muted/20">
    <div className="absolute left-1/2 top-1/2 w-full" style={{ transform: `translate(-50%, -50%) scale(${scale})`, transformOrigin: 'center' }}>
      {children}
    </div>
  </div>;
}
