"""本体质量与口径权威性标注（P4 剩余）回归。

两件事：
1. **上云文档标注认证状态**：未认证口径必须标 `[草稿]`，不得默认当权威答案
   （认证机制一直在、但 55 个口径零认证，默认标权威就是在误导业务决策）；
2. **本体质量校验器**：把 ontology-modeling 的建模规范变成可执行检查，
   只报告不阻断（阻断在 activate），产出建模待办而非缺陷清单。

纯函数，不触碰数据库。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services.ontology_service import (
    _cert_label,
    check_doc_quality,
    to_cloud_md,
)


class TestCertLabel:
    def test_defaults_to_draft_without_entry(self):
        """不传条目一律标草稿——宁可保守也不冒充权威。"""
        assert _cert_label(None) == "[草稿]"
        assert _cert_label({}) == "[草稿]"

    def test_reads_certified_flag_from_dict_entry(self):
        assert _cert_label({"name": "GMV", "certified": True}) == "[已认证]"
        assert _cert_label({"name": "GMV", "certified": False}) == "[草稿]"

    def test_owner_is_not_required_for_label(self):
        """标注只看 certified，owner 是展示信息。"""
        assert _cert_label({"certified": True, "owner": ""}) == "[已认证]"


class TestCloudMdMarksCertification:
    def _doc(self):
        return {"domain": "测试域", "description": "d", "objects": [{
            "key": "case", "display_name": "案例",
            "metrics": [{"name": "is_active", "description": "是否活跃"}],
        }]}

    def test_no_dict_metrics_means_no_claim_section(self):
        """不传字典口径时不得把派生字段冒充成口径——宁可少说。"""
        md = to_cloud_md(self._doc())
        assert "指标（业务口径）" not in md
        assert "派生字段" in md
        assert "is_active" in md

    def test_dict_metrics_rendered_with_certification(self):
        md = to_cloud_md(self._doc(), dict_metrics={"case": [
            {"name": "GMV", "description": "总额", "certified": True, "owner": "张三"},
            {"name": "AI 会话数", "description": "会话", "certified": False, "owner": ""},
        ]})
        assert "指标（业务口径）" in md
        gmv_line = [l for l in md.splitlines() if "GMV" in l][0]
        ai_line = [l for l in md.splitlines() if "AI 会话数" in l][0]
        assert "[已认证]" in gmv_line and "张三" in gmv_line
        assert "[草稿]" in ai_line

    def test_derived_metrics_are_not_labelled_as_claim(self):
        """派生字段不带认证标注（它不是口径，不该参与权威性判断）。"""
        md = to_cloud_md(self._doc(), dict_metrics={"case": [
            {"name": "GMV", "certified": True}]})
        derived_line = [l for l in md.splitlines() if "is_active" in l][0]
        assert "[已认证]" not in derived_line and "[草稿]" not in derived_line

    def test_dict_metrics_for_other_object_not_leaked(self):
        md = to_cloud_md(self._doc(), dict_metrics={"other": [
            {"name": "别的指标", "certified": True}]})
        assert "别的指标" not in md


class TestDocQuality:
    def _obj(self, **kw):
        base = {"key": "case", "display_name": "案例", "primary_table": "t_case",
                "properties": [{"column": "t_case.id", "name": "编号"}],
                "links": [], "metrics": []}
        base.update(kw)
        return base

    def test_clean_doc_scores_100(self):
        r = check_doc_quality({"objects": [self._obj()]})
        assert r["score"] == 100 and r["total"] == 0
        assert "质量良好" in r["note"]

    def test_detects_borrowed_display_name(self):
        r = check_doc_quality({"objects": [self._obj(display_name="Case")]})
        assert r["by_category"].get("naming") == 1
        assert r["errors"] == 0            # 建议项，非阻断

    def test_chinese_display_name_passes(self):
        r = check_doc_quality({"objects": [self._obj(display_name="案例记录")]})
        assert r["by_category"].get("naming") is None

    def test_detects_property_without_business_name(self):
        r = check_doc_quality({"objects": [self._obj(
            properties=[{"column": "t_case.id", "name": "编号"},
                        {"column": "t_case.status", "name": ""}])]})
        assert r["by_category"].get("property") == 1

    def test_detects_dangling_link(self):
        """link 指向 doc 内不存在的对象 = 阻断级（§4 宁阻断勿悬空）。"""
        r = check_doc_quality({"objects": [self._obj(
            links=[{"target": "不存在的对象", "join": "a=b"}])]})
        assert r["by_category"].get("link") == 1
        assert r["errors"] == 1

    def test_valid_link_target_ok(self):
        """目标在 key 或别名里都算命中。"""
        r = check_doc_quality({"objects": [
            self._obj(links=[{"target": "user", "join": "a=b"}]),
            self._obj(key="user", display_name="用户", aliases=["客户"]),
        ]})
        assert r["by_category"].get("link") is None

    def test_detects_select_shaped_formula(self):
        """整条 SELECT 的 formula 与聚合表达式形状不一致（可能含物理列）。"""
        r = check_doc_quality({"objects": [self._obj(
            metrics=[{"name": "m1", "formula": "SELECT a FROM t LIMIT 5"},
                     {"name": "m2", "formula": "COUNT(*)"}])]})
        assert r["by_category"].get("metric") == 1

    def test_detects_duplicate_keys(self):
        r = check_doc_quality({"objects": [
            self._obj(key="case_file"), self._obj(key="casefile")]})
        assert r["by_category"].get("duplicate") == 1
        assert r["errors"] >= 1

    def test_score_not_zeroed_by_many_warnings(self):
        """评分按规模封顶：22 个建议项不该把分打到 0（否则看不出阻断级已清零）。"""
        objs = [self._obj(key=f"o{i}", display_name=f"Obj{i}") for i in range(22)]
        r = check_doc_quality({"objects": objs})
        assert r["errors"] == 0
        assert r["score"] > 0

    def test_errors_cost_more_than_warnings(self):
        warn_only = check_doc_quality({"objects": [self._obj(display_name="Case")]})
        err_doc = check_doc_quality({"objects": [self._obj(
            links=[{"target": "ghost", "join": "a=b"}])]})
        assert err_doc["score"] < warn_only["score"]
