#!/usr/bin/env bash
# TG 频道自动化机器人 · 一键部署脚本（Ubuntu/Debian，2H2G 与 X-Ray 共存）
# 用法：在项目根目录执行  bash deploy/install.sh
#   支持两种来源：① 整个目录 scp 上来 ② git clone 自 GitHub
# 注意：GitHub 仓库不含 config.toml 与 cookies.txt（敏感），需要手动 scp 到服务器
set -euo pipefail

APP_DIR="/opt/tgbot"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BOT_USER="tgbot"

echo "==> 1/6 安装系统依赖 (python3 / venv / ffmpeg / curl)"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip ffmpeg curl

echo "==> 2/6 创建用户与目录，复制代码"
sudo id -u "$BOT_USER" &>/dev/null || sudo useradd -r -s /usr/sbin/nologin "$BOT_USER"
sudo mkdir -p "$APP_DIR"
sudo cp -r "$SRC_DIR/tgbot" "$APP_DIR/"

# --- 配置：优先已有 config.toml，否则从示例生成并提醒 ---
if [ -f "$SRC_DIR/config.toml" ]; then
  sudo cp "$SRC_DIR/config.toml" "$APP_DIR/config.toml"
else
  sudo cp "$SRC_DIR/config.example.toml" "$APP_DIR/config.toml"
  echo "    ⚠️  未找到 config.toml，已从 config.example.toml 生成。"
  echo "       请编辑 $APP_DIR/config.toml 填入 api_id / api_hash（my.telegram.org 获取）"
fi

# --- 敏感文件（VK+X cookies / api_id-hash）：存在则复制，否则提醒 ---
if [ -f "$SRC_DIR/cookies.txt" ]; then
  sudo cp "$SRC_DIR/cookies.txt" "$APP_DIR/cookies.txt"
else
  echo "    ⚠️  未找到 cookies.txt。请把本地的 cookies.txt 传到服务器："
  echo "       scp cookies.txt user@SERVER:/tmp/tgbot-secrets/  (再移到 $APP_DIR/cookies.txt)"
fi
if [ -f "$SRC_DIR/tg-app_id-hash.txt" ]; then
  sudo cp "$SRC_DIR/tg-app_id-hash.txt" "$APP_DIR/tg-app_id-hash.txt"
fi
sudo chown -R "$BOT_USER:$BOT_USER" "$APP_DIR"

echo "==> 3/6 创建虚拟环境并安装依赖"
sudo -u "$BOT_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$BOT_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$BOT_USER" "$APP_DIR/.venv/bin/pip" install -r "$SRC_DIR/requirements.txt"

echo "==> 4/6 首次登录备用小号（仅首次需要，交互输入手机号+验证码）"
cd "$APP_DIR"
API_ID=$(grep -oP '^api_id\s*=\s*\K\d+' "$APP_DIR/config.toml" 2>/dev/null | head -1)
if [ "${API_ID:-0}" != "0" ] && [ ! -f "$APP_DIR/tgbot_session.session" ]; then
  sudo -u "$BOT_USER" "$APP_DIR/.venv/bin/python" -m tgbot.main --login || echo "   登录未完成，可稍后手动重跑此命令"
elif [ ! -f "$APP_DIR/tgbot_session.session" ]; then
  echo "   跳过自动登录：config.toml 未填 api_id，填好后执行："
  echo "       cd $APP_DIR && sudo -u $BOT_USER .venv/bin/python -m tgbot.main --login"
fi

echo "==> 5/6 安装 systemd 服务"
sudo cp "$SRC_DIR/deploy/tgbot.service" /etc/systemd/system/tgbot.service
sudo systemctl daemon-reload

echo "==> 6/6 启动服务"
sudo systemctl enable --now tgbot.service
sleep 3
sudo systemctl status tgbot.service --no-pager || true

echo ""
echo "======================================================"
echo "部署完成。接下来："
echo " 1. 往备用小号的【收藏夹】发:  !target @你的频道    设置目标频道"
echo " 2. 发一个 VK 链接 和 一个 t.me/频道/消息ID 链接 实测"
echo " 3. 发 !follow <源频道> 开启整频道跟播"
echo " 4. 看日志: sudo journalctl -u tgbot -f"
echo "    重启:   sudo systemctl restart tgbot"
echo "======================================================"
