#!/usr/bin python3
"""
AI News Digest - RSS → AI Summary → Telegram Push

Continuous monitoring powered by GitHub Actions.
Runs every 15 minutes, only pushes new articles (GUID-based dedup).
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

SEEN_FILE = "seen.json"


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_seen(path=SEEN_FILE):
    """Load previously processed article GUIDs from disk."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "ids" in data:
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"ids": {}, "updated": ""}


def save_seen(seen, path=SEEN_FILE):
    """Save processed article GUIDs to disk."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)


def cleanup_seen(seen, max_age_days=7):
    """Remove entries older than max_age_days to keep file small."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    seen["ids"] = {
        guid: ts
        for guid, ts in seen["ids"].items()
        if ts > cutoff
    }
    seen["updated"] = datetime.now(timezone.utc).isoformat()
    return seen


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
                    # Prefer RSS <guid> or <id>, fallback to link, fallback to title hash
                    guid = entry.get("id") or entry.get("guid") or entry.get("link", "")
                    if not guid:
                        guid = hashlib.md5(
                            entry.get("title", "").encode()
                        ).hexdigest()
                    articles.append(
                        {
                            "guid": guid,
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

    # Deduplicate by GUID (in-memory, within this run)
    seen = set()
    unique = []
    for a in articles:
        if a["guid"] not in seen:
            seen.add(a["guid"])
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

    content = content.replace("\ufffd", "")
    content = content.strip()

    blocks = re.split(r"\n(?=【\d+】)", content)
    if not blocks:
        return content, False

    cleaned_blocks = []
    was_truncated = False

    for block in blocks:
        has_link = bool(re.search(r"https?://\S+", block))
        has_title = bool(re.search(r"📰", block))

        if has_link:
            cleaned_blocks.append(block)
        elif has_title and not was_truncated:
            was_truncated = True

    result = "\n\n".join(cleaned_blocks).strip()
    return result, was_truncated


def _call_ai_api(api_base, api_key, model, system_prompt, user_prompt, read_timeout=300):
    """Call OpenAI-compatible chat completions API with timeout."""
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 8192,
    }

    r = requests.post(url, headers=headers, json=payload, timeout=(30, read_timeout))
    print(f"  [DEBUG] HTTP status: {r.status_code}")

    if r.status_code != 200:
        raise RuntimeError(f"API returned {r.status_code}: {r.text[:500]}")

    data = r.json()

    content = None
    if "choices" in data and len(data["choices"]) > 0:
        choice = data["choices"][0]
        msg = choice.get("message", {})
        content = msg.get("content") if isinstance(msg, dict) else str(msg)
        if not content:
            for key in msg if isinstance(msg, dict) else []:
                val = msg[key]
                if isinstance(val, str) and len(val) > 50:
                    content = val
                    break

    finish_reason = data.get("choices", [{}])[0].get("finish_reason", "")
    print(f"  [DEBUG] finish_reason: {finish_reason}")
    print(f"  [DEBUG] Content length: {len(content) if content else 0} chars")

    return content, finish_reason


def generate_summary(articles, config):
    """Use AI to generate a structured daily digest with retry on timeout."""
    ai_cfg = config.get("ai", {})

    api_key = os.environ.get("AI_API_KEY", ai_cfg.get("api_key", ""))
    api_base = os.environ.get(
        "AI_API_BASE", ai_cfg.get("api_base", "https://open.bigmodel.cn/api/coding/paas/v4")
    )
    model = os.environ.get("AI_MODEL", ai_cfg.get("model", "glm-5.1"))

    if not api_key:
        print("  ⚠️ AI_API_KEY not set, using raw article list.")
        return format_article_list(articles)

    article_list = "\n\n".join(
        f"文章{idx+1}:\n"
        f"标题: {a['title']}\n"
        f"来源: {a['source']} | 分类: {a['category']}\n"
        f"内容: {a['description'][:300]}\n"
        f"链接: {a['link']}"
        for idx, a in enumerate(articles)
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    system_prompt = (
        "你是专业新闻分析师。输出严格按用户指定格式："
        "使用【序号】分隔文章，每篇含 📰 双语标题、📝 50字中文摘要和 🔗 链接。"
        "绝对禁止使用任何 Markdown 格式（* ** # - 等），纯文本输出。"
    )

    # ── 第一次尝试：完整 prompt ──
    full_prompt = f"""你是专业的安全/科技新闻分析师。以下是今日 {len(articles)} 篇 RSS 新闻文章。

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

    # ── 第二次尝试：精简 prompt ──
    simple_prompt = f"""你是专业新闻分析师。以下是 {len(articles)} 篇 RSS 文章。

请为每篇文章按以下格式输出（共 {len(articles)} 篇）：

【序号】
📰 原始英文标题
📰 中文标题翻译
📝 约50字中文摘要
🔗 文章链接

禁止使用 * ** # - 等 Markdown 符号。纯文本输出。

文章列表：

{article_list}"""

    for attempt in (1, 2):
        prompt = full_prompt if attempt == 1 else simple_prompt
        label = "full" if attempt == 1 else "simplified"
        print(f"  [DEBUG] Attempt {attempt}/2 ({label} prompt)")

        try:
            content, finish_reason = _call_ai_api(
                api_base, api_key, model,
                system_prompt, prompt,
                read_timeout=300,
            )

            if not content or not content.strip():
                print(f"  ⚠️ AI returned empty on attempt {attempt}.")
                if attempt == 1:
                    print("  Retrying with simplified prompt...")
                    continue
                return format_article_list(articles)

            content, was_truncated = _clean_truncated_content(content)
            if was_truncated or finish_reason == "length":
                content += "\n\n⚠️ 部分文章因长度限制被截断，完整内容请查看源站链接"

            return content

        except Exception as e:
            error_type = type(e).__name__
            print(f"  ⚠️ Attempt {attempt} failed: {error_type}: {e}")

            if attempt == 1:
                print("  Retrying with simplified prompt...")
                continue
            else:
                print(f"  ⚠️ Both attempts failed, using fallback list.")
                return format_article_list(articles)

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

    MAX_LEN = 3800

    blocks = re.split(r"\n\n(?=【\d+】)", text)
    chunks = []
    current = ""

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if len(block) > MAX_LEN:
            if current:
                chunks.append(current)
                current = ""
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
    hours = int(os.environ.get("FETCH_HOURS", settings.get("fetch_hours", 1)))
    max_articles = int(os.environ.get("MAX_ARTICLES", settings.get("max_articles", 30)))

    # 1. Load seen GUIDs from previous runs
    seen = load_seen()
    print(f"\n📚 Loaded {len(seen['ids'])} previously seen articles.")

    # 2. Fetch RSS
    print(f"\n📡 Fetching RSS feeds (last {hours}h)...")
    articles = fetch_rss(
        config.get("feeds", []), hours=hours, max_articles=max_articles
    )
    print(f"   → {len(articles)} articles found in time window")

    if not articles:
        print("\n⚠️  No articles in time window. Exiting.")
        return

    # 3. Filter out already-seen articles (cross-run dedup)
    new_articles = []
    skipped = 0
    now_ts = datetime.now(timezone.utc).isoformat()
    for a in articles:
        if a["guid"] in seen["ids"]:
            skipped += 1
        else:
            new_articles.append(a)
            seen["ids"][a["guid"]] = now_ts

    print(f"   → {len(new_articles)} new / {skipped} already seen")

    if not new_articles:
        print("\n✅ No new articles since last check. Exiting.")
        return

    # 4. AI Summary (only for new articles)
    print(f"\n🤖 Generating AI summary for {len(new_articles)} new articles...")
    summary = generate_summary(new_articles, config)

    # 5. Push Telegram
    print("\n📨 Pushing to Telegram...")
    push_telegram(summary, config)

    # 6. Cleanup old entries and save seen.json
    seen = cleanup_seen(seen, max_age_days=7)
    save_seen(seen)
    print(f"\n💾 Saved {len(seen['ids'])} entries to {SEEN_FILE} (expired entries cleaned)")

    print("\n" + "=" * 50)
    print("✅ Done!")
    print("=" * 50)


if __name__ == "__main__":
    main()
