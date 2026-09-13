"""1회 실행: 수집 → 트렌드/인사이트 분석 → 슬랙 전송 → state 저장.

python -m pulse.main              # 실제 전송
python -m pulse.main --dry-run    # 전송·state 저장 없이 미리보기
python -m pulse.main --force      # 새 소식 없어도 전송
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import insights, slack, state, trends
from .sources import blogs, bluesky, coingecko, farcaster, reddit, telegram, x
from .translate import summarize, to_korean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_LABEL = {"x": "X", "reddit": "Reddit", "bluesky": "Bluesky", "farcaster": "Farcaster",
                "telegram": "Telegram", "blogs": "블로그", "coingecko": "CoinGecko"}


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
           blogs.collect, coingecko.collect]
    with ThreadPoolExecutor(max_workers=len(fns)) as ex:
        return list(ex.map(lambda f: f(cfg), fns))


COIN_KR = {"BTC": "비트코인", "ETH": "이더리움", "SOL": "솔라나", "XRP": "리플", "DOGE": "도지코인",
           "BNB": "바이낸스코인", "ADA": "에이다", "SUI": "수이", "HYPE": "하이퍼리퀴드", "LINK": "체인링크",
           "TRX": "트론", "AVAX": "아발란체", "TON": "톤", "PEPE": "페페", "COIN": "코인베이스 주식",
           "MSTR": "스트래티지 주식", "USDT": "테더"}
# 누가 누군지 모르면 인용문이 그냥 영어 잡담으로 읽힌다
WHO = {"Vitalik Buterin": "이더리움 창시자", "Michael Saylor": "비트코인 최대 보유기업 회장",
       "CZ": "바이낸스 창업자", "Brian Armstrong": "코인베이스 CEO", "Arthur Hayes": "비트멕스 창업자",
       "Anthony Pompliano": "비트코인 투자자·유튜버", "Raoul Pal": "매크로 투자자", "Mike Novogratz": "갤럭시디지털 CEO",
       "Cathie Wood": "ARK인베스트 CEO", "Balaji": "전 코인베이스 CTO", "Anatoly Yakovenko": "솔라나 공동창업자",
       "Hayden Adams": "유니스왑 창업자", "Haseeb Qureshi": "드래곤플라이 투자사", "ZachXBT": "온체인 사기 추적가",
       "Lookonchain": "고래 지갑 추적", "Wu Blockchain": "아시아 크립토 속보", "Peter Schiff": "대표적 비트코인 비관론자",
       "Elon Musk": "테슬라 CEO", "Lyn Alden": "매크로 애널리스트"}


def ent_label(ent):
    if ent.startswith("#"):
        return ent[1:]
    return f"{COIN_KR[ent]}({ent})" if ent in COIN_KR else f"${ent}"


def who(author):
    role = WHO.get(author)
    return f"{author} ({role})" if role else author


def build_message(cfg, results, st, now):
    posts = [p for r in results for p in r.posts]
    by_name = {r.name: r for r in results}
    tr = trends.analyze(trends.window(posts, cfg, now), st["mentions"], cfg)
    trending = by_name["coingecko"].extra.get("trending", [])
    new_trending = trends.trending_changes(trending, st["trending_prev"])
    picks = insights.select(posts, st["seen"], cfg, now)
    news = insights.channel_news(posts, st["seen"], cfg, now, exclude=picks)
    rtop = insights.reddit_top(posts, st["seen"], cfg)

    kst = now + timedelta(hours=cfg.get("timezone_offset_hours", 9))
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"🌐 크립토 펄스 · {kst.month}월 {kst.day}일 {kst:%H:%M}"}},
              slack.section("_해외 크립토 커뮤니티·유명인·속보 채널에서 최근 몇 시간 동안 오간 이야기를 한국어로 정리했어요._")]

    # AI 요약 (선택)
    lines = [f"[{p.source}] {p.author}: {p.text[:200]}" for p in picks + news]
    lines += [f"[reddit #{p.rank}] {p.extra.get('title', p.text[:150])}" for p in rtop]
    lines += [f"[급상승] {ent_label(s['ent'])} {s['n']}회 (평소 {s['base']})" for s in tr["surges"]]
    summary = summarize(lines)
    if summary:
        blocks.append(slack.section("🧭 *지금 흐름 (AI 요약)*\n" + "\n".join(f"• {slack.esc(b)}" for b in summary)))

    # 트렌드
    trend_lines = []
    if tr["warm"]:
        for s in tr["surges"]:
            ex = s["example"]
            tail = f" — {slack.link(ex.url, to_korean(ex.extra.get('title') or ex.text)[:70])}" if ex else ""
            if s["new"]:
                trend_lines.append(f"🆕 *{ent_label(s['ent'])}* — 평소엔 조용하다가 이번에 {s['n']}번 언급됐어요{tail}")
            else:
                trend_lines.append(f"📈 *{ent_label(s['ent'])}* — 평소 {s['base']}번 → 이번 {s['n']}번, "
                                   f"*{s['ratio']}배*로 이야기가 늘었어요{tail}")
        if not trend_lines:
            trend_lines.append("_평소보다 갑자기 화제가 된 코인·주제는 없어요. 조용한 편이에요._")
    top_txt = ", ".join(
        f"{ent_label(e)} {n}번" + (" (평소보다 ↑)" if r and r >= 1.5 else " (평소보다 ↓)" if r and r <= 0.6 else "")
        for e, n, r in tr["top"][:8])
    if top_txt:
        trend_lines.append(f"🔢 *가장 많이 나온 이야기*: {top_txt}")
    if not tr["warm"]:
        trend_lines.append(f"_아직 평소 수준을 배우는 중이에요 ({tr['runs']}/3회). "
                           "3번째 알림부터 '갑자기 화제가 된 코인'도 짚어드려요._")
    if new_trending:
        tt = ", ".join(
            f"{c['symbol']}" + (f"(하루 {c['change_24h']:+.0f}%)" if c["change_24h"] is not None else "")
            for c in new_trending[:7])
        trend_lines.append(f"🔥 *새로 검색이 몰리기 시작한 코인* (코인게코 인기검색 신규 진입): {tt}")
    blocks += slack.chunked_sections("📊 요즘 사람들이 제일 많이 얘기하는 것", trend_lines)

    # 유명인사 인사이트
    if picks:
        blocks.append({"type": "divider"})
        ins_lines = []
        for p in picks:
            icon = {"x": "𝕏", "bluesky": "🦋", "farcaster": "🟪", "blog": "📝"}[p.source]
            meta = f" · ❤️ {slack.fmt_num(p.likes)}" if p.likes else ""
            ko = to_korean(p.extra.get("title") if p.source == "blog" else p.text, 450)
            quote = ""
            if p.extra.get("quote"):
                q = p.extra["quote"]
                quote = f"\n> ↪ @{slack.esc(q['handle'])}: {slack.esc(to_korean(q['text'], 200))[:200]}"
            ins_lines.append(
                f"{icon} *{slack.esc(who(p.author))}*{meta} · {slack.link(p.url, '원문 보기')}\n"
                f"> {slack.esc(ko).replace(chr(10), chr(10) + '> ')[:600]}{quote}\n")
        blocks += slack.chunked_sections("🗣️ 업계 유명인들이 한 말 (한국어 번역)", ins_lines)

    # 텔레그램 속보·온체인
    if news:
        blocks.append({"type": "divider"})
        n_lines = []
        for p in news:
            views = p.extra.get("views", 0)
            meta = f" · 👁 {slack.fmt_num(views)}" if views else ""
            ko = slack.esc(to_korean(p.text, 350))[:320].replace("\n", " ")
            n_lines.append(f"📡 *{slack.esc(who(p.author))}*{meta} · {slack.link(p.url, '원문 보기')}\n> {ko}\n")
        blocks += slack.chunked_sections("📡 속보·고래 움직임 (텔레그램 채널)", n_lines)

    # 레딧
    if rtop:
        blocks.append({"type": "divider"})
        r_lines = [
            f"{i}. {slack.link(p.url, to_korean(p.extra.get('title', ''), 200)[:150])} _({slack.esc(p.handle)})_"
            for i, p in enumerate(rtop, 1)]
        blocks += slack.chunked_sections("👾 해외 코인 커뮤니티(레딧)에서 오늘 뜬 글", r_lines)

    status = "수집 현황 — " + " | ".join(
        f"{'✅' if r.ok else '⏸️'} {SOURCE_LABEL.get(r.name, r.name)} " + (r.note if r.ok else "이번엔 못 가져옴")
        for r in results)
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": slack.esc(status)[:2900]}]})

    has_news = bool(picks or news or rtop or tr["surges"] or new_trending)
    return {"blocks": blocks, "has_news": has_news, "trends": tr, "picks": picks, "news": news, "reddit": rtop,
            "trending": trending,
            "fallback": f"크립토 펄스 {kst:%m/%d %H:%M} — 인사이트 {len(picks)} · 속보 {len(news)} · 급상승 {len(tr['surges'])}"}


def write_outbox(msg, now):
    d = os.path.join(ROOT, "data", "outbox")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{now:%Y%m%d}.md")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n\n## {now.isoformat()}\n")
        for b in msg["blocks"]:
            if b["type"] == "section":
                f.write(b["text"]["text"] + "\n\n")
            elif b["type"] == "header":
                f.write("# " + b["text"]["text"] + "\n")
            elif b["type"] == "context":
                f.write("_" + b["elements"][0]["text"] + "_\n")
    return path


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
          f"· 기준선 {msg['trends']['runs']}회")

    if dry_run:
        print(json.dumps(msg["blocks"], ensure_ascii=False, indent=1))
        return 0

    sent, detail = False, "새 소식 없음 — 전송 생략"
    if msg["has_news"] or force:
        write_outbox(msg, now)
        sent, detail = slack.send(msg["blocks"], msg["fallback"])
    print(f"slack: sent={sent} {detail}")

    # 전송 실패 시 seen 을 올리지 않아 다음 회차에 다시 시도. 트렌드 히스토리는 항상 쌓는다.
    if sent:
        state.mark_seen(st, msg["picks"] + msg["news"] + msg["reddit"], now)
    st["mentions"].append({"ts": now.isoformat(), **msg["trends"]["snapshot"]})
    if msg["trending"]:
        st["trending_prev"] = [c["symbol"] for c in msg["trending"]]
    state.save(st, now=now, history_runs=cfg["trends"]["history_runs"])

    no_webhook = detail == "SLACK_WEBHOOK_URL 없음"
    return 0 if (sent or not (msg["has_news"] or force) or no_webhook) else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return run(dry_run=a.dry_run, force=a.force)


if __name__ == "__main__":
    sys.exit(main())
