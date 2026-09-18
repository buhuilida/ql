#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# cron: 15 8 * * *
# new Env('论坛模板签到')

复制本文件后，按目标论坛修改 CHECKIN_URL、请求头、请求体和成功判断。
不要把 Cookie、Token 或账号密码直接写进代码。

青龙环境变量：
    FORUM_TEMPLATE_COOKIES  多账号，一行一个 Cookie；也支持用 | 分隔
    FORUM_TEMPLATE_UA       可选 User-Agent
"""

import os
from typing import Dict, List

import requests

try:
    # 青龙容器通常提供 notify.py；本地运行时可能不存在。
    from notify import send as ql_send
except ImportError:
    ql_send = None


CHECKIN_URL = "https://example.com/api/checkin"  # TODO: 替换为目标论坛签到接口
ENV_NAME = "FORUM_TEMPLATE_COOKIES"


def read_accounts() -> List[str]:
    """读取多账号凭据，不记录或打印凭据内容。"""
    raw = os.getenv(ENV_NAME, "")
    return [item.strip() for item in raw.replace("|", "\n").splitlines() if item.strip()]


def checkin(cookie: str) -> str:
    """执行一次签到；每个论坛必须根据实际接口改写本函数。"""
    headers: Dict[str, str] = {
        "User-Agent": os.getenv(
            "FORUM_TEMPLATE_UA",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        ),
        "Cookie": cookie,
        "Accept": "application/json, text/plain, */*",
    }

    # TODO: 有些论坛需要 POST JSON、表单、CSRF Token 或特定 Referer。
    # 请先在浏览器开发者工具 Network 中确认请求，再替换下面的参数。
    response = requests.get(CHECKIN_URL, headers=headers, timeout=20)
    response.raise_for_status()

    try:
        data = response.json()
        message = data.get("message") or data.get("msg") or str(data)
        success = data.get("success") is True or data.get("code") in (0, 200)
    except ValueError:
        message = response.text[:200]
        success = response.ok

    if not success:
        raise RuntimeError(f"接口返回失败：{message}")
    return str(message)


def main() -> None:
    accounts = read_accounts()
    if not accounts:
        text = f"未配置 {ENV_NAME}，请在青龙变量中填写 Cookie。"
        print(text)
        notify("论坛模板签到", text)
        return

    results = []
    for index, cookie in enumerate(accounts, start=1):
        try:
            message = checkin(cookie)
            result = f"账号 {index}：签到成功，{message}"
        except Exception as exc:  # 单个账号失败不影响后续账号
            result = f"账号 {index}：签到失败，{exc}"
        print(result)
        results.append(result)

    notify("论坛模板签到", "\n".join(results))


def notify(title: str, content: str) -> None:
    if ql_send is not None:
        ql_send(title, content)


if __name__ == "__main__":
    main()

