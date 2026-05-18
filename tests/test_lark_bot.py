#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Author: yaroxy
Date: 2026-05-18
Description: 

references:
    - https://open.feishu.cn/document/develop-a-card-interactive-bot/introduction

usage:
    python tests/test_lark_bot.py
"""

from dotenv import load_dotenv
load_dotenv()

import json
import os

import lark_oapi as lark

from lark_oapi.api.im.v1 import *


APP_ID = os.environ["TEST_APP_ID"]
APP_SECRET = os.environ["TEST_APP_SECRET"]


client = (
    lark.Client.builder()
    .app_id(APP_ID)
    .app_secret(APP_SECRET)
    .log_level(lark.LogLevel.INFO)
    .build()
)


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
        response = client.im.v1.message.create(request)
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
    response = client.im.v1.message.reply(request)
    if not response.success():
        lark.logger.error(
            f"reply failed, code={response.code}, msg={response.msg}, "
            f"log_id={response.get_log_id()}"
        )


def handle_command(text: str) -> str:
    text = text.strip()

    if text.startswith("/help") or text.startswith("/command"):
        return """支持的命令：
/list-workspace 或 /ls
/change-workspace <name> 或 /cw <name>
/plan <task>
"""

    if text.startswith("/list-workspace") or text.startswith("/ls"):
        return "TODO: 返回 workspace 列表"

    if text.startswith("/change-workspace") or text.startswith("/cw"):
        return "TODO: 切换 workspace"

    if text.startswith("/plan"):
        prompt = text.removeprefix("/plan").strip()
        return f"TODO: 使用 opencode plan 模式处理：{prompt}"

    return "未识别命令。发送 /help 查看支持的命令。"


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

    # 群聊里 @bot 后，text 里可能包含 mention，需要按实际 payload 再清洗
    result = handle_command(text)
    send_text_response(message, result)


event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
    .build()
)


def main() -> None:
    ws_client = lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    ws_client.start()


if __name__ == "__main__":
    main()