"""敏感字段识别口径回归（backend/modules/gov/services/sensitive_suggest.py）。

背景：`adh_sensitive_fields` 现存 14 条**全局精确列名**规则（`phone`/`email`/`id_card`），
真实列名带业务前缀（`patient_phone`/`charger_id_no`/`patient_user_name`），
精确匹配一列都命中不了——实测 `t_case_records` 49 列，敏感策略 0 列命中，
13 列疑似敏感全部裸奔。这是数据护城河的实际失效，不是设计错。

识别口径（每条都被踩过，故逐条锁住）：
1. 布尔/标志位绝对不敏感，**优先于**强模式（`force_change_password` 不是密码值）；
2. 强模式**优先于**一般排除（`charger_id_no` 是身份证，不能被 `_no` 类宽模式误杀）；
3. URL / 经纬度 / 计数 / 主键外键 不算敏感（脱敏只损失可用性）；
4. 弱模式必须业务描述佐证（`case_name` 案例名称 ≠ 人名）；
5. mask_type 分级，不再一律 partial；
6. 表级「含个人信息」标签提升置信度（人工确认优先于纯规则）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.gov.services.sensitive_suggest import (
    classify_column,
    suggest_for_table,
)


def _c(col, comment="", desc=""):
    return classify_column(col, comment=comment, business_desc=desc)


class TestBooleanFlagsExcluded:
    """布尔/标志位优先于强模式 —— 名字带 password/email 也不是敏感值。"""

    def test_force_change_password_is_not_password(self):
        assert _c("force_change_password", "是否强制改密码") is None

    def test_is_confirm_email_is_not_email(self):
        assert _c("is_confirm_email", "是否已确认邮箱") is None

    def test_status_and_flag_columns(self):
        for col, desc in [("case_status", "案例状态"), ("has_order", "是否发送过订单"),
                          ("auto_finish_enable", "是否开启"), ("del_flag", "是否删除"),
                          ("need_sync", "是否需要同步")]:
            assert _c(col, desc) is None, col


class TestStrongPatternBeatsGenericExclude:
    """强模式优先于一般排除 —— 否则身份证会被 `_no` 这类宽模式误杀。"""

    def test_charger_id_no_is_id_card(self):
        r = _c("charger_id_no", "负责人身份证号")
        assert r is not None and r["category"] == "id_card"

    def test_legal_person_id_no_is_id_card(self):
        r = _c("legal_person_id_no", "法人身份证号")
        assert r is not None and r["category"] == "id_card"

    def test_id_no_is_id_card(self):
        r = _c("id_no", "证件号")
        assert r is not None and r["category"] == "id_card"

    def test_patient_phone_is_phone(self):
        r = _c("patient_phone", "病人手机号")
        assert r is not None and r["category"] == "phone"


class TestNonSensitiveExcluded:
    """URL / 经纬度 / 计数 / 编码 主键外键 —— 不承载敏感值。"""

    def test_url_columns(self):
        for col in ("url", "image_url", "logo_url", "download_url", "notify_url",
                    "business_license_url", "style_urls"):
            assert _c(col, "图片地址") is None, col

    def test_geo_and_counters(self):
        assert _c("longitude", "经度") is None
        assert _c("latitude", "纬度") is None
        assert _c("occupied_space_size", "占用空间大小") is None
        assert _c("patient_count", "病人数") is None

    def test_area_code_is_not_phone(self):
        assert _c("area_code", "负责人手机区号") is None

    def test_business_codes(self):
        """业务单号/编码不是证件号。"""
        assert _c("case_code", "订单编码") is None
        assert _c("order_no", "订单号") is None
        assert _c("company_code", "公司账户编码") is None

    def test_primary_and_foreign_keys(self):
        assert _c("id", "主键id") is None
        assert _c("company_id", "企业id") is None


class TestWeakPatternNeedsDescription:
    """弱模式必须描述佐证 —— 否则"案例名称"会被当成人名。"""

    def test_person_name_detected(self):
        r = _c("patient_user_name", "病人姓名")
        assert r is not None and r["category"] == "name"
        r2 = _c("doctor_user_name", "诊断医生姓名")
        assert r2 is not None and r2["category"] == "name"

    def test_non_person_name_excluded(self):
        assert _c("case_name", "案例名称") is None
        assert _c("company_name", "医院名称") is None
        assert _c("app_name", "应用名称") is None

    def test_name_without_description_is_not_guessed(self):
        """没描述就不猜（宁缺勿错）。"""
        assert _c("contact_name") is None

    def test_profile_columns(self):
        r = _c("patient_age", "病人年龄")
        assert r is not None and r["category"] == "profile"
        assert _c("patient_sex", "病人性别") is not None
        assert _c("birthday", "出生日期") is not None

    def test_credentials(self):
        assert _c("medical_license", "医生执照")["category"] == "credential"
        assert _c("openid", "外部映射ID")["category"] == "credential"
        assert _c("business_license_no", "营业执照号")["category"] == "credential"


class TestMaskTypeIsGraded:
    """mask_type 分级，不再一律 partial（原 /scan 的做法）。"""

    def test_password_is_full(self):
        assert _c("password", "密码")["mask_type"] == "full"

    def test_id_card_is_hash(self):
        assert _c("charger_id_no", "身份证号")["mask_type"] == "hash"

    def test_phone_is_partial(self):
        assert _c("patient_phone", "手机号")["mask_type"] == "partial"

    def test_level_reflects_severity(self):
        assert _c("password", "密码")["sensitivity_level"] == "critical"
        assert _c("charger_id_no", "身份证号")["sensitivity_level"] == "high"


class TestLabelRaisesConfidence:
    """表级「含个人信息」标签把置信度提到 high（人工确认优先于纯规则）。"""

    def test_labelled_table_raises_confidence(self):
        unlabelled = _c("address_detail", "地址详情")
        labelled = classify_column("address_detail", business_desc="地址详情",
                                   table_has_pii_label=True)
        assert unlabelled["confidence"] == "medium"
        assert labelled["confidence"] == "high"
        assert labelled["table_labelled_pii"] is True

    def test_suggest_for_table_propagates_label(self):
        cols = [{"column_name": "patient_phone", "business_desc": "病人手机号"}]
        hits = suggest_for_table(cols, table_has_pii_label=True)
        assert len(hits) == 1
        assert hits[0]["table_labelled_pii"] is True
        assert hits[0]["confidence"] == "high"


class TestRealWorldColumns:
    """用真实列名做端到端口径校验（t_case_records / t_user_company 实测样本）。"""

    def test_case_records_sensitive_columns_are_caught(self):
        expect = {
            "patient_phone": "phone", "patient_user_name": "name",
            "doctor_user_name": "name", "medical_license": "credential",
            "openid": "credential", "patient_age": "profile", "patient_sex": "profile",
        }
        descs = {
            "patient_phone": "病人手机号", "patient_user_name": "病人姓名",
            "doctor_user_name": "诊断医生姓名", "medical_license": "医生执照",
            "openid": "外部映射ID", "patient_age": "病人年龄", "patient_sex": "病人性别",
        }
        for col, cat in expect.items():
            r = classify_column(col, business_desc=descs[col], table_has_pii_label=True)
            assert r is not None, f"{col} 应被识别"
            assert r["category"] == cat, f"{col}: {r['category']} != {cat}"

    def test_case_records_noise_columns_are_not_flagged(self):
        for col, desc in [("case_name", "案例名称"), ("app_infos", "app版本信息"),
                          ("c_version", "Connect版本号"), ("serial_number", "硬件序列号"),
                          ("occupied_space_size", "case文件占用云空间大小"),
                          ("collection_status", "产品改善计划状态")]:
            assert classify_column(col, business_desc=desc) is None, col

    def test_company_sensitive_columns(self):
        for col, desc, cat in [("charger_id_no", "负责人身份证号", "id_card"),
                               ("charger_phone", "负责人手机号", "phone"),
                               ("charger_email", "负责人邮箱地址", "email"),
                               ("charger_name", "负责人姓名", "name")]:
            r = classify_column(col, business_desc=desc, table_has_pii_label=True)
            assert r is not None and r["category"] == cat, f"{col}"
