"""언급량 트렌드: 코인·내러티브 언급을 세고, 지난 N회 평균 대비 급상승을 잡는다.

소스 가용성이 매번 달라서(X 쿠키 만료, 레딧 429 등) 절대 횟수가 아니라
'이번 회차 글 수 대비 비율(share)'로 비교한다.
"""
import re
from collections import Counter

# symbol: (이름 별칭들, strict)  strict=True 면 영어 일반단어와 겹쳐서 $캐시태그로만 센다
COINS = {
    "BTC": (["bitcoin", "비트코인"], False),
    "ETH": (["ethereum", "ether", "이더리움", "이더"], False),
    "SOL": (["solana", "솔라나"], False),
    "XRP": (["ripple", "리플"], False),
    "BNB": (["binance coin"], False),
    "DOGE": (["dogecoin", "도지코인"], False),
    "ADA": (["cardano", "에이다"], False),
    "HYPE": (["hyperliquid", "하이퍼리퀴드"], True),
    "ZEC": (["zcash", "지캐시"], False),
    "XMR": (["monero"], False),
    "TON": (["toncoin"], True),
    "SUI": (["수이"], False),
    "AVAX": (["avalanche", "아발란체"], False),
    "LINK": (["chainlink", "체인링크"], True),
    "DOT": (["polkadot"], True),
    "LTC": (["litecoin"], False),
    "TRX": (["tron", "트론"], False),
    "PEPE": (["페페"], False),
    "SHIB": (["shiba inu", "시바이누"], False),
    "ARB": (["arbitrum"], False),
    "OP": (["optimism"], True),
    "TAO": (["bittensor"], True),
    "ENA": (["ethena", "에테나"], False),
    "ONDO": ([], False),
    "PUMP": (["pump.fun", "pumpfun"], True),
    "WLFI": (["world liberty"], False),
    "USDT": (["tether", "테더"], False),
    "USDC": ([], False),
    "MSTR": (["microstrategy"], False),
    "COIN": (["coinbase"], True),
    "PENGU": (["pudgy penguins"], False),
    "VIRTUAL": (["virtuals protocol"], False),
    "BONK": (["봉크"], False),
    "WIF": (["dogwifhat"], True),
    "FARTCOIN": (["fartcoin"], False),
    "ZORA": ([], True),
    "BERA": (["berachain", "베라체인"], False),
    "MON": (["monad", "모나드"], True),
    "PLUME": ([], True),
}

NARRATIVES = {
    "ETF": r"\betfs?\b",
    "스테이블코인": r"stablecoin|스테이블",
    "해킹·익스플로잇": r"\bhack(ed|s)?\b|exploit|drained|해킹",
    "규제·SEC": r"\bsec\b|regulat|genius act|clarity act|규제|gensler|atkins",
    "에어드랍": r"airdrop|에어드랍",
    "청산·레버리지": r"liquidat|청산|leverage|short squeeze",
    "밈코인": r"memecoin|meme coin|밈코인",
    "AI 에이전트": r"\bai agents?\b|agentic",
    "RWA·토큰화": r"\brwa\b|tokeniz",
    "금리·연준": r"\bfed\b|rate cut|rate hike|fomc|powell|금리",
    "트럼프": r"trump|트럼프",
    "비트코인 비축": r"strategic (bitcoin )?reserve|비축",
    "기업 트레저리": r"treasury compan|digital asset treasur|\bdat\b",
    "양자컴퓨터": r"quantum|양자",
    "L2·롤업": r"\brollups?\b|\blayer ?2\b|\bl2s?\b",
    "메인넷·새 체인": r"mainnet|메인넷|new chain|\bl1 launch|\btge\b",
    "퍼프 DEX": r"perp dex|perps? dex|perpetual dex",
    "김프·국내거래소": r"김프|김치 ?프리미엄|kimchi premium|upbit|업비트|bithumb|빗썸",
    "예측시장": r"polymarket|kalshi|prediction market|폴리마켓|예측시장",
}

_HANGUL = re.compile(r"[가-힣]")
_CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b")
_COMPILED = {}


def _coin_patterns():
    if not _COMPILED:
        for sym, (names, strict) in COINS.items():
            latin = [re.escape(n) for n in names if not _HANGUL.search(n)]
            # 한글은 조사가 바로 붙는다("리플이", "비트코인은") → 뒤쪽 경계는 보지 않는다
            hangul = [re.escape(n) for n in names if _HANGUL.search(n)]
            parts = ([r"(?<![\w$])(?:" + "|".join(latin) + r")(?!\w)"] if latin else []) +                     ([r"(?<![가-힣A-Za-z0-9])(?:" + "|".join(hangul) + ")"] if hangul else [])
            ci = re.compile("|".join(parts), re.I) if parts else None
            upper = None if strict else re.compile(r"(?<![\w$])" + sym + r"(?!\w)")
            _COMPILED[sym] = (ci, upper)
        for label, pat in NARRATIVES.items():
            _COMPILED["#" + label] = re.compile(pat, re.I)
    return _COMPILED


def extract(text):
    """글 1개 → 언급된 엔티티 집합. 코인은 'BTC', 내러티브는 '#ETF' 형태."""
    pats = _coin_patterns()
    found = set()
    for tag in _CASHTAG.findall(text or ""):
        t = tag.upper()
        if t.isdigit() or len(t) < 2:
            continue
        found.add(t)
    for sym in COINS:
        ci, upper = pats[sym]
        if (ci and ci.search(text)) or (upper and upper.search(text)):
            found.add(sym)
    for label in NARRATIVES:
        if pats["#" + label].search(text):
            found.add("#" + label)
    return found


def is_crypto(text):
    return bool(extract(text) - {"#트럼프", "#금리·연준", "#양자컴퓨터", "#AI 에이전트", "#ETF"}) or bool(
        re.search(r"crypto|blockchain|web3|defi|onchain|on-chain|nft|token|coin|wallet|satoshi|크립토|코인", text or "", re.I))


def count(posts):
    c = Counter()
    examples = {}
    for p in posts:
        for ent in extract(p.text):
            c[ent] += 1
            # 예시 글: 가장 인기 있는(레딧은 순위 높은, X는 좋아요 많은) 글
            if p.source == "reddit":
                score = 10000 - p.rank
            elif p.source == "telegram":
                score = p.extra.get("views", 0) // 20  # 조회수는 좋아요보다 한 자릿수 이상 크다
            else:
                score = p.likes
            if ent not in examples or score > examples[ent][0]:
                examples[ent] = (score, p)
    return c, {k: v[1] for k, v in examples.items()}


TREND_SOURCES = ("x", "reddit", "bluesky", "farcaster", "telegram", "dcinside", "coinpan")
KR_SOURCES = ("dcinside", "coinpan")
SNAPSHOT_VERSION = 2  # 집계 대상 소스가 바뀌면 올린다 → 옛 기준선과 섞지 않음(다시 3회 워밍업)


def window(posts, cfg, now=None):
    """트렌드 집계 대상: 최근 window_hours 안의 여론 글(해외 SNS·레딧·텔레그램 + 국내 커뮤니티).
    블로그·매체 기사·속보는 사람들의 '말'이 아니라서 제외한다(블로그는 수년치 과거글도 딸려온다)."""
    hours = cfg["trends"].get("window_hours", 24)
    out = []
    for p in posts:
        if p.source not in TREND_SOURCES:
            continue
        age = p.age_hours(now)
        if age is not None and age <= hours:
            out.append(p)
    return out


def analyze(posts, history, cfg):
    """반환: {top:[(ent,n,ratio|None)], surges:[{ent,n,base,ratio,new,example}], snapshot:{...}}"""
    tcfg = cfg["trends"]
    counts, examples = count(posts)
    total = max(len(posts), 1)
    snapshot = {"total": len(posts), "counts": dict(counts), "v": SNAPSHOT_VERSION}

    past = [h for h in history if h.get("total") and h.get("v", 1) == SNAPSHOT_VERSION][-tcfg["history_runs"]:]
    warm = len(past) >= 3
    surges, top = [], []
    for ent, n in counts.most_common():
        share = n / total
        base_shares = [h["counts"].get(ent, 0) / h["total"] for h in past]
        base = sum(base_shares) / len(base_shares) if base_shares else 0.0
        base_n = base * total  # 이번 회차 규모로 환산한 '평소' 언급수
        ratio = (share / base) if base > 0 else None
        top.append((ent, n, ratio))
        if not warm or n < tcfg["min_mentions"]:
            continue
        seen_before = sum(1 for s in base_shares if s > 0)
        if ratio is None or seen_before <= 1:
            surges.append({"ent": ent, "n": n, "base": round(base_n, 1), "ratio": None, "new": True,
                           "example": examples.get(ent)})
        elif ratio >= tcfg["surge_ratio"] and n - base_n >= 2:
            surges.append({"ent": ent, "n": n, "base": round(base_n, 1), "ratio": round(ratio, 1), "new": False,
                           "example": examples.get(ent)})
    surges.sort(key=lambda s: (s["ratio"] or 99, s["n"]), reverse=True)
    kr = Counter()
    for p in posts:
        if p.source in KR_SOURCES:
            kr.update(e for e in extract(p.text) if not e.startswith("#"))
    return {"top": top[:10], "surges": surges[:tcfg["max_items"]], "warm": warm,
            "runs": len(past), "snapshot": snapshot, "examples": examples, "kr_top": kr.most_common(6),
            "kr_total": sum(1 for p in posts if p.source in KR_SOURCES)}


def trending_changes(current, previous_symbols):
    prev = set(previous_symbols or [])
    new = [c for c in current if c["symbol"] not in prev] if prev else []
    return new
