"""yt-dlp 下载器：外部链接（VK / X / YouTube / TikTok ...）下载到临时目录。"""
from __future__ import annotations

import glob
import logging
import os
from typing import Callable, Optional

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

log = logging.getLogger("tgbot.download")

_VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts", ".mpeg", ".mpg", ".3gp")


def _is_video_file(path: str) -> bool:
    return path.lower().endswith(_VIDEO_EXTS)


class YtDlpDownloader:
    def __init__(
        self,
        cookies_file: str,
        outdir: str,
        max_file_mb: int,
        fmt: str,
        retries: int,
        socket_timeout: int,
    ):
        self.cookies_file = cookies_file
        self.outdir = outdir
        self.max_file_mb = max_file_mb
        self.fmt = fmt
        self.retries = retries
        self.socket_timeout = socket_timeout

    def _opts(self, task_id: str, on_progress: Optional[Callable[[float], None]]) -> dict:
        opts = {
            "outtmpl": os.path.join(self.outdir, f"{task_id}.%(ext)s"),
            "format": self.fmt,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "cachedir": False,
            "quiet": True,
            "no_warnings": True,
            "retries": self.retries,
            "fragment_retries": self.retries,
            "socket_timeout": self.socket_timeout,
            "noprogress": True,
        }
        if self.cookies_file and os.path.exists(self.cookies_file):
            opts["cookiefile"] = self.cookies_file
        if on_progress:
            def hook(d: dict) -> None:
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    downloaded = d.get("downloaded_bytes") or 0
                    if total:
                        on_progress(min(100.0, downloaded / total * 100))

            opts["progress_hooks"] = [hook]
        return opts

    def download(
        self,
        url: str,
        task_id: str,
        on_progress: Optional[Callable[[float], None]] = None,
    ) -> Optional[str]:
        """下载 url 到临时目录，返回本地文件路径；失败/超限返回 None。"""
        os.makedirs(self.outdir, exist_ok=True)
        self.cleanup(task_id)  # 清掉上次残留的 part 文件

        try:
            with YoutubeDL(self._opts(task_id, on_progress)) as ydl:
                ydl.download([url])
        except DownloadError as e:
            log.warning("yt-dlp 下载失败 %s: %s", url, e)
            return None

        # 定位产物
        video_candidates = [
            f
            for f in glob.glob(os.path.join(self.outdir, f"{task_id}.*"))
            if os.path.isfile(f) and _is_video_file(f)
        ]
        if not video_candidates:
            all_files = [
                f
                for f in glob.glob(os.path.join(self.outdir, f"{task_id}.*"))
                if os.path.isfile(f)
            ]
            if not all_files:
                return None
            video_candidates = all_files

        best = max(video_candidates, key=os.path.getsize)
        size_mb = os.path.getsize(best) / 1024 / 1024
        if size_mb > self.max_file_mb:
            log.warning("文件过大 %.1fMB > %dMB，丢弃", size_mb, self.max_file_mb)
            self.cleanup(task_id)
            return None
        log.info("下载完成 %s -> %s (%.1fMB)", url, best, size_mb)
        return best

    def cleanup(self, task_id: str) -> None:
        for f in glob.glob(os.path.join(self.outdir, f"{task_id}.*")):
            try:
                os.remove(f)
            except OSError:
                pass
