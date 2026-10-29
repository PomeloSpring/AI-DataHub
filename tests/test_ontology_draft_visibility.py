"""草案可见性契约（两段式生效的"改了要看得见、看得见要发得了"）。

回归锁（用户报"AS-BOT 说对象加上了，业务本体里搜不到，页面还显示无待发布"）：
1. draft_diff 必须把草案与生效版的对象级差异算出来（新增/删除/修改 + 基线是否失效），
   前端据此驱动发布按钮 —— 不得再凭一个本地布尔判定"无待发布"（死开关）；
2. 草案正文可单独取回预览（发布前可核对），可见性与模型正文同口径；
3. 模型列表/详情带 has_draft，AS-BOT 写草案后建模页在列表层就有感知；
4. add_ontology_objects 必须**基于已有草案**合并（连续新增不互相覆盖），
   与 update_ontology_object 同口径 —— 早先直读生效版，第二次 add 会把第一次的草案对象顶掉。

锁（2026-10-28 用户报“为什么是 JSON 草案，不能可视化为对象清单吗”后新增）：
5. draft_diff 的 added/changed/removed 每项必须带 delta（属性/关系/别名增减、改名、换主表、
   改描述），且 objects[] 给全量状态 —— 前端「变更对比」直接渲人话清单，不再让用户比 JSON；
6. objects[] 按状态优先级排序（added→changed→removed→unchanged）并带 unchanged_count，
   未改动项不占审阅注意力但仍可数；
7. discard_draft（两段式的撤销）无草案时必须报错不得假成功，且只清草案四列、
   不得碰生效内容列（生效版被误清 = 线上口径丢失）；
8. 发布/丢弃端点必须按 4 参调用 require_write_perm（历史缺陷：写成两参 → 缺参 TypeError
   → 接口恒 500，“发布”从未能走通），身份只从服务端解析（护栏 §2）。
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.modules.catalog.services import ontology_service as svc
from backend.modules.mind.execution.sdk_tools import ontology_tools as ot

MID = 901
EFF = {"objects": [
    {"key": "case", "display_name": "案例", "properties": [{"name": "工单号"}], "links": []},
    {"key": "isv", "display_name": "ISV", "properties": [], "links": []},
]}


def _eff_raw():
    return json.dumps(EFF, ensure_ascii=False)


def _draft_raw(extra_key="isv_integration", extra_name="集成记录"):
    doc = {"objects": [json.loads(json.dumps(o)) for o in EFF["objects"]]}
    doc["objects"].append({"key": extra_key, "display_name": extra_name,
                           "properties": [{"name": "来源类型"}], "links": []})
    return json.dumps(doc, ensure_ascii=False)


def _patch(monkeypatch, draft_raw, base_stamp=None, updated=("2026-10-28 16:38:28", "admin"),
            sql_log=None, eff_doc=None):
    """把生效版/草案列模拟成内存行，并接住 discard_draft 的 UPDATE（只模拟草案四列语义）。

    关键点：UPDATE 里出现生效内容列（json_content）即视为越界改动，用例据此断言。
    """
    eff = json.dumps(eff_doc or EFF, ensure_ascii=False)
    state = {"draft": draft_raw, "at": updated[0], "by": updated[1]}

    def _execute(sql, params=None):
        s = " ".join(sql.split())
        if sql_log is not None:
            sql_log.append(s)
        if "draft_json_content=NULL" in s:
            state.update({"draft": "", "at": "", "by": ""})

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            _execute(sql, params)

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return _Cur()

        def commit(self):
            pass

    monkeypatch.setattr(svc, "get_metadata_conn", lambda: _Conn())
    monkeypatch.setattr(svc, "get_model", lambda mid: {
        "id": mid, "name": "联耀业务本体", "status": "active", "json_content": eff})
    monkeypatch.setattr(svc, "_draft_row", lambda mid: {
        "draft_json_content": state["draft"],
        "draft_base_stamp": svc._doc_fingerprint(eff) if base_stamp is None else base_stamp,
        "draft_updated_at": state["at"], "draft_updated_by": state["by"]})
    return state


class TestDraftDiff:
    def test_added_object_is_visible_in_diff(self, monkeypatch):
        _patch(monkeypatch, _draft_raw())
        d = svc.draft_diff(MID)
        assert d["has_draft"] is True
        assert d["effective_object_count"] == 2 and d["draft_object_count"] == 3
        # 差异带业务名（UI 显示 name，不裸显 key），每项附 delta 供可视化审阅
        assert d["added"] == [{"key": "isv_integration", "display_name": "集成记录", "delta": None}]
        assert d["removed"] == [] and d["changed"] == []
        assert d["base_stale"] is False
        assert d["draft_updated_by"] == "admin"
        assert d["draft_parse_error"] is False
        # 全量对象状态清单（草案视图的对象行靠它打标；同状态内按业务名排）
        assert [(o["key"], o["state"]) for o in d["objects"]] == [
            ("isv_integration", "added"), ("isv", "unchanged"), ("case", "unchanged")]
        assert d["unchanged_count"] == 2
        new = next(o for o in d["objects"] if o["state"] == "added")
        assert new["property_count"] == 1 and new["link_count"] == 0

    def test_modified_object_detected_regardless_of_key_order(self, monkeypatch):
        modified = {"objects": [
            # 同一对象，键序不同 + 属性增改 → 应判为 changed（不被键序噪声误判）
            {"display_name": "案例", "key": "case", "properties": [{"name": "工单号"}, {"name": "新列"}], "links": []},
            {"key": "isv", "display_name": "ISV", "properties": [], "links": []},
        ]}
        _patch(monkeypatch, json.dumps(modified, ensure_ascii=False))
        d = svc.draft_diff(MID)
        assert [c["key"] for c in d["changed"]] == ["case"]
        assert d["added"] == [] and d["removed"] == []

    def test_removed_object_detected(self, monkeypatch):
        _patch(monkeypatch, json.dumps({"objects": EFF["objects"][:1]}, ensure_ascii=False))
        d = svc.draft_diff(MID)
        assert [r["key"] for r in d["removed"]] == ["isv"]

    def test_no_draft_is_explicit(self, monkeypatch):
        _patch(monkeypatch, "")
        d = svc.draft_diff(MID)
        assert d["has_draft"] is False and d["base_stale"] is False
        assert d["added"] == [] and d["draft_object_count"] == 0

    def test_stale_base_flagged_so_publish_would_be_blocked(self, monkeypatch):
        _patch(monkeypatch, _draft_raw(), base_stamp="deadbeef")
        assert svc.draft_diff(MID)["base_stale"] is True

    def test_broken_draft_json_does_not_crash(self, monkeypatch):
        _patch(monkeypatch, "{not json")
        d = svc.draft_diff(MID)
        assert d["has_draft"] is True and d["draft_object_count"] == 0
        # 坏正文必须能被识别（否则前端会把“解不出对象”渲成空审阅列表，假称无改动）
        assert d["draft_parse_error"] is True

    def test_objects_not_a_list_is_parse_error_not_empty_diff(self, monkeypatch):
        _patch(monkeypatch, json.dumps({"objects": "case"}, ensure_ascii=False))
        d = svc.draft_diff(MID)
        assert d["has_draft"] is True and d["draft_parse_error"] is True

    def test_changed_object_delta_lists_property_link_alias_details(self, monkeypatch):
        """审阅面板的数据源：属性增减/修改、关系新增、别名新增、改名、换主表都得是结构化项。"""
        draft = {"objects": [
            {"key": "case", "display_name": "工单", "aliases": ["案例单"],
             "primary_table": "dwd_case",
             "properties": [{"name": "工单号", "type": "string"}, {"name": "创建时间"}],
             "links": [{"type": "belongs_to", "target": "isv"}]},
            {"key": "isv", "display_name": "ISV", "properties": [], "links": []},
        ]}
        _patch(monkeypatch, json.dumps(draft, ensure_ascii=False))
        d = svc.draft_diff(MID)
        assert [c["key"] for c in d["changed"]] == ["case"]
        delta = d["changed"][0]["delta"]
        assert [p["name"] for p in delta["properties_added"]] == ["创建时间"]
        assert delta["properties_removed"] == []
        assert [p["key"] for p in delta["properties_modified"]] == ["工单号"]
        assert [l["label"] for l in delta["links_added"]] == ["belongs_to → isv"]
        assert delta["aliases_added"] == ["案例单"] and delta["aliases_removed"] == []
        assert delta["renamed_from"] == "案例"
        assert delta["table_changed"] is True
        assert delta["primary_table_from"] == "" and delta["primary_table_to"] == "dwd_case"
        assert delta["description_changed"] is False
        # 未动的对象不得被误报为改动（无关键序噪声不算 delta）
        assert next(o for o in d["objects"] if o["key"] == "isv")["state"] == "unchanged"

    def test_object_list_order_puts_added_first(self, monkeypatch):
        """审阅列表默认把“加了什么”顶到最前（商业产品提案流的阅读优先级）。

        业务名故意选成与字母序相反：若只按 display_name 排，“斑马新增”会在最后，
        断言能区分“状态优先”与“名字排序”两种实现。
        """
        eff_doc = {"objects": [
            {"key": "case", "display_name": "案例", "properties": [{"name": "工单号"}], "links": []},
            {"key": "isv", "display_name": "ISV", "properties": [], "links": []},
            {"key": "vendor", "display_name": "甲供应商", "properties": [], "links": []},
        ]}
        draft = {"objects": [
            {"key": "aaa_new", "display_name": "斑马新增", "properties": [], "links": []},
            {"key": "case", "display_name": "案例", "primary_table": "dwd_case",
             "properties": [{"name": "工单号"}], "links": []},
            {"key": "isv", "display_name": "ISV", "properties": [], "links": []},
        ]}
        _patch(monkeypatch, json.dumps(draft, ensure_ascii=False), eff_doc=eff_doc)
        d = svc.draft_diff(MID)
        assert [o["state"] for o in d["objects"]] == ["added", "changed", "removed", "unchanged"]
        assert [o["key"] for o in d["objects"]] == ["aaa_new", "case", "vendor", "isv"]
        assert d["unchanged_count"] == 1


class TestDiscardDraft:
    """丢弃草案（两段式的撤销）：没草案要报错，有草案只能清草案列。"""

    def test_discard_clears_draft_columns_and_leaves_effective(self, monkeypatch):
        log: list = []
        _patch(monkeypatch, _draft_raw(), sql_log=log)
        out = svc.discard_draft(MID, discarded_by="admin")
        assert out["discarded"] is True and out["discarded_by"] == "admin"
        assert out["has_draft"] is False and out["added"] == []
        # 生效版对象个数仍在（草案被清空的 diff 回到“无待发布”，而不是“正文也没了”）
        assert out["effective_object_count"] == 2
        upd = [s for s in log if s.startswith("UPDATE")]
        assert len(upd) == 1 and "draft_json_content=NULL" in upd[0]
        # 越界护栏：丢弃不得碰生效内容列，也不得顺手碰派生表（对象表/图谱）
        stmt = upd[0]
        assert "SET json_content" not in stmt and ", json_content" not in stmt
        assert "adh_ontology_objects" not in " ".join(log)

    def test_discard_without_draft_raises_not_fake_success(self, monkeypatch):
        _patch(monkeypatch, "")
        try:
            svc.discard_draft(MID)
            assert False, "无草案时必须报错，不得回 discarded=True 假成功"
        except ValueError as e:
            assert "没有待发布的草案" in str(e)

    def test_discard_missing_model_raises(self, monkeypatch):
        monkeypatch.setattr(svc, "get_model", lambda mid: None)
        try:
            svc.discard_draft(MID)
            assert False, "模型不存在必须 fail-loud"
        except ValueError as e:
            assert "模型不存在" in str(e)


class TestDraftContentPreview:
    def test_draft_body_is_retrievable_for_review(self, monkeypatch):
        raw = _draft_raw()
        _patch(monkeypatch, raw)
        c = svc.get_draft_content(MID)
        assert c["has_draft"] is True
        assert len(json.loads(c["effective_json_content"])["objects"]) == 2
        assert json.loads(c["draft_json_content"])["objects"][-1]["key"] == "isv_integration"

    def test_missing_model_raises_not_silently_empty(self, monkeypatch):
        monkeypatch.setattr(svc, "get_model", lambda mid: None)
        try:
            svc.get_draft_content(MID)
            assert False, "模型不存在必须 fail-loud，不得回空正文"
        except ValueError as e:
            assert "模型不存在" in str(e)


class TestHasDraftOnSummaries:
    def test_list_row_derives_from_flag_column(self):
        row = {"id": 1, "datasource_id": 0, "name": "n", "status": "active",
               "created_at": "x", "updated_at": "y", "has_draft": 1}
        assert svc._row_to_model(row, include_content=False)["has_draft"] is True

    def test_detail_row_derives_from_draft_column(self):
        row = {"id": 1, "datasource_id": 0, "name": "n", "status": "active",
               "created_at": "x", "updated_at": "y", "draft_json_content": '{"objects":[]}'}
        assert svc._row_to_model(row)["has_draft"] is True
        row["draft_json_content"] = None
        assert svc._row_to_model(row)["has_draft"] is False


class TestAddObjectsMergesOntoDraft:
    """连续 add 不互相覆盖：基线必须是已有草案（与 update_ontology_object 同口径）。"""

    def _run(self, monkeypatch, existing_draft, objects):
        saved = {}
        eff = _eff_raw()
        monkeypatch.setattr(svc, "get_model", lambda mid: {
            "id": mid, "name": "n", "status": "active", "json_content": eff})
        monkeypatch.setattr(svc, "get_draft", lambda mid: {"draft": existing_draft})
        monkeypatch.setattr(svc, "save_draft_only",
                            lambda mid, content, created_by="": saved.update(
                                {"mid": mid, "content": content}) or {"id": mid})
        monkeypatch.setattr(ot, "_assert_model_editable", lambda mid: None)
        monkeypatch.setattr(ot, "_require_write_perm", lambda code, action: None)
        monkeypatch.setattr(ot, "_reject_datasource_arg", lambda args: None)
        monkeypatch.setattr("backend.modules.mind.execution.sdk_tools.context"
                            ".get_execution_context", lambda: SimpleNamespace(username="admin"))
        out = asyncio.run(ot.add_ontology_objects({"model_id": MID, "objects": objects}))
        payload = json.loads(out["content"][0]["text"])
        return saved, payload

    def test_second_add_keeps_first_draft_object(self, monkeypatch):
        saved, payload = self._run(
            monkeypatch, _draft_raw(),
            [{"key": "isv_token", "display_name": "密钥记录"}])
        keys = {o["key"] for o in json.loads(saved["content"])["objects"]}
        assert {"isv_integration", "isv_token"} <= keys   # 草案里前一次的对象还在
        assert payload["added"] == ["isv_token"] and payload["skipped"] == []
        assert payload["draft_pending"] is True            # 不生效，要发布

    def test_no_draft_falls_back_to_effective(self, monkeypatch):
        saved, _ = self._run(monkeypatch, "", [{"key": "isv_token", "display_name": "密钥记录"}])
        keys = {o["key"] for o in json.loads(saved["content"])["objects"]}
        assert keys == {"case", "isv", "isv_token"}

    def test_existing_key_is_not_overwritten(self, monkeypatch):
        saved, payload = self._run(monkeypatch, _draft_raw(),
                                   [{"key": "isv_integration", "display_name": "被顶掉的名字"}])
        # 只增不删：同 key 不覆盖，且全部被跳过时根本不写草案（不产生空改动）
        assert payload["skipped"] == ["isv_integration"] and payload["added"] == []
        assert saved == {}


USER = {"user_id": "1791022918038", "username": "admin"}


class TestDraftEndpointGuard:
    """端点层把关：发布与丢弃都得真能走通（不是看上去有按钮、按下去必 500）。

    回归锁：publish 曾写成 `require_write_perm("ontology:publish", "发布本体草案")`，
    而签名是 (user_id, workspace_id, perm_code, label) → 两参当缺参报 TypeError，
    接口恒定 500。本类把“调用 arity + 权限码 + 身份来源”三项锁住。
    """

    def _publish(self, monkeypatch, calls, side_effect=None):
        from backend.core import perm_link
        from backend.modules.catalog.api import ontology as api

        seen: dict = {}

        def _guard(user_id, workspace_id, perm_code, label):
            calls.append((user_id, workspace_id, perm_code, label))
            if side_effect:
                raise side_effect
            return None

        monkeypatch.setattr(perm_link, "require_write_perm", _guard)
        monkeypatch.setattr(api.ontology_service, "publish_draft",
                            lambda mid, published_by="": seen.update(
                                {"mid": mid, "by": published_by}) or {"object_count": 3})
        out = api.publish_model_draft(MID, user=USER)
        return out, seen

    def test_publish_calls_guard_with_four_args_and_server_identity(self, monkeypatch):
        calls: list = []
        out, seen = self._publish(monkeypatch, calls)
        assert out == {"success": True, "object_count": 3}
        # arity 锁：必须是 (user_id, workspace_id, perm_code, label) 四参，不得再顶旧缺陷
        assert calls == [(1791022918038, 0, "ontology:publish", "发布本体草案")]
        # 身份只从服务端解析（护栏 §2）：published_by 取 username，不来自请求体
        assert seen == {"mid": MID, "by": "admin"}

    def test_publish_without_perm_is_403_not_500(self, monkeypatch):
        from fastapi import HTTPException

        calls: list = []
        try:
            self._publish(monkeypatch, calls, side_effect=PermissionError("无 ontology:publish 权限"))
            assert False, "无权限码必须 fail-closed 拒绝"
        except HTTPException as e:
            assert e.status_code == 403
            assert "ontology:publish" in str(e.detail)

    def _discard(self, monkeypatch, calls, side_effect=None):
        from backend.core import perm_link
        from backend.modules.catalog.api import ontology as api

        seen: dict = {}

        def _guard(user_id, workspace_id, perm_code, label):
            calls.append((user_id, workspace_id, perm_code, label))
            if side_effect:
                raise side_effect
            return None

        monkeypatch.setattr(perm_link, "require_write_perm", _guard)
        monkeypatch.setattr(api.ontology_service, "discard_draft",
                            lambda mid, discarded_by="": seen.update(
                                {"mid": mid, "by": discarded_by}) or {"discarded": True})
        return api.discard_model_draft(MID, user=USER), seen

    def test_discard_endpoint_uses_save_perm_and_server_identity(self, monkeypatch):
        calls: list = []
        out, seen = self._discard(monkeypatch, calls)
        assert out == {"discarded": True}
        # 把关口径与存草案同（ontology:save），且同样是四参调用
        assert calls == [(1791022918038, 0, "ontology:save", "丢弃本体草案")]
        assert seen == {"mid": MID, "by": "admin"}

    def test_discard_without_draft_surfaces_reason_as_400(self, monkeypatch):
        from fastapi import HTTPException

        from backend.modules.catalog.api import ontology as api

        calls: list = []
        monkeypatch.setattr("backend.core.perm_link.require_write_perm",
                            lambda *a: calls.append(a))

        def _boom(mid, discarded_by=""):
            raise ValueError("当前没有待发布的草案，无需丢弃")

        monkeypatch.setattr(api.ontology_service, "discard_draft", _boom)
        try:
            api.discard_model_draft(MID, user=USER)
            assert False, "无草案不得假报成功"
        except HTTPException as e:
            assert e.status_code == 400 and "没有待发布的草案" in str(e.detail)
