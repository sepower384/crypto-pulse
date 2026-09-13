"""언급량 트렌드: 코인·내러티브 언급을 세고, 지난 N회 평균 대비 급상승을 잡는다.

소스 가용성이 매번 달라서(X 쿠키 만료, 레딧 429 등) 절대 횟수가 아니라
'이번 회차 글 수 대비 비율(share)'로 비교한다.
"""
import re
from collections import Counter

# symbol: (이름 별칭들, strict)  strict=True 면 영어 일반단어와 겹쳐서 $캐시태그로만 센다
COINS = {
    "BTC": (["bitcoin", "비트코인"], False),
    "ETH": (["ethereum", "ether", "이더리움"], False),
    "SOL": (["solana", "솔라나"], False),
    "XRP": (["ripple", "리플"], False),
    "BNB": (["binance coin"], False),
    "DOGE": (["dogecoin", "도지코인"], False),
    "ADA": (["cardano"], False),
    "HYPE": (["hyperliquid", "하이퍼리퀴드"], True),
    "ZEC": (["zcash", "지캐시"], False),
    "XMR": (["monero"], False),
    "TON": (["toncoin"], True),
    "SUI": ([], False),
    "AVAX": (["avalanche"], False),
    "LINK": (["chainlink"], True),
    "DOT": (["polkadot"], True),
    "LTC": (["litecoin"], False),
    "TRX": (["tron"], False),
    "PEPE": ([], False),
    "SHIB": (["shiba inu"], False),
    "ARB": (["arbitrum"], False),
    "OP": (["optimism"], True),
    "TAO": (["bittensor"], True),
    "ENA": (["ethena"], False),
    "ONDO": ([], False),
    "PUMP": (["pump.fun", "pumpfun"], True),
    "WLFI": (["world liberty"], False),
    "USDT": (["tether"], False),
    "USDC": ([], False),
    "MSTR": (["microstrategy"], False),
    "COIN": (["coinbase"], True),
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
}

_CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b")
_COMPILED = {}


def _coin_patterns():
    if not _COMPILED:
        for sym, (names, strict) in COINS.items():
            alts = [re.escape(n) for n in names]
            ci = re.compile(r"(?<![\w$])(" + "|".join(alts) + r")(?!\w)", re.I) if alts else None
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


def window(posts, cfg, now=None):
    """트렌드 집계 대상: 최근 window_hours 안의 X/레딧/블루스카이/파캐스터/텔레그램 글.
    블로그 RSS 는 수년치 과거글이 딸려와서 언급량을 오염시키므로 제외한다."""
    hours = cfg["trends"].get("window_hours", 24)
    out = []
    for p in posts:
        if p.source not in ("x", "reddit", "bluesky", "farcaster", "telegram"):
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
    snapshot = {"total": len(posts), "counts": dict(counts)}

    past = [h for h in history if h.get("total")][-tcfg["history_runs"]:]
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
    return {"top": top[:10], "surges": surges[:tcfg["max_items"]], "warm": warm,
            "runs": len(past), "snapshot": snapshot, "examples": examples}


def trending_changes(current, previous_symbols):
    prev = set(previous_symbols or [])
    new = [c for c in current if c["symbol"] not in prev] if prev else []
    return new
