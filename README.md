# AI News Digest

RSS → AI Summary → Telegram | 持续新闻监控与智能推送系统

**每 15 分钟自动抓取 8 个安全/科技 RSS 源，AI 翻译摘要后推送到 Telegram。不会重复推送同一篇文章。**

## 工作流程

```
每 15 分钟 (GitHub Actions)
    → 抓取 RSS 源 (最近 1 小时)
    → seen.json 过滤已处理文章
    → AI 大模型生成双语摘要
    → Telegram 推送
    → 更新 seen.json 防止重复
```

## 特性

- **持续监控** — 每 15 分钟轮询，新文章发布后几乎实时推送
- **智能去重** — `seen.json` 持久化记录已处理文章 GUID，跨运行不重复
- **双语翻译** — 保留英文原标题 + 中文翻译标题 + 50 字中文摘要
- **排版美观** — 纯文本 emoji 格式，无 Markdown 符号，Telegram 上完美渲染
- **高可用** — AI 超时自动重试（精简 prompt），失败降级到原始列表
- **零成本** — 公开仓库 + GitHub Actions 无限分钟，完全免费

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

进入 **Actions → News Monitor → Run workflow** 手动触发测试。

## 定时任务

默认 **每 15 分钟** 自动运行（`*/15 * * * *`）。

如需调整频率，修改 `.github/workflows/news-monitor.yml` 中的 `cron`：

| 频率 | cron 表达式 |
|------|------------|
| 每 5 分钟 | `*/5 * * * *` |
| 每 15 分钟 | `*/15 * * * *` |
| 每 30 分钟 | `*/30 * * * *` |
| 每 1 小时 | `0 * * * *` |

## 去重机制

每次运行后自动更新 `seen.json`，记录已处理文章的 GUID。下次运行时：

```
新文章 → 检查是否在 seen.json 中 → 未见过 → AI 摘要 → 推送 → 记录 GUID
                                  → 已见过 → 跳过
```

`seen.json` 自动清理 7 天前的记录，保持文件轻量。

## 支持的 AI 模型

任何 OpenAI 兼容接口：

- **OpenAI** — GPT-4o-mini / GPT-4o
- **DeepSeek** — deepseek-chat
- **智谱** — GLM-4 / GLM-5.1
- **本地部署** — Ollama / vLLM

修改 `config.yaml` 中的 `ai.model` 并设置对应的 `AI_API_BASE` 即可切换。

## 环境变量（可选）

可在 GitHub Actions workflow 中通过 `env` 覆盖默认配置：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `FETCH_HOURS` | 抓取时间窗口（小时） | `1` |
| `MAX_ARTICLES` | 单次最多处理文章数 | `30` |

## 项目结构

```
├── .github/workflows/
│   └── news-monitor.yml     # GitHub Actions 定时任务（每 15 分钟）
├── main.py                  # 核心逻辑（RSS → 去重 → AI → Telegram）
├── config.yaml              # RSS 源和参数配置
├── seen.json                # 已处理文章 GUID 记录（自动生成）
├── requirements.txt         # Python 依赖
└── README.md
```

## 发布说明

本仓库为**公开仓库**，充分利用 GitHub Actions 对公开仓库的**无限免费额度**。所有敏感信息（API Key、Token）均存储在 GitHub Secrets 中，不会暴露在代码或提交历史中。
