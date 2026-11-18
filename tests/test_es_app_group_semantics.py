"""ES 域「应用分组 / 三方集成 App」语义的静态护栏。

为什么需要：17 个第三方集成码的口径同时存在于三处——看板分桶数据集（ds#36/ds#56）、
语义层「应用分组」维度 value_labels、内置指标「三方集成App访问量」公式。任何一处漂移都会
要么少算（新增码被漏）、要么串口径（看板与语义层两个数对不上）。本用例把它们钉成同源。

同时锁住两个已踩过的坑：
- `ensure_dicts` 曾把 `value_labels` 写死 None → 分桶维度永远分组不出业务标签；
- 迁移里用户变量直接进 JSON_OBJECT/JSON_SET 会被二次编码成字符串 → 触发词变 1 个元素、
  场景永不命中。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.scripts import build_es_platform_logs_ontology as es_builder  # noqa: E402

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BUCKET_SQL = os.path.join(_REPO, "docker", "mysql", "app_usage_group_bucket_migration.sql")
_SEMANTICS_SQL = os.path.join(_REPO, "docker", "mysql", "app_usage_third_party_semantics_migration.sql")


def _sw_spec() -> dict:
    return next(s for s in es_builder.OBJECTS if s["key"] == es_builder.OBJ_SW)


def _dashboard_tp_codes() -> set[str]:
    """看板分桶 SQL 里「第三方集成App」那一桶的码值清单。"""
    text = open(_BUCKET_SQL, encoding="utf-8").read()
    m = re.search(r"WHEN \"appCode\.keyword\" IN \(((?:''[^'']+''(?:,\s*)?)+)\)"
                  r" THEN ''第三方集成App''", text, re.S)
    assert m, "看板分桶迁移里找不到「第三方集成App」桶（口径事实源）"
    return set(re.findall(r"''([^'']+)''", m.group(1)))


class TestThirdPartyAppCodesSameSource:
    def test_code_list_matches_dashboard_bucket(self):
        assert set(es_builder.SW_TP_APP_LIST) == _dashboard_tp_codes()

    def test_code_count_and_no_duplicates(self):
        assert len(es_builder.SW_TP_APP_LIST) == len(set(es_builder.SW_TP_APP_LIST)) == 17

    def test_group_labels_bucket(self):
        labels = es_builder.SW_APP_GROUP_LABELS
        for code in es_builder.SW_TP_APP_LIST:
            assert labels[code] == "第三方集成App"
        assert labels["NewOrthoSim"] == labels["OrthoSim"] == "Ortho Simulation"
        # 只登记分桶码值，不给自研单列硬编中文名（枚举不编标签原则）
        assert len(labels) == 19


class TestSwObjectSemantics:
    def test_dimension_app_group_has_value_labels(self):
        dim = next(d for d in _sw_spec()["dimensions"] if d["name"] == "应用分组")
        assert dim["target_column"] == "appCode.keyword"
        assert dim["value_labels"], "应用分组是分桶维度，value_labels 不得为空"
        assert all(d.get("name_en") for d in _sw_spec()["dimensions"])

    def test_other_dimensions_keep_no_labels(self):
        # 枚举「只登记码值」原则：除分桶维度外一律不填 value_labels
        for d in _sw_spec()["dimensions"]:
            if d["name"] == "应用分组":
                continue
            assert d.get("value_labels") in (None, {}), d["name"]

    def test_tp_open_metric_formula_covers_all_codes(self):
        m = next(x for x in _sw_spec()["metrics"] if x["name"] == "三方集成App访问量")
        assert "'openApp'" in m["formula"]
        for code in es_builder.SW_TP_APP_LIST:
            assert f"'{code}'" in m["formula"]
        assert m["formula"].startswith("SUM(CASE WHEN")

    def test_tp_user_metric_is_distinct_with_case(self):
        m = next(x for x in _sw_spec()["metrics"] if x["name"] == "三方集成App使用用户数")
        assert m["formula"].startswith("COUNT(DISTINCT CASE WHEN")
        assert m["formula"].endswith('ELSE NULL END)')
        for code in ("CircleOneIntegration", "HeyGearsIntegration"):
            assert f"'{code}'" in m["formula"]

    def test_open_app_metric_has_visit_aliases(self):
        m = next(x for x in _sw_spec()["metrics"] if x["name"] == "打开应用次数")
        assert {"应用访问量", "App访问量"}.issubset(set(m["aliases"]))

    def test_object_aliases_point_to_app_usage(self):
        aliases = _sw_spec()["aliases"]
        assert any("三方集成App" in a for a in aliases)

    def test_object_description_names_the_wrong_neighbours(self):
        # 反吸附：正文必须点名"不是接口调用日志、不是集成记录"
        desc = _sw_spec()["description"]
        assert "第三方接口调用日志" in desc and "集成记录" in desc


class TestBuilderWiring:
    def test_ensure_dicts_passes_value_labels_through(self):
        src = open(es_builder.__file__, encoding="utf-8").read()
        assert '"value_labels": d.get("value_labels")' in src
        assert '"value_labels": None, "certified": 0' not in src


class TestSemanticsMigration:
    @classmethod
    def setup_class(cls):
        cls.sql = open(_SEMANTICS_SQL, encoding="utf-8").read()

    def test_isv_dimension_alias_drops_example_value(self):
        # 示例值别名是字面吸附根因：必须换成中性别名
        assert "JSON_ARRAY('应用域标识', '集成来源应用码')" in self.sql
        assert "'集成来源应用标识，如 CircleOneIntegration'" not in self.sql

    def test_isv_metric_description_states_boundary(self):
        assert "不是 App 打开次数" in self.sql
        assert "三方集成App访问量" in self.sql

    def test_scenario_registered_with_es_subject(self):
        assert "'三方集成 App 与自研 App 使用量分析'" in self.sql
        assert "'subjects', JSON_ARRAY('cloud_es_software_app_operation_log')" in self.sql
        assert "WHERE NOT EXISTS" in self.sql, "场景插入必须按标题判重（幂等）"

    def test_triggers_are_not_double_encoded(self):
        # 用户变量是字符串：进 JSON_OBJECT/JSON_SET 必须 CAST AS JSON，否则触发词变字符串
        assert "CAST(@qp_triggers AS JSON)" in self.sql

    def test_scenario_convergence_does_not_touch_content(self):
        tail = self.sql.split("-- ④")[1]
        assert "SET metadata = JSON_SET" in tail
        assert "content =" not in tail, "收敛只对齐路由参数，不得覆盖正文（人工修订会被打回）"
