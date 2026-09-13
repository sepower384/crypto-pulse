"""유명인사 장문 블로그/에세이 RSS. 트윗보다 느리지만 '인사이트' 밀도가 제일 높다."""
import hashlib

from ..feeds import parse_feed
from ..model import Post, SourceResult
from ..net import fetch_text


def parse(xml_text, name):
    posts = []
    for it in parse_feed(xml_text):
        pid = hashlib.sha1((it["link"] or it["id"]).encode()).hexdigest()[:16]
        posts.append(Post(
            source="blog", id=pid, author=name, handle=name,
            text=it["title"] + ("\n" + it["summary"][:500] if it["summary"] else ""),
            url=it["link"], created=it["published"], extra={"title": it["title"]},
        ))
    return posts


def collect(cfg):
    posts, fails = [], []
    for b in cfg.get("blogs", []):
        try:
            posts += parse(fetch_text(b["url"]), b["name"])
        except Exception as e:  # noqa: BLE001
            fails.append(f"{b['name']}({type(e).__name__})")
    note = f"{len(posts)}건" + (f", 실패 {', '.join(fails)}" if fails else "")
    return SourceResult("blogs", not fails or bool(posts), posts, note)
