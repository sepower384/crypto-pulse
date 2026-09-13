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
        if p.source not in ("x", "bluesky", "farcaster", "blog") or p.key in seen:
            continue
        age = p.age_hours(now)
        # 블루스카이·파캐스터 인물은 2~4일에 한 번 올린다 → X 보다 넓은 창 (보낸 글은 seen 으로 중복 차단)
        limit = {"blog": icfg["blog_max_age_hours"],
                 "x": xcfg["max_age_hours"]}.get(p.source, icfg.get("social_max_age_hours", xcfg["max_age_hours"]))
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
        # 같은 글을 X/블루스카이/파캐스터에 동시 게시한 경우. 블루스카이는 300자에서 잘려서
        # 전체 비교가 빗나가므로 같은 인물이면 앞 30자만 봐도 같은 글로 친다.
        same_author = (p.author.lower(), n[:30])
        if n in texts or same_author in texts:
            continue
        hk = p.handle.lower()
        if per_handle.get(hk, 0) >= xcfg["per_handle_limit"]:
            continue
        per_handle[hk] = per_handle.get(hk, 0) + 1
        texts.update((n, same_author))
        p.extra["score"] = s
        picked.append(p)
        if len(picked) >= icfg["max_items"]:
            break
    return picked


def channel_news(posts, seen, cfg, now=None, exclude=()):
    """텔레그램 속보·온체인 채널: 최근 글 중 조회수 높은 것. 인물 인사이트와 같은 글이면 뺀다."""
    tcfg = cfg.get("telegram", {})
    max_age, per_ch = tcfg.get("max_age_hours", 12), tcfg.get("per_channel_limit", 2)
    texts = {_norm(p.text) for p in exclude}
    cands = []
    for p in posts:
        if p.source != "telegram" or p.key in seen:
            continue
        age = p.age_hours(now)
        if age is None or age > max_age or age < -1:
            continue
        if p.extra.get("require_crypto") and not is_crypto(p.text):
            continue
        s = math.log10(p.extra.get("views", 0) + 1) * 10 + min(len(extract(p.text)), 4) * 3 - age * 1.5
        cands.append((s, p))
    cands.sort(key=lambda x: x[0], reverse=True)
    picked, per = [], {}
    for s, p in cands:
        n, hk = _norm(p.text), p.handle.lower()
        if n in texts or per.get(hk, 0) >= per_ch:
            continue
        texts.add(n)
        per[hk] = per.get(hk, 0) + 1
        p.extra["score"] = round(s, 2)
        picked.append(p)
        if len(picked) >= tcfg.get("max_items", 5):
            break
    return picked


def reddit_top(posts, seen, cfg):
    rows = sorted((p for p in posts if p.source == "reddit" and p.key not in seen), key=lambda p: p.rank)
    return rows[: cfg["reddit"]["show_top"]]
