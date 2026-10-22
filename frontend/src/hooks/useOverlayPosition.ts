import { useLayoutEffect, useState } from 'react';
import type { RefObject } from 'react';

/**
 * 跟随外部 ref 测量编辑视口的可用尺寸。
 *
 * 覆盖层（选中工具条、缩放手柄）渲染在缩放层之外，需要视口尺寸把画布坐标
 * 换算成屏幕坐标；用 ResizeObserver 跟随窗口变化与左右面板折叠。
 */
export function useViewportSize(ref: RefObject<HTMLElement | null>) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize({ width: el.clientWidth, height: el.clientHeight });
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    measure();
    return () => observer.disconnect();
  }, [ref]);
  return size;
}
