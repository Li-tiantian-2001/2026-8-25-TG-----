"""配置加载（TOML；Python 3.11+ 用标准库 tomllib，3.10 及以下用 tomli）。"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python 3.10 及以下
    import tomli as tomllib  # 需 requirements.txt 里的 tomli

DEFAULTS: dict = {
    "telegram": {"api_id": 0, "api_hash": "", "session": "tgbot_session"},
    "download": {
        "cookies_file": "cookies.txt",
        "temp_dir": "",
        "max_file_mb": 3800,
        "format": "best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "retries": 5,
        "socket_timeout": 60,
    },
    "upload": {
        "min_interval_sec": 30,
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
    "follow": {
        "enabled": True,
        "mode": "forward",
        "interval_sec": 300,
        "allow_media": ["video"],
        "block_forwarded": False,
        "ad_min_hits": 1,
        "ad_keywords": [
            "群组", "频道", "视频", "音乐", "必备", "搜索", "公开", "实力", "直播", "内幕",
            "数据", "精准", "备用", "体育", "存款", "微信", "支付宝", "银行卡", "客服", "经理",
            "官方", "品牌", "信赖", "真人", "无忧", "提款", "大额", "抽奖", "短句", "上头",
            "精选", "系列", "t.me", "http", "@", "短剧", "洗浴", "全国", "代理", "圈子", "会员",
            "内容", "付费", "资源", "链接", "详情", "议价", "推荐", "入圈", "梯子", "套餐",
            "vpn", "https", "实测", "退款", "退费", "下注", "一注",
        ],
        "ad_domains": [],
    },
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
