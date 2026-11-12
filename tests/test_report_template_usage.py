"""报告模板「被用起来」的回归：沉淀（报告→模板）/ 套用（模板→骨架）/ 预览（与真实渲染同一管线）。

口径：
* 模板 = 字模库 ``report_template`` 的版式契约（``{format, theme, layout}``），全平台一处抽取、一处生成；
* 模板资产**不保存任何数据**：骨架里的 rows 必须为空，示例数据只允许出现在预览（sample=True）；
* 模板读不到（缺失/下架）一律显式拒，不得静默建一张空白报告或建一个跑不出模板的任务（§1）。
"""
import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.modules.viz.services import report_service as reports

BODY = """# 全国页面访问统计

## 核心结论

近 7 天 PV 1234567，环比上升 12%。

## 分省明细

```chart
{"title": "各省 PV", "chart_type": "bar", "columns": ["省份", "PV"], "rows": [["广东", 8888]]}
```
"""

LAYOUT = {"format": "markdown", "headings": ["核心结论", "分省明细"],
          "charts": [{"title": "各省 PV", "chart_type": "bar", "columns": ["省份", "PV"]}]}
# 从 BODY 抽出来的版式：H1 报告标题也是一级章节（抽取口径只看结构）
BODY_LAYOUT = {"format": "markdown", "headings": ["全国页面访问统计", "核心结论", "分省明细"],
               "charts": [{"title": "各省 PV", "chart_type": "bar", "columns": ["省份", "PV"]}]}


# ── 1. 骨架生成：结构进、数字不出 ──────────────────────────────────────────────

def test_skeleton_keeps_structure_and_drops_every_number():
    out = reports.template_skeleton_from_layout(LAYOUT)
    assert "## 核心结论" in out and "## 分省明细" in out
    assert '"chart_type": "bar"' in out and '"columns": ["省份", "PV"]' in out
    # 套用模板新建报告时 rows 必须为空：模板里冻结的旧值被当成新数据 = 假成功
    assert '"rows": []' in out
    assert "8888" not in out and "1234567" not in out


def test_skeleton_sample_rows_are_marked_and_only_for_preview():
    out = reports.template_skeleton_from_layout(LAYOUT, sample=True)
    assert "示例甲" in out and '"rows": []' not in out
    # 预览说明必须自带：否则用户分不清预览里的数字是样例还是模板存的数据
    assert "模板本身不保存任何数值" in out


def test_html_skeleton_is_a_standalone_document():
    layout = {"format": "html", "headings": ["概览", "明细"], "charts": []}
    out = reports.template_skeleton_from_layout(layout)
    assert out.lstrip().lower().startswith("<!doctype html") and "</html>" in out
    assert "<h2>概览</h2>" in out


def test_analyze_report_layout_shares_the_extractor():
    """配置页「从正文抽版式」与报告重跑/任务契约用的是同一个 extract_report_layout。"""
    out = reports.analyze_report_layout(BODY, "markdown")
    assert out["has_layout"] is True
    assert out["layout"]["headings"] == ["全国页面访问统计", "核心结论", "分省明细"]
    assert out["layout"]["charts"][0]["chart_type"] == "bar"
    assert "## 核心结论" in out["skeleton"]

    empty = reports.analyze_report_layout("就是一段没有结构的文字", "markdown")
    assert empty["has_layout"] is False


def test_html_skeleton_inlines_tokens_only_on_render_surface():
    """HTML 骨架必须带样式，但**色值只出现在渲染面**：模板资产存样式快照会让改字模库没反应。

    实拍缺陷：配置页「预览」一片黑白 —— 骨架只写了 <h2>/<p>，而 HTML 在隔离 iframe 里
    渲染（主文档 CSS 变量进不去），于是按浏览器 UA 默认样式出图，看着像"模板没带样式"。
    """
    layout = {"format": "html", "headings": ["核心结论", "分省明细"], "charts": []}
    stored = reports.template_skeleton_from_layout(layout)            # 资产（另存为模板/保存正文）
    preview = reports.template_skeleton_from_layout(layout, theme="dark")   # 预览/套用
    assert "<style>" in stored and "var(--bg)" in stored
    assert "--bg:" not in stored, "模板资产里不该有色值快照（改字模库主题必须即时生效）"
    assert ":root" in preview and "--bg:" in preview
    # 样式不得改变结构判定：照版式写的骨架仍然零偏差（否则预览达标、生成判不达标）
    assert reports.layout_deviation(preview, layout) == []


def test_preview_and_apply_pass_the_viewer_theme(monkeypatch):
    """预览/套用两个入口都要把主题带到骨架里；模板锁了主题时以模板为准。"""
    html_layout = {"format": "html", "headings": ["核心结论"], "charts": []}
    out = reports.analyze_report_layout("", "html", html_layout, theme="tech")
    assert ":root" in out["skeleton"]

    def resolve_as(locked_theme):
        monkeypatch.setattr(reports, "resolve_report_template", lambda key: {
            "found": True, "name": "t", "format": "html", "theme": locked_theme,
            "layout": html_layout, "body": ""})

    # 模板锁了 finance：令牌必须是 finance，不能被查看者的 tech 顶掉（那是这份资产的语义）
    resolve_as("finance")
    locked = reports.template_skeleton_by_key("1", theme="tech")["skeleton"]
    assert locked == reports.template_skeleton_from_layout(html_layout, theme="finance")
    assert locked != reports.template_skeleton_from_layout(html_layout, theme="tech")

    # 没锁主题：跟随查看者（前端传谁就用谁）
    resolve_as("")
    assert (reports.template_skeleton_by_key("1", theme="tech")["skeleton"]
            == reports.template_skeleton_from_layout(html_layout, theme="tech"))


# ── 2. 沉淀：报告 → 模板 ──────────────────────────────────────────────────────

def _patch_report_row(monkeypatch, row, body: bytes):
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: row)
    monkeypatch.setattr(reports, "read_report_content_bytes", lambda r: body)


def test_save_report_as_template_stores_layout_not_content(monkeypatch):
    """另存为模板只搬结构（章节 + 图表 + 主题），正文里的数字一个都不带。"""
    import backend.modules.viz.services.vis_library_service as vls
    row = {"id": 1, "title": "全国页面访问统计", "format": "markdown", "owner_id": 7,
           "analysis_source": json.dumps({"report_theme": "finance", "origin": {}})}
    _patch_report_row(monkeypatch, row, BODY.encode("utf-8"))
    created = {}

    def capture(data, user_id, is_admin):
        created.update({"data": data, "user_id": user_id, "is_admin": is_admin})
        return 555

    monkeypatch.setattr(vls.vis_library_service, "create_component", capture)
    out = reports.save_report_as_template(1, {"user_id": 7, "role": "analyst"}, name="", description="")

    assert out["component_id"] == 555 and out["theme"] == "finance"
    sc = created["data"]["style_config"]
    assert created["data"]["category"] == "report_template"
    assert sc["layout"] == BODY_LAYOUT and sc["format"] == "markdown"
    assert "8888" not in sc["content"] and '"rows": []' in sc["content"]
    # 用户自建的模板不得声明成内置（否则配置页对它只读，等于沉淀完就不能再改）
    assert created["data"]["is_builtin"] is False and created["is_admin"] is False
    assert out["name"] == "全国页面访问统计 版式模板"


def test_save_report_as_template_requires_reusable_layout(monkeypatch):
    """没有章节也没有图表的报告 → 显式拒，不产出一张挂得上去却不起作用的空模板。"""
    row = {"id": 2, "title": "随手记录", "format": "markdown", "owner_id": 7, "analysis_source": "{}"}
    _patch_report_row(monkeypatch, row, "只有一段话，没有任何结构".encode("utf-8"))
    with pytest.raises(ValueError) as exc:
        reports.save_report_as_template(2, {"user_id": 7, "role": "analyst"})
    assert "版式结构" in str(exc.value)


def test_save_report_as_template_missing_body_is_explained(monkeypatch):
    row = {"id": 3, "title": "没正文的报告", "format": "markdown", "owner_id": 7, "analysis_source": "{}"}
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: row)
    monkeypatch.setattr(reports, "read_report_content_bytes",
                        Mock(side_effect=LookupError("报告还没有正文")))
    with pytest.raises(ValueError) as exc:
        reports.save_report_as_template(3, {"user_id": 7, "role": "analyst"})
    assert "还没有正文" in str(exc.value)


# ── 3. 套用：模板 → 报告骨架 / 定时任务 ────────────────────────────────────────

@pytest.fixture
def insert_capture(monkeypatch):
    """捕获 INSERT，不连真库（正文写内存桶）。"""
    statements = []

    class _Cursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None): statements.append((sql, params))
        def fetchone(self): return None
        def fetchall(self): return []

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cursor()

    monkeypatch.setattr(reports, "DBConnection", lambda: _Conn())
    monkeypatch.setattr(reports, "content_storage", lambda: Mock(
        is_object_storage=True, upload_bytes=lambda *a, **k: True))
    return statements


def test_create_content_report_from_template_scaffolds_and_records_origin(insert_capture, monkeypatch):
    uploaded = {}

    def capture_put(rid, owner, fmt, content):
        uploaded.update({"fmt": fmt, "content": content})
        return "k/1", len(content)

    monkeypatch.setattr(reports, "put_report_content", capture_put)
    monkeypatch.setattr(reports, "resolve_report_template",
                        lambda key: {"found": True, "name": "周报模板", "format": "markdown",
                                     "theme": "tech", "layout": LAYOUT, "body": ""})
    out = reports.create_content_report(title="本周访问统计", content="", format="markdown",
                                        workspace_id=3, owner_id=7,
                                        origin={"channel": "report_center"}, template_key="5")
    assert out["report_id"] and out["format"] == "markdown"
    # 正文 = 模板骨架：有章节与图表规格，没有数字
    assert "## 核心结论" in uploaded["content"] and '"rows": []' in uploaded["content"]
    insert = next(s for s in insert_capture if "INSERT" in s[0])
    origin = json.loads(insert[1][-2])["origin"]
    assert origin["template_key"] == "5" and origin["template_name"] == "周报模板"


def test_create_content_report_keeps_user_content_over_template(insert_capture, monkeypatch):
    """用户已经写了正文 → 以正文为准，模板只负责登记来源，不覆盖人写的内容。"""
    uploaded = {}
    monkeypatch.setattr(reports, "put_report_content",
                        lambda rid, owner, fmt, content: uploaded.update({"content": content})
                        or ("k/1", 1))
    monkeypatch.setattr(reports, "resolve_report_template",
                        lambda key: {"found": True, "name": "T", "format": "markdown",
                                     "theme": "", "layout": LAYOUT, "body": ""})
    reports.create_content_report(title="报告", content="# 我自己写的正文", format="markdown",
                                  workspace_id=3, owner_id=7, template_key="9")
    assert uploaded["content"] == "# 我自己写的正文"


def test_create_content_report_missing_template_is_loud(insert_capture, monkeypatch):
    monkeypatch.setattr(reports, "resolve_report_template",
                        lambda key: {"found": False, "reason": "报告模板不存在或已下架（404）"})
    with pytest.raises(ValueError) as exc:
        reports.create_content_report(title="报告", content="", format="markdown",
                                      workspace_id=3, owner_id=7, template_key="404")
    assert "已下架" in str(exc.value) or "不存在" in str(exc.value)
    assert not [s for s in insert_capture if "INSERT" in s[0]]


def _patch_task_deps(monkeypatch, created):
    row = {"id": 4, "title": "报告", "workspace_id": 3, "owner_id": 7,
           "analysis_source": json.dumps({"origin": {"question": "近7天 PV 走势"}})}
    monkeypatch.setattr(reports, "_load_owned_report", lambda rid, user: row)
    import backend.core.perm_link as perm_link
    monkeypatch.setattr(perm_link, "require_write_perm", lambda *a, **k: True)
    import backend.core.task_runtime as rt
    monkeypatch.setattr(rt, "call", lambda name, *a, **k: True)
    monkeypatch.setattr(rt, "get_callback", lambda name: created)


def test_create_task_from_report_validates_template_before_creating(monkeypatch):
    created = Mock(create_task=Mock(return_value=99))
    _patch_task_deps(monkeypatch, created)
    monkeypatch.setattr(reports, "resolve_report_template",
                        lambda key: {"found": False, "reason": "报告模板不存在或已下架（7）"})
    with pytest.raises(ValueError) as exc:
        reports.create_task_from_report(4, {"user_id": 7, "role": "analyst"},
                                        cron_expression="0 9 * * *", report_template_key="7")
    assert "已下架" in str(exc.value)
    created.create_task.assert_not_called()


def test_create_task_from_report_carries_template_key(monkeypatch):
    created = Mock(create_task=Mock(return_value=99))
    _patch_task_deps(monkeypatch, created)
    monkeypatch.setattr(reports, "resolve_report_template",
                        lambda key: {"found": True, "name": "日报模板", "format": "markdown",
                                     "theme": "tech", "layout": LAYOUT, "body": ""})
    reports.create_task_from_report(4, {"user_id": 7, "role": "analyst"},
                                    cron_expression="0 9 * * *", report_template_key="3")
    data = created.create_task.call_args.kwargs["data"]
    assert data["report_template_key"] == "3"


# ── 4. 问数出报告通道：template_key 有人消费，且"套不上"必须可见 ────────────────

def test_submit_report_resolves_template_or_refuses(monkeypatch):
    """提交时就把模板解析成版式契约随运行带走；模板不可用直接 400，不落一张假报告。"""
    from fastapi import HTTPException
    created = Mock(return_value={"id": 501})
    monkeypatch.setattr(reports, "create_report", created)
    monkeypatch.setattr(reports, "dispatch_report", lambda *a: None)
    monkeypatch.setattr(reports, "authorize_workspace", lambda *a: None)
    calls = []
    monkeypatch.setattr(reports, "resolve_report_template",
                        lambda key: calls.append(key) or
                        {"found": True, "name": "日报模板", "format": "markdown", "theme": "tech",
                         "layout": LAYOUT, "body": ""})
    out = reports.submit_report({"workspace_id": 3, "title": "报告", "dataset_id": 9,
                                 "template_key": "30"}, {"user_id": 7}, Mock())
    assert out["report_id"] == 501 and calls == ["30"]
    source = created.call_args.kwargs["analysis_source"]
    assert source["template_name"] == "日报模板" and source["layout"] == LAYOUT

    monkeypatch.setattr(reports, "resolve_report_template",
                        lambda key: {"found": False, "reason": "报告模板不存在或已下架（31）"})
    with pytest.raises(HTTPException) as exc:
        reports.submit_report({"workspace_id": 3, "dataset_id": 9, "template_key": "31"},
                              {"user_id": 7}, Mock())
    assert exc.value.status_code == 400 and "已下架" in exc.value.detail
    assert created.call_count == 1        # 失败的那次没有落库


def test_run_report_marks_template_layout_as_not_applied_on_snapshot_channel(monkeypatch):
    """取数快照通道没有模型终稿可套版式：必须显式写 layout_result 原因，不冒充"已按模板"。"""
    finished = Mock()
    monkeypatch.setattr(reports, "load_report", lambda rid: {
        "id": rid, "owner_id": 7, "workspace_id": 3, "title": "报告", "format": "markdown",
        "analysis_source": json.dumps({"dataset_id": 9, "template_key": "30",
                                       "template_name": "日报模板", "layout": LAYOUT})})
    monkeypatch.setattr(reports, "claim_report", lambda rid: True)
    monkeypatch.setattr(reports, "resolve_execution_owner", lambda *a: {"user_id": 7})
    monkeypatch.setattr(reports, "execute_report_source",
                        lambda source, identity: {"status": "success", "columns": ["省份"],
                                                  "rows": [{"省份": "广东"}]})
    monkeypatch.setattr("backend.core.task_runtime.guarded_call",
                        lambda fn, *a, **k: fn(*a))
    monkeypatch.setattr(reports, "render_fact_report", lambda title, results: "# 事实表格")
    monkeypatch.setattr(reports, "finish_report", finished)
    reports.run_report(501)
    kwargs = finished.call_args.kwargs
    result = kwargs["analysis_source"]["layout_result"]
    assert result["status"] == "fact_only" and "日报模板" in result["reasons"][0]

    # 没选模板的报告不受影响（不无中生有写 layout_result）
    finished.reset_mock()
    monkeypatch.setattr(reports, "load_report", lambda rid: {
        "id": rid, "owner_id": 7, "workspace_id": 3, "title": "报告", "format": "markdown",
        "analysis_source": json.dumps({"dataset_id": 9})})
    reports.run_report(502)
    assert "layout_result" not in finished.call_args.kwargs.get("analysis_source", {})


def test_resolve_report_template_by_name_never_guesses(monkeypatch):
    """AI 工具侧按业务名取模板：命中不到就回可用清单，模糊命中只当候选、不自动绑定。"""
    import backend.modules.viz.services.vis_library_service as vls
    rows = [{"id": 41, "code": "rpt_41", "name": "周报版式", "is_active": 1},
            {"id": 42, "code": "rpt_42", "name": "周报明细版式", "is_active": 1},
            {"id": 43, "code": "rpt_43", "name": "已停用", "is_active": 0}]
    monkeypatch.setattr(vls.vis_library_service, "list_components", lambda cat: rows)
    monkeypatch.setattr(reports, "resolve_report_template",
                        lambda key: {"found": True, "name": "周报版式", "theme": "", "layout": LAYOUT,
                                     "format": "markdown", "body": ""})
    hit = reports.resolve_report_template_by_name("周报版式")
    assert hit["found"] and hit["template_key"] == "41"

    miss = reports.resolve_report_template_by_name("月报版式")
    assert miss["found"] is False and "周报版式" in miss["available"]
    assert "已停用" not in miss["available"]      # 下架模板不进候选
    fuzzy = reports.resolve_report_template_by_name("周报")
    assert fuzzy["found"] is False and set(fuzzy["available"]) == {"周报版式", "周报明细版式"}

# ── 5. 出厂模板迁移：版式必须是"可校验的静态章节"，SQL 字面量不能被转义写坏 ──────────
# 这一节锁的是本批实测的两个坑，都属于"配了但不起作用、而且没人会怀疑到模板"那类缺陷：
#  ① 旧正文是 Jinja2，抽出来的"章节"是 `✅ {{ r.title }}` —— 契约要求逐字保留，模型永远
#     不可能命中，选了模板必然退化（layout_result=fact_only）；
#  ② 内联 JSON 的 ``\n`` 只写一根斜杠时，MySQL 按换行转义吃掉，落库是带真换行的坏 JSON ——
#     ``resolve_report_template`` 解析不到 layout，版式静默为空。

_MIGRATION = (Path(__file__).resolve().parents[1]
              / "docker/mysql/report_template_builtin_layout_migration.sql")
_BUILTIN_CODES = {"rpt_1", "rpt_2", "rpt_3", "rpt_20260703001"}
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u2705\u274C]")


def _mysql_unescape(lit: str) -> str:
    """按 MySQL 默认 SQL 模式还原字符串字面量（``''``→``'``，``\\x``→ 转义值）。"""
    s = lit.replace("''", "'")
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), s)


def _migration_templates():
    """解析迁移文件，回 ``{code: style_config}``（走的是真 MySQL 会走的同一道转义关）。"""
    text = "\n".join(l for l in _MIGRATION.read_text(encoding="utf-8").splitlines()
                      if not l.strip().startswith("--"))
    out = {}
    for tup in re.findall(r"VALUES \((.*?)\)\s*\n\s*ON DUPLICATE", text, re.S):
        lits = re.findall(r"'((?:[^']|'')*)'", tup)
        code, _name, category, style_config = (lits[0], lits[1], lits[2], lits[3])
        assert category == "report_template", f"迁移写错分类：{category}"
        out[code] = json.loads(_mysql_unescape(style_config))   # 转义坏了这里就炸
    return out


def test_builtin_template_migration_registers_verifiable_static_layout():
    rows = _migration_templates()
    assert set(rows) == _BUILTIN_CODES, "出厂模板清单与迁移不一致（漏一条=配置页仍显示旧模板）"
    for code, sc in rows.items():
        layout = sc.get("layout")
        assert isinstance(layout, dict) and layout.get("headings"), f"{code} 未登记版式"
        assert layout.get("format") == sc.get("format"), f"{code} 正文格式与版式格式不一致"
        assert sc.get("theme") == "", f"{code} 出厂模板不该锁死主题（跟随查看者）"
        # 不编造图表规格：columns 是猜的，猜错就每次判不达标 → 要带图表请从真报告沉淀
        assert layout.get("charts") == [], f"{code} 出厂模板不得编造图表规格"
        for h in layout["headings"]:
            assert not re.search(r"[{}<]|%", h), f"{code} 章节含模板变量/标签，模型不可能逐字命中：{h}"
            assert not _EMOJI_RE.search(h), f"{code} 章节带 emoji，违反 report-delivery 风格基线：{h}"
            assert not re.search(r"[A-Za-z]", h), f"{code} 章节中英混排会因空格差异误判缺章节：{h}"
            assert len(layout["headings"]) <= 5, f"{code} 章节过多，无人值守产出难以全部命中"


def test_builtin_template_migration_content_is_data_free_skeleton():
    rows = _migration_templates()
    for code, sc in rows.items():
        content = sc.get("content") or ""
        assert "{{" not in content and "{%" not in content, f"{code} 正文仍留 Jinja2（已无渲染器，是死重量）"
        assert "示例甲" not in content and '"rows": [' not in content, f"{code} 模板资产带了数据"
        # 正文与版式必须同口径：照骨架写出来的报告，事后校验不得判缺章节
        reextracted = reports.extract_report_layout(content, sc["format"])["headings"]
        missing = [h for h in sc["layout"]["headings"] if h not in reextracted]
        assert not missing, f"{code} 骨架抽不回自己的章节：{missing}"
        assert not reports.layout_deviation(content, sc["layout"]), f"{code} 骨架照版式校验不达标"
