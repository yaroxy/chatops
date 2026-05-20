#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-20
Description: 
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ChatIdentity:
    platform: str          # "lark", "slack", "cli"
    
    chat_id: str           # 群/私聊 ID
    user_id: str | None    # 发送者
    thread_id: str | None = None


@dataclass(frozen=True)
class IncomingMessage:
    identity: ChatIdentity
    text: str
    raw: object | None = None


@dataclass(frozen=True)
class OutgoingMessage:
    text: str


@dataclass(frozen=True)
class AgentRunRequest:
    task: str
    workspace: str # "test-chatops", ...
    session_id: str | None
    agent: str             # "plan", "build", ...
    model: str | None = None # "deepseek-v4-pro", ...
    provider: str | None = None # "deepseek", ...


@dataclass(frozen=True)
class AgentRunResult:
    text: str
    session_id: str | None = None
    raw: object | None = None