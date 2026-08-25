"""TG 取件：解析 t.me 链接、Telethon 取频道/群消息的视频。"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

from telethon import TelegramClient

log = logging.getLogger("tgbot.tgfetch")

# t.me/username/123 | t.me/c/123456789/123 | telegram.me/...
TG_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t(?:elegram)?)\.me/"
    r"(?:(?P<id>c/\d+)|(?P<name>[\w._+-]+))"
    r"/(?P<msg>\d+)"
)
TG_DOMAIN_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:t|telegram)\.me(/|\?)")


def is_tg_domain(url: str) -> bool:
    return bool(TG_DOMAIN_RE.search(url.strip()))


def parse_tg_link(url: str) -> Optional[Tuple[object, int]]:
    """从 t.me 链接提取 (peer, message_id)。username 或频道数字 id。"""
    m = TG_LINK_RE.search(url.strip())
    if not m:
        return None
    msg_id = int(m.group("msg"))
    if m.group("id"):
        peer = int(m.group("id")[2:])  # 去掉 'c/'
    else:
        peer = m.group("name")
    return peer, msg_id


async def resolve_peer(client: TelegramClient, peer) -> Optional[object]:
    """解析 peer 为实体。已传实体直接返回；数字 id 尝试各种写法。"""
    if hasattr(peer, "id"):  # 已经是实体/InputPeer
        return peer
    if isinstance(peer, int):
        candidates = [peer, int(f"-100{peer}") if peer > 0 else peer, str(peer)]
        for cand in candidates:
            try:
                return await client.get_entity(cand)
            except Exception:
                continue
        # 兜底：取反、字符串形式
        for cand in (int(f"-{peer}"), f"-100{peer}"):
            try:
                return await client.get_entity(cand)
            except Exception:
                continue
        return None
    try:
        return await client.get_entity(peer)
    except Exception as e:
        log.warning("无法解析 peer %r: %s", peer, e)
        return None


def _has_video(msg) -> bool:
    if msg.video:
        return True
    doc = getattr(msg, "document", None)
    if doc and (getattr(doc, "mime_type", "") or "").startswith("video/"):
        return True
    return False


async def fetch_tg_video(
    client: TelegramClient, peer, msg_id: int, outdir: str
) -> Optional[Tuple[str, str]]:
    """下载 TG 消息中的视频到 outdir，返回 (路径, 原帖文字)；无视频返回 None。"""
    entity = await resolve_peer(client, peer)
    if entity is None:
        return None
    try:
        msg = await client.get_messages(entity, ids=msg_id)
    except Exception as e:
        log.warning("get_messages %s/%s 失败: %s", peer, msg_id, e)
        return None
    if msg is None:
        log.warning("消息不存在 %s/%s", peer, msg_id)
        return None

    if not _has_video(msg):
        log.info("消息 %s/%s 没有视频，跳过", peer, msg_id)
        return None

    try:
        path = await client.download_media(msg, file=outdir)
    except Exception as e:
        log.warning("download_media %s/%s 失败: %s", peer, msg_id, e)
        return None
    if not path:
        return None
    return str(path), (getattr(msg, "message", None) or "")
