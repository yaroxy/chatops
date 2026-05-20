#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-20
Description: 
"""
from __future__ import annotations

import httpx
import json
import logging

from typing import Any, Literal, Iterator
from .base import AgentBackend

logger = logging.getLogger(__name__)

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


class OpenCodeAgentBackend(AgentBackend):
    name = "opencode"

    def __init__(self, api: OpenCodeServeAPI) -> None:
        self.api = api



