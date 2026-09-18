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

PushPlus 通知变量：
    PUSH_PLUS_TOKEN  PushPlus Token（兼容 PUSHPLUS_TOKEN）
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
CREDIT_PAGE = f"{BASE_URL}/home.php?mod=spacecp&ac=credit"
PUSHPLUS_URL = "https://www.pushplus.plus/send"
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


def extract_credit_balances(content: str) -> List[Tuple[str, str]]:
    """解析 Discuz 积分页 creditl 区域中的各项当前余额。"""
    match = re.search(
        r"<ul\b[^>]*class\s*=\s*['\"][^'\"]*\bcreditl\b[^'\"]*['\"][^>]*>(.*?)</ul>",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []

    balances = []
    for item in re.findall(r"<li\b[^>]*>(.*?)</li>", match.group(1), flags=re.IGNORECASE | re.DOTALL):
        value_match = re.search(
            r"<em\b[^>]*>\s*([^:<]+?)\s*[:：]\s*</em>\s*([+-]?[\d,]+(?:\.\d+)?)",
            item,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not value_match:
            continue
        name = page_text(value_match.group(1))
        value = value_match.group(2).replace(",", "")
        if name == "星币":
            balances.append((name, value))
    return balances


def query_credit_balances(cookie: str) -> Tuple[bool, str]:
    headers = {
        "User-Agent": os.getenv("SYSBBS_UA", DEFAULT_UA),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": REFERER_URL,
        "Cookie": cookie,
    }
    try:
        response = requests.get(CREDIT_PAGE, headers=headers, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        return False, f"当前积分查询失败：{exc}"

    if not is_logged_in(response.text):
        return False, "当前积分查询失败：Cookie 已失效或未登录"
    balances = extract_credit_balances(response.text)
    if not balances:
        return False, "当前积分查询失败：积分页结构可能已变化"
    return True, "当前签到货币：" + "，".join(f"{name} {value}" for name, value in balances)


def has_signed_marker(content: str) -> bool:
    """识别 k_misign 在页面和按钮中使用的已签到标志。"""
    text = page_text(content)
    if any(marker in text for marker in ("已签到", "已经签到", "今日已签", "签到过了", "重复签到")):
        return True
    return bool(
        re.search(
            r"class\s*=\s*['\"][^'\"]*(?:\bJD_sign\b[^'\"]*\bvisted\b|\bbtnvisted\b)[^'\"]*['\"]",
            content,
            flags=re.IGNORECASE,
        )
    )


def verify_signed(session: requests.Session, headers: dict) -> Optional[bool]:
    """签到接口响应不明确时，通过页面按钮状态二次核验。"""
    checked = False
    for url in (SIGN_PAGE, REFERER_URL):
        try:
            response = session.get(url, headers=headers, timeout=20)
            response.raise_for_status()
        except requests.RequestException:
            continue
        checked = True
        if has_signed_marker(response.text):
            return True
    return False if checked else None


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
        signed = verify_signed(session, headers)
        if signed is True:
            return "ALREADY_TODAY", "今天已经签到过了"
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
    if has_signed_marker(response.text):
        return "ALREADY_TODAY", "今天已经签到过了"
    if "请先登录" in text or "需要先登录" in text or "登录后" in text:
        return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"
    signed = verify_signed(session, headers)
    if signed is True:
        return "ALREADY_TODAY", "今天已经签到过了"
    if signed is None:
        return "NET_ERR", "签到接口未返回结果，且无法联网核验签到状态"
    return "FAIL", f"签到接口返回异常：{text[:200] or '响应内容为空'}"


def chinese_status(status: str) -> str:
    return STATUS_TEXT.get(status, "未知状态")


def notify(title: str, content: str) -> bool:
    """优先直接发送 PushPlus；未配置 Token 时回退青龙 notify.py。"""
    token = (os.getenv("PUSH_PLUS_TOKEN") or os.getenv("PUSHPLUS_TOKEN", "")).strip()
    if token:
        payload = {
            "token": token,
            "title": title,
            "content": content,
            "template": "txt",
        }
        topic = os.getenv("PUSH_PLUS_TOPIC", "").strip()
        if topic:
            payload["topic"] = topic
        try:
            response = requests.post(PUSHPLUS_URL, json=payload, timeout=20)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200:
                print("PushPlus 通知发送成功。")
                return True
            print(f"PushPlus 通知发送失败：{data.get('msg') or data.get('message') or data}")
        except (requests.RequestException, ValueError) as exc:
            print(f"PushPlus 通知发送失败：{exc}")
        return False

    if ql_send is not None:
        try:
            ql_send(title, content)
            print("未配置 PUSH_PLUS_TOKEN，已调用青龙通知模块。")
            return True
        except Exception as exc:
            print(f"青龙通知模块调用失败：{exc}")
            return False

    print("未配置 PUSH_PLUS_TOKEN，且未找到青龙通知模块，未发送通知。")
    return False


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
        _, credit_message = query_credit_balances(cookie)
        line = (
            f"账号 {index}：\n"
            f"- 每日签到：{message}【{chinese_status(status)}】\n"
            f"- {credit_message}"
        )
        print(line)
        results.append(line)

    notify("源社区每日签到", "\n".join(results))


if __name__ == "__main__":
    main()
