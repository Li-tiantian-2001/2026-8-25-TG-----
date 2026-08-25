"""入口：python -m tgbot.main [--login]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .bot import TgBot
from .config import Config


def main() -> int:
    parser = argparse.ArgumentParser(description="TG 频道自动化机器人")
    parser.add_argument("--config", default="config.toml", help="配置文件路径")
    parser.add_argument("--login", action="store_true", help="交互式登录并保存 session（首次部署用）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    cfg = Config.load(args.config)
    bot = TgBot(cfg)

    if args.login:
        async def _do_login() -> None:
            await bot.client.start()  # 交互式输入手机号+验证码
            me = await bot.client.get_me()
            print(f"登录成功: {getattr(me, 'username', None) or me.id}")
            await bot.client.disconnect()

        try:
            asyncio.run(_do_login())
        except Exception as e:
            logging.error("登录失败: %s", e)
            return 1
        return 0

    try:
        asyncio.run(bot.run_forever())
    except KeyboardInterrupt:
        print("已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
