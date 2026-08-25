"""上传器：备用小号串行上传到目标频道，内置冷却、FloodWait 退避、每日上限。"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from .db import Store

log = logging.getLogger("tgbot.upload")


class Uploader:
    def __init__(self, client: TelegramClient, store: Store, cfg):
        self.client = client
        self.store = store
        self.cfg = cfg
        self._lock = asyncio.Lock()
        self._last_upload = 0.0
        self.paused = False  # 由 !pause / 磁盘水位 控制

    # ---------- 状态 ----------
    def today_count(self) -> int:
        return self.store.today_count()

    def daily_cap(self) -> int:
        return int(self.cfg.get("upload", "daily_cap", default=30) or 30)

    def min_interval(self) -> float:
        return float(self.cfg.get("upload", "min_interval_sec", default=60) or 60)

    async def _resolve_target(self) -> Optional[object]:
        target = self.store.get_setting("target_channel", None) or self.cfg.get(
            "target_channel", default=""
        )
        if not target:
            return None
        try:
            return await self.client.get_entity(target)
        except Exception as e:
            log.warning("无法解析目标频道 %r: %s", target, e)
            return None

    async def upload(self, path: str, task_id: str, source_url: str) -> Optional[int]:
        """串行 + 冷却 + 每日上限后上传，返回目标消息 id；被跳过/失败返回 None。"""
        async with self._lock:
            if self.paused:
                log.info("已暂停，跳过 %s", task_id)
                return None
            if self.store.today_count() >= self.daily_cap():
                log.warning("已达今日上限 %d，跳过 %s", self.daily_cap(), task_id)
                return None

            wait = self._last_upload + self.min_interval() - time.time()
            if wait > 0:
                log.info("冷却中，等待 %.0f 秒", wait)
                await asyncio.sleep(wait)

            target = await self._resolve_target()
            if target is None:
                log.warning("未设置目标频道，跳过 %s", task_id)
                return None

            caption = self.cfg.get("upload", "caption_prefix", default="") or ""
            timeout = float(
                self.cfg.get("upload", "upload_timeout_sec", default=1800) or 1800
            )
            size_mb = os.path.getsize(path) / 1024 / 1024

            last_err: Optional[Exception] = None
            for attempt in range(3):
                try:
                    msg = await asyncio.wait_for(
                        self.client.send_file(
                            target,
                            file=path,
                            caption=caption or None,
                            supports_streaming=True,
                        ),
                        timeout=timeout,
                    )
                    self._last_upload = time.time()
                    self.store.bump_today(1)
                    self.store.mark_done(task_id, getattr(msg, "id", None))
                    log.info(
                        "上传成功 %s -> %s (msg %s, %.0fMB)",
                        os.path.basename(path),
                        target,
                        msg.id,
                        size_mb,
                    )
                    return msg.id
                except FloodWaitError as e:
                    log.warning("FloodWait %s 秒，等待后重试", e.seconds)
                    last_err = e
                    await asyncio.sleep(min(e.seconds, 600))
                except asyncio.TimeoutError:
                    log.warning("上传超时 %s", task_id)
                    last_err = TimeoutError("upload timeout")
                    await asyncio.sleep(10 * (attempt + 1))
                except Exception as e:
                    log.exception("上传失败 %s", task_id)
                    last_err = e
                    await asyncio.sleep(10 * (attempt + 1))

            self.store.mark_failed(task_id, f"upload failed: {last_err}")
            return None
