import { useMemo } from 'react';
import { useVisLibrary } from './useVisLibrary';
import { useThemeStore } from '../stores/themeStore';
import {
  legacyCanvasFilters, resolveChartDesign, resolveDashboardDesign, visualConfig,
} from '@/lib/dashboardDesign';

/**
 * 图表样式解析 —— UI 字模库是系统统一样式规范的唯一消费入口。
 *
 * 所有图表渲染面（Chat 结果卡/Markdown chart 块、看板与大屏、数据集预览、
 * 弹窗页、AS-BOT 设计面板）一律经此取 `config`+`visual` 再交给 DashboardChart，
 * 禁止各自写配色/线型或裸渲染 DashboardChart（.qoder/rules/ui-vis-library-style.md）。
 *
 * 解析链：主题包 `tp_<theme>`（缺则 tp_datafoundry）→ `charts.default`+`charts.<type>`
 * → 卡片样式 → 调用方覆盖项；样式在渲染时解析，主题一切换即生效（历史数据不落样式）。
 */
export function useChartDesign(opts?: { filters?: any }) {
  const { items } = useVisLibrary();
  const theme = useThemeStore(s => s.theme);
  const filters = opts?.filters;
  const design = useMemo(
    () => resolveDashboardDesign(legacyCanvasFilters(filters || {}), items, { theme }),
    [items, theme, filters],
  );
  return useMemo(() => ({
    /** 字模库原始字模（调用方一般不需要，留作扩展） */
    items,
    /** 单图样式：返回可直接传给 DashboardChart 的 config/visual */
    chartStyle(config: Record<string, any> = {}, chartType = '') {
      const resolved = resolveChartDesign(config, design, items, chartType);
      // 与看板卡同口径：图表画布背景取主题包卡片底，深色主题下文字/网格/tooltip 同套配色
      return {
        config: resolved.config,
        visual: { ...resolved.visual, viewBg: visualConfig(resolved.config).cardBg || resolved.visual.viewBg },
      };
    },
  }), [items, design]);
}
