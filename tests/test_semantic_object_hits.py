"""get_metrics 对象级命中 + 跨对象可执行写法单测（build_object_hits）。

为什么不依赖库：这两个东西是**发现层**的提示，判定口径全在内存里（本体 doc 的
objects[].links + 作用域内字典行），用假数据就能锁死，不必连元数据库。
锁的缺陷（2026-11-26 联耀实测）：
  ① 字典行检索看不到本体对象自身的业务名/别名（「第三方集成」命不中 isv），模型只看到
     半套「用户类型」就反问口径；
  ② 跨对象链路（isv → 商户绑定用户 → 用户企业关系）没有任何可发现提示；
  ③ 提示若沿跨跳占位符 join 给出，模型会照抄一个编译不了的写法；
  ④ 主对象自己已有的字段名不得被推荐到别的对象上（「用户类型」指向 user 主档的简化分类
     而不是 user_company_membership 的权威 0–11 编码）。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.mind.execution.sdk_tools.semantic_tools import (  # noqa: E402
    build_object_hits,
)

OBJECTS = [
    {"key": "isv", "display_name": "ISV", "aliases": ["第三方集成", "开放平台商户"],
     "description": "开放平台第三方服务商",
     "links": [
         # 跨跳占位符：不可编译，不得据此推荐 user 上的字段
         {"target": "user", "join": "isv.user_id → user.id"},
         {"target": "isv_user_binding", "join": "isv.appid = isv_user_binding.appid"},
     ]},
    {"key": "isv_user_binding", "display_name": "商户绑定用户", "aliases": [],
     "description": "商户与平台用户的绑定关系",
     "links": [
         {"target": "user_company_membership",
          "join": "isv_user_binding.user_code = user_company_membership.user_code"},
         {"target": "company", "join": ""},   # 空 join：不可编译
     ]},
    {"key": "user_company_membership", "display_name": "用户企业关系",
     "aliases": ["用户企业关联"], "description": "企业成员关系（0–11 用户类型编码）",
     "links": []},
    {"key": "company", "display_name": "企业", "aliases": [], "description": "", "links": []},
]

DICT_ROWS = [
    {"object": "user_company_membership", "kind": "dimension", "name": "企业用户类型",
     "name_en": "membership_user_type", "aliases": ["用户类型"]},
    {"object": "user_company_membership", "kind": "metric", "name": "企业成员用户数",
     "aliases": []},
    {"object": "company", "kind": "dimension", "name": "企业类型", "aliases": []},
]


def _by_key(hits):
    return {h["object"]: h for h in hits}


def test_object_name_alias_hit_beats_description_hit():
    """别名精确命中（isv）排在描述命中（membership）之前。"""
    hits = build_object_hits(["第三方集成", "企业成员关系"], OBJECTS, DICT_ROWS)
    assert hits[0]["object"] == "isv"
    assert hits[0]["display_name"] == "ISV"
    assert {h["object"] for h in hits} >= {"isv", "user_company_membership"}


def test_cross_object_follows_usable_links_only():
    """跨对象推荐只沿可编译 join：isv 不该推荐 user 上的字段（那是跨跳占位符）。"""
    hits = _by_key(build_object_hits(["第三方集成"], OBJECTS, DICT_ROWS))
    refs = hits["isv"].get("cross_object") or []
    assert "user_company_membership.企业成员用户数" in refs   # 2 跳可达
    assert all(not r.startswith("user.") for r in refs)
    assert all(not r.startswith("company.") for r in refs)    # company 是空 join，不可达


def test_no_usable_link_means_no_cross_object():
    """连一条可编译边都没有时不得凭空给跨对象写法。"""
    hits = _by_key(build_object_hits(["用户企业关系"], OBJECTS, DICT_ROWS))
    assert "cross_object" not in hits["user_company_membership"]


def test_own_field_name_not_recommended_elsewhere():
    """主对象自己已有「用户类型」时，不再把别处同名字段当跨对象推荐。"""
    extra = [{"object": "user", "kind": "dimension", "name": "用户类型",
              "name_en": "user_type", "aliases": []}]
    hits = _by_key(build_object_hits(["用户企业关系"], OBJECTS, DICT_ROWS + extra))
    refs = hits["user_company_membership"].get("cross_object") or []
    assert all(r != "user.用户类型" for r in refs)


def test_keyword_matched_field_is_preferred_over_backup():
    """关键词命中的字段（企业用户类型）先于凑数的指标出现。"""
    hits = _by_key(build_object_hits(["第三方集成", "用户类型"], OBJECTS, DICT_ROWS))
    refs = hits["isv"].get("cross_object") or []
    assert refs.index("user_company_membership.企业用户类型") == 0


def test_backup_slots_rotate_across_objects():
    """余量按目标对象轮转，单个对象不得刷满名额。"""
    rows = list(DICT_ROWS)
    for i in range(6):
        rows.append({"object": "company", "kind": "metric", "name": f"企业指标{i}",
                     "aliases": []})
    hits = _by_key(build_object_hits(["第三方集成"], OBJECTS, rows, max_cross=4))
    refs = hits["isv"].get("cross_object") or []
    assert len(refs) <= 4
    # 轮转后仍要覆盖 2 跳的 membership，而不是被 company 的指标全占
    assert any(r.startswith("user_company_membership.") for r in refs)


def test_empty_keywords_return_nothing():
    assert build_object_hits([], OBJECTS, DICT_ROWS) == []
    assert build_object_hits(["", "  "], OBJECTS, DICT_ROWS) == []


def test_limit_caps_hit_count():
    hits = build_object_hits(["第三方集成", "用户企业关系", "企业"], OBJECTS, DICT_ROWS, limit=1)
    assert len(hits) == 1


def test_long_description_keeps_note_section_verbatim():
    """长描述只截业务摘要，『【…】』口径段必须整段保留且不得重复。

    2026-11-26 实测：硬截 160 后模型只看到「本对象经「商户绑定用户」(isv_」，
    于是判定“描述被截断，需要知识库查完整描述”多烧了一轮检索。
    """
    note = "【第三方集成用户口径】object=isv，dimensions=[\"user_company_membership.用户类型\"]"
    long_body = "开放平台第三方服务商。" + "业务背景描述。" * 40
    objs = [dict(o) for o in OBJECTS]
    objs[0]["description"] = long_body + "\n" + note
    desc = build_object_hits(["第三方集成"], objs, DICT_ROWS)[0]["description"]
    assert note in desc                       # 口径段完整可见
    assert desc.count("【第三方集成用户口径】") == 1   # 不重复两遍
    assert "摘要已截断" in desc                 # 截断显式声明
    assert len(desc) < len(objs[0]["description"]) + 80


def test_short_description_is_verbatim():
    """短描述不动：不截、不加标记。"""
    desc = build_object_hits(["第三方集成"], OBJECTS, DICT_ROWS)[0]["description"]
    assert desc == "开放平台第三方服务商"


def test_no_note_section_still_marks_truncation():
    """没有口径段时也要显式标注截断，不静默切半句。"""
    objs = [dict(o) for o in OBJECTS]
    objs[0]["description"] = "长" * 600
    desc = build_object_hits(["第三方集成"], objs, DICT_ROWS)[0]["description"]
    assert "摘要已截断" in desc and len(desc) < 600