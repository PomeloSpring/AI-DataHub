---
trigger: always_on
description: UI 字模库（adh_vis_components）是系统统一样式规范的唯一事实源：一切图表/控件渲染面必须经 useChartDesign→resolveChartDesign 管线消费主题包 tp_<theme>，禁止各自写配色/线型/圆角/渐变；字模样式字段必须被渲染引擎真实消费（存了不生效=缺陷），加载失败要显式提示。
---

# UI 字模库 = 系统统一样式规范 — 强制规范

> 字模库不是"可选皮肤"，是系统统一样式的**唯一事实源**：主题包定义色板、卡片、图表线型/圆角/渐变/标签，
> 主题一切换全端（Chat、看板、大屏、数据集预览、弹窗页、设计面板、报表）同观感。
> 任何渲染面另写配色、任何字模字段"存了但渲染不生效"，都视为样式缺陷，必须修复而非妥协。

## 1. 唯一样式源与消费管线
- 字模分类（`adh_vis_components.category`）：`theme_pack`（`tp_<theme>` 九套主题）、`chart_style`、`kpi_card`、`color_theme`、`screen_background`、`layout_template`、`decoration_frame`；主题包结构 `{mode, palette, background, card, charts{default,<type>}, widgets{default,<type>}}`。
- **唯一消费入口**：`hooks/useChartDesign.ts`（内部 `resolveDashboardDesign` + `resolveChartDesign`），返回 `config`/`visual` **两个都要**传给 `DashboardChart`，且 `visual.viewBg = visualConfig(config).cardBg || visual.viewBg`（图表画布背景取主题包卡片底）。
- 一切图表渲染面必须经它：Chat 结果卡与 ```chart 块、看板/大屏、数据集预览、`ModalPage` 弹窗页、AS-BOT 设计面板、报表。**禁止裸 `<DashboardChart>`、禁止硬编码色值/渐变/圆角/线宽**；看板画布场景直接用 `resolveDashboardDesign/resolveChartDesign` 同源算出（等价管线）。

## 2. 样式字段必须真实消费（防"存了不生效"）
- 新增字模样式键必须**三方同步**：① `lib/dashboardDesign.ts` 的 `VISUAL_KEYS`/`sanitize*` 白名单（漏了会被入库即剥）；② `DashboardChart.buildG2Spec` 各图型分支真实消费；③ 用例锁定（断言 spec 里出现该样式）。
- 各图型分支（bar/line/area/pie/时序族/…）对同一组旋钮口径一致：`gradient`/`borderRadius`/`showLabel`/`smooth`/`lineWidth`/`areaFill`/`areaOpacity`/`glow`。**新增图型分支不得写死半径/线宽**（历史缺陷：时序族 `radiusTopLeft: 4`、`lineWidth: 2` 写死、不看 `gradient`，导致科技风时序柱状图没有渐变）。
- 字模库管理页的预览是"预期效果"基准：预览与真实渲染同源，二者不一致即缺陷。

## 3. 主题与历史数据
- 样式在**渲染时**解析（`tp_<theme>`，缺失回退 `tp_datafoundry`），不落库：历史对话/快照/报表随当前查看者主题即时换装，改字模不需要重问/重取数。
- 主题包改动（改色板、加渐变）应即时在所有渲染面生效，"改了字模只有某页没变"= 该页绕开了管线。

## 4. 降级要显式（与 no-silent-degradation 对齐）
- 字模库未加载/加载失败：图表可回默认样式，但**必须显式提示 + 可重试**（ChartPicker/看板同口径），禁止静默用默认样式假装"已按主题"。

## 5. 回归门禁
- 触碰 `lib/dashboardDesign.ts` / `components/DashboardChart.tsx` / `hooks/useChartDesign.ts` 后跑：
  `components/__tests__/ChartPickerTheme.test.tsx`（主题包契约 + 降级提示）、`components/__tests__/DashboardChartSpec.test.ts`（样式键被 spec 真实消费 + 禁裸渲染静态护栏）、`lib/__tests__/dashboardDesign.test.ts`；
- 新增图表渲染面必须补契约用例（断言 config/visual 来自主题包），并在本规则 §1 清单登记。
