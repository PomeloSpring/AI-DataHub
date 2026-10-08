# 大屏设计模板参考

模板给出"典型图表组合 + 语义意图骨架"。`object`/`metrics`/`dimensions` 名称必须先用
`knowledge_search`/`get_metrics` 在真实本体中确认存在后再套用;不可臆造字段。取数一律走
`query` 声明式意图,**不写 SQL**。

## 经营驾驶舱

- 核心 KPI(4×`big_number_trend`):营收 / 订单数 / 客单价 / 同比增速
- `timeseries_line` 营收趋势(time_window:"90d", time_grain:"day")
- `bar` 区域/产品线营收 TopN;`pie` 渠道占比;`table_value` 门店明细排行
- 字模:`theme_finance_gold` + `bg_finance_navy` + `card_gold` + `layout_kpi_top`

## 用户增长

- KPI:DAU / MAU / 新增 / 次日留存率
- `line` 增长趋势;`funnel` 激活转化;`bar` 渠道分布;`radar` 多维健康度
- 字模:`theme_aurora` + `bg_dark_aurora` + `card_glow` + `layout_kpi_top`

## 销售看板

- KPI:销售额 / 订单数 / 转化率
- `bar` 区域 TopN;`line` 月度趋势;`treemap` 品类结构;`pie` 新老客占比
- 字模:`theme_tech_neon` + `bg_tech_grid` + `card_glow` + `layout_left_right`

## 生产运营监控

- 顶部多 `gauge`:OEE / 良率 / 达成率
- `timeseries_line` 实时产能;`heatmap` 班次×设备;`bar` 故障排行
- 字模:`theme_tech_neon` + `bg_tech_grid` + `frame_tech_corner` + `layout_grid_3x3`
