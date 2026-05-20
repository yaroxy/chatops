#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-20
Description: 
"""
from __future__ import annotations


from chatops.agents.base import AgentBackendRegistry
from chatops.commands.parser import CommandParser
from chatops.core.models import IncomingMessage, OutgoingMessage


class ChatOpsService:
    def __init__(
        self,
        *,
        parser: CommandParser,
        agent_backend_registry: "AgentBackendRegistry",
        # state_store: "StateStore",
        # workspace_store: "WorkspaceStore",
    ) -> None:
        self.parser = parser
        self.agent_backend_registry = agent_backend_registry
        # self.state_store = state_store
        # self.workspace_store = workspace_store

    async def handle_message(self, message: IncomingMessage) -> OutgoingMessage:
        command = self.parser.parse(message.text)

        match command.name:
            case "help":
                return OutgoingMessage(text=self.help_text())

            case "health":
                backend = self.agent_backend_registry.default()
                health = await backend.health()
                return OutgoingMessage(text=health)

            case "list_workspace":
                workspaces = self.workspace_store.list_allowed()
                return OutgoingMessage(text="\n".join(workspaces))

            case "change_workspace":
                return await self.change_workspace(message, command.args)

            case "run_agent":
                return await self.run_agent(message, command)

            case "abort":
                return await self.abort(message)

            case _:
                return OutgoingMessage(
                    text="Unknown command. Use /help to see supported commands."
                )
