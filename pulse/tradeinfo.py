"""코인별 '어디서 사나요 / 어떻게 투자하나요 / 거래소 간 가격 차이' 정리 (한국어)."""
import re

from . import friendly as fr

KR_EXCHANGES = {"upbit": "업비트", "bithumb": "빗썸", "korbit": "코빗", "coinone": "코인원", "gopax": "고팍스"}
GLOBAL_EXCHANGES = {  # 코인게코 거래소 id → 한국어 이름 (대형·국내 이용자가 많이 쓰는 곳만)
    "binance": "바이낸스", "gdax": "코인베이스", "okex": "오케이엑스", "bybit_spot": "바이비트", "bitget": "비트겟",
    "kraken": "크라켄", "kucoin": "쿠코인", "gate": "게이트", "mxc": "멕시", "huobi": "HTX(후오비)",
    "bingx": "빙엑스", "hyperliquid_spot": "하이퍼리퀴드", "hyperliquid": "하이퍼리퀴드", "bitfinex": "비트파이넥스",
    "crypto_com": "크립토닷컴", "binance_us": "바이낸스US", "robinhood": "로빈후드",
}
DEX_NAMES = [  # 이름(소문자)에 들어 있는 조각 → 한국어
    ("uniswap", "유니스왑"), ("pancakeswap", "팬케이크스왑"), ("raydium", "레이디움"), ("orca", "오르카"),
    ("meteora", "메테오라"), ("jupiter", "주피터"), ("aerodrome", "에어로드롬"), ("pumpswap", "펌프스왑"),
    ("pump.fun", "펌프펀"), ("pump-fun", "펌프펀"), ("curve", "커브"), ("balancer", "밸런서"), ("sushi", "스시스왑"),
    ("velodrome", "벨로드롬"), ("camelot", "카멜롯"), ("trader joe", "트레이더조"), ("traderjoe", "트레이더조"),
    ("hyperswap", "하이퍼스왑"), ("project x", "프로젝트X"), ("cetus", "세투스"), ("thena", "테나"),
    ("aster", "애스터"), ("lifinity", "리피니티"), ("fluxbeam", "플럭스빔"), ("launchlab", "런치랩"),
]
FUTURES_NAMES = [("binance", "바이낸스"), ("bybit", "바이비트"), ("okx", "오케이엑스"), ("bitget", "비트겟"),
                 ("hyperliquid", "하이퍼리퀴드"), ("gate", "게이트"), ("bingx", "빙엑스"), ("mexc", "멕시"),
                 ("kucoin", "쿠코인"), ("aster", "애스터"), ("lighter", "라이터")]
STABLE_TARGETS = {"USDT", "USD", "USDC", "FDUSD", "USD1", "DAI", "USDE"}

WALLET = {  # 게코터미널 네트워크 id → (지갑, 필요한 코인)
    "solana": ("팬텀 같은 솔라나 지갑", "솔라나(SOL)"),
    "eth": ("메타마스크·래비 같은 지갑", "이더리움(ETH)"),
    "base": ("메타마스크·래비 같은 지갑", "베이스 체인의 이더리움(ETH)"),
    "arbitrum": ("메타마스크·래비 같은 지갑", "아비트럼 체인의 이더리움(ETH)"),
    "bsc": ("메타마스크·래비 같은 지갑", "BNB"),
    "robinhood": ("메타마스크·래비 같은 지갑", "로빈후드 체인의 이더리움(ETH)"),
    "arc": ("메타마스크·래비 같은 지갑", "아크 체인의 USDC(수수료도 USDC)"),
    "hyperevm": ("메타마스크·래비 같은 지갑", "하이퍼리퀴드 체인의 HYPE"),
    "sui-network": ("수이 지갑", "수이(SUI)"),
    "ton": ("톤키퍼 같은 톤 지갑", "톤(TON)"),
    "tron": ("트론링크 지갑", "트론(TRX)"),
    "polygon_pos": ("메타마스크·래비 같은 지갑", "폴리곤(POL)"),
    "avax": ("메타마스크·래비 같은 지갑", "아발란체(AVAX)"),
}


def known_dex(raw):
    low = (raw or "").lower()
    for key, ko in DEX_NAMES:
        if key in low:
            return ko
    return None


def dex_name(raw):
    return known_dex(raw) or "블록체인 거래소"


def _is_dex(t):
    name = t["market"].lower()
    return any(k in name for k, _ in DEX_NAMES) or bool(re.search(r"\(.*\)$", t["market"])) or \
        t["target"].startswith("0X") or len(t["target"]) > 20


def usable(t, min_volume=50_000):
    return (t["usd"] and not t["stale"] and not t["anomaly"] and t["trust"] != "red"
            and t["volume"] >= min_volume)


def venues(rows):
    """거래 장소를 국내·해외·블록체인 거래소로 나눈다 (거래액 큰 순, 이름 중복 제거)."""
    kr, glob, dex, other = [], [], [], 0
    for t in rows:
        if t["stale"] or t["anomaly"]:
            continue
        if t["id"] in KR_EXCHANGES:
            kr.append(KR_EXCHANGES[t["id"]])
        elif t["id"] in GLOBAL_EXCHANGES:
            glob.append(GLOBAL_EXCHANGES[t["id"]])
        elif _is_dex(t) and known_dex(t["market"]):
            chain_m = re.search(r"\(([^)]+)\)$", t["market"])
            dex.append(known_dex(t["market"]) + (f"({fr.chain(chain_m.group(1))})" if chain_m else ""))
        else:
            other += 1
    uniq = lambda xs: list(dict.fromkeys(xs))  # noqa: E731
    return {"kr": uniq(kr), "global": uniq(glob), "dex": uniq(dex), "other": other}


def ref_price(rows):
    prices = sorted(t["usd"] for t in rows if usable(t))
    return prices[len(prices) // 2] if prices else None


def futures(symbol, deriv, limit=3, ref=None):
    """선물 시장. 같은 심볼의 다른 코인이 섞이지 않게 현물 기준가와 15% 넘게 다르면 뺀다."""
    rows = (deriv or {}).get((symbol or "").upper()) or []
    if ref:
        rows = [r for r in rows if r.get("price") and abs(r["price"] / ref - 1) <= 0.15]
    names, funding = [], None
    for r in rows:
        low = r["market"].lower()
        for key, ko in FUTURES_NAMES:
            if key in low and ko not in names:
                names.append(ko)
                if funding is None and r["funding"] is not None:
                    funding = r["funding"]
    return names[:limit], len(rows), funding


def _known(t):
    return t["id"] in GLOBAL_EXCHANGES or (_is_dex(t) and known_dex(t["market"]))


def spread(rows, min_pct=1.5, max_pct=20.0):
    """달러 기준 같은 코인의 거래소 간 가격 차이 — 이름을 아는 거래소끼리만.
    원화 마켓은 김치 프리미엄이 섞이므로 뺀다."""
    cands = [t for t in rows if usable(t) and t["target"] != "KRW" and _known(t)
             and (t["target"] in STABLE_TARGETS or _is_dex(t))]
    if len(cands) < 2:
        return None
    lo = min(cands, key=lambda t: t["usd"])
    hi = max(cands, key=lambda t: t["usd"])
    pct = (hi["usd"] / lo["usd"] - 1) * 100
    if pct < min_pct or pct > max_pct:
        return None
    return {"pct": pct, "low": lo, "high": hi}


def market_label(t):
    if t["id"] in KR_EXCHANGES:
        return KR_EXCHANGES[t["id"]]
    if t["id"] in GLOBAL_EXCHANGES:
        return GLOBAL_EXCHANGES[t["id"]]
    if _is_dex(t):
        return dex_name(t["market"])
    return t["market"]


def price_txt(v):
    if v is None:
        return "?"
    if v >= 100:
        return f"{v:,.0f}달러"
    if v >= 1:
        return f"{v:,.2f}달러"
    return f"{v:.6f}".rstrip("0") + "달러"


def lines_for(symbol, rows, deriv, has_airdrop_note=True):
    """코인 1개 → 안내 줄 목록 (들여쓰기 포함)."""
    v = venues(rows)
    out = []
    if v["kr"]:
        out.append(f"      • 국내 거래소: {' · '.join(v['kr'])}")
    else:
        out.append("      • 국내 거래소: 아직 없음")
    if v["global"]:
        more = len(v["global"]) - 4 + v["other"]
        out.append(f"      • 해외 거래소: {' · '.join(v['global'][:4])}" + (f" 외 {more}곳" if more > 0 else ""))
    elif v["other"]:
        out.append(f"      • 해외 거래소: 중소형 거래소 {v['other']}곳")
    if v["dex"]:
        out.append(f"      • 블록체인 거래소: {' · '.join(v['dex'][:3])}")
    elif not v["global"] and not v["kr"] and not v["other"]:
        out.append("      • 블록체인 거래소에서만 거래됩니다")
    fut, n_fut, funding = futures(symbol, deriv, ref=ref_price(rows))
    modes = []
    if v["kr"] or v["global"] or v["other"] or v["dex"]:
        modes.append("현물 매매")
    if fut:
        f_txt = f"선물 매매(오를 때·내릴 때 모두 가능): {' · '.join(fut)}"
        if funding is not None:
            side = "오른다에 건 쪽이 수수료를 내는 중" if funding > 0 else "내린다에 건 쪽이 수수료를 내는 중"
            f_txt += f" — 자금 조달 수수료 {funding:+.3f}%, {side}"
        modes.append(f_txt)
    elif n_fut:
        modes.append(f"선물 매매: 중소형 거래소 {n_fut}곳")
    if has_airdrop_note:
        modes.append("에어드랍 작업: 이미 코인이 나와 해당 없음")
    if modes:
        out.append("      • 투자 방법: " + " / ".join(modes))
    sp = spread(rows)
    if sp:
        out.append(f"      • 거래소 간 가격 차이 *{sp['pct']:.1f}%*: {market_label(sp['low'])} {price_txt(sp['low']['usd'])} → "
                   f"{market_label(sp['high'])} {price_txt(sp['high']['usd'])}")
    return out, sp


def kimchi_rows(k):
    """[(심볼, "업비트 +0.1% · 빗썸 +0.2%", 대표 프리미엄%)]"""
    rows = []
    for sym, v in (k or {}).items():
        g = v.get("global")
        parts, main = [], None
        for ex, ko in (("upbit", "업비트"), ("bithumb", "빗썸")):
            if v.get(ex) and g:
                pct = (v[ex] / g - 1) * 100
                parts.append(f"{ko} {pct:+.1f}%" if abs(pct) >= 0.1 else f"{ko} 거의 같음")
                main = pct if main is None else main
        if parts:
            rows.append((sym, " · ".join(parts), main))
    return rows


def dex_howto(t):
    wallet, coin = WALLET.get(t.get("chain_id"), ("그 블록체인을 지원하는 지갑", "그 블록체인의 기본 코인"))
    where = dex_name(t.get("dex") or "")
    return (f"{wallet} 준비 → {coin} 넣기 → {where}에서 교환. "
            "이름만 같은 가짜 코인이 많으니 차트 링크의 코인 주소와 같은지 꼭 확인하시는 것이 좋습니다")
