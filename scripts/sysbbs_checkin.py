#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# cron: 20 8 * * *
# new Env('源社区每日签到')

源社区（pc.sysbbs.com）Discuz k_misign 每日签到脚本。

青龙环境变量：
    SYSBBS_COOKIES  多账号 Cookie，一行一个；也支持用 | 分隔
    SYSBBS_COOKIE   单账号 Cookie（兼容写法）

Cookie 只从环境变量读取，不要写入脚本或提交到 Git。
"""

import html
import os
import re
from typing import List, Optional, Tuple
from urllib.parse import unquote

import requests

try:
    from notify import send as ql_send
except ImportError:
    ql_send = None


BASE_URL = "https://pc.sysbbs.com"
REFERER_URL = f"{BASE_URL}/forum-2-1.html"
SIGN_PAGE = f"{BASE_URL}/k_misign-sign.html"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
STATUS_TEXT = {
    "SUCCESS": "成功",
    "ALREADY_TODAY": "今日已完成",
    "NO_LOGIN": "登录失效",
    "NET_ERR": "网络异常",
    "PARSE_ERR": "页面解析失败",
    "FAIL": "失败",
}


def read_accounts() -> List[str]:
    raw = os.getenv("SYSBBS_COOKIES") or os.getenv("SYSBBS_COOKIE", "")
    return [item.strip() for item in raw.replace("|", "\n").splitlines() if item.strip()]


def page_text(content: str) -> str:
    content = re.sub(r"<script\b[^>]*>.*?</script>", " ", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<style\b[^>]*>.*?</style>", " ", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<[^>]+>", " ", content)
    return re.sub(r"\s+", " ", html.unescape(content)).strip()


def extract_formhash(content: str) -> Optional[str]:
    """兼容签到链接、隐藏表单和 Discuz JavaScript 变量。"""
    decoded = html.unescape(unquote(content))
    patterns = (
        r"[?&]formhash=([A-Za-z0-9]{6,32})",
        r"\bname\s*=\s*['\"]formhash['\"][^>]*\bvalue\s*=\s*['\"]([^'\"]+)",
        r"\bvalue\s*=\s*['\"]([^'\"]+)['\"][^>]*\bname\s*=\s*['\"]formhash['\"]",
        r"\bformhash\s*=\s*['\"]([A-Za-z0-9]{6,32})['\"]",
    )
    for pattern in patterns:
        match = re.search(pattern, decoded, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def is_logged_in(content: str) -> bool:
    match = re.search(r"\bdiscuz_uid\s*=\s*['\"](\d+)['\"]", content, flags=re.IGNORECASE)
    if match:
        return match.group(1) != "0"
    text = page_text(content)
    return not ("立即登录" in text or "请先登录" in text)


def get_formhash(session: requests.Session, headers: dict) -> Tuple[Optional[str], Optional[str]]:
    """按优先级访问常见页面，返回动态 formhash 和错误说明。"""
    last_error = None
    for url in (REFERER_URL, SIGN_PAGE, f"{BASE_URL}/forum.php"):
        try:
            response = session.get(url, headers=headers, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            last_error = str(exc)
            continue

        if not is_logged_in(response.text):
            return None, "Cookie 已失效或未登录，请重新获取 Cookie"
        formhash = extract_formhash(response.text)
        if formhash:
            return formhash, None

    if last_error:
        return None, f"访问论坛页面失败：{last_error}"
    return None, "页面中没有找到动态 formhash，论坛页面结构可能已变化"


def checkin_once(cookie: str) -> Tuple[str, str]:
    headers = {
        "User-Agent": os.getenv("SYSBBS_UA", DEFAULT_UA),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": REFERER_URL,
        "Cookie": cookie,
    }
    session = requests.Session()
    formhash, error = get_formhash(session, headers)
    if not formhash:
        if error and "Cookie" in error:
            return "NO_LOGIN", error
        if error and "访问论坛页面失败" in error:
            return "NET_ERR", error
        return "PARSE_ERR", error or "无法获取动态 formhash"

    params = {
        "operation": "qiandao",
        "format": "button",
        "formhash": formhash,
        "inajax": "1",
        "ajaxtarget": "midaben_sign",
    }
    ajax_headers = dict(headers)
    ajax_headers.update({"Accept": "*/*", "X-Requested-With": "XMLHttpRequest"})

    try:
        response = session.get(SIGN_PAGE, params=params, headers=ajax_headers, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        return "NET_ERR", f"提交签到请求失败：{exc}"

    text = page_text(response.text)
    if "签到成功" in text:
        reward = re.search(r"获得(?:随机)?奖励\s*([^。！!<]+)", text)
        total = re.search(r"已累计签到\s*(\d+)\s*天", text)
        details = []
        if reward:
            details.append(reward.group(1).strip())
        if total:
            details.append(f"累计签到 {total.group(1)} 天")
        suffix = "，" + "，".join(details) if details else ""
        return "SUCCESS", f"签到成功{suffix}"
    if "已签到" in text or "已经签到" in text or "今日已签" in text or "重复签到" in text:
        return "ALREADY_TODAY", "今天已经签到过了"
    if "请先登录" in text or "需要先登录" in text or "登录后" in text:
        return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"
    return "FAIL", f"签到接口返回异常：{text[:200] or '响应内容为空'}"


def chinese_status(status: str) -> str:
    return STATUS_TEXT.get(status, "未知状态")


def notify(title: str, content: str) -> None:
    if ql_send is not None:
        ql_send(title, content)


def main() -> None:
    accounts = read_accounts()
    if not accounts:
        message = "未配置 SYSBBS_COOKIES 或 SYSBBS_COOKIE。"
        print(message)
        notify("源社区每日签到", message)
        return

    results = []
    for index, cookie in enumerate(accounts, start=1):
        status, message = checkin_once(cookie)
        line = f"账号 {index}：{message}【{chinese_status(status)}】"
        print(line)
        results.append(line)

    notify("源社区每日签到", "\n".join(results))


if __name__ == "__main__":
    main()

