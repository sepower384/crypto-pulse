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


def ent_label(ent):
    return ent[1:] if ent.startswith("#") else f"${ent}"


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
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"🌐 크립토 펄스 · {kst:%m/%d %H:%M} KST"}}]

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
                trend_lines.append(f"🆕 *{ent_label(s['ent'])}* {s['n']}회 (새로 등장){tail}")
            else:
                trend_lines.append(f"📈 *{ent_label(s['ent'])}* {s['n']}회 · 평소 {s['base']}회 → ×{s['ratio']}{tail}")
        if not trend_lines:
            trend_lines.append("_평소 대비 급상승한 코인·키워드 없음_")
    top_txt = " · ".join(
        f"{ent_label(e)} {n}" + (" ↑" if r and r >= 1.5 else " ↓" if r and r <= 0.6 else "")
        for e, n, r in tr["top"][:8])
    if top_txt:
        trend_lines.append(f"🔢 많이 언급: {top_txt}")
    if not tr["warm"]:
        trend_lines.append(f"_기준선 쌓는 중 ({tr['runs']}/3회) — 3회차부터 급상승 감지_")
    if new_trending:
        tt = ", ".join(
            f"{c['symbol']}" + (f"({c['change_24h']:+.0f}%)" if c["change_24h"] is not None else "")
            for c in new_trending[:7])
        trend_lines.append(f"🔥 코인게코 트렌딩 신규 진입: {tt}")
    blocks += slack.chunked_sections("📊 언급량 트렌드 변화", trend_lines)

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
                f"{icon} *{slack.esc(p.author)}*{meta} · {slack.link(p.url, '원문')}\n"
                f"> {slack.esc(ko).replace(chr(10), chr(10) + '> ')[:600]}{quote}\n")
        blocks += slack.chunked_sections("🗣️ 유명인사 인사이트", ins_lines)

    # 텔레그램 속보·온체인
    if news:
        blocks.append({"type": "divider"})
        n_lines = []
        for p in news:
            views = p.extra.get("views", 0)
            meta = f" · 👁 {slack.fmt_num(views)}" if views else ""
            ko = slack.esc(to_korean(p.text, 350))[:320].replace("\n", " ")
            n_lines.append(f"📡 *{slack.esc(p.author)}*{meta} · {slack.link(p.url, '원문')}\n> {ko}\n")
        blocks += slack.chunked_sections("📡 온체인·속보 (텔레그램)", n_lines)

    # 레딧
    if rtop:
        blocks.append({"type": "divider"})
        r_lines = [
            f"{p.rank}. [{slack.esc(p.handle)}] {slack.link(p.url, to_korean(p.extra.get('title', ''), 200)[:150])}"
            for p in rtop]
        blocks += slack.chunked_sections("👾 레딧 오늘의 인기글", r_lines)

    status = " | ".join(f"{'✅' if r.ok else '⚠️'} {SOURCE_LABEL.get(r.name, r.name)} {r.note}" for r in results)
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
