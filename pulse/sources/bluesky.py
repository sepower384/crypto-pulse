"""블루스카이 공개 AppView — getAuthorFeed 는 키 없이 200 (searchPosts 는 403)."""
from urllib.parse import quote

from ..feeds import parse_date
from ..model import Post, SourceResult
from ..net import fetch_json

API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"


def parse(data, name):
    posts = []
    for item in data.get("feed", []):
        if item.get("reason"):          # 리포스트 제외
            continue
        p = item.get("post", {})
        rec = p.get("record", {})
        if rec.get("reply"):            # 답글 제외
            continue
        handle = p.get("author", {}).get("handle", "")
        rkey = p.get("uri", "").rsplit("/", 1)[-1]
        posts.append(Post(
            source="bluesky", id=p.get("uri", rkey), author=name, handle=handle,
            text=rec.get("text", ""), url=f"https://bsky.app/profile/{handle}/post/{rkey}",
            created=parse_date(rec.get("createdAt")), likes=p.get("likeCount", 0),
            reposts=p.get("repostCount", 0),
        ))
    return posts


def collect(cfg):
    posts, fails = [], []
    for h in cfg.get("bluesky", {}).get("handles", []):
        try:
            data = fetch_json(f"{API}?actor={quote(h['handle'])}&limit=15&filter=posts_no_replies")
            posts += parse(data, h.get("name", h["handle"]))
        except Exception as e:  # noqa: BLE001
            fails.append(f"{h['handle']}({type(e).__name__})")
    ok = not fails or bool(posts)
    note = f"{len(posts)}건" + (f", 실패 {', '.join(fails)}" if fails else "")
    return SourceResult("bluesky", ok, posts, note)
