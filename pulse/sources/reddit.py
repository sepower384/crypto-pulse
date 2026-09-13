"""레딧: 여러 서브를 `r/A+B+C` 로 묶어 RSS 1회 호출.

실측(2026-09-13): 서브별로 연달아 부르면 두 번째부터 429. 묶음 1회는 200·50건.
.json 엔드포인트는 403 이라 RSS 만 쓴다. RSS 에는 추천수가 없어서 top 순위(rank)를 인기 지표로 쓴다.
"""
import re

from ..feeds import parse_feed
from ..model import Post, SourceResult
from ..net import fetch_text

_ID = re.compile(r"/comments/([a-z0-9]+)/")


def build_url(subs, sort="top", limit=50):
    q = "t=day&" if sort == "top" else ""
    return f"https://www.reddit.com/r/{'+'.join(subs)}/{sort}.rss?{q}limit={limit}"


def parse(xml_text, sort="top"):
    posts = []
    for i, it in enumerate(parse_feed(xml_text), start=1):
        m = _ID.search(it["link"])
        pid = m.group(1) if m else it["id"]
        sub = it["category"] or "reddit"
        body = it["summary"]
        # RSS 본문에 붙는 "submitted by /u/x to r/y [link] [comments]" 꼬리 제거
        body = re.sub(r"submitted by\s+/u/\S+\s+to\s+r/\S+.*$", "", body).strip()
        posts.append(Post(
            source="reddit", id=pid, author=it["author"].replace("/u/", "u/"),
            handle=f"r/{sub}", text=it["title"] + ("\n" + body[:400] if body else ""),
            url=it["link"], created=it["published"], rank=i, extra={"sort": sort, "title": it["title"]},
        ))
    return posts


def collect(cfg):
    subs = cfg["reddit"]["subreddits"]
    try:
        xml_text = fetch_text(build_url(subs, "top", cfg["reddit"].get("top_limit", 50)), retries=3, backoff=10)
        posts = parse(xml_text, "top")
        return SourceResult("reddit", True, posts, f"{len(posts)}건")
    except Exception as e:  # noqa: BLE001 - 소스 하나 실패가 전체를 막으면 안 됨
        return SourceResult("reddit", False, [], f"실패: {type(e).__name__} {str(e)[:60]}")
