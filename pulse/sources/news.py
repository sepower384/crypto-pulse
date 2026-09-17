"""해외·국내 크립토 매체 RSS (키 불필요). 실측 200: CoinDesk·The Block·Decrypt·Cointelegraph·The Defiant·블록미디어·토큰포스트."""
from ..feeds import parse_feed
from ..model import Post, SourceResult
from ..net import fetch_text


def parse(xml_text, name, lang="en"):
    posts = []
    for it in parse_feed(xml_text):
        if not it["title"] or not it["link"]:
            continue
        posts.append(Post(
            source="news", id=it["id"] or it["link"], author=name, handle=name,
            text=it["title"] + "\n" + it["summary"][:400], url=it["link"], created=it["published"],
            extra={"title": it["title"], "lang": lang},
        ))
    return posts


def collect(cfg):
    posts, fails = [], []
    feeds = cfg.get("news", {}).get("feeds", [])
    for f in feeds:
        try:
            posts += parse(fetch_text(f["url"], retries=1), f["name"], f.get("lang", "en"))
        except Exception as e:  # noqa: BLE001
            fails.append(f"{f['name']}({type(e).__name__})")
    ok = len(fails) < len(feeds) or not feeds
    note = f"{len(feeds) - len(fails)}/{len(feeds)}곳 {len(posts)}건" + (f", 실패 {', '.join(fails[:4])}" if fails else "")
    return SourceResult("news", ok, posts, note)
