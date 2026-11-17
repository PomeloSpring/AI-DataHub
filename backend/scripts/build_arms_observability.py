"""为 ARMS SLS 数据源（arms-prd-cn）构建 RUM 前端体验观测语义层资产。

一次性幂等构建（可重复执行，已存在的对象跳过）：
1. 受治理数据集 `arms_rum_observability`（预制 SQL 正文，env=prod + 相对时间窗 ${days}）；
2. 业务本体模型（kind=business，对象 `rum_event`，属性业务名→ASCII 列）；
3. 激活本体（展开 adh_ontology_objects）+ 写部署绑定 adh_ontology_bindings(bind_kind=dataset)；
4. 指标/维度字典（adh_metrics / adh_dimensions，bound_object_key=rum_event，名与数据集字段 label 对齐）。

背景：SLS/Presto 端点拒绝非 ASCII 标识符，故对象走数据集（预制 SQL 里显式引号处理带点/
连字符的 logstore 字段），语义层外层 ASCII 别名 + header_map 映回由 backend/semantics 承担。

运行：venv/bin/python -m backend.scripts.build_arms_observability
"""
from __future__ import annotations

import json

from backend.common.db.metadata_db import get_metadata_conn
from backend.modules.catalog.services import metrics_service, ontology_service
from backend.modules.viz.services.dashboard_service import chart_service, dashboard_service
from backend.modules.viz.services import dataset_service

DS_NAME = "arms-prd-cn"
OWNER_ID = 1778154925          # 数据源 owner（create_dataset 记录 owner_id）
DATASET_NAME = "arms_rum_observability"
OBJECT_KEY = "rum_event"

# ── SLS 预制 SQL 正文（每行一条 RUM 事件；env=prod 默认；相对时间窗 ${days} 天）──
# 带点/连字符的物理字段用双引号显式处理；时间桶用 date_trunc 预聚为普通维度列
# （语义层 role=time + time_grain 会套 to_char，SLS 不支持，故正文里先算好）。
_RUM_BODY = """SELECT
  "event_type" AS event_type,
  "app.name" AS app_name, "app.version" AS app_version, "app.channel" AS app_channel,
  "app.type" AS app_type,
  "view.name" AS view_name, "view.url" AS view_url, "view.domain" AS view_domain,
  "resource.name" AS resource_name, "resource.type" AS resource_type,
  "device.type" AS device_type, "device.brand" AS device_brand, "os.type" AS os_type,
  "os.container" AS browser,
  "geo.province" AS geo_province, "geo.city" AS geo_city, "geo.country" AS country,
  "isp.name" AS isp_name,
  "exception.type" AS exception_type, "exception.subtype" AS exception_subtype,
  "exception.name" AS exception_name,
  "session.id" AS session_id, "user.id" AS user_id,
  "net.ip" AS client_ip, "session.ip" AS session_ip,
  "resource.success" AS resource_success,
  "resource.duration" AS resource_duration, "view.first_contentful_paint" AS fcp,
  "view.largest_contentful_paint" AS lcp, "session.time_spent" AS session_time_spent,
  substr(CAST(from_unixtime(__time__) AS VARCHAR), 1, 13) AS event_hour,
  substr(CAST(from_unixtime(__time__) AS VARCHAR), 1, 10) AS event_date
FROM "logstore-rum"
WHERE "app.env" = 'prod' AND __time__ >= to_unixtime(now()) - ${days} * 86400"""


def _dim(field, label, **kw):
    d = {"field": field, "label": label, "role": "dimension"}
    d.update(kw)
    return d


def _mea(field, label, **kw):
    d = {"field": field, "label": label, "role": "measure"}
    d.update(kw)
    return d


# 数据集字段声明（mini-MDL）：label 即业务权威名（与字典 name 对齐），
# aliases 与字典别名对齐，使 get_metrics 广告的名字 run_semantic_query 均可解析。
_FIELD_CONFIG = [
    _dim("event_type", "事件类型", aliases=["事件"], value_labels={
        "resource": "资源", "view": "页面", "session": "会话", "action": "交互",
        "exception": "异常", "longtask": "长任务", "custom": "自定义"}),
    _dim("app_name", "应用名称", aliases=["应用", "App", "端"]),
    _dim("app_version", "应用版本", aliases=["版本"]),
    _dim("app_channel", "应用渠道", aliases=["渠道", "分发渠道"]),
    _dim("view_name", "页面名称", aliases=["页面", "访问页面", "路由"]),
    _dim("view_url", "页面URL", aliases=["页面链接", "URL", "访问链接", "地址"]),
    _dim("view_domain", "页面域名", aliases=["域名", "站点域名", "host"]),
    _dim("resource_name", "资源名称", aliases=["资源", "接口", "请求资源"]),
    _dim("resource_type", "资源类型", aliases=["资源种类"]),
    _dim("device_type", "设备类型", aliases=["设备", "终端类型"]),
    _dim("device_brand", "设备品牌", aliases=["品牌"]),
    _dim("os_type", "操作系统", aliases=["系统", "OS"]),
    _dim("geo_province", "省份", aliases=["省", "地区"]),
    _dim("geo_city", "城市", aliases=["市"]),
    _dim("isp_name", "网络运营商", aliases=["运营商", "ISP"]),
    _dim("exception_type", "异常类型", aliases=["错误类型", "JS异常类型"]),
    _dim("session_id", "会话ID", aliases=["会话标识"]),
    _dim("user_id", "用户ID", aliases=["用户标识"]),
    # IP 口径（产品拍板）：RUM 的 IP 明文可查不脱敏，故不入敏感字段基线；
    # 客户端IP=net.ip（采集侧完整）会话IP=session.ip（仅少量会话回传，空值不是 0）
    _dim("client_ip", "客户端IP", aliases=["IP", "访问IP", "来源IP", "ip"]),
    _dim("session_ip", "会话IP", aliases=["会话来源IP"]),
    _dim("resource_success", "资源请求结果", aliases=["请求成败"], value_labels={"1": "成功", "0": "失败"}),
    _dim("event_date", "统计日期", aliases=["日期", "天"]),
    _dim("event_hour", "统计小时", aliases=["小时", "整点"]),
    _dim("app_type", "应用类型", aliases=["端类型", "应用端"], value_labels={"browser": "浏览器/Web", "miniapp": "小程序", "app": "原生应用"}),
    _dim("browser", "浏览器", aliases=["浏览器内核", "Browser"]),
    _dim("country", "国家", aliases=["国家地区"]),
    _dim("exception_subtype", "异常子类型", aliases=["错误子类型"]),
    _dim("exception_name", "异常名称", aliases=["错误名称"]),
    _mea("resource_duration", "资源加载平均耗时(ms)", agg="AVG", unit="ms", aliases=["接口平均耗时", "平均响应时间"]),
    _mea("fcp", "首屏时间FCP(ms)", agg="AVG", unit="ms", aliases=["FCP", "首屏"]),
    _mea("lcp", "最大内容绘制LCP(ms)", agg="AVG", unit="ms", aliases=["LCP"]),
    _mea("session_time_spent", "会话平均时长(ms)", agg="AVG", unit="ms", aliases=["平均会话时长"]),
    _mea("m_events", "事件总数", expr="COUNT(*)", unit="个", aliases=["事件数", "上报量"]),
    _mea("m_sessions", "会话数", expr="COUNT(DISTINCT session_id)", unit="个", aliases=["会话量", "Session数"]),
    _mea("m_users", "独立用户数", expr="COUNT(DISTINCT user_id)", unit="个", aliases=["用户数", "UV"]),
    _mea("m_ips", "独立IP数", expr="COUNT(DISTINCT client_ip)", unit="个", aliases=["IP数", "客户端IP数", "独立访客IP"]),
    _mea("m_pv", "页面浏览量", expr="SUM(CASE WHEN event_type = 'view' THEN 1 ELSE 0 END)", unit="次", aliases=["PV", "页面访问量"]),
    _mea("m_res_req", "资源请求数", expr="SUM(CASE WHEN event_type = 'resource' THEN 1 ELSE 0 END)", unit="次", aliases=["接口请求数", "API数"]),
    _mea("m_res_err", "资源错误数", expr="SUM(CASE WHEN resource_success = 0 THEN 1 ELSE 0 END)", unit="次", aliases=["接口错误数", "请求失败数"]),
    _mea("m_res_succ_rate", "资源成功率(%)",
         expr="ROUND(SUM(CASE WHEN resource_success = 1 THEN 1 ELSE 0 END) * 100.0 / NULLIF(SUM(CASE WHEN resource_success IS NOT NULL THEN 1 ELSE 0 END), 0), 2)",
         unit="%", aliases=["请求成功率", "接口成功率"]),
    _mea("m_res_p95", "资源加载P95耗时(ms)", expr="approx_percentile(resource_duration, 0.95)", unit="ms", aliases=["接口P95耗时", "P95响应时间"]),
    _mea("m_exc", "异常数", expr="SUM(CASE WHEN event_type = 'exception' THEN 1 ELSE 0 END)", unit="个", aliases=["JS错误数", "报错数"]),
    _mea("m_lt", "长任务数", expr="SUM(CASE WHEN event_type = 'longtask' THEN 1 ELSE 0 END)", unit="个", aliases=["卡顿数"]),
    _mea("m_sess_per_user", "人均会话数", expr="ROUND(COUNT(DISTINCT session_id) * 1.0 / NULLIF(COUNT(DISTINCT user_id), 0), 2)", unit="个", aliases=["人均session"]),
    _mea("m_time_per_user", "人均会话时长(秒)", expr="ROUND(SUM(session_time_spent) * 1.0 / NULLIF(COUNT(DISTINCT user_id), 0) / 1000, 1)", unit="秒", aliases=["人均停留时长"]),
    _mea("m_err_users", "异常用户数", expr="COUNT(DISTINCT CASE WHEN event_type = 'exception' THEN user_id END)", unit="个", aliases=["Error UV", "受影响用户数"]),
    _mea("m_err_sessions", "异常会话数", expr="COUNT(DISTINCT CASE WHEN event_type = 'exception' THEN session_id END)", unit="个", aliases=["异常会话量"]),
]

# 维度字典（category 分类）：target_column 指向数据集输出列（ASCII），name 用业务权威名
_DIMS = [
    ("事件类型", "event_type", "状态", ["事件", "event"], {"resource": "资源", "view": "页面", "session": "会话", "action": "交互", "exception": "异常", "longtask": "长任务", "custom": "自定义"}),
    ("应用名称", "app_name", "属性", ["应用", "App", "端"], None),
    ("应用版本", "app_version", "属性", ["版本"], None),
    ("应用渠道", "app_channel", "属性", ["渠道", "分发渠道"], None),
    ("页面名称", "view_name", "属性", ["页面", "访问页面", "路由"], None),
    ("页面URL", "view_url", "属性", ["页面链接", "URL", "访问链接", "地址"], None),
    ("页面域名", "view_domain", "属性", ["域名", "站点域名", "host"], None),
    ("资源名称", "resource_name", "属性", ["资源", "接口", "请求资源"], None),
    ("资源类型", "resource_type", "分类", ["资源种类"], None),
    ("设备类型", "device_type", "属性", ["设备", "终端类型"], None),
    ("设备品牌", "device_brand", "属性", ["品牌"], None),
    ("操作系统", "os_type", "属性", ["系统", "OS"], None),
    ("省份", "geo_province", "地理", ["省", "地区"], None),
    ("城市", "geo_city", "地理", ["市"], None),
    ("网络运营商", "isp_name", "属性", ["运营商", "ISP"], None),
    ("异常类型", "exception_type", "分类", ["错误类型", "JS异常类型"], None),
    ("会话ID", "session_id", "属性", ["会话标识"], None),
    ("用户ID", "user_id", "属性", ["用户标识"], None),
    ("客户端IP", "client_ip", "地理", ["IP", "访问IP", "来源IP", "ip"], None),
    ("会话IP", "session_ip", "地理", ["会话来源IP"], None),
    ("资源请求结果", "resource_success", "状态", ["请求成败"], {"1": "成功", "0": "失败"}),
    ("统计日期", "event_date", "时间", ["日期", "天"], None),
    ("统计小时", "event_hour", "时间", ["小时", "整点"], None),
    ("应用类型", "app_type", "分类", ["端类型", "应用端"], {"browser": "浏览器/Web", "miniapp": "小程序", "app": "原生应用"}),
    ("浏览器", "browser", "属性", ["浏览器内核", "Browser"], None),
    ("国家", "country", "地理", ["国家地区"], None),
    ("异常子类型", "exception_subtype", "分类", ["错误子类型"], None),
    ("异常名称", "exception_name", "分类", ["错误名称"], None),
]

# 指标字典：formula 为口径说明（数据集正文负责执行），name 与数据集 label 对齐
_METRICS = [
    ("事件总数", "m_events", "COUNT", "COUNT(*)", "个", "RUM 上报事件总数", ["事件数", "上报量", "PV+资源等总数"]),
    ("会话数", "m_sessions", "COUNT_DISTINCT", "COUNT(DISTINCT session.id)", "个", "去重会话数", ["会话量", "Session数", "UV(会话)"]),
    ("独立用户数", "m_users", "COUNT_DISTINCT", "COUNT(DISTINCT user.id)", "个", "去重用户数", ["用户数", "UV"]),
    ("独立IP数", "m_ips", "COUNT_DISTINCT", "COUNT(DISTINCT net.ip)", "个", "去重客户端 IP 数（RUM IP 明文可查，产品口径不脱敏）", ["IP数", "客户端IP数", "独立访客IP"]),
    ("页面浏览量", "m_pv", "SUM", "SUM(CASE WHEN event_type='view' THEN 1 ELSE 0 END)", "次", "页面浏览(view)事件数", ["PV", "页面访问量"]),
    ("资源请求数", "m_res_req", "SUM", "SUM(CASE WHEN event_type='resource' THEN 1 ELSE 0 END)", "次", "资源/接口请求事件数", ["接口请求数", "API数"]),
    ("资源错误数", "m_res_err", "SUM", "SUM(CASE WHEN resource.success=0 THEN 1 ELSE 0 END)", "次", "资源请求失败数", ["接口错误数", "请求失败数"]),
    ("资源成功率(%)", "m_res_succ_rate", "SUM", "成功资源请求数 / 有结果资源请求数 * 100", "%", "资源/接口请求成功率", ["请求成功率", "接口成功率"]),
    ("资源加载平均耗时(ms)", "resource_duration", "AVG", "AVG(resource.duration)", "ms", "资源加载平均耗时", ["接口平均耗时", "平均响应时间"]),
    ("资源加载P95耗时(ms)", "m_res_p95", "NONE", "approx_percentile(resource.duration, 0.95)", "ms", "资源加载 P95 分位耗时", ["接口P95耗时", "P95响应时间"]),
    ("首屏时间FCP(ms)", "fcp", "AVG", "AVG(view.first_contentful_paint)", "ms", "首次内容绘制平均值", ["FCP", "首屏"]),
    ("最大内容绘制LCP(ms)", "lcp", "AVG", "AVG(view.largest_contentful_paint)", "ms", "最大内容绘制平均值", ["LCP"]),
    ("会话平均时长(ms)", "session_time_spent", "AVG", "AVG(session.time_spent)", "ms", "会话平均停留时长", ["平均会话时长"]),
    ("异常数", "m_exc", "SUM", "SUM(CASE WHEN event_type='exception' THEN 1 ELSE 0 END)", "个", "前端异常(JS错误)数", ["JS错误数", "报错数"]),
    ("长任务数", "m_lt", "SUM", "SUM(CASE WHEN event_type='longtask' THEN 1 ELSE 0 END)", "个", "长任务(longtask)数", ["卡顿数"]),
    ("人均会话数", "m_sess_per_user", "NONE", "COUNT(DISTINCT session.id) / COUNT(DISTINCT user.id)", "个", "会话数/访问用户数", ["人均session"]),
    ("人均会话时长(秒)", "m_time_per_user", "NONE", "SUM(session.time_spent)/COUNT(DISTINCT user.id)/1000", "秒", "总会话时长/访问用户数", ["人均停留时长"]),
    ("异常用户数", "m_err_users", "COUNT_DISTINCT", "COUNT(DISTINCT user.id WHERE 有异常事件)", "个", "发生过异常的去重用户数(Error UV)", ["Error UV", "受影响用户数"]),
    ("异常会话数", "m_err_sessions", "COUNT_DISTINCT", "COUNT(DISTINCT session.id WHERE 有异常事件)", "个", "发生过异常的去重会话数", ["异常会话量"]),
]


def _properties_from_fields():
    props = []
    for f in _FIELD_CONFIG:
        enum = f.get("value_labels") or []
        props.append({
            "column": f"rum.{f['field']}",
            "name": f["label"],
            "type": "int" if f.get("agg") == "AVG" or "duration" in f["field"] else "string",
            "is_key": f["field"] == "session_id",
            "description": f"{f['label']}（ARMS RUM 观测字段）",
            "enum": enum,
        })
    return props


def _ensure_dataset(ds_id: int) -> int:
    req = {
        "name": DATASET_NAME,
        "description": "ARMS RUM 前端体验观测明细（默认 env=prod，${days} 天窗口）",
        "sql_query": _RUM_BODY,
        "variables": {"days": {"type": "int", "default": 30}},
        "dialect": "sls",
        "field_config": _FIELD_CONFIG,
        "datasource_id": ds_id,
    }
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_datasets WHERE name=%s", (DATASET_NAME,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row:
        # 收敛：重跑时把正文/参数/字段声明同步为脚本定义（幂等）
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_datasets SET sql_query=%s, variables=%s, field_config=%s "
                    "WHERE id=%s",
                    (_RUM_BODY, json.dumps(req["variables"], ensure_ascii=False),
                     json.dumps(_FIELD_CONFIG, ensure_ascii=False), int(row["id"])))
            conn.commit()
        print(f"[dataset] 已同步 {DATASET_NAME} (id={row['id']}) 的正文/参数/字段声明")
        return int(row["id"])
    ds = dataset_service.create_dataset(req, {"user_id": OWNER_ID})
    print(f"[dataset] 已创建 {DATASET_NAME} (id={ds['id']})")
    return int(ds["id"])


# ── 运营人员专用用户分析数据集（基于 arms-prd-cn RUM 可分析维度，默认 env=prod）──
# 均为**预聚合正文 + 字面量滞动窗口（近 90 天）**，不经参数，故在数据集页预览/看板/Playground 均可直接执行。
# field: 正文输出列(ASCII)；label: 业务中文名；预聚合列用 agg=NONE（看板直接取列绘图）。
# 时间区间：看板顶部的日期范围控件会传 date_start/date_end（'YYYY-MM-DD'）作为数据集参数；
# 未选时（空串）COALESCE 回退近 90 天。CAST(NULLIF('','')) 避免空串转 timestamp 报错。
_TIME_FILTER = (
    "__time__ >= COALESCE(to_unixtime(CAST(NULLIF(${date_start}, '') AS timestamp)), "
    "to_unixtime(now()) - 90 * 86400)\n"
    "  AND __time__ <= COALESCE(to_unixtime(CAST(NULLIF(${date_end}, '') AS timestamp)) + 86399, "
    "to_unixtime(now()))"
)
_OPS_WINDOW = "\"app.env\" = 'prod' AND " + _TIME_FILTER
# 每个运营数据集都声明这两个参数（供看板日期控件驱动）；type=string 允许空串。
_OPS_VARS = {"date_start": {"type": "string", "default": ""},
             "date_end": {"type": "string", "default": ""}}


def _of(field, label, role="dimension", **kw):
    d = {"field": field, "label": label, "role": role}
    d.update(kw)
    return d


_DAILY = "substr(CAST(from_unixtime(__time__) AS VARCHAR), 1, 10)"


def _daily_sql(measures_sql: str) -> str:
    return (f"SELECT {_DAILY} AS stat_date,\n       {measures_sql}\n"
            "FROM \"logstore-rum\"\n"
            f"WHERE {_OPS_WINDOW}\n"
            f"GROUP BY {_DAILY}\nORDER BY stat_date")


def _dist_sql(dim_expr: str, dim_alias: str, measures_sql: str, order_alias: str,
              extra_where: str = "") -> str:
    where = _OPS_WINDOW + (f" AND {extra_where}" if extra_where else "")
    return (f"SELECT {dim_expr} AS {dim_alias},\n       {measures_sql}\n"
            "FROM \"logstore-rum\"\n"
            f"WHERE {where}\n"
            f"GROUP BY {dim_expr}\nORDER BY {order_alias} DESC")


def _m(field, label, unit=""):
    return _of(field, label, "measure", agg="NONE", unit=unit)


# 17 个运营数据集：每个对应 ARMS「用户分析」看板的一张图卡；标题中文。
_OPS_DATASETS = [
    {"name": "ARMS·用户与会话趋势", "chart_type": "timeseries_line",
     "preset": {"xCol": "stat_date", "yCol": "uv", "groupCol": "sessions"},
     "desc": "逐日独立用户数/会话数/PV/事件数趋势（env=prod，近90天）",
     "sql": _daily_sql('COUNT(DISTINCT "user.id") AS uv, COUNT(DISTINCT "session.id") AS sessions,\n       SUM(CASE WHEN event_type = \'view\' THEN 1 ELSE 0 END) AS pv, COUNT(*) AS events'),
     "fields": [_of("stat_date", "统计日期"), _m("uv", "独立用户数", "个"), _m("sessions", "会话数", "个"), _m("pv", "页面浏览量", "次"), _m("events", "事件总数", "个")]},
    {"name": "ARMS·页面浏览量趋势", "chart_type": "timeseries_line",
     "preset": {"xCol": "stat_date", "yCol": "pv"},
     "desc": "逐日页面浏览量(PV)趋势（env=prod）",
     "sql": _daily_sql("SUM(CASE WHEN event_type = 'view' THEN 1 ELSE 0 END) AS pv"),
     "fields": [_of("stat_date", "统计日期"), _m("pv", "页面浏览量", "次")]},
    {"name": "ARMS·人均会话数趋势", "chart_type": "timeseries_line",
     "preset": {"xCol": "stat_date", "yCol": "sessions_per_user"},
     "desc": "逐日人均会话数（会话数/访问用户数，env=prod）",
     "sql": _daily_sql('ROUND(COUNT(DISTINCT "session.id") * 1.0 / NULLIF(COUNT(DISTINCT "user.id"), 0), 2) AS sessions_per_user'),
     "fields": [_of("stat_date", "统计日期"), _m("sessions_per_user", "人均会话数", "个")]},
    {"name": "ARMS·人均会话时长趋势", "chart_type": "timeseries_line",
     "preset": {"xCol": "stat_date", "yCol": "time_per_user_sec"},
     "desc": "逐日人均会话时长（秒，env=prod）",
     "sql": _daily_sql('ROUND(SUM("session.time_spent") * 1.0 / NULLIF(COUNT(DISTINCT "user.id"), 0) / 1000, 1) AS time_per_user_sec'),
     "fields": [_of("stat_date", "统计日期"), _m("time_per_user_sec", "人均会话时长(秒)", "秒")]},
    {"name": "ARMS·异常用户数趋势", "chart_type": "timeseries_line",
     "preset": {"xCol": "stat_date", "yCol": "err_users", "groupCol": "err_sessions"},
     "desc": "逐日异常用户数/异常会话数趋势（env=prod）",
     "sql": _daily_sql('COUNT(DISTINCT CASE WHEN event_type = \'exception\' THEN "user.id" END) AS err_users,\n       COUNT(DISTINCT CASE WHEN event_type = \'exception\' THEN "session.id" END) AS err_sessions'),
     "fields": [_of("stat_date", "统计日期"), _m("err_users", "异常用户数", "个"), _m("err_sessions", "异常会话数", "个")]},
    {"name": "ARMS·资源成功率趋势", "chart_type": "timeseries_line",
     "preset": {"xCol": "stat_date", "yCol": "succ_rate"},
     "desc": "逐日资源/接口请求成功率趋势（env=prod）",
     "sql": _daily_sql('ROUND(SUM(CASE WHEN "resource.success" = 1 THEN 1 ELSE 0 END) * 100.0 / NULLIF(SUM(CASE WHEN "resource.success" IS NOT NULL THEN 1 ELSE 0 END), 0), 2) AS succ_rate'),
     "fields": [_of("stat_date", "统计日期"), _m("succ_rate", "资源成功率(%)", "%")]},
    {"name": "ARMS·事件类型分布", "chart_type": "pie",
     "preset": {"xCol": "event_type", "yCol": "events"},
     "desc": "各事件类型的事件数/用户数分布（env=prod）",
     "sql": _dist_sql('"event_type"', "event_type", 'COUNT(*) AS events, COUNT(DISTINCT "user.id") AS uv', "events"),
     "fields": [_of("event_type", "事件类型"), _m("events", "事件总数", "个"), _m("uv", "独立用户数", "个")]},
    {"name": "ARMS·省份用户分布", "chart_type": "bar",
     "preset": {"xCol": "province", "yCol": "uv"},
     "desc": "按省份的独立用户数/会话数分布（env=prod）",
     "sql": _dist_sql('"geo.province"', "province", 'COUNT(DISTINCT "user.id") AS uv, COUNT(DISTINCT "session.id") AS sessions', "uv"),
     "fields": [_of("province", "省份"), _m("uv", "独立用户数", "个"), _m("sessions", "会话数", "个")]},
    {"name": "ARMS·应用维度对比", "chart_type": "bar",
     "preset": {"xCol": "app_name", "yCol": "uv", "groupCol": "sessions"},
     "desc": "各应用的独立用户数/会话数/PV 对比（env=prod）",
     "sql": _dist_sql('"app.name"', "app_name", 'COUNT(DISTINCT "user.id") AS uv, COUNT(DISTINCT "session.id") AS sessions,\n       SUM(CASE WHEN event_type = \'view\' THEN 1 ELSE 0 END) AS pv', "uv"),
     "fields": [_of("app_name", "应用名称"), _m("uv", "独立用户数", "个"), _m("sessions", "会话数", "个"), _m("pv", "页面浏览量", "次")]},
    {"name": "ARMS·浏览器分布", "chart_type": "pie",
     "preset": {"xCol": "browser", "yCol": "uv"},
     "desc": "按浏览器的独立用户数分布（env=prod）",
     "sql": _dist_sql('"os.container"', "browser", 'COUNT(DISTINCT "user.id") AS uv', "uv"),
     "fields": [_of("browser", "浏览器"), _m("uv", "独立用户数", "个")]},
    {"name": "ARMS·设备类型分布", "chart_type": "pie",
     "preset": {"xCol": "device_type", "yCol": "uv"},
     "desc": "按设备类型的独立用户数分布（env=prod）",
     "sql": _dist_sql('"device.type"', "device_type", 'COUNT(DISTINCT "user.id") AS uv', "uv"),
     "fields": [_of("device_type", "设备类型"), _m("uv", "独立用户数", "个")]},
    {"name": "ARMS·操作系统分布", "chart_type": "pie",
     "preset": {"xCol": "os_type", "yCol": "uv"},
     "desc": "按操作系统的独立用户数分布（env=prod）",
     "sql": _dist_sql('"os.type"', "os_type", 'COUNT(DISTINCT "user.id") AS uv', "uv"),
     "fields": [_of("os_type", "操作系统"), _m("uv", "独立用户数", "个")]},
    {"name": "ARMS·应用类型分布", "chart_type": "pie",
     "preset": {"xCol": "app_type", "yCol": "uv"},
     "desc": "按应用类型(Web/小程序/原生)的独立用户数分布（env=prod）",
     "sql": _dist_sql('"app.type"', "app_type", 'COUNT(DISTINCT "user.id") AS uv', "uv"),
     "fields": [_of("app_type", "应用类型"), _m("uv", "独立用户数", "个")]},
    {"name": "ARMS·网络运营商分布", "chart_type": "pie",
     "preset": {"xCol": "isp", "yCol": "uv"},
     "desc": "按网络运营商的独立用户数分布（env=prod）",
     "sql": _dist_sql('"isp.name"', "isp", 'COUNT(DISTINCT "user.id") AS uv', "uv"),
     "fields": [_of("isp", "网络运营商"), _m("uv", "独立用户数", "个")]},
    {"name": "ARMS·页面热度Top", "chart_type": "bar",
     "preset": {"xCol": "view_name", "yCol": "pv"},
     "desc": "页面浏览量 PV Top（env=prod）",
     "sql": _dist_sql('"view.name"', "view_name", "SUM(CASE WHEN event_type = 'view' THEN 1 ELSE 0 END) AS pv", "pv"),
     "fields": [_of("view_name", "页面名称"), _m("pv", "页面浏览量", "次")]},
    {"name": "ARMS·页面资源P95耗时", "chart_type": "bar",
     "preset": {"xCol": "view_name", "yCol": "p95_res_ms"},
     "desc": "逐页资源加载 P95 耗时(ms)（env=prod）",
     "sql": _dist_sql('"view.name"', "view_name", 'approx_percentile("resource.duration", 0.95) AS p95_res_ms, AVG("resource.duration") AS avg_res_ms', "p95_res_ms", "event_type = 'resource'"),
     "fields": [_of("view_name", "页面名称"), _m("p95_res_ms", "资源P95耗时(ms)", "ms"), _m("avg_res_ms", "资源平均耗时(ms)", "ms")]},
    {"name": "ARMS·页面资源错误数", "chart_type": "bar",
     "preset": {"xCol": "view_name", "yCol": "err_count"},
     "desc": "逐页资源请求失败数（env=prod）",
     "sql": _dist_sql('"view.name"', "view_name", 'SUM(CASE WHEN "resource.success" = 0 THEN 1 ELSE 0 END) AS err_count, COUNT(*) AS res_total', "err_count", "event_type = 'resource'"),
     "fields": [_of("view_name", "页面名称"), _m("err_count", "资源错误数", "次"), _m("res_total", "资源请求数", "次")]},
]

# 历史 ASCII 命名的运营数据集（已被中文数据集取代，一次性清理）
_LEGACY_OPS_DATASETS = [
    "arms_rum_user_activity_trend", "arms_rum_region_device_distribution",
    "arms_rum_user_analysis", "arms_rum_page_engagement",
]


def _ensure_operational_datasets(ds_id: int) -> None:
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            ph = ",".join(["%s"] * len(_LEGACY_OPS_DATASETS))
            # 先删引用这些旧数据集的图表行，再删数据集（避免看板悬空引用）
            cur.execute(f"SELECT id FROM adh_datasets WHERE name IN ({ph})", tuple(_LEGACY_OPS_DATASETS))
            legacy_ids = [int(r["id"]) for r in cur.fetchall()]
            if legacy_ids:
                lph = ",".join(["%s"] * len(legacy_ids))
                cur.execute(f"DELETE FROM adh_charts WHERE source_type='dataset' AND source_id IN ({lph})", tuple(legacy_ids))
                cur.execute(f"DELETE FROM adh_datasets WHERE id IN ({lph})", tuple(legacy_ids))
                print(f"[ops-dataset] 已清理旧 ASCII 数据集 {len(legacy_ids)} 个")
        conn.commit()
    for spec in _OPS_DATASETS:
        with get_metadata_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM adh_datasets WHERE name=%s", (spec["name"],))
                exists = cur.fetchone()
        req = {
            "name": spec["name"], "description": spec["desc"],
            "sql_query": spec["sql"], "variables": _OPS_VARS, "dialect": "sls",
            "field_config": spec["fields"], "datasource_id": ds_id,
            "chart_type": spec["chart_type"], "chart_preset": spec["preset"],
        }
        if exists:
            with get_metadata_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE adh_datasets SET sql_query=%s, field_config=%s, chart_type=%s, "
                        "chart_preset=%s, description=%s, variables=%s WHERE id=%s",
                        (spec["sql"], json.dumps(spec["fields"], ensure_ascii=False),
                         spec["chart_type"], json.dumps(spec["preset"], ensure_ascii=False),
                         spec["desc"], json.dumps(_OPS_VARS, ensure_ascii=False), int(exists["id"])))
                conn.commit()
        else:
            dataset_service.create_dataset(req, {"user_id": OWNER_ID})
    print(f"[ops-dataset] 已同步 {len(_OPS_DATASETS)} 个中文命名运营数据集")


def _ensure_model(ds_id: int) -> int:
    """ARMS 观测建模为**业务本体**（kind='business'，主源 arms-prd-cn）。

    源本体已退役（合并为 business）；business 的 kind 级单例也已解除（激活/展开按
    name+datasource_id 收口），多个业务本体可并存，因此 ARMS 建为 business 不再顶掉现有业务本体。
    """
    doc = {
        "datasource_name": DS_NAME,
        "domain": "ARMS 可观测域（RUM 前端体验）",
        "description": ("阿里云 ARMS 用户体验监控(RUM)业务本体（主源 arms-prd-cn）：以 RUM 观测事件为核心对象，"
                        "覆盖页面浏览、资源加载、会话、异常、长任务与 Core Web Vitals 等前端体验指标。默认生产环境(app.env='prod')。"),
        "kind": "business",
        "datasource_id": ds_id,
        "objects": [{
            "key": OBJECT_KEY,
            "display_name": "RUM观测事件",
            "aliases": ["RUM", "观测事件", "前端体验事件", "用户体验事件", "rum_event", "logstore-rum"],
            "description": ("ARMS RUM 上报的单个前端观测事件（页面/资源/会话/交互/异常/长任务），"
                            "是前端体验分析的核心事实对象。默认仅统计生产环境(app.env=prod)。"),
            "properties": _properties_from_fields(),
        }],
    }
    json_content = json.dumps(doc, ensure_ascii=False, indent=2)
    now = ontology_service._now()
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, kind FROM adh_ontology_models WHERE name=%s AND datasource_id=%s",
                        ("ARMS RUM 观测本体", ds_id))
            row = cur.fetchone()
            if row:
                model_id = int(row["id"])
                # 确保 kind=business（源本体退役）并刷新 canonical doc
                cur.execute(
                    "UPDATE adh_ontology_models SET kind='business', json_content=%s, yaml_content=%s, "
                    "md_content=%s, updated_at=%s WHERE id=%s",
                    (json_content, ontology_service.to_yaml(doc), ontology_service.to_md(doc), now, model_id))
                conn.commit()
                print(f"[model] 已刷新为业务本体 (id={model_id}, kind=business)")
            else:
                model_id = ontology_service._gen_id()
                cur.execute(
                    "INSERT INTO adh_ontology_models "
                    "(id, datasource_id, name, kind, domain, status, json_content, yaml_content, md_content, "
                    "object_count, created_by, created_at, updated_at) "
                    "VALUES (%s,%s,%s,'business',%s,'draft',%s,%s,%s,1,%s,%s,%s)",
                    (model_id, ds_id, "ARMS RUM 观测本体", doc["domain"], json_content,
                     ontology_service.to_yaml(doc), ontology_service.to_md(doc),
                     str(OWNER_ID), now, now))
                conn.commit()
                print(f"[model] 已创建业务本体 draft (id={model_id})")
    ontology_service.activate(model_id)
    print(f"[model] 已激活 (id={model_id})")
    return model_id


def _restore_lianyao() -> None:
    """修复历史误伤：旧 business kind 级单例下，ARMS 误建为 business 激活时曾归档并删掉
    「联耀业务本体」的对象。business 单例已解除（激活按 name+datasource_id 收口）后不再发生。

    把联耀业务本体重新激活（从它的 canonical doc 重建 17 个对象）。现 ARMS 与联耀
    同为 business 但 name/datasource 不同，互不顶掉。幂等：仅当联耀非 active 时才修复。
    """
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, status FROM adh_ontology_models "
                        "WHERE kind='business' AND name=%s", ("联耀业务本体",))
            row = cur.fetchone()
    if not row:
        print("[repair] 未找到联耀业务本体，跳过")
        return
    if row["status"] == "active":
        print("[repair] 联耀业务本体已是 active，无需修复")
        return
    print("[repair] 重新激活联耀业务本体（恢复被误归档/误删的业务对象）...")
    ontology_service.activate(int(row["id"]))
    print("[repair] 联耀业务本体已恢复 active")


def _ensure_binding(ds_id: int, model_id: int) -> None:
    """把对象绑到数据集（bind_kind=dataset）。

    `adh_ontology_bindings.bind_kind` 是 DB 枚举（暂不含 'dataset'），而内联视图
    `adh_ontology_objects.execution_binding`（JSON）不受此限，且 binding_resolver Level 2
    （_resolve_from_objects_inline）本就消费它并允许 dataset 绑定。故写内联视图（Level 1 无行
    时回落 Level 2），避免为一个枚举去改治理表结构。
    """
    eb = {
        "bind_kind": "dataset",
        "dataset_ref": DATASET_NAME,
        "datasource_name": DS_NAME,
        "datasource_id": ds_id,
        "physical_table": "",
        "query_mode": "raw_source",
        "size_class": "large",
        "allow_full_scan": 1,
        "sync_state": "bound",
    }
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT execution_binding FROM adh_ontology_objects "
                "WHERE model_id=%s AND object_key=%s AND is_active=1",
                (model_id, OBJECT_KEY))
            row = cur.fetchone()
            if not row:
                raise SystemExit(f"对象 {OBJECT_KEY} 未展开到 adh_ontology_objects（先确认激活成功）")
            cur.execute(
                "UPDATE adh_ontology_objects SET execution_binding=%s "
                "WHERE model_id=%s AND object_key=%s AND is_active=1",
                (json.dumps(eb, ensure_ascii=False), model_id, OBJECT_KEY))
        conn.commit()
    print("[binding] 已写对象内联 execution_binding(bind_kind=dataset)")


def _ensure_dicts() -> None:
    # 清理旧版误建的 PII 字典行（用户名称/业务用户数 依赖 user.name，被全局敏感基线 block）
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM adh_dimensions WHERE bound_object_key=%s AND name=%s",
                        (OBJECT_KEY, "用户名称"))
            cur.execute("DELETE FROM adh_metrics WHERE bound_object_key=%s AND name=%s",
                        (OBJECT_KEY, "业务用户数"))
        conn.commit()
    conn = get_metadata_conn()
    try:
        with conn.cursor() as cur:
            for label, _col, _agg, _formula, _unit, _desc, _alias in _METRICS:
                cur.execute("SELECT id FROM adh_metrics WHERE name=%s", (label,))
                if cur.fetchone():
                    continue
                metrics_service.create_metric({
                    "display_name": label,
                    "name": _col,
                    "agg_type": _agg,
                    "formula": _formula,
                    "unit": _unit,
                    "description": f"{_desc}（ARMS RUM，默认 env=prod）",
                    "category": "可观测",
                    "bound_object_key": OBJECT_KEY,
                    "aliases": _alias,
                    "certified": 1,
                })
                print(f"[metric] 已创建 {label}")
            for label, col, cat, aliases, vlabels in _DIMS:
                cur.execute("SELECT id FROM adh_dimensions WHERE name=%s", (label,))
                if cur.fetchone():
                    continue
                metrics_service.create_dimension({
                    "display_name": label,
                    "name_en": col,
                    "target_column": col,
                    "category": cat,
                    "description": f"{label}（ARMS RUM 观测维度）",
                    "bound_object_key": OBJECT_KEY,
                    "aliases": aliases or [],
                    "value_labels": vlabels,
                    "certified": 1,
                })
                print(f"[dimension] 已创建 {label}")
    finally:
        conn.close()


def _ensure_datasource_access(ds_id: int) -> None:
    """为新数据源补角色授权（admin role_id=1）。

    新建数据源未入任何角色的 adh_role_datasource_access 时，取数会因 RBAC fail-closed 被拒
    （无权访问数据源）。按“新资源需默认可用”补 admin 授权（INSERT IGNORE 幂等），管理员仍可按角色收回。
    """
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO adh_role_datasource_access (role_id, datasource_id) "
                "VALUES (1, %s)", (ds_id,))
        conn.commit()
    print(f"[access] 已确保 admin(role 1) 对数据源 {ds_id} 的授权")


def _ensure_skill_binding() -> None:
    """把 arms-observability 技能挂到 admin AS-BOT（role 1 已授权 arms-prd-cn）。

    技能唯一事实源是 config/skills/arms-observability/SKILL.md（已落盘），这里只把它登记进
    bot 的 skills 数组（幂等），使 skill_loader 将其注入系统提示。
    """
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT skills FROM adh_as_bots WHERE as_bot_key='admin'")
            row = cur.fetchone()
            if not row:
                print("[skill] 未找到 admin AS-BOT，跳过绑定")
                return
            skills = row["skills"]
            if isinstance(skills, (str, bytes)):
                skills = json.loads(skills or "[]")
            skills = list(skills or [])
            if "arms-observability" in skills:
                print("[skill] admin 已绑定 arms-observability，跳过")
                return
            skills.append("arms-observability")
            cur.execute("UPDATE adh_as_bots SET skills=%s WHERE as_bot_key='admin'",
                        (json.dumps(skills, ensure_ascii=False),))
        conn.commit()
    print("[skill] 已将 arms-observability 绑定到 admin AS-BOT")


def _ensure_arms_dashboard() -> None:
    """重建「ARMS 用户体验监控」看板：17 张图卡，每张对应一个中文命名的运营数据集。

    图表全部 source_type='dataset'（不复制 SQL，口径唯一），创建后逐个 refresh 预填 data_cache
    （看板阅读页只读 data_cache，不预填会“暂无数据”）。数据集 id 按中文名反查，不写死。
    幂等：同名看板先删后建（图表随之重建）。
    """
    dash_name = "ARMS 用户体验监控"
    # 名称 → 数据集 id
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_dashboards WHERE name=%s", (dash_name,))
            old = cur.fetchone()
            if old:
                cur.execute("DELETE FROM adh_charts WHERE dashboard_id=%s", (int(old["id"]),))
                cur.execute("DELETE FROM adh_dashboards WHERE id=%s", (int(old["id"]),))
                conn.commit()
                print(f"[dashboard] 已删除旧看板《{dash_name}》(id={old['id']})，重建中")

    did = dashboard_service.create_dashboard({
        "name": dash_name,
        "description": ("面向运营/研发的 ARMS RUM 用户分析看板（env=prod，近90天）：用户与会话趋势、"
                        "页面浏览量、人均会话数/时长、事件类型/省份/应用/浏览器/设备/系统/应用类型/运营商分布、"
                        "页面热度与 P95 耗时、异常用户数、资源成功率与错误数。"),
        "workspace_id": 0, "is_public": True, "status": "enabled",
    }, OWNER_ID)
    print(f"[dashboard] 已创建《{dash_name}》 (id={did})")

    # 布局：12 列网格，顶部时间控件行 + 下方 4 列图卡；显式写 config.pageLayout.desktop，
    # 避免阅读页从像素坐标自动推导网格导致空洞/错位。
    def _page(cfg: dict, gx: int, gy: int, gw: int, gh: int) -> dict:
        c = dict(cfg)
        c["pageLayout"] = {"version": 1, "desktop": {"x": gx, "y": gy, "w": gw, "h": gh}}
        return c

    # 顶部：时间区间控件 + 查询按钮（widget 不参与取数，不预填缓存）
    chart_service.create_chart(did, {
        "name": "时间区间", "chart_type": "widget_daterange",
        "source_type": "query", "sql_query": "",
        "config": _page({"paramKey": "date_range", "bind_param": "date_start,date_end",
                         "label": "时间区间", "rangeMaxDays": 365}, 0, 0, 8, 3),
        "position": {"x": 0, "y": 0, "w": 620, "h": 60},
    })
    chart_service.create_chart(did, {
        "name": "查询", "chart_type": "widget_search",
        "source_type": "query", "sql_query": "",
        "config": _page({"label": "查询"}, 8, 0, 4, 3),
        "position": {"x": 640, "y": 0, "w": 300, "h": 60},
    })

    # 图卡直接用 raw_sql（SQL + 数据源挂在图上，配置页可见可编辑），动态时间参数
    # '{{date_start}}/{{date_end}}'；数据集仍保留同一口径（供数据集页/ChatBI）。
    with get_metadata_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM adh_datasources WHERE name=%s", (DS_NAME,))
            ds_row = cur.fetchone()
    ds_id_v = int(ds_row["id"]) if ds_row else 0

    COLS, CH_H = 4, 14  # 每行 4 图（w=3），图高 14 行
    created = 0
    for i, spec in enumerate(_OPS_DATASETS):
        col, row = i % COLS, i // COLS
        gx, gy = col * 3, 3 + row * CH_H  # 网格坐标（12 列，顶部 3 行留给控件）
        preset = {k: v for k, v in spec["preset"].items() if k in ("xCol", "yCol")}
        # 不带 groupCol：宽表多度量列当分组维度会让时序图渲染空（历史缺陷）
        preset["limit"] = 200
        preset["datasource_id"] = ds_id_v
        cfg = _page(preset, gx, gy, 3, CH_H)
        chart_sql = (spec["sql"]
                     .replace("${date_start}", "'{{date_start}}'")
                     .replace("${date_end}", "'{{date_end}}'") + "\nLIMIT 200")
        cid = chart_service.create_chart(did, {
            "name": spec["name"].replace("ARMS·", ""),
            "chart_type": spec["chart_type"],
            "source_type": "query", "query_source": "raw_sql",
            "sql_query": chart_sql,
            "config": cfg,
            "position": {"x": col * 361, "y": 70 + row * 336, "w": 345, "h": 320},
        })
        # 预填 data_cache（看板阅读页只读缓存，不刷则“暂无数据”）
        try:
            chart_service.refresh_chart(did, cid, params={}, user_id=OWNER_ID,
                                        workspace_id=0, username="admin")
        except Exception as e:  # noqa: BLE001 — 单图预填失败不阻断整看板，但显式报出
            print(f"[chart] 《{spec['name']}》 数据预填失败（可在页面手动刷新）: {str(e)[:120]}")
        created += 1
    print(f"[dashboard] 《{dash_name}》 已创建 时间控件 + {created} 张图卡（raw_sql，已预填数据）")


def main() -> None:
    from backend.common.db.datasource_db import get_datasource_by_name

    ds = get_datasource_by_name(DS_NAME)
    if not ds:
        raise SystemExit(f"数据源 {DS_NAME} 不存在")
    ds_id = int(ds["id"])
    print(f"== 目标数据源 {DS_NAME} (id={ds_id}) ==")
    _ensure_dataset(ds_id)
    _ensure_operational_datasets(ds_id)
    model_id = _ensure_model(ds_id)
    _restore_lianyao()
    _ensure_binding(ds_id, model_id)
    _ensure_datasource_access(ds_id)
    _ensure_dicts()
    _ensure_skill_binding()
    _ensure_arms_dashboard()
    print("== 构建完成 ==")


if __name__ == "__main__":
    main()
