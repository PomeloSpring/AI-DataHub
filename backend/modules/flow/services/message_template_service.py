"""Message Template Service — 通知渠道引用的消息模板（模板中心）。

Table: adh_message_templates

为什么单独一张表（不放字模库）：报告模板是**版式契约**（章节 + 图表规格，渲染时取样式），
消息模板是**带变量的文案**（发送时展开成正文），两者语义与生命周期都不同，混在一个分类里
会让"版式不带数据"这类纪律管不住文案模板。

三条硬口径：
1. **变量清单只在这里定义一处**。前端 chips 必须读 `GET /api/message-templates/variables`，
   不得再自己列一份 —— 旧 UI 硬写了 9 个变量（含 `task_name`/`result_summary`），而发送侧
   一个都没消费，等于广告了三年用不了的变量。
2. **业务内容不进通知**（与 `tests/test_scheduled_execution.py::test_notification_contains_no_business_content`
   和安全护栏同源）：任务名、结果摘要、报表正文、数据行都不在可用变量里。要给用户看细节，
   只给 `report_link`（需登录）。
3. **用不了的变量不静默替换**：未登记的 `{{x}}` 原样保留并把变量名回显给调用方
   （`unknown_variables`），预览与发送结果都看得到 —— 否则用户会以为通知里就是这几个字。
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

from backend.common.db import execute_insert, execute_query, execute_write

logger = logging.getLogger(__name__)

# 模板分类（业务名，页面与下拉都显示这个）
TEMPLATE_KINDS = {
    "im": "群机器人 / Webhook",
    "email": "邮件",
    "sms": "短信",
}

# 渠道类型 → 模板分类（沉淀与校验同一份映射，不各自 if）
CHANNEL_KIND = {
    "dingtalk": "im", "feishu": "im", "wecom": "im", "webhook": "im",
    "email": "email",
    "sms": "sms",
}

# 每个分类允许的正文字格式：短信只有纯文本（运营商口径），IM 没有 HTML 容器
KIND_FORMATS = {
    "im": ("text", "markdown"),
    "email": ("text", "markdown", "html"),
    "sms": ("text",),
}

# 可用变量：唯一事实源。`sample` 供配置页预览（明确标注是示例，不是数据）。
# 刻意不含 task_name / result_summary / 报表正文 —— 通知只报状态与登录入口，不带业务内容。
MESSAGE_TEMPLATE_VARIABLES = [
    {"name": "date", "label": "执行日期", "sample": "2026-11-11"},
    {"name": "time", "label": "执行时间", "sample": "11:20"},
    {"name": "status", "label": "执行状态", "sample": "success"},
    {"name": "total", "label": "查询项总数", "sample": "3"},
    {"name": "succeeded", "label": "成功项数", "sample": "2"},
    {"name": "failed", "label": "失败项数", "sample": "1"},
    {"name": "report_link", "label": "报告链接（需登录）", "sample": "https://ai-datahub.example.com/reports/1024"},
    # 决策建议定时预警用：仍是**执行元信息**（命中了几条），不是业务内容。
    # 指标名/阈值/实测值/建议正文一律不得进变量面（它们能拼出一整套判断口径，
    # 而外部 IM 渠道不受平台权限与脱敏链路管辖）；建议详情走 report_link
    # 指向的决策中心实例页，那一页由 decision:read 把关。
    {"name": "decision_hit_count", "label": "命中的决策建议条数", "sample": "2"},
]

# 国内短信按 70 字计一条：超了不是错误，但必须让用户知道会被拆成多条计费
SMS_SEGMENT_CHARS = 70

_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_ALLOWED_VARS = {v["name"] for v in MESSAGE_TEMPLATE_VARIABLES}


def variable_names() -> list:
    return [v["name"] for v in MESSAGE_TEMPLATE_VARIABLES]


def used_variables(text: str) -> list:
    """模板正文里出现过的**登记过的**变量名（按首次出现顺序）。"""
    seen, out = set(), []
    for m in _VAR_RE.finditer(str(text or "")):
        name = m.group(1)
        if name in _ALLOWED_VARS and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def unregistered_variables(text: str) -> list:
    """正文里出现但**发不出去**的变量（发送时原样留字面量）。

    必须可见：从通知渠道沉淀来的历史文本带着旧 UI 广告过的 `task_name`/`result_summary`，
    列表里不标出来，用户只会收到一条写着 `{{task_name}}` 的通知而不知道为什么。
    """
    seen, out = set(), []
    for m in _VAR_RE.finditer(str(text or "")):
        name = m.group(1)
        if name not in _ALLOWED_VARS and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def render(text: str, context: dict) -> tuple:
    """展开模板，回 ``(正文, 未登记变量)``。

    未登记的变量**原样保留**并回显名字：替换成空串会把"这个变量根本用不了"掩成
    "通知就这么短"，是 §1 要防的那类静默失败。
    """
    unknown = []

    def _sub(m):
        name = m.group(1)
        if name not in _ALLOWED_VARS:
            if name not in unknown:
                unknown.append(name)
            return m.group(0)
        value = context.get(name, "")
        return str(value) if value is not None else ""

    out = _VAR_RE.sub(_sub, str(text or ""))
    return out, unknown


def sample_context() -> dict:
    """预览用示例上下文（值来自 MESSAGE_TEMPLATE_VARIABLES 的 sample，一处来源）。"""
    return {v["name"]: v["sample"] for v in MESSAGE_TEMPLATE_VARIABLES}


def build_context(task: dict, results: list, report_link: str = "") -> dict:
    """发送用的上下文：只取执行元信息，不取任务名/正文/数据行（安全护栏 §2）。"""
    rows = results or []
    succeeded = sum(1 for r in rows if isinstance(r, dict) and r.get("status") == "success")
    failed = sum(1 for r in rows if isinstance(r, dict) and r.get("status") != "success")
    # 决策预警的命中条数：只数 verdict，不取任何建议正文或指标名。
    # 非决策类任务（rows 里没有 verdict）自然得到 0，不会多一条业务信号。
    hit_count = sum(1 for r in rows if isinstance(r, dict) and r.get("verdict") == "hit")
    now = datetime.now()
    status = "success" if rows and failed == 0 else ("failed" if succeeded == 0 else "partial")
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "status": status,
        "total": str(len(rows)),
        "succeeded": str(succeeded),
        "failed": str(failed),
        "report_link": report_link or "",
        "decision_hit_count": str(hit_count),
    }


def _row_to_dict(row: dict) -> dict:
    sc = row.get("content")
    return {
        "id": row["id"],
        "code": row.get("code") or "",
        "name": row.get("name") or "",
        "template_kind": row.get("template_kind") or "im",
        "format": row.get("format") or "text",
        "subject": row.get("subject") or "",
        "content": sc or "",
        "description": row.get("description") or "",
        "workspace_id": int(row.get("workspace_id") or 0),
        "is_active": int(row.get("is_active") or 0),
        "is_builtin": bool(row.get("is_builtin")),
        "created_by": int(row.get("created_by") or 0),
        "variables": used_variables(sc),
        # 旧 UI 广告过的变量会沉淀进来：发不出去的那些必须列得出来，别让用户去猜
        "unregistered_variables": (unregistered_variables(sc)
                                   + unregistered_variables(row.get("subject") or "")),
        "updated_at": str(row.get("updated_at") or ""),
    }


class MessageTemplateService:
    """消息模板 CRUD + 引用解析 + 渲染（通知渠道唯一消费方）。"""

    # ── 读 ────────────────────────────────────────────────────────
    def list_templates(self, kind: str = "", include_inactive: bool = False) -> list:
        sql = "SELECT * FROM adh_message_templates"
        conds, params = [], []
        if kind:
            conds.append("template_kind = %s")
            params.append(kind)
        if not include_inactive:
            conds.append("is_active = 1")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY is_builtin DESC, template_kind, updated_at DESC"
        rows = execute_query(sql, tuple(params)) or []
        out = [self._with_usage(_row_to_dict(r)) for r in rows]
        return out

    def get_template(self, key) -> Optional[dict]:
        """按引用键（code）或自增 id 取模板。"""
        want = str(key or "").strip()
        if not want:
            return None
        row = None
        if want.isdigit():
            row = execute_query("SELECT * FROM adh_message_templates WHERE id = %s",
                                (int(want),), fetchone=True)
        if row is None:
            row = execute_query("SELECT * FROM adh_message_templates WHERE code = %s",
                                (want,), fetchone=True)
        return self._with_usage(_row_to_dict(row)) if row else None

    def resolve_reference(self, key) -> dict:
        """渠道引用解析：读不到就带原因回，**不得**静默按默认文案发（§1）。"""
        want = str(key or "").strip()
        if not want:
            return {"found": False, "reason": "渠道未选择消息模板"}
        tpl = self.get_template(want)
        if not tpl:
            return {"found": False, "reason": f"消息模板不存在或已删除（{want}）"}
        if int(tpl["is_active"]) != 1:
            return {"found": False, "reason": f"消息模板「{tpl['name']}」已下架，请在通知渠道改选其它模板"}
        return {"found": True, "template": tpl}

    def usages(self) -> dict:
        """``{模板 code: [{id, name}]}``：谁在引用这份模板（表格列 + 删除门禁同一来源）。"""
        rows = execute_query("SELECT id, name, channel_type, config "
                             "FROM adh_notification_channels") or []
        out: dict = {}
        for row in rows:
            cfg = row.get("config")
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg or "{}")
                except (ValueError, TypeError):
                    cfg = {}
            key = str((cfg or {}).get("message_template_key") or "")
            if key:
                out.setdefault(key, []).append({"id": row["id"], "name": row.get("name") or ""})
        return out

    def _with_usage(self, item: dict) -> dict:
        item["used_by_channels"] = self.usages().get(item["code"], [])
        item["used_count"] = len(item["used_by_channels"])
        return item

    # ── 校验 ──────────────────────────────────────────────────────
    def validate(self, data: dict, *, kind: str, fmt: str, content: str,
                 subject: str = "") -> tuple:
        """返回 ``(清洗后的字段, 告警清单)``；不合格直接 ValueError（不静默改语义）。"""
        if kind not in TEMPLATE_KINDS:
            raise ValueError(f"未知模板分类: {kind}，可选: {', '.join(TEMPLATE_KINDS)}")
        if fmt not in KIND_FORMATS[kind]:
            raise ValueError(f"「{TEMPLATE_KINDS[kind]}」不支持 {fmt} 格式"
                             f"（可用: {', '.join(KIND_FORMATS[kind])}）")
        if not str(content or "").strip():
            raise ValueError("模板正文不能为空")
        warnings = []
        if kind == "email" and not str(subject or "").strip():
            raise ValueError("邮件模板必须填主题（邮件客户端没有主题就是空邮件）")
        if kind != "email" and str(subject or "").strip():
            raise ValueError("主题只对邮件模板有意义，IM/短信模板请留空")
        if kind == "sms":
            # 按渲染后的实际字数估（模板里的 {{status}} 占 10 个字符、发出去只有 7 个），
            # 用示例值算，所以文案里写明"按示例值估算"
            rendered = len(render(content, sample_context())[0])
            if rendered > SMS_SEGMENT_CHARS:
                # 拆分计费是事实而非错误：显式告诉用户，不拦保存也不静默截断
                warnings.append(f"正文按示例值约 {rendered} 字，超过 {SMS_SEGMENT_CHARS} 字会按多条计费，"
                                "建议精简或改用邮件")
            if "<" in content or ">" in content:
                warnings.append("短信是纯文本通道，正文里的尖括号会原样发给收件人")
        unknown = [v for v in re.findall(_VAR_RE, content) if v not in _ALLOWED_VARS]
        if unknown:
            # 保存时就挡住：这些变量发送时不会展开，收件人会看到字面量 {{xxx}}
            raise ValueError(f"模板里有未登记的变量: {', '.join(sorted(set(unknown)))}；"
                             f"可用变量只有 {', '.join(variable_names())}")
        return {"template_kind": kind, "format": fmt, "content": content,
                "subject": str(subject or "").strip()}, warnings

    # ── 写 ────────────────────────────────────────────────────────
    def create_template(self, data: dict, user: dict) -> int:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("请输入模板名称")
        clean, warnings = self.validate(data, kind=str(data.get("template_kind") or "im"),
                                       fmt=str(data.get("format") or "text"),
                                       content=str(data.get("content") or ""),
                                       subject=str(data.get("subject") or ""))
        is_admin = bool((user or {}).get("role") == "admin")
        # 内置模板由迁移维护；非 admin 不得声明 is_builtin（与字模库同一口径）
        is_builtin = 1 if (bool(data.get("is_builtin")) and is_admin) else 0
        code = str(data.get("code") or "").strip() or f"mt_{clean['template_kind']}_{int(datetime.now().timestamp() * 1000)}"
        if execute_query("SELECT id FROM adh_message_templates WHERE code = %s", (code,), fetchone=True):
            code = f"{code}_{int(datetime.now().timestamp() * 1000)}"
        if warnings:
            logger.info("[MessageTemplate] 创建 %s 带告警: %s", code, "; ".join(warnings))
        return execute_insert(
            "INSERT INTO adh_message_templates "
            "(code, name, template_kind, format, subject, content, description, workspace_id, "
            " is_active, is_builtin, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s)",
            (code, name[:128], clean["template_kind"], clean["format"], clean["subject"][:255],
             clean["content"], str(data.get("description") or "")[:512],
             int(data.get("workspace_id") or 0), is_builtin, int((user or {}).get("user_id") or 0)))

    def update_template(self, template_id: int, data: dict, user: dict) -> dict:
        row = execute_query("SELECT * FROM adh_message_templates WHERE id = %s",
                            (template_id,), fetchone=True)
        if not row:
            raise ValueError("消息模板不存在或已删除")
        is_admin = bool((user or {}).get("role") == "admin")
        if bool(row.get("is_builtin")) and not is_admin:
            raise PermissionError("内置模板仅管理员可修改")
        current = _row_to_dict(row)
        kind = str(data.get("template_kind") or current["template_kind"])
        fmt = str(data.get("format") or current["format"])
        content = str(data.get("content") if data.get("content") is not None else current["content"])
        subject = str(data.get("subject") if data.get("subject") is not None else current["subject"])
        clean, warnings = self.validate(data, kind=kind, fmt=fmt, content=content, subject=subject)
        name = str(data.get("name") or current["name"]).strip()
        if not name:
            raise ValueError("请输入模板名称")
        sets = ["name = %s", "template_kind = %s", "format = %s", "subject = %s",
                "content = %s", "description = %s", "updated_at = NOW()"]
        params = [name[:128], clean["template_kind"], clean["format"], clean["subject"][:255],
                  clean["content"], str(data.get("description") or current["description"])[:512]]
        if "is_active" in data:
            sets.append("is_active = %s")
            params.append(1 if data["is_active"] else 0)
        params.append(template_id)
        execute_write(f"UPDATE adh_message_templates SET {', '.join(sets)} WHERE id = %s",
                      tuple(params))
        return {"success": True, "warnings": warnings}

    def delete_template(self, template_id: int, user: dict) -> dict:
        """删除前查引用：被渠道引用的模板不拦就会让通知静默回默认文案 —— 直接拒并给渠道清单。"""
        row = execute_query("SELECT * FROM adh_message_templates WHERE id = %s",
                            (template_id,), fetchone=True)
        if not row:
            raise ValueError("消息模板不存在或已删除")
        is_admin = bool((user or {}).get("role") == "admin")
        if bool(row.get("is_builtin")) and not is_admin:
            raise PermissionError("内置模板仅管理员可删除")
        hits = self.usages().get(row.get("code") or "", [])
        if hits:
            names = "、".join(f"「{h['name']}」" for h in hits[:5])
            raise ValueError(f"该模板正被 {len(hits)} 个通知渠道引用（{names}"
                             f"{'…' if len(hits) > 5 else ''}）：请先在通知渠道改选其它模板，"
                             "或直接下架（不影响引用可追溯）")
        execute_write("DELETE FROM adh_message_templates WHERE id = %s", (template_id,))
        return {"success": True}

    # ── 预览（配置页"看效果"与发送同一渲染函数，不另写一套替换）────────────
    def preview(self, data: dict) -> dict:
        content = str(data.get("content") or "")
        subject = str(data.get("subject") or "")
        body, unknown_body = render(content, sample_context())
        subj, unknown_subj = render(subject, sample_context())
        return {
            "subject": subj,
            "content": body,
            "unknown_variables": sorted(set(unknown_body + unknown_subj)),
            "variables": used_variables(content) + used_variables(subject),
            "char_count": len(body),
            "note": "预览取登记变量的示例值，只为看文案形状；模板不保存任何数值，发送时按当轮执行结果展开。",
        }


message_template_service = MessageTemplateService()
