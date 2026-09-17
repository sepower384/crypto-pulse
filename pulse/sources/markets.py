"""코인별 거래 장소·가격 (코인게코 공개 API, 키 불필요. 실측 2026-09-17 전부 200).

- /coins/{id}/tickers   : 그 코인을 파는 거래소(중앙·탈중앙)와 거래소별 달러 환산 가격·거래액
- /derivatives          : 선물(무기한) 시장 전체 ≈9MB — 코인별로 어느 거래소에서 선물이 되는지
- /exchanges/{upbit|bithumb}/tickers + /simple/price : 김치 프리미엄
무료 한도(분당 수십 회)를 넘지 않게 호출 사이를 조금 띄운다. 실패하면 빈 값.
"""
import time

from ..net import fetch_json

CG = "https://api.coingecko.com/api/v3"
PAUSE = 2.5  # 실측: 1.5초 간격으로 10회 넘기면 뒤쪽 호출이 한도에 걸림
_last = [0.0]


def _get(path, timeout=40):
    wait = PAUSE - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    try:
        return fetch_json(CG + path, retries=2, backoff=8, timeout=timeout)
    finally:
        _last[0] = time.time()


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_tickers(data):
    out = []
    for t in (data or {}).get("tickers", []):
        m = t.get("market") or {}
        out.append({
            "market": m.get("name") or "", "id": m.get("identifier") or "",
            "base": t.get("base") or "", "target": t.get("target") or "",
            "usd": _f((t.get("converted_last") or {}).get("usd")),
            "volume": _f((t.get("converted_volume") or {}).get("usd")) or 0.0,
            "trust": t.get("trust_score"), "stale": bool(t.get("is_stale")), "anomaly": bool(t.get("is_anomaly")),
            "url": t.get("trade_url") or "",
        })
    return out


def tickers(coin_id):
    try:
        return parse_tickers(_get(f"/coins/{coin_id}/tickers?depth=false&order=volume_desc"))
    except Exception:  # noqa: BLE001
        return []


def parse_derivatives(rows):
    """{지수 심볼: [{market, funding, oi, volume}]} — 무기한 선물만."""
    out = {}
    for r in rows or []:
        if r.get("contract_type") != "perpetual" or not r.get("index_id"):
            continue
        out.setdefault(str(r["index_id"]).upper(), []).append({
            "market": r.get("market") or "", "funding": _f(r.get("funding_rate")), "price": _f(r.get("price")),
            "oi": _f(r.get("open_interest")) or 0.0, "volume": _f(r.get("volume_24h")) or 0.0})
    for v in out.values():
        v.sort(key=lambda x: x["volume"], reverse=True)
    return out


def derivatives():
    try:
        return parse_derivatives(_get("/derivatives", timeout=90))
    except Exception:  # noqa: BLE001
        return {}


KIMCHI_COINS = {"bitcoin": "BTC", "ethereum": "ETH", "ripple": "XRP", "solana": "SOL", "dogecoin": "DOGE"}


def kimchi(coin_ids=tuple(KIMCHI_COINS)):
    """{심볼: {"global": 달러, "upbit": 달러환산, "bithumb": 달러환산}}"""
    out = {}
    try:
        price = _get(f"/simple/price?ids={','.join(coin_ids)}&vs_currencies=usd")
    except Exception:  # noqa: BLE001
        return {}
    for cid in coin_ids:
        usd = _f((price.get(cid) or {}).get("usd"))
        if usd:
            out[KIMCHI_COINS.get(cid, cid.upper())] = {"global": usd}
    for ex in ("upbit", "bithumb"):
        try:
            rows = parse_tickers(_get(f"/exchanges/{ex}/tickers?coin_ids={','.join(coin_ids)}"))
        except Exception:  # noqa: BLE001
            continue
        for r in rows:
            if r["target"] == "KRW" and r["base"] in out and r["usd"]:
                out[r["base"]].setdefault(ex, r["usd"])
    return out
