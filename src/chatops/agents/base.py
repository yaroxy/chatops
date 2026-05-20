#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-20
Description: 
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from chatops.core.models import AgentRunRequest, AgentRunResult


class AgentBackend(ABC):
    name: str

    @abstractmethod
    async def health(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def create_session(
        self,
        *,
        workspace: str,
        title: str | None = None,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    async def run(
        self,
        request: AgentRunRequest,
    ) -> AgentRunResult:
        raise NotImplementedError

    @abstractmethod
    async def abort(
        self,
        *,
        session_id: str,
        workspace: str,
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def list_messages(
        self,
        *,
        session_id: str,
        workspace: str,
    ) -> list[dict]:
        raise NotImplementedError
