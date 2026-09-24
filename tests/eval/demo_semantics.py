"""golden-question 评测离线内存语义目录: 绑定 + 物理列 + 指标/维度字典。

不依赖元数据库: runner 用这里的表把 _ColumnResolver._load_* 打桩, 从而
对"自然语言 intent -> 编译解析质量"做确定性评测(种子解析是三因子中的瓶颈)。
含两个数据源(hospital / retail)以覆盖"跨源同名指标各自绑定本源"的解析。
"""
from services.shared.semantics.models import Guardrail, ResolvedBinding


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
}

# SQL 模板(内存版), 供"模板缺参"用例
TEMPLATES: dict[str, dict] = {
    "tpl-funnel": {
        "template_id": "tpl-funnel", "template_name": "转化漏斗",
        "sql_template": "SELECT * FROM t_case_records WHERE create_time >= ${start_date} AND scene = ${scene}",
        "variables": {"start_date": {"type": "date"}, "scene": {"type": "string"}},
        "tpl_dialect": "generic",
    },
}

# (datasource_id, object_key) -> 绑定。object 未在此表 = 未绑定(用于"未绑定对象"用例)。
def _b(ds: int, obj: str, table: str, **kw) -> ResolvedBinding:
    return ResolvedBinding(object_key=obj, datasource_id=ds, physical_table=table,
                           db_name="stardb", **kw)


BINDINGS: dict[tuple[int, str], ResolvedBinding] = {
    (1, "case"): _b(1, "case", "t_case_records"),
    (2, "case"): _b(2, "case", "t_ticket_records"),
    (1, "funnel"): _b(1, "funnel", "", bind_kind="sql_template", template_ref="tpl-funnel",
                      guardrail=Guardrail(query_mode="raw_source")),
    # PG 方言源, 验证时间函数按方言编译
    (3, "case"): _b(3, "case", "t_case_records", db_type="postgres"),
}


def binding_for(datasource_id: int, object_key: str):
    return BINDINGS.get((datasource_id, (object_key or "").strip()))
