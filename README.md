# AI News Digest

RSS → AI Summary → Telegram | 自动化新闻聚合与智能推送系统

## 工作流程

```
GitHub Actions (每天定时触发)
    → 抓取 RSS 源 (最近 24h)
    → AI 大模型生成中文摘要
    → Telegram 推送
```

## 快速配置

### 1. 设置 GitHub Secrets

进入仓库 **Settings → Secrets and variables → Actions**，添加以下 4 个 Secret：

| Secret | 说明 | 示例 |
|--------|------|------|
| `AI_API_KEY` | AI 模型 API Key | `sk-...` |
| `AI_API_BASE` | API 地址（OpenAI 兼容） | `https://api.openai.com/v1` |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | `123456:ABC...` |
| `TELEGRAM_CHAT_ID` | 你的 Chat ID | `-1001234567890` |

### 2. 获取 Telegram 凭据

1. 在 Telegram 搜索 `@BotFather`，发送 `/newbot` 创建 Bot
2. 获取 Bot Token
3. 将 Bot 添加到目标频道/群组
4. 发送一条消息后，访问以下 URL 获取 Chat ID：
   `https://api.telegram.org/bot<TOKEN>/getUpdates`

### 3. 自定义 RSS 源

编辑 `config.yaml`，按格式添加/删除订阅源：

```yaml
feeds:
  - name: 显示名称
    url: RSS 地址
    category: 分类名
```

### 4. 测试运行

进入 **Actions → Daily News Digest → Run workflow** 手动触发测试。

## 定时任务

默认 **北京时间每天 09:00** 自动运行。

修改 `.github/workflows/daily-news.yml` 中的 `cron` 调整时间：

| 北京时间 | cron 表达式 |
|---------|------------|
| 06:00 | `0 22 * * *` |
| 09:00 | `0 1 * * *` |
| 12:00 | `0 4 * * *` |
| 18:00 | `0 10 * * *` |

## 支持的 AI 模型

任何 OpenAI 兼容接口：

- **OpenAI** — GPT-4o-mini / GPT-4o
- **DeepSeek** — deepseek-chat
- **智谱** — glm-4-flash
- **本地部署** — Ollama / vLLM

修改 `config.yaml` 中的 `ai.model` 并设置对应的 `AI_API_BASE` 即可切换。

## 项目结构

```
├── .github/workflows/
│   └── daily-news.yml      # GitHub Actions 定时任务
├── main.py                  # 核心逻辑（RSS → AI → Telegram）
├── config.yaml              # RSS 源和参数配置
├── requirements.txt         # Python 依赖
└── README.md
```
