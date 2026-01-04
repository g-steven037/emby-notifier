# 🎬 Emby Telegram Notification Bot 使用说明

## 📑 目录
1. [准备工作](#getting-started)
2. [创建配置目录与文件](#config-files)
3. [用 Docker 运行](#docker-run)
4. [在 Telegram 添加机器人并配置权限](#telegram-bot-setup)
5. [将机器人加入群聊与频道并获取 Chat ID](#join-chats-and-get-chat-id)
6. [配置 Emby Webhook](#emby-webhook)
7. [常用命令与管理菜单](#commands-and-settings-menu)
8. [进阶设置与可选项](#advanced-settings)
9. [注意事项](#notes)
10. [日志与排错](#logs-and-troubleshooting)
11. [安全与备份建议](#security-and-backup)

---

<a id="getting-started"></a>
## 🧰 准备工作

### 1) 创建 Telegram 机器人（获取 `token`）
- 与 **@BotFather** 对话 → ` /newbot `
- 按提示设置 **Bot 名称** 与 **用户名**（以 `bot` 结尾）
- 记下返回的 **HTTP API Token**，填入 `config.yaml` 的 `telegram.token`

> 建议：在 @BotFather 里执行 ` /setprivacy ` → 选择你的机器人 → 选择 **Disable**（关闭隐私模式），以便在群聊中更好地接收消息与处理回复。

### 2) 获取你的 Telegram 用户 ID（`ADMIN_USER_ID`）
- 任一方式均可：
  - 与 **@userinfobot** 或 **@getidsbot** 对话，发送任何消息，它会返回你的 `id`
  - 或访问：`https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`，给机器人发一条私聊消息后在返回 JSON 里找 `message.from.id`
- 将这个数值填入 `config.yaml` 的 `telegram.admin_user_id`

> 说明：**超级管理员** = `admin_user_id` 对应的人。脚本中只有超级管理员能用 `/status` 与 `/settings`。

### 3) 注册 TMDB API Token（可选但强烈建议）
- 在 TMDB 创建账号并生成 **API Key (v3)**
- 将 Token 填入 `config.yaml` 的 `tmdb.api_token`
- 用于查询海报与生成 TMDB 链接

### 4) Emby 参数
- `emby.server_url`：形如 `https://emby.example.com` 或 `http://192.168.1.10:8096`
- `emby.api_key`：Emby 后台 → 高级 → **API 密钥** 生成
- `emby.user_id`：用于调用部分用户上下文 API。可通过 Emby 后台用户列表或 API 获取
- `emby.remote_url`：公网可访问的 Emby Web（用于生成 “在服务器中查看” 按钮）
- `emby.username` 与 `emby.password`：（可选）用于执行删除媒体等高级操作，机器人会用此账号密码获取临时的管理员权限。
- `emby.app_scheme`：（可选）预留

### 5) 网络代理（可选）
- 如果 Telegram/TMDB 访问受限，设置 `proxy.http_proxy`，格式：  
  `http://user:pass@host:port` 或 `http://host:port`
- 脚本会对 Telegram/TMDB 请求自动套用该代理

---

<a id="config-files"></a>
## 📁 创建配置目录与文件

### 目录结构（建议）
```
/opt/emby-notifier/
└── config/
    ├── config.yaml
    └── cache/
        ├── poster_cache.json (运行时自动生成 )
        └── languages.json (可选，用于将音频的语言显示为中文，可以直接下载https://github.com/xpisce/emby-notifier/blob/main/cache/languages.json使用）
```

### `config.yaml` 最小可用示例（按需修改）
📄 以下为最小可用示例，完整配置文件说明参见：  👉 [config/config.yaml](https://github.com/xpisce/emby-notifier/blob/main/config/config.yaml)
```yaml
telegram:
  token: "YOUR_TELEGRAM_BOT_TOKEN"
  admin_user_id: 123456789      # 你的 Telegram 用户 ID（超级管理员）
  group_id: -1001122334455      # 群组 Chat ID（可先留空）
  channel_id: -1009988776655    # 频道 Chat ID（可先留空）

tmdb:
  api_token: "YOUR_TMDB_V3_API_KEY"

proxy:
  http_proxy: ""                # 如需代理，填 http://host:port

emby:
  server_url: "http://192.168.1.10:8096"
  api_key: "YOUR_EMBY_API_KEY"
  user_id: "YOUR_EMBY_USER_ID"
  username: "YOUR_EMBY_USERNAME"  # 可选，用于执行删除等高级操作
  password: "YOUR_EMBY_PASSWORD"  # 可选，用于执行删除等高级操作
  remote_url: "https://emby.example.com"   # 公网访问地址，供按钮跳转
  app_scheme: ""                # 预留

settings:
  timezone: "Asia/Shanghai"     # 时区
  debounce_seconds: 10          # 开始/继续播放防抖
  media_base_path: "/media"     # 你的媒体根路径（用于推断分类）
  poster_cache_ttl_days: 30

  content_settings:
    new_library_notification:
      show_poster: true
      show_media_detail: true
      media_detail_has_tmdb_link: true
      show_overview: true
      show_media_type: true
      show_video_spec: false
      show_audio_spec: false
      show_timestamp: true
      show_view_on_server_button: true

    status_feedback:
      show_poster: true
      show_player: true
      show_device: true
      show_location: true
      show_media_detail: true
      media_detail_has_tmdb_link: true
      show_media_type: true
      show_overview: false
      show_timestamp: true
      show_view_on_server_button: true
      show_terminate_session_button: true
      show_send_message_button: true
      show_broadcast_button: true
      show_terminate_all_button: true

    playback_action:
      show_poster: true
      show_media_detail: true
      media_detail_has_tmdb_link: true
      show_user: true
      show_player: true
      show_device: true
      show_location: true
      show_progress: true
      show_video_spec: false
      show_audio_spec: false
      show_media_type: true
      show_overview: true
      show_timestamp: true
      show_view_on_server_button: true

    library_deleted_notification:
      show_poster: true
      show_media_detail: true
      media_detail_has_tmdb_link: true
      show_overview: true
      show_media_type: true
      show_timestamp: true

    search_display:
      show_media_type_in_list: true
      movie:
        show_poster: true
        title_has_tmdb_link: true
        show_type: true
        show_category: true
        show_overview: true
        show_video_spec: true
        show_audio_spec: true
        show_added_time: true
        show_view_on_server_button: true
      series:
        show_poster: true
        title_has_tmdb_link: true
        show_type: true
        show_category: true
        show_overview: true
        show_view_on_server_button: true
        season_specs:
          show_video_spec: true
          show_audio_spec: true
        update_progress:
          show_latest_episode: true
          latest_episode_has_tmdb_link: true
          show_overview: false
          show_added_time: true
          show_progress_status: true

  notification_management:
    library_new:
      to_group: true
      to_channel: true
      to_private: false
    playback_start: true
    playback_pause: false
    playback_stop: true
    library_deleted: true

  auto_delete_settings:
    new_library:
      to_group: false
      to_channel: false
      to_private: true
    library_deleted: true
    playback_start: true
    playback_pause: true
    playback_stop: true
```

> 注：即便未完整填写，脚本也会以 **默认值** 补齐。你可随时使用 `/settings` 菜单在线切换，脚本会自动 **写回 `config.yaml`**。

---

<a id="docker-run"></a>
## 🐳 用 Docker 运行

### 方式 A：`docker run`
```bash
# 1) 创建目录
sudo mkdir -p /opt/emby-notifier/config
# 将上面的 config.yaml 放到 /opt/emby-notifier/config/config.yaml

# 2) 运行容器（假设镜像为 steven03799/emby_notifier:latest）
docker run -d \
  --name emby-notifier \
  -p 8080:8080 \
  -v /opt/emby-notifier/config:/config \
  # 如果要使用文件管理功能，请取消以下两行的注释，并替换为你的实际路径
 # -v /path/to/your/media:/media \
 # -v /path/to/your/cloud:/cloud \
  --restart unless-stopped \
  steven03799/emby_notifier
```

### 方式 B：`docker-compose`
```yaml
version: "3.8"
services:
  emby-notifier:
    image: steven03799/emby_notifier
    container_name: emby-notifier
    ports:
      - "8080:8080"           # Emby Webhook 将 POST 到这个端口
    volumes:
      - /opt/emby-notifier/config:/config
      # 如果要使用文件管理功能，请取消以下两行的注释，并替换为你的实际路径
     # - /path/to/your/media:/media
     # - /path/to/your/cloud:/cloud
    restart: unless-stopped
```
```bash
docker compose up -d
```

> 说明：容器启动后会在前台 **轮询 Telegram**，并在 `0.0.0.0:8080` 开一个 HTTP 服务（供 Emby Webhook 调用）。

---

<a id="telegram-bot-setup"></a>
## 🤖 在 Telegram 添加机器人并配置权限

### 私聊
- 直接给你的机器人发送 `/start`，确认可正常响应

### 群聊
1. 将机器人拉进群
2. 在群 **将机器人设为管理员**，至少勾选：
   - **删除消息**（用于 60 秒后自动删除）
   - **置顶消息**、**管理聊天** 非必须
3. 若开启隐私模式，机器人只能看到命令与对机器人消息的**直接回复**；  
   建议在 @BotFather 关闭隐私模式以获得更好体验

### 频道
1. 将机器人添加为 **频道管理员**，授予 **发布消息** 权限
2. 把频道的 `chat_id` 写入 `config.yaml` 的 `telegram.channel_id`（见下节如何获取）

---

<a id="join-chats-and-get-chat-id"></a>
## 👥 将机器人加入群聊与频道并获取 Chat ID

> 你需要 **群组 ID（负数）** 与 **频道 ID（通常以 `-100` 开头）**，写入 `config.yaml`：

### 获取群组 `group_id`
- 将机器人加入群后，在群里发送一条消息（如 `/start`）
- 在浏览器访问：
  ```
  https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
  ```
- 在返回 JSON 中找到该群消息对应的：
  ```json
  "chat": { "id": -1001234567890, "type": "supergroup", ... }
  ```
- 将这个 `id` 写入 `telegram.group_id`

### 获取频道 `channel_id`
- 把机器人设为频道管理员
- 在频道发一条消息（或让机器人发一次）
- 访问 `getUpdates`，找到频道消息对应的：
  ```json
  "chat": { "id": -1009876543210, "type": "channel", ... }
  ```
- 将这个 `id` 写入 `telegram.channel_id`

> 也可借助 **@RawDataBot / @getidsbot** 获取 `chat_id`（把消息转发给它即可）。

---

<a id="emby-webhook"></a>
## 🔔 配置 Emby Webhook

> 目标：让 Emby 在发生事件时 **POST** 到 `http://<你的主机IP或域名>:8080/`

### 步骤
1. 在 Emby 后台安装并启用 **Webhook/通知**（不同版本路径略有不同，通常在 “通知” 或 “Webhooks”）
2. 新建一个 **Webhook** 项，URL 填：
   ```
   http://<HOST>:8080/
   ```
   - 若 Docker 部署在 Emby 同一台主机，可写 `http://host.docker.internal:8080/`（Windows/Mac），Linux 用宿主机 IP
3. 选择要推送的 **事件类型**（脚本支持）：

   **播放**
   - 开始
   - 暂停
   - 取消暂停
   - 停止

   **媒体库**
   - 新媒体已添加 
   - 媒体删除
   - 按剧集和专辑对通知进行分组

4. 保存并应用

> 提示：脚本已兼容 `Content-Type: application/json` 与 `application/x-www-form-urlencoded`（`data=` 字段）的两种推送格式。

---

<a id="commands-and-settings-menu"></a>
## ⌨️ 命令与管理菜单

| 命令 | 描述 | 权限要求 | 详细使用说明 |
|------------|-----------------------------------------|--------------|--------------|
| `/search` | 搜索节目关键词，支持智能后备搜索，展示剧集更新状态与规格。 | 所有人 | 👉 [/search.md](https://github.com/xpisce/emby-notifier/blob/main/User%20Guide/userguide-search.md) |
| `/status` | 查看当前播放会话，支持远程控制。 | 管理员 | 👉 [/status.md](https://github.com/g-steven037/emby-notifier/blob/main/User%20Guide/userguide-status.md) |
| `/settings`| 打开通知展示与开关设置交互菜单。 | 管理员 | 👉 [/settings.md](https://github.com/g-steven037/emby-notifier/blob/main/User%20Guide/userguide-settings.md) |
| `/manage` | 管理媒体文件，支持更新、入库、删除等高级操作。 | 管理员 | 👉 [/manage.md](https://github.com/g-steven037/emby-notifier/blob/main/User%20Guide/userguide-manage.md) |

### `/start`
- 输出简要帮助

### `/search`
- 输入 `/search <关键词 或 关键词+年份>` ，从而在 Emby 库中搜索电影/剧集  
- 也可只输入 `/search` ，然后机器人等待你**回复**关键词（群里需 **回复** 提示消息）

### `/status`（仅超级管理员）
- 拉取所有正在播放的会话并展示细节
- 支持（可配置显示）：
  - 用户、播放器、设备、IP/地理位置
  - 节目详情与 TMDB 链接、海报
  - 播放进度（百分比/时间）
  - “在服务器中查看”按钮
  - 管理按钮：**终止会话**、**发送消息**、**群发消息**、**终止所有**
 
### `/manage`（仅超级管理员）
- 直接输入 /manage 会弹出交互式菜单，可选择“管理已有节目”或“从网盘更新”。
- 输入 /manage <关键词 或 TMDB ID> 可直接搜索要管理的节目。
- 核心功能：
  更新文件：将网盘中的文件更新/同步到本地目录中，.nfo、.jpg 等元素据文件会直接复制到本地，.mkv等视频文件会在本地创建 .strm 链接。
  添加入库：通过引导式流程，输入节目信息，机器人会自动解析云端目录中的 .nfo 文件并执行更新操作。
  删除节目：提供精细化删除选项，可选择：
  - 仅从 Emby 媒体库中删除。
  - 删除本地文件。
  - 删除网盘文件。
  - 同时删除本地与网盘文件。
> 所有危险操作均有二次确认。

### `/settings`（仅超级管理员）
- 打开 **交互式管理菜单**：
  - **推送内容设置**：新增 / 删除 / 播放 / 状态反馈 / 搜索结果 展示项逐一开启关闭
  - **通知管理**：各类事件推送到 **群组 / 频道 / 私聊** 的开关
  - **自动删除消息设置**：每类通知是否在 60 秒后自动删除
- 菜单中的开关变更会 **立即保存** 到 `config.yaml`
- 导航说明：  
  - “➡️ 子菜单” 进入下一级  
  - “✅/❌ 选项” 切换开关  
  - “◀️ 返回上一级 / ☑️ 完成” 关闭菜单

> 消息自动删除规则（默认 60 秒）：  
> - **私聊**：除 “新增节目” 外的所有消息均自动删除（可按配置关闭）  
> - **群聊**：命令消息、对机器人消息的**直接回复**会被删除（需机器人是群管理员并有**删除消息**权限）  
> - 频道与群的新增/播放/删除通知是否删除由 `settings.auto_delete_settings.*` 控制

---

<a id="advanced-settings"></a>
## ⚙️ 进阶设置与可选项

### 代理
- `proxy.http_proxy`：脚本会自动给 Telegram 与 TMDB 请求加代理
- 示例：`http://127.0.0.1:7890`

### 时区
- `settings.timezone`：影响推送中的时间戳显示，如 `Asia/Shanghai`

### 媒体根路径
- `settings.media_base_path`：用于从路径推断节目分类（国产剧/动漫/外语电影等）

### 海报缓存
- `settings.poster_cache_ttl_days`：缓存 TMDB 海报 URL 的有效天数
- 缓存文件：`/config/cache/poster_cache.json`

### 语言映射（可选）
- `languages.json` 放在 `/config/cache/languages.json`（不存在则使用内置简表）
- 用于将音轨 `eng/jpn/zho` 转译为中文显示

---

<a id="notes"></a>
## ❗ 注意事项

- `emby.user_id` 为必填项，否则节目信息将无法正确解析  
- 请授予机器人在群组中的消息管理、删除权限  
- 若启用了 `allowed_group_id`，则机器人仅对该群组内消息作出响应  

---

<a id="logs-and-troubleshooting"></a>
## 🐞 日志与排错

### 查看日志
```bash
docker logs -f emby-notifier
```

### 常见问题
- **`权限不足：此命令仅限超级管理员使用。`**  
  - 确认 `config.yaml` 的 `telegram.admin_user_id` 填的是你自己的 Telegram 用户 ID
- **群里按钮任何人都能点？**  
  - 代码已限制：只有 **发起者**（以及超级管理员）能操作  
  - 若仍异常，检查是否点的是同一条消息、以及回调数据是否被篡改（过期）
- **消息无法自动删除**  
  - 机器人需是 **群管理员**，且具备 **删除消息** 权限
- **频道收不到新增通知**  
  - 机器人必须是 **频道管理员** 且有 **发布消息** 权限  
  - `telegram.channel_id` 填写正确（常见为 `-100xxxxxxxxxx`）
- **TMDB 海报不显示**  
  - 填写 `tmdb.api_token`  
  - 网络可达（必要时配置 `proxy.http_proxy`）
- **Emby 事件未触发**  
  - Emby Webhook URL 是否指向 `http://<宿主IP>:8080/`  
  - 对应事件是否勾选（library.new / playback.start 等）  
  - 宿主机防火墙是否放行 8080

### 获取/验证 Chat ID
- 发消息后访问：`https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
- 在对应消息里寻找：
  - **私聊**：`"chat":{"id": 123456789, "type": "private"}`
  - **群组**：`"chat":{"id": -100..., "type": "supergroup"}`
  - **频道**：`"chat":{"id": -100..., "type": "channel"}`

---

<a id="security-and-backup"></a>
## 🔒 安全与备份建议
- `config.yaml` 含有敏感信息（Token、API Key），请限制文件权限与目录访问
- 定期备份 `/config/` 目录（尤其是 `config.yaml` 与 `cache/`）
- 如部署在公网，建议用反向代理（Nginx/Caddy）加 TLS 保护 Emby 与远程面板
