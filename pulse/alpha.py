"""디젠 알파: 새 체인·TVL 급증 체인·온체인 핫 토큰·국내 커뮤니티/속보/거래소 공지·매체 기사 고르기.

상태(state.json)에 쓰는 키
  chains_known  {"llama:<이름>"|"gt:<id>": 처음 본 시각}   — 새 체인 판정. 소스별로 처음 성공한 회차엔 조용히 기록만
  chain_tvl     [{ts, tvl:{이름: 달러}}]                   — 24시간 전 대비 TVL 급증 판정
  degen_prev    [직전 회차 핫 토큰 key]                     — '새로 뜬' 표시
"""
import math
import re
from datetime import datetime, timedelta

from . import friendly as fr
from .insights import _norm
from .trends import extract, is_crypto

LAUNCH = re.compile(r"mainnet|testnet|메인넷|테스트넷|\bTGE\b|\blayer[ -]?1\b|\bL1\b|new (?:blockchain|chain)|"
                    r"chain launch|launch(?:es|ed)? (?:its |a )?(?:own )?(?:chain|blockchain|network)|"
                    r"신규 체인|자체 체인|블록체인 출시|네트워크 출시|genesis", re.I)
_ALNUM = re.compile(r"[^a-z0-9]+")


def _key(name):
    return _ALNUM.sub("", (name or "").lower())


def _ts(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ 새 체인
def detect_new_chains(llama, gt_networks, known, now):
    """반환 (새 체인 목록, known 에 넣을 항목). 소스가 비었거나(실패) 처음이면 알림 없이 기록만."""
    updates, found = {}, {}
    for prefix, rows, name_of, key_of in (
            ("llama", llama, lambda r: r["name"], lambda r: r["name"]),
            ("gt", gt_networks, lambda r: r["name"], lambda r: r["id"])):
        if not rows:
            continue
        seeded = any(k.startswith(prefix + ":") for k in known)
        for r in rows:
            k = f"{prefix}:{key_of(r)}"
            if k in known or k in updates:
                continue
            updates[k] = now.isoformat()
            if not seeded:
                continue
            nk = _key(name_of(r))
            item = found.setdefault(nk, {"name": name_of(r), "llama": None, "gt": None})
            item[prefix] = r
    # 이미 다른 소스로 알려진 체인이 한쪽에 새로 붙은 경우(예: 디파이라마에 늦게 등록)는 '새 체인'이 아니다
    known_names = {_key(k.split(":", 1)[1]) for k in known}
    known_names |= {_key(r["name"]) for r in (gt_networks or []) if f"gt:{r['id']}" in known}
    known_names |= {_key(r["name"]) for r in (llama or []) if f"llama:{r['name']}" in known}
    new = [v for nk, v in found.items() if nk not in known_names]
    new.sort(key=lambda v: (v["llama"] or {}).get("tvl") or 0, reverse=True)
    return new, updates


def chain_mentions(name, posts, limit=2):
    """수집한 글(뉴스·커뮤니티·SNS) 중 그 체인 이름이 나온 글."""
    if len(_key(name)) < 4:
        return 0, []
    rx = re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", re.I)
    hits = [p for p in posts if rx.search(p.text or "")]
    hits.sort(key=lambda p: p.created.timestamp() if p.created else 0, reverse=True)
    return len(hits), hits[:limit]


def launch_news(posts, seen, now, max_age=24, limit=4):
    """메인넷·새 체인·TGE 소식 (매체·속보·레딧·텔레그램)."""
    out, texts = [], set()
    for p in sorted(posts, key=lambda p: p.created.timestamp() if p.created else 0, reverse=True):
        if p.source not in ("news", "coinness", "reddit", "telegram") or p.key in seen:
            continue
        age = p.age_hours(now)
        if age is None or age > max_age or age < -1:
            continue
        head = p.extra.get("title") or p.text
        if not LAUNCH.search(head):
            continue
        n = _norm(head)
        if n in texts:
            continue
        texts.add(n)
        out.append(p)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ TVL 급증
def tvl_snapshot(llama, min_tvl, cap=200):
    rows = sorted((c for c in llama if c["tvl"] >= min_tvl), key=lambda c: c["tvl"], reverse=True)[:cap]
    return {c["name"]: round(c["tvl"]) for c in rows}


def tvl_movers(llama, history, now, cfg):
    """24시간(20~30시간) 전 스냅샷 대비 TVL 이 크게 늘어난 체인."""
    ccfg = cfg.get("chains", {})
    past = None
    for h in reversed(history or []):
        t = _ts(h.get("ts"))
        if t and timedelta(hours=20) <= now - t <= timedelta(hours=30):
            past = h
            break
    if not past or not llama:
        return []
    out = []
    for c in llama:
        before = past["tvl"].get(c["name"])
        if not before or c["tvl"] < ccfg.get("mover_min_tvl", 5_000_000):
            continue
        pct = (c["tvl"] / before - 1) * 100
        if pct >= ccfg.get("mover_min_pct", 15):
            out.append({**c, "pct": round(pct, 1), "before": before})
    out.sort(key=lambda c: c["pct"], reverse=True)
    return out[:ccfg.get("movers", 4)]


# ------------------------------------------------------------------ 디젠 레이더
def degen_pick(pools, boosted, cfg):
    dcfg = cfg.get("degen", {})
    min_liq, min_vol = dcfg.get("min_liquidity_usd", 50_000), dcfg.get("min_volume_usd", 200_000)
    stable = re.compile(r"^(W?ETH|WBTC|USD.?|DAI|WSOL|SOL|BTC|STETH|WBNB|BNB)$", re.I)

    def good(t):
        return ((t["liquidity"] or 0) >= min_liq and (t["volume_24h"] or 0) >= min_vol
                and not stable.match(t["symbol"] or ""))
    hot, seen = [], set()
    for t in pools:
        if good(t) and _key(t["symbol"]) not in seen:
            seen.add(_key(t["symbol"]))
            hot.append(t)
    hot = hot[:dcfg.get("max_items", 6)]
    shill = [t for t in boosted if good(t) and _key(t["symbol"]) not in seen]
    shill.sort(key=lambda t: t["volume_24h"] or 0, reverse=True)
    return hot, shill[:dcfg.get("boost_items", 3)]


def chain_heat(pools):
    """트렌딩 풀이 몰린 체인 순위 [(체인, 개수)]."""
    c = {}
    for p in pools:
        c[p["chain"]] = c.get(p["chain"], 0) + 1
    return sorted(c.items(), key=lambda x: x[1], reverse=True)


# ------------------------------------------------------------------ 국내
KR_SLANG = {"비트": "BTC", "이더": "ETH", "리플": "XRP", "솔라나": "SOL", "도지": "DOGE"}
BANNED = re.compile(r"세력")  # 이 단어가 들어간 글은 어디에도 싣지 않는다


def clean_posts(posts):
    return [p for p in posts if not BANNED.search((p.text or "") + (p.extra.get("title") or "") + (p.handle or ""))]


def scrub_doc(doc):
    """마지막 안전장치: 번역("forces"→"세력")·AI 요약에서 생긴 단어까지 '큰손'으로 바꾼다."""
    for d in doc:
        for k in ("text", "tg_title", "tg_sub", "title"):
            if isinstance(d.get(k), str):
                d[k] = BANNED.sub("큰손", d[k])
        if "lines" in d:
            d["lines"] = [BANNED.sub("큰손", ln) for ln in d["lines"]]
    return doc


def kr_coin_counts(posts, names, top=6):
    """국내 커뮤니티 글의 코인 언급: 기본 추출 + 업비트 한글 코인명 + 흔한 줄임말. 글 하나당 코인별 1회."""
    table = {**{k: v for k, v in (names or {}).items()}, **KR_SLANG}
    alts = sorted(table, key=len, reverse=True)
    rx = re.compile(r"(?<![가-힣A-Za-z0-9])(" + "|".join(map(re.escape, alts)) + r")(?!사|지사|겟|썸|맥스)") if alts else None
    c, total = {}, 0
    for p in posts:
        if p.source not in ("dcinside", "coinpan"):
            continue
        total += 1
        ents = {e for e in extract(p.text) if not e.startswith("#")}
        if rx:
            ents |= {table[m] for m in rx.findall(p.text or "")}
        for e in ents:
            c[e] = c.get(e, 0) + 1
    return sorted(c.items(), key=lambda x: x[1], reverse=True)[:top], total


def kr_hot(posts, seen, cfg, now):
    kcfg = cfg.get("kr_community", {})
    max_age = kcfg.get("max_age_hours", 36)
    cands = []
    for p in posts:
        if p.source not in ("dcinside", "coinpan") or p.key in seen:
            continue
        age = p.age_hours(now)
        if age is None or age > max_age:
            continue
        views, com = p.extra.get("views", 0), p.extra.get("comments", 0)
        if p.source == "dcinside" and views < kcfg.get("dc_min_views", 150):
            continue
        s = math.log10(views + 1) * 10 + p.likes * 2 + com * 1.5 + min(len(extract(p.text)), 3) * 4
        s -= max(age, 0) * (0.2 if p.source == "coinpan" else 1.0)
        cands.append((s, p))
    cands.sort(key=lambda x: x[0], reverse=True)
    out, per, texts = [], {}, set()
    for s, p in cands:
        n = _norm(p.text)
        if n in texts or per.get(p.source, 0) >= kcfg.get("per_source", 4):
            continue
        texts.add(n)
        per[p.source] = per.get(p.source, 0) + 1
        out.append(p)
        if len(out) >= kcfg.get("show_top", 6):
            break
    return out


def coinness_pick(posts, seen, cfg, now):
    ccfg = cfg.get("coinness", {})
    cands = []
    for p in posts:
        if p.source != "coinness" or p.key in seen:
            continue
        age = p.age_hours(now)
        if age is None or age > ccfg.get("max_age_hours", 4) or age < -1:
            continue
        s = (30 if p.extra.get("important") else 0) + p.extra.get("bull", 0) + p.extra.get("bear", 0)
        s += min(len(extract(p.text)), 3) * 3 - age * 2
        cands.append((s, p))
    cands.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in cands[:ccfg.get("show_top", 5)]]


def upbit_pick(posts, seen, now, max_age=72):
    out = []
    for p in posts:
        if p.source != "upbit" or p.key in seen:
            continue
        age = p.age_hours(now)
        if age is not None and age <= max_age:
            out.append(p)
    out.sort(key=lambda p: p.created.timestamp() if p.created else 0, reverse=True)
    return out[:5]


def news_pick(posts, seen, cfg, now, exclude=()):
    ncfg = cfg.get("news", {})
    skip = {p.key for p in exclude}
    cands = []
    for p in posts:
        if p.source != "news" or p.key in seen or p.key in skip:
            continue
        age = p.age_hours(now)
        if age is None or age > ncfg.get("max_age_hours", 12) or age < -1:
            continue
        if not is_crypto(p.text):
            continue
        s = min(len(extract(p.text)), 4) * 4 - age * 1.2
        if re.search(r"analysis|research|report|why|what|분석|전망|리포트", p.extra.get("title", ""), re.I):
            s += 4
        cands.append((s, p))
    cands.sort(key=lambda x: x[0], reverse=True)
    out, per, texts = [], {}, set()
    for s, p in cands:
        n = _norm(p.extra.get("title", ""))
        if n in texts or per.get(p.author, 0) >= ncfg.get("per_outlet", 2):
            continue
        texts.add(n)
        per[p.author] = per.get(p.author, 0) + 1
        out.append(p)
        if len(out) >= ncfg.get("show_top", 6):
            break
    return out


# ------------------------------------------------------------------ 에어드랍
AIRDROP_WORDS = re.compile(r"airdrop|points? (?:program|farming|campaign|system)|earn(?:ing)? points|"
                           r"\bfarming\b|season \d|\bTGE\b|에어드[랍롭]|포인트 (?:파밍|프로그램)|파밍", re.I)
INSTITUTION = re.compile(r"binance|coinbase|okx|bybit|bitget|kraken|robinhood|blackrock|fidelity|franklin|"
                         r"bitwise|circle|usdd|superstate|wisdomtree|janus|apollo|hamilton lane|securitize|"
                         r"anchorage|paxos|gemini|crypto\.com|upbit|bithumb", re.I)


def _name_rx(name):
    # 흔한 영어 단어 이름(Current·Noon…)은 대소문자를 맞춰야만 인정
    flags = 0 if name.istitle() and len(name) <= 7 else re.I
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", flags)


def _near(rx, text, span=150):
    """이름 바로 근처(앞뒤 150자)에 에어드랍 단어가 있어야 '에어드랍 이야기'로 친다."""
    for m in rx.finditer(text):
        if AIRDROP_WORDS.search(text[max(0, m.start() - span):m.end() + span]):
            return True
    return False


def airdrop_pick(tokenless, posts, cfg, now, shown=None):
    """토큰 없는 프로토콜 중 에어드랍 후보: TVL 규모·유입 속도·커뮤니티 에어드랍 언급·관심 목록."""
    acfg = cfg.get("airdrop", {})
    watch = {w.lower() for w in acfg.get("watch", [])}
    repeat = timedelta(hours=acfg.get("repeat_hours", 24))
    talk = [p for p in posts if AIRDROP_WORDS.search(p.text or "")]
    cands = []
    for t in tokenless:
        name = t["name"] or ""
        if INSTITUTION.search(name) or INSTITUTION.search(t.get("product") or ""):
            continue
        last = _ts((shown or {}).get(name))
        if last and now - last < repeat:
            continue
        rx = _name_rx(name) if len(_key(name)) >= 4 else None
        hits = [p for p in talk if rx and _near(rx, p.text or "")]
        c7 = max(min(t["change_7d"] or 0, 150), -50)
        c30 = max(min(t["change_30d"] or 0, 300), -50)
        s = math.log10(max(t["tvl"], 1)) * 6 + c7 * 0.35 + c30 * 0.1 + min(len(hits), 5) * 8
        if name.lower() in watch:
            s += 15
        if t.get("listed_at") and now.timestamp() - t["listed_at"] < 120 * 86400:
            s += 6  # 최근 등록 = 초기 파밍 구간
        cands.append((s, {**t, "mentions": len(hits), "hits": hits[:2], "watch": name.lower() in watch}))
    cands.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in cands[:acfg.get("max_items", 5)]]


AIRDROP_SHARE = (0.03, 0.06, 0.10)  # 보수·중간·낙관: 전체 물량 중 예치자 몫(통상 3~10%)


def airdrop_estimate(d, ratios, deposit=1000):
    """에어드랍 예상 가치(예치금 deposit 달러 기준).
    가정: 토큰 시총 ≈ 예치금 × (같은 분야 토큰들의 시총/예치금 배수), 그중 3~10%를 예치금 비율대로 나눠 받는다.
    → 받는 가치 = deposit × 배수 × 비중. 배수는 분야 분포의 25%·50%·75% 지점을 보수·중간·낙관에 맞춘다."""
    r, basis = (ratios or {}).get(d.get("category")), d.get("category")
    if not r or r.get("n", 0) < 5:
        r, basis = (ratios or {}).get("_all"), "전체 디파이"
    if not r:
        return None
    low, mid, high = (deposit * r[k] * sh for k, sh in zip(("p25", "p50", "p75"), AIRDROP_SHARE))
    return {"deposit": deposit, "low": low, "mid": mid, "high": high, "ratio": r["p50"], "basis": basis,
            "n": r["n"], "mid_pct": r["p50"] * AIRDROP_SHARE[1] * 100}


def airdrop_reasons(d, detail, hot_chains=(), now=None):
    """'눈여겨볼 이유' — 데이터로 확인되는 것만, 최대 4개."""
    out = []
    raises = (detail or {}).get("raises") or []
    if raises:
        vcs = list(dict.fromkeys(v for r in raises for v in r["lead"] + r["others"]))
        total = detail.get("total_raised") or 0
        head = f"투자금 {fr.dollars(total * 1e6)} 유치" if total else "투자 유치"
        out.append(head + (f"(투자사: {', '.join(vcs[:3])}{' 등' if len(vcs) > 3 else ''})" if vcs else ""))
    if (d.get("tvl") or 0) >= 1e8:
        out.append("맡겨진 돈이 1억 달러가 넘어 이미 큰돈이 들어와 있음")
    if (d.get("change_7d") or 0) >= 15:
        out.append(f"일주일 새 맡겨진 돈 {d['change_7d']:+.0f}% 증가")
    elif (d.get("change_30d") or 0) >= 50:
        out.append(f"한 달 새 맡겨진 돈 {d['change_30d']:+.0f}% 증가")
    if d.get("listed_at") and now and now.timestamp() - d["listed_at"] < 120 * 86400:
        out.append("시작한 지 4개월이 안 된 초기 단계")
    if (detail or {}).get("audits"):
        out.append("외부 보안 점검을 받은 기록 있음")
    hot = [c for c in d.get("chains", []) if c in set(hot_chains)]
    if hot:
        out.append(f"요즘 인기 있는 블록체인({', '.join(fr.chain(c) for c in hot[:2])}) 위에서 운영")
    if d.get("mentions"):
        out.append(f"커뮤니티·언론에서 에어드랍 이야기 {d['mentions']}건")
    if d.get("watch"):
        out.append("코인 투자자들이 오래 주목해 온 대형 프로젝트")
    return out[:4]


def airdrop_guides(guides, now, max_days=21, limit=2):
    out = []
    for g in guides or []:
        t = _ts(g.get("published"))
        if t and now - t <= timedelta(days=max_days):
            out.append(g)
    return out[:limit]
