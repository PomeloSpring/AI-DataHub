"""Notification Service — manage channels and send messages.

Supported channels: DingTalk, Feishu, WeCom, Email, Webhook

Table: adh_notification_channels
"""

import hashlib
import hmac
import json
import logging
import smtplib
import time
import urllib.parse
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import httpx

from services.shared.common.db import get_metadata_conn

logger = logging.getLogger(__name__)


def _get_conn():
    """Get a database connection from the shared pool."""
    return get_metadata_conn()



class NotificationService:
    """Manage notification channels and send messages."""

    # ── Channel CRUD ──────────────────────────────────────────────

    def list_channels(self, workspace_id: int = 0) -> list:
        """List notification channels for a workspace."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                if workspace_id:
                    cur.execute(
                        "SELECT * FROM adh_notification_channels WHERE workspace_id = %s ORDER BY created_at DESC",
                        (workspace_id,),
                    )
                else:
                    cur.execute("SELECT * FROM adh_notification_channels ORDER BY created_at DESC")
                rows = cur.fetchall()
                for row in rows:
                    self._deserialize_row(row)
                return rows
        finally:
            conn.close()

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

    def create_channel(self, data: dict, workspace_id: int = 0) -> int:
        """Create a new notification channel."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    """INSERT INTO adh_notification_channels
                    (name, channel_type, config, workspace_id, is_active, test_status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 1, 'untested', %s, %s)""",
                    (
                        data["name"],
                        data["channel_type"],
                        json.dumps(data["config"]),
                        workspace_id,
                        now, now,
                    ),
                )
                conn.commit()
                return cur.lastrowid
        finally:
            conn.close()

    def update_channel(self, channel_id: int, data: dict) -> bool:
        """Update a notification channel."""
        if not data:
            return True

        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                sets = []
                params = []
                for key, value in data.items():
                    if key == "config":
                        sets.append(f"{key} = %s")
                        params.append(json.dumps(value))
                    else:
                        sets.append(f"{key} = %s")
                        params.append(value)

                sets.append("updated_at = %s")
                params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                params.append(channel_id)

                cur.execute(
                    f"UPDATE adh_notification_channels SET {', '.join(sets)} WHERE id = %s",
                    params,
                )
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def update_channel_status(self, channel_id: int, status: str):
        """Update the test status of a channel."""
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE adh_notification_channels SET test_status = %s, updated_at = %s WHERE id = %s",
                    (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), channel_id),
                )
                conn.commit()
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
        }

        handler = dispatch.get(channel_type)
        if not handler:
            raise ValueError(f"Unknown channel type: {channel_type}")

        logger.info("[Notification] Sending via %s (channel_id=%s)", channel_type, channel_id)
        return handler(config, content, title)

    def test_channel(self, channel_id: int) -> dict:
        """Send a test message to verify channel connectivity."""
        return self.send(channel_id, "AI-DataHub Notification Channel Test Message", "Test")

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
