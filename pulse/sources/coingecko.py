"""코인게코 트렌딩(검색량 기준 상위) — 키 불필요."""
from ..model import SourceResult
from ..net import fetch_json

URL = "https://api.coingecko.com/api/v3/search/trending"


def parse(data):
    coins = []
    for c in data.get("coins", []):
        it = c.get("item", {})
        d = it.get("data", {}) or {}
        chg = (d.get("price_change_percentage_24h") or {}).get("usd")
        coins.append({
            "symbol": (it.get("symbol") or "").upper(),
            "name": it.get("name", ""),
            "id": it.get("id", ""),
            "rank": it.get("market_cap_rank"),
            "change_24h": round(chg, 1) if isinstance(chg, (int, float)) else None,
            "image": it.get("large") or it.get("small") or it.get("thumb") or "",
        })
    return coins


def collect(cfg):
    try:
        coins = parse(fetch_json(URL, retries=2, backoff=8))
        return SourceResult("coingecko", True, [], f"트렌딩 {len(coins)}종", extra={"trending": coins})
    except Exception as e:  # noqa: BLE001
        return SourceResult("coingecko", False, [], f"실패: {type(e).__name__}", extra={"trending": []})
