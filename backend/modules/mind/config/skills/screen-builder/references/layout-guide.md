# 大屏布局与栅格

工具侧画布基准 **1920 × 1080**,`create_data_screen` 未给 `position` 时自动布局:KPI 类(`text_display`/`big_number_trend`/`gauge`)排顶部一行,其余按 2~3 列均分,末行不足自动加宽铺满。

## 两套版面几何(易错点,务必区分)

同一个看板对象有两套版面口径,`position` 只驱动其中一套:

| 视图 | 几何 | 谁写 |
|------|------|------|
| 大屏播放 `/screen/<id>` | 走 `ResponsiveDashboardCanvas` → **响应式列格** | 由 `position` 像素**等比换算**得出(`automatic` 项),按断点重排 |
| 看板页面/编辑器 | pageLayout v3:desktop **48 列** × 行单位 **4px** | 前端编辑器「页面布局」保存,本工具无入口 |

推论(均为实测踩过的坑):

- `position` 的 `w/h` **只保证相对比例**,不保证像素级所见即所得;窄屏/平板会换列数重排,别向用户承诺像素还原。
- 控件类(`widget_*`)在页面视图按**自然尺寸**生成,不按 position 等比放大;图表才按等比映射。
- 列宽换算必须**把 gap 算进去**(只按列数等分不含间距会让控件/卡片窄一截,历史缺陷:日期区间控件"只显一半");行高单位是 4px,`gridAutoRows` 不再叠加行间距。
- 精确版面(网格列数、控件行、间距)只能在编辑器保存页面布局;AI 侧生成后如需微调,引导用户进编辑器,或改 `position` 比例后重生成。

## 常见版式

- **经营驾驶舱**:顶部 4 个 KPI 卡(y=0,h=180)→ 中部主趋势图占左 2/3 + 右侧占比/排行 → 底部明细表或多维雷达。用 `layout_kpi_top`。
- **实时监控**:九宫格 `layout_grid_3x3`,每格一个 gauge/迷你趋势。
- **焦点分析**:左主右辅 `layout_left_right`,左侧大趋势/地图,右侧排行/KPI 列表。

## 信息层级

- 最重要指标放左上与顶部;观众视线从左上看起。
- 同类图放同一行/列;留白 ≥ 12px;避免同一屏混用超过 2 种图表家族。
- 标题栏 `frame_title_bar` 放整屏标题与时间;角标框 `frame_tech_corner` 强化科技感。

## 图表选型(与渲染器一致)

趋势→`line/area/timeseries_line`;对比→`bar`;占比→`pie/rose/treemap`;多维→`radar`;转化→`funnel`;达成→`gauge`;排行/明细→`table/table_value`;核心数字→`big_number_trend/text_display`;地理→`china_map/world_map`。
