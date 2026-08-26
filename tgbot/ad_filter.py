"""广告甄别：基于关键词 / 域名 / 转发标记，判断源消息是否应被屏蔽。

注意：甄别用源消息的正文/文案做判断，但搬运时仍只搬媒体、不搬文案。
"""
from __future__ import annotations

import re
from typing import List

URL_RE = re.compile(r"https?://[^\s<>\"']+")
DOMAIN_RE = re.compile(r"(?:https?://)?(?:www\.)?([^/\s]+)")


def _text_of(msg) -> str:
    return getattr(msg, "message", None) or ""


def extract_domains(text: str) -> List[str]:
    """从文本里提取链接的域名（去 www 前缀、转小写）。"""
    out: List[str] = []
    for url in URL_RE.findall(text or ""):
        m = DOMAIN_RE.match(url)
        if m:
            out.append(m.group(1).lower().lstrip("."))
    return out


def is_ad(msg, keywords: List[str], domains: List[str], block_forwarded: bool) -> bool:
    """命中任一规则即视为广告：转发标记 / 关键词 / 域名黑名单。"""
    if block_forwarded and getattr(msg, "fwd_from", None):
        return True

    text = _text_of(msg)
    low = text.lower()

    for kw in keywords or []:
        if kw and kw.lower() in low:
            return True

    if domains:
        for d in extract_domains(text):
            if d in domains:
                return True

    return False
