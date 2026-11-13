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
    # 地图热力(经度+纬度+数值权重列,前端用 UI 字模库地图瓦片渲染热力)
    "map_heat",
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
sankey, tree, treemap, rose, radial_bar, word_cloud, china_map, world_map, map_heat
- columns: 字符串数组,表头列名;至少包含一个维度列(字符串)和一个数值列
- rows: 二维数组,每行元素顺序与 columns 对应;数值列必须是数字(不加引号)
- 按数据形态选图:雷达图需多指标对比(1-2 行多数值列);词云需 词+频次 两列;\
地图需首列为地区名(中国地图用省份/城市中文名);地图热力(map_heat)需 经度列+纬度列+数值权重列\
(列名含 lng/lon/lat 或 经度/纬度),用于展示地理分布热力;桑基/树图需 源→目标(或层级)+数值;\
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
- 一个回答可输出多个 ```artifact 块（本形态是**载体**）：整份报告若已有平台交付通道（见注入的交付契约），
  不要用本块再交一份同内容；没有那条通道时，把报告写成自包含 HTML 文件并用本块交付仍是合法做法。

严格禁止(避免误导用户与展示不出来):
- **不存在**所谓的"工作空间文件面板/文件管理器/右侧或上方的文件列表 UI"; 不得让用户去那里找下载入口。
- 交付文件的**唯一**方式就是上面的 ```artifact 下载卡片。不要用文字描述一个不存在的界面。
- HTML 产物必须单文件自包含: 禁止引用任何外部 CDN / 远程 JS 库(echarts/cdn-mermaid/markmap) / 远程字体 / 远程图片; 图表用内联 SVG 或 base64 PNG。
- 报告/产物禁止 emoji 与装饰性图标; 风格专业、严谨、克制。
- 数据图表用 ```chart 块、流程/脑图用 ```mermaid 块(平台内置渲染), 不要在 HTML 里自造图表库。
- path/filename 必须是你的真实写入工作区的文件, 不得编造; 文件内容必须来自真实产出, 不得伪造。
"""


RENDER_CONTRACT = """\
# 交付形态渲染机

裁决权在你：服务端只拒「这条通道做不到 / 会泄露」的事，不替你判「该不该用整页文档」。
选道顺序（只比下面列出的通道 —— 没列出的就是本会话调不到的）：
1. 本轮没取到数据、也没形成归因链 → 纯文本回答，不碰任何渲染机。
2. 结论主要是一两个数据形状、叙述不超三段 → 图表片段（可多次）。
3. 需要成文叙述 + 多个图表一起阅读 → 交**一份**整体文档，不要再散铺一串图表卡片。
"""

RENDER_CHART_SECTION = """\
## 图表片段（render_chart）
1. 调 `render_chart(title, intent, chart_type?)`：`intent` 与 run_semantic_query 的 intent_json 同形，
   数据由服务端经语义层取，`row_count` 与取数同源——**不要把数据抄进调用**。只有多次取数合并、
   数据不落在单一 intent 时才用 columns+rows 兜底（且必须与本轮已展示的结果一致）。
2. 成功后在回答正文输出一个 ```chart 代码块引用它（严格 JSON、无多余文本）：

```chart
{"chart_ref": "<渲染机返回的 chart_id>", "title": "近 7 天访问量趋势"}
```

3. 渲染机会校验图型与数据形态：未知图型、雷达缺多指标、地图缺地理列、时序缺时间轴、数值列被写成
   字符串，都会**拒收并回原因**（不会再静默退成柱状图）。收到错误请读原因后重新取数或换图型。
4. 引用块不要再重复粘贴同一份数据（图表卡内置 图表/明细/SQL 三视图与图上下钻）；
   确实画不出的形态（明细表/单句结论）用 Markdown 表格或文字，不要硬凑图。
"""

RENDER_HTML_SECTION = """\
## 整页文档（render_html）
1. 调 `render_html(title, html, theme?)`。html 必须是**单文件自包含**的整页文档。
2. 成功后在回答正文输出一个 ```report 代码块引用它（严格 JSON、无多余文本）：

```report
{"doc_id": "<渲染机返回的 id>", "body_kind": "html", "title": "订单分析报告"}
```

- doc_id / body_kind 原样取回执，不得自己编造或改动；**不要在正文里重复粘贴报告内容**
  （前端按引用取回正文并就地渲染整页，可全屏阅读/下载）。
- 体检不过会被拒：外链/伪地图/缺主题令牌/含系统信息/超体积。收到错误要读原因后改文档重调，
  不得声称“报告已生成”（回执里没有 doc_id 就是没生成）。
- 单页文档的已知代价（要在报告元信息里如实写明，不得当作等价于平台渲染）：
  地理底图与雷达/桑基/树图/日历热力/时序动画在这条通道拿不到平台渲染（退为表格）；
  配色是生成时的主题快照（改字模不会让存量报告跟着变）；文档内图表不可下钻。
"""

RENDER_REPORT_SECTION = """\
## 结构化章节报告（render_report）
适用判断：结论里有地理分布或富图型（地图/雷达/桑基/树图/日历热力/时序动画）时要选本形态，
因为它们由平台真渲染（主题随查看者即时换装、图表可下钻）；整页文档通道只能把这些退成表格。
1. 调 `render_report(title, sections, theme?, template?)`：你只交章节定义，数据由服务端逐章经语义层取。
   kind 取 kpi / chart / table / text / mermaid；数据章节必须带 `intent`（与 run_semantic_query 的
   intent_json 同形），**不接受 rows/sql/data**；`text` 带 `narrative`，`mermaid` 带 `code`。
2. 成功后在回答正文输出一个 ```report 代码块引用它（严格 JSON、无多余文本）：

```report
{"doc_id": "<渲染机返回的 id>", "body_kind": "spec", "title": "订单分析报告"}
```

3. 什么时候不用本形态：只有一个数据形状、结论两三句 → 图表片段（` ```chart `）；用户点名要“可离线转发的
   单页文档”→ 整页文档形态（上面那节）。两者与本章互斥，一轮只交一个主体。
4. 某章取数失败 → 该章写“暂无数据 + 原因”（不得沿用上一版数字）；回执里的 `failed_sections` 要如实转述给用户。
5. `template` 传报告模板的**业务名**（不是 id）；模板不可用会回可用清单，不得猜名字（模板只定版式，不代你写结论）。
"""

RENDER_CONTRACT_TAIL = """\
## 共同纪律
- 回执里没有 id 就是没生成：不得向用户声称已生成，也不得静默换成另一种形态或部分内容。
- 要修订已交出的内容就带原 id 再调，不要另起一份（同会话多份正文会互相对不上数）。
- 引用块本身就是交付入口：不存在“文件清单/下载中心”那类 UI，也不要把正文再贴一遍。
- 报告主体走上面的引用块，**不要再同时交一个 ```artifact 的 .html 文件**：同一份内容两种形态必然对不上数。
  ` ```artifact ` 在本会话只留给**数据文件与用户点名要的下载件**（Excel/CSV/PDF/图片等）。
"""

# 两条以上通道并存才需要说“主交付物互斥”：只授权其中一条时注入这句会把没授权的工具说成可用
RENDER_SINGLE_PRIMARY_RULE = """\
- 一轮**只一个主交付物**：整份文档（两种文档形态任选其一）与图表卡片互斥，两种文档形态之间也互斥；
  交了整份文档就不要再散铺图表片段，同一结论两份形态并存必然对不上数。
"""

# 契约段 ↔ 工具名：注入哪几段取决于本会话真的注册了哪些渲染机（不拿全文当固定块重发）
RENDER_CONTRACT_SECTIONS = {
    "render_chart": RENDER_CHART_SECTION,
    "render_html": RENDER_HTML_SECTION,
    "render_report": RENDER_REPORT_SECTION,
}


def granted_render_tools(capabilities: dict | None) -> list[str]:
    """本次会话真的注册了哪些渲染机（按已注册工具清单判定，不看角色也不猜）。

    没授权就不能注入对应段落：给模型广告一个调不到的入口 = 提示词写了但功能不存在，
    它会把“报告已生成”当成事实说出来，而前端拿不到任何交付对象。
    入参是 compose_system_prompt 的 capabilities 载荷（与工具权限清单同形）。
    """
    if not capabilities:
        return []
    names = {str(t.get("name") or "").rsplit("__", 1)[-1]
             for t in (capabilities.get("tools") or [])}
    return [tool for tool in RENDER_CONTRACT_SECTIONS if tool in names]


def render_contract_text(granted=None) -> str:
    """按已授权的渲染机拼装交付契约；一个都没授权时回空串（调用方不得注入空段）。"""
    tools = [t for t in (granted if granted is not None else list(RENDER_CONTRACT_SECTIONS))
             if t in RENDER_CONTRACT_SECTIONS]
    parts = [RENDER_CONTRACT_SECTIONS[t] for t in tools]
    if not parts:
        return ""
    if len(parts) > 1:
        parts.append(RENDER_SINGLE_PRIMARY_RULE)
    return RENDER_CONTRACT + "\n" + "\n".join(parts) + "\n" + RENDER_CONTRACT_TAIL


def chart_contract_text() -> str:
    """返回图表 + 文件产物输出契约提示词文本。"""
    return CHART_CONTRACT + "\n" + ARTIFACT_CONTRACT
