"""「运营管理」AS-BOT 提示词回归：只专注业务运营，且知识/技能/数据三者分工必须写清。

锁住的行为（防止提示词漂移）：
1. 不得出现产品/平台自我叙述词（AI-DataHub、数据中台、数据分析师(NL2SQL)）——
   对用户只讲业务语言；
2. **知识问答是一等能力，不得推诿式婉拒**：绑了知识库与 knowledge_search 就要接住
   概念/口径/流程/事实类问题，查不到也得说"资料里没查到 + 下一步"（历史跑偏：
   先是一句"这个我帮不上忙"讲成平台说明书，改口后又退化成"这个不归我管哈"把问题推走）；
3. 必须保留"知识库=口径与背景、技能=方法与交付、目录+取数=唯一数字来源"的三件套
   分工与冲突裁决（缺了就会拿知识库旧指标名去取数，或口径含糊直接猜）；
4. 必须保留业务运营场景与"分站呈现、不把各站数字相加当全局值"的口径；
5. 必须保留"不写/不执行 SQL、不做数据工程"的红线（与工具授权里没有 execute_sql 一致）；
6. 绑定技能必须含 data-analysis / arms-observability / report-delivery，且不得绑
   pro-analysis —— 其正文要求 execute_sql，能力门控会拒，绑了就是"配了用不上"的空承诺；
7. **空结果不是结论**：0 行不得被说成"没采集/口径缺失"，必须先排时间/过滤/范围并附
   已查证据（历史缺陷：会话 1783069459140 把当天数据还在写入的 0 行断言成"EDM 渠道
   参数没进统计口径"，还建议用户换掉本来正确的归因口径）。

纯文本静态校验，不触碰数据库。
"""

import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(REPO, "docker", "mysql", "operations_manager_persona_migration.sql")

# 提示词正文（只看 SET 子句里的 description/system_prompt/persona，注释不参与断言）
_BODY_RE = re.compile(r"UPDATE adh_as_bots(?P<body>.*)WHERE as_bot_key", re.S)
_SKILLS_RE = re.compile(r"skills\s*=\s*JSON_ARRAY\((?P<items>[^)]*)\)", re.S)

#: 实现侧自我叙述词汇——出现在给用户看的人格描述里就是定位漂移
FORBIDDEN = ("AI-DataHub", "数据中台", "NL2SQL", "平台功能")


def _body() -> str:
    with open(MIGRATION, encoding="utf-8") as fh:
        sql = fh.read()
    matched = _BODY_RE.search(sql)
    assert matched, "迁移里找不到 UPDATE adh_as_bots … WHERE as_bot_key 语句"
    return matched.group("body")


def _skills() -> list:
    matched = _SKILLS_RE.search(_body())
    assert matched, "迁移里没有 skills = JSON_ARRAY(...) 绑定"
    return [x.strip().strip("'\"") for x in matched.group("items").split(",") if x.strip()]


def test_prompt_has_no_platform_self_narration():
    body = _body()
    leaked = [word for word in FORBIDDEN if word in body]
    assert not leaked, f"提示词仍在向用户讲述平台自身：{leaked}"


def test_knowledge_skill_data_contract_is_stated():
    """三件套分工与裁决必须写全，缺一条就会退化成"拿知识库当指标名来源"。"""
    body = _body()
    assert "知识库回答" in body and "口径" in body, "缺少「知识库=口径与业务背景」的分工"
    assert "已绑定技能回答" in body, "缺少「技能=分析方法与交付形态」的分工"
    assert "唯一的数字来源" in body, "缺少「实际取数是唯一数字来源」的裁决"
    assert "冲突裁决" in body, "缺少三者冲突时的裁决顺序"
    # 知识库不可用时必须显式告知（no-silent-degradation）：不得凭记忆编口径
    assert "查不到" in body, "缺少「知识库查不到时如实说明」的显式暴露"


def test_knowledge_qa_is_first_class_and_refusal_is_not_by_topic():
    """知识问答必须写成一等能力，且"拒"只能拒动作、不能拒话题。"""
    body = _body()
    assert "知识问答" in body and "一等能力" in body, "知识问答未作为一等职责写入提示词"
    assert "先查运营知识库" in body or "先查知识库" in body, "缺少「知识类问题先查知识库」的路径"
    assert "推诿" in body, "缺少「禁止推诿式婉拒」的行为约束（历史退化成'不归我管'）"
    assert "话题不设限" in body, "缺少「只拒动作、不拒话题」的边界口径"


def test_business_operations_scope_and_site_discipline():
    body = _body()
    for scene in ("转化漏斗", "复购留存", "归因", "周报"):
        assert scene in body, f"运营场景清单缺少「{scene}」"
    assert "相加" in body and "全局值" in body, "缺少「分站呈现、不把各站数字相加当全局值」的口径"


def test_no_sql_and_no_data_engineering_redline():
    """红线必须与工具授权一致：没有 execute_sql，就不该出现"可以写 SQL"的任何暗示。"""
    body = _body()
    assert "不写、不执行" in body or "绝不编写/执行" in body, "缺少「不编写/不执行 SQL」红线"
    assert "不做数据工程" in body, "缺少「不做数据工程侧的活」边界"


def test_bound_skills_cover_analysis_and_delivery_without_sql_skill():
    skills = _skills()
    assert "data-analysis" in skills, "运营角色必须绑通用分析剧本（趋势/异常/漏斗/留存/画像）"
    assert "report-delivery" in skills, "运营角色必须绑报告交付规范"
    assert "arms-observability" in skills, "运营角色必须绑线上体验观测分析"
    assert "pro-analysis" not in skills, "pro-analysis 需要 execute_sql，该角色没有此授权"


def test_empty_result_and_url_discipline_are_stated():
    """拿一条完整 URL 提问与“查到 0 行”这两个现场动作必须有约束（本次会话都输了）。"""
    body = _body()
    # 一条带参数的链接：先拆再取，精确那条链接与页面整体两个数一起给
    assert "两个数一起给" in body, "缺少「精确 URL 与页面整体一起给」的口径"
    assert "先查一次知识库" in body, "缺少「域名/渠道参数等实体身份先查知识库」的路径"
    # 空结果不得当成口径缺失的结论
    assert "不把" in body and "口径缺失" in body, "缺少「0 行不得断言采集口径缺失」的红线"
    assert "当前取不到数" in body, "缺少空结果时的标准说法（必须附已查证据）"
