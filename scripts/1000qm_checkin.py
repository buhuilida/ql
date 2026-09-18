#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# cron: 10 8 * * *
# new Env('阡陌居每日签到')

阡陌居（www.1000qm.vip）Discuz dsu_paulsign 签到脚本。

青龙环境变量：
    QM1000_COOKIES  多账号 Cookie，一行一个；也支持用 | 分隔
    QM1000_COOKIE   单账号 Cookie（兼容写法）
    QM1000_MESSAGE  签到寄语，可选，默认“谢谢生活”
    QM1000_MOOD     签到心情代码，可选，默认“kx”

Cookie 只从环境变量读取，不要写入脚本或提交到 Git。
"""

import html
import os
import re
from typing import List, Optional, Tuple

import requests

try:
    # 青龙容器通常提供 notify.py；本地运行时可能不存在。
    from notify import send as ql_send
except ImportError:
    ql_send = None


BASE_URL = "https://www.1000qm.vip"
SIGN_PAGE = f"{BASE_URL}/plugin.php?id=dsu_paulsign:sign"
SIGN_URL = f"{BASE_URL}/plugin.php?id=dsu_paulsign:sign&operation=qiandao&infloat=1&inajax=1"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def read_accounts() -> List[str]:
    raw = os.getenv("QM1000_COOKIES") or os.getenv("QM1000_COOKIE", "")
    return [item.strip() for item in raw.replace("|", "\n").splitlines() if item.strip()]


def extract_formhash(page: str) -> Optional[str]:
    """从签到页的 hidden input 动态提取 formhash。"""
    for tag in re.findall(r"<input\b[^>]*>", page, flags=re.IGNORECASE):
        name = re.search(r"\bname\s*=\s*['\"]([^'\"]+)['\"]", tag, flags=re.IGNORECASE)
        if not name or name.group(1).lower() != "formhash":
            continue
        value = re.search(r"\bvalue\s*=\s*['\"]([^'\"]*)['\"]", tag, flags=re.IGNORECASE)
        if value:
            return html.unescape(value.group(1))
    return None


def page_text(content: str) -> str:
    content = re.sub(r"<script\b[^>]*>.*?</script>", " ", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<style\b[^>]*>.*?</style>", " ", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<[^>]+>", " ", content)
    return re.sub(r"\s+", " ", html.unescape(content)).strip()


def response_message(content: str) -> str:
    """提取 Discuz XML/HTML 响应里的可读提示。"""
    text = page_text(content)
    text = re.sub(r"^.*?签到提示\s*", "", text, flags=re.IGNORECASE)
    return text[:300]


def is_logged_in(page: str) -> bool:
    match = re.search(r"\bdiscuz_uid\s*=\s*['\"](\d+)['\"]", page, flags=re.IGNORECASE)
    return not match or match.group(1) != "0"


def checkin_once(cookie: str) -> Tuple[str, str]:
    headers = {
        "User-Agent": os.getenv("QM1000_UA", DEFAULT_UA),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": SIGN_PAGE,
        "Origin": BASE_URL,
        "Cookie": cookie,
    }
    session = requests.Session()

    try:
        page_resp = session.get(SIGN_PAGE, headers=headers, timeout=20)
        page_resp.raise_for_status()
    except requests.RequestException as exc:
        return "NET_ERR", f"打开签到页失败：{exc}"

    page = page_resp.text
    if not is_logged_in(page):
        return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"

    visible = page_text(page)
    if "您今天已经签到过了" in visible and "上次签到时间" in visible:
        return "ALREADY_TODAY", "今天已经签到过了"

    formhash = extract_formhash(page)
    if not formhash:
        return "PARSE_ERR", "签到页中没有找到 formhash，页面结构可能已变化"

    payload = {
        "formhash": formhash,
        "qdxq": os.getenv("QM1000_MOOD", "kx"),
        "qdmode": "1",
        "todaysay": os.getenv("QM1000_MESSAGE", "谢谢生活"),
        "fastreply": "0",
    }
    post_headers = dict(headers)
    post_headers["Content-Type"] = "application/x-www-form-urlencoded"

    try:
        result = session.post(SIGN_URL, data=payload, headers=post_headers, timeout=20)
        result.raise_for_status()
    except requests.RequestException as exc:
        return "NET_ERR", f"提交签到请求失败：{exc}"

    message = response_message(result.text)
    if "签到成功" in message:
        return "SUCCESS", message
    if "已经签到" in message or "签到过" in message:
        return "ALREADY_TODAY", message
    if "登录" in message or "请先登录" in message:
        return "NO_LOGIN", message
    return "FAIL", message or f"接口返回异常（HTTP {result.status_code}）"


def notify(title: str, content: str) -> None:
    if ql_send is not None:
        ql_send(title, content)


def main() -> None:
    accounts = read_accounts()
    if not accounts:
        message = "未配置 QM1000_COOKIES 或 QM1000_COOKIE。"
        print(message)
        notify("阡陌居签到", message)
        return

    results = []
    for index, cookie in enumerate(accounts, start=1):
        status, message = checkin_once(cookie)
        line = f"账号 {index}：{message} [{status}]"
        print(line)
        results.append(line)

    notify("阡陌居每日签到", "\n".join(results))


if __name__ == "__main__":
    main()

