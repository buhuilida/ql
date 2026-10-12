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
import time
from typing import List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

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
TASK_DRAW_URL = f"{BASE_URL}/home.php?mod=task&do=draw&id=1"
TASK_DOING_URL = f"{BASE_URL}/home.php?mod=task&item=doing"
TASK_DONE_URL = f"{BASE_URL}/home.php?mod=task&item=done"
CREDIT_PAGE = f"{BASE_URL}/home.php?mod=spacecp&ac=credit&showcredit=1"
PUSHPLUS_URL = "https://www.pushplus.plus/send"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 阡陌居动态页偶发慢响应：原 timeout=20 太紧，一次卡顿就整轮判为网络异常。
# 改为可调超时 + 自动重试，让瞬时慢响应在本轮内自愈。
TIMEOUT = float(os.getenv("QM1000_TIMEOUT", "60"))
RETRIES = max(1, int(os.getenv("QM1000_RETRIES", "3")))
RETRY_BACKOFF = float(os.getenv("QM1000_RETRY_BACKOFF", "3"))


def http_request(method, url, *, headers=None, session=None, retries=None, **kwargs):
    """带重试的 HTTP 请求，缓解阡陌居偶发慢响应导致的整轮网络异常。

    成功返回 Response（此处不调用 raise_for_status，交由调用方按原逻辑处理）；
    重试耗尽后抛出最后一次 requests.RequestException。
    """
    sess = session or requests
    kwargs.setdefault("timeout", TIMEOUT)
    attempts = retries or RETRIES
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return sess.request(method, url, headers=headers, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(RETRY_BACKOFF * attempt)
    raise last_exc

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
    if match:
        return match.group(1) != "0"
    login_form = re.search(
        r"member\.php\?mod=logging(?:&amp;|&)action=login|\bname\s*=\s*['\"]username['\"]",
        page,
        flags=re.IGNORECASE,
    )
    return login_form is None


def authenticated_session(cookie: str) -> requests.Session:
    """将 Cookie 放入 CookieJar，确保 Discuz 重定向后仍保持登录态。"""
    session = requests.Session()
    for part in cookie.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            session.cookies.set(name, value)
    return session


def extract_task_action_url(page: str, action: str, task_id: int = 1) -> Optional[str]:
    """从任务页提取指定任务的操作链接，不依赖查询参数顺序。"""
    for raw_url in re.findall(r"\bhref\s*=\s*['\"]([^'\"]+)['\"]", page, flags=re.IGNORECASE):
        url = urljoin(f"{BASE_URL}/", html.unescape(raw_url))
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if (
            parsed.path.endswith("/home.php")
            and query.get("mod") == ["task"]
            and query.get("do") == [action]
            and query.get("id") == [str(task_id)]
        ):
            return url
    return None


def completed_task_message(page: str) -> Optional[str]:
    """识别已领取任务及下次可申请时间。"""
    text = page_text(page)
    if "每日威望红包" not in text or "后可以再次申请" not in text:
        return None
    next_time = re.search(r"(\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})\s*后可以再次申请", text)
    suffix = f"，{next_time.group(1)} 后可再次申请" if next_time else ""
    return f"每日威望红包今天已经领取{suffix}"


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
        if name in ("铜币", "威望"):
            balances.append((name, value))
    return balances


def query_credit_balances(cookie: str) -> Tuple[bool, str]:
    headers = {
        "User-Agent": os.getenv("QM1000_UA", DEFAULT_UA),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": SIGN_PAGE,
        "Cookie": cookie,
    }
    try:
        response = http_request("GET", CREDIT_PAGE, headers=headers)
        response.raise_for_status()
    except requests.RequestException as exc:
        return False, f"当前积分查询失败：{exc}"

    if not is_logged_in(response.text):
        return False, "当前积分查询失败：Cookie 已失效或未登录"
    balances = extract_credit_balances(response.text)
    if not balances:
        return False, "当前积分查询失败：积分页结构可能已变化"
    return True, "当前任务货币：" + "，".join(f"{name} {value}" for name, value in balances)


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
        page_resp = http_request("GET", SIGN_PAGE, headers=headers, session=session)
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
        result = http_request("POST", SIGN_URL, headers=post_headers, session=session, data=payload)
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
    }
    session = authenticated_session(cookie)

    try:
        task_resp = http_request("GET", TASK_PAGE, headers=headers, session=session)
        task_resp.raise_for_status()
    except requests.RequestException as exc:
        return "NET_ERR", f"打开任务中心失败：{exc}"

    task_page = task_resp.text
    if not is_logged_in(task_page):
        return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"

    apply_url = extract_task_action_url(task_page, "apply")
    applied_now = apply_url is not None
    if apply_url:
        try:
            apply_resp = http_request("GET", apply_url, headers=headers, session=session, allow_redirects=True)
            apply_resp.raise_for_status()
        except requests.RequestException as exc:
            return "NET_ERR", f"申请每日威望红包失败：{exc}"

        if not is_logged_in(apply_resp.text):
            return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"
    else:
        apply_resp = None

    try:
        doing_resp = http_request("GET", TASK_DOING_URL, headers=headers, session=session)
        doing_resp.raise_for_status()
    except requests.RequestException as exc:
        return "NET_ERR", f"查询进行中的威望任务失败：{exc}"

    if not is_logged_in(doing_resp.text):
        return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"

    draw_url = extract_task_action_url(doing_resp.text, "draw")
    if not draw_url:
        draw_url = extract_task_action_url(task_page, "draw")
    if not draw_url and apply_resp is not None:
        draw_url = extract_task_action_url(apply_resp.text, "draw")

    # 部分模板不输出领取链接，但 Discuz 的领取端点仍固定为 do=draw&id=1。
    if not draw_url and applied_now:
        draw_url = TASK_DRAW_URL

    if draw_url:
        draw_headers = dict(headers)
        draw_headers["Referer"] = TASK_DOING_URL
        try:
            result = http_request("GET", draw_url, headers=draw_headers, session=session, allow_redirects=True)
            result.raise_for_status()
        except requests.RequestException as exc:
            return "NET_ERR", f"领取每日威望红包失败：{exc}"

        if not is_logged_in(result.text):
            return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"

        message = page_text(result.text)
        success_markers = ("任务已成功完成", "任务奖励已领取", "恭喜您完成任务")
        if any(marker in message for marker in success_markers):
            return "SUCCESS", "每日威望红包领取成功，威望 +1"

    try:
        done_resp = http_request("GET", TASK_DONE_URL, headers=headers, session=session)
        done_resp.raise_for_status()
    except requests.RequestException as exc:
        return "NET_ERR", f"核验威望任务状态失败：{exc}"

    if not is_logged_in(done_resp.text):
        return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"

    done_message = completed_task_message(done_resp.text)
    if done_message:
        if applied_now or draw_url:
            return "SUCCESS", "每日威望红包领取成功，威望 +1"
        return "ALREADY_TODAY", done_message

    if draw_url:
        message = page_text(result.text)
        return "FAIL", f"领取威望奖励后状态核验失败：{message[:200] or '响应内容为空'}"
    return "NOT_AVAILABLE", "未找到可申请或可领取的每日威望红包任务"


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
            response = http_request("POST", PUSHPLUS_URL, json=payload)
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
        _, credit_message = query_credit_balances(cookie)
        line = (
            f"账号 {index}：\n"
            f"- 每日签到：{sign_message}【{chinese_status(sign_status)}】\n"
            f"- 每日威望：{task_message}【{chinese_status(task_status)}】\n"
            f"- {credit_message}"
        )
        print(line)
        results.append(line)

    notify("阡陌居每日任务", "\n\n".join(results))


if __name__ == "__main__":
    main()
