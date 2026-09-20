#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
# cron: 15 8 * * *
# new Env('搜书吧每日任务')

搜书吧每日任务：
1. 访问地址发布页并解析最新地址；
2. 使用 Cookie 访问主站，确认登录态；
3. 每天发表一条记录，默认内容为“记录打卡，爱上搜书吧”。

青龙环境变量：
    SOUSHUBA_COOKIES  多账号 Cookie，一行一个；也支持用 | 分隔
    SOUSHUBA_COOKIE   单账号 Cookie（兼容写法）
    SOUSHUBA_MESSAGE  记录内容，可选
    SOUSHUBA_PUBLISH_URL 地址发布页，可选，默认取抓包中的发布页
    SOUSHUBA_UA       User-Agent，可选
    SOUSHUBA_VERIFY_SSL 是否严格校验主站证书，1 开启；默认 0（该站证书链不完整）

PushPlus：PUSH_PLUS_TOKEN（兼容 PUSHPLUS_TOKEN），可选 PUSH_PLUS_TOPIC。

Cookie 只从环境变量读取，不会写入脚本或日志。
"""

import html
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
import urllib3

try:
    from notify import send as ql_send
except ImportError:
    ql_send = None


PUBLISH_URL = "https://upyt.fv1e5dg5eas.com/"
DOING_URL_PATH = "/home.php?mod=space&do=doing&view=me&from=space"
DOING_POST_PATH = "/home.php?mod=spacecp&ac=doing&view=me"
CREDIT_URL_PATH = "/home.php?mod=spacecp&ac=credit&showcredit=1"
DEFAULT_MESSAGE = "记录打卡，爱上搜书吧"
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
    raw = os.getenv("SOUSHUBA_COOKIES") or os.getenv("SOUSHUBA_COOKIE", "")
    return [item.strip() for item in raw.replace("|", "\n").splitlines() if item.strip()]


def make_session(cookie: str) -> requests.Session:
    session = requests.Session()
    for part in cookie.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            session.cookies.set(name, value)
    session.headers.update({"User-Agent": os.getenv("SOUSHUBA_UA", DEFAULT_UA)})
    return session


def response_text(response: requests.Response) -> str:
    """按响应声明解码；搜书吧主站页面使用 GBK。"""
    content_type = response.headers.get("Content-Type", "")
    charset_match = re.search(r"charset\s*=\s*['\"]?([\w-]+)", content_type, re.I)
    charset = charset_match.group(1) if charset_match else ""
    if charset.lower() in {"gbk", "gb2312", "gb18030"}:
        return response.content.decode("gb18030", errors="replace")
    if charset:
        return response.content.decode(charset, errors="replace")
    encoding = response.apparent_encoding or "utf-8"
    return response.content.decode(encoding, errors="replace")


def page_text(content: str) -> str:
    content = re.sub(r"<script\b[^>]*>.*?</script>", " ", content, flags=re.I | re.S)
    content = re.sub(r"<style\b[^>]*>.*?</style>", " ", content, flags=re.I | re.S)
    content = re.sub(r"<[^>]+>", " ", content)
    return re.sub(r"\s+", " ", html.unescape(content)).strip()


def extract_hidden_fields(content: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for tag in re.findall(r"<input\b[^>]*>", content, flags=re.I):
        name_match = re.search(r"\bname\s*=\s*['\"]([^'\"]+)['\"]", tag, re.I)
        if not name_match:
            continue
        value_match = re.search(r"\bvalue\s*=\s*['\"]([^'\"]*)['\"]", tag, re.I)
        fields[name_match.group(1)] = html.unescape(value_match.group(1)) if value_match else ""
    return fields


def extract_formhash(content: str) -> Optional[str]:
    fields = extract_hidden_fields(content)
    return fields.get("formhash") or None


def is_logged_in(content: str) -> bool:
    if re.search(r"member\.php\?mod=logging(?:&amp;|&)action=logout", content, re.I):
        return True
    uid = re.search(r"\bdiscuz_uid\s*=\s*['\"](\d+)['\"]", content, re.I)
    if uid:
        return uid.group(1) != "0"
    return not re.search(
        r"member\.php\?mod=logging(?:&amp;|&)action=login|\bname\s*=\s*['\"]username['\"]",
        content,
        re.I,
    )


def extract_latest_urls(content: str, base_url: str) -> List[str]:
    """解析地址发布页的全部候选地址，保留发布页提供的顺序。"""
    candidates = re.findall(
        r"(?:urls\s*\[\s*\d+\s*\]|url\s*:)\s*=\s*['\"]([^'\"]+)['\"]",
        content,
        re.I,
    )
    if not candidates:
        candidates = re.findall(
            r"(?:url|href)\s*=\s*['\"]([^'\"]+)['\"]|url=([^;\"']+)",
            content,
            re.I,
        )
        candidates = [a or b for a, b in candidates]
    urls = []
    for candidate in candidates:
        candidate = html.unescape(candidate.strip())
        if candidate.startswith("javascript:") or candidate.startswith("#"):
            continue
        resolved = urljoin(base_url, candidate)
        if resolved not in urls:
            urls.append(resolved)
    return urls


def extract_latest_url(content: str, base_url: str) -> Optional[str]:
    """兼容旧调用：返回发布页上的第一个最新地址候选。"""
    urls = extract_latest_urls(content, base_url)
    return urls[0] if urls else None


def extract_named_links(content: str, base_url: str, label: str = "最新地址") -> List[str]:
    """从中转页提取带有指定文字的链接，例如“最新地址”。"""
    links = []
    for attrs, inner in re.findall(r"<a\b([^>]*)>(.*?)</a\s*>", content, re.I | re.S):
        if label not in page_text(inner):
            continue
        match = re.search(r"\bhref\s*=\s*['\"]([^'\"]+)['\"]", attrs, re.I)
        if not match:
            continue
        target = urljoin(base_url, html.unescape(match.group(1).strip()))
        if target not in links:
            links.append(target)
    return links


def follow_meta_refresh(session: requests.Session, response: requests.Response) -> requests.Response:
    """requests 不执行 HTML meta refresh，这里补上发布页的两级跳转。"""
    current = response
    for _ in range(4):
        content = response_text(current)
        match = re.search(
            r"<meta\b[^>]*http-equiv\s*=\s*['\"]?refresh['\"]?[^>]*content\s*=\s*['\"]?[^>]*?url\s*=\s*([^\"' >]+)",
            content,
            re.I,
        )
        if not match:
            match = re.search(r"location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", content, re.I)
        if not match:
            return current
        target = urljoin(current.url, html.unescape(match.group(1)))
        try:
            current = session.get(
                target,
                headers={"Referer": current.url},
                timeout=20,
                allow_redirects=True,
            )
        except requests.RequestException:
            return current
    return current


def resolve_main_url(session: requests.Session) -> Optional[str]:
    publish_url = os.getenv("SOUSHUBA_PUBLISH_URL", PUBLISH_URL).strip()
    try:
        publish = session.get(publish_url, timeout=20, allow_redirects=True)
        publish.raise_for_status()
        # 发布页首页通常只通过 meta refresh 跳到 /sou/go.html，最新地址数组在后者中。
        publish = follow_meta_refresh(session, publish)
        content = response_text(publish)
        targets = extract_latest_urls(content, publish.url)
        if not targets:
            return None
        visited = set()
        pending = list(targets)
        for _ in range(12):
            if not pending:
                break
            target = pending.pop(0)
            if target in visited:
                continue
            visited.add(target)
            for _attempt in range(3):
                try:
                    target_resp = session.get(
                        target,
                        headers={"Referer": publish.url},
                        timeout=20,
                        allow_redirects=True,
                    )
                    target_resp = follow_meta_refresh(session, target_resp)
                    target_text = response_text(target_resp)
                    if re.search(r"Powered by Discuz|discuz_uid|name\s*=\s*['\"]formhash['\"]", target_text, re.I):
                        parsed = urlparse(target_resp.url)
                        return f"{parsed.scheme}://{parsed.netloc}/"
                    named_links = extract_named_links(target_text, target_resp.url)
                    if named_links:
                        # 中转页明确标记的“最新地址”就是主站入口，无需依赖主站证书来完成地址解析。
                        parsed = urlparse(named_links[0])
                        return f"{parsed.scheme}://{parsed.netloc}/"
                except requests.RequestException:
                    continue
    except requests.RequestException:
        return None
    return None


def extract_credit(content: str) -> Optional[str]:
    match = re.search(
        r"<em\b[^>]*>\s*银币\s*[:：]\s*</em>\s*([+-]?\d[\d,]*)",
        content,
        re.I | re.S,
    )
    if match:
        return match.group(1).replace(",", "")
    visible = page_text(content)
    match = re.search(r"银币\s*[:：]\s*([+-]?\d[\d,]*)", visible)
    return match.group(1).replace(",", "") if match else None


def has_today_record(content: str, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    today = rf"{now.year}-0?{now.month}-0?{now.day}"
    for block in re.findall(r"<dd\b[^>]*class\s*=\s*['\"][^'\"]*xg1[^'\"]*['\"][^>]*>(.*?)</dd>", content, re.I | re.S):
        if re.search(rf"\b{today}\b", page_text(block)):
            return True
    return False


def run_account(cookie: str) -> Tuple[str, str]:
    # 地址发现过程不携带账号 Cookie，避免凭据发送给发布页或中转站。
    discovery_session = make_session("")
    main_url = resolve_main_url(discovery_session)
    if not main_url:
        return "PARSE_ERR", "发布页未解析到可用的搜书吧主站地址"
    session = make_session(cookie)
    verify_ssl = os.getenv("SOUSHUBA_VERIFY_SSL", "0").strip().lower() in {"1", "true", "yes", "on"}
    session.verify = verify_ssl
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    headers = {"Referer": main_url}
    try:
        home = session.get(main_url, headers=headers, timeout=20)
        home.raise_for_status()
    except requests.RequestException as exc:
        return "NET_ERR", f"打开搜书吧主站失败：{exc}"
    home_content = response_text(home)
    if not is_logged_in(home_content):
        return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"

    credit_before = extract_credit(home_content)
    doing_url = urljoin(main_url, DOING_URL_PATH)
    try:
        doing = session.get(doing_url, headers=headers, timeout=20)
        doing.raise_for_status()
    except requests.RequestException as exc:
        return "NET_ERR", f"打开记录页面失败：{exc}"
    doing_content = response_text(doing)
    if not is_logged_in(doing_content):
        return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"
    if has_today_record(doing_content):
        return "ALREADY_TODAY", f"今天已经发表记录，当前银币 {credit_before or '未知'}"

    formhash = extract_formhash(doing_content)
    if not formhash:
        return "PARSE_ERR", "记录页面中没有找到 formhash，页面结构可能已变化"
    payload = {
        "message": os.getenv("SOUSHUBA_MESSAGE", DEFAULT_MESSAGE),
        "add": "",
        "addsubmit": "true",
        "refer": doing_url,
        "topicid": "",
        "formhash": formhash,
    }
    post_url = urljoin(main_url, DOING_POST_PATH)
    post_headers = {"Referer": doing_url, "Content-Type": "application/x-www-form-urlencoded"}
    try:
        result = session.post(post_url, data=payload, headers=post_headers, timeout=20, allow_redirects=True)
        result.raise_for_status()
    except requests.RequestException as exc:
        return "NET_ERR", f"发表记录失败：{exc}"
    result_content = response_text(result)
    if not is_logged_in(result_content):
        return "NO_LOGIN", "Cookie 已失效或未登录，请重新获取 Cookie"
    if payload["message"] not in result_content and result.url != doing_url:
        return "FAIL", "发表记录请求已返回，但未在结果页确认记录内容"
    credit_after = extract_credit(result_content)
    credit_message = f"，当前银币 {credit_after}" if credit_after else ""
    return "SUCCESS", f"记录发表成功{credit_message}"


def notify(title: str, content: str) -> bool:
    token = (os.getenv("PUSH_PLUS_TOKEN") or os.getenv("PUSHPLUS_TOKEN", "")).strip()
    if token:
        payload = {"token": token, "title": title, "content": content, "template": "txt"}
        topic = os.getenv("PUSH_PLUS_TOPIC", "").strip()
        if topic:
            payload["topic"] = topic
        try:
            response = requests.post("https://www.pushplus.plus/send", json=payload, timeout=20)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200:
                print("PushPlus 通知发送成功。")
                return True
            print(f"PushPlus 通知发送失败：{data.get('msg') or data}")
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


def main() -> None:
    accounts = read_accounts()
    if not accounts:
        message = "未配置 SOUSHUBA_COOKIES 或 SOUSHUBA_COOKIE。"
        print(message)
        notify("搜书吧每日任务", message)
        return
    results = []
    for index, cookie in enumerate(accounts, start=1):
        status, message = run_account(cookie)
        line = f"账号 {index}：{message}【{STATUS_TEXT.get(status, status)}】"
        print(line)
        results.append(line)
    notify("搜书吧每日任务", "\n".join(results))


if __name__ == "__main__":
    main()
