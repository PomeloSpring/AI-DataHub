# 大屏布局与栅格

画布基准 **1920 × 1080**,前端按容器等比缩放。`create_data_screen` 未给 `position` 时自动布局:KPI 类(`text_display`/`big_number_trend`/`gauge`)排顶部一行,其余按 2~3 列均分,末行不足自动加宽铺满。

需要精确控制时,给 widget 传 `position:{x,y,w,h}`(像素,基于 1920×1080)。

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
