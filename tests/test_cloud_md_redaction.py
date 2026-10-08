"""to_cloud_md 脱敏渲染单测: 上云文档不得含物理表/列名/JOIN 表达式/formula 原文。

对照 to_md(内部, 含物理信息), 验证 to_cloud_md 白名单渲染把敏感实现细节全部剔除。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services.ontology_service import to_md, to_cloud_md


DOC = {
    "domain": "医疗",
    "description": "就诊与费用本体",
    "objects": [
        {
            "key": "case",
            "display_name": "案例",
            "aliases": ["工单", "case"],
            "description": "一条就诊案例记录",
            "primary_table": "t_case_records",
            "source_ref": "stardb.t_case_records",
            "execution_binding": {
                "datasource_id": 7,
                "datasource_name": "prod-hospital-db",
                "physical_table": "t_case_records",
                "catalog_ref": "cat://internal/host:3306",
                "catalog_name": "internal",
                "db_name": "stardb",
                "bind_kind": "primary",
                "template_ref": "",
                "query_mode": "raw_source",
                "permission_tokens": ["secret-token-abc"],
            },
            "properties": [
                {"column": "patient_phone", "type": "varchar", "name": "患者手机号",
                 "description": "就诊人联系电话"},
                {"column": "case_status", "type": "int", "name": "案例状态",
                 "enum": ["仅表单", "需要处理"]},
                {"column": "salary_level", "type": "int"},  # 无业务名 → 整条跳过
            ],
            "links": [
                {"type": "references", "target": "hospital",
                 "join": "t_case_records.hospital_id = t_hospital.id",
                 "cardinality": "many_to_one", "description": "关联就诊医院"},
            ],
            "metrics": [
                {"name": "案例数量", "formula": "COUNT(DISTINCT patient_phone)",
                 "description": "去重案例计数"},
            ],
        },
    ],
}

# 必须从对外文档中消失的物理/敏感标识
FORBIDDEN = [
    "t_case_records", "patient_phone", "salary_level", "hospital_id", "t_hospital",
    "COUNT", "DISTINCT", "cat://internal", "prod-hospital-db", "secret-token-abc",
    "stardb", "case_status", "hospital_id = ",
]

# 必须保留的业务语义
REQUIRED = ["案例", "工单", "患者手机号", "案例状态", "仅表单", "案例数量", "references hospital"]


class TestToMdContainsPhysical:
    """先确认内部 to_md 确实含物理信息(否则本测试无意义)。"""

    def test_internal_md_has_physical(self):
        internal = to_md(DOC)
        assert "t_case_records" in internal
        assert "patient_phone" in internal
        assert "COUNT" in internal


class TestToCloudMdRedaction:
    def setup_method(self):
        self.cloud = to_cloud_md(DOC)

    def test_strips_all_physical_identifiers(self):
        for token in FORBIDDEN:
            assert token not in self.cloud, f"泄露敏感标识: {token}"

    def test_keeps_business_semantics(self):
        for token in REQUIRED:
            assert token in self.cloud, f"丢失业务语义: {token}"

    def test_property_without_business_name_dropped(self):
        # salary_level 无 name, 整条属性不得出现(既不含列名也不渲染)
        assert "salary_level" not in self.cloud

    def test_strip_notice_present(self):
        assert "已剔除物理表" in self.cloud


class TestTemplateVarsRedaction:
    def test_template_ref_kept_but_sql_stripped(self):
        doc = {
            "domain": "分析",
            "objects": [{
                "key": "funnel", "display_name": "漏斗",
                "execution_binding": {
                    "bind_kind": "sql_template", "template_ref": "tpl-funnel-7d",
                    "query_mode": "raw_source", "physical_table": "",
                    "catalog_ref": "cat://x",
                },
            }],
        }
        vars_fn = lambda ref: {"start_date": {"type": "date"}, "scene": {"type": "string"}}
        cloud = to_cloud_md(doc, template_vars_fn=vars_fn)
        assert "tpl-funnel-7d" in cloud          # 逻辑模板引用保留
        assert "start_date(date)" in cloud        # 参数名+类型保留
        assert "cat://x" not in cloud             # catalog_ref 剔除
