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
        "AI_API_BASE", ai_cfg.get("api_base", "https://open.bigmodel.cn/api/paas/v4")
    )
    # 支持 env 覆盖，fallback 到 config，再 fallback 到 glm-4-flash
    model = os.environ.get("AI_MODEL", ai_cfg.get("model", "glm-4-flash"))

    if not api_key:
        return "⚠️ AI_API_KEY not configured. Skipping AI summary."

    # 调试：打印实际使用的参数（隐藏 key 中间部分）
    masked_key = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
    print(f"  API Base: {api_base}")
    print(f"  Model:    {model}")
    print(f"  API Key:  {masked_key}")

    client = OpenAI(api_key=api_key, base_url=api_base)

    article_text = "\n".join(
        f"[{i+1}] [{a['source']}] {a['title']}\n    {a['description'][:200]}"
        for i, a in enumerate(articles)
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prompt = f"""你是专业的安全/科技新闻分析师。请为以下 {len(articles)} 篇今日 RSS 文章生成结构化中文摘要。

要求：
1. 按主题分类（安全、AI、科技等）
2. 每篇用 1-2 句话概括核心，标注重要性（🔴重要 / 🟡关注 / ⚪一般）
3. 文末 2-3 句「今日趋势」总结
4. 格式用 emoji + 纯文本，不用 Markdown 语法
5. 每篇文章附原链接

---
{article_text}
---

请输出今日（{today}）新闻摘要："""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "你是专业新闻分析师，擅长提炼关键信息生成结构化摘要。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"⚠️ AI summary failed: {e}\n\n共 {len(articles)} 篇文章待处理。"


def push_telegram(text, config):
    """Push message to Telegram, auto-splitting if needed."""
    tg_cfg = config.get("telegram", {})

    token = os.environ.get("TELEGRAM_BOT_TOKEN", tg_cfg.get("bot_token", ""))
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", tg_cfg.get("chat_id", ""))

    if not token or not chat_id:
        print("⚠️ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured.")
        return False

    api = f"https://api.telegram.org/bot{token}"
    chunks = _split_text(text, 4096)

    for i, chunk in enumerate(chunks, 1):
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
