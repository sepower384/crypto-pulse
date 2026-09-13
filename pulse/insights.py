"""유명인사 글 중 '새로 나온 + 크립토 관련 + 반응 큰' 것만 고른다."""
import math
import re

from .trends import extract, is_crypto

_NORM = re.compile(r"[^a-z0-9가-힣]+")


def _norm(text):
    return _NORM.sub("", (text or "").lower())[:80]


def score(post, now=None):
    age = post.age_hours(now) or 0
    s = math.log10(post.likes + 1) * 10 + math.log10(post.reposts + 1) * 6
    s += min(len(extract(post.text)), 4) * 5
    if post.source == "blog":
        s += 25                      # 장문 에세이는 드물고 밀도가 높다
    if post.extra.get("quote"):
        s += 3
    return round(s - age * 0.6, 2)


def select(posts, seen, cfg, now=None):
    xcfg, icfg = cfg["x"], cfg["insights"]
    per_handle = {}
    picked, texts = [], set()
    cands = []
    for p in posts:
        if p.source not in ("x", "bluesky", "blog") or p.key in seen:
            continue
        age = p.age_hours(now)
        limit = icfg["blog_max_age_hours"] if p.source == "blog" else xcfg["max_age_hours"]
        if age is None or age > limit or age < -1:
            continue
        body = p.text.strip()
        if p.source != "blog":
            if len(re.sub(r"https?://\S+", "", body)) < 25 and p.likes < 5000:
                continue
            if p.extra.get("require_crypto") and not is_crypto(body):
                continue
        cands.append((score(p, now), p))
    cands.sort(key=lambda x: x[0], reverse=True)
    for s, p in cands:
        n = _norm(p.text)
        if n in texts:                  # 같은 글을 X/블루스카이에 동시 게시한 경우
            continue
        hk = p.handle.lower()
        if per_handle.get(hk, 0) >= xcfg["per_handle_limit"]:
            continue
        per_handle[hk] = per_handle.get(hk, 0) + 1
        texts.add(n)
        p.extra["score"] = s
        picked.append(p)
        if len(picked) >= icfg["max_items"]:
            break
    return picked


def reddit_top(posts, seen, cfg):
    rows = sorted((p for p in posts if p.source == "reddit" and p.key not in seen), key=lambda p: p.rank)
    return rows[: cfg["reddit"]["show_top"]]
