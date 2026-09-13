"""텔레그램 공개채널 웹 미리보기(t.me/s/<채널>) — 키·로그인 없이 최근 약 20개 메시지.

실측(2026-09-13, 이 PC): wublockchainenglish·lookonchainchannel·WatcherGuru 전부 200, 당일 글까지 나옴.
X 쿠키가 없어도 룩온체인·우블록체인 같은 속보 계정은 여기서 받는다.
"""
import html
import re

from ..feeds import parse_date
from ..model import Post, SourceResult
from ..net import fetch_text

URL = "https://t.me/s/{}"
_WRAP = "tgme_widget_message_wrap"
_POST = re.compile(r'data-post="([^"]+)"')
# 답글 인용(js-message_reply_text)이 아닌 본문(js-message_text)만
_TEXT = re.compile(r'<div class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>', re.S)
_VIEWS = re.compile(r'<span class="tgme_widget_message_views">([^<]+)</span>')
_DATE = re.compile(r'<time datetime="([^"]+)"')
_BR = re.compile(r"<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")


def _text(fragment):
    s = _TAG.sub("", _BR.sub("\n", fragment))
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def parse_views(s):
    s = (s or "").strip().upper().replace(",", "")
    mult = {"K": 1_000, "M": 1_000_000}.get(s[-1:], 1)
    try:
        return int(float(s[:-1] if mult > 1 else s) * mult)
    except ValueError:
        return 0


def parse(page, channel, name):
    posts = []
    for blk in page.split(_WRAP)[1:]:
        m_post, m_text = _POST.search(blk), _TEXT.search(blk)
        if not m_post or not m_text:      # 사진·영상만 있는 메시지
            continue
        text = _text(m_text.group(1))
        if not text:
            continue
        m_views, m_date = _VIEWS.search(blk), _DATE.search(blk)
        pid = m_post.group(1)             # "channel/123"
        posts.append(Post(
            source="telegram", id=pid, author=name, handle=channel, text=text,
            url=f"https://t.me/{pid}", created=parse_date(m_date.group(1)) if m_date else None,
            extra={"views": parse_views(m_views.group(1)) if m_views else 0},
        ))
    return posts


def collect(cfg):
    posts, fails = [], []
    for c in cfg.get("telegram", {}).get("channels", []):
        try:
            got = parse(fetch_text(URL.format(c["channel"]), retries=1), c["channel"], c.get("name", c["channel"]))
            for p in got:
                p.extra["require_crypto"] = bool(c.get("require_crypto"))
            if not got:
                fails.append(f"{c['channel']}(0건)")
            posts += got
        except Exception as e:  # noqa: BLE001
            fails.append(f"{c['channel']}({type(e).__name__})")
    total = len(cfg.get("telegram", {}).get("channels", []))
    ok = len(fails) < total or not total
    note = f"{total - len(fails)}/{total}채널 {len(posts)}건" + (f", 실패 {', '.join(fails[:5])}" if fails else "")
    return SourceResult("telegram", ok, posts, note)
