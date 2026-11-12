"""Notification Service — manage channels and send messages.

Supported channels: DingTalk, Feishu, WeCom, Email, Webhook, SMS(占位，未接服务商)

Table: adh_notification_channels

通知正文的唯一来源是**渠道引用的消息模板**（``config.message_template_key`` →
``message_template_service``）：
* 渠道没选模板 → 用平台默认文案（``FALLBACK_TEXT``），结果里标 ``status="default"``；
* 模板读不到（删除/下架）→ 仍发默认文案，但把原因带在 ``reasons`` 里（§1：不能把
  "模板没了" 掩成 "通知本来就这几个字"）；
* 旧字段 ``config.message_template``（内联手填文本）**不再消费**：模板中心改造已把它
  一次性沉淀成模板并回填引用，留着只会让"内联 + 引用"两套真值源并存。检测到就显式告警，
  不静默优先任何一个。
"""

import hashlib
import hmac
import json
import logging
import os
import smtplib
import time
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import httpx

from backend.common.db import get_metadata_conn
from backend.modules.flow.services import message_template_service as mts

logger = logging.getLogger(__name__)

# 无模板/模板不可用时的平台默认文案。业务内容（任务名、结果摘要、数据行）一律不进通知，
# 与 tests/test_scheduled_execution.py::test_notification_contains_no_business_content 同口径。
FALLBACK_TEXT = "定时分析任务已结束，请登录平台查看。"
DEFAULT_TITLE = "AI-DataHub 定时报告"


def compose_for_channel(channel: dict, context: dict) -> dict:
    """按渠道引用渲染待发内容，回 ``{content, title, status, reasons, unknown_variables}``。

    渲染只有一处实现（``message_template_service.render``）：模板中心预览与真发送同函数，
    预览长什么样、发出去就长什么样 —— 两套替换逻辑正是"看着对、发出去不对"的历史形态。
    """
    config = channel.get("config") or {}
    if isinstance(config, str):
        try:
            config = json.loads(config or "{}")
        except (ValueError, TypeError):
            config = {}
    warnings = []
    key = str(config.get("message_template_key") or "").strip()
    if config.get("message_template"):
        warnings.append("渠道里还留着内联 message_template：模板中心改造后该字段不再消费，"
                        "请改选消息模板（这段文字不会发出去）")
    if not key:
        return {"content": FALLBACK_TEXT, "title": DEFAULT_TITLE, "status": "default",
                "reasons": ["渠道未选择消息模板，按平台默认文案发送"],
                "unknown_variables": [], "warnings": warnings}
    hit = mts.message_template_service.resolve_reference(key)
    if not hit.get("found"):
        return {"content": FALLBACK_TEXT, "title": DEFAULT_TITLE, "status": "degraded",
                "reasons": [f"消息模板不可用（{hit.get('reason')}），按平台默认文案发送"],
                "unknown_variables": [], "warnings": warnings}
    tpl = hit["template"]
    body, unknown_body = mts.render(tpl["content"], context)
    subj, unknown_subj = mts.render(tpl["subject"], context)
    return {"content": body,
            "title": subj.strip() or DEFAULT_TITLE,
            "status": "rendered",
            "reasons": [f"按模板「{tpl['name']}」渲染"],
            "unknown_variables": sorted(set(unknown_body + unknown_subj)),
            "warnings": warnings,
            "template_kind": tpl["template_kind"],
            "template_format": tpl["format"]}


def _get_conn():
    """Get a database connection from the shared pool."""
    return get_metadata_conn()



class NotificationService:
    """Manage notification channels and send messages."""

    # ── Channel 读取 ───────────────────────────────────────────────
    # 写路径只有一份：``scheduled_task_service`` 的 channel CRUD（建/改/删/测试状态）。
    # 这里曾并存第二份实现，写的列名（``test_status``）在表里根本不存在、又不填
    # ``owner_id NOT NULL``，而 ``id`` 没有 AUTO_INCREMENT —— 「新建渠道」因此一直是 500，
    # 「测试发送」发完在写状态那步炸成 400。留读取是因为 send/compose 都按 id 取渠道。

    def get_channel(self, channel_id: int) -> Optional[dict]:
        """Get a single notification channel by ID."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_notification_channels WHERE id = %s", (channel_id,))
                row = cur.fetchone()
                if row:
                    self._deserialize_row(row)
                return row
        finally:
            conn.close()

    # ── Sending ───────────────────────────────────────────────────

    def send(self, channel_id: int, content: str, title: str = "AI-DataHub Notification") -> dict:
        """Send a notification through the specified channel."""
        channel = self.get_channel(channel_id)
        if not channel:
            raise ValueError(f"Notification channel {channel_id} not found")

        channel_type = channel["channel_type"]
        config = channel["config"]

        dispatch = {
            "dingtalk": self._send_dingtalk,
            "feishu": self._send_feishu,
            "wecom": self._send_wecom,
            "email": self._send_email,
            "webhook": self._send_webhook,
            "sms": self._send_sms,
        }

        handler = dispatch.get(channel_type)
        if not handler:
            raise ValueError(f"Unknown channel type: {channel_type}")

        logger.info("[Notification] Sending via %s (channel_id=%s)", channel_type, channel_id)
        return handler(config, content, title)

    def send_rendered(self, channel_id: int, context: dict) -> dict:
        """按渠道引用的模板渲染后发送，回渲染元信息 + 传输结果（发送结果要能诊断）。"""
        channel = self.get_channel(channel_id)
        if not channel:
            raise ValueError(f"Notification channel {channel_id} not found")
        msg = compose_for_channel(channel, context)
        result = self.send(channel_id, msg["content"], msg["title"])
        return {**{k: v for k, v in msg.items() if k != "content"}, "result": result}

    def test_channel(self, channel_id: int) -> dict:
        """测试发送走同一条渲染链（示例上下文）：预览什么就发什么，不是另发一句硬编码。"""
        return self.send_rendered(channel_id, mts.sample_context())

    # ── Channel Implementations ───────────────────────────────────

    def _send_dingtalk(self, config: dict, content: str, title: str) -> dict:
        """DingTalk group robot (webhook + optional sign)."""
        url = config["webhook_url"]
        secret = config.get("secret", "")

        if secret:
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hmac.new(
                secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
            sign = urllib.parse.quote_plus(
                __import__("base64").b64encode(hmac_code).decode()
            )
            url = f"{url}&timestamp={timestamp}&sign={sign}"

        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": content},
        }

        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        if body.get("errcode", 0) != 0:
            raise RuntimeError(f"DingTalk error: {body}")
        return {"status_code": resp.status_code, "body": body}

    def _send_sms(self, config: dict, content: str, title: str) -> dict:
        """短信通道（本期为占位实现）：配置不齐就显式失败，**绝不静默成功**。

        凭据只取 ``backend/.env``（服务商级，全平台一份）：渠道 config 在页面上可读可导出，
        把 AccessKey 写进去就是泄露面（安全护栏 §7）。渠道里只放业务侧参数：
        ``sign_name``（短信签名）、``template_code``（运营商已审核模板码）、``to_numbers``（收件号码）。
        国内短信正文必须走已审核模板，所以平台的消息模板只是**文案镜像**：真实下发参数是
        template_code + 变量值，改了平台文案并不会改运营商侧模板 —— 这层差异必须在文案里说清，
        否则用户会以为在平台改完就能发。
        """
        missing_env = [k for k in ("ADH_SMS_PROVIDER", "ADH_SMS_ACCESS_KEY_ID",
                                   "ADH_SMS_ACCESS_KEY_SECRET") if not os.getenv(k)]
        missing_cfg = [k for k in ("sign_name", "template_code", "to_numbers")
                       if not (config or {}).get(k)]
        if missing_env or missing_cfg:
            parts = []
            if missing_env:
                parts.append(f"backend/.env 缺少服务商凭据 {', '.join(missing_env)}")
            if missing_cfg:
                parts.append(f"渠道配置缺少 {', '.join(missing_cfg)}")
            raise RuntimeError("短信服务商未配置，本次未发送：" + "；".join(parts)
                               + "。国内短信还需运营商审核通过的 template_code；配好后重启服务生效。")
        raise RuntimeError(
            "短信通道配置已齐，但本平台尚未接入发送适配器（本期占位）：消息未发送。"
            "请改用邮件或群机器人通道，或联系管理员开通服务商凭据后补适配器。")

    def _send_feishu(self, config: dict, content: str, title: str) -> dict:
        """Feishu/Lark group robot."""
        url = config["webhook_url"]
        payload = {"msg_type": "text", "content": {"text": content}}

        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code", 0) != 0:
            raise RuntimeError(f"Feishu error: {body}")
        return {"status_code": resp.status_code, "body": body}

    def _send_wecom(self, config: dict, content: str, title: str) -> dict:
        """WeCom/WeChat Work group robot."""
        url = config["webhook_url"]
        payload = {"msgtype": "markdown", "markdown": {"content": content}}

        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        if body.get("errcode", 0) != 0:
            raise RuntimeError(f"WeCom error: {body}")
        return {"status_code": resp.status_code, "body": body}

    def _send_email(self, config: dict, content: str, title: str) -> dict:
        """SMTP email."""
        host = config["smtp_host"]
        port = config.get("smtp_port", 465)
        user = config["smtp_user"]
        password = config["smtp_password"]
        use_ssl = config.get("use_ssl", True)
        from_addr = config.get("from_addr", user)
        to_addrs = config["to_addrs"]
        if isinstance(to_addrs, str):
            to_addrs = [to_addrs]

        msg = MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)
        msg.attach(MIMEText(content, "plain", "utf-8"))

        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()

        try:
            server.login(user, password)
            server.sendmail(from_addr, to_addrs, msg.as_string())
        finally:
            server.quit()

        return {"status": "sent", "to": to_addrs}

    def _send_webhook(self, config: dict, content: str, title: str) -> dict:
        """Generic HTTP POST webhook."""
        url = config["url"]
        method = config.get("method", "POST").upper()
        headers = config.get("headers", {})
        content_type = config.get("content_type", "json")

        if content_type == "form":
            payload = {"content": content}
            resp = httpx.request(method, url, data=payload, headers=headers, timeout=30)
        else:
            payload = {"content": content, "title": title, "timestamp": time.time()}
            resp = httpx.request(method, url, json=payload, headers=headers, timeout=30)

        resp.raise_for_status()
        return {"status_code": resp.status_code, "body": resp.text[:500]}

    # ── Helpers ───────────────────────────────────────────────────

    def _deserialize_row(self, row: dict):
        """Parse JSON string fields."""
        for field in ("config",):
            if field in row and isinstance(row[field], str):
                try:
                    row[field] = json.loads(row[field])
                except (json.JSONDecodeError, TypeError):
                    pass


# Singleton
notification_service = NotificationService()
