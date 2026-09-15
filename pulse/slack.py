"""슬랙 Incoming Webhook 전송 (Block Kit). 섹션 텍스트 3000자 제한을 지킨다."""
import os

from .net import fetch

MAX_SECTION = 2900


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def link(url, label):
    return f"<{url}|{esc(label).replace('|', '¦')}>"


def fmt_num(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def section(text):
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:MAX_SECTION]}}


def chunked_sections(title, lines):
    """제목 + 줄 목록을 3000자 안 넘게 여러 섹션으로 쪼갠다."""
    blocks, buf = [], f"*{title}*"
    for ln in lines:
        if len(buf) + len(ln) + 1 > MAX_SECTION:
            blocks.append(section(buf))
            buf = ""
        buf += "\n" + ln
    if buf.strip():
        blocks.append(section(buf))
    return blocks


def blocks_from_doc(doc):
    """브리핑 문서(main.build_message 의 doc) → Block Kit 블록."""
    blocks = []
    for d in doc:
        kind = d["kind"]
        if kind == "header":
            blocks.append({"type": "header", "text": {"type": "plain_text", "text": d["text"][:150]}})
        elif kind == "text":
            blocks.append(section(d["text"]))
        elif kind == "section":
            blocks += chunked_sections(d["title"], d["lines"])
        elif kind == "divider":
            blocks.append({"type": "divider"})
        elif kind == "context":
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": d["text"][:2900]}]})
    return blocks


def send(blocks, fallback_text, webhook=None):
    webhook = webhook or os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return False, "SLACK_WEBHOOK_URL 없음"
    payload = {"text": fallback_text, "blocks": blocks[:50], "unfurl_links": False, "unfurl_media": False}
    try:
        res = fetch(webhook, data=payload, retries=2, backoff=3).decode("utf-8", "replace")
        return res.strip() == "ok", res[:100]
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:100]}"
