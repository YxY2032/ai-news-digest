#!/usr/bin python3
"""
AI News Digest - RSS → AI Summary → Telegram Push

Daily automated news aggregation powered by GitHub Actions.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import yaml


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_rss(feeds, hours=24, max_articles=30):
    """Fetch articles from all RSS feeds within the time window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles = []

    for feed_cfg in feeds:
        try:
            print(f"  Fetching {feed_cfg.get('name', feed_cfg['url'])}...")
            feed = feedparser.parse(feed_cfg["url"])
            source = feed_cfg.get(
                "name", getattr(feed.feed, "title", feed_cfg["url"])
            )
            category = feed_cfg.get("category", "General")

            for entry in feed.entries:
                pub_time = _parse_entry_time(entry)
                if pub_time and pub_time > cutoff:
                    articles.append(
                        {
                            "title": entry.get("title", "").strip(),
                            "link": entry.get("link", ""),
                            "description": _strip_html(
                                entry.get(
                                    "summary", entry.get("description", "")
                                )
                            )[:500],
                            "published": pub_time.strftime("%Y-%m-%d %H:%M UTC"),
                            "source": source,
                            "category": category,
                        }
                    )
        except Exception as e:
            print(f"  ⚠️  Failed to fetch {feed_cfg['url']}: {e}")

    # Deduplicate by title hash
    seen = set()
    unique = []
    for a in articles:
        key = hashlib.md5(a["title"].encode()).hexdigest()
        if key not in seen:
            seen.add(key)
            unique.append(a)

    unique.sort(key=lambda x: x["published"], reverse=True)
    return unique[:max_articles]


def _parse_entry_time(entry):
    for field in ("published_parsed", "updated_parsed"):
        t = entry.get(field)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _strip_html(text):
    return re.sub(r"<[^>]+>", "", text).strip()


def _clean_truncated_content(content):
    """Clean up AI output that was truncated mid-character or mid-article."""
    if not content:
        return content, False

    # 1. Remove Unicode replacement character (broken UTF-8 sequences)
    content = content.replace("\ufffd", "")

    # 2. Remove trailing whitespace
    content = content.strip()

    # 3. Remove incomplete trailing article block (no link after title)
    # Split by article separator 【\d+】
    blocks = re.split(r"\n(?=【\d+】)", content)
    if not blocks:
        return content, False

    cleaned_blocks = []
    was_truncated = False

    for block in blocks:
        # An article block is considered complete if it contains a URL
        has_link = bool(re.search(r"https?://\S+", block))
        has_title = bool(re.search(r"📰", block))

        if has_link:
            cleaned_blocks.append(block)
        elif has_title and not was_truncated:
            # First incomplete article — mark as truncated and stop
            was_truncated = True
        # If no title and no link, skip entirely (garbage)

    result = "\n\n".join(cleaned_blocks).strip()
    return result, was_truncated


def generate_summary(articles, config):
    """Use AI to generate a structured daily digest."""
    ai_cfg = config.get("ai", {})

    api_key = os.environ.get("AI_API_KEY", ai_cfg.get("api_key", ""))
    api_base = os.environ.get(
        "AI_API_BASE", ai_cfg.get("api_base", "https://open.bigmodel.cn/api/coding/paas/v4")
    )
    model = os.environ.get("AI_MODEL", ai_cfg.get("model", "glm-5.1"))

    if not api_key:
        print("  ⚠️ AI_API_KEY not set, using raw article list.")
        return format_article_list(articles)

    # 构建文章列表供 AI 处理
    article_list = "\n\n".join(
        f"文章{idx+1}:\n"
        f"标题: {a['title']}\n"
        f"来源: {a['source']} | 分类: {a['category']}\n"
        f"内容: {a['description'][:300]}\n"
        f"链接: {a['link']}"
        for idx, a in enumerate(articles)
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prompt = f"""你是专业的安全/科技新闻分析师。以下是今日 {len(articles)} 篇 RSS 新闻文章。

请严格按以下格式输出每篇文章的信息。使用【序号】分隔每篇文章，共 {len(articles)} 篇。

—— 格式示例（必须严格照搬）——

【1】
📰 Original English Title Here
📰 中文标题翻译
📝 约50字的中文摘要，精准到位、全面概括文章核心内容
🔗 https://example.com/article

【2】
📰 Another English Title
📰 下一篇中文标题翻译
📝 下一篇约50字的中文摘要
🔗 https://example.com/article2

—— 严格禁止 ——
❌ 禁止使用 * ** # - 等任何 Markdown 语法
❌ 禁止使用 bullet 符号或列表标记
❌ 禁止在每行开头加 - 或 • 或 *
❌ 禁止使用粗体或斜体
❌ 禁止添加额外的分类标题或总结段落

—— 关键要求 ——
✅ 标题保留原始语言（英文），下一行提供中文翻译，两行都以 📰 开头
✅ 摘要用中文，50字左右，到位且全面
✅ 不要遗漏任何文章，共 {len(articles)} 篇
✅ 纯文本输出，绝对不使用 Markdown
✅ 每篇文章严格使用【序号】分隔

---

以下是文章列表：

{article_list}

---

请输出今日（{today}）新闻摘要："""

    try:
        print(f"  [DEBUG] Calling AI: base={api_base}, model={model}")

        url = f"{api_base.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是专业新闻分析师。输出严格按用户指定格式：使用【序号】分隔文章，每篇含 📰 双语标题、📝 50字中文摘要和 🔗 链接。绝对禁止使用任何 Markdown 格式（* ** # - 等），纯文本输出。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 8192,
        }

        r = requests.post(url, headers=headers, json=payload, timeout=120)
        print(f"  [DEBUG] HTTP status: {r.status_code}")

        if r.status_code != 200:
            print(f"  ⚠️ AI API error: {r.status_code} - {r.text[:500]}")
            return format_article_list(articles)

        data = r.json()

        # 从原始 JSON 中提取内容，兼容多种返回格式
        content = None
        if "choices" in data and len(data["choices"]) > 0:
            choice = data["choices"][0]
            msg = choice.get("message", {})
            content = msg.get("content") if isinstance(msg, dict) else str(msg)
            # 检查是否有 reasoning_content 等其他字段
            if not content:
                for key in msg if isinstance(msg, dict) else []:
                    val = msg[key]
                    if isinstance(val, str) and len(val) > 50:
                        content = val
                        break

        # 检查 finish_reason，如果是 length 说明被截断了
        finish_reason = data.get("choices", [{}])[0].get("finish_reason", "")
        print(f"  [DEBUG] finish_reason: {finish_reason}")
        print(f"  [DEBUG] Content length: {len(content) if content else 0} chars")

        if not content or not content.strip():
            print("  ⚠️ AI returned empty, using fallback.")
            return format_article_list(articles)

        # 清理截断内容：去除乱码和不完整的文章块
        content, was_truncated = _clean_truncated_content(content)

        if was_truncated or finish_reason == "length":
            content += "\n\n⚠️ 部分文章因长度限制被截断，完整内容请查看源站链接"

        return content
    except Exception as e:
        print(f"  ⚠️ AI failed: {type(e).__name__}: {e}")
        return format_article_list(articles)


def format_article_list(articles):
    """Fallback: format articles with clean emoji separators and no markdown."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"📡 今日新闻速递 | {today}",
        f"共 {len(articles)} 篇文章",
        "",
    ]

    for idx, a in enumerate(articles, 1):
        desc = a["description"][:150].strip()
        if desc:
            desc = desc + "..."

        lines.append(f"【{idx}】")
        lines.append(f"📰 {a['title']}")
        lines.append(f"📝 {desc}")
        lines.append(f"🔗 {a['link']}")
        lines.append("")

    return "\n".join(lines)


def push_telegram(text, config):
    """Push message to Telegram, auto-splitting at article boundaries."""
    tg_cfg = config.get("telegram", {})

    token = os.environ.get("TELEGRAM_BOT_TOKEN", tg_cfg.get("bot_token", ""))
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", tg_cfg.get("chat_id", ""))

    if not token or not chat_id:
        print("⚠️ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured.")
        return False

    if not text or not text.strip():
        print("⚠️ Message text is empty, skipping Telegram push.")
        return False

    api = f"https://api.telegram.org/bot{token}"

    # 按文章块分片：先按双换行分割文章，再组装成 ≤3800 字符的消息块
    # 3800 留余量避免特殊字符导致超限
    MAX_LEN = 3800

    # Split by article blocks using 【digit+】separator
    blocks = re.split(r"\n\n(?=【\d+】)", text)
    chunks = []
    current = ""

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        # 如果单个块就超限，强制按字符切
        if len(block) > MAX_LEN:
            if current:
                chunks.append(current)
                current = ""
            # 按行切分超长块
            sub = ""
            for line in block.split("\n"):
                if len(sub) + len(line) + 1 > MAX_LEN:
                    if sub:
                        chunks.append(sub)
                    sub = line
                else:
                    sub = sub + "\n" + line if sub else line
            if sub:
                current = sub
        elif len(current) + len(block) + 2 > MAX_LEN:
            chunks.append(current)
            current = block
        else:
            current = current + "\n\n" + block if current else block

    if current:
        chunks.append(current)

    print(f"  Split into {len(chunks)} chunks")

    for i, chunk in enumerate(chunks, 1):
        if not chunk.strip():
            continue
        try:
            r = requests.post(
                f"{api}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk.strip(),
                    "disable_web_page_preview": True,
                },
                timeout=30,
            )
            if r.status_code != 200:
                print(f"  ⚠️ Telegram error [{i}/{len(chunks)}]: {r.text[:200]}")
            else:
                print(f"  ✅ Sent chunk {i}/{len(chunks)} to Telegram")
        except Exception as e:
            print(f"  ⚠️ Push failed [{i}/{len(chunks)}]: {e}")

    return True


def main():
    print("=" * 50)
    print(
        f"🚀 AI News Digest - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    print("=" * 50)

    config = load_config()
    settings = config.get("settings", {})
    hours = settings.get("fetch_hours", 24)
    max_articles = settings.get("max_articles", 30)

    # 1. Fetch RSS
    print(f"\n📡 Fetching RSS feeds (last {hours}h)...")
    articles = fetch_rss(
        config.get("feeds", []), hours=hours, max_articles=max_articles
    )
    print(f"   → {len(articles)} articles found")

    if not articles:
        print("\n⚠️  No new articles. Exiting.")
        return

    # 2. AI Summary
    print("\n🤖 Generating AI summary...")
    summary = generate_summary(articles, config)

    # 3. Push Telegram
    print("\n📨 Pushing to Telegram...")
    push_telegram(summary, config)

    print("\n" + "=" * 50)
    print("✅ Done!")
    print("=" * 50)


if __name__ == "__main__":
    main()
