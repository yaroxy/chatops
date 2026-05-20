#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-18
Description: 

opencode version: 1.14.50

待测试：
- ### 健康检查
- ### 全局事件流



用户测试示例（按执行顺序）：
1. 用户发送 /help 或 /command，机器人回复帮助信息，包含所有可用的命令
2. 用户发送 /health ，机器人回复 opencode server 健康状态
3. 用户发送 /list-workspace 或 /ls ，机器人回复所有可用的 workspace 列表
    - 为了防止误操作，我们在 .toml 中配置了受信任的 workspace 列表，只有这些 workspace 才能被用户切换
    - 初始时会有默认 workspace，比如 test-chatops
4. 用户 /plan 记住数字 123
5. 用户 /build 刚刚的数字是什么
    - 这两步是测试 /plan 和 /build 的会话是否连续
6. 用户 /session ，机器人回复当前会话状态
7. 用户 /new-session 或 /ns ，机器人创建新会话
8. 用户 /change-workspace <name> 或 /cw <name> ，机器人切换到指定 workspace
9. 用户 /session ，机器人回复当前会话状态
10. 用户 /model ，机器人回复当前模型和连接的 provider
11. 用户 /model <provider/model> ，机器人切换到指定模型
12. 用户 /plan <task> ，机器人执行 plan agent
13. 用户在执行期间再次 /plan <task> ，机器人返回当前任务正在执行，不能同时执行多个任务
14. 用户 /build <task> ，机器人执行 build agent


"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import httpx
import json
import lark_oapi as lark
import logging
import os
import textwrap
import threading
import time

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from lark_oapi.api.im.v1 import *
from typing import Any, Literal


OPENCODE_SERVER_PASSWORD: str = os.environ.get("OPENCODE_SERVER_PASSWORD", "vibe-coding")
OPENCODE_SERVER_USERNAME: str = os.environ.get("OPENCODE_SERVER_USERNAME", "yaroxy")
OPENCODE_PORT: int = int(os.environ.get("OPENCODE_PORT", "8192"))
OPENCODE_HOSTNAME: str = os.environ.get("OPENCODE_HOSTNAME", "127.0.0.1")
OUTPUTS_DIR: str = os.environ.get("OUTPUTS_DIR", "outputs")


logger = logging.getLogger(__name__)

# ==============================================================================
# config
# ==============================================================================
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator


class WorkspaceConfig(BaseModel):
    path: Path

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: Path) -> Path:
        value = value.expanduser().resolve()

        if not value.exists():
            raise ValueError(f"workspace path does not exist: {value}")

        if not value.is_dir():
            raise ValueError(f"workspace path is not a directory: {value}")

        return value


class ChatOpsConfig(BaseModel):
    workspace: str
    agent: str = "plan"

    # TOML 里是 providerID / modelID
    # Python 里建议用 snake_case
    provider_id: str = Field(alias="providerID")
    model_id: str = Field(alias="modelID")

    workspaces: dict[str, WorkspaceConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_workspace(self) -> "ChatOpsConfig":
        if self.workspace not in self.workspaces:
            available = ", ".join(self.workspaces.keys())
            raise ValueError(
                f"workspace={self.workspace!r} not found in workspaces. "
                f"Available workspaces: {available}"
            )

        return self


class AppConfig(BaseModel):
    chatops: ChatOpsConfig


def load_config(config_path: str | Path) -> AppConfig:
    config_path = Path(config_path).expanduser().resolve()

    with config_path.open("rb") as f:
        raw_config = tomllib.load(f)

    return AppConfig.model_validate(raw_config)

# ==============================================================================
# opencode serve API
# ==============================================================================
class OpenCodeServeAPI:
    """
    HTTP client for the OpenCode serve API.

    opencode version: 1.14.50

    link:
        - https://opencode.ai/docs/zh-cn/server/
        - http://127.0.0.1:4096/doc

    Context manager (auto-closes on exit)::

        with OpenCodeServeAPI(...) as api:
            api.health()

    Regular initialization (call close() when done)::

        api = OpenCodeServeAPI(...)
        try:
            api.health()
        finally:
            api.close()
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth: tuple[str, str] | None = (
            (username, password) if username and password else None
        )
        if self._auth is None:
            logger.warning("No authentication provided, using anonymous access")
        self._client: httpx.Client | None = None

    def _create_client(self) -> httpx.Client:
        timeout = httpx.Timeout(
            connect=10.0,
            read=None,  # 关键：等待模型输出不要 read timeout
            write=30.0,
            pool=10.0,
        )
        return httpx.Client(
            auth=self._auth,
            base_url=self.base_url,
            timeout=timeout,
        )

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def open(self) -> OpenCodeServeAPI:
        """Create the underlying HTTP client if not already open."""
        self._ensure_client()
        return self

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> OpenCodeServeAPI:
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def client(self) -> httpx.Client:
        return self._ensure_client()

    def endpoint(
        self,
        method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"],
        endpoint: str,
        **kwargs: Any,
    ) -> dict[str, Any] | list[Any] | bool | None:
        r: httpx.Response | None = None
        try:
            path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
            r = self.client.request(method, path, **kwargs)
            r.raise_for_status()

            if r.status_code == 204 or not r.content:
                return None

            content_type = r.headers.get("content-type", "")
            if "application/json" not in content_type:
                return {
                    "error": "Non-JSON response",
                    "status_code": r.status_code,
                    "content_type": content_type,
                    "response": r.text,
                }

            return r.json()

        except httpx.TimeoutException as e:
            return {
                "error": f"Request timeout: {type(e).__name__}",
                "status_code": r.status_code if r is not None else None,
                "response": r.text if r is not None else None,
            }
        except httpx.HTTPStatusError as e:
            return {
                "error": f"HTTP status error: {str(e)}",
                "status_code": r.status_code if r is not None else None,
                "response": r.text if r is not None else None,
            }
        except Exception as e:
            return {
                "error": f"Request failed: {type(e).__name__}: {str(e)}",
                "status_code": r.status_code if r is not None else None,
                "response": r.text if r is not None else None,
            }

    def health(self) -> dict[str, Any]:
        """
        全局接口：用于检查健康状态和版本信息
        """
        return self.endpoint("GET", "/global/health")
    
    def path(self) -> dict[str, Any]:
        """
        Retrieve the current working directory and related path information for the OpenCode instance.
        """
        return self.endpoint("GET", "/path")

    def vcs(self) -> dict[str, Any]:
        """
        Retrieve version control system (VCS) information for the current project, such as git branch.
        """
        return self.endpoint("GET", "/vcs")
    
    def dispose(self) -> dict[str, Any]:
        """
        Clean up and dispose the current OpenCode instance, releasing all resources.
        """
        return self.endpoint("POST", "/instance/dispose")
    
    def config(self) -> dict[str, Any]:
        return self.endpoint("GET", "/config")
    
    def providers(self) -> dict[str, Any]:
        return self.endpoint("GET", "/config/providers")
    
    def provider(self) -> dict[str, Any]:
        """
        返回当前连接的 provider 列表，不包含 all 和 default 字段
        """
        res: dict[str, Any] = self.endpoint("GET", "/provider")
        res.pop("all", None)
        res.pop("default", None)
        return res
    
    def list_sessions(self, params: dict[str, Any] = {}) -> dict[str, Any]:
        return self.endpoint("GET", "/session", params=params)

    def create_session(
        self,
        json: dict[str, Any] = {},
        params: dict[str, Any] = {},
    ) -> dict[str, Any]:
        return self.endpoint("POST", "/session", json=json, params=params)
    
    def delete_session(self, session_id: str) -> dict[str, Any] | bool:
        """
        delete session by session id

        "delete": {
            "tags": [
                "session"
            ],
            "operationId": "session.delete",
            "parameters": [
                {
                "name": "sessionID",
                "in": "path",
                "schema": {
                    "type": "string",
                    "pattern": "^ses"
                },
                "required": true
                },
                {
                "name": "directory",
                "in": "query",
                "schema": {
                    "type": "string"
                },
                "required": false
                },
                {
                "name": "workspace",
                "in": "query",
                "schema": {
                    "type": "string"
                },
                "required": false
                }
            ],
            "responses": {
                "200": {
                "description": "Successfully deleted session",
                "content": {
                    "application/json": {
                    "schema": {
                        "type": "boolean",
                        "description": "Successfully deleted session"
                    }
                    }
                }
                },
                "400": {
                "description": "Bad request",
                "content": {
                    "application/json": {
                    "schema": {
                        "$ref": "#/components/schemas/BadRequestError"
                    }
                    }
                }
                },
                "404": {
                "description": "NotFoundError",
                "content": {
                    "application/json": {
                    "schema": {
                        "$ref": "#/components/schemas/NotFoundError"
                    }
                    }
                }
                }
            },
            "description": "Delete a session and permanently remove all associated data, including messages and history.",
            "summary": "Delete session"
        },
        """
        return self.endpoint("DELETE", f"/session/{session_id}")

    def status(self) -> dict[str, Any]:
        """
        Retrieve the current status of all sessions, including active, idle, and completed states.
        """
        return self.endpoint("GET", "/session/status")
    
    def abort(self, session_id: str) -> dict[str, Any] | bool:
        """
        Abort an active session and stop any ongoing AI processing or command execution.
        """
        return self.endpoint("POST", f"/session/{session_id}/abort")

    def diff(self, session_id: str) -> list:
        """
        Get the file changes (diff) that resulted from a specific user message in the session.
        """
        return self.endpoint("GET", f"/session/{session_id}/diff")

    def permissions(
        self,
        permission_id: str,
        session_id: str,
        *,
        json: dict[str, Any] = {},
        params: dict[str, Any] = {},
    ) -> dict[str, Any] | bool:
        """
        Approve or deny a permission request from the AI assistant.

        "/session/{sessionID}/permissions/{permissionID}": {
        "post": {
            "tags": [
            "session"
            ],
            "operationId": "permission.respond",
            "parameters": [
            {
                "name": "sessionID",
                "in": "path",
                "schema": {
                "type": "string",
                "pattern": "^ses"
                },
                "required": true
            },
            {
                "name": "permissionID",
                "in": "path",
                "schema": {
                "type": "string",
                "pattern": "^per"
                },
                "required": true
            },
            {
                "name": "directory",
                "in": "query",
                "schema": {
                "type": "string"
                },
                "required": false
            },
            {
                "name": "workspace",
                "in": "query",
                "schema": {
                "type": "string"
                },
                "required": false
            }
            ],
            "responses": {
            "200": {
                "description": "Permission processed successfully",
                "content": {
                "application/json": {
                    "schema": {
                    "type": "boolean",
                    "description": "Permission processed successfully"
                    }
                }
                }
            },
            "400": {
                "description": "Bad request",
                "content": {
                "application/json": {
                    "schema": {
                    "$ref": "#/components/schemas/BadRequestError"
                    }
                }
                }
            },
            "404": {
                "description": "NotFoundError",
                "content": {
                "application/json": {
                    "schema": {
                    "$ref": "#/components/schemas/NotFoundError"
                    }
                }
                }
            }
            },
            "description": "Approve or deny a permission request from the AI assistant.",
            "summary": "Respond to permission",
            "deprecated": true,
            "requestBody": {
            "content": {
                "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                    "response": {
                        "type": "string",
                        "enum": [
                        "once",
                        "always",
                        "reject"
                        ]
                    }
                    },
                    "required": [
                    "response"
                    ],
                    "additionalProperties": false
                }
                }
            }
            }
        }
        },
        """
        logger.warning("endpoint: /session/<session_id>/permissions/<permission_id> is deprecated")
        return self.endpoint("POST", f"/session/{session_id}/permissions/{permission_id}", json=json, params=params)
    
    def list_permissions(
        self,
        params: dict[str, Any] = {},
    ) -> list:
        """
        "/permission": {
            "get": {
            "tags": [
                "permission"
            ],
            "operationId": "permission.list",
            "parameters": [
                {
                "name": "directory",
                "in": "query",
                "schema": {
                    "type": "string"
                },
                "required": false
                },
                {
                "name": "workspace",
                "in": "query",
                "schema": {
                    "type": "string"
                },
                "required": false
                }
            ],
            "responses": {
                "200": {
                "description": "List of pending permissions",
                "content": {
                    "application/json": {
                    "schema": {
                        "type": "array",
                        "items": {
                        "$ref": "#/components/schemas/PermissionRequest"
                        },
                        "description": "List of pending permissions"
                    }
                    }
                }
                }
            },
            "description": "Get all pending permission requests across all sessions.",
            "summary": "List pending permissions"
            }
        },
        """
        return self.endpoint("GET", "/permission", params=params)

    def reply_permission(
        self,
        request_id: str,
        *,
        json: dict[str, Any] = {},
        params: dict[str, Any] = {},
    ) -> dict[str, Any]:
        """
        args:
            json
                required: reply
                optional: message
            params
                optional: directory
                optional: workspace


        "/permission/{requestID}/reply": {
        "post": {
            "tags": [
            "permission"
            ],
            "operationId": "permission.reply",
            "parameters": [
            {
                "name": "requestID",
                "in": "path",
                "schema": {
                "type": "string",
                "pattern": "^per"
                },
                "required": true
            },
            {
                "name": "directory",
                "in": "query",
                "schema": {
                "type": "string"
                },
                "required": false
            },
            {
                "name": "workspace",
                "in": "query",
                "schema": {
                "type": "string"
                },
                "required": false
            }
            ],
            "responses": {
            "200": {
                "description": "Permission processed successfully",
                "content": {
                "application/json": {
                    "schema": {
                    "type": "boolean",
                    "description": "Permission processed successfully"
                    }
                }
                }
            },
            "400": {
                "description": "Bad request",
                "content": {
                "application/json": {
                    "schema": {
                    "$ref": "#/components/schemas/BadRequestError"
                    }
                }
                }
            },
            "404": {
                "description": "Not found",
                "content": {
                "application/json": {
                    "schema": {
                    "$ref": "#/components/schemas/NotFoundError"
                    }
                }
                }
            }
            },
            "description": "Approve or deny a permission request from the AI assistant.",
            "summary": "Respond to permission request",
            "requestBody": {
            "content": {
                "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                    "reply": {
                        "type": "string",
                        "enum": [
                        "once",
                        "always",
                        "reject"
                        ]
                    },
                    "message": {
                        "type": "string"
                    }
                    },
                    "required": [
                    "reply"
                    ],
                    "additionalProperties": false
                }
                }
            }
            }
        }
        },
        """
        return self.endpoint("POST", f"/permission/{request_id}/reply", json=json, params=params)
    
    def list_messages(
        self,
        session_id: str,
        *,
        params: dict[str, Any] = {},
    ) -> list:
        """
        Retrieve all messages in a session, including user prompts and AI responses.

        args:

            params
                optional: directory
                optional: workspace
                optional: limit
                optional: before
        """
        return self.endpoint("GET", f"/session/{session_id}/message", params=params)
    
    def send_message(
        self,
        session_id: str,
        *,
        json: dict[str, Any] = {},
        params: dict[str, Any] = {},
    ) -> dict[str, Any]:
        """
        Create and send a new message to a session, streaming the AI response.

        args:
            json

                agent: plan / build
                parts:
                    [
                        {
                            "type": "text",
                            "text": "hello, world!"
                        }
                    ]

            params
                optional: directory
                optional: workspace
        
        return:
        Message 结构
        {
        "info": {                          // 消息元数据
            "id": "msg_xxx",                 // 消息 ID (msg_ 前缀)
            "sessionID": "ses_xxx",
            "parentID": "msg_xxx",           // 父消息 ID（用户消息引用上一条 assistant 消息）
            "role": "user | assistant",     // 角色
            "agent": "plan | build",         // 使用的 agent
            "mode": "plan | build",          // 执行模式
            "modelID": "deepseek-v4-pro",
            "providerID": "deepseek",
            "path": { "cwd": "...", "root": "/" },
            "cost": 0.0033,                  // 费用（仅 assistant）
            "tokens": {                      // token 用量（仅 assistant）
            "total": 18325,
            "input": 127,
            "output": 48,
            "reasoning": 102,
            "cache": { "read": 18048, "write": 0 }
            },
            "finish": "stop",                // 结束原因（仅 assistant）
            "summary": { "diffs": [] },     // 变更摘要（仅 user）
            "time": {
            "created": 1779182882117,     // 创建时间戳
            "completed": 1779182888867    // 完成时间戳（仅 assistant）
            }
        },
        "parts": [ ... ]                   // 内容部分
        }
        """
        return self.endpoint("POST", f"/session/{session_id}/message", json=json, params=params)

    def iter_global_events(
        self,
        *,
        max_events: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """
        usage:
            for event in api.iter_global_events(max_events=5):
                print(event.get("type"), event)
        """
        
        headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        data_lines: list[str] = []
        count = 0

        with self.client.stream(
            "GET",
            "/global/event",
            headers=headers,
            timeout=None,  # 长连接：不要给 read timeout
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line == "":
                    if not data_lines:
                        continue
                    payload = "\n".join(data_lines)
                    data_lines.clear()
                    event = json.loads(payload)
                    yield event
                    count += 1
                    if max_events is not None and count >= max_events:
                        break
                    continue
                if line.startswith(":"):
                    continue  # SSE 注释/心跳
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                # 可选: event: xxx / id: xxx


# ==============================================================================
# command router
# ==============================================================================
help_text = textwrap.dedent("""Supported commands:
/help or /command: Show commands
/health: Check the opencode server
/list-workspace or /ls: List allowed workspaces
/change-workspace <name> or /cw <name>: Switch workspace
/session: Show current chat state
/new-session or /ns: Clear the stored session (next request starts fresh)
/model: Show the current model and connected providers
/model <provider/model>: Switch model
/plan <task>: Run the opencode plan agent
/build <task>: Run the opencode build agent
/abort: Abort the current opencode task in this chat
""")

executor = ThreadPoolExecutor(max_workers=4)

class OpenCodeAgentBackend:
    """
    CommandRouter is a class that routes commands to the appropriate handler.
    """
    def __init__(
        self,
        api: OpenCodeServeAPI,
        config: AppConfig,
    ) -> None:
        self.api: OpenCodeServeAPI = api
        self.config: AppConfig = config

        self.current_workspace: str = self.config.chatops.workspace
        self.workspaces: dict[str, WorkspaceConfig] = self.config.chatops.workspaces

        """
        sessions: {
            "workspace_name": {
                "session_id": "session_id",
                "busy": False,
            }
        }
        """
        
        self.sessions: dict[str, dict[str, Any]] = {}
        self.session_lock: threading.Lock = threading.Lock()
    
        for workspace_name in self.workspaces.keys():
            self.sessions[workspace_name] = {
                "session_id": None,
                "busy": False,
            }

    def help(self) -> str:
        return help_text
    
    def list_workspace(self) -> str:
        
        lines = ["Allowed workspaces:"]
        for name, workspace in self.workspaces.items():
            mark = "*" if name == self.current_workspace else " "
            lines.append(f"{mark} {name}: {workspace.path}")
        return "\n".join(lines)

    def handle_command(
        self,
        command: str,
    ) -> str:
        """
        args:
            command: str
                e.g. /plan xxx
        """
        cmd, _, text = command.strip().partition(" ")

        if cmd in {"/h",  "/help", "/command"}:
            return self.help()
        
        if cmd in {"/health"}:
            try:
                health: dict[str, Any] = self.api.health()
                return json.dumps(health, ensure_ascii=False, indent=2)
            except Exception as e:
                return f"Health check failed: {e}"
        
        if cmd in {"/cfg", "/config"}:
            return self.config.model_dump_json(indent=2)
        
        if cmd in {"/list-workspace", "/ls"}:
            return self.list_workspace()

        if cmd in {"/change-workspace", "/cw"}:
            if not text:
                return "Usage: /cw <workspace>"
            if text not in self.workspaces.keys():
                return f"Unknown workspace: {text}\nSend /ls to see the allowed list."
            self.current_workspace = text
            return f"Switched workspace: {text}\nSend /status to see the current state."
        
        if cmd in {"/status"}:
            return json.dumps(self.sessions[self.current_workspace], ensure_ascii=False, indent=2)
        
        if cmd in {"/clear-session", "/cs"}:
            with self.session_lock:
                if self.sessions[self.current_workspace]["session_id"] is not None:
                    session_id = self.sessions[self.current_workspace]["session_id"]
                    
                    self.api.abort(session_id)
                    self.api.delete_session(session_id)
                    
                    self.sessions[self.current_workspace] = {
                        "session_id": None,
                        "busy": False,
                    }
                    return "Cleared the session. The next request will create a new session."
        
        if cmd in {"/abort"}:
            with self.session_lock:
                session_id = self.sessions[self.current_workspace]["session_id"]
                if session_id is not None:
                    self.api.abort(session_id)
                    self.sessions[self.current_workspace]["busy"] = False
                    return "Aborted the session. Send /status to see the current state."
                else:
                    return "No session to abort. Send /status to see the current state."
        
        if cmd in {"/plan", "/build"}:
            agent: str = cmd.removeprefix("/")
            if agent not in {"plan", "build"}:
                return "Usage: /plan <task> or /build <task>"
            
            prompt: str = text
            if not prompt:
                return f"Usage: {cmd} <task>"
            
            title: str = f"{self.current_workspace}-{agent}"
            directory: Path = self.workspaces[self.current_workspace].path

            session_id: str | None = self.sessions[self.current_workspace]["session_id"]
            if session_id is None:
                created_session: dict[str, Any] = self.api.create_session(
                    json={"title": title},
                    params={"directory": str(directory)},
                )
                self.sessions[self.current_workspace] = created_session["id"]
            
            session_id = self.sessions[self.current_workspace]
            self.api.send_message(

            )
            return "Running agent. Send /status to see the current state."

        
        return "Unknown command. Use /help or /command to see supported commands."
    
    def run_and_reply(
        self,
        
    ):
        pass

# ==============================================================================
# lark
# ==============================================================================
seen_message_ids: dict[str, float] = {}
seen_message_ids_lock = threading.Lock()

MESSAGE_ID_TTL_SECONDS = 10 * 60

def claim_message(message_id: str | None) -> bool:
    if not message_id:
        return True

    now = time.monotonic()

    with seen_message_ids_lock:
        # 清理过期 message_id
        expired_ids = [
            mid for mid, expire_at in seen_message_ids.items()
            if expire_at <= now
        ]
        for mid in expired_ids:
            seen_message_ids.pop(mid, None)

        if message_id in seen_message_ids:
            return False

        seen_message_ids[message_id] = now + MESSAGE_ID_TTL_SECONDS
        return True

lark.APP_ID = os.environ.get("LARK_APP_ID", None)
lark.APP_SECRET = os.environ.get("LARK_APP_SECRET", None)

if not lark.APP_ID or not lark.APP_SECRET:
    raise ValueError("LARK_APP_ID and LARK_APP_SECRET must be set")

class LarkBot:
    def __init__(
        self,
        backend: OpenCodeAgentBackend,
    ) -> None:
        self.backend: OpenCodeAgentBackend = backend

        self.client: lark.Client = lark.Client.builder().app_id(lark.APP_ID).app_secret(lark.APP_SECRET).build()

        # Register event handler.
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self.do_p2_im_message_receive_v1)
            .register_p2_im_message_message_read_v1(self.do_p2_im_message_message_read_v1)
            .build()
        )
        self.ws_client = lark.ws.Client(
            lark.APP_ID,
            lark.APP_SECRET,
            event_handler=event_handler,
            log_level=lark.LogLevel.DEBUG,
        )
    
    def start(self) -> None:
        self.ws_client.start()

    def send_text_response(self, message: EventMessage, text: str) -> None:
        payload = json.dumps({"text": text}, ensure_ascii=False)

        if message.chat_type == "p2p":
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(message.chat_id)
                    .msg_type("text")
                    .content(payload)
                    .build()
                )
                .build()
            )

            # Use send OpenAPI to send messages
            # https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/create
            response = self.client.im.v1.message.create(request)

            if not response.success():
                lark.logger.error(
                    f"create message failed, code={response.code}, msg={response.msg}, "
                    f"log_id={response.get_log_id()}"
                )
        else:
            request = (
                ReplyMessageRequest.builder()
                .message_id(message.message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .content(payload)
                    .msg_type("text")
                    .build()
                )
                .build()
            )

            # Reply to messages using send OpenAPI
            # https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/reply
            response = self.client.im.v1.message.reply(request)

            if not response.success():
                lark.logger.error(
                    f"reply failed, code={response.code}, msg={response.msg}, "
                    f"log_id={response.get_log_id()}"
                )

    # Register event handler to handle received messages.
    # https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/events/receive
    def do_p2_im_message_receive_v1(self, data: P2ImMessageReceiveV1) -> None:
        message = data.event.message

        try:
            message_id: str = message.message_id

            if not claim_message(message_id):
                self.send_text_response(message, f"Duplicate message: {message_id}")
                return
        except Exception as e:
            self.send_text_response(message, f"Getting message_id failed: {e}")
            return

        if message.message_type != "text":
            self.send_text_response(message, "Only text messages are supported for now.")
            return

        try:
            content = json.loads(message.content)
            text = content.get("text", "")
        except Exception:
            text = message.content

        try:
            result = self.backend.handle_command(text)
        except Exception as e:
            result = f"Handler command failed: {e}"

        self.send_text_response(message, result)


    def do_p2_im_message_message_read_v1(self, data: P2ImMessageMessageReadV1) -> None:
        return


def lark_bot() -> None:
    try:
        api = OpenCodeServeAPI(
            f"http://{OPENCODE_HOSTNAME}:{OPENCODE_PORT}",
            OPENCODE_SERVER_USERNAME,
            OPENCODE_SERVER_PASSWORD,
        )
        config: AppConfig = load_config("tests/test.toml")
        backend = OpenCodeAgentBackend(
            api=api,
            config=config,
        )
        bot = LarkBot(backend)
        bot.start()
    finally:
        if api is not None:
            api.close()

# ==============================================================================
# 测试 OpenCode Serve API
# ==============================================================================
def test_opencode_serve_api() -> None:
    workspace_dir = "/Users/roxy/Documents/workspace/test-chatops"
    modelId = "deepseek-v4-pro"
    providerId = "deepseek"

    with OpenCodeServeAPI(
        f"http://{OPENCODE_HOSTNAME}:{OPENCODE_PORT}",
        OPENCODE_SERVER_USERNAME,
        OPENCODE_SERVER_PASSWORD,
    ) as api:
        print("health:", api.health())
        print("path:", api.path())
        print("vcs:", api.vcs())
        print("provider connected:", api.provider())

        created_session: dict[str, Any] = api.create_session(
            json={"title": "test-create-session"},
            params={"directory": workspace_dir},
        )
        print("create session:", created_session)

        print("conditional list sessions:", api.list_sessions(
            {"search": "test-create-session"}
        ))
        
        sent_message: dict[str, Any] = api.send_message(
            session_id=created_session["id"],
            json={
                "agent": "plan",
                "model": {
                    "modelID": modelId,
                    "providerID": providerId,
                },
                "parts": [
                    {
                        "type": "text",
                        "text": "你是谁？是什么模型？当前目录是什么？记住数字 1236"
                    }
                ],
            },
            params={"directory": workspace_dir},
        )
        print("send message:", json.dumps(sent_message, ensure_ascii=False, indent=2))

        sent_message: dict[str, Any] = api.send_message(
            session_id=created_session["id"],
            json={
                "agent": "build",
                "parts": [
                    {
                        "type": "text",
                        "text": "刚刚的数字是什么？"
                    }
                ],
            },
            params={"directory": workspace_dir},
        )
        print("send message:", json.dumps(sent_message, ensure_ascii=False, indent=2))

        print("status:", api.status())
        print("diff:", api.diff(created_session["id"]))

        print("permissions:", api.list_permissions())

        print("abort session:", api.abort(created_session["id"]))

        messages: list[dict[str, Any]] = api.list_messages(
            session_id=created_session["id"],
            params={"directory": workspace_dir},
        )
        print("list messages:", json.dumps(messages, ensure_ascii=False, indent=2))

        deleted_session = api.delete_session(created_session["id"])
        print("delete session:", type(deleted_session), deleted_session)


# ==============================================================================
# 测试 command router
# ==============================================================================
def test_opencode_agent_backend() -> None:
    command_router = OpenCodeAgentBackend()
    print(command_router.help())


"""
usage:
    python tests/test_opencode_serve.py
"""
if __name__ == "__main__":
    # test_opencode_serve_api()
    # print("=" * 80)
    # test_opencode_agent_backend()

    lark_bot()
