"""模板中心 · 消息模板（IM/邮件/短信）与通知发送链的回归。

背景（这次要修的形态）：渠道里 ``config.message_template`` 是全平台死字段 —— 前端写、后端零消费，
executor 里硬编码一句正文；前端还自己硬写了 9 个变量，其中 ``task_name``/``result_summary``
属业务内容，按安全护栏根本不允许进通知。现在：

* 文案唯一来源 = 渠道引用的消息模板（``config.message_template_key`` → ``adh_message_templates``）；
* 可用变量口径唯一 = ``MESSAGE_TEMPLATE_VARIABLES``，页面 chips 与渲染都读它；
* 渲染只有一处实现：配置页预览与真发送同函数，预览什么样发出去就什么样；
* 任何非正常态（没选模板/模板不可用/用了发不出去的变量）都必须带原因回给调用方（§1）。
"""
import re
from pathlib import Path

import pytest

from backend.modules.flow.services import message_template_service as mts
from backend.modules.flow.services.notification_service import (
    DEFAULT_TITLE, FALLBACK_TEXT, compose_for_channel, notification_service)

_MIGRATION = (Path(__file__).resolve().parents[1]
              / "docker/mysql/message_template_migration.sql")
_BUILTIN_CODES = {"mt_builtin_im_default", "mt_builtin_email_default", "mt_builtin_sms_default"}
_VARIABLE_NAMES = {v["name"] for v in mts.MESSAGE_TEMPLATE_VARIABLES}


def _mysql_unescape(lit: str) -> str:
    """按 MySQL 默认 SQL 模式还原字符串字面量（``''``→``'``，``\\x``→ 转义值）。"""
    s = lit.replace("''", "'")
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), s)


def _migration_templates():
    """解析迁移文件里的种子，回 ``{code: {name, kind, format, subject, content, description}}``。

    走真 MySQL 会走的同一道转义关：迁移里反斜杠被吃掉时，这里读到的就是坏字面量，断言当场炸，
    不会再出现"迁移跑成功了但文案是空的"这种要连库才能发现的问题。
    """
    text = "\n".join(l for l in _MIGRATION.read_text(encoding="utf-8").splitlines()
                     if not l.strip().startswith("--"))
    out = {}
    for tup in re.findall(r"adh_message_templates \(code, name, template_kind, format, subject, content,"
                          r" description,[^)]*\)\s*VALUES \((.*?)\)\s*\n\s*ON DUPLICATE", text, re.S):
        lits = [_mysql_unescape(x) for x in re.findall(r"'((?:[^']|'')*)'", tup)]
        code, name, kind, fmt, subject, content, description = lits[:7]
        out[code] = {"name": name, "template_kind": kind, "format": fmt,
                     "subject": subject, "content": content, "description": description}
    return out


# ── 1. 渲染与上下文：口径只有一处 ─────────────────────────────────────────────

def test_render_keeps_unregistered_variables_literally_and_names_them():
    body, unknown = mts.render("{{date}}|{{status}}|{{task_name}}", mts.sample_context())
    assert "{{task_name}}" in body, "未登记变量被替换掉 = 把'这个变量用不了'掩成'通知就这么短'"
    assert unknown == ["task_name"]
    assert "{{" not in body.replace("{{task_name}}", "")


def test_build_context_carries_execution_metadata_only():
    ctx = mts.build_context({"name": "敏感任务名", "id": 9},
                            [{"status": "success", "title": "患者明细", "rows": [[999]]},
                             {"status": "failed", "title": "处方", "rows": []}],
                            report_link="https://x/reports/1")
    assert set(ctx) == _VARIABLE_NAMES, "上下文变量集合必须与登记口径一致（多一个就多一条泄露面）"
    assert ctx["status"] == "partial" and ctx["total"] == "2" and ctx["failed"] == "1"
    joined = "".join(str(v) for v in ctx.values())
    for banned in ("敏感", "患者", "处方", "999"):
        assert banned not in joined


def test_variable_surface_excludes_business_fields():
    """任务名/结果摘要不在可用变量里：这是护栏，不是漏配（页面据此显示原因文案）。"""
    assert "task_name" not in _VARIABLE_NAMES
    assert "result_summary" not in _VARIABLE_NAMES
    assert mts.SMS_SEGMENT_CHARS == 70


def test_decision_hit_count_is_registered_but_stays_metadata_only():
    """决策预警只多一个**执行元信息**变量（命中几条），业务内容仍不得进变量面。

    指标名/阈值/实测值/建议正文一旦进了通知，外部 IM 渠道就不受平台权限与
    脱敏链路管辖了（那等于多开一条绕黑名单的出口）。建议详情走 report_link
    指向的实例页，那一页由 decision:read 把关。
    """
    assert "decision_hit_count" in _VARIABLE_NAMES, "没登记 = 模板里用了也发不出去（空承诺）"
    ctx = mts.build_context({"name": "精度预警", "id": 3},
                            [{"verdict": "hit"}, {"verdict": "hit"}, {"verdict": "no_hit"}],
                            report_link="https://x/ws/7/decisions")
    assert ctx["decision_hit_count"] == "2"
    for banned in ("advice", "metric", "threshold", "evidence", "severity",
                   "decision_advice", "hit_path"):
        assert banned not in _VARIABLE_NAMES, f"通知变量面多了业务字段「{banned}」"
    # 非决策类任务不会被这个新变量误报业务信号
    plain = mts.build_context({"name": "日报", "id": 4}, [{"status": "success"}], "")
    assert plain["decision_hit_count"] == "0"


# ── 2. validate：越级格式、发不出去的变量、分类专属字段 ────────────────────────

def test_validate_rejects_format_beyond_kind_and_unregistered_variable():
    svc = mts.message_template_service
    with pytest.raises(ValueError, match="不支持"):
        svc.validate({}, kind="im", fmt="html", content="状态 {{status}}")
    with pytest.raises(ValueError, match="未登记"):
        svc.validate({}, kind="im", fmt="text", content="任务：{{task_name}}")
    with pytest.raises(ValueError, match="正文不能为空"):
        svc.validate({}, kind="im", fmt="text", content="  ")


def test_validate_subject_is_email_only():
    svc = mts.message_template_service
    with pytest.raises(ValueError, match="必须填主题"):
        svc.validate({}, kind="email", fmt="markdown", content="正文")
    with pytest.raises(ValueError, match="主题只对邮件"):
        svc.validate({}, kind="im", fmt="text", content="正文", subject="多填的主题")
    # 短信超长/尖括号是事实不是错误：告警说出来，不拦保存也不静默截断
    _, warnings = svc.validate({}, kind="sms", fmt="text", content="分" * 120)
    assert any("70" in w for w in warnings)


# ── 3. compose_for_channel：四种状态都要能诊断 ────────────────────────────────

def test_compose_without_template_reference_sends_default_and_says_so(monkeypatch):
    monkeypatch.setattr(mts.message_template_service, "resolve_reference",
                        lambda key: pytest.fail("没选模板就不该去读模板"))
    out = compose_for_channel({"config": {}}, mts.sample_context())
    assert out["status"] == "default" and out["content"] == FALLBACK_TEXT
    assert out["reasons"] and "未选择" in out["reasons"][0]


def test_compose_with_missing_template_is_degraded_not_silently_default(monkeypatch):
    monkeypatch.setattr(mts.message_template_service, "resolve_reference",
                        lambda key: {"found": False, "reason": "模板已下架"})
    out = compose_for_channel({"config": {"message_template_key": "mt_gone"}}, mts.sample_context())
    assert out["status"] == "degraded" and out["content"] == FALLBACK_TEXT
    assert any("已下架" in r for r in out["reasons"]), "模板没了必须说清，否则像'通知本来就这几个字'"


def test_compose_ignores_legacy_inline_field_with_warning(monkeypatch):
    template = {"name": "群文案", "template_kind": "im", "format": "markdown",
                "subject": "", "content": "共 {{total}} 项，{{result_summary}}"}
    monkeypatch.setattr(mts.message_template_service, "resolve_reference",
                        lambda key: {"found": True, "template": template})
    out = compose_for_channel({"config": {"message_template_key": "mt_a",
                                          "message_template": "手填的旧文案"}}, mts.sample_context())
    assert out["status"] == "rendered"
    assert any("message_template" in w for w in out["warnings"]), "死字段还在渠道里就得点名"
    assert out["unknown_variables"] == ["result_summary"]
    assert "{{result_summary}}" in out["content"], "发不出去的变量原样保留，不静默抹掉"


def test_compose_renders_subject_for_email(monkeypatch):
    monkeypatch.setattr(mts.message_template_service, "resolve_reference", lambda key: {
        "found": True,
        "template": {"name": "邮件文案", "template_kind": "email", "format": "html",
                     "subject": "分析报告已生成（{{date}}）", "content": "<h1>{{status}}</h1>"}})
    out = compose_for_channel({"config": {"message_template_key": "mt_email"}}, mts.sample_context())
    assert out["title"].startswith("分析报告已生成（"), out["title"]
    assert out["template_kind"] == "email" and out["template_format"] == "html"


def test_default_title_is_used_when_template_has_no_subject(monkeypatch):
    monkeypatch.setattr(mts.message_template_service, "resolve_reference", lambda key: {
        "found": True, "template": {"name": "群文案", "template_kind": "im", "format": "text",
                                    "subject": "", "content": "状态 {{status}}"}})
    out = compose_for_channel({"config": {"message_template_key": "mt_im"}}, mts.sample_context())
    assert out["title"] == DEFAULT_TITLE


# ── 4. 短信：占位实现绝不静默成功 ─────────────────────────────────────────────

_SMS_ENV = ("ADH_SMS_PROVIDER", "ADH_SMS_ACCESS_KEY_ID", "ADH_SMS_ACCESS_KEY_SECRET")


def test_sms_without_provider_config_names_what_is_missing(monkeypatch):
    for key in _SMS_ENV:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError) as exc:
        notification_service._send_sms({"sign_name": "签名"}, "正文", "标题")
    message = str(exc.value)
    assert "ADH_SMS_PROVIDER" in message, "缺凭据要点名缺哪几个环境变量"
    assert "template_code" in message and "to_numbers" in message, "渠道缺的业务参数同样点名"
    assert "签名" not in message, "已填好的参数不点名（点名的是缺的）"


def test_sms_with_full_config_still_refuses_to_claim_success(monkeypatch):
    for key in _SMS_ENV:
        monkeypatch.setenv(key, "x")
    cfg = {"sign_name": "签名", "template_code": "SMS_1", "to_numbers": ["13800000000"]}
    with pytest.raises(RuntimeError, match="占位"):
        notification_service._send_sms(cfg, "正文", "标题")


# ── 5. 迁移种子：必须过服务自己的校验，且不含业务内容变量 ──────────────────────

def test_message_template_migration_seeds_pass_service_validation():
    rows = _migration_templates()
    assert set(rows) == _BUILTIN_CODES, "内置模板清单与迁移不一致（少一条=某分类没有默认引用）"
    svc = mts.message_template_service
    for code, row in rows.items():
        # 自洽：迁移里写进去的东西，服务端校验必须认（否则配置页看到的是过不了关的种子）
        svc.validate({}, kind=row["template_kind"], fmt=row["format"],
                     content=row["content"], subject=row["subject"])
        assert row["content"].strip(), f"{code} 正文为空"
        assert row["format"] in mts.KIND_FORMATS[row["template_kind"]], f"{code} 格式越级"


def test_message_template_migration_seeds_use_no_business_variables():
    for code, row in _migration_templates().items():
        blob = row["content"] + row["subject"]
        used = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", blob))
        assert used <= _VARIABLE_NAMES, f"{code} 用了未登记/被禁变量: {sorted(used - _VARIABLE_NAMES)}"
        assert "{{" not in row["description"]
        if row["template_kind"] == "sms":
            rendered, _ = mts.render(row["content"], mts.sample_context())
            assert len(rendered) <= mts.SMS_SEGMENT_CHARS, f"{code} 短信种子超一条就拆分计费"


def test_message_template_migration_wires_permissions_and_menu():
    """权限登记与菜单改名同批对齐：菜单勾「模板中心」要能读写消息模板，报告模板权限点也不再绑退役路径。"""
    text = _MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS adh_message_templates" in text
    assert "message-template:read" in text and "message-template:manage" in text
    assert "/api/message-templates" in text
    # 漂移点：report-template:* 过去绑的是已退役的 /vis-library 路径
    assert "/api/reports/templates" in text
    assert "模板中心" in text, "菜单 label 未改名（页面与侧栏不同名 = 勾了找不到）"


# ── 6. 渠道读写只一份实现：实打时报出来的两个 500/400 当场锁住 ──────────────

_NOTIFICATION_SERVICE = (Path(__file__).resolve().parents[1]
                         / "backend/modules/flow/services/notification_service.py")
_NOTIFICATION_API = (Path(__file__).resolve().parents[1]
                     / "backend/modules/flow/api/notification.py")


def test_there_is_no_second_channel_writer():
    """渠道建/改/删/测试状态只能有一份 SQL。

    旧的那份写的是表里根本不存在的列 ``test_status``，又不填 ``owner_id NOT NULL``、
    而 ``id`` 没有 AUTO_INCREMENT —— 「新建渠道」永远 500、「测试发送」发完在写状态那步 400。
    重复实现不止是维护成本，它是“页面看着能用、其实始终不能用了”的直接原因。
    """
    src = _NOTIFICATION_SERVICE.read_text(encoding="utf-8")
    assert "INSERT INTO adh_notification_channels" not in src
    # 只看真 SQL 行：注释里还要给后人讲清“旧写法错在哪”，不能连词都不让提
    sql_lines = [ln for ln in src.splitlines()
                 if "adh_notification_channels" in ln and ("SELECT" in ln or "UPDATE" in ln or "INSERT" in ln)]
    assert sql_lines and all("test_status" not in ln for ln in sql_lines), \
        f"渠道 SQL 里又引用了不存在的列 test_status: {sql_lines}"
    from backend.modules.flow.services.notification_service import notification_service
    for dead in ("create_channel", "update_channel", "update_channel_status", "list_channels"):
        assert not hasattr(notification_service, dead), f"{dead} 又长回来了：写路径归 scheduled_task_service"
    # 读仍需保留：send/compose/test 都按 id 取渠道
    assert hasattr(notification_service, "get_channel")


def test_notification_api_routes_cover_the_buttons_the_page_uses():
    """通知渠道页用到的写操作都得有路由（以前没有 DELETE，前端删除按钮始终吃 405）。"""
    from backend.modules.flow.api.notification import router

    surface = {(r.path, m) for r in router.routes for m in (r.methods or set())}
    for method in ("GET", "PUT", "DELETE"):
        assert ("/channels/{channel_id}", method) in surface, f"缺 {method} /channels/{{channel_id}}"
    assert ("/channels", "POST") in surface
    assert ("/channels/{channel_id}/test", "POST") in surface

    # 写路径全部委派到唯一实现：API 层不该自己拼 SQL（表名出现在注释里没问题）
    api_src = _NOTIFICATION_API.read_text(encoding="utf-8")
    for fragment in ("INSERT INTO", "UPDATE adh_", "SELECT * FROM", "DELETE FROM adh_"):
        assert fragment not in api_src, f"API 层又自己拼 SQL（{fragment}）：读写只能走 scheduled_task_service"
    for call in ("scheduled_task_service.create_channel", "scheduled_task_service.update_channel",
                 "scheduled_task_service.delete_channel", "scheduled_task_service.update_channel_test_status",
                 "notification_service.test_channel"):
        assert call in api_src, f"渠道写链没收口到 {call}"


def test_channel_test_endpoint_shares_the_render_chain():
    """两个入口（/notification 与 /scheduled-tasks）的测试发送必须走同一个渲染入口。

    以前定时任务侧调 ``notification_sender.test``，可薄壳里根本没这个方法 → AttributeError
    被 except 掉，回一句「测试失败」。同一个渠道两种发法就是“预览与实发不一致”的根。
    """
    from backend.modules.flow.tasks.notification import notification_sender

    assert callable(getattr(notification_sender, "test", None)), "薄壳又少了 test()"
    scheduled_api = (Path(__file__).resolve().parents[1]
                     / "backend/modules/flow/api/scheduled.py").read_text(encoding="utf-8")
    assert "notification_sender.test(channel_id)" in scheduled_api
