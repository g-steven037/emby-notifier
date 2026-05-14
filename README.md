# emby-notifier
Emby-Notifier 是一款为 Emby 深度定制的 Telegram 通知系统。它通过 Python Flask 接收 Emby 的 Webhook 信号，并利用 TMDB API 自动补全元数据（如横屏海报、主演、简介等），同时通过调用 Emby 本地 API 实现精准的“已播出缺集”统计和“下一集”播出预报。
