"""内容中心 → LLM 功能能力（AI 生成内容 / 管理内容）的契约用例。

锁的是四类"配错就会越权或给用户假信号"的判据：

1. **授权来源唯一**：六个内容动作全部挂 `article:manage`，不新建第二套"谁能写内容"判断；
2. **边界不能被配置绕过**：发布 / 下架 / 审核 / 语料写**没有**任何 AI 工具可走
   （`article:publish` / `article:review` / `kbdoc:write` 的 ai_access=none），
   技能正文也不得向模型承诺它没有的能力；
3. **入参与返回都不出现内部 id**：让模型传它看不到的 id 只会换来猜值与被拒（护栏 §7）；
4. **写动作只落草稿**：AI 通道不可能把内容直接推到线上，也不可能在草稿阶段
   把连接串/生成 SQL 写进正文。
"""
import inspect
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.core import perm_link                                            # noqa: E402
from backend.modules.mind.execution import function_tools as ft                # noqa: E402
from backend.modules.mind.execution.sdk_tools import context as ctx_mod        # noqa: E402
from backend.modules.mind.execution.models import ExecutionContext             # noqa: E402
from backend.modules.platform.services import content_service as cs            # noqa: E402

CONTENT_ACTIONS = ("content_article.list", "content_article.get", "content_taxonomy.list",
                   "content_draft.create", "content_draft.update", "content_review.submit")

# 完整一条条目响应（含内部标识），用来断言 AI 侧投影把它们剥干净
ARTICLE = {
    "id": 4101, "slug": "scan-accuracy", "title": "口内扫描精度怎么衡量", "summary": "摘要",
    "category_id": 7, "category_label": "技术白皮书", "category_icon": "", "category_color": "",
    "tags": ["扫描精度"], "kind": "markdown", "kind_label": "站内正文",
    "link_url": "", "link_label": "", "refs": [{"title": "ISO 20896-1", "url": ""}],
    "pinned": 0, "featured": 0, "status": "draft", "is_live": False, "has_pending_change": False,
    "head_revision_id": 88, "live_revision_id": 0, "review_by": "", "published_at": "",
    "archived_at": "", "view_count": 0, "author": "ed", "updated_by": "ed",
    "updated_at": "2026-11-05T10:00:00", "version": 3, "body": "正文一段", "files": [],
    "kb_sync": None,
}


def payload(result):
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def env(monkeypatch):
    """身份 + 权限码 + 内容服务全部替身：只验接线，不碰库与云端。"""
    from backend.common import auth

    token = ctx_mod.ExecutionContextVar.set(ExecutionContext(user_id=7, workspace_id=3))
    monkeypatch.setattr(auth, "resolve_execution_owner",
                        lambda uid, ws: {"user_id": uid, "workspace_id": ws,
                                         "role": "editor", "username": "ed"})
    calls = []
    state = {"deny": ""}

    def _perm(user_id, workspace_id, perm_code, label=""):
        calls.append(("perm", perm_code, label))
        if state["deny"]:
            raise PermissionError(state["deny"])

    monkeypatch.setattr(perm_link, "require_write_perm", _perm)
    monkeypatch.setattr(cs, "body_leak_labels", lambda body: [])
    monkeypatch.setattr(cs, "gate_article", lambda art, body: ([], []))
    monkeypatch.setattr(cs, "list_categories", lambda **kw: [
        {"id": 7, "key": "whitepaper", "label": "技术白皮书", "description": "", "is_active": 1, "count": 3}])
    monkeypatch.setattr(cs, "list_tags", lambda **kw: [
        {"id": 3, "tag": "扫描精度", "sort": 0, "is_active": 1, "count": 2}])
    monkeypatch.setattr(cs, "get_article",
                        lambda **kw: (calls.append(("get", kw)) or dict(ARTICLE)))
    monkeypatch.setattr(cs, "create_article",
                        lambda p, actor: (calls.append(("create", p, actor)) or dict(ARTICLE)))
    monkeypatch.setattr(cs, "update_article",
                        lambda aid, p, actor, expected_version=None: (
                            calls.append(("update", aid, p, actor, expected_version)) or dict(ARTICLE)))
    monkeypatch.setattr(cs, "submit_review",
                        lambda aid, actor, **kw: (calls.append(("submit", aid, actor, kw)) or
                                                  {"status": "in_review", "warnings": [],
                                                   "message": "已提交审核"}))
    monkeypatch.setattr(cs, "list_articles", lambda **kw: (calls.append(("list", kw)) or
                                                           {"items": [dict(ARTICLE)], "total": 1,
                                                            "page": 1, "page_size": 20}))
    try:
        yield calls, state
    finally:
        ctx_mod.ExecutionContextVar.reset(token)


# ── 1. 授权来源唯一 ───────────────────────────────────────────────────────────

def test_all_content_actions_gated_by_article_manage():
    for key in CONTENT_ACTIONS:
        spec = ft.FUNCTION_ACTION_SPECS[key]
        assert spec["perm_code"] == "article:manage", f"{key} 换了授权来源（会另造第二套判断）"
    writes = [k for k in CONTENT_ACTIONS if not ft.FUNCTION_ACTION_SPECS[k]["read_only"]]
    assert set(writes) == {"content_draft.create", "content_draft.update", "content_review.submit"}


def test_content_reads_are_marked_read_only():
    for key in ("content_article.list", "content_article.get", "content_taxonomy.list"):
        assert ft.FUNCTION_ACTION_SPECS[key]["read_only"] is True


# ── 2. 边界：发布/审核/语料写没有任何 AI 工具可走 ─────────────────────────────

def test_no_ai_tool_can_publish_archive_or_review():
    """工具面上不得出现发布/下架/审核/语料写动作（换工具绕过是这类设计最常见的破口）。"""
    names = " ".join(ft.FUNCTION_ACTION_SPECS).lower() + " " + " ".join(
        s["tool_name"] for s in ft.FUNCTION_ACTION_SPECS.values()).lower()
    for banned in ("publish", "archive", "approve", "reject", "withdraw", "waive"):
        assert banned not in names, f"出现了不该开放给 AI 的内容动作关键词: {banned}"


def test_review_and_publish_perms_are_not_ai_callable():
    """注册表里这三个码必须是 none：技能说"不能"的依据就是它，不能只写在注释里。"""
    for code in ("article:review", "article:publish", "kbdoc:write"):
        assert perm_link.cap_level(code, "none") == perm_link.AI_LEVEL_NONE
        assert perm_link.normalize_level("none") == "none"


def test_role_without_article_manage_denies_every_content_action():
    """fail-closed：角色没这个码，六个动作全拒且原因点名权限码（不是静默少注册）。"""
    allowed, denied = perm_link.resolve_function_actions(
        {k: ft.FUNCTION_ACTION_SPECS[k] for k in CONTENT_ACTIONS},
        {"article:review": {"ai_access": "none", "label": "审核内容"}})
    assert allowed == {}
    assert len(denied) == len(CONTENT_ACTIONS)
    assert all("article:manage" in reason for reason in denied.values())


def test_read_level_blocks_write_content_actions():
    """把 article:manage 配成 read：读放行、写全拒，且原因说清"是写操作"。"""
    allowed, denied = perm_link.resolve_function_actions(
        {k: ft.FUNCTION_ACTION_SPECS[k] for k in CONTENT_ACTIONS},
        {"article:manage": {"ai_access": "read", "label": "编辑内容条目", "ai_note": ""}})
    assert set(allowed) == {"content_article.list", "content_article.get", "content_taxonomy.list"}
    assert {k for k, v in denied.items() if "写操作" in v} == {
        "content_draft.create", "content_draft.update", "content_review.submit"}


# ── 3. 内部标识不进 LLM 视野 ──────────────────────────────────────────────────

def test_content_schemas_never_ask_for_internal_ids():
    forbidden = {"id", "article_id", "category_id", "kb_id", "datasource_id",
                 "workspace_id", "user_id", "revision_id"}
    for key in CONTENT_ACTIONS:
        props = set(ft.FUNCTION_ACTION_SPECS[key]["schema"]["properties"])
        assert not (props & forbidden), f"{key} 的入参要求模型传内部 id: {sorted(props & forbidden)}"


def test_publish_state_fields_are_rejected_not_ignored(env):
    """传 status=published 必须当场报错：静默忽略会让模型以为"已经发布了"。"""
    calls, _ = env
    out = payload(ft._create_content_draft({"title": "T", "body": "B", "status": "published"}))
    assert out["error"] and "status" in out["error"], out
    assert [c for c in calls if c[0] == "create"] == [], "被拒的写入不得落库"


def test_ai_output_strips_internal_ids(env):
    """返回体只留业务标识：slug / 栏目 key / 版本，id 与 revision id 一律不下发。"""
    calls, _ = env
    out = payload(ft._get_content_article({"slug": "scan-accuracy"}))
    for k in ("id", "category_id", "head_revision_id", "live_revision_id"):
        assert k not in out, f"内部标识 {k} 漏给 LLM"
    assert out["slug"] == "scan-accuracy" and out["category"] == "whitepaper"
    assert out["version"] == 3, "乐观锁要用的 version 必须给（它是版本号，不是内部 id）"
    assert out["gate_hard"] == [] and out["gate_warnings"] == []


# ── 4. 写动作只落草稿，且写前过安全边界 ───────────────────────────────────────

def test_create_draft_writes_draft_only_and_names_the_human_next_step(env):
    calls, _ = env
    out = payload(ft._create_content_draft({"title": "口内扫描精度", "body": "正文",
                                            "category": "whitepaper", "tags": ["扫描精度"],
                                            "refs": [{"title": "ISO 20896-1", "url": "https://x"}]}))
    created = next(c for c in calls if c[0] == "create")
    assert created[2] == "ed", "作者取服务端身份，不信任入参"
    assert "status" not in created[1], "payload 里不得出现发布态字段"
    assert out["article"]["status"] == "draft"
    assert "草稿" in out["note"] and "发布" in out["note"]
    assert next(c for c in calls if c[0] == "perm")[1] == "article:manage"


def test_create_draft_refuses_body_with_internal_identifiers(env, monkeypatch):
    """连接串/生成 SQL 在**写入之前**就拒：草稿阶段允许半成品，但安全边界不能松一口。"""
    calls, _ = env
    # 源码级：写前预检被摘掉的话，内部标识就会先落库、等提交审核才发现（那时已经脏了）
    assert "body_leak_labels" in inspect.getsource(ft._create_content_draft)
    assert "body_leak_labels" in inspect.getsource(ft._update_content_draft)
    monkeypatch.setattr(cs, "body_leak_labels", lambda body: ["数据库连接串"])
    out = payload(ft._create_content_draft({"title": "T", "body": "带连接串"}))
    assert out["error"] and "内部标识" in out["error"], out
    assert [c for c in calls if c[0] == "create"] == [], "被拒的正文不得落库"


def test_update_draft_requires_optimistic_lock(env):
    calls, _ = env
    out = payload(ft._update_content_draft({"slug": "scan-accuracy", "body": "改一版"}))
    assert out["error"] and "expected_version" in out["error"], out
    assert [c for c in calls if c[0] == "update"] == []

    out2 = payload(ft._update_content_draft({"slug": "scan-accuracy", "body": "改一版",
                                             "expected_version": 3}))
    upd = next(c for c in calls if c[0] == "update")
    assert upd[1] == 4101 and upd[4] == 3, "slug 在服务端解析成 id，version 原样带进乐观锁"
    assert "工作稿" in out2["note"], "必须说清改动没上线"


def test_submit_review_states_not_published(env):
    calls, _ = env
    out = payload(ft._submit_content_review({"slug": "scan-accuracy"}))
    assert out["status"] == "in_review"
    assert "尚未发布" in out["note"] and "article:publish" in out["note"]
    submit = next(c for c in calls if c[0] == "submit")
    assert submit[2] == "ed" and submit[3].get("channel") == "ai", "流水要能分辨人还是 AI"


def test_write_actions_fail_closed_without_perm(env):
    calls, state = env
    state["deny"] = "角色未授予「article:manage」权限，无法执行「创建内容草稿」"
    out = payload(ft._create_content_draft({"title": "T", "body": "B"}))
    assert out["error"] and "article:manage" in out["error"]
    assert [c for c in calls if c[0] == "create"] == []


def test_all_content_handlers_fail_closed_without_identity(monkeypatch):
    from backend.common import auth
    token = ctx_mod.ExecutionContextVar.set(None)
    monkeypatch.setattr(auth, "resolve_execution_owner", lambda uid, ws: None)
    try:
        for fn, args in ((ft._list_content_articles, {}), (ft._get_content_article, {"slug": "s"}),
                         (ft._create_content_draft, {"title": "T", "body": "B"}),
                         (ft._update_content_draft, {"slug": "s", "expected_version": 1}),
                         (ft._submit_content_review, {"slug": "s"})):
            assert payload(fn(args)).get("error"), fn.__name__
    finally:
        ctx_mod.ExecutionContextVar.reset(token)


# ── 5. 双侧一致 + 技能不承诺没有的能力 ────────────────────────────────────────

def test_db_ai_action_keys_cover_content_actions():
    """注册表与 DB 的 ai_action_key 必须对上（全局一致性另有用例，这里给内容侧更清楚的失败信息）。"""
    from backend.common.db import execute_query
    try:
        row = execute_query("SELECT ai_action_key FROM adh_perm_registry WHERE perm_code='article:manage'",
                            fetchone=True)
    except Exception:  # noqa: BLE001 — 元数据库不可用时跳过，不伪装通过
        pytest.skip("元数据库不可用")
    keys = {x.strip() for x in str((row or {}).get("ai_action_key") or "").split(",") if x.strip()}
    assert keys >= set(CONTENT_ACTIONS), f"DB 未登记: {sorted(set(CONTENT_ACTIONS) - keys)}"


def test_skill_body_exists_and_only_declares_real_tools():
    """SKILL.md 的 requires_any 必须是真注册的工具名：技能不能向模型承诺它没有的能力。"""
    from backend.modules.mind.config import skill_loader
    from backend.modules.mind.execution import as_bots

    skill = skill_loader.load_skill("content-writer")
    assert skill and skill.get("display_name") == "内容创作"
    meta = as_bots.load_skills(["content-writer"])[0]
    tool_names = {s["tool_name"] for s in ft.FUNCTION_ACTION_SPECS.values()}
    assert set(meta["requires_any"]) <= tool_names, meta["requires_any"]
    assert meta["triggers"], "没有命中词的话多技能绑定时永远不展开正文"


def test_skill_states_the_forbidden_actions_as_forbidden():
    """技能正文必须写清"发布/审核/语料同步由人做"：只靠没注册工具拦，模型会自己编一套说法。"""
    from backend.modules.mind.config import skill_loader
    text = skill_loader.load_skill("content-writer")["system_prompt"]
    for must in ("article:publish", "article:review", "kbdoc:write", "不能", "尚未发布"):
        assert must in text, f"技能正文缺少边界声明: {must}"
