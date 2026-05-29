#!/usr/bin python3
"""
AI News Digest - RSS → AI Summary → Telegram Push

Daily automated news aggregation powered by GitHub Actions.
"""

import hashlib
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import yaml
from openai import OpenAI


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
        f"[{i+1}] 原始标题: {a['title']}\n"
        f"来源: {a['source']} | 分类: {a['category']}\n"
        f"内容摘要: {a['description'][:300]}\n"
        f"链接: {a['link']}"
        for i, a in enumerate(articles)
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prompt = f"""你是专业的安全/科技新闻分析师。以下是今日 {len(articles)} 篇 RSS 新闻文章。

请严格按以下格式输出每篇文章的信息，每篇文章之间用一个空行分隔：

📰 原始英文标题
📰 中文标题翻译
📝 约50字的中文摘要，要求精准到位、全面概括文章核心内容
🔗 链接

关键要求：
1. 标题必须保留原始语言（英文），并在下一行提供中文翻译
2. 摘要用中文，控制在50字左右，要到位且全面
3. 不要遗漏任何文章，共 {len(articles)} 篇
4. 不要加额外分类或总结，只输出上述格式

---

文章列表：

{article_list}

---

请输出今日（{today}）新闻摘要："""

    try:
        client = OpenAI(api_key=api_key, base_url=api_base)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "你是专业新闻分析师。输出严格按用户指定格式，每篇文章包含双语标题、50字中文摘要和链接。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        content = resp.choices[0].message.content
        print(f"  [DEBUG] AI response length: {len(content) if content else 0} chars")

        if not content or not content.strip():
            print("  ⚠️ AI returned empty, using raw article list.")
            return format_article_list(articles)

        return content
    except Exception as e:
        print(f"  ⚠️ AI failed: {e}")
        return format_article_list(articles)


def format_article_list(articles):
    """Fallback: format articles with bilingual title and link."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"📡 今日新闻速递 | {today}", f"共 {len(articles)} 篇文章", ""]

    for i, a in enumerate(articles, 1):
        desc = a["description"][:100].strip()
        if desc:
            desc = desc + "..."
        lines.append(f"📰 {a['title']}")
        lines.append(f"📝 {desc}")
        lines.append(f"🔗 {a['link']}")
        lines.append("")

    return "\n".join(lines)


def push_telegram(text, config):
    """Push message to Telegram, auto-splitting if needed."""
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
    chunks = _split_text(text, 4096)

    for i, chunk in enumerate(chunks, 1):
        if not chunk.strip():
            continue
        try:
            r = requests.post(
                f"{api}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk,
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


def _split_text(text, limit):
    """Split text at paragraph boundaries."""
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        cut = text.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts


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
