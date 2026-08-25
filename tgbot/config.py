"""配置加载（TOML，Python 3.11+ 标准库 tomllib）。"""
from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULTS: dict = {
    "telegram": {"api_id": 0, "api_hash": "", "session": "tgbot_session"},
    "download": {
        "cookies_file": "提供信息/cookies.txt",
        "temp_dir": "",
        "max_file_mb": 3800,
        "format": "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "retries": 5,
        "socket_timeout": 60,
    },
    "upload": {
        "min_interval_sec": 60,
        "daily_cap": 30,
        "upload_timeout_sec": 1800,
        "caption_prefix": "",
    },
    "storage": {
        "db_path": "tgbot.db",
        "disk_watermark_pct": 80,
        "cleanup_interval_sec": 3600,
        "temp_max_age_sec": 21600,
    },
    "follow": {"enabled": True},
    "target_channel": "",
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    data: dict

    @classmethod
    def load(cls, path: str = "config.toml") -> "Config":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"找不到配置文件: {p}（请先 cp config.example.toml config.toml 并填入真实值）"
            )
        with open(p, "rb") as f:
            user = tomllib.load(f)
        merged = _deep_merge(DEFAULTS, user)
        cfg = cls(data=merged)
        cfg._apply_platform_defaults()
        return cfg

    def _apply_platform_defaults(self) -> None:
        temp = self.data["download"].get("temp_dir") or ""
        if not temp:
            if sys.platform == "win32":
                temp = str(Path("tmp") / "tgbot")
            else:
                temp = "/tmp/tgbot"
            self.data["download"]["temp_dir"] = temp

    @property
    def api_id(self) -> int:
        return int(self.data["telegram"]["api_id"] or 0)

    @property
    def api_hash(self) -> str:
        return str(self.data["telegram"]["api_hash"] or "")

    @property
    def session(self) -> str:
        return str(self.data["telegram"]["session"] or "tgbot_session")

    def get(self, *keys: str, default=None):
        node = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node
