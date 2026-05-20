#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import httpx
import json
import lark_oapi as lark
import logging
import os
import re
import threading
import time
import textwrap
import tomllib
import uuid

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from lark_oapi.api.im.v1 import *

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ==============================================================================
# env
# ==============================================================================

OPENCODE_SERVER_PASSWORD: str = os.environ.get("OPENCODE_SERVER_PASSWORD", "vibe-coding")
OPENCODE_SERVER_USERNAME: str = os.environ.get("OPENCODE_SERVER_USERNAME", "yaroxy")
OPENCODE_PORT: int = int(os.environ.get("OPENCODE_PORT", "8192"))
OPENCODE_HOSTNAME: str = os.environ.get("OPENCODE_HOSTNAME", "127.0.0.1")
OUTPUTS_DIR: str = os.environ.get("OUTPUTS_DIR", "outputs")
CONFIG_PATH: str = os.environ.get("CHATOPS_CONFIG", "tests/test.toml")

LARK_APP_ID = os.environ.get("LARK_APP_ID")
LARK_APP_SECRET = os.environ.get("LARK_APP_SECRET")

if not LARK_APP_ID or not LARK_APP_SECRET:
    raise ValueError("LARK_APP_ID and LARK_APP_SECRET must be set")

lark.APP_ID = LARK_APP_ID
lark.APP_SECRET = LARK_APP_SECRET


# ==============================================================================
# logging
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)

# 避免 Lark SDK 日志被 SDK handler 和 root handler 打印两次
logging.getLogger("Lark").propagate = False

logger = logging.getLogger(__name__)


# ==============================================================================
# config
# ==============================================================================

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
    """
    Supports TOML like:

    [chatops]
    default_workspace="test-chatops"
    default_agent="plan"
    default_providerID="deepseek"
    default_modelID="deepseek-v4-pro"

    [chatops.workspaces.test-chatops]
    path="/Users/roxy/Documents/workspace/test-chatops"
    """

    model_config = ConfigDict(populate_by_name=True)

    workspace: str = Field(
        validation_alias=AliasChoices("workspace", "default_workspace"),
    )
    agent: str = Field(
        default="plan",
        validation_alias=AliasChoices("agent", "default_agent"),
    )
    provider_id: str = Field(
        validation_alias=AliasChoices("provider_id", "providerID", "default_providerID"),
    )
    model_id: str = Field(
        validation_alias=AliasChoices("model_id", "modelID", "default_modelID"),
    )

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
    HTTP client for opencode serve API.

    Tested around opencode version: 1.14.50
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
        self._client_lock = threading.Lock()

    def _create_client(self) -> httpx.Client:
        timeout = httpx.Timeout(
            connect=10.0,
            read=None,
            write=30.0,
            pool=10.0,
        )

        return httpx.Client(
            auth=self._auth,
            base_url=self.base_url,
            timeout=timeout,
        )

    def _ensure_client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None:
                self._client = self._create_client()
            return self._client

    @property
    def client(self) -> httpx.Client:
        return self._ensure_client()

    def open(self) -> "OpenCodeServeAPI":
        self._ensure_client()
        return self

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def __enter__(self) -> "OpenCodeServeAPI":
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

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
        res = self.endpoint("GET", "/global/health")
        assert isinstance(res, dict)
        return res

    def path(self) -> dict[str, Any]:
        res = self.endpoint("GET", "/path")
        assert isinstance(res, dict)
        return res

    def vcs(self) -> dict[str, Any]:
        res = self.endpoint("GET", "/vcs")
        assert isinstance(res, dict)
        return res

    def provider(self) -> dict[str, Any]:
        res = self.endpoint("GET", "/provider")
        assert isinstance(res, dict)

        res.pop("all", None)
        res.pop("default", None)
        return res

    def create_session(
        self,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        res = self.endpoint(
            "POST",
            "/session",
            json=json_body or {},
            params=params or {},
        )

        if not isinstance(res, dict):
            raise RuntimeError(f"create_session returned non-dict response: {res}")

        if "error" in res:
            raise RuntimeError(json.dumps(res, ensure_ascii=False, indent=2))

        if "id" not in res:
            raise RuntimeError(f"create_session response has no id: {res}")

        return res

    def delete_session(self, session_id: str) -> dict[str, Any] | bool | None:
        return self.endpoint("DELETE", f"/session/{session_id}")

    def abort(self, session_id: str) -> dict[str, Any] | bool | None:
        return self.endpoint("POST", f"/session/{session_id}/abort")

    def list_messages(
        self,
        session_id: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[Any]:
        res = self.endpoint(
            "GET",
            f"/session/{session_id}/message",
            params=params or {},
        )

        if not isinstance(res, list):
            return []

        return res

    def send_message(
        self,
        session_id: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        res = self.endpoint(
            "POST",
            f"/session/{session_id}/message",
            json=json_body or {},
            params=params or {},
        )

        if not isinstance(res, dict):
            raise RuntimeError(f"send_message returned non-dict response: {res}")

        if "error" in res:
            raise RuntimeError(json.dumps(res, ensure_ascii=False, indent=2))

        return res


# ==============================================================================
# command router / backend
# ==============================================================================

help_text = textwrap.dedent(
    """
    Supported commands:
    /help or /command: Show commands
    /health: Check the opencode server
    /list-workspace or /ls: List allowed workspaces
    /change-workspace <name> or /cw <name>: Switch workspace
    /status or /session: Show current workspace session state
    /clear-session or /cs: Delete current workspace session when idle
    /model: Show current model and connected providers
    /model <provider/model>: Switch model
    /plan <task>: Run the opencode plan agent
    /build <task>: Run the opencode build agent
    /abort: Abort the current opencode task in this workspace
    """
).strip()

executor = ThreadPoolExecutor(max_workers=4)


class OpenCodeAgentBackend:
    def __init__(
        self,
        api: OpenCodeServeAPI,
        config: AppConfig,
    ) -> None:
        self.api = api
        self.config = config

        self.current_workspace: str = self.config.chatops.workspace
        self.workspaces: dict[str, WorkspaceConfig] = self.config.chatops.workspaces

        self.provider_id: str = self.config.chatops.provider_id
        self.model_id: str = self.config.chatops.model_id

        self.session_lock = threading.Lock()

        self.sessions: dict[str, dict[str, Any]] = {}

        for workspace_name in self.workspaces.keys():
            self.sessions[workspace_name] = {
                "session_id": None,
                "busy": False,
                "task_id": None,
                "running_agent": None,
                "running_prompt": None,
                "started_at": None,
            }

    def help(self) -> str:
        return help_text

    def list_workspace(self) -> str:
        with self.session_lock:
            current_workspace = self.current_workspace

        lines = ["Allowed workspaces:"]

        for name, workspace in self.workspaces.items():
            mark = "*" if name == current_workspace else " "
            lines.append(f"{mark} {name}: {workspace.path}")

        return "\n".join(lines)

    def format_status(self, workspace_name: str) -> str:
        with self.session_lock:
            state = dict(self.sessions[workspace_name])

        if state.get("started_at") is not None:
            state["running_seconds"] = round(time.time() - state["started_at"], 1)

        return json.dumps(state, ensure_ascii=False, indent=2)

    def format_agent_result(self, result: Any) -> str:
        if result is None:
            return "Task finished, but opencode returned no content."

        if isinstance(result, dict):
            parts = result.get("parts") or []

            texts: list[str] = []

            for part in parts:
                if not isinstance(part, dict):
                    continue

                if part.get("type") == "text" and part.get("text"):
                    texts.append(str(part["text"]))

            if texts:
                return "\n".join(texts).strip()

            return json.dumps(result, ensure_ascii=False, indent=2)

        return str(result)

    def clip_for_lark(self, text: str, limit: int = 7000) -> str:
        text = text.strip()

        if len(text) <= limit:
            return text

        return text[:limit] + "\n\n...[truncated]"

    def run_agent_and_reply(
        self,
        *,
        workspace_name: str,
        task_id: str,
        session_id: str | None,
        agent: Literal["plan", "build"],
        prompt: str,
        directory: Path,
        on_done: Callable[[str], None] | None,
    ) -> None:
        should_notify = False
        final_text = ""

        try:
            if session_id is None:
                created_session = self.api.create_session(
                    json_body={"title": f"{workspace_name}-{agent}"},
                    params={"directory": str(directory)},
                )
                session_id = created_session["id"]

                with self.session_lock:
                    state = self.sessions[workspace_name]

                    if state.get("task_id") != task_id:
                        try:
                            self.api.delete_session(session_id)
                        except Exception:
                            logger.exception("failed to delete unused session")
                        return

                    state["session_id"] = session_id

            result = self.api.send_message(
                session_id=session_id,
                json_body={
                    "agent": agent,
                    "model": {
                        "modelID": self.model_id,
                        "providerID": self.provider_id,
                    },
                    "parts": [
                        {
                            "type": "text",
                            "text": prompt,
                        }
                    ],
                },
                params={"directory": str(directory)},
            )

            final_text = self.format_agent_result(result)
            final_text = self.clip_for_lark(final_text)

        except Exception as e:
            logger.exception(
                "agent task failed: workspace=%s task_id=%s agent=%s",
                workspace_name,
                task_id,
                agent,
            )
            final_text = f"Task failed: {type(e).__name__}: {e}"

        finally:
            with self.session_lock:
                state = self.sessions[workspace_name]

                if state.get("task_id") == task_id:
                    state["busy"] = False
                    state["task_id"] = None
                    state["running_agent"] = None
                    state["running_prompt"] = None
                    state["started_at"] = None
                    should_notify = True

        if should_notify and on_done is not None:
            try:
                on_done(final_text)
            except Exception:
                logger.exception("sending final reply failed")

    def handle_command(
        self,
        command: str,
        *,
        on_done: Callable[[str], None] | None = None,
    ) -> str:
        command = command.strip()

        if not command:
            return "Empty command. Use /help or /command to see supported commands."

        cmd, _, text = command.partition(" ")
        text = text.strip()

        if cmd in {"/h", "/help", "/command"}:
            return self.help()

        if cmd == "/health":
            try:
                health = self.api.health()
                return json.dumps(health, ensure_ascii=False, indent=2)
            except Exception as e:
                return f"Health check failed: {type(e).__name__}: {e}"

        if cmd in {"/cfg", "/config"}:
            return self.config.model_dump_json(indent=2)

        if cmd in {"/list-workspace", "/ls"}:
            return self.list_workspace()

        if cmd in {"/change-workspace", "/cw"}:
            if not text:
                return "Usage: /cw <workspace>"

            if text not in self.workspaces:
                return f"Unknown workspace: {text}\nSend /ls to see the allowed list."

            with self.session_lock:
                self.current_workspace = text

            return f"Switched workspace: {text}\nSend /status to see the current state."

        if cmd in {"/status", "/session"}:
            with self.session_lock:
                workspace_name = self.current_workspace

            return self.format_status(workspace_name)

        if cmd == "/model":
            if not text:
                try:
                    providers = self.api.provider()
                    provider_text = json.dumps(providers, ensure_ascii=False, indent=2)
                except Exception as e:
                    provider_text = f"Failed to fetch providers: {type(e).__name__}: {e}"

                return (
                    f"Current model:\n"
                    f"providerID: {self.provider_id}\n"
                    f"modelID: {self.model_id}\n\n"
                    f"Connected providers:\n{provider_text}"
                )

            if "/" not in text:
                return "Usage: /model <provider/model>"

            provider_id, model_id = text.split("/", 1)
            provider_id = provider_id.strip()
            model_id = model_id.strip()

            if not provider_id or not model_id:
                return "Usage: /model <provider/model>"

            self.provider_id = provider_id
            self.model_id = model_id

            return (
                "Model updated:\n"
                f"providerID: {self.provider_id}\n"
                f"modelID: {self.model_id}"
            )

        if cmd in {"/clear-session", "/cs"}:
            with self.session_lock:
                workspace_name = self.current_workspace
                state = self.sessions[workspace_name]

                if state["busy"]:
                    return (
                        f"Workspace `{workspace_name}` is busy.\n"
                        f"Please send /abort first, or wait until the task finishes."
                    )

                session_id = state["session_id"]

                if session_id is None:
                    return "No session to clear."

            try:
                self.api.delete_session(session_id)
            except Exception as e:
                return f"Delete session failed: {type(e).__name__}: {e}"

            with self.session_lock:
                state = self.sessions[workspace_name]

                if state["session_id"] == session_id:
                    state["session_id"] = None
                    state["busy"] = False
                    state["task_id"] = None
                    state["running_agent"] = None
                    state["running_prompt"] = None
                    state["started_at"] = None

            return "Cleared the session. The next /plan or /build will create a new session."

        if cmd == "/abort":
            with self.session_lock:
                workspace_name = self.current_workspace
                state = self.sessions[workspace_name]

                session_id = state["session_id"]
                task_id = state["task_id"]

                if not state["busy"]:
                    return "No running task to abort."

                state["busy"] = False
                state["task_id"] = None
                state["running_agent"] = None
                state["running_prompt"] = None
                state["started_at"] = None

            if session_id is not None:
                try:
                    self.api.abort(session_id)
                except Exception:
                    logger.exception(
                        "abort failed: workspace=%s session_id=%s task_id=%s",
                        workspace_name,
                        session_id,
                        task_id,
                    )

            return "Abort requested. Send /status to see the current state."

        if cmd in {"/plan", "/build"}:
            agent: Literal["plan", "build"] = cmd.removeprefix("/")  # type: ignore

            if not text:
                return f"Usage: {cmd} <task>"

            with self.session_lock:
                workspace_name = self.current_workspace
                directory = self.workspaces[workspace_name].path
                state = self.sessions[workspace_name]

                if state["busy"]:
                    running_agent = state.get("running_agent") or "unknown"
                    running_prompt = state.get("running_prompt") or ""

                    return (
                        f"Workspace `{workspace_name}` is busy.\n"
                        f"Running agent: {running_agent}\n"
                        f"Current task: {running_prompt[:200]}\n\n"
                        f"Please wait until it finishes, or send /abort."
                    )

                task_id = f"task_{uuid.uuid4().hex[:12]}"
                session_id = state["session_id"]

                state["busy"] = True
                state["task_id"] = task_id
                state["running_agent"] = agent
                state["running_prompt"] = text
                state["started_at"] = time.time()

            executor.submit(
                self.run_agent_and_reply,
                workspace_name=workspace_name,
                task_id=task_id,
                session_id=session_id,
                agent=agent,
                prompt=text,
                directory=directory,
                on_done=on_done,
            )

            session_text = session_id if session_id is not None else "creating..."

            return (
                f"Task submitted.\n"
                f"Workspace: {workspace_name}\n"
                f"Session: {session_text}\n"
                f"Task: {task_id}\n"
                f"Agent: {agent}\n\n"
                f"I will send the result when it finishes."
            )

        return "Unknown command. Use /help or /command to see supported commands."


# ==============================================================================
# lark idempotency
# ==============================================================================

seen_message_ids: dict[str, float] = {}
seen_message_ids_lock = threading.Lock()

MESSAGE_ID_TTL_SECONDS = 10 * 60


def claim_message(message_id: str | None) -> bool:
    if not message_id:
        return True

    now = time.monotonic()

    with seen_message_ids_lock:
        expired_ids = [
            mid
            for mid, expire_at in seen_message_ids.items()
            if expire_at <= now
        ]

        for mid in expired_ids:
            seen_message_ids.pop(mid, None)

        if message_id in seen_message_ids:
            return False

        seen_message_ids[message_id] = now + MESSAGE_ID_TTL_SECONDS
        return True


# ==============================================================================
# lark bot
# ==============================================================================

class LarkBot:
    def __init__(
        self,
        backend: OpenCodeAgentBackend,
    ) -> None:
        self.backend = backend

        self.client: lark.Client = (
            lark.Client.builder()
            .app_id(LARK_APP_ID)
            .app_secret(LARK_APP_SECRET)
            .build()
        )

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self.do_p2_im_message_receive_v1)
            .register_p2_im_message_message_read_v1(self.do_p2_im_message_message_read_v1)
            .build()
        )

        self.ws_client = lark.ws.Client(
            LARK_APP_ID,
            LARK_APP_SECRET,
            event_handler=event_handler,
            # log_level=lark.LogLevel.DEBUG,
        )

    def start(self) -> None:
        self.ws_client.start()

    def normalize_lark_text(self, message: EventMessage, text: str) -> str | None:
        """
        p2p:
            /help

        group:
            @_user_1 /help

        群聊中只有 @bot 才响应。
        """
        text = text.strip()

        if message.chat_type == "p2p":
            return text

        # --------------------------------------------------------------------------
        # 1. 最稳妥：直接处理 content 里的 @_user_1 前缀
        # --------------------------------------------------------------------------
        # 飞书群聊 @bot 后，content.text 通常类似：
        #   @_user_1 /help
        #   @_user_1 /plan xxx
        #
        # 只处理 @xxx 后面紧跟 /command 的情况，避免误响应普通群消息。
        match = re.match(r"^@\S+\s+(/.*)$", text, flags=re.DOTALL)

        if match:
            return match.group(1).strip()

        # --------------------------------------------------------------------------
        # 2. 可选：再尝试 mentions 字段
        # --------------------------------------------------------------------------
        mentions = getattr(message, "mentions", None) or []

        logger.info("lark mentions raw objects: %r", mentions)

        for mention in mentions:
            # 调试 MentionEvent 真实字段
            logger.info(
                "mention object: type=%s dict=%s dir=%s",
                type(mention),
                getattr(mention, "__dict__", None),
                [x for x in dir(mention) if not x.startswith("_")],
            )

        # 如果没匹配到 @xxx /command，则群聊中忽略
        return None

    def send_text_response(self, message: EventMessage, text: str) -> None:
        text = text or "(empty)"
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

            response = self.client.im.v1.message.create(request)

            if not response.success():
                lark.logger.error(
                    f"create message failed, code={response.code}, msg={response.msg}, "
                    f"log_id={response.get_log_id()}"
                )

            return

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

        response = self.client.im.v1.message.reply(request)

        if not response.success():
            lark.logger.error(
                f"reply failed, code={response.code}, msg={response.msg}, "
                f"log_id={response.get_log_id()}"
            )

    def do_p2_im_message_receive_v1(self, data: P2ImMessageReceiveV1) -> None:
        message = data.event.message

        try:
            message_id: str = message.message_id

            if not claim_message(message_id):
                logger.info("duplicate message ignored: %s", message_id)
                return

        except Exception as e:
            self.send_text_response(
                message,
                f"Getting message_id failed: {type(e).__name__}: {e}",
            )
            return

        if message.message_type != "text":
            self.send_text_response(message, "Only text messages are supported for now.")
            return

        try:
            content = json.loads(message.content)
            raw_text = content.get("text", "")
        except Exception:
            raw_text = message.content

        normalized_text = self.normalize_lark_text(message, raw_text)

        logger.info(
            "lark text normalized: chat_type=%s raw=%r normalized=%r",
            message.chat_type,
            raw_text,
            normalized_text,
        )

        # 群聊中没有 @bot，直接忽略
        if normalized_text is None:
            self.send_text_response(message, "Command extracted: None. Please inspect the logs for details.")
            return

        if not normalized_text:
            self.send_text_response(message, "Empty command. Use /help or /command.")
            return

        try:
            def on_done(final_text: str) -> None:
                self.send_text_response(message, final_text)

            result = self.backend.handle_command(
                normalized_text,
                on_done=on_done,
            )

        except Exception as e:
            logger.exception("handle command failed")
            result = f"Handler command failed: {type(e).__name__}: {e}"

        self.send_text_response(message, result)

    def do_p2_im_message_message_read_v1(self, data: P2ImMessageMessageReadV1) -> None:
        return


# ==============================================================================
# app entry
# ==============================================================================

def lark_bot() -> None:
    api: OpenCodeServeAPI | None = None

    try:
        api = OpenCodeServeAPI(
            f"http://{OPENCODE_HOSTNAME}:{OPENCODE_PORT}",
            OPENCODE_SERVER_USERNAME,
            OPENCODE_SERVER_PASSWORD,
        )

        config = load_config(CONFIG_PATH)

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
# local tests
# ==============================================================================

def test_config() -> None:
    config = load_config(CONFIG_PATH)
    print(config.model_dump_json(indent=2))


def test_opencode_serve_api() -> None:
    with OpenCodeServeAPI(
        f"http://{OPENCODE_HOSTNAME}:{OPENCODE_PORT}",
        OPENCODE_SERVER_USERNAME,
        OPENCODE_SERVER_PASSWORD,
    ) as api:
        print("health:", api.health())
        print("path:", api.path())
        print("vcs:", api.vcs())
        print("provider:", api.provider())


if __name__ == "__main__":
    # test_config()
    # test_opencode_serve_api()
    lark_bot()