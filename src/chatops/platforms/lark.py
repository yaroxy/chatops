#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-20
Description: 
"""
from __future__ import annotations

from chatops.core.models import IncomingMessage, OutgoingMessage, ChatIdentity

from .base import PlatformAdapter, MessageHandler


class LarkPlatformAdapter(PlatformAdapter):
    name = "lark"

    async def start(self, handler: MessageHandler) -> None:
        # 注册飞书事件回调
        # 收到消息后转换成 IncomingMessage
        # 调用 response = await handler(incoming)
        # 再 send_message(...)
        ...

    async def send_message(
        self,
        identity: ChatIdentity,
        message: OutgoingMessage,
    ) -> None:
        # 调用飞书 create/reply API
        ...
