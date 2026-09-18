#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# cron: 10 8 * * *
# new Env('阡陌居每日任务')

阡陌居（www.1000qm.vip）每日任务脚本：
1. Discuz dsu_paulsign 每日签到；
2. 每日威望红包（任务 ID 1，领取威望 +1）。

青龙环境变量：
    QM1000_COOKIES  多账号 Cookie，一行一个；也支持用 | 分隔
    QM1000_COOKIE   单账号 Cookie（兼容写法）
    QM1000_MESSAGE  签到寄语，可选，默认“谢谢生活”
    QM1000_MOOD     签到心情代码，可选，默认“kx”

Cookie 只从环境变量读取，不要写入脚本或提交到 Git。

PushPlus 通知变量：
    PUSH_PLUS_TOKEN  PushPlus Token（兼容 PUSHPLUS_TOKEN）
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
TASK_PAGE = f"{BASE_URL}/home.php?mod=task"
TASK_APPLY_URL = f"{BASE_URL}/home.php?mod=task&do=apply&id=1"
TASK_DONE_URL = f"{BASE_URL}/home.php?mod=task&item=done"
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
    "NOT_AVAILABLE": "任务不可用",
    "FAIL": "失败",
}


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


def prestige_task_once(cookie: str) -> Tuple[str, str]:
    """申请并领取任务 ID 1 的每日威望红包。"""
    headers = {
        "User-Agent": os.getenv("QM1000_UA", DEFAULT_UA),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": TASK_PAGE,
        "Cookie": cookie,
    }
    session = requests.Session()

    try:
        task_resp = session.get(TASK_PAGE, headers=headers, timeout=20)
        task_resp.raise_for_status()
    except requests.RequestException as exc:
        return "NET_ERR", f"打开任务中心失败：{exc}"

    task_page = task_resp.text
    if not is_logged_in(task_page):
        return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"

    # 只有任务 ID 1 的申请链接存在时才执行，避免误领其他任务。
    apply_pattern = r"home\.php\?mod=task(?:&amp;|&)do=apply(?:&amp;|&)id=1(?:[\"'&<]|$)"
    if not re.search(apply_pattern, task_page, flags=re.IGNORECASE):
        try:
            done_resp = session.get(TASK_DONE_URL, headers=headers, timeout=20)
            done_resp.raise_for_status()
        except requests.RequestException as exc:
            return "NET_ERR", f"查询威望任务状态失败：{exc}"

        done_text = page_text(done_resp.text)
        if "每日威望红包" in done_text and "后可以再次申请" in done_text:
            next_time = re.search(r"(\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})\s*后可以再次申请", done_text)
            suffix = f"，{next_time.group(1)} 后可再次申请" if next_time else ""
            return "ALREADY_TODAY", f"每日威望红包今天已经领取{suffix}"
        return "NOT_AVAILABLE", "未找到可申请的每日威望红包任务"

    try:
        # apply 会由服务器 302 到 draw，requests 跟随重定向完成奖励领取。
        result = session.get(TASK_APPLY_URL, headers=headers, timeout=20, allow_redirects=True)
        result.raise_for_status()
    except requests.RequestException as exc:
        return "NET_ERR", f"申请每日威望红包失败：{exc}"

    message = page_text(result.text)
    if "任务已成功完成" in message:
        return "SUCCESS", "每日威望红包领取成功，威望 +1"
    if "已经申请" in message or "下次申请" in message or "不能申请" in message:
        return "ALREADY_TODAY", "每日威望红包今天已经领取"
    if "登录" in message and ("请先" in message or "需要" in message):
        return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"
    return "FAIL", f"每日威望任务返回异常：{message[:200]}"


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


def chinese_status(status: str) -> str:
    """将内部状态码转换为日志和通知使用的中文。"""
    return STATUS_TEXT.get(status, "未知状态")


def main() -> None:
    accounts = read_accounts()
    if not accounts:
        message = "未配置 QM1000_COOKIES 或 QM1000_COOKIE。"
        print(message)
        notify("阡陌居签到", message)
        return

    results = []
    for index, cookie in enumerate(accounts, start=1):
        sign_status, sign_message = checkin_once(cookie)
        task_status, task_message = prestige_task_once(cookie)
        line = (
            f"账号 {index}：\n"
            f"- 每日签到：{sign_message}【{chinese_status(sign_status)}】\n"
            f"- 每日威望：{task_message}【{chinese_status(task_status)}】"
        )
        print(line)
        results.append(line)

    notify("阡陌居每日任务", "\n\n".join(results))


if __name__ == "__main__":
    main()
