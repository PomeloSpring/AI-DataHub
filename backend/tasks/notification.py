"""Notification Sender — sends messages to various channels.

Supported channels:
- dingtalk: DingTalk group robot (webhook + optional sign)
- feishu: Feishu/Lark group robot (webhook)
- wecom: WeCom/WeChat Work group robot (webhook)
- email: SMTP email
- webhook: Generic HTTP POST
"""

import hashlib
import hmac
import json
import logging
import smtplib
import time
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

logger = logging.getLogger(__name__)


class NotificationSender:
    """Send notifications to configured channels."""

    def send(self, channel_id: int, content: str) -> dict:
        """Send notification to a channel by ID."""
        from backend.services.scheduled_task_service import scheduled_task_service

        channel = scheduled_task_service.get_channel(channel_id)
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
        }

        handler = dispatch.get(channel_type)
        if not handler:
            raise ValueError(f"Unknown channel type: {channel_type}")

        logger.info("[Notification] Sending via %s (channel_id=%s)", channel_type, channel_id)
        return handler(config, content)

    def test(self, channel_id: int) -> dict:
        """Send a test message to verify channel connectivity."""
        return self.send(channel_id, "🔔 AI-DataHub 通知渠道测试消息")

    # ── DingTalk ────────────────────────────────────────────────────

    def _send_dingtalk(self, config: dict, content: str) -> dict:
        """DingTalk group robot.

        config: {
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
            "secret": "SEC..." (optional, for signed messages)
        }
        """
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
            "markdown": {
                "title": "AI-DataHub 定时报告",
                "text": content,
            },
        }

        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        if body.get("errcode", 0) != 0:
            raise RuntimeError(f"DingTalk error: {body}")
        return {"status_code": resp.status_code, "body": body}

    # ── Feishu / Lark ──────────────────────────────────────────────

    def _send_feishu(self, config: dict, content: str) -> dict:
        """Feishu/Lark group robot.

        config: {
            "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
        }
        """
        url = config["webhook_url"]
        payload = {
            "msg_type": "text",
            "content": {"text": content},
        }

        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code", 0) != 0:
            raise RuntimeError(f"Feishu error: {body}")
        return {"status_code": resp.status_code, "body": body}

    # ── WeCom / WeChat Work ────────────────────────────────────────

    def _send_wecom(self, config: dict, content: str) -> dict:
        """WeCom group robot.

        config: {
            "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
        }
        """
        url = config["webhook_url"]
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }

        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        if body.get("errcode", 0) != 0:
            raise RuntimeError(f"WeCom error: {body}")
        return {"status_code": resp.status_code, "body": body}

    # ── Email (SMTP) ───────────────────────────────────────────────

    def _send_email(self, config: dict, content: str) -> dict:
        """SMTP email.

        config: {
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_user": "user@example.com",
            "smtp_password": "password",
            "use_ssl": true,
            "from_addr": "noreply@example.com",
            "to_addrs": ["admin@example.com", "boss@example.com"]
        }
        """
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
        msg["Subject"] = "AI-DataHub 定时报告"
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

    # ── Generic Webhook ────────────────────────────────────────────

    def _send_webhook(self, config: dict, content: str) -> dict:
        """Generic HTTP POST webhook.

        config: {
            "url": "https://example.com/webhook",
            "method": "POST",
            "headers": {"Authorization": "Bearer xxx"},
            "content_type": "json"  // or "form"
        }
        """
        url = config["url"]
        method = config.get("method", "POST").upper()
        headers = config.get("headers", {})
        content_type = config.get("content_type", "json")

        if content_type == "form":
            payload = {"content": content}
            resp = httpx.request(method, url, data=payload, headers=headers, timeout=30)
        else:
            payload = {"content": content, "timestamp": time.time()}
            resp = httpx.request(method, url, json=payload, headers=headers, timeout=30)

        resp.raise_for_status()
        return {"status_code": resp.status_code, "body": resp.text[:500]}


# Singleton
notification_sender = NotificationSender()
