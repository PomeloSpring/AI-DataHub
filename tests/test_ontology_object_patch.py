"""本体对象**局部合并**与破坏性覆盖护栏的回归锁。

用户原话：「要让 chat 真正做到增强系统能力，让他能正确的修改模型」。

历史缺陷（2026-11-25 实测）：`update_ontology_object` 是**整对象替换**，而读接口
`get_ontology_model` 出于脱敏只回摘要（属性无物理列、关系完全不回）。chat 拿摘要重建
User/Company 写进草案 → 51/50 个属性的 `column` 全部丢失、13/6 条关系清零；
草案「保存成功」，直到发布才被 `_reject_destructive_overwrite` 打回。

本文件锁住四条不变量：
1. `patch_object` 只改提交的字段，未提交的属性/关系/物理列/主表一律保留；
2. 关系按 `type→target` 合并追加，删除必须显式 `remove_links` 并写入
   `_declared_link_removals`（否则发布按意外丢失阻断）；
3. `save_draft_only(strict=True)` 遇定义丢失**直接拒写**（不再“存好了却发不出去”），
   显式 `column: ""` 只作提醒不阻断；
4. 工具层：`update_ontology_object` 走 patch 且新建对象要 `ontology:create`；
   `get_ontology_model` 下发关系摘要但绝不下发 join/物理列；
   `add_ontology_objects` 拒无主表对象、按列元数据自动补列。

纯函数 + fake DB，不触碰真实数据库。
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services import ontology_service as svc
from backend.modules.mind.execution.sdk_tools import ontology_tools as ot

MID = 1501
DS_ID = 7

EFF_DOC = {"objects": [
    {
        "key": "user", "display_name": "用户", "description": "登录主体",
        "primary_table": "t_user", "aliases": ["账号"],
        "properties": [
            {"name": "用户ID", "column": "user_id", "type": "int", "is_key": True},
            {"name": "创建时间", "column": "created_at", "type": "time"},
        ],
        "links": [
            {"type": "belongs_to", "target": "company",
             "join": "t_user.company_id = t_company.id"},
            {"type": "has", "target": "order"},
        ],
    },
    {
        "key": "company", "display_name": "企业", "primary_table": "t_company",
        "properties": [{"name": "企业ID", "column": "company_id"}],
        "links": [{"type": "has_many", "target": "user"}],
    },
]}

# adh_column_metadata 的假数据（业务描述 → 物理列，供自动补列）
COLUMNS = {
    "t_user": [
        {"column_name": "user_id", "business_desc": "用户ID", "column_comment": ""},
        {"column_name": "created_at", "business_desc": "创建时间", "column_comment": ""},
        {"column_name": "mobile", "business_desc": "手机号", "column_comment": ""},
    ],
    "t_company": [
        {"column_name": "company_id", "business_desc": "企业ID", "column_comment": ""},
    ],
    "t_ticket": [
        {"column_name": "ticket_id", "business_desc": "工单号", "column_comment": ""},
        {"column_name": "title", "business_desc": "标题", "column_comment": ""},
    ],
}


class _Cur:
    def __init__(self, state):
        self.state = state
        self.rows: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.rows = []
        if "FROM adh_column_metadata" in s:
            self.rows = COLUMNS.get(str((params or ("", ""))[1]), [])
        elif "FROM adh_ontology_bindings" in s:
            self.rows = []
        elif s.startswith("UPDATE adh_ontology_models SET draft_json_content"):
            self.state["draft"] = params[0]

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return None


class _Conn:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _Cur(self.state)

    def commit(self):
        pass


def _state(monkeypatch, draft=""):
    """生效版固定为 EFF_DOC；草案列用一个内存 dict 承接（只模拟 draft 四列 + 列元数据查询）。"""
    state = {"draft": draft}
    eff = json.dumps(EFF_DOC, ensure_ascii=False)
    monkeypatch.setattr(svc, "get_metadata_conn", lambda: _Conn(state))
    monkeypatch.setattr(svc, "get_model", lambda mid: {
        "id": mid, "name": "联耀业务本体", "status": "active", "kind": "business",
        "datasource_id": DS_ID, "json_content": eff})
    monkeypatch.setattr(svc, "get_draft", lambda mid: {
        "has_draft": bool(state["draft"]), "draft": state["draft"] or None})
    return state


def _doc(state):
    return json.loads(state["draft"])


def _obj(state, key):
    return next(o for o in _doc(state)["objects"] if o["key"] == key)


def _tool_env(monkeypatch, perms=None):
    """工具层 harness：接住权限码（记录），身份取服务端上下文。"""
    allowed = set(perms if perms is not None else {"ontology:save", "ontology:create"})
    seen: list = []

    def _require(code, label):
        seen.append(code)
        if code not in allowed:
            raise PermissionError(f"缺少权限码 {code}")

    monkeypatch.setattr(ot, "_assert_model_editable", lambda mid: None)
    monkeypatch.setattr(ot, "_reject_datasource_arg", lambda args: None)
    monkeypatch.setattr(ot, "_require_write_perm", _require)
    monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.context"
                        ".get_execution_context", lambda: SimpleNamespace(username="admin"))
    return seen


def _payload(result):
    return json.loads(result["content"][0]["text"])


class TestPatchObjectMerge:
    """只改提交的字段：属性/关系/物理列/主表由服务端保留（历史事故的正面锁）。"""

    def test_scalar_only_keeps_properties_links_and_columns(self, monkeypatch):
        state = _state(monkeypatch)
        out = svc.patch_object(MID, "user", {"key": "user", "description": "登录主体（改）"})
        user = _obj(state, "user")
        assert user["description"] == "登录主体（改）"
        assert [p["column"] for p in user["properties"]] == ["user_id", "created_at"]
        assert len(user["links"]) == 2          # 关系一条不丢
        assert user["primary_table"] == "t_user"
        assert out["mode"] == "updated"
        assert out["kept_properties"] == 2 and out["kept_links"] == 2
        assert out["properties_added"] == [] and out["links_removed"] == []

    def test_property_merge_keeps_untouched_fields(self, monkeypatch):
        state = _state(monkeypatch)
        svc.patch_object(MID, "user", {
            "key": "user",
            "properties": [{"name": "创建时间", "description": "注册时间"}]})
        user = _obj(state, "user")
        created = next(p for p in user["properties"] if p["name"] == "创建时间")
        assert created["column"] == "created_at" and created["type"] == "time"
        assert created["description"] == "注册时间"
        assert len(user["properties"]) == 2      # 未提交的属性没被删

    def test_new_property_resolved_from_column_metadata(self, monkeypatch):
        state = _state(monkeypatch)
        out = svc.patch_object(MID, "user", {
            "key": "user", "properties": [{"name": "手机号", "type": "string"}]})
        user = _obj(state, "user")
        mobile = next(p for p in user["properties"] if p["name"] == "手机号")
        assert mobile["column"] == "mobile"       # 服务端按业务描述解析，不猜
        assert out["properties_column_resolved"] == ["手机号"]
        assert "mobile" not in json.dumps(out, ensure_ascii=False)  # 物理列不进回执

    def test_new_property_without_resolvable_column_is_rejected(self, monkeypatch):
        _state(monkeypatch)
        with pytest.raises(ValueError) as e:
            svc.patch_object(MID, "user", {
                "key": "user", "properties": [{"name": "不存在的业务属性"}]})
        assert "找不到同名业务属性" in str(e.value)
        assert "可用业务属性示例" in str(e.value)   # 回候选（业务名），不让人猜

    def test_links_append_and_duplicate_identity_updates_in_place(self, monkeypatch):
        state = _state(monkeypatch)
        svc.patch_object(MID, "user", {
            "key": "user",
            "links": [{"type": "has", "target": "ticket"},
                      {"type": "belongs_to", "target": "company", "cardinality": "N:1"}]})
        links = _obj(state, "user")["links"]
        assert len(links) == 3                    # 默认追加，不替换
        belong = next(l for l in links if l["target"] == "company")
        assert belong["cardinality"] == "N:1" and belong["join"]    # 只改提交字段，join 保留

    def test_remove_links_requires_explicit_declaration(self, monkeypatch):
        state = _state(monkeypatch)
        out = svc.patch_object(MID, "user", {"key": "user"}, remove_links=["company"])
        user = _obj(state, "user")
        assert [l["target"] for l in user["links"]] == ["order"]
        assert out["links_removed"] == ["belongs_to→company"]
        # 声明必须活到发布：否则 publish 时会被当成意外丢失而阻断
        assert _doc(state)["_declared_link_removals"]["user"] == ["belongs_to→company"]
        blocking, advisory = svc._overwrite_risk(
            {"json_content": json.dumps(EFF_DOC, ensure_ascii=False)}, state["draft"])
        assert blocking == [] and any("按声明删除关系" in a for a in advisory)

    def test_create_object_needs_physical_shape(self, monkeypatch):
        _state(monkeypatch)
        with pytest.raises(ValueError) as e:
            svc.patch_object(MID, "ticket", {"key": "ticket", "display_name": "工单"})
        assert "未声明物理形状" in str(e.value)


class TestLinkJoinDerivation:
    """新关系必须落成**可执行的单跳 join**（2026-11-26 口径）。

    历史缺陷：chat 新建的关系只带业务名（读接口剥 join），落库的 links 没有 join →
    `planner._link_chain` 当“语义关系”跳过，跨对象查询永远走不通（用户问「按集成方区分」
    只能退化成裸 SQL）。服务端按两表同名业务列确定性推导，多义时回 join_on 指名，不猜。
    """

    def test_unique_shared_business_column_is_derived(self, monkeypatch):
        _state(monkeypatch)
        monkeypatch.setitem(COLUMNS, "t_order", [
            {"column_name": "user_id", "business_desc": "用户ID", "column_comment": ""},
            {"column_name": "amount", "business_desc": "金额", "column_comment": ""},
        ])
        doc = {"objects": [
            {"key": "user", "primary_table": "t_user"},
            {"key": "order", "primary_table": "t_order"},
        ]}
        join, why = svc.derive_link_join(doc, {"id": MID, "datasource_id": DS_ID},
                                         doc["objects"][0], {"target": "order"})
        assert join == "user.user_id = order.user_id"
        assert why == ""

    def test_ambiguous_shared_columns_refuse_to_guess(self, monkeypatch):
        _state(monkeypatch)
        monkeypatch.setitem(COLUMNS, "t_a", [{"column_name": "x"}, {"column_name": "y"}])
        monkeypatch.setitem(COLUMNS, "t_b", [{"column_name": "x"}, {"column_name": "y"}])
        doc = {"objects": [{"key": "alpha", "primary_table": "t_a"},
                           {"key": "beta", "primary_table": "t_b"}]}
        join, why = svc.derive_link_join(doc, {"id": MID, "datasource_id": DS_ID},
                                         doc["objects"][0], {"target": "beta"})
        assert join == "" and "join_on" in why        # 宁缺勿错：回指名方式，不猜

    def test_join_on_business_property_is_resolved_server_side(self, monkeypatch):
        _state(monkeypatch)
        monkeypatch.setitem(COLUMNS, "t_a", [{"column_name": "x"}, {"column_name": "y"}])
        monkeypatch.setitem(COLUMNS, "t_b", [{"column_name": "x"}, {"column_name": "y"}])
        doc = {"objects": [
            {"key": "alpha", "primary_table": "t_a",
             "properties": [{"name": "号码", "column": "x"}]},
            {"key": "beta", "primary_table": "t_b"}]}
        join, why = svc.derive_link_join(doc, {"id": MID, "datasource_id": DS_ID},
                                         doc["objects"][0],
                                         {"target": "beta", "join_on": "号码"})
        assert join == "alpha.x = beta.x" and why == ""

    def test_patch_new_link_is_written_with_derived_join(self, monkeypatch):
        state = _state(monkeypatch)
        monkeypatch.setitem(COLUMNS, "t_order", [{"column_name": "user_id"}])
        svc.patch_object(MID, "order", {
            "key": "order", "display_name": "订单", "primary_table": "t_order",
            "properties": [{"name": "用户ID", "column": "user_id"}]})
        out = svc.patch_object(MID, "user", {
            "key": "user", "links": [{"type": "places", "target": "order"}]})
        link = next(l for l in _obj(state, "user")["links"]
                    if l["target"] == "order" and l["type"] == "places")
        assert link["join"] == "user.user_id = order.user_id"
        assert out["links_join_derived"] == ["places→order"]
        assert not any("未生成单跳 join" in w for w in out["warnings"])

    def test_patch_warns_when_join_cannot_be_derived(self, monkeypatch):
        state = _state(monkeypatch)
        out = svc.patch_object(MID, "user", {
            "key": "user", "links": [{"type": "refs", "target": "company"}]})
        link = next(l for l in _obj(state, "user")["links"]
                    if l["target"] == "company" and l["type"] == "refs")
        assert not str(link.get("join") or "")    # 不写半截 join
        assert any("未生成单跳 join" in w for w in out["warnings"])

    def test_links_without_sql_join_are_reported(self):
        doc = {"objects": [{
            "key": "user", "display_name": "用户",
            "links": [
                {"type": "has", "target": "order"},
                {"type": "via", "target": "company",
                 "join": "User.x = Mid.x → Mid.y = Company.y"},
                {"type": "ok", "target": "case", "join": "User.id = Case.user_id"},
            ]}]}
        flagged = svc.find_links_without_sql_join(doc)
        assert flagged == ["用户→order", "用户→company"]


class TestStrictDraftWrite:
    """全量覆盖通道：会造成定义丢失时在写入时就拒（不再“存好了却发不出来”）。"""

    def test_column_drop_is_rejected_before_write(self, monkeypatch):
        state = _state(monkeypatch)
        bad = json.loads(json.dumps(EFF_DOC))
        # 模拟“拿脱敏摘要重建对象”：属性名齐全，但 column 键全没了
        for o in bad["objects"]:
            for p in o.get("properties") or []:
                p.pop("column", None)
        with pytest.raises(ValueError) as e:
            svc.save_draft_only(MID, json.dumps(bad, ensure_ascii=False), strict=True)
        assert "已拒绝写入草案" in str(e.value)
        assert "物理列定义丢失" in str(e.value)
        assert state["draft"] == ""            # 没有落半截草案

    def test_link_shrink_without_declaration_is_rejected(self, monkeypatch):
        state = _state(monkeypatch)
        bad = json.loads(json.dumps(EFF_DOC))
        next(o for o in bad["objects"] if o["key"] == "user")["links"] = []
        with pytest.raises(ValueError) as e:
            svc.save_draft_only(MID, json.dumps(bad, ensure_ascii=False), strict=True)
        assert "关系 2→0" in str(e.value)
        assert state["draft"] == ""

    def test_explicit_column_unbind_is_advisory_not_blocking(self, monkeypatch):
        state = _state(monkeypatch)
        doc = json.loads(json.dumps(EFF_DOC))
        next(p for p in doc["objects"][0]["properties"]
             if p["name"] == "创建时间")["column"] = ""   # 显式解绑
        out = svc.save_draft_only(MID, json.dumps(doc, ensure_ascii=False))  # 非 strict
        assert out["destructive"] is False
        assert any("显式解绑" in w for w in out["validation_warnings"])

    def test_primary_table_loss_is_rejected_before_write(self, monkeypatch):
        """主表被清空 = 对象失去物理绑定（真实坏草案里 company 就是这样）。

        历史草案 company.primary_table 变空、属性全变「新增」，发布后对象永远取不到数；
        只提醒不够，必须与物理列丢失同口径阻断。
        """
        state = _state(monkeypatch)
        bad = json.loads(json.dumps(EFF_DOC))
        next(o for o in bad["objects"] if o["key"] == "company")["primary_table"] = ""
        with pytest.raises(ValueError) as e:
            svc.save_draft_only(MID, json.dumps(bad, ensure_ascii=False), strict=True)
        assert "主表" in str(e.value) and "物理绑定" in str(e.value)
        assert state["draft"] == ""

    def test_patch_cannot_clear_primary_table(self, monkeypatch):
        """局部合并也不允许把主表改成空（换表给新表名，不是清空）。"""
        _state(monkeypatch)
        with pytest.raises(ValueError) as e:
            svc.patch_object(MID, "user", {"key": "user", "primary_table": ""})
        assert "primary_table 不能改成空" in str(e.value)


class TestToolLayer:
    """工具层：改对象走局部合并；新建对象要 create 码；读接口给关系但不给物理列。"""

    def test_update_existing_object_uses_save_perm_and_merges(self, monkeypatch):
        state = _state(monkeypatch)
        seen = _tool_env(monkeypatch)
        p = _payload(asyncio.run(ot.update_ontology_object(
            {"model_id": MID, "object": {"key": "user", "display_name": "用户（改）"}})))
        assert seen == ["ontology:save"]
        assert p["mode"] == "updated" and p["kept_links"] == 2
        assert _obj(state, "user")["display_name"] == "用户（改）"
        assert len(_obj(state, "user")["links"]) == 2

    def test_create_object_requires_create_perm(self, monkeypatch):
        _state(monkeypatch)
        seen = _tool_env(monkeypatch, perms={"ontology:save"})
        out = asyncio.run(ot.update_ontology_object({
            "model_id": MID,
            "object": {"key": "ticket", "primary_table": "t_ticket",
                       "properties": [{"name": "工单号"}]}}))
        assert out.get("isError") is True
        assert "ontology:create" in _payload(out)["error"]
        assert seen == ["ontology:create"]

    def test_get_model_exposes_links_but_never_joins_or_columns(self, monkeypatch):
        _state(monkeypatch)
        p = _payload(asyncio.run(ot.get_ontology_model(
            {"model_id": MID, "object_key": "user"})))
        user = p["objects_summary"][0]
        assert user["link_count"] == 2 and len(user["links"]) == 2
        assert {l["target"] for l in user["links"]} == {"company", "order"}
        blob = json.dumps(p, ensure_ascii=False)
        assert "t_user.company_id" not in blob and "user_id" not in blob  # 物理信息不外发
        assert "update_ontology_object" in p["edit_note"]

    def test_add_objects_rejects_object_without_physical_shape(self, monkeypatch):
        _state(monkeypatch)
        _tool_env(monkeypatch)
        out = asyncio.run(ot.add_ontology_objects({
            "model_id": MID, "objects": [{"key": "ticket", "display_name": "工单"}]}))
        assert out.get("isError") is True
        assert "未声明物理形状" in _payload(out)["error"]

    def test_add_objects_resolves_columns_from_metadata(self, monkeypatch):
        state = _state(monkeypatch)
        _tool_env(monkeypatch)
        p = _payload(asyncio.run(ot.add_ontology_objects({
            "model_id": MID,
            "objects": [{"key": "ticket", "display_name": "工单", "primary_table": "t_ticket",
                         "properties": [{"name": "工单号"}, {"name": "标题"}]}]})))
        assert p["added"] == ["ticket"]
        assert p["properties_column_resolved"] == [
            {"key": "ticket", "properties": ["工单号", "标题"]}]
        ticket = _obj(state, "ticket")
        assert [x["column"] for x in ticket["properties"]] == ["ticket_id", "title"]