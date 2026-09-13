"""파캐스터 — Warpcast 클라이언트 API(client.warpcast.com/v2), 키 불필요.

실측(2026-09-13, 이 PC): user-by-username → fid, casts?fid= → 최근 캐스트(좋아요·리캐스트 수 포함) 200.
비탈릭·댄 로메로·제시 폴락·린다 셰 등 X 에도 쓰는 인물이 여기에도 글을 올린다.
"""
from datetime import datetime, timezone
from urllib.parse import quote

from ..model import Post, SourceResult
from ..net import fetch_json

API = "https://client.warpcast.com/v2"


def parse(data, username, name):
    posts = []
    for c in (data.get("result") or {}).get("casts", []):
        if c.get("parentHash"):           # 답글 제외 (채널 게시는 parentUrl 이라 통과)
            continue
        author = (c.get("author") or {}).get("username", username)
        if author.lower() != username.lower():  # 리캐스트로 딸려온 남의 글
            continue
        h, ts = c.get("hash", ""), c.get("timestamp")
        if not h or not c.get("text", "").strip():
            continue
        posts.append(Post(
            source="farcaster", id=h, author=name, handle=author, text=c["text"],
            url=f"https://farcaster.xyz/{author}/{h[:10]}",
            created=datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None,
            likes=(c.get("reactions") or {}).get("count", 0),
            reposts=(c.get("recasts") or {}).get("count", 0),
            extra={"views": c.get("viewCount", 0)},
        ))
    return posts


def collect(cfg):
    posts, fails = [], []
    users = cfg.get("farcaster", {}).get("users", [])
    for u in users:
        name = u.get("name", u["username"])
        try:
            fid = u.get("fid") or fetch_json(
                f"{API}/user-by-username?username={quote(u['username'])}", retries=1)["result"]["user"]["fid"]
            got = parse(fetch_json(f"{API}/casts?fid={fid}&limit=15", retries=1), u["username"], name)
            for p in got:
                p.extra["require_crypto"] = bool(u.get("require_crypto"))
            posts += got
        except Exception as e:  # noqa: BLE001
            fails.append(f"{u['username']}({type(e).__name__})")
    ok = len(fails) < len(users) or not users
    note = f"{len(users) - len(fails)}/{len(users)}명 {len(posts)}건" + (f", 실패 {', '.join(fails[:5])}" if fails else "")
    return SourceResult("farcaster", ok, posts, note)
