#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-20
Description: 
平台只负责三件事：
    1.接收消息；
    2.解析成统一消息；
    3.发送回复。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from chatops.core.models import IncomingMessage, OutgoingMessage, ChatIdentity


MessageHandler = Callable[[IncomingMessage], Awaitable[OutgoingMessage]]


class PlatformAdapter(ABC):
    name: str

    @abstractmethod
    async def start(self, handler: MessageHandler) -> None:
        """启动平台监听，例如飞书长连接、Slack Socket Mode、HTTP webhook。"""
        raise NotImplementedError

    @abstractmethod
    async def send_message(
        self,
        identity: ChatIdentity,
        message: OutgoingMessage,
    ) -> None:
        """向指定平台会话发送消息。"""
        raise NotImplementedError