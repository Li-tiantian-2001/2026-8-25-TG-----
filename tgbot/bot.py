"""核心：收藏夹遥控 + 跟播监听 + 任务队列 + 清理/水位。"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
import uuid
from typing import Optional

from telethon import TelegramClient, events

from .ad_filter import is_ad
from .config import Config
from .db import Store
from .tg_fetch import (
    fetch_tg_media,
    is_tg_domain,
    media_allowed,
    media_kind,
    parse_tg_link,
    resolve_peer,
)
from .uploader import Uploader
from .ytdlp_downloader import YtDlpDownloader

log = logging.getLogger("tgbot.bot")

URL_RE = re.compile(r"https?://[^\s<>\"']+")

HELP_TEXT = """\
📖 指令（发到小号自己的【收藏夹 / Saved Messages】）：
!target <@频道 或 t.me/频道 或 -100xxx>   设置目标频道
!follow <t.me链接 或 @用户名>             添加整频道跟播
!unfollow <同>                            取消跟播
!list                                     查看目标频道与跟播列表
!follows                                  一次列出所有跟播频道+冷却状态
!status                                   队列/今日数量/暂停/磁盘
!pause / !resume                          暂停 / 恢复
!dl <视频链接>                            强制按外部链接下载
!help                                     帮助

📹 直接发一条链接也会自动处理：
· t.me/频道/消息ID  → 把那条视频搬过来
· vk.com / x.com / 其他视频链接 → 下载后上传
· 也可以直接把视频转发到收藏夹
"""


class TgBot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        dl = cfg.data["download"]
        self.temp_dir = dl.get("temp_dir", "/tmp/tgbot")
        os.makedirs(self.temp_dir, exist_ok=True)

        self.store = Store(cfg.get("storage", "db_path", default="tgbot.db") or "tgbot.db")
        self.downloader = YtDlpDownloader(
            cookies_file=dl.get("cookies_file", ""),
            outdir=self.temp_dir,
            max_file_mb=int(dl.get("max_file_mb", 3800)),
            fmt=dl.get("format", "best[ext=mp4]/best"),
            retries=int(dl.get("retries", 5)),
            socket_timeout=int(dl.get("socket_timeout", 60)),
        )

        self.client = TelegramClient(cfg.session, cfg.api_id, cfg.api_hash)
        self.uploader = Uploader(self.client, self.store, cfg)

        fl = cfg.data["follow"]
        self.allow_media = list(fl.get("allow_media", ["video"]) or ["video"])
        self.follow_mode = str(fl.get("mode") or "forward").lower()  # forward=真转发 / download=下载重传
        self.follow_interval_sec = float(fl.get("interval_sec", 300) or 300)  # 每源冷却
        self.ad_keywords = list(fl.get("ad_keywords", []) or [])
        self.ad_domains = list(fl.get("ad_domains", []) or [])
        self.block_forwarded = bool(fl.get("block_forwarded", False))
        self.ad_min_hits = int(fl.get("ad_min_hits", 1) or 1)

        self.queue: asyncio.Queue = asyncio.Queue()
        self.status: dict = {}
        self._me_id: Optional[int] = None
        self._follow_peers = set()
        self._source_last_forward: dict = {}  # 源 chat_id -> 上次成功转发时间（冷却用）
        self._seen_groups: dict = {}          # grouped_id -> 入队时间（连体消息去重）
        self._self_texts: dict = {}  # 防"自己的回执又触发处理"的回环

    # ---------- 基础 ----------
    def set_status(self, task_id: str, text: str) -> None:
        self.status[task_id] = text
        if len(self.status) > 200:
            self.status = dict(list(self.status.items())[-100:])
        log.info("[%s] %s", task_id[:8], text)

    async def report(self, text: str) -> None:
        # 先登记文本再发送，避免自己发送的回执被当作指令/链接重新处理
        now = time.time()
        self._self_texts[text] = now
        if len(self._self_texts) > 500:
            self._self_texts = {k: v for k, v in self._self_texts.items() if now - v < 3600}
        try:
            await self.client.send_message("me", text)
        except Exception as e:
            log.warning("回执发送失败: %s", e)

    async def check_paused(self) -> bool:
        return bool(self.store.get_setting("paused", False))

    def _is_ad_msg(self, msg) -> bool:
        """广告甄别：转发标记 / 关键词（命中>=ad_min_hits）/ 域名黑名单。"""
        return is_ad(
            msg, self.ad_keywords, self.ad_domains, self.block_forwarded, self.ad_min_hits
        )

    # ---------- 入队 ----------
    async def enqueue(self, task: dict) -> None:
        task_id = task.get("task_id") or uuid.uuid4().hex
        task["task_id"] = task_id
        src = task.get("source_url") or task.get("url") or ""
        if src and self.store.already_done(src):
            await self.report(f"已在之前搬运过，跳过: {src}")
            return
        if await self.check_paused():
            await self.report("⏸ 当前已暂停，任务已入队（!resume 恢复）")
        self.store.add_record(task_id, src, task.get("msg_id"))
        self.set_status(task_id, "queued")
        await self.queue.put(task)
        log.info("入队 %s: %s", task["kind"], src)

    # ---------- 任务处理 ----------
    async def _worker(self) -> None:
        while True:
            task = await self.queue.get()
            try:
                await self._process(task)
            except Exception as e:
                log.exception("任务 %s 异常", task.get("task_id"))
                self.store.mark_failed(task.get("task_id"), f"crashed: {e}")
            finally:
                self.queue.task_done()

    async def _process(self, task: dict) -> None:
        task_id = task["task_id"]
        kind = task["kind"]
        path: Optional[str] = None
        try:
            if kind == "web":
                url = task["url"]
                self.set_status(task_id, f"下载中: {url}")
                await self.report(f"⬇️ 开始下载: {url}")
                path = await asyncio.to_thread(
                    self.downloader.download,
                    url,
                    task_id,
                    lambda p: self.set_status(task_id, f"下载中 {p:.0f}%"),
                )
            elif kind in ("tg_single", "tg_follow"):
                peer = task["peer"]
                msg_id = task["msg_id"]
                if kind == "tg_follow" and self.follow_mode == "forward":
                    # 真转发：服务端复制，不下载不重传（保留转发自标签 + 原文案）
                    entity = await resolve_peer(self.client, peer)
                    if entity is None:
                        self.store.mark_failed(task_id, "resolve peer failed")
                        await self.report(f"❌ 无法解析来源: {task.get('source_url')}")
                        return
                    self.set_status(task_id, f"转发中: {peer}/{msg_id}")
                    await self.report(
                        f"↪️ 转发中: {task.get('source_url') or f'{peer}/{msg_id}'}"
                    )
                    ids = await self._resolve_forward_ids(
                        entity, msg_id, task.get("grouped_id")
                    )
                    if not ids:
                        self.store.mark_failed(task_id, "no forwardable media")
                        await self.report(
                            f"❌ 不搬（非视频/连体无视频/含广告）: {task.get('source_url')}"
                        )
                        return
                    new_id = await self.uploader.forward(entity, ids, task_id)
                    src = task.get("source_id")
                    if new_id is not None:
                        if src is not None:
                            self._source_last_forward[src] = time.time()
                        await self.report(f"✅ 已转发到目标频道 (消息 {new_id})")
                    else:
                        await self.report("⚠️ 转发被跳过（暂停/达上限/未设目标/失败）")
                    return
                # 下载→重传（无痕）：单条链接 / 直接转发到收藏夹
                self.set_status(task_id, f"从 TG 取媒体: {peer}/{msg_id}")
                await self.report(f"⬇️ 从 TG 取媒体: {peer}/{msg_id}")
                res = await fetch_tg_media(
                    self.client, peer, msg_id, self.temp_dir, self.allow_media
                )
                if res:
                    path = res[0]
            else:
                raise ValueError(f"未知任务类型: {kind}")

            if not path:
                self.store.mark_failed(task_id, "no media / download failed")
                await self.report(f"❌ 取不到媒体: {task.get('source_url') or task.get('url')}")
                return

            size_mb = os.path.getsize(path) / 1024 / 1024
            max_mb = int(self.dl_cfg().get("max_file_mb", 3800))
            if size_mb > max_mb:
                self.store.mark_failed(task_id, f"too large {size_mb:.0f}MB")
                await self.report(f"❌ 文件过大 ({size_mb:.0f}MB，上限 {max_mb}MB): {os.path.basename(path)}")
                return

            self.set_status(task_id, "uploading")
            await self.report(f"⬆️ 上传中 ({size_mb:.0f}MB): {os.path.basename(path)}")
            target_msg = await self.uploader.upload(
                path, task_id, task.get("source_url") or task.get("url") or ""
            )
            if target_msg is None:
                self.set_status(task_id, "skipped/failed")
                await self.report("⚠️ 上传被跳过或失败（!status 查看 / !resume 恢复）")
                return
            self.set_status(task_id, "done")
            await self.report(f"✅ 已发布到频道: {os.path.basename(path)}")
        finally:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            self.downloader.cleanup(task_id)

    def dl_cfg(self) -> dict:
        return self.cfg.data["download"]

    async def _resolve_forward_ids(self, entity, msg_id, grouped_id):
        """确定要转发的消息 id 列表（真转发用）。空列表 = 不搬。

        - 单条：仅视频才搬
        - 连体消息：取整组；整组任一文案含广告 → 屏蔽；组内含视频才整组转发（图片随行）
        """
        if not grouped_id:
            try:
                msg = await self.client.get_messages(entity, ids=msg_id)
            except Exception as e:
                log.warning("取消息 %s/%s 失败: %s", entity, msg_id, e)
                return []
            if msg is None:
                return []
            if media_kind(msg) != "video":
                log.info("单条 %s/%s 非视频，跳过", entity, msg_id)
                return []
            return [msg_id]

        # 连体消息：以该消息为中心取一段，再按 grouped_id 过滤出整组
        try:
            msgs = await self.client.get_messages(
                entity, min_id=msg_id - 15, max_id=msg_id + 15, limit=31
            )
        except Exception as e:
            log.warning("取连体消息 %s/%s 失败: %s", entity, msg_id, e)
            return []
        group = [m for m in msgs if getattr(m, "grouped_id", None) == grouped_id]
        if not group:
            return []
        # 整组文案任一含广告 → 整组屏蔽（连体消息的广告文案可能挂在任一条上）
        for m in group:
            if self._is_ad_msg(m):
                self.store.bump_blocked_today()
                log.info("连体消息 %s 含广告文案，整组屏蔽", grouped_id)
                return []
        # 组内含视频才整组转发（带图一并转发）
        if not any(media_kind(m) == "video" for m in group):
            log.info("连体消息 %s 无视频（纯图），跳过", grouped_id)
            return []
        return [m.id for m in group]

    # ---------- 事件 ----------
    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        msg = event.message
        if self._me_id is None:
            me = await self.client.get_me()
            self._me_id = me.id

        if event.chat_id == self._me_id:  # 收藏夹
            if (msg.text or "") in self._self_texts:  # 我们自己发的回执，跳过
                return
            await self._handle_saved(msg)
            return

        if event.chat_id in self._follow_peers:  # 跟播源
            # 广告甄别：命中关键词/域名/转发标记 → 屏蔽，不搬
            if self._is_ad_msg(msg):
                self.store.bump_blocked_today()
                log.info("屏蔽疑似广告 %s/%s", event.chat_id, msg.id)
                return
            # 只搬视频；连体消息（相册）含视频则整组转发（可带图）
            kind = media_kind(msg)
            grouped_id = getattr(msg, "grouped_id", None)
            if grouped_id is None:
                if kind != "video":
                    log.info("跟播源 %s 非视频（%s）单条，跳过", event.chat_id, kind)
                    return
            else:
                if grouped_id in self._seen_groups:
                    log.info("连体消息 %s 已入队，忽略其余成员", grouped_id)
                    return
                self._seen_groups[grouped_id] = time.time()
                if len(self._seen_groups) > 200:  # 只保留最近记录，防内存增长
                    for k in sorted(self._seen_groups, key=self._seen_groups.get)[:100]:
                        self._seen_groups.pop(k, None)
            # 每个源频道冷却：interval_sec 内只搬 1 条，冷却中的更新直接忽略
            last = self._source_last_forward.get(event.chat_id, 0.0)
            remain = self.follow_interval_sec - (time.time() - last)
            if remain > 0:
                log.info(
                    "源 %s 冷却中（还有 %ds），本次更新忽略",
                    event.chat_id,
                    int(remain),
                )
                return
            try:
                peer = event.chat  # 直接传实体更可靠
            except Exception:
                peer = event.chat_id
            await self.enqueue(
                {
                    "kind": "tg_follow",
                    "peer": peer,
                    "msg_id": msg.id,
                    "grouped_id": grouped_id,
                    "source_id": event.chat_id,
                    "source_url": f"t.me/{event.chat_id}/{msg.id}",
                }
            )

    async def _handle_saved(self, msg) -> None:
        text = (msg.text or "").strip()
        if text.startswith("!"):
            await self._run_command(text)
            return
        if media_allowed(msg, self.allow_media) and not text:  # 直接把视频/图片转发到收藏夹 → 复制
            await self.enqueue(
                {
                    "kind": "tg_single",
                    "peer": self._me_id,
                    "msg_id": msg.id,
                    "source_url": f"saved/{msg.id}",
                }
            )
            return

        urls = URL_RE.findall(text)
        if not urls:
            await self.report("没有识别到链接。用 !help 查看支持的指令与链接格式。")
            return
        for url in urls:
            if is_tg_domain(url):
                parsed = parse_tg_link(url)
                if parsed:
                    peer, mid = parsed
                    await self.enqueue(
                        {"kind": "tg_single", "peer": peer, "msg_id": mid, "source_url": url}
                    )
                else:
                    await self.report(
                        f"{url} 是频道/群组链接（不含消息ID），整频道跟播请用: !follow {url}"
                    )
            else:
                await self.enqueue({"kind": "web", "url": url, "source_url": url})

    # ---------- 指令 ----------
    async def _run_command(self, text: str) -> None:
        parts = text.split()
        cmd = parts[0].lower()
        arg = " ".join(parts[1:]).strip()
        try:
            if cmd == "!target":
                await self._cmd_target(arg)
            elif cmd in ("!follow",):
                await self._cmd_follow(arg, add=True)
            elif cmd == "!unfollow":
                await self._cmd_follow(arg, add=False)
            elif cmd == "!list":
                await self._cmd_list()
            elif cmd == "!follows":
                await self._cmd_follows()
            elif cmd == "!status":
                await self._cmd_status()
            elif cmd == "!pause":
                self.store.set_setting("paused", True)
                self.uploader.paused = True
                await self.report("⏸ 已暂停（新任务只入队不执行）")
            elif cmd == "!resume":
                self.store.set_setting("paused", False)
                self.uploader.paused = False
                await self.report("▶️ 已恢复")
            elif cmd == "!dl":
                if not arg:
                    await self.report("用法: !dl <视频链接>")
                else:
                    await self.enqueue({"kind": "web", "url": arg, "source_url": arg})
            elif cmd == "!help":
                await self.report(HELP_TEXT)
            else:
                await self.report(f"未知指令 {cmd}，用 !help 查看")
        except Exception as e:
            log.exception("指令执行出错")
            await self.report(f"指令出错: {e}")

    async def _cmd_target(self, arg: str) -> None:
        if not arg:
            await self.report("用法: !target <@频道名 或 t.me/频道 或 -100xxx>")
            return
        try:
            entity = await self.client.get_entity(arg)
        except Exception as e:
            await self.report(f"无法解析目标频道 {arg}: {e}")
            return
        self.store.set_setting("target_channel", arg)
        title = getattr(entity, "title", None) or arg
        await self.report(f"🎯 目标频道已设置: {title}")

    async def _cmd_follow(self, ref: str, add: bool) -> None:
        if not ref:
            await self.report("用法: !follow <t.me链接或@用户名>  /  !unfollow <同>")
            return
        clean = (
            ref.replace("https://", "")
            .replace("http://", "")
            .replace("t.me/", "")
            .replace("telegram.me/", "")
            .lstrip("@")
        )
        try:
            entity = await self.client.get_entity(clean)
        except Exception as e:
            await self.report(f"无法解析 {ref}: {e}")
            return
        # 归一化 peer id：entity.id 与 event.chat_id 可能差一个 -100 前缀，两者都加入比对集合
        cid = entity.id
        try:
            cid_marked = await self.client.get_peer_id(entity)
        except Exception:
            cid_marked = cid
        sources = list(self.store.get_setting("follow_sources", []) or [])
        title = getattr(entity, "title", None) or clean
        if add:
            self._follow_peers.add(cid)
            self._follow_peers.add(cid_marked)
            if ref not in sources:
                sources.append(ref)
                self.store.set_setting("follow_sources", sources)
            msg = f"👁 已跟播: {title}"
            # 小号未加入该频道就收不到更新 → 给明确提示
            if getattr(entity, "left", False):
                msg += "\n⚠️ 小号还没【加入】这个频道，加入后才能收到更新并自动搬运（频道设置里点加入）"
            await self.report(msg)
        else:
            self._follow_peers.discard(cid)
            self._follow_peers.discard(cid_marked)
            sources = [s for s in sources if s != ref]
            self.store.set_setting("follow_sources", sources)
            await self.report(f"👁 已取消跟播: {title}")

    async def _cmd_list(self) -> None:
        target = self.store.get_setting("target_channel", None) or self.cfg.get(
            "target_channel", default=""
        )
        sources = self.store.get_setting("follow_sources", []) or []
        lines = [f"🎯 目标频道: {target or '未设置（用 !target）'}"]
        lines.append(f"👁 跟播 {len(sources)} 个:")
        for s in sources:
            lines.append(f"   - {s}")
        lines.append(f"⏸ 暂停: {'是' if await self.check_paused() else '否'}")
        await self.report("\n".join(lines))

    async def _cmd_follows(self) -> None:
        """一次列出所有正在跟播的频道：标题 + 冷却状态。"""
        sources = list(self.store.get_setting("follow_sources", []) or [])
        if not sources:
            await self.report("👁 当前没有在跟播的频道。用 !follow <t.me链接或@用户名> 添加")
            return
        lines = [f"👁 正在跟播 {len(sources)} 个频道（每源 {int(self.follow_interval_sec)}s 一条）："]
        for s in sources:
            clean = (
                s.replace("https://", "")
                .replace("http://", "")
                .replace("t.me/", "")
                .replace("telegram.me/", "")
                .lstrip("@")
            )
            title = clean
            cid_marked = None
            try:
                entity = await self.client.get_entity(clean)
                title = getattr(entity, "title", None) or clean
                cid_marked = await self.client.get_peer_id(entity)
            except Exception:
                title = f"{clean} (无法解析)"
            last = 0.0
            if cid_marked is not None:
                last = self._source_last_forward.get(cid_marked, 0.0)
                if not last:
                    last = self._source_last_forward.get(getattr(entity, "id", None), 0.0)
            remain = self.follow_interval_sec - (time.time() - last)
            state = f"冷却中 {int(remain)}s" if remain > 0 else "可搬"
            lines.append(f"  • {title}  [{state}]")
        await self.report("\n".join(lines))

    async def _cmd_status(self) -> None:
        qsize = self.queue.qsize()
        today = self.store.today_count()
        cap = self.uploader.daily_cap()
        lines = [
            f"📥 队列: {qsize} 个待处理",
            f"📤 今日已发: {today}/{cap}",
            f"🚫 今日屏蔽广告: {self.store.blocked_today()}",
            f"⏸ 暂停: {'是' if await self.check_paused() else '否'}",
        ]
        try:
            usage = shutil.disk_usage(self.temp_dir)
            lines.append(f"💾 磁盘: 已用 {usage.used // 2**30}G / {usage.total // 2**30}G")
        except Exception:
            pass
        if self.status:
            lines.append("最近状态:")
            for k, v in list(self.status.items())[-8:]:
                lines.append(f"   {k[:8]}: {v}")
        await self.report("\n".join(lines))

    # ---------- 跟播加载 ----------
    async def _load_follow(self) -> None:
        for ref in self.store.get_setting("follow_sources", []) or []:
            clean = (
                ref.replace("https://", "")
                .replace("http://", "")
                .replace("t.me/", "")
                .replace("telegram.me/", "")
                .lstrip("@")
            )
            try:
                entity = await self.client.get_entity(clean)
                self._follow_peers.add(entity.id)
                try:
                    self._follow_peers.add(await self.client.get_peer_id(entity))
                except Exception:
                    pass
            except Exception as e:
                log.warning("加载跟播源失败 %s: %s", ref, e)
        if self._follow_peers:
            log.info("已加载 %d 个跟播源 peer id: %s", len(self._follow_peers), sorted(self._follow_peers))

    # ---------- 清理 / 水位 ----------
    async def _cleanup_loop(self) -> None:
        interval = int(self.cfg.get("storage", "cleanup_interval_sec", default=3600) or 3600)
        max_age = int(self.cfg.get("storage", "temp_max_age_sec", default=21600) or 21600)
        watermark = int(self.cfg.get("storage", "disk_watermark_pct", default=80) or 80)
        while True:
            await asyncio.sleep(interval)
            try:
                now = time.time()
                removed = 0
                for name in os.listdir(self.temp_dir):
                    fp = os.path.join(self.temp_dir, name)
                    try:
                        if os.path.isfile(fp) and now - os.path.getmtime(fp) > max_age:
                            os.remove(fp)
                            removed += 1
                    except OSError:
                        pass
                if removed:
                    log.info("清理了 %d 个过期临时文件", removed)
                usage = shutil.disk_usage(self.temp_dir)
                pct = usage.used / usage.total * 100
                if pct > watermark and not self.uploader.paused:
                    self.uploader.paused = True
                    await self.report(f"⚠️ 磁盘使用 {pct:.0f}% 超过水位 {watermark}%，已暂停上传。")
                elif self.uploader.paused and pct < watermark - 10:
                    self.uploader.paused = False
                    await self.report("💾 磁盘水位回落，已恢复上传。")
            except Exception as e:
                log.exception("清理任务出错: %s", e)

    # ---------- 生命周期 ----------
    async def start(self) -> None:
        await self.client.start()
        me = await self.client.get_me()
        self._me_id = me.id
        log.info("已登录: %s", getattr(me, "username", None) or me.id)
        self.client.add_event_handler(self._on_new_message, events.NewMessage())
        await self._load_follow()
        asyncio.create_task(self._worker())
        asyncio.create_task(self._cleanup_loop())
        await self.report("🤖 机器人已启动。发链接/指令到收藏夹即可，!help 查看帮助。")

    async def run_forever(self) -> None:
        await self.start()
        await self.client.run_until_disconnected()
