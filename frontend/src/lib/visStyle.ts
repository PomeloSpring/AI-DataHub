/**
 * 字模取值 — 按 code 从字模库取 style_config 并与内置默认浅合并。
 * 消费方(Home/DataProfileCard/GraphCard/AnimatedTimeChart/DataGrid)共用单一取值口径:
 * 库中有该字模 → 以库值覆盖默认(库中缺失的键仍由默认补齐);库中无该字模 → 全量默认,不阻断渲染。
 */
import { useMemo } from 'react';
import { useVisLibrary } from '@/hooks/useVisLibrary';
import { sanitizeStyle } from '@/lib/dashboardDesign';

export function useVisConfig<T extends Record<string, any>>(code: string, fallback: T): T {
  const { items } = useVisLibrary();
  return useMemo(() => {
    const hit = items.find(i => i.code === code);
    if (!hit) return fallback;
    const cfg = (sanitizeStyle(hit.style_config) as any).config
      || (sanitizeStyle(hit.style_config) as any); // screen_background 等类目键在顶层
    return { ...fallback, ...(cfg || {}) } as T;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, code]);
}
