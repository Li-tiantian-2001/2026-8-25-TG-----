# TG 频道自动化机器人

一个**薄壳版** TG 频道自动化机器人：备用小号（Telethon 用户号）负责下载与上传，用小号自己的**收藏夹（Saved Messages）做遥控器**，无需注册任何 Bot。

- **需求 1**：把 VK / X / YouTube 等链接的视频下载到服务器，再上传到你的频道（用户号上限 4GB，解决"Bot 发不出大文件"问题）
- **需求 2**：把其他频道/群组的**视频**搬到自己的频道 —— 支持 **单条 t.me 链接**搬运 + **整频道自动跟播**，受限频道能搬就搬
  - **跟播默认"真转发"**：服务端复制，不下载不重传、秒级省资源（保留"转发自"+原文案）；想无痕可把 `follow.mode` 改成 `download`（下载→重传，去标签去文案）
  - **只搬视频**：纯图片、文字、文件、音频等一律不搬；**连体消息（相册）里含视频则整组转发**（图片随行）
  - **广告甄别**：关键词黑名单 + 域名黑名单 + 转发标记，命中即屏蔽不搬运（含连体消息整组文案复查，`!status` 可看今日屏蔽数）
  - **冷却**：每个源频道 `follow.interval_sec`（默认 300s）内只搬 1 条，冷却中的更新直接忽略；主频道全局两次发帖间隔 ≥ `upload.min_interval_sec`（30s）
- 内置**冷却时间 / FloodWait 自动退避 / 每日发帖上限**，降低风控风险

---

## 目录结构

```
├── config.toml              # 配置（含你的 api_id/hash，敏感，勿提交）
├── config.example.toml      # 脱敏示例
├── cookies.txt              # VK + X 的 cookies（敏感，勿提交）
├── tg-app_id-hash.txt       # api_id / api_hash（敏感，勿提交）
├── requirements.txt
├── tgbot/
│   ├── main.py              # 入口：--login 首次登录 / 正常运行
│   ├── bot.py               # 核心：收藏夹指令 + 跟播 + 队列 + 清理/水位
│   ├── config.py            # TOML 配置
│   ├── db.py                # SQLite（去重 / 设置 / 每日计数 / 广告屏蔽计数）
│   ├── ad_filter.py         # 广告甄别（关键词 / 域名 / 转发标记）
│   ├── ytdlp_downloader.py  # yt-dlp 下载（VK/X + cookies + mp4 优先）
│   ├── media_processor.py   # ffprobe 检测 + 按需重封装/单线程转码
│   ├── tg_fetch.py          # t.me 链接解析 + Telethon 取图片/视频
│   └── uploader.py          # 串行上传/转发 + 冷却 + FloodWait 退避 + 每日上限
├── deploy/
│   ├── tgbot.service        # systemd 单元
│   └── install.sh           # 一键部署
└── .gitignore               # 已屏蔽所有敏感文件
```

---

## 使用方法（部署后）

所有操作都发给备用小号自己的**收藏夹 / Saved Messages**：

| 你发的 | 效果 |
|---|---|
| `https://vk.com/...` 或 `https://x.com/...` 等视频链接 | 下载并上传到目标频道 |
| `t.me/频道名/123` | 把那条消息的视频搬到你的频道 |
| 直接把视频转发到收藏夹 | 复制该视频到你的频道 |
| `!target @你的频道` | 设置目标频道 |
| `!follow https://t.me/源频道` | 添加整频道自动跟播 |
| `!unfollow <同>` | 取消跟播 |
| `!follows` | 一次列出所有正在跟播的频道 + 冷却状态 |
| `!list` / `!status` / `!pause` / `!resume` / `!help` | 查看/控制 |

---

## 部署到 2H2G Linux 服务器（Ubuntu/Debian）

> 服务器上已有 X-Ray 也不冲突：机器人常驻内存 <500MB，且 systemd 已限制 `MemoryMax=1G`。

1. **本地准备**
   - 在 `my.telegram.org` 申请 `api_id` / `api_hash`（任何手机号都能领）
   - 用 Cookie-Editor 等导出 **VK** 和 **X** 的 cookies（Netscape 格式），命名为 `cookies.txt` 放到项目根目录
   - 备用小号先在手机上养几天号、加入要跟播的源频道、把备用小号设为你频道的管理员（开启"发帖"权限）

2. **上传到服务器**（示例用 scp）
   ```bash
   scp -r 你的项目目录 user@server:/tmp/tgbot-project
   ssh user@server
   cd /tmp/tgbot-project && bash deploy/install.sh
   ```

3. **首次登录**：`install.sh` 会自动执行 `--login`，按提示输入**备用小号的手机号 + 验证码**（如开启了二次验证还要输入密码）。只做一次，之后靠 `tgbot_session.session` 运行。

4. **配置目标频道并实测**
   - 打开备用小号，进入**收藏夹**，发 `!target @你的频道`
   - 发一个 VK 链接 → 应自动下载上传
   - 发一个 `t.me/xxx/123` 链接 → 应自动搬运
   - 发 `!follow https://t.me/源频道` 开启跟播

5. **日常运维**
   ```bash
   sudo journalctl -u tgbot -f      # 看日志
   sudo systemctl restart tgbot     # 重启
   ```

---

## 手动部署（不走 install.sh）

```bash
# 1. 系统依赖
sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip ffmpeg

# 2. 项目
sudo mkdir -p /opt/tgbot && cd /opt/tgbot
# 把 tgbot/、config.toml、cookies.txt、requirements.txt 放进来

# 3. venv
sudo -u tgbot python3 -m venv .venv 2>/dev/null || python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. 首次登录
.venv/bin/python -m tgbot.main --login

# 5. systemd
sudo cp deploy/tgbot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now tgbot
```

---

## 配置项速查（config.toml）

| 配置 | 默认 | 说明 |
|---|---|---|
| `telegram.api_id / api_hash` | — | my.telegram.org 申请 |
| `download.cookies_file` | `cookies.txt` | VK+X cookies，X 下载必需 |
| `download.max_file_mb` | `3800` | 用户号上限 4096MB，留安全余量 |
| `download.format` | mp4/H.264 优先 | 兼容性最好 |
| `upload.min_interval_sec` | `30` | **主频道全局冷却**：两次发帖最小间隔，防触发风控 |
| `upload.daily_cap` | `30` | 每日发帖上限 |
| `media.enabled` | `true` | 上传前检测真实尺寸，并按需修复兼容性 |
| `media.transcode_threads` | `1` | 异常视频转码线程数；2核2G建议保持 1 |
| `media.max_height` | `1080` | 异常视频转码高度上限；不会放大小视频 |
| `follow.allow_media` | `["video"]` | 只搬视频；连体消息含视频则整组转发 |
| `follow.interval_sec` | `300` | **每个源频道冷却**：N 秒内只搬 1 条，期间更新忽略 |
| `follow.mode` | `forward` | 跟播方式：`forward`=真转发（快、省资源，保留转发自+文案）；`download`=下载重传（无痕） |
| `follow.block_forwarded` | `false` | 屏蔽"转发自他人"的帖子（防广告） |
| `follow.ad_min_hits` | `1` | 同一条文案命中 ≥ 几个词才算广告（误伤多就调大） |
| `follow.ad_keywords` | 中英常用广告词 | 正文/文案命中即屏蔽 |
| `follow.ad_domains` | 空 | 出现这些域名的链接即屏蔽 |
| `storage.disk_watermark_pct` | `80` | 磁盘水位，超了自动暂停上传 |
| `target_channel` | 空 | 可留空，用 `!target` 设置 |

## 推送到 GitHub 并在服务器安装

> 本项目已在仓库里排除敏感文件（`.gitignore` 屏蔽了 `cookies.txt`、`tg-app_id-hash.txt`、`config.toml`、`*.session`、`tgbot.db`）。
> 无论仓库公开还是私有，**都不要把 cookies / api_hash / session 推到 GitHub**。

### 1. 创建仓库并推送（在本机执行）

```bash
# ① 在 github.com 新建一个空仓库（不要勾选初始化 README），拿到仓库地址
#    例如: https://github.com/你的用户名/tg-channel-bot.git

# ② 在本项目目录执行
git init
git add .
git commit -m "init: TG 频道自动化机器人"
git branch -M main
git remote add origin https://github.com/你的用户名/tg-channel-bot.git
git push -u origin main
# 首次推送需要输入 GitHub 用户名 + Personal Access Token（Settings → Developer settings → Tokens）
```

推送前先确认敏感文件没被跟踪：

```bash
git status --porcelain          # 应不包含 cookies.txt / tg-app_id-hash.txt / config.toml / *.session
git ls-files | grep -E 'cookies|app_id|config\.toml|session|tgbot\.db' || echo "OK，无敏感文件"
```

### 2. 在服务器上安装

```bash
# ① 克隆仓库
cd /opt && sudo git clone https://github.com/你的用户名/tg-channel-bot.git tgbot
cd tgbot

# ② 把敏感文件从本机传到服务器（只传这一次，不进 GitHub）
#    在本机（项目目录）执行：
#    scp cookies.txt tg-app_id-hash.txt config.toml user@服务器IP:/tmp/tgbot-secrets/
#    然后在服务器执行：
#    sudo cp /tmp/tgbot-secrets/cookies.txt /opt/tgbot/cookies.txt
#    sudo cp /tmp/tgbot-secrets/tg-app_id-hash.txt /opt/tgbot/tg-app_id-hash.txt
#    sudo cp /tmp/tgbot-secrets/config.toml /opt/tgbot/config.toml

# ③ 一键部署
bash deploy/install.sh
```

`install.sh` 会：装依赖 → 生成 venv → **用备用小号登录一次**（输入手机号+验证码）→ 装成 systemd 服务。若没有 `config.toml`/`cookies.txt`，脚本会提示你手动补。

### 3. 日常更新

```bash
cd /opt/tgbot && sudo git pull
sudo systemctl restart tgbot
```

## 注意事项

- **X 下载**依赖 cookies 且平台风控不稳定，属最大不确定点，请先实测一条 X 链接。
- **新号养号**：新注册的小号别一上来就高频搬运，先正常使用几天再逐步提频（默认 60s 间隔/每日 30 条是比较保守的设置）。
- **cookies / session / 配置文件含敏感信息**，请勿提交到公开仓库。
- 搬运他人内容请注意版权合规，只搬有授权/允许转载的内容。
