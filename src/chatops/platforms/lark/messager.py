#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-18
Description: 
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

from concurrent.futures import ThreadPoolExecutor
from lark_oapi.api.im.v1 import *

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
help_text = textwrap.dedent("""支持的命令：
/list-workspace 或 /ls
/change-workspace <name> 或 /cw <name>
/plan <task>
""")

def run_opencode_plan_and_reply(
    message: EventMessage,
    prompt: str,
    task_id: str,
) -> None:
    # debug
    print(f"run_opencode_plan_and_reply: prompt={prompt}, task_id={task_id}")
    try:
        workspace_dir = "/Users/roxy/Documents/workspace/test-chatops"

        cmd = [
            "opencode",
            "run",
            "--dir",
            workspace_dir,
            "--agent",
            "plan",
            prompt,
        ]

        completed = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=600,
        )
        # debug
        print(f"run_opencode_plan_and_reply: completed={completed}")

        if completed.returncode == 0:
            output = completed.stdout.strip()
            if not output:
                output = "opencode 执行完成，但没有输出。"

            text = f"任务 {task_id} 执行完成：\n\n{output}"
        else:
            text = (
                f"任务 {task_id} 执行失败，退出码：{completed.returncode}\n\n"
                f"stderr:\n{completed.stderr[-3000:]}"
            )

    except subprocess.TimeoutExpired:
        text = f"任务 {task_id} 执行超时。"

    except Exception:
        text = f"任务 {task_id} 执行异常：\n\n{traceback.format_exc()[-3000:]}"

    send_text_response(message, text)

def handle_command(message: EventMessage, text: str) -> str:
    text = text.strip()

    if text.startswith("/help") or text.startswith("/command"):
        return help_text

    if text.startswith("/list-workspace") or text.startswith("/ls"):
        return "TODO: 返回 workspace 列表"

    if text.startswith("/change-workspace") or text.startswith("/cw"):
        return "TODO: 切换 workspace"

    if text.startswith("/plan"):
        prompt = text.removeprefix("/plan").strip()
        if not prompt:
            return "用法：/plan <task>"

        task_id = uuid.uuid4().hex[:8]

        future = executor.submit(
            run_opencode_plan_and_reply,
            message,
            prompt,
            task_id,
        )
        future.add_done_callback(log_future_exception)

        return f"已收到 /plan 任务，任务 ID：{task_id}\n我会在执行完成后把结果发到当前会话。"

    return "未识别命令。发送 /help 或 /command 查看支持的命令。"


def log_future_exception(future):
    try:
        future.result()
    except Exception:
        print("background task failed:")
        print(traceback.format_exc())

# ==============================================================================
# 回调函数
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

        # 使用OpenAPI发送消息
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

        # 使用OpenAPI回复消息
        # Reply to messages using send OpenAPI
        # https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/reply
        response = client.im.v1.message.reply(request)

        if not response.success():
            lark.logger.error(
                f"reply failed, code={response.code}, msg={response.msg}, "
                f"log_id={response.get_log_id()}"
            )

# 注册接收消息事件，处理接收到的消息。
# Register event handler to handle received messages.
# https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/events/receive
def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    message = data.event.message

    if message.message_type != "text":
        send_text_response(message, "暂时只支持文本消息。")
        return

    try:
        content = json.loads(message.content)
        text = content.get("text", "")
    except Exception:
        text = message.content

    result = handle_command(message, text)
    send_text_response(message, result)


# ==============================================================================
# SECTION TITLE: MESSAGER.PY
# ==============================================================================
# 注册事件回调
# Register event handler.
event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
    .build()
)

# 创建 LarkClient 对象，用于请求OpenAPI, 并创建 LarkWSClient 对象，用于使用长连接接收事件。
# Create LarkClient object for requesting OpenAPI, and create LarkWSClient object for receiving events using long connection.
client = lark.Client.builder().app_id(lark.APP_ID).app_secret(lark.APP_SECRET).build()
wsClient = lark.ws.Client(
    lark.APP_ID,
    lark.APP_SECRET,
    event_handler=event_handler,
    log_level=lark.LogLevel.DEBUG,
)

def main() -> None:
    #  启动长连接，并注册事件处理器。
    #  Start long connection and register event handler.
    wsClient.start()


"""
usage:
    python src/chatops/platforms/lark/messager.py
"""
if __name__ == "__main__":

    main()