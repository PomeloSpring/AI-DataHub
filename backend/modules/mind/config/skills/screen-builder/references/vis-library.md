# 字模库(UI 库)使用指南

字模 = 大屏可复用的视觉积木,分七类(`category`):

| category | 含义 | 用在哪 |
|----------|------|--------|
| `chart_style` | 单个图表的视觉样式(配色/渐变/圆角/坐标轴/图例) | widget 的 `component_ids` |
| `screen_background` | 大屏整体背景(纯色/渐变/网格/光晕) | create 的 `style.background` |
| `color_theme` | 配色主题包(系列色板 + 明暗) | `style.colorTheme` |
| `kpi_card` | 指标卡样式(发光/扁平/金边) | KPI 类 widget `component_ids` 或 `style.cardStyle` |
| `layout_template` | 布局模板(顶部指标行 / 左右 / 九宫格) | `style.layout` |
| `decoration_frame` | 装饰边框 / 标题栏 | `style.decoration` |
| `sql_template` | 供传统 BI 人工配置的参数化 SQL 模板(仅管理员/数据开发在图表配置里用,**不进入 Agent 取数**) | 前端图表配置 |

## 内置字模速查(节选)

- 配色:`theme_tech_neon`(科技霓虹·深) `theme_finance_gold`(金融金·深) `theme_datafoundry`(近黑·浅) `theme_glass`(玻璃拟态) `theme_aurora`(极光)
- 背景:`bg_tech_grid`(深蓝网格) `bg_dark_aurora`(暗夜极光) `bg_finance_navy`(金融深蓝) `bg_light_clean`(简洁亮白)
- 卡片:`card_glow`(霓虹发光) `card_flat`(极简扁平) `card_gold`(金色高端)
- 布局:`layout_kpi_top`(顶部指标+多栏) `layout_left_right`(左主右辅) `layout_grid_3x3`(九宫格)
- 装饰:`frame_tech_corner`(科技角标框) `frame_title_bar`(渐变标题栏)
- 图表样式:`cs_bar_neon` `cs_bar_flat` `cs_line_tech` `cs_line_gold` `cs_pie_ring` `cs_radar_tech` `cs_gauge_ring` `cs_funnel_grad`

## 组装建议

- 风格成套:背景 + 配色 + 卡片 + 图表样式应来自同一风格族(科技族=青蓝霓虹;金融族=深蓝金;报表族=浅色近黑)。
- 单系列柱/折线在浅色(报表)族下用近黑主色;多系列保留低饱和辅助色区分。
- `create_data_screen` 里:整屏 `style={background,colorTheme,cardStyle,layout,decoration}`,每图 `component_ids=[<chart_style code>]`。

## 沉淀(回存为字模)

设计出一套好看的样式后,经用户同意用 `save_vis_component` 存为 `custom` 字模,供后续复用。内置字模(`is_builtin`)仅管理员可改。
