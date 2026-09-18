"""图表输出契约 — 注入 Qoder 系统提示词,约定 Agent 输出可视化图表的格式.

Agent 在回答中用 ```chart 围栏代码块输出 JSON,前端 Chat 页解析并渲染 ChartPicker。
契约与前端看板共用同一套图表类型(DashboardChart.tsx CHART_TYPES,参数控件除外)。
"""

# 支持的图表类型(与 frontend/src/components/DashboardChart.tsx CHART_TYPES 一致;
# widget_* 为看板参数控件不适用于对话取数,table 为历史兼容别名→前端退回柱状图)
CHART_TYPES = [
    # 基础
    "bar", "line", "pie", "area", "scatter", "radar", "funnel", "waterfall",
    # 数据展示
    "text_display", "table_value", "big_number_trend", "gauge",
    # 时间序列
    "timeseries_line", "timeseries_bar", "timeseries_area", "calendar_heatmap",
    # 高级
    "heatmap", "boxplot", "bubble", "sankey", "tree", "treemap",
    "rose", "radial_bar", "word_cloud",
    # 地理
    "china_map", "world_map",
    # 历史兼容
    "table",
]

CHART_CONTRACT = """\
# 数据可视化输出约定

当你的分析涉及结构化数据且适合用图表展示时,请在回答中输出一个 ```chart 代码块,
内容为严格 JSON(不要包含注释、不要多余文本),格式:

```chart
{
  "title": "图表标题",
  "chart_type": "line",
  "sql": "生成该数据的查询语句",
  "columns": ["列名1", "列名2"],
  "rows": [["值", 数值], ["值", 数值]]
}
```

字段说明:
- chart_type 只能取以下之一: bar, line, pie, area, scatter, radar, funnel, waterfall, \
text_display, table_value, big_number_trend, gauge, timeseries_line, timeseries_bar, \
timeseries_area, calendar_heatmap, heatmap, boxplot, bubble, sankey, tree, treemap, \
rose, radial_bar, word_cloud, china_map, world_map
- columns: 字符串数组,表头列名;至少包含一个维度列(字符串)和一个数值列
- rows: 二维数组,每行元素顺序与 columns 对应;数值列必须是数字(不加引号)
- 按数据形态选图:雷达图需多指标对比(1-2 行多数值列);词云需 词+频次 两列;\
地图需首列为地区名(中国地图用省份/城市中文名);桑基/树图需 源→目标(或层级)+数值;\
形态不满足时退回 bar/line/table,不要硬凑字段
- sql 字段填写生成该图表数据的真实查询语句(前端卡片内置 SQL 视图展示它)
- 输出了 ```chart 块后,正文不要再重复粘贴同一份原始数据的 Markdown 表格或逐行数值\
(前端卡片已内置 图表/明细/SQL 三视图),正文只给结论与要点解读
- 一个回答可包含多个 ```chart 代码块;不需要图表时用普通 Markdown 即可
- 数据必须来自真实查询结果,禁止编造
"""


def chart_contract_text() -> str:
    """返回图表契约提示词文本。"""
    return CHART_CONTRACT
