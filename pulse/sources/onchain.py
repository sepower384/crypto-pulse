"""온체인 디젠 데이터 + 체인 목록 (전부 키 불필요, 실측 2026-09-17).

- 게코터미널 trending_pools: 지금 거래가 몰리는 DEX 풀 (토큰 이름은 include=base_token 으로 한 번에)
- 덱스스크리너 token-boosts/top: 돈 내고 노출을 산 토큰. 대부분 초소형·러그라 유동성 필터를 반드시 건다
- 디파이라마 v2/chains: 체인별 예치금(TVL) — 새 체인 등장·TVL 급증 감지용
- 게코터미널 networks: DEX 가 붙은 체인 목록(3페이지 ≈ 250개) — 디파이라마보다 새 체인이 빨리 뜬다
"""
from datetime import datetime, timezone

from ..feeds import parse_date
from ..model import SourceResult
from ..net import fetch_json

GT = "https://api.geckoterminal.com/api/v2"
LLAMA_CHAINS = "https://api.llama.fi/v2/chains"
DS_BOOSTS = "https://api.dexscreener.com/token-boosts/top/v1"
DS_TOKENS = "https://api.dexscreener.com/tokens/v1/{chain}/{addrs}"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_trending(data, now=None):
    now = now or datetime.now(timezone.utc)
    inc = {(i["type"], i["id"]): i.get("attributes", {}) for i in data.get("included", [])}
    pools = []
    for p in data.get("data", []):
        a, rel = p.get("attributes", {}), p.get("relationships", {})
        tok = inc.get(("token", ((rel.get("base_token") or {}).get("data") or {}).get("id")), {})
        net_id = ((rel.get("network") or {}).get("data") or {}).get("id", "")
        net = inc.get(("network", net_id), {})
        created = parse_date(a.get("pool_created_at"))
        chg = a.get("price_change_percentage") or {}
        tx = (a.get("transactions") or {}).get("h24") or {}
        pools.append({
            "symbol": tok.get("symbol") or (a.get("name") or "").split(" / ")[0],
            "name": tok.get("name") or a.get("name", ""),
            "pair": a.get("name", ""),
            "chain": net.get("name") or net_id, "chain_id": net_id,
            "change_24h": _f(chg.get("h24")), "change_1h": _f(chg.get("h1")),
            "volume_24h": _f((a.get("volume_usd") or {}).get("h24")),
            "liquidity": _f(a.get("reserve_in_usd")),
            "mcap": _f(a.get("market_cap_usd")) or _f(a.get("fdv_usd")),
            "buyers_24h": tx.get("buyers"), "sellers_24h": tx.get("sellers"),
            "age_hours": round((now - created).total_seconds() / 3600, 1) if created else None,
            "url": f"https://www.geckoterminal.com/{net_id}/pools/{a.get('address', '')}",
            "image": tok.get("image_url") or "",
            "key": p.get("id", ""),
        })
    return pools


def parse_boost_pairs(pairs, boosts, now=None):
    """tokens/v1 응답(페어 목록) → 토큰당 유동성 최대 페어 1개."""
    now = now or datetime.now(timezone.utc)
    best = {}
    for p in pairs or []:
        addr = ((p.get("baseToken") or {}).get("address") or "").lower()
        liq = _f((p.get("liquidity") or {}).get("usd")) or 0
        if addr and (addr not in best or liq > best[addr][0]):
            best[addr] = (liq, p)
    out = []
    for addr, (liq, p) in best.items():
        b = boosts.get(addr, {})
        created = p.get("pairCreatedAt")
        age = (now.timestamp() - created / 1000) / 3600 if created else None
        socials = [s.get("url") for s in ((p.get("info") or {}).get("socials") or []) if s.get("url")]
        out.append({
            "symbol": (p.get("baseToken") or {}).get("symbol", ""),
            "name": (p.get("baseToken") or {}).get("name", ""),
            "chain": p.get("chainId", ""), "chain_id": p.get("chainId", ""),
            "change_24h": _f((p.get("priceChange") or {}).get("h24")),
            "change_1h": _f((p.get("priceChange") or {}).get("h1")),
            "volume_24h": _f((p.get("volume") or {}).get("h24")),
            "liquidity": liq, "mcap": _f(p.get("marketCap")) or _f(p.get("fdv")),
            "age_hours": round(age, 1) if age is not None else None,
            "url": p.get("url") or b.get("url", ""), "boost": b.get("totalAmount") or b.get("amount") or 0,
            "description": (b.get("description") or "")[:160], "socials": socials[:2],
            "image": (p.get("info") or {}).get("imageUrl") or "",
            "key": f"{p.get('chainId')}:{addr}",
        })
    return out


def parse_llama_chains(data):
    return [{"name": c.get("name", ""), "tvl": _f(c.get("tvl")) or 0.0, "token": c.get("tokenSymbol"),
             "gecko_id": c.get("gecko_id"), "chain_id": c.get("chainId")}
            for c in data or [] if c.get("name")]


def parse_gt_networks(data):
    return [{"id": n["id"], "name": (n.get("attributes") or {}).get("name") or n["id"],
             "cg_platform": (n.get("attributes") or {}).get("coingecko_asset_platform_id")}
            for n in (data or {}).get("data", []) if n.get("id")]


def network_pools(network_id, limit=3):
    """새 체인 자료용: 그 체인의 상위 풀 몇 개 (이름·24h 거래대금). 실패하면 []."""
    try:
        data = fetch_json(f"{GT}/networks/{network_id}/pools?page=1&include=base_token", retries=1, timeout=15)
    except Exception:  # noqa: BLE001
        return []
    pools = parse_trending(data)
    pools.sort(key=lambda p: p["volume_24h"] or 0, reverse=True)
    return pools[:limit]


def _boosted():
    boosts = fetch_json(DS_BOOSTS, retries=1)
    by_chain, meta = {}, {}
    for b in boosts[:30]:
        addr = b.get("tokenAddress") or ""
        if not addr:
            continue
        by_chain.setdefault(b.get("chainId", ""), []).append(addr)
        meta[addr.lower()] = b
    out = []
    for chain, addrs in by_chain.items():
        try:
            pairs = fetch_json(DS_TOKENS.format(chain=chain, addrs=",".join(addrs[:30])), retries=1)
        except Exception:  # noqa: BLE001
            continue
        out += parse_boost_pairs(pairs, meta)
    return out


def collect(cfg):
    extra = {"trending_pools": [], "boosted": [], "llama_chains": [], "gt_networks": []}
    notes, fails = [], []
    try:
        extra["trending_pools"] = parse_trending(
            fetch_json(f"{GT}/networks/trending_pools?include=base_token,network&page=1", retries=1))
        notes.append(f"트렌딩풀 {len(extra['trending_pools'])}")
    except Exception as e:  # noqa: BLE001
        fails.append(f"게코터미널({type(e).__name__})")
    if cfg.get("degen", {}).get("boosts", True):
        try:
            extra["boosted"] = _boosted()
            notes.append(f"부스트 {len(extra['boosted'])}")
        except Exception as e:  # noqa: BLE001
            fails.append(f"덱스스크리너({type(e).__name__})")
    try:
        extra["llama_chains"] = parse_llama_chains(fetch_json(LLAMA_CHAINS, retries=1))
        notes.append(f"체인 {len(extra['llama_chains'])}")
    except Exception as e:  # noqa: BLE001
        fails.append(f"디파이라마({type(e).__name__})")
    nets, page = [], 1
    try:
        while page <= 5:
            data = fetch_json(f"{GT}/networks?page={page}", retries=1)
            nets += parse_gt_networks(data)
            if not (data.get("links") or {}).get("next"):
                break
            page += 1
        extra["gt_networks"] = nets
        notes.append(f"네트워크 {len(nets)}")
    except Exception as e:  # noqa: BLE001
        # 일부 페이지만 받은 목록으로 '새 체인'을 판정하면 안 된다 → 통째로 버림
        fails.append(f"네트워크목록({type(e).__name__})")
    ok = len(fails) < 3
    return SourceResult("onchain", ok, [], " · ".join(notes) + (f", 실패 {', '.join(fails)}" if fails else ""),
                        extra=extra)
