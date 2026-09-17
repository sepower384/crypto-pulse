"""국내 속보·거래소 공지 (키 불필요).

- 코인니스 속보: api.coinness.com 공개 피드. 기사 주소는 coinness.com/news/<id> (앱 번들 라우트 확인, 2026-09-17)
- 업비트 공지(거래 카테고리): 신규 거래지원·거래지원 종료·유의종목 지정
"""
from ..feeds import parse_date
from ..model import Post, SourceResult
from ..net import fetch_json

COINNESS_URL = "https://api.coinness.com/feed/v1/breaking-news?languageCode=ko&limit={n}"
UPBIT_MARKETS = "https://api.upbit.com/v1/market/all"
UPBIT_URL = "https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page={n}&category=trade"


def parse_coinness(data):
    posts = []
    for it in data or []:
        title = (it.get("title") or "").strip()
        if not title or not it.get("id"):
            continue
        posts.append(Post(
            source="coinness", id=str(it["id"]), author="코인니스", handle="coinness",
            text=title + "\n" + (it.get("content") or "").strip(),
            url=f"https://coinness.com/news/{it['id']}", created=parse_date(it.get("publishAt")),
            likes=int(it.get("bullCount") or it.get("bull") or 0),
            extra={"title": title, "bull": int(it.get("bullCount") or it.get("bull") or 0),
                   "bear": int(it.get("bearCount") or it.get("bear") or 0),
                   "important": bool(it.get("isImportant")), "codes": it.get("originCodes") or []},
        ))
    return posts


def upbit_kind(title):
    if "거래지원 종료" in title:
        return "delist"
    if "유의 종목" in title or "유의종목" in title:
        return "warning"
    if "신규 거래지원" in title or "디지털 자산 추가" in title:
        return "listing"
    return ""


def parse_upbit(data):
    posts = []
    for it in ((data or {}).get("data") or {}).get("notices", []):
        title = (it.get("title") or "").strip()
        kind = upbit_kind(title)
        if not kind:
            continue
        posts.append(Post(
            source="upbit", id=str(it["id"]), author="업비트", handle="upbit", text=title,
            url=f"https://upbit.com/service_center/notice?id={it['id']}",
            created=parse_date(it.get("first_listed_at") or it.get("listed_at")),
            extra={"title": title, "kind": kind},
        ))
    return posts


def parse_markets(data):
    """업비트 마켓 목록 → {한글이름: 심볼}. 국내 커뮤니티 글에서 코인 언급을 셀 때 쓴다.
    두 글자 이름(빔·세이·보라…)은 일반 단어와 겹쳐서 뺀다."""
    names = {}
    for m in data or []:
        ko, market = (m.get("korean_name") or "").strip(), m.get("market") or ""
        if len(ko) >= 3 and "-" in market:
            names.setdefault(ko, market.split("-", 1)[1])
    return names


def collect(cfg):
    posts, notes, fails = [], [], []
    names = {}
    try:
        names = parse_markets(fetch_json(UPBIT_MARKETS, retries=1))
    except Exception:  # noqa: BLE001  (없으면 기본 별칭으로만 센다)
        pass
    try:
        got = parse_coinness(fetch_json(COINNESS_URL.format(n=cfg.get("coinness", {}).get("limit", 40)), retries=1))
        posts += got
        notes.append(f"코인니스 {len(got)}")
    except Exception as e:  # noqa: BLE001
        fails.append(f"코인니스({type(e).__name__})")
    try:
        got = parse_upbit(fetch_json(UPBIT_URL.format(n=20), retries=1))
        posts += got
        notes.append(f"업비트 공지 {len(got)}")
    except Exception as e:  # noqa: BLE001
        fails.append(f"업비트({type(e).__name__})")
    return SourceResult("kr_news", len(fails) < 2, posts,
                        " · ".join(notes) + (f", 실패 {', '.join(fails)}" if fails else ""),
                        extra={"upbit_names": names})
