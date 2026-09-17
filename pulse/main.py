"""1회 실행: 수집 → 트렌드/인사이트 분석 → 슬랙·텔레그램 전송 → state 저장.

python -m pulse.main                     # 실제 전송 (슬랙·텔레그램 중 설정된 쪽)
python -m pulse.main --dry-run           # 전송·state 저장 없이 슬랙 블록 출력
python -m pulse.main --force             # 새 소식 없어도 전송
python -m pulse.main --preview-telegram  # 전송·state 저장 없이 data/preview_telegram.html · preview_slack.txt 생성
"""
import argparse
import html
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import alpha, chart, friendly as fr, insights, slack, state, telegram as tgsend, trends, wording
from .sources import (blogs, bluesky, coingecko, community_kr, farcaster, kr_news, news, onchain, reddit,
                      telegram, x)
from .translate import summarize, to_korean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_LABEL = {"x": "X", "reddit": "Reddit", "bluesky": "Bluesky", "farcaster": "Farcaster",
                "telegram": "Telegram", "blogs": "블로그", "coingecko": "CoinGecko",
                "kr_community": "국내 커뮤니티", "kr_news": "국내 속보·공지", "news": "매체", "onchain": "온체인"}
BOT_NAME = "크립토마스"
TG_TOPIC = f"🌐 {BOT_NAME}"


def load_config(path=None):
    with open(path or os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def load_dotenv(path=None):
    path = path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def collect_all(cfg):
    # X(Playwright)는 오래 걸리니 나머지와 병렬
    fns = [x.collect, reddit.collect, bluesky.collect, farcaster.collect, telegram.collect,
           blogs.collect, coingecko.collect, community_kr.collect, kr_news.collect, news.collect, onchain.collect]
    with ThreadPoolExecutor(max_workers=len(fns)) as ex:
        return list(ex.map(lambda f: f(cfg), fns))


COIN_KR = {"BTC": "비트코인", "ETH": "이더리움", "SOL": "솔라나", "XRP": "리플", "DOGE": "도지코인",
           "BNB": "바이낸스코인", "ADA": "에이다", "SUI": "수이", "HYPE": "하이퍼리퀴드", "LINK": "체인링크",
           "TRX": "트론", "AVAX": "아발란체", "TON": "톤", "PEPE": "페페", "COIN": "코인베이스 주식",
           "MSTR": "스트래티지 주식", "USDT": "테더", "USDC": "USD코인", "ZEC": "지캐시",
           "ENA": "에테나", "ONDO": "온도파이낸스", "TAO": "비트텐서", "WLFI": "월드리버티", "PUMP": "펌프펀",
           "ARB": "아비트럼", "OP": "옵티미즘", "DOT": "폴카닷", "LTC": "라이트코인", "SHIB": "시바이누",
           "XMR": "모네로", "BONK": "봉크", "BERA": "베라체인", "MON": "모나드"}
# 누가 누군지 모르면 인용문이 그냥 영어 잡담으로 읽힌다
WHO = {"Vitalik Buterin": "이더리움 창시자", "Michael Saylor": "비트코인 최대 보유기업 회장",
       "CZ": "바이낸스 창업자", "Brian Armstrong": "코인베이스 CEO", "Arthur Hayes": "비트멕스 창업자",
       "Anthony Pompliano": "비트코인 투자자·유튜버", "Raoul Pal": "매크로 투자자", "Mike Novogratz": "갤럭시디지털 CEO",
       "Cathie Wood": "ARK인베스트 CEO", "Balaji": "전 코인베이스 CTO", "Anatoly Yakovenko": "솔라나 공동창업자",
       "Hayden Adams": "유니스왑 창업자", "Haseeb Qureshi": "드래곤플라이 투자사", "ZachXBT": "온체인 사기 추적가",
       "Lookonchain": "고래 지갑 추적", "Wu Blockchain": "아시아 크립토 속보", "Peter Schiff": "대표적 비트코인 비관론자",
       "Elon Musk": "테슬라 CEO", "Lyn Alden": "매크로 애널리스트"}


def usd(v):
    if v is None:
        return "?"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:.1f}B"
    if a >= 1e6:
        return f"${v / 1e6:.1f}M"
    if a >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:.0f}"


def pct(v):
    return "?" if v is None else f"{v:+.0f}%"


def age_txt(h):
    if h is None:
        return ""
    if h < 48:
        return f"거래 시작 {max(h, 0):.0f}시간째"
    return f"거래 시작 {h / 24:.0f}일째"


def llama_url(name):
    return "https://defillama.com/chain/" + quote(name)


def _extra(by_name, name, key, default):
    r = by_name.get(name)
    return (r.extra.get(key) if r else None) or default


def _head(p, n=90):
    """한 줄 제목(번역). 링크 라벨에 줄바꿈이 섞이면 슬랙 링크·텔레그램 변환이 깨진다."""
    t = " ".join(fr.ko_terms(to_korean(p.extra.get("title") or p.text, 300)).split())
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def ent_label(ent):
    if ent.startswith("#"):
        return ent[1:]
    return f"{COIN_KR[ent]}({ent})" if ent in COIN_KR else f"${ent}"


def kr_label(ent, ko_names):
    """국내 순위용: 기본 한글명 → 업비트 한글명 → $심볼."""
    if ent in COIN_KR or ent not in ko_names:
        return ent_label(ent)
    return f"{ko_names[ent]}({ent})"


def who(author):
    role = WHO.get(author)
    name = fr.person(author)
    return f"{name}({role})" if role else name


def pick_photo(tr, trending, stamp, cfg):
    """메시지당 사진 1장: 언급 횟수 막대차트 우선, 안 되면 코인게코 인기검색 1위 코인 이미지."""
    top = tr["top"][:8]
    if len(top) >= 3:
        url = chart.bar_chart_url([(ent_label(e), n, e.startswith("#")) for e, n, _ in top],
                                  f"가장 많이 나온 이야기 · 최근 {cfg['trends'].get('window_hours', 24)}시간 언급 횟수")
        if url:
            return {"kind": "chart", "url": url,
                    "caption": f"<b>📊 가장 많이 나온 이야기</b>\n{stamp} 기준 · 주황색은 코인, 보라색은 주제입니다"}
    if trending and trending[0].get("image"):
        c = trending[0]
        return {"kind": "coin", "url": c["image"],
                "caption": f"<b>🔥 코인 인기 검색 1위</b>\n{html.escape(c.get('name') or c['symbol'], quote=False)}"
                           f"({html.escape(c['symbol'], quote=False)})입니다"}
    return None


def build_message(cfg, results, st, now):
    posts = alpha.clean_posts([p for r in results for p in r.posts])
    by_name = {r.name: r for r in results}
    seen = st.get("seen", {})
    tr = trends.analyze(trends.window(posts, cfg, now), st.get("mentions", []), cfg)
    upbit_names = _extra(by_name, "kr_news", "upbit_names", {})
    ko_names = {}
    for ko, sym in upbit_names.items():
        ko_names.setdefault(sym, ko)
    tr["kr_top"], tr["kr_total"] = alpha.kr_coin_counts(trends.window(posts, cfg, now), upbit_names)
    trending = _extra(by_name, "coingecko", "trending", [])
    new_trending = trends.trending_changes(trending, st.get("trending_prev", []))
    picks = insights.select(posts, seen, cfg, now)
    news = insights.channel_news(posts, seen, cfg, now, exclude=picks)
    rtop = insights.reddit_top(posts, seen, cfg)
    # 디젠 알파
    llama = _extra(by_name, "onchain", "llama_chains", [])
    gt_nets = _extra(by_name, "onchain", "gt_networks", [])
    pools = _extra(by_name, "onchain", "trending_pools", [])
    new_chains, chain_updates = alpha.detect_new_chains(llama, gt_nets, st.get("chains_known", {}), now)
    new_chains = new_chains[:cfg.get("chains", {}).get("new_max_items", 5)]
    movers = alpha.tvl_movers(llama, st.get("chain_tvl", []), now, cfg)
    launches = alpha.launch_news(posts, seen, now)
    hot, shill = alpha.degen_pick(pools, _extra(by_name, "onchain", "boosted", []), cfg)
    degen_prev = set(st.get("degen_prev", []))
    upbit = alpha.upbit_pick(posts, seen, now)
    flash = alpha.coinness_pick(posts, seen, cfg, now)
    kr = alpha.kr_hot(posts, seen, cfg, now)
    media = alpha.news_pick(posts, seen, cfg, now, exclude=launches)
    drops = alpha.airdrop_pick(_extra(by_name, "onchain", "tokenless", []), posts, cfg, now, st.get("airdrop_shown", {}))
    guides = [g_ for g_ in alpha.airdrop_guides(_extra(by_name, "onchain", "airdrop_guides", []), now)
              if g_["url"] not in seen]
    g = wording.Glossary()  # 어려운 단어는 이 브리핑에서 처음 나올 때 한 번만 풀어 준다 → 메시지 순서대로 호출

    kst = now + timedelta(hours=cfg.get("timezone_offset_hours", 9))
    stamp = f"{kst.month}월 {kst.day}일 {kst:%H:%M}"
    doc = [{"kind": "header", "text": f"🌐 {BOT_NAME} · {stamp}",
            "tg_title": f"{TG_TOPIC} · 오늘의 코인 시장 이야기", "tg_sub": f"{stamp} 기준"},
           {"kind": "text", "text": "_안녕하세요, 크립토마스입니다. 해외·국내 코인 커뮤니티와 뉴스, 블록체인 거래 기록까지 "
                                    "두루 살펴서 지금 사람들의 관심과 돈이 어디로 모이는지 정리해 드립니다. "
                                    "해외 글은 자동 번역이라 표현이 조금 어색할 수 있고, 여기 나온 코인은 추천이 아니라 "
                                    "참고용으로만 봐 주시면 좋겠습니다._"}]

    # AI 요약 (선택)
    lines = [f"[{p.source}] {p.author}: {p.text[:200]}" for p in picks + news]
    lines += [f"[reddit #{p.rank}] {p.extra.get('title', p.text[:150])}" for p in rtop]
    lines += [f"[급상승] {ent_label(s['ent'])} {s['n']}회 (평소 {s['base']})" for s in tr["surges"]]
    lines += [f"[국내 커뮤니티] {p.text[:120]}" for p in kr]
    lines += [f"[국내 속보] {p.extra.get('title', '')}" for p in flash]
    lines += [f"[매체] {p.author}: {p.extra.get('title', '')}" for p in media + launches]
    lines += [f"[새 체인] {c['name']}" for c in new_chains]
    lines += [f"[에어드랍 후보] {d['name']} ({d['category']}) 예치금 {usd(d['tvl'])} 7일 {pct(d['change_7d'])}" for d in drops]
    lines += [f"[온체인 인기 코인] {t['symbol']}({t['chain']}) 하루 {pct(t['change_24h'])} 거래액 {usd(t['volume_24h'])}"
              for t in hot]
    summary = summarize(lines)
    if summary:
        doc.append({"kind": "section", "title": "🧭 한눈에 보는 지금 흐름 (AI 요약)",
                    "lines": [f"• {slack.esc(g.explain(fr.ko_terms(wording.polite(b))))}" for b in summary]})

    # 트렌드
    trend_lines = []
    if tr["warm"]:
        for s in tr["surges"]:
            ex = s["example"]
            tail = f" — {slack.link(ex.url, _head(ex, 70))}" if ex else ""
            label = slack.esc(g.explain(ent_label(s["ent"])))
            if s["new"]:
                trend_lines.append(f"🆕 *{label}* — 평소엔 조용하다가 이번에 {s['n']}번 언급됐습니다{tail}")
            else:
                trend_lines.append(f"📈 *{label}* — 평소 {s['base']}번이던 이야기가 이번엔 {s['n']}번으로, "
                                   f"*{s['ratio']}배* 늘었습니다{tail}")
        if not trend_lines:
            trend_lines.append("_평소보다 갑자기 화제가 된 코인이나 주제는 없습니다. 비교적 조용한 흐름으로 보입니다._")
    top_txt = ", ".join(
        f"{slack.esc(g.explain(ent_label(e)))} {n}번"
        + (" (평소보다 많음)" if r and r >= 1.5 else " (평소보다 적음)" if r and r <= 0.6 else "")
        for e, n, r in tr["top"][:8])
    if top_txt:
        trend_lines.append(f"🔢 *가장 많이 나온 이야기*: {top_txt}")
    if tr.get("kr_top"):
        trend_lines.append(f"🇰🇷 *국내 커뮤니티에서 많이 나온 코인* (최근 글 {tr['kr_total']}개 기준): "
                           + ", ".join(f"{slack.esc(kr_label(e, ko_names))} {n}번" for e, n in tr["kr_top"]))
    if not tr["warm"]:
        trend_lines.append(f"_아직 {g.explain('기준선')}을 익히는 중입니다 ({tr['runs']}/3회). "
                           "세 번째 알림부터는 갑자기 화제가 된 코인도 함께 짚어 드리겠습니다._")
    if new_trending:
        tt = ", ".join(
            f"{c['symbol']}" + (f"(하루 {c['change_24h']:+.0f}%)" if c["change_24h"] is not None else "")
            for c in new_trending[:7])
        trend_lines.append(f"🔥 *검색이 새로 몰리기 시작한 코인* (코인 정보 사이트 코인게코 인기 검색 기준): {tt}")
    doc.append({"kind": "section", "title": "📊 요즘 사람들이 가장 많이 이야기하는 주제", "lines": trend_lines})

    # 새 체인·메인넷
    chain_lines = [chain_line(c, posts, g) for c in new_chains]
    for c in movers:
        chain_lines.append(f"🚀 *{slack.esc(fr.chain(c['name']))}* — 하루 사이에 맡겨진 돈이 {fr.dollars(c['before'])}에서 "
                           f"{fr.dollars(c['tvl'])}로 *{c['pct']:+.0f}%* 늘었습니다 · "
                           f"{slack.link(llama_url(c['name']), '자세히 보기')}")
    for p in launches:
        chain_lines.append(f"📢 {slack.link(p.url, g.explain(_head(p)))} _({slack.esc(fr.outlet(p.author))})_")
    if chain_lines:
        doc.append({"kind": "divider"})
        chain_lines.insert(0, "_새로 생긴 블록체인, 돈이 빠르게 몰리는 블록체인, 정식 출시 소식을 모았습니다. "
                              "초기에는 정보가 적으니 공식 발표를 함께 확인하시는 것이 좋습니다._")
        doc.append({"kind": "section", "title": "🆕 새로 나온 블록체인·출시 소식", "lines": chain_lines})

    # 온체인 인기 코인
    if hot or shill:
        doc.append({"kind": "divider"})
        d_lines = ["_중개 회사 없이 블록체인 위에서 바로 사고파는 거래소에서 지금 거래가 가장 활발한 코인입니다. "
                   "하루에도 가격이 크게 오르내리니 구경하는 용도로만 보시는 것이 좋습니다._"]
        d_lines += [degen_line(t, bool(degen_prev) and t["key"] not in degen_prev, g) for t in hot]
        heat = alpha.chain_heat(pools)[:4]
        if heat:
            d_lines.append("🔥 *인기 코인이 많이 몰린 블록체인*: "
                           + ", ".join(f"{slack.esc(fr.chain(n))} {k}개" for n, k in heat))
        if shill:
            d_lines.append("\n💸 *돈을 내고 광고 중인 코인* — 최소한의 거래 규모는 넘겼지만, 광고 효과로 잠깐 오른 것일 수 있어 "
                           "특히 조심해서 보시는 것이 좋습니다")
            d_lines += [degen_line(t, False, g) for t in shill]
        doc.append({"kind": "section", "title": "🎰 지금 뜨는 코인 (블록체인 거래소 기준)", "lines": d_lines})

    # 에어드랍
    if drops or guides:
        doc.append({"kind": "divider"})
        ratios = _extra(by_name, "onchain", "category_ratio", {})
        hot_chains = [n for n, _ in alpha.chain_heat(pools)[:4]] + [c["name"] for c in new_chains]
        for d in drops:
            d["detail"] = onchain.protocol_detail(d["slug"])
            d["reasons"] = alpha.airdrop_reasons(d, d["detail"], hot_chains, now)
            d["estimate"] = alpha.airdrop_estimate(d, ratios)
        a_lines = ["_에어드랍은 새 프로젝트가 초기 이용자에게 자기 코인을 무료로 나눠 주는 것입니다. "
                   "아직 코인을 내지 않았는데 돈이 계속 모이고 있는 곳을 골랐습니다._\n"]
        a_lines += [airdrop_line(i, d, g) for i, d in enumerate(drops, 1)]
        for gd in guides:
            a_lines.append(f"📚 참고할 만한 글: {slack.link(gd['url'], ' '.join(fr.ko_terms(to_korean(gd['title'], 200)).split())[:90])}"
                           " _(에어드랍 정보 사이트)_")
        a_lines.append("_예상 금액은 비슷한 프로젝트들의 가격 수준으로 계산한 추정일 뿐이고, 에어드랍 자체도 확정된 것이 아닙니다. "
                       "돈을 맡기면 해킹이나 손실 위험도 있으니, 꼭 공식 사이트 주소가 맞는지 확인하신 뒤 "
                       "감당할 수 있는 만큼만 해 보시는 것이 좋습니다._")
        doc.append({"kind": "section", "title": "🪂 에어드랍이 기대되는 프로젝트", "lines": a_lines})

    # 국내 거래소 공지·속보
    kn_lines = []
    for p in upbit:
        icon = {"listing": "🟢 업비트 신규 상장", "delist": "🔴 업비트 상장 폐지", "warning": "🟡 업비트 투자 유의 지정"}[p.extra["kind"]]
        kn_lines.append(f"{icon} · {slack.link(p.url, p.extra['title'][:90])}")
    for p in flash:
        mood = ""
        if p.extra.get("bull") or p.extra.get("bear"):
            mood = f" _(호재라는 표 {p.extra.get('bull', 0)} · 악재라는 표 {p.extra.get('bear', 0)})_"
        kn_lines.append(f"⚡ {slack.link(p.url, g.explain(p.extra['title'][:100]))}{mood}")
    if kn_lines:
        doc.append({"kind": "divider"})
        doc.append({"kind": "section", "title": "🇰🇷 국내 거래소 공지·속보", "lines": kn_lines})

    # 유명인사 인사이트
    if picks:
        doc.append({"kind": "divider"})
        ins_lines = []
        for p in picks:
            icon = {"x": "𝕏", "bluesky": "🦋", "farcaster": "🟪", "blog": "📝"}[p.source]
            meta = f" · ❤️ {fr.count(p.likes)}" if p.likes else ""
            ko = g.explain(fr.ko_terms(to_korean(p.extra.get("title") if p.source == "blog" else p.text, 450)))
            quote = ""
            if p.extra.get("quote"):
                q = p.extra["quote"]
                quote = (f"\n> ↪ 인용한 글: "
                         f"{slack.esc(g.explain(fr.ko_terms(to_korean(q['text'], 200))))[:260]}")
            ins_lines.append(
                f"{icon} *{slack.esc(who(p.author))}*{meta} · {slack.link(p.url, '원문 보기')}\n"
                f"> {slack.esc(ko).replace(chr(10), chr(10) + '> ')[:700]}{quote}\n")
        doc.append({"kind": "section", "title": "🗣️ 업계 유명인들이 한 말 (한국어 번역)", "lines": ins_lines})

    # 텔레그램 속보·온체인
    if news:
        doc.append({"kind": "divider"})
        n_lines = []
        for p in news:
            views = p.extra.get("views", 0)
            meta = f" · 조회 {fr.count(views)}" if views else ""
            ko = slack.esc(g.explain(fr.ko_terms(to_korean(p.text, 350))))[:400].replace("\n", " ")
            n_lines.append(f"📡 *{slack.esc(who(p.author))}*{meta} · {slack.link(p.url, '원문 보기')}\n> {ko}\n")
        doc.append({"kind": "section", "title": "📡 속보·고래 움직임 (해외 텔레그램 채널)", "lines": n_lines})

    # 레딧
    if rtop:
        doc.append({"kind": "divider"})
        r_lines = [
            f"{i}. {slack.link(p.url, fr.ko_terms(to_korean(p.extra.get('title', ''), 200))[:150])} "
            f"_({slack.esc(fr.subreddit(p.handle))})_"
            for i, p in enumerate(rtop, 1)]
        doc.append({"kind": "section", "title": "👾 해외 코인 커뮤니티(레딧)에서 오늘 뜬 글", "lines": r_lines})

    # 매체 분석·뉴스
    if media:
        doc.append({"kind": "divider"})
        m_lines = [f"📰 {slack.link(p.url, g.explain(_head(p)))} _({slack.esc(fr.outlet(p.author))})_" for p in media]
        doc.append({"kind": "section", "title": "📰 주요 언론 기사 (해외 기사는 번역)", "lines": m_lines})

    # 국내 커뮤니티
    if kr:
        doc.append({"kind": "divider"})
        k_lines = []
        for p in kr:
            meta = [f"조회 {fr.count(p.extra.get('views', 0))}"]
            if p.likes:
                meta.append(f"추천 {p.likes}")
            if p.extra.get("comments"):
                meta.append(f"댓글 {p.extra['comments']}")
            k_lines.append(f"💬 {slack.link(p.url, p.text[:80])} _({slack.esc(p.handle)} · {' · '.join(meta)})_")
        doc.append({"kind": "section", "title": "🇰🇷 국내 커뮤니티 인기 글 (디시·코인판)", "lines": k_lines})

    doc.append({"kind": "context", "text": slack.esc(status_line(results))[:2900]})

    alpha.scrub_doc(doc)
    fresh_degen = [t for t in hot if t["key"] not in degen_prev]
    has_news = bool(picks or news or rtop or tr["surges"] or new_trending or new_chains or movers or launches
                    or upbit or flash or kr or media or fresh_degen or drops)
    return {"doc": doc, "blocks": slack.blocks_from_doc(doc), "has_news": has_news, "trends": tr, "picks": picks,
            "news": news, "reddit": rtop, "trending": trending, "photo": pick_photo(tr, trending, stamp, cfg),
            "extra_seen": launches + upbit + flash + kr + media, "drops": drops,
            "guide_urls": [g_["url"] for g_ in guides],
            "chain_updates": chain_updates, "new_chains": new_chains, "movers": movers, "hot": hot,
            "tvl_snapshot": alpha.tvl_snapshot(llama, cfg.get("chains", {}).get("snapshot_min_tvl", 1_000_000)),
            "fallback": f"{BOT_NAME} {kst:%m/%d %H:%M} — 새 체인 {len(new_chains)} · 핫토큰 {len(hot)} · "
                        f"인사이트 {len(picks)} · 속보 {len(news) + len(flash)} · 급상승 {len(tr['surges'])}"}


def status_line(results):
    """맨 아래 한 줄: 몇 곳을 살펴봤고 어디가 쉬었는지."""
    ok = sum(1 for r in results if r.ok)
    missing = [fr.SOURCE_KR.get(r.name, r.name) for r in results if not r.ok]
    for r in results:
        if r.ok:
            missing += [re.sub(r"\(.*?\)", "", m).strip() for m in re.findall(r"실패 ([^,]+)", r.note or "")]
    total_posts = sum(len(r.posts) for r in results)
    line = f"📦 이번에 살펴본 자료: 글 {total_posts:,}개 · {len(results)}곳 중 {ok}곳 정상"
    if missing:
        names = []
        for m in missing:
            names += [x.strip() for x in m.split(",") if x.strip()]
        line += f" (이번에 못 가져온 곳: {', '.join(dict.fromkeys(names))})"
    return line


def chain_line(c, posts, g):
    """새 블록체인 1건: 대표 코인 / 맡겨진 돈 / 가장 활발한 거래 / 자료 / 관련 글."""
    ll, gt = c.get("llama"), c.get("gt")
    out = [f"🆕 *{slack.esc(fr.chain(c['name']))}* — 새로 확인된 블록체인입니다"]
    links = []
    if ll:
        if ll.get("token"):
            out.append(f"      • 대표 코인: {slack.esc(ll['token'])}")
        out.append(f"      • 맡겨진 돈: {fr.dollars(ll['tvl'])}")
        links.append(slack.link(llama_url(ll["name"]), "디파이라마"))
        if ll.get("gecko_id"):
            links.append(slack.link(f"https://www.coingecko.com/en/coins/{ll['gecko_id']}", "코인게코"))
    if gt:
        pools = onchain.network_pools(gt["id"])
        if pools:
            top = pools[0]
            out.append(f"      • 가장 활발한 거래: {slack.esc(top['symbol'])} · 하루 거래액 {fr.dollars(top['volume_24h'])}")
        links.append(slack.link(f"https://www.geckoterminal.com/{gt['id']}/pools", "거래 현황"))
    if links:
        out.append("      • 자료: " + " · ".join(links))
    n, hits = alpha.chain_mentions(c["name"], posts)
    if n:
        out.append(f"      • 관련 글 {n}건: " + " / ".join(slack.link(p.url, _head(p, 60)) for p in hits))
    else:
        out.append("      _아직 뉴스나 커뮤니티에서 다룬 글은 없습니다. 아주 초기 단계로 보입니다._")
    return "\n".join(out) + "\n"


CATEGORY_KR = {"Dexs": "탈중앙 거래소", "Lending": "코인 대출", "Yield": "이자 수익", "Yield Aggregator": "이자 수익 자동 운용",
               "Farm": "예치 보상(파밍)", "Derivatives": "선물 거래소", "Perps": "선물 거래소", "Prediction Market": "예측 시장",
               "Restaking": "재예치(리스테이킹)", "Liquid Restaking": "재예치(리스테이킹)", "Collateral Markets": "담보 시장",
               "Basis Trading": "차익 거래 수익", "CDP": "담보 대출형 스테이블코인", "Options Vault": "옵션 자동 운용",
               "Leveraged Farming": "빌린 돈으로 예치 보상 키우기", "Restaked BTC": "비트코인 재예치", "Launchpad": "신규 코인 공개 판매",
               "NFT Marketplace": "NFT 거래소", "Decentralized AI": "탈중앙 인공지능", "Liquidity Manager": "거래 자금 자동 관리",
               "Options": "옵션 거래소", "Insurance": "보험", "Indexes": "지수 상품", "Synthetics": "합성 자산",
               "CDP Manager": "담보 대출 관리", "Governance Incentives": "투표 보상", "OTC Marketplace": "장외 거래",
               "Staking Pool": "스테이킹", "Bridge": "블록체인 간 이동", "Gaming": "게임", "SoFi": "소셜 금융",
               "Payments": "결제", "Privacy": "개인정보 보호", "Stablecoins": "스테이블코인", "Onchain Capital Allocator": "자금 운용",
               "Uncollateralized Lending": "무담보 대출", "RWA Lending": "실물자산 담보 대출", "Liquid Staking": "스테이킹",
               "전체 디파이": "디파이 전체"}


def airdrop_line(i, d, g):
    """에어드랍 후보 1건: 블록체인 / 분야 / 맡겨진 돈 / 어떤 곳 / 눈여겨볼 이유 / 에어드랍이 나온다면 / 자료."""
    det = d.get("detail") or {}
    cat = CATEGORY_KR.get(d["category"], d["category"])
    flow = [f"{k} {fr.change(d[f])}" for k, f in (("일주일 새", "change_7d"), ("한 달 새", "change_30d"))
            if d.get(f) is not None]
    out = [f"🪂 *{i}. {slack.esc(d['name'])}*" + (" ⭐ 관심 프로젝트" if d.get("watch") else ""),
           f"      • 블록체인: {slack.esc(fr.chains(d.get('chains')))}",
           f"      • 분야: {slack.esc(cat)}",
           f"      • 맡겨진 돈: {fr.dollars(d['tvl'])}" + (f" ({', '.join(flow)})" if flow else "")]
    if det.get("description"):
        first = re.split(r"(?<=[.!?])\s+", det["description"].strip())[0]  # 첫 문장만 → 중간에서 잘리지 않게
        desc = " ".join(fr.ko_terms(to_korean(first, 300)).split())
        if len(desc) > 160:
            desc = desc[:158].rstrip() + "…"
        out.append(f"      • 어떤 곳인가요: {slack.esc(g.explain(desc))}")
    if d.get("reasons"):
        out.append("      • 눈여겨볼 이유: " + slack.esc(" / ".join(d["reasons"])))
    est = d.get("estimate")
    if est:
        out.append(f"      • 에어드랍이 나온다면: {est['deposit']:,}달러를 맡겼을 때 약 *{est['mid']:,.0f}달러* "
                   f"(맡긴 돈의 {est['mid_pct']:.1f}%) 정도로 예상됩니다. "
                   f"적게는 {est['low']:,.0f}달러, 많게는 {est['high']:,.0f}달러까지 볼 수 있습니다.")
        out.append(f"        _계산 방법: 같은 분야({slack.esc(CATEGORY_KR.get(est['basis'], est['basis']))})에서 이미 코인을 낸 "
                   f"{est['n']}곳의 가격 수준(맡겨진 돈 대비 시가총액이 보통 {est['ratio']:.2f}배)에, "
                   "전체 코인의 3~10%를 이용자에게 나눠 준다고 가정했습니다._")
    links = [slack.link(d["url"], "공식 사이트")] if d.get("url") else []
    links.append(slack.link(f"https://defillama.com/protocol/{d['slug']}", "디파이라마"))
    tw = d.get("twitter") or det.get("twitter")
    if tw:
        links.append(slack.link(f"https://x.com/{tw}", "엑스(트위터)"))
    out.append("      • 자료: " + " · ".join(links))
    if d.get("hits"):
        out.append("      • 관련 글: " + " / ".join(slack.link(p.url, _head(p, 60)) for p in d["hits"]))
    return "\n".join(out) + "\n"


def degen_line(t, fresh, g):
    """블록체인 거래소 인기 코인 1건."""
    tag = " 🆕 새로 등장" if fresh else ""
    out = [f"🎲 *{slack.link(t['url'], t['symbol'] or t['name'])}* _({slack.esc(fr.chain(t['chain']))})_{tag}",
           f"      • 가격: 하루 *{fr.change(t['change_24h'])}* · 최근 1시간 {fr.change(t['change_1h'])}",
           f"      • 하루 거래액 {fr.dollars(t['volume_24h'])} · {g.explain('유동성')} {fr.dollars(t['liquidity'])} · "
           f"시가총액 {fr.dollars(t['mcap'])}"]
    more = []
    if t.get("age_hours") is not None:
        more.append(age_txt(t["age_hours"]))
    if t.get("buyers_24h"):
        more.append(f"하루 동안 산 지갑 {t['buyers_24h']:,}개 · 판 지갑 {t.get('sellers_24h') or 0:,}개")
    if more:
        out.append("      • " + " · ".join(more))
    return "\n".join(out) + "\n"


def telegram_messages(msg):
    return tgsend.split_html(tgsend.render(msg["doc"]))


def slack_text(blocks):
    out = []
    for b in blocks:
        if b["type"] == "section":
            out.append(b["text"]["text"] + "\n")
        elif b["type"] == "header":
            out.append("# " + b["text"]["text"])
        elif b["type"] == "context":
            out.append("_" + b["elements"][0]["text"] + "_")
        elif b["type"] == "divider":
            out.append("---")
    return "\n".join(out)


def write_outbox(msg, now):
    d = os.path.join(ROOT, "data", "outbox")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{now:%Y%m%d}.md")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n\n## {now.isoformat()}\n")
        f.write(slack_text(msg["blocks"]) + "\n")
    return path


def deliver(msg):
    """슬랙·텔레그램 각각 시도. 한쪽 예외·실패가 다른 쪽을 막지 않는다."""
    try:
        slack_ok, slack_detail = slack.send(msg["blocks"], msg["fallback"])
    except Exception as e:  # noqa: BLE001
        slack_ok, slack_detail = False, f"{type(e).__name__}: {str(e)[:100]}"
    try:
        tg = tgsend.send_briefing(telegram_messages(msg), msg.get("photo"))
    except Exception as e:  # noqa: BLE001
        tg = {"configured": tgsend.config() is not None, "ok": False, "detail": f"{type(e).__name__}: {str(e)[:100]}"}
    return {"slack": {"configured": slack_detail != "SLACK_WEBHOOK_URL 없음", "ok": slack_ok, "detail": slack_detail},
            "telegram": tg}


def run(dry_run=False, force=False, cfg=None, now=None):
    load_dotenv()
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)
    st = state.load()
    results = collect_all(cfg)
    msg = build_message(cfg, results, st, now)

    for r in results:
        print(f"[{r.name}] ok={r.ok} {r.note}")
    print(f"인사이트 {len(msg['picks'])} · 속보 {len(msg['news'])} · 레딧 {len(msg['reddit'])} · 급상승 {len(msg['trends']['surges'])} "
          f"· 기준선 {msg['trends']['runs']}회 · 새 체인 {len(msg['new_chains'])} · TVL급증 {len(msg['movers'])} "
          f"· 핫토큰 {len(msg['hot'])} · 국내·매체 {len(msg['extra_seen'])}")

    if dry_run:
        print(json.dumps(msg["blocks"], ensure_ascii=False, indent=1))
        return 0

    attempted = bool(msg["has_news"] or force)
    res = {"slack": {"configured": False, "ok": False, "detail": "새 소식 없음 — 전송 생략"},
           "telegram": {"configured": False, "ok": False, "detail": "새 소식 없음 — 전송 생략"}}
    if attempted:
        write_outbox(msg, now)
        res = deliver(msg)
    for name in ("slack", "telegram"):
        print(f"{name}: sent={res[name]['ok']} {res[name]['detail']}")
    sent = res["slack"]["ok"] or res["telegram"]["ok"]
    configured = res["slack"]["configured"] or res["telegram"]["configured"]
    if attempted and sent and not all(res[n]["ok"] for n in res if res[n]["configured"]):
        print("::warning::슬랙·텔레그램 중 한쪽만 전송됐습니다 (보낸 글 기록은 진행)")

    # 보낸 글(seen) 기록: 둘 중 하나라도 나갔으면 기록한다. 한쪽만 실패했다고 다음 회차에 다시 보내면
    # 성공한 쪽에 같은 글이 두 번 나가기 때문. 둘 다 실패면 기록하지 않아 다음 회차에 재시도.
    # 트렌드 히스토리는 항상 쌓는다.
    if sent:
        state.mark_seen(st, msg["picks"] + msg["news"] + msg["reddit"] + msg["extra_seen"], now)
    # 새 체인은 알림이 나갔을 때만 '아는 체인'으로 넘긴다(실패하면 다음 회차에 다시 알림).
    # 알릴 게 없는 기록(처음 목록 적재·이미 아는 이름)은 바로 반영.
    if sent or not msg["new_chains"]:
        st.setdefault("chains_known", {}).update(msg["chain_updates"])
    if msg["tvl_snapshot"]:
        st["chain_tvl"] = (st.get("chain_tvl", []) + [{"ts": now.isoformat(), "tvl": msg["tvl_snapshot"]}])[-20:]
    if sent:
        shown = st.setdefault("airdrop_shown", {})
        shown.update({d["name"]: now.isoformat() for d in msg["drops"]})
        cutoff = (now - timedelta(days=14)).isoformat()
        st["airdrop_shown"] = {k: v for k, v in shown.items() if v >= cutoff}
        st["seen"].update({u: now.isoformat() for u in msg["guide_urls"]})
    if msg["hot"] and (sent or not attempted):
        st["degen_prev"] = [t["key"] for t in msg["hot"]]
    st["mentions"].append({"ts": now.isoformat(), **msg["trends"]["snapshot"]})
    if msg["trending"]:
        st["trending_prev"] = [c["symbol"] for c in msg["trending"]]
    state.save(st, now=now, history_runs=cfg["trends"]["history_runs"])

    return 0 if (sent or not attempted or not configured) else 1


_PREVIEW_CSS = """body{font-family:system-ui,'Malgun Gothic',sans-serif;background:#e7ebf0;margin:0;padding:16px;color:#111}
h1{font-size:20px}h2{font-size:16px;margin-top:28px}.meta{background:#fff;padding:12px;border-radius:8px}
.bubble{background:#fff;max-width:560px;padding:10px 14px;border-radius:12px;white-space:pre-wrap;line-height:1.45;
box-shadow:0 1px 2px #0002;overflow-wrap:anywhere}.bubble blockquote{margin:4px 0;padding:2px 8px;border-left:3px solid #3390ec;
background:#3390ec14}.bubble a{color:#168acd}pre{background:#1e1e1e;color:#ddd;padding:10px;overflow-x:auto;
white-space:pre-wrap;font-size:12px;border-radius:6px}img{max-width:560px;width:100%;border-radius:12px}"""


def write_previews(msg, out_dir, now):
    """텔레그램 HTML 원문·분할 결과·사진 URL + 슬랙 텍스트를 파일로. 전송·state 저장 없음."""
    os.makedirs(out_dir, exist_ok=True)
    chunks = telegram_messages(msg)
    photo = msg.get("photo")
    e = lambda s: html.escape(s or "", quote=True)  # noqa: E731
    cfg = tgsend.config()
    parts = [f"<!doctype html><meta charset='utf-8'><title>텔레그램 미리보기 · {TG_TOPIC}</title>",
             f"<style>{_PREVIEW_CSS}</style><h1>텔레그램 미리보기 · {e(TG_TOPIC)}</h1>",
             "<div class='meta'>",
             f"생성: {e(now.isoformat())}<br>메시지 {len(chunks)}개 (UTF-16 길이: "
             f"{', '.join(str(tgsend.u16(c)) for c in chunks)} / 한도 {tgsend.MAX_MESSAGE})<br>",
             f"새 소식 여부(has_news): {msg['has_news']}<br>",
             f"텔레그램 설정: {'있음 (' + cfg['token_source'] + ')' if cfg else '없음 — 실제 실행 시 건너뜀'}<br>",
             "전송 순서: sendPhoto(사진 먼저) → sendMessage × N (사이 1초), parse_mode=HTML, "
             "disable_web_page_preview=true, message_thread_id=TELEGRAM_TOPIC_PULSE</div>"]
    if photo:
        parts += [f"<h2>0. 사진 ({e(photo['kind'])}) — sendPhoto</h2>",
                  f"<p>URL ({len(photo['url'])}자): <a href='{e(photo['url'])}'>{e(photo['url'])}</a></p>",
                  f"<img src='{e(photo['url'])}' alt='photo'>",
                  f"<p>캡션 (UTF-16 {tgsend.u16(photo['caption'])} / {tgsend.MAX_CAPTION}):</p>",
                  f"<div class='bubble'>{photo['caption']}</div><pre>{e(photo['caption'])}</pre>"]
    else:
        parts.append("<h2>0. 사진 없음</h2>")
    for i, c in enumerate(chunks, 1):
        parts += [f"<h2>{i}/{len(chunks)}. sendMessage — {tgsend.u16(c)}자</h2>",
                  f"<div class='bubble'>{c}</div>", f"<details><summary>HTML 원문</summary><pre>{e(c)}</pre></details>"]
    parts += ["<h2>분할 전 전체 HTML</h2>", f"<pre>{e(tgsend.render(msg['doc']))}</pre>"]
    tg_path = os.path.join(out_dir, "preview_telegram.html")
    with open(tg_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))

    slack_path = os.path.join(out_dir, "preview_slack.txt")
    with open(slack_path, "w", encoding="utf-8") as f:
        f.write(f"# 슬랙 미리보기 {now.isoformat()} · 블록 {len(msg['blocks'])}개\n")
        f.write(f"fallback: {msg['fallback']}\n\n")
        f.write(slack_text(msg["blocks"]))
        f.write("\n\n===== Block Kit JSON =====\n")
        f.write(json.dumps(msg["blocks"], ensure_ascii=False, indent=1))
    return tg_path, slack_path


def preview(cfg=None, now=None, out_dir=None):
    load_dotenv()
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)
    st = state.load()  # 읽기만 한다. state.save 는 부르지 않음
    results = collect_all(cfg)
    msg = build_message(cfg, results, st, now)
    for r in results:
        print(f"[{r.name}] ok={r.ok} {r.note}")
    tg_path, slack_path = write_previews(msg, out_dir or os.path.join(ROOT, "data"), now)
    print(f"텔레그램 미리보기: {tg_path}\n슬랙 미리보기: {slack_path}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--preview-telegram", action="store_true", help="전송·state 저장 없이 미리보기 파일만 만든다")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.preview_telegram:
        return preview()
    return run(dry_run=a.dry_run, force=a.force)


if __name__ == "__main__":
    sys.exit(main())
