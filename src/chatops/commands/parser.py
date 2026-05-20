#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-20
Description: 文本 -> Command 对象
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class Command:
    name: str
    args: str


class CommandParser:
    aliases = {
        "/help": "help",
        "/command": "help",
        "/health": "health",
        "/list-workspace": "list_workspace",
        "/ls": "list_workspace",
        "/change-workspace": "change_workspace",
        "/cw": "change_workspace",
        "/session": "session",
        "/new-session": "new_session",
        "/ns": "new_session",
        "/model": "model",
        "/plan": "run_agent",
        "/build": "run_agent",
        "/abort": "abort",
    }

    def parse(self, text: str) -> Command:
        cmd, _, args = text.strip().partition(" ")
        name = self.aliases.get(cmd)

        if name is None:
            return Command(name="unknown", args=text)

        return Command(name=name, args=args)
