#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-18
Description: 

workflow:

加载环境变量 .env

加载 .toml 配置

--config /mnt/data/cpfs/yafengsun/workspace/chatops/config/chatops.opencode.toml 或者默认配置 config/chatops.opencode.toml



"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import json
import lark_oapi as lark
import os
import subprocess
import textwrap
import traceback
import uuid
import tomllib

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from lark_oapi.api.im.v1 import *
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

executor = ThreadPoolExecutor(max_workers=4)

# ==============================================================================
# env
# ==============================================================================
APP_ID = os.environ["LARK_APP_ID"]
APP_SECRET = os.environ["LARK_APP_SECRET"]
lark.APP_ID = APP_ID
lark.APP_SECRET = APP_SECRET

# ==============================================================================
# for opencode
# ==============================================================================
WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
CONFIG_PATH = Path(os.environ.get("CHATOPS_OPENCODE_CONFIG", WORKSPACE_ROOT / "config/chatops.opencode.toml"))


@dataclass(frozen=True)
class OpenCodeSettings:
    server_url: str
    username_env: str
    password_env: str
    default_workspace: str
    default_agent: str
    default_model: str
    state_path: Path
    task_timeout_seconds: int
    workspaces: dict[str, Path]


def load_opencode_settings() -> OpenCodeSettings:
    with CONFIG_PATH.open("rb") as f:
        raw = tomllib.load(f)

    opencode = raw["opencode"]
    workspaces = {
        name: Path(item["path"]).expanduser()
        for name, item in opencode.get("workspaces", {}).items()
    }
    default_workspace = opencode["default_workspace"]
    if default_workspace not in workspaces:
        raise ValueError(f"default_workspace is not configured: {default_workspace}")
    state_path = Path(opencode.get("state_path", ".chatops/opencode-state.json"))
    if not state_path.is_absolute():
        state_path = WORKSPACE_ROOT / state_path

    return OpenCodeSettings(
        server_url=opencode.get("server_url", "http://127.0.0.1:4096").rstrip("/"),
        username_env=opencode.get("username_env", "OPENCODE_SERVER_USERNAME"),
        password_env=opencode.get("password_env", "OPENCODE_SERVER_PASSWORD"),
        default_workspace=default_workspace,
        default_agent=opencode.get("default_agent", "plan"),
        default_model=opencode.get("default_model", ""),
        state_path=state_path,
        task_timeout_seconds=int(opencode.get("task_timeout_seconds", 1800)),
        workspaces=workspaces,
    )


settings = load_opencode_settings()
chat_locks: dict[str, Lock] = {}
chat_locks_guard = Lock()
state_lock = Lock()
running_tasks: dict[str, list[subprocess.Popen[str]]] = {}
NO_OPENCODE_OUTPUT = "opencode completed, but returned no output."


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


class OpenCodeClient:
    def __init__(self, cfg: OpenCodeSettings):
        self.cfg = cfg

    def get_json(self, path: str) -> Any:
        req = request.Request(f"{self.cfg.server_url}{path}")
        username = os.environ.get(self.cfg.username_env)
        password = os.environ.get(self.cfg.password_env)
        if username and password:
            import base64

            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")

        with request.urlopen(req, timeout=10) as response:
            data = response.read().decode("utf-8")
            return json.loads(data) if data else None

    def health(self) -> dict[str, Any]:
        return self.get_json("/global/health")

    def providers(self) -> dict[str, Any]:
        return self.get_json("/provider")

    def sessions(self) -> list[dict[str, Any]]:
        return self.get_json("/session")

    def session_messages(self, session_id: str) -> list[dict[str, Any]]:
        return self.get_json(f"/session/{session_id}/message")

    def find_session(self, workspace_dir: Path, agent: str, title: str) -> str | None:
        workspace = str(workspace_dir)
        matches = [
            item
            for item in self.sessions()
            if item.get("directory") == workspace
            and item.get("agent") == agent
            and item.get("title") == title
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: item.get("time", {}).get("updated", 0), reverse=True)
        return matches[0].get("id")


opencode_client = OpenCodeClient(settings)


def state_key(message: EventMessage) -> str:
    return message.chat_id


def get_chat_lock(chat_id: str) -> Lock:
    with chat_locks_guard:
        if chat_id not in chat_locks:
            chat_locks[chat_id] = Lock()
        return chat_locks[chat_id]


def load_state() -> dict[str, Any]:
    if not settings.state_path.exists():
        return {}
    try:
        return json.loads(settings.state_path.read_text(encoding="utf-8"))
    except Exception:
        print(f"failed to load state: {settings.state_path}")
        print(traceback.format_exc())
        return {}


def save_state(state: dict[str, Any]) -> None:
    settings.state_path.parent.mkdir(parents=True, exist_ok=True)
    settings.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_chat_state(chat_id: str) -> dict[str, Any]:
    with state_lock:
        state = load_state()
        chat_state = state.setdefault(chat_id, {})
        chat_state.setdefault("workspace", settings.default_workspace)
        if chat_state["workspace"] not in settings.workspaces:
            chat_state["workspace"] = settings.default_workspace
        chat_state.setdefault("model", settings.default_model)
        chat_state.setdefault("session_id", None)
        chat_state.setdefault("active_task", None)
        save_state(state)
        return dict(chat_state)


def update_chat_state(chat_id: str, **updates: Any) -> dict[str, Any]:
    with state_lock:
        state = load_state()
        chat_state = state.setdefault(chat_id, {})
        chat_state.setdefault("workspace", settings.default_workspace)
        if chat_state["workspace"] not in settings.workspaces:
            chat_state["workspace"] = settings.default_workspace
        chat_state.setdefault("model", settings.default_model)
        chat_state.setdefault("session_id", None)
        chat_state.update(updates)
        save_state(state)
        return dict(chat_state)


def command_parts(text: str) -> tuple[str, str]:
    cmd, _, arg = text.strip().partition(" ")
    return cmd, arg.strip()


def summarize_output(stdout: str) -> str:
    output = stdout.strip()
    if not output:
        return NO_OPENCODE_OUTPUT
    parsed_output, _ = extract_json_run_result(output)
    if parsed_output:
        return parsed_output
    if is_json_event_stream(output):
        return NO_OPENCODE_OUTPUT
    output = clean_assistant_text(output)
    if not output:
        return NO_OPENCODE_OUTPUT
    if len(output) > 3500:
        return output[-3500:]
    return output


def clean_assistant_text(text: str) -> str:
    text = text.strip()
    while True:
        start = text.find("<system-reminder>")
        if start == -1:
            break
        end = text.find("</system-reminder>", start)
        if end == -1:
            text = text[:start]
            break
        text = text[:start] + text[end + len("</system-reminder>"):]
    return text.strip()


def is_json_event_stream(stdout: str) -> bool:
    saw_json = False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            return False
        saw_json = True
    return saw_json


def extract_latest_assistant_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        info = message.get("info", {})
        if info.get("role") != "assistant":
            continue
        texts = [
            clean_assistant_text(part.get("text", ""))
            for part in message.get("parts", [])
            if part.get("type") == "text" and clean_assistant_text(part.get("text", ""))
        ]
        if texts:
            return "\n".join(texts).strip()
    return ""


def extract_json_run_result(stdout: str) -> tuple[str, str | None]:
    texts: list[str] = []
    session_id = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = event.get("sessionID") or session_id
        part = event.get("part", {})
        if part.get("type") == "text" and part.get("text"):
            text = clean_assistant_text(part["text"])
            if text:
                texts.append(text)
            session_id = part.get("sessionID") or session_id
    return "\n".join(texts).strip(), session_id


def run_opencode_command(
    cmd: list[str],
    env: dict[str, str],
    process_holder: list[subprocess.Popen[str]] | None = None,
) -> tuple[int, str, str]:
    process = subprocess.Popen(
        cmd,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if process_holder is not None:
        process_holder.append(process)
    try:
        try:
            stdout, stderr = process.communicate(timeout=settings.task_timeout_seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            stdout, stderr = process.communicate()
            raise
        return process.returncode or 0, stdout, stderr
    finally:
        if process_holder is not None:
            process_holder.clear()


def run_opencode_task_and_reply(
    message: EventMessage,
    prompt: str,
    agent: str,
    task_id: str,
) -> None:
    chat_id = state_key(message)
    lock = get_chat_lock(chat_id)

    try:
        chat_state = get_chat_state(chat_id)
        workspace_name = chat_state["workspace"]
        workspace_dir = settings.workspaces[workspace_name]
        model = chat_state.get("model") or settings.default_model
        session_id = chat_state.get("session_id")
        title = f"chatops {task_id}: {prompt[:64]}"

        cmd = [
            "opencode", "run",
            "--dir", str(workspace_dir),
            "--agent", agent,
            "--title", title,
            "--format", "json",
        ]
        child_env = os.environ.copy()
        child_env.pop(settings.username_env, None)
        child_env.pop(settings.password_env, None)

        if model:
            cmd.extend(["--model", model])
        if session_id:
            cmd.extend(["--session", session_id])
        cmd.append(prompt)

        print(f"run_opencode_task_and_reply: task_id={task_id}, agent={agent}, workspace={workspace_name}")
        process_holder: list[subprocess.Popen[str]] = []
        running_tasks[chat_id] = process_holder
        returncode, stdout, stderr = run_opencode_command(cmd, child_env, process_holder)

        if returncode != 0:
            text = (
                f"Task {task_id} failed (exit code {returncode}).\n\n"
                f"stderr:\n{stderr[-3000:]}" if stderr else ""
            )
            send_text_response(message, text)
            return

        _, stdout_session_id = extract_json_run_result(stdout)
        active_session_id = stdout_session_id or session_id
        if stdout_session_id:
            update_chat_state(chat_id, session_id=stdout_session_id)

        output = NO_OPENCODE_OUTPUT

        if active_session_id:
            try:
                messages = opencode_client.session_messages(active_session_id)
                output = extract_latest_assistant_text(messages)
            except Exception:
                print("failed to fetch session messages from opencode server")
                print(traceback.format_exc())

        if output == NO_OPENCODE_OUTPUT:
            output = summarize_output(stdout)

        if output == NO_OPENCODE_OUTPUT:
            text = f"Task {task_id} completed ({workspace_name}/{agent}), but no output was produced."
            if stderr:
                text += f"\n\nstderr:\n{stderr[-2000:]}"
            text += f"\n\nstdout ({len(stdout)} chars):\n{stdout[-3000:]}" if stdout else ""
        else:
            text = f"Task {task_id} completed ({workspace_name}/{agent}):\n\n{output}"
        send_text_response(message, text)

    except subprocess.TimeoutExpired:
        send_text_response(message, f"Task {task_id} timed out.")
    except Exception:
        send_text_response(
            message,
            f"Task {task_id} raised an exception:\n\n{traceback.format_exc()[-3000:]}",
        )
    finally:
        running_tasks.pop(chat_id, None)
        update_chat_state(chat_id, active_task=None)
        lock.release()


def handle_command(message: EventMessage, text: str) -> str:
    text = text.strip()
    chat_id = state_key(message)
    cmd, arg = command_parts(text)

    if cmd in {"/help", "/command"}:
        return help_text

    if cmd == "/health":
        try:
            health = opencode_client.health()
            return f"opencode server is healthy: version={health.get('version')}"
        except (HTTPError, URLError, TimeoutError) as exc:
            return f"opencode server is unavailable: {exc}"

    if cmd in {"/list-workspace", "/ls"}:
        current = get_chat_state(chat_id)["workspace"]
        lines = ["Allowed workspaces:"]
        for name, path in settings.workspaces.items():
            mark = "*" if name == current else " "
            lines.append(f"{mark} {name}: {path}")
        return "\n".join(lines)

    if cmd in {"/change-workspace", "/cw"}:
        if not arg:
            return "Usage: /cw <workspace>"
        if arg not in settings.workspaces:
            return f"Unknown workspace: {arg}\nSend /ls to see the allowed list."
        if running_tasks.get(chat_id):
            return "This chat has a running task. Wait for it to finish or send /abort before switching workspace."
        update_chat_state(chat_id, workspace=arg, session_id=None, active_task=None)
        return f"Switched workspace: {arg}"

    if cmd == "/session":
        chat_state = get_chat_state(chat_id)
        busy = "yes" if running_tasks.get(chat_id) else "no"
        return "\n".join(
            [
                "Current state:",
                f"workspace: {chat_state['workspace']}",
                f"model: {chat_state.get('model') or settings.default_model}",
                f"busy: {busy}",
                f"session_id: {chat_state.get('session_id') or 'none'}",
            ]
        )

    if cmd in {"/new-session", "/ns"}:
        update_chat_state(chat_id, session_id=None)
        return "Cleared the session. The next request will create a new session."

    if cmd == "/model":
        if not arg:
            try:
                providers = opencode_client.providers()
                connected = ", ".join(providers.get("connected", [])) or "none"
            except Exception:
                connected = "failed to fetch"
            chat_state = get_chat_state(chat_id)
            return f"Current model: {chat_state.get('model') or settings.default_model}\nConnected providers: {connected}\nUsage: /model <provider/model>"
        if running_tasks.get(chat_id):
            return "This chat has a running task. Wait for it to finish or send /abort before switching model."
        update_chat_state(chat_id, model=arg)
        return f"Switched model: {arg}"

    if cmd in {"/plan", "/build"}:
        prompt = arg
        if not prompt:
            return f"Usage: {cmd} <task>"

        lock = get_chat_lock(chat_id)
        if not lock.acquire(blocking=False):
            return "This chat has a running opencode task. Wait for it to finish or send /abort."

        task_id = uuid.uuid4().hex[:8]
        agent = cmd.removeprefix("/")
        update_chat_state(chat_id, active_task={"id": task_id, "agent": agent})

        future = executor.submit(
            run_opencode_task_and_reply,
            message,
            prompt,
            agent,
            task_id,
        )
        future.add_done_callback(log_future_exception)

        return f"Accepted {cmd} task. Task ID: {task_id}\nI will send the result back to this chat when it completes."

    if cmd == "/abort":
        procs = running_tasks.get(chat_id)
        if not procs:
            return "This chat has no running task."
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        return "Abort request sent."

    return "Unknown command. Send /help or /command to see supported commands."


def log_future_exception(future):
    try:
        future.result()
    except Exception:
        print("background task failed:")
        print(traceback.format_exc())

# ==============================================================================
# callbacks
# ==============================================================================
def send_text_response(message: EventMessage, text: str) -> None:
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
        response = client.im.v1.message.create(request)

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
        response = client.im.v1.message.reply(request)

        if not response.success():
            lark.logger.error(
                f"reply failed, code={response.code}, msg={response.msg}, "
                f"log_id={response.get_log_id()}"
            )

# Register event handler to handle received messages.
# https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/events/receive
def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    message = data.event.message

    if message.message_type != "text":
        send_text_response(message, "Only text messages are supported for now.")
        return

    try:
        content = json.loads(message.content)
        text = content.get("text", "")
    except Exception:
        text = message.content

    result = handle_command(message, text)
    send_text_response(message, result)


def do_p2_im_message_message_read_v1(data: P2ImMessageMessageReadV1) -> None:
    return


# ==============================================================================
# SECTION TITLE: MESSAGER.PY
# ==============================================================================
# Register event handler.
event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
    .register_p2_im_message_message_read_v1(do_p2_im_message_message_read_v1)
    .build()
)

# Create LarkClient object for requesting OpenAPI, and create LarkWSClient object for receiving events using long connection.
client = lark.Client.builder().app_id(lark.APP_ID).app_secret(lark.APP_SECRET).build()
wsClient = lark.ws.Client(
    lark.APP_ID,
    lark.APP_SECRET,
    event_handler=event_handler,
    log_level=lark.LogLevel.DEBUG,
)

def main() -> None:
    #  Start long connection and register event handler.
    wsClient.start()


"""
usage:
    python src/chatops/platforms/lark/messager.py
"""
if __name__ == "__main__":

    main()
