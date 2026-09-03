"""Low-cost video validation and conditional Telegram compatibility repair."""
from __future__ import annotations

import json
import logging
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


log = logging.getLogger("tgbot.media")


@dataclass(frozen=True)
class VideoInfo:
    path: str
    width: int
    height: int
    duration: int
    action: str = "direct"


class MediaProcessError(RuntimeError):
    pass


class MediaProcessor:
    def __init__(self, cfg):
        media = cfg.data.get("media", {})
        self.enabled = bool(media.get("enabled", True))
        self.ffprobe = str(media.get("ffprobe_path", "ffprobe") or "ffprobe")
        self.ffmpeg = str(media.get("ffmpeg_path", "ffmpeg") or "ffmpeg")
        self.preset = str(media.get("transcode_preset", "veryfast") or "veryfast")
        self.crf = int(media.get("transcode_crf", 23) or 23)
        self.threads = max(1, int(media.get("transcode_threads", 1) or 1))
        self.max_height = max(0, int(media.get("max_height", 1080) or 0))

    def prepare(self, path: str) -> VideoInfo:
        """Probe a video and repair only when its Telegram compatibility requires it."""
        meta = self._probe(path)
        if not self.enabled:
            return self._info(path, meta, "probe-only")

        video = meta["video"]
        compatible_video = video["codec"] == "h264" and video["pix_fmt"] in {
            "yuv420p", "yuvj420p"
        }
        compatible_audio = meta["audio_codec"] in {None, "aac"}
        square_pixels = video["sar"] in {None, "1:1", "0:1"}
        no_rotation = video["rotation"] % 360 == 0
        is_mp4 = "mp4" in meta["format"] or "mov" in meta["format"]

        if compatible_video and compatible_audio and square_pixels and no_rotation and is_mp4:
            return self._info(path, meta, "direct")

        if compatible_video and compatible_audio and square_pixels and no_rotation:
            output = self._output_path(path, "remux")
            try:
                self._run([
                    self.ffmpeg, "-y", "-v", "error", "-i", path,
                    "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
                    "-movflags", "+faststart", output,
                ])
                fixed = self._probe(output)
            except Exception:
                self._remove_partial(output)
                raise
            return self._info(output, fixed, "remux")

        output = self._output_path(path, "fixed")
        scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1"
        if self.max_height:
            scale = (
                f"scale='trunc(min(iw*sar,{self.max_height}*dar)/2)*2':"
                f"'trunc(min(ih,{self.max_height})/2)*2':force_original_aspect_ratio=decrease,"
                "setsar=1"
            )
        try:
            self._run([
                self.ffmpeg, "-y", "-v", "error", "-i", path,
                "-map", "0:v:0", "-map", "0:a?", "-vf", scale,
                "-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf),
                "-pix_fmt", "yuv420p", "-threads", str(self.threads),
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                "-metadata:s:v:0", "rotate=0", output,
            ])
            fixed = self._probe(output)
            result = self._info(output, fixed, "transcode")
        except Exception:
            self._remove_partial(output)
            raise
        return result

    def _probe(self, path: str) -> dict:
        proc = self._run([
            self.ffprobe, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", path,
        ])
        try:
            raw = json.loads(proc.stdout)
            streams = raw.get("streams", [])
            video = next(s for s in streams if s.get("codec_type") == "video")
        except (ValueError, KeyError, StopIteration, TypeError) as exc:
            raise MediaProcessError(f"ffprobe returned no usable video stream: {exc}") from exc

        rotation = 0
        tags = video.get("tags") or {}
        if tags.get("rotate") is not None:
            rotation = int(float(tags["rotate"]))
        for side_data in video.get("side_data_list") or []:
            if side_data.get("rotation") is not None:
                rotation = int(float(side_data["rotation"]))

        duration = video.get("duration") or (raw.get("format") or {}).get("duration") or 0
        try:
            duration = float(duration)
            if not math.isfinite(duration):
                duration = 0
        except (TypeError, ValueError):
            duration = 0
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        return {
            "video": {
                "width": int(video.get("width") or 0),
                "height": int(video.get("height") or 0),
                "codec": str(video.get("codec_name") or "").lower(),
                "pix_fmt": str(video.get("pix_fmt") or "").lower(),
                "sar": video.get("sample_aspect_ratio"),
                "rotation": rotation,
                "duration": duration,
            },
            "audio_codec": str(audio.get("codec_name") or "").lower() if audio else None,
            "format": str((raw.get("format") or {}).get("format_name") or "").lower(),
        }

    def _info(self, path: str, meta: dict, action: str) -> VideoInfo:
        video = meta["video"]
        width, height = video["width"], video["height"]
        if video["rotation"] % 180:
            width, height = height, width
        if width < 1 or height < 1:
            raise MediaProcessError("video has invalid dimensions")
        return VideoInfo(
            path=path,
            width=width,
            height=height,
            duration=max(1, math.ceil(video["duration"])),
            action=action,
        )

    @staticmethod
    def _output_path(path: str, suffix: str) -> str:
        source = Path(path)
        return str(source.with_name(f"{source.stem}.{suffix}.mp4"))

    @staticmethod
    def _remove_partial(path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("无法清理媒体处理残留 %s: %s", path, exc)

    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                command, check=True, capture_output=True, text=True, timeout=7200
            )
        except FileNotFoundError as exc:
            raise MediaProcessError(f"required command not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaProcessError(f"media command timed out: {command[0]}") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()[-1000:]
            raise MediaProcessError(f"media command failed: {detail}") from exc
