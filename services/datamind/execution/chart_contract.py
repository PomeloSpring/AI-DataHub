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
    # 时间动画(需时间列+数值列; 竞速图再加系列列)
    "bar_race", "timeline_play",
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
timeseries_area, calendar_heatmap, bar_race, timeline_play, heatmap, boxplot, bubble, \
sankey, tree, treemap, rose, radial_bar, word_cloud, china_map, world_map
- columns: 字符串数组,表头列名;至少包含一个维度列(字符串)和一个数值列
- rows: 二维数组,每行元素顺序与 columns 对应;数值列必须是数字(不加引号)
- 按数据形态选图:雷达图需多指标对比(1-2 行多数值列);词云需 词+频次 两列;\
地图需首列为地区名(中国地图用省份/城市中文名);桑基/树图需 源→目标(或层级)+数值;\
时间动画图(bar_race 竞速条形图/timeline_play 时序播放)需 时间列+数值列(bar_race 最好再有系列列,\
如 月份+产品+销量),按时间演进展示时优先于静态时序图;形态不满足时退回 bar/line/table,不要硬凑字段
- sql 字段填写生成该图表数据的真实查询语句(前端卡片内置 SQL 视图展示它)
- 输出了 ```chart 块后,正文不要再重复粘贴同一份原始数据的 Markdown 表格或逐行数值\
(前端卡片已内置 图表/明细/SQL 三视图),正文只给结论与要点解读
- 一个回答可包含多个 ```chart 代码块;不需要图表时用普通 Markdown 即可
- 关系图谱(表血缘/实体关系/流程拓扑)可用 ```graph 代码块输出,内容为严格 JSON:

```graph
{"title":"表血缘","nodes":[{"id":"orders","label":"订单表","type":"table"}],"edges":[{"source":"orders","target":"dws_sales","label":"汇总"}]}
```

  其中 nodes 需 id(label/type 可选,type 决定配色分组),edges 需 source/target(悬空边会被丢弃)
- 数据必须来自真实查询结果,禁止编造
"""


ARTIFACT_CONTRACT = """\
# 文件产物交付约定

当你为用户生成了可下载/可查看的文件产物(如 Excel 报表、HTML 报告、Markdown、PDF、CSV、PNG/JPG 图片)时:
1. 把文件写入会话工作区 `/workspace/<文件名>`(例如 `/workspace/案例订单分析报告.html`)。
2. 在回答中输出一个 ```artifact 代码块, 内容为严格 JSON(无注释、无多余文本):

```artifact
{"type": "html", "filename": "案例订单分析报告.html", "path": "案例订单分析报告.html", "theme": "datafoundry", "description": "一句话说明该产物内容"}
```

字段说明:
- type 只能取: excel, pdf, html, md, csv, png, jpg (按文件真实类型; xlsx 用 excel)。
- filename: 展示与下载用的文件名(含扩展名); path: 相对会话工作区的文件名(可带 /workspace/ 前缀, 会被剥离)。
- theme(可选): 该产物采用的主题 id; 配色与图表色序必须取自系统注入的"报告主题令牌", 默认继承用户当前主题, 禁止自造颜色。
- 前端会把该块渲染为**下载卡片**; html 与 png/jpg 还会在卡片内**可视化预览**(iframe/图片为生成时的主题快照)。
- 一个回答可输出多个 ```artifact 块。

严格禁止(避免误导用户与展示不出来):
- **不存在**所谓的"工作空间文件面板/文件管理器/右侧或上方的文件列表 UI"; 不得让用户去那里找下载入口。
- 交付文件的**唯一**方式就是上面的 ```artifact 下载卡片。不要用文字描述一个不存在的界面。
- HTML 产物必须单文件自包含: 禁止引用任何外部 CDN / 远程 JS 库(echarts/cdn-mermaid/markmap) / 远程字体 / 远程图片; 图表用内联 SVG 或 base64 PNG。
- 报告/产物禁止 emoji 与装饰性图标; 风格专业、严谨、克制。
- 数据图表用 ```chart 块、流程/脑图用 ```mermaid 块(平台内置渲染), 不要在 HTML 里自造图表库。
- path/filename 必须是你真实写入工作区的文件, 不得编造; 文件内容必须来自真实产出, 不得伪造。
"""


def chart_contract_text() -> str:
    """返回图表 + 文件产物输出契约提示词文本。"""
    return CHART_CONTRACT + "\n" + ARTIFACT_CONTRACT
