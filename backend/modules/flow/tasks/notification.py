"""Notification Sender — 定时任务侧的通知发送入口（薄壳）。

历史上这个文件里有一份与 ``services/notification_service`` **完全重复**的 5 个通道实现
（钉钉/飞书/企微/邮件/Webhook），连标题都各写一套（这里 "AI-DataHub 定时报告"，那边
"AI-DataHub Notification"）。两套实现的实际代价：消息模板接线要改两遍、改一处漏一处，
而页面测试按钮走 service、定时任务走这里 —— 同一个渠道两种发法。

现在传输实现只有一份（``NotificationService``），本模块只保留 ``notification_sender``
这个既有单例与 ``send(channel_id, content, title)`` 形状，供 executor 与既有测试桩使用。
"""

import logging

from backend.modules.flow.services.notification_service import notification_service

logger = logging.getLogger(__name__)


class NotificationSender:
    """按渠道 id 发送（委托 NotificationService，不再自带通道实现）。"""

    def send(self, channel_id: int, content: str,
             title: str = "AI-DataHub 定时报告") -> dict:
        return notification_service.send(channel_id, content, title)

    def send_rendered(self, channel_id: int, context: dict) -> dict:
        """按渠道引用的消息模板渲染后发送（透传渲染元信息，便于诊断）。"""
        return notification_service.send_rendered(channel_id, context)

    def test(self, channel_id: int) -> dict:
        """测试发送：与定时任务同一条渲染链（本页以前调的是不存在的方法，直接 AttributeError）。"""
        return notification_service.test_channel(channel_id)


# Singleton
notification_sender = NotificationSender()
