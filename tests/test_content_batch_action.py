"""内容中心「批量操作」的契约回归：批量只批量"循环 + 逐条回报"，不批量语义。

状态机本身的把关（未审核不得发布、已上线不得删、乐观锁冲突）已由
``tests/test_content_workflow.py`` 逐条锁住；本文件锁的是批量这一层的四条纪律：

1. **逐条走单条实现**：不新建第二套判断，也不把整批塞进一个事务（部分成功是真实语义）；
2. **失败逐条点名**：拒因来自单条动作原话 + 业务标题，不得笼统说成"部分失败"，
   更不得把"库里根本没这条"报成成功（no-silent-degradation）；
3. **不代开免审直发**：``waive_review`` 是逐条写明原因的显式例外，批量通道必须够不着它；
4. **权限裁决点不变**：批量端点落在单条已登记的 api_pattern 里（用例见
   ``test_content_workflow.py::test_perm_pattern_matrix_matches_endpoints`` 同批补的 cases）。

DB 与状态机函数全部替身：这里要断言的是"调了谁、传了什么、怎么回报"。
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.common.api_permission import _match_path
from backend.modules.platform.api import content as api
from backend.modules.platform.services import content_service as svc


ROWS = {"a1": 1, "a2": 2, "a3": 3}
TITLES = {1: "种植导板精度复盘", 2: "口扫数据合规红线", 3: "固件发布说明"}


@pytest.fixture
def spy(monkeypatch):
    """替身：寻址按 slug 表查，五个单条动作记录调用并可注入逐条失败。"""
    calls: list[tuple[str, int, dict]] = []
    state = {"fail": {}, "audit": []}

    def _make(name, out):
        def runner(article_id, actor, **kw):
            calls.append((name, int(article_id), {"actor": actor, **kw}))
            err = state["fail"].get(int(article_id))
            if err:
                raise svc.ContentError(err[0], err[1] if len(err) > 1 else 400)
            return dict(out)
        return runner

    monkeypatch.setattr(svc, "rows_by_slugs",
                        lambda slugs: [{"id": ROWS[s], "slug": s, "title": TITLES[ROWS[s]]}
                                       for s in slugs if s in ROWS])
    monkeypatch.setattr(svc, "submit_review", _make("submit", {"status": "in_review", "message": "已提交审核"}))
    monkeypatch.setattr(svc, "publish_article", _make("publish", {"status": "published", "message": "已发布"}))
    monkeypatch.setattr(svc, "archive_article", _make("archive", {"status": "archived", "message": "已下架"}))
    monkeypatch.setattr(svc, "republish_article", _make("republish", {"status": "published", "message": "已上架"}))
    monkeypatch.setattr(svc, "delete_article", _make("delete", {"deleted": True}))
    # 审计只记参数：真库里 log_audit 是旁路，不得影响主链路（护栏 §9）
    monkeypatch.setattr(api, "_audit",
                        lambda user, request, action, article_id, detail:
                        state["audit"].append((action, int(article_id), detail)))
    return SimpleNamespace(calls=calls, state=state)


def _run(endpoint, slugs, *, action=None, reason=None, user=None):
    body = {"slugs": slugs}
    if action is not None:
        body["action"] = action
    if reason is not None:
        body["reason"] = reason
    payload = (api.BatchPublishAction(**body) if action is not None or reason is not None
               else api.BatchSlugs(**body))
    req = SimpleNamespace(client=None)
    return endpoint(payload, req, user or {"user_id": 9, "username": "alice"})


def test_batch_submit_calls_the_single_item_path_per_slug(spy):
    """逐条走 submit_review，身份取服务端解析的用户（不信请求体）；重复 slug 只处理一次。"""
    out = _run(api.batch_submit_articles, ["a2", "a1", "a2"])

    assert [(c[0], c[1]) for c in spy.calls] == [("submit", 2), ("submit", 1)]
    assert all(c[2]["actor"] == "alice" for c in spy.calls)
    assert out["success_count"] == 2 and out["failed_count"] == 0
    # 回执按标题点名（ui-resource-display §1：不得只给裸 slug）
    assert {s["slug"]: s["title"] for s in out["succeeded"]} == {"a1": "种植导板精度复盘",
                                                                "a2": "口扫数据合规红线"}


def test_batch_reports_each_rejection_verbatim_and_keeps_others(spy):
    """一条被拒不得拖垮整批，也不得把拒因压成"部分失败"：原话 + 标题逐条回。"""
    spy.state["fail"][2] = ("内容还没有审核通过。需要跳过审核请显式勾选「免审直发」并写明原因", 400)
    out = _run(api.publish_batch, ["a1", "a2"], action="publish")

    assert out["succeeded"] == [{"slug": "a1", "title": "种植导板精度复盘",
                                 "status": "published", "message": "已发布"}]
    assert out["failed"] == [{"slug": "a2", "title": "口扫数据合规红线",
                              "reason": "内容还没有审核通过。需要跳过审核请显式勾选「免审直发」并写明原因"}]
    # 结论以服务端文案为准，且部分成功必须把"没成的有几篇"说出来
    assert out["message"] == "已发布 1 篇；1 篇未处理（原因逐条列出）"


def test_batch_missing_slug_is_a_failure_not_a_silent_success(spy):
    """勾了但库里没有（已被他人删除）：绝不能当成"目标态已达成"静默成功。"""
    out = _run(api.batch_delete_articles, ["a1", "gone"])

    assert [s["slug"] for s in out["succeeded"]] == ["a1"]
    assert out["failed"][0]["slug"] == "gone"
    assert "不存在或已被删除" in out["failed"][0]["reason"]
    assert out["message"] == "已删除 1 篇；1 篇未处理（原因逐条列出）"
    assert spy.calls == [("delete", 1, {"actor": "alice"})]


def test_batch_with_nothing_succeeded_says_so(spy):
    """整批都没成：结论要说「本次没有改动生效」，不写「已提交审核 0 篇」这种半截话。"""
    spy.state["fail"][1] = ("不能提交审核：正文还有【待填】未清零", 422)
    spy.state["fail"][2] = ("已经在审核中了，请等审核结论", 400)
    out = _run(api.batch_submit_articles, ["a1", "a2"])

    assert out["success_count"] == 0
    assert out["message"] == "2 篇未处理（原因逐条列出），本次没有改动生效"


def test_batch_publish_never_waives_review(spy):
    """免审直发是逐条写明原因的显式例外：批量通道必须够不着它（否则成了成批上线的暗门）。"""
    _run(api.publish_batch, ["a1"], action="publish", reason="顺手都发了")

    _name, _aid, kw = spy.calls[0]
    assert "waive_review" not in kw and not kw.get("waive_review")
    assert "reason" not in kw          # 非免审直发时发布本就不看 reason，不顺手传
    # 请求体里根本没有 waive_review 字段可传（模型层面就堵死）
    assert "waive_review" not in api.BatchPublishAction.model_fields


def test_batch_archive_and_republish_share_one_reason_into_every_item(spy):
    """下架/上架原因对整批共用，逐条写进各自的动作（审核流水按条记）。"""
    _run(api.publish_batch, ["a1", "a2"], action="archive", reason="法规条款已更新")
    assert [c[2].get("reason") for c in spy.calls] == ["法规条款已更新", "法规条款已更新"]

    spy.calls.clear()
    _run(api.publish_batch, ["a3"], action="republish", reason="已复核")
    assert spy.calls[0][2] == {"actor": "alice", "reason": "已复核"}


def test_batch_surfaces_per_item_warning_and_soft_reminders(spy):
    """旁路没做干净的提醒（语料还在库里）与待确认软提醒必须跟着条目回，不被汇总顶掉。"""
    def with_warning(article_id, actor, **kw):
        return {"status": "archived", "message": "已下架", "warning": "已同步的语料还在那儿"}

    def with_soft(article_id, actor, **kw):
        return {"status": "in_review", "message": "已提交审核",
                "warnings": [{"code": "refs_missing", "message": "没有引用出处"}]}

    setattr(svc, "archive_article", with_warning)
    setattr(svc, "submit_review", with_soft)
    try:
        out = _run(api.publish_batch, ["a1"], action="archive")
        assert out["succeeded"][0]["warning"] == "已同步的语料还在那儿"
        out2 = _run(api.batch_submit_articles, ["a2"])
        assert out2["succeeded"][0]["warnings"][0]["code"] == "refs_missing"
    finally:
        spy.calls.clear()
    assert [a[0] for a in spy.state["audit"]] == ["content.article.archive", "content.article.submit"]


def test_batch_audits_every_succeeded_item_only(spy):
    """审计逐条落（成功与拒绝都要有痕迹），且只给真成功的记状态转换审计。"""
    spy.state["fail"][2] = ("已上线的内容不能直接删除，请先在「内容管理」里下架（保留下架流水可追溯）", 400)
    _run(api.batch_delete_articles, ["a1", "a2"])

    assert [a[1] for a in spy.state["audit"]] == [1]
    assert spy.state["audit"][0][0] == "content.article.delete"
    assert "批量" in spy.state["audit"][0][2]


def test_batch_rejects_empty_and_oversized_selection(spy):
    with pytest.raises(HTTPException) as exc:
        _run(api.batch_submit_articles, ["", "  "])
    assert exc.value.status_code == 400 and "勾选" in exc.value.detail

    with pytest.raises(HTTPException) as exc2:
        _run(api.batch_submit_articles, [f"s{i}" for i in range(svc.MAX_BATCH_ACTION + 1)])
    assert exc2.value.status_code == 400 and "分批" in exc2.value.detail


def test_batch_publish_channel_only_accepts_visibility_actions(spy):
    """发布通道只收 publish/archive/republish：把 submit 塞进来必须当场拒（权限通道不混用）。"""
    with pytest.raises(HTTPException) as exc:
        _run(api.publish_batch, ["a1"], action="submit")
    assert exc.value.status_code == 400 and "publish / archive / republish" in exc.value.detail


def test_rows_by_slugs_is_one_query_dedup_and_keeps_order(monkeypatch):
    """寻址预检：一次 IN 查询（不逐条往返）、去重保序、只回存在的行。"""
    seen: list[tuple[str, tuple]] = []

    def fake_query(sql, params=None, **kw):
        seen.append((sql, tuple(params or ())))
        rows = [{"id": 1, "slug": "a1", "title": "甲"}, {"id": 2, "slug": "a2", "title": "乙"}]
        return [r for r in rows if r["slug"] in (params or ())]

    monkeypatch.setattr(svc, "execute_query", fake_query)
    out = svc.rows_by_slugs(["a2", "a1", "a2", "", "gone"])

    assert [r["slug"] for r in out] == ["a2", "a1"]          # 按传入顺序，去重
    sql, params = seen[0]
    assert sql.count("%s") == len(params) == 3               # 占位符与参数一一对应
    assert "WHERE slug IN" in sql
    assert out[0]["title"] == "乙"                            # 标题供 UI 点名
