"""golden-question 评测离线内存语义目录: 绑定 + 物理列 + 指标/维度字典。

不依赖元数据库: runner 用这里的表把 _ColumnResolver._load_* 打桩, 从而
对"自然语言 intent -> 编译解析质量"做确定性评测(种子解析是三因子中的瓶颈)。
含两个数据源(hospital / retail)以覆盖"跨源同名指标各自绑定本源"的解析。
"""
import json

from backend.semantics.models import Guardrail, ResolvedBinding


# 物理表 -> {列名: {data_type, business_desc}}
PHYS: dict[str, dict[str, dict]] = {
    "t_case_records": {
        "create_time": {"data_type": "datetime", "business_desc": "创建时间"},
        "case_status": {"data_type": "int", "business_desc": "案例状态"},
        "company_name": {"data_type": "varchar", "business_desc": "医院名称"},
        "patient_phone": {"data_type": "varchar", "business_desc": ""},
        "amount": {"data_type": "decimal", "business_desc": "费用金额"},
        "del_flag": {"data_type": "int", "business_desc": ""},
    },
    "t_ticket_records": {
        "ticket_time": {"data_type": "datetime", "business_desc": "工单时间"},
        "region": {"data_type": "varchar", "business_desc": "区域"},
        "revenue": {"data_type": "decimal", "business_desc": "营收"},
        "ticket_id": {"data_type": "bigint", "business_desc": ""},
    },
    # ── 跨对象 JOIN 素材(B2/B3): case → customer → region ──────────
    "t_customer": {
        "id": {"data_type": "bigint", "business_desc": ""},
        "name": {"data_type": "varchar", "business_desc": "客户名称"},
        "industry": {"data_type": "varchar", "business_desc": "所属行业"},
        "region_code": {"data_type": "varchar", "business_desc": ""},
    },
    "t_region": {
        "region_code": {"data_type": "varchar", "business_desc": ""},
        "region_name": {"data_type": "varchar", "business_desc": "区域名称"},
    },
}

# 物理表 -> 维度字典(name 为主键)
DIMS: dict[str, dict[str, dict]] = {
    "t_case_records": {
        "创建日期": {"name": "创建日期", "name_en": "create_date", "target_column": "create_time",
                   "category": "时间", "aliases": ["创建时间", "case created at"], "value_labels": {}},
        "案例状态": {"name": "案例状态", "name_en": "case_status", "target_column": "case_status",
                   "category": "状态", "aliases": [],
                   "value_labels": {"0": "仅表单", "1": "需要处理", "3": "已上传"}},
        "就诊医院": {"name": "就诊医院", "name_en": "hospital", "target_column": "company_name",
                   "category": "组织", "aliases": ["医院"], "value_labels": {}},
    },
    "t_ticket_records": {
        "工单日期": {"name": "工单日期", "name_en": "ticket_date", "target_column": "ticket_time",
                   "category": "时间", "aliases": ["工单创建时间"], "value_labels": {}},
        "区域": {"name": "区域", "name_en": "region", "target_column": "region",
                "category": "地理", "aliases": ["地区"],
                "value_labels": {"EAST": "华东", "SOUTH": "华南"}},
    },
    "t_customer": {
        "行业": {"name": "行业", "name_en": "industry", "target_column": "industry",
                "category": "分类", "aliases": ["所属行业"], "value_labels": {}},
    },
    "t_region": {
        "区域名": {"name": "区域名", "name_en": "region_name", "target_column": "region_name",
                 "category": "地理", "aliases": ["大区名"], "value_labels": {}},
    },
}

# 物理表 -> 指标字典。"案例数量"在两个源同名但口径不同(跨源同名指标)。
METRICS: dict[str, dict[str, dict]] = {
    "t_case_records": {
        "案例数量": {"name": "案例数量", "name_en": "case_count", "formula": "COUNT(CASE WHEN del_flag=0 THEN id END)",
                   "formula_dsl": None, "agg_type": "COUNT", "default_agg": "COUNT",
                   "unit": "个", "description": "未删除案例去重计数", "aliases": ["病例数", "total_cases"],
                   "formula_dialects": None},
        "总费用": {"name": "总费用", "name_en": "total_amount", "formula": "SUM(amount)",
                 "formula_dsl": None, "agg_type": "SUM", "default_agg": "SUM",
                 "unit": "元", "description": "费用金额合计", "aliases": ["费用合计"],
                 "formula_dialects": None},
    },
    "t_ticket_records": {
        "案例数量": {"name": "案例数量", "name_en": "case_count", "formula": "COUNT(DISTINCT ticket_id)",
                   "formula_dsl": None, "agg_type": "COUNT", "default_agg": "COUNT",
                   "unit": "单", "description": "工单数", "aliases": ["工单量"],
                   "formula_dialects": None},
        "营收": {"name": "营收", "name_en": "revenue", "formula": "SUM(revenue)",
                "formula_dsl": None, "agg_type": "SUM", "default_agg": "SUM",
                "unit": "元", "description": "营收合计", "aliases": ["收入"],
                "formula_dialects": None},
    },
    "t_customer": {
        "客户数": {"name": "客户数", "name_en": "customer_count", "formula": "COUNT(DISTINCT name)",
                 "formula_dsl": None, "agg_type": "COUNT", "default_agg": "COUNT",
                 "unit": "个", "description": "客户去重计数", "aliases": [],
                 "formula_dialects": None},
    },
}

# 查询数据集(内存版), 供"缺参/未声明参数"与"行为路径/漏斗多方言"用例。
# 数据集是平台内唯一的"可信任预制 SQL"载体：
#   sql_bodies   generic = 可移植写法(MySQL/Doris/PG 同构), sls = Presto 风味专写
#   field_config 声明后其结果可在语义层再聚合(按维度下钻)
#   touched_tables 保存时静态提表, 使预制 SQL 查询同样进 RLS 改写
_FUNNEL_SQL = (
    "SELECT * FROM t_case_records WHERE create_time >= ${start_date} AND scene = ${scene}")
_FUNNEL_SLS_SQL = (
    'SELECT "pcId" AS entity, "scene" AS scene FROM observability_rum '
    'WHERE "__time__" >= ${start_date}')

# 数据源 id → 业务名（供 planner._load_datasource_rows 打桩：数据集「可用数据源范围」
# 按业务名登记，解析不到 id 的源名一律 fail-closed，所以评测侧必须提供同一份清单）
DATASOURCES: dict[int, str] = {
    1: "hospital", 2: "retail", 3: "hospital-pg",
    4: "doris-analytics", 5: "sls-observability",
}

DATASETS: dict[str, dict] = {
    "转化漏斗": {
        "id": 9001, "name": "转化漏斗", "description": "案例转化漏斗(预制 SQL)",
        # 可用数据源范围：MySQL(hospital) / Doris / SLS 三源同构可跑（多方言正文用例的前提）
        "source_type": "sql", "object_key": "", "datasource_id": 0,
        "available_datasources": json.dumps(
            ["hospital", "doris-analytics", "sls-observability"], ensure_ascii=False),
        "sql_query": _FUNNEL_SQL,
        "variables": json.dumps({"start_date": {"type": "date"}, "scene": {"type": "string"}}),
        "sql_bodies": json.dumps({"generic": _FUNNEL_SQL, "sls": _FUNNEL_SLS_SQL}),
        "touched_tables": json.dumps(["t_case_records"]),
        "field_config": json.dumps([
            {"field": "create_time", "label": "统计日期", "role": "dimension", "is_time": True},
            {"field": "scene", "label": "场景", "role": "dimension"},
            {"field": "step_order", "label": "步骤序号", "role": "dimension"},
            {"field": "company_name", "label": "到达实体数", "role": "measure",
             "expr": "COUNT(DISTINCT company_name)", "unit": "个"},
        ], ensure_ascii=False),
        "certified": 1,
    },
}

# (datasource_id, object_key) -> 绑定。object 未在此表 = 未绑定(用于"未绑定对象"用例)。
def _b(ds: int, obj: str, table: str, **kw) -> ResolvedBinding:
    return ResolvedBinding(object_key=obj, datasource_id=ds, physical_table=table,
                           db_name="stardb", **kw)


BINDINGS: dict[tuple[int, str], ResolvedBinding] = {
    (1, "case"): _b(1, "case", "t_case_records"),
    (2, "case"): _b(2, "case", "t_ticket_records"),
    (1, "funnel"): _b(1, "funnel", "", bind_kind="dataset", dataset_ref="转化漏斗",
                      guardrail=Guardrail(query_mode="raw_source")),
    # 多方言数据集对象: Doris / SLS 引擎取各自正文, MySQL 族走 generic
    (4, "funnel"): _b(4, "funnel", "", bind_kind="dataset", dataset_ref="转化漏斗",
                      db_type="doris", guardrail=Guardrail(query_mode="raw_source")),
    (5, "funnel"): _b(5, "funnel", "", bind_kind="dataset", dataset_ref="转化漏斗",
                      db_type="sls", guardrail=Guardrail(query_mode="raw_source")),
    # PG 方言源, 验证时间函数按方言编译
    (3, "case"): _b(3, "case", "t_case_records", db_type="postgres"),
    # 跨对象 JOIN 素材(B2/B3)
    (1, "customer"): _b(1, "customer", "t_customer"),
    (1, "region"): _b(1, "region", "t_region"),
}


# 跨对象关系(canonical links 的评测投影): 供 JOIN 编译用例。
LINKS: dict = {
    "edges": {
        "case": [("customer", "t_case_records.company_name = t_customer.name")],
        "customer": [("region", "t_customer.region_code = t_region.region_code")],
    },
    "names": {
        "case": "case", "customer": "customer", "客户": "customer",
        "region": "region", "地区": "region",
    },
}


def binding_for(datasource_id: int, object_key: str):
    return BINDINGS.get((datasource_id, (object_key or "").strip()))


def links_for(datasource_id: int = 0) -> dict:
    """planner._load_object_links 打桩入口(B2/B3)。"""
    return LINKS


def datasource_rows_for(names=None, exec_id: int = 0) -> list[dict]:
    """planner._load_datasource_rows 打桩入口（按名/按 id 返回内存清单行）。"""
    rows = [{"id": i, "name": n} for i, n in DATASOURCES.items()]
    if names:
        want = {str(x).strip() for x in names}
        rows = [r for r in rows if r["name"] in want]
    if int(exec_id or 0):
        rows = rows + [r for r in rows if r["id"] == int(exec_id)]
    return rows


def foreign_binding_for(obj_key: str, datasource_id: int = 0):
    """planner._resolve_foreign_binding 打桩入口(B2/B3)。"""
    return BINDINGS.get((datasource_id, (obj_key or "").strip())) \
        or BINDINGS.get((1, (obj_key or "").strip()))
