"""오프라인 로직 테스트 — 네트워크 없이 돈다.  python -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, "tests", "fixtures")

from pulse import insights, main, slack, state, trends  # noqa: E402
from pulse.model import Post, SourceResult  # noqa: E402
from pulse.sources import blogs, bluesky, coingecko, farcaster, reddit, telegram, x  # noqa: E402

NOW = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def cfg():
    return main.load_config()


def post(source="x", pid="1", text="Bitcoin to the moon", hours=1, likes=100, handle="saylor", **kw):
    return Post(source=source, id=pid, author=kw.pop("author", handle), handle=handle, text=text,
                url=f"https://example.com/{pid}", created=NOW - timedelta(hours=hours), likes=likes, **kw)


class TestReddit(unittest.TestCase):
    def test_build_url_combines_subs(self):
        url = reddit.build_url(["Bitcoin", "ethereum"], "top", 50)
        self.assertEqual(url, "https://www.reddit.com/r/Bitcoin+ethereum/top.rss?t=day&limit=50")

    def test_parse_real_rss(self):
        posts = reddit.parse(fixture("reddit_top.xml"))
        self.assertEqual(len(posts), 3)
        p = posts[0]
        self.assertEqual(p.source, "reddit")
        self.assertEqual(p.rank, 1)
        self.assertTrue(p.handle.startswith("r/"))
        self.assertRegex(p.id, r"^[a-z0-9]+$")
        self.assertIsNotNone(p.created)
        self.assertNotIn("submitted by", p.text)
        self.assertEqual([q.rank for q in posts], [1, 2, 3])


class TestX(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(fixture("x_usertweets.json"))
        self.posts = {p.id: p for p in x.parse_graphql(self.data, "saylor", "Michael Saylor")}

    def test_keeps_own_tweets_only(self):
        self.assertEqual(set(self.posts), {"100", "201", "202"})

    def test_drops_retweets_and_replies_to_others(self):
        self.assertNotIn("203", self.posts)
        self.assertNotIn("301", self.posts)
        self.assertNotIn("300", self.posts)

    def test_fields(self):
        p = self.posts["201"]
        self.assertEqual(p.likes, 15300)
        self.assertEqual(p.reposts, 2100)
        self.assertEqual(p.extra["views"], 1200345)
        self.assertEqual(p.url, "https://x.com/saylor/status/201")
        self.assertEqual(p.created, datetime(2026, 9, 12, 13, 0, tzinfo=timezone.utc))

    def test_visibility_wrapper_note_tweet_and_quote(self):
        p = self.posts["202"]
        self.assertTrue(p.text.startswith("Long form note"))
        self.assertEqual(p.extra["quote"]["handle"], "WatcherGuru")

    def test_cookie_formats(self):
        a = x.parse_cookies("auth_token=abc; ct0=def")
        b = x.parse_cookies('{"auth_token": "abc", "ct0": "def"}')
        c = x.parse_cookies('[{"name":"auth_token","value":"abc","domain":".x.com"},'
                            '{"name":"ct0","value":"def","domain":".x.com"},'
                            '{"name":"other","value":"z","domain":".google.com"}]')
        for cookies in (a, b, c):
            names = {k["name"]: k["value"] for k in cookies}
            self.assertEqual(names, {"auth_token": "abc", "ct0": "def"})
            self.assertTrue(all(k["domain"] == ".x.com" for k in cookies))
        self.assertEqual(x.parse_cookies(""), [])

    def test_collect_skips_without_cookie(self):
        with mock.patch.dict(os.environ, {"X_COOKIES": ""}):
            r = x.collect(cfg())
        self.assertFalse(r.ok)
        self.assertIn("X_COOKIES", r.note)

    def test_dom_fallback(self):
        rows = [
            {"datetime": "2026-09-12T10:00:00.000Z", "link": "https://x.com/saylor/status/555",
             "text": "Buy bitcoin", "stats": "12 replies, 340 reposts, 5,600 likes, 9 bookmarks", "social": ""},
            {"datetime": "2026-09-12T09:00:00.000Z", "link": "https://x.com/other/status/556",
             "text": "not mine", "stats": "", "social": ""},
            {"datetime": "2026-09-12T08:00:00.000Z", "link": "https://x.com/saylor/status/557",
             "text": "reposted", "stats": "", "social": "Michael Saylor reposted"},
        ]
        ps = x.parse_dom(rows, "saylor", "Michael Saylor")
        self.assertEqual([p.id for p in ps], ["555"])
        self.assertEqual(ps[0].likes, 5600)
        self.assertEqual(ps[0].reposts, 340)


class TestOtherSources(unittest.TestCase):
    def test_bluesky_real_payload(self):
        ps = bluesky.parse(json.loads(fixture("bluesky_feed.json")), "Vitalik")
        self.assertTrue(ps)
        for p in ps:
            self.assertTrue(p.url.startswith("https://bsky.app/profile/"))
            self.assertIsNotNone(p.created)

    def test_bluesky_skips_reposts_and_replies(self):
        data = {"feed": [
            {"post": {"uri": "at://d/app.bsky.feed.post/a1", "author": {"handle": "v.ca"},
                      "record": {"text": "mine", "createdAt": "2026-09-12T00:00:00Z"}}},
            {"reason": {"$type": "repost"}, "post": {"uri": "at://d/p/a2", "author": {"handle": "o"}, "record": {}}},
            {"post": {"uri": "at://d/p/a3", "author": {"handle": "v.ca"},
                      "record": {"text": "reply", "reply": {"parent": {}}}}},
        ]}
        self.assertEqual([p.text for p in bluesky.parse(data, "V")], ["mine"])

    def test_blog_rss_and_atom(self):
        rss = """<?xml version="1.0"?><rss><channel><item><title>On &lt;b&gt;Stablecoins&lt;/b&gt;</title>
        <link>https://b.com/1</link><pubDate>Fri, 11 Sep 2026 10:00:00 GMT</pubDate>
        <description>&lt;p&gt;Long essay&lt;/p&gt;</description></item></channel></rss>"""
        atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom post</title>
        <link rel="alternate" href="https://a.com/2"/><updated>2026-09-10T00:00:00Z</updated>
        <summary>hi</summary></entry></feed>"""
        r = blogs.parse(rss, "B")[0]
        self.assertEqual(r.extra["title"], "On Stablecoins")
        self.assertIn("Long essay", r.text)
        self.assertEqual(r.created, datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc))
        a = blogs.parse(atom, "A")[0]
        self.assertEqual(a.url, "https://a.com/2")

    def test_telegram_real_page(self):
        ps = telegram.parse(fixture("telegram_channel.html"), "wublockchainenglish", "Wu Blockchain")
        self.assertEqual(len(ps), 4)
        p = ps[0]
        self.assertEqual(p.url, "https://t.me/wublockchainenglish/26000")
        self.assertEqual(p.key, "telegram:wublockchainenglish/26000")
        self.assertEqual(p.created.tzinfo, timezone.utc)
        self.assertGreater(p.extra["views"], 0)
        for q in ps:
            self.assertNotIn("<", q.text)

    def test_telegram_skips_media_only_and_reply_quote(self):
        page = ('<div class="tgme_widget_message_wrap"><div data-post="ch/1">'
                '<div class="tgme_widget_message_text js-message_reply_text">quoted</div>'
                '<div class="tgme_widget_message_text js-message_text" dir="auto">$BTC &#036;80K<br/>next</div>'
                '<span class="tgme_widget_message_views">2.5K</span><time datetime="2026-09-12T00:00:00+00:00"></time>'
                '</div></div><div class="tgme_widget_message_wrap"><div data-post="ch/2">photo only</div></div>')
        ps = telegram.parse(page, "ch", "Ch")
        self.assertEqual([p.text for p in ps], ["$BTC $80K\nnext"])
        self.assertEqual(ps[0].extra["views"], 2500)
        self.assertEqual(telegram.parse_views("1.2M"), 1_200_000)
        self.assertEqual(telegram.parse_views("987"), 987)

    def test_farcaster_real_payload(self):
        ps = farcaster.parse(json.loads(fixture("farcaster_casts.json")), "vitalik.eth", "Vitalik")
        self.assertTrue(ps)
        for p in ps:
            self.assertTrue(p.url.startswith("https://farcaster.xyz/vitalik.eth/0x"))
            self.assertIsNotNone(p.created)
            self.assertGreater(p.likes, 0)
        self.assertLess(len(ps), 4)  # 답글은 빠진다

    def test_farcaster_skips_replies_and_others(self):
        data = {"result": {"casts": [
            {"hash": "0xaaa", "text": "mine", "timestamp": 1788968354000, "author": {"username": "v"},
             "reactions": {"count": 3}, "recasts": {"count": 1}},
            {"hash": "0xbbb", "text": "reply", "parentHash": "0x1", "author": {"username": "v"}},
            {"hash": "0xccc", "text": "recast of other", "author": {"username": "someone"}},
        ]}}
        ps = farcaster.parse(data, "v", "V")
        self.assertEqual([(p.text, p.likes, p.reposts) for p in ps], [("mine", 3, 1)])

    def test_coingecko_parse(self):
        data = {"coins": [{"item": {"symbol": "hype", "name": "Hyperliquid", "id": "hyperliquid",
                                    "market_cap_rank": 12, "data": {"price_change_percentage_24h": {"usd": 7.456}}}},
                          {"item": {"symbol": "pepe", "name": "Pepe", "data": {}}}]}
        coins = coingecko.parse(data)
        self.assertEqual(coins[0]["symbol"], "HYPE")
        self.assertEqual(coins[0]["change_24h"], 7.5)
        self.assertIsNone(coins[1]["change_24h"])


class TestTrends(unittest.TestCase):
    def test_extract_coins_and_narratives(self):
        e = trends.extract("Bitcoin ETF inflows hit record as $SOL rallies; Fed rate cut next?")
        self.assertTrue({"BTC", "SOL", "#ETF", "#금리·연준"} <= e)

    def test_strict_tickers_need_cashtag(self):
        self.assertNotIn("HYPE", trends.extract("so much hype about this"))
        self.assertIn("HYPE", trends.extract("$hype breaking out"))
        self.assertIn("HYPE", trends.extract("Hyperliquid volume ATH"))
        self.assertNotIn("LINK", trends.extract("click the LINK below"))
        self.assertNotIn("COIN", trends.extract("flip a COIN"))

    def test_uppercase_ticker_word_boundary(self):
        self.assertIn("ETH", trends.extract("ETH/BTC ratio"))
        self.assertNotIn("ETH", trends.extract("METHOD"))
        self.assertNotIn("ADA", trends.extract("CANADA"))

    def test_cashtag_ignores_dollar_amounts(self):
        self.assertEqual({t for t in trends.extract("raised $100M and $5B") if not t.startswith("#")}, set())

    def test_is_crypto(self):
        self.assertTrue(trends.is_crypto("on-chain data shows whales"))
        self.assertFalse(trends.is_crypto("Tesla FSD v14 is amazing"))
        self.assertFalse(trends.is_crypto("Trump says tariffs"))

    def _hist(self, n_runs, counts, total=100):
        return [{"ts": f"r{i}", "total": total, "counts": dict(counts)} for i in range(n_runs)]

    def test_warmup_no_surges(self):
        posts = [post(pid=str(i), text="$ZEC pumping") for i in range(10)]
        r = trends.analyze(posts, self._hist(2, {"BTC": 10}), cfg())
        self.assertFalse(r["warm"])
        self.assertEqual(r["surges"], [])
        self.assertEqual(r["top"][0][:2], ("ZEC", 10))

    def test_surge_by_share(self):
        c = cfg()
        posts = [post(pid=f"z{i}", text="$ZEC privacy season") for i in range(12)]
        posts += [post(pid=f"f{i}", text="gm") for i in range(88)]
        hist = self._hist(10, {"ZEC": 3, "BTC": 20})
        r = trends.analyze(posts, hist, c)
        s = {x["ent"]: x for x in r["surges"]}
        self.assertIn("ZEC", s)
        self.assertEqual(s["ZEC"]["ratio"], 4.0)
        self.assertEqual(s["ZEC"]["base"], 3.0)
        self.assertFalse(s["ZEC"]["new"])

    def test_share_normalization_ignores_source_outage(self):
        # 평소 글 200개 중 BTC 20 (10%). 이번엔 X 가 죽어 글 50개 중 BTC 5 (10%) → 급상승 아님, 급락도 아님
        posts = [post(pid=f"b{i}", text="bitcoin") for i in range(5)] + [post(pid=f"f{i}", text="gm") for i in range(45)]
        r = trends.analyze(posts, self._hist(5, {"BTC": 20}, total=200), cfg())
        self.assertEqual(r["surges"], [])
        self.assertAlmostEqual(r["top"][0][2], 1.0)

    def test_new_entity(self):
        posts = [post(pid=f"n{i}", text="$NEWCOIN listing") for i in range(4)] + [post(pid="f", text="gm")]
        r = trends.analyze(posts, self._hist(5, {"BTC": 5}), cfg())
        self.assertTrue(r["surges"][0]["new"])
        self.assertEqual(r["surges"][0]["ent"], "NEWCOIN")

    def test_min_mentions(self):
        posts = [post(pid="a", text="$RARE")] + [post(pid=f"f{i}", text="gm") for i in range(9)]
        r = trends.analyze(posts, self._hist(5, {"BTC": 5}), cfg())
        self.assertEqual(r["surges"], [])

    def test_window_excludes_blogs_and_old(self):
        ps = [post(pid="a", hours=2), post(pid="b", hours=30), post(source="blog", pid="c", hours=1),
              post(source="reddit", pid="d", hours=5)]
        self.assertEqual({p.id for p in trends.window(ps, cfg(), NOW)}, {"a", "d"})

    def test_trending_changes(self):
        cur = [{"symbol": "BTC"}, {"symbol": "HYPE"}]
        self.assertEqual(trends.trending_changes(cur, ["BTC"]), [{"symbol": "HYPE"}])
        self.assertEqual(trends.trending_changes(cur, []), [])  # 첫 실행은 전부 '신규'로 도배하지 않음


class TestInsights(unittest.TestCase):
    def test_filters(self):
        c = cfg()
        ps = [
            post(pid="new", text="Bitcoin treasury strategy explained in detail today", likes=5000),
            post(pid="old", text="Bitcoin treasury strategy explained long ago", hours=50),
            post(pid="short", text="gm", likes=10),
            post(pid="elon1", handle="elonmusk", text="Starship launch was incredible today folks",
                 likes=900000, extra={"require_crypto": True}),
            post(pid="elon2", handle="elonmusk", text="Dogecoin to the moon, maybe on Mars too",
                 likes=300000, extra={"require_crypto": True}),
            post(source="reddit", pid="r1", text="reddit post here long enough"),
            post(source="blog", pid="blog1", text="Essay on stablecoins", hours=100, likes=0),
        ]
        ids = [p.id for p in insights.select(ps, {}, c, NOW)]
        self.assertIn("new", ids)
        self.assertIn("elon2", ids)
        self.assertIn("blog1", ids)
        for bad in ("old", "short", "elon1", "r1"):
            self.assertNotIn(bad, ids)

    def test_seen_and_per_handle_limit_and_dedupe(self):
        c = cfg()
        ps = [post(pid=str(i), text=f"Ethereum roadmap update number {i} is here", likes=100 + i) for i in range(6)]
        picked = insights.select(ps, {}, c, NOW)
        self.assertEqual(len(picked), c["x"]["per_handle_limit"])
        seen = {p.key: "t" for p in picked}
        again = insights.select(ps, seen, c, NOW)
        self.assertFalse({p.id for p in again} & {p.id for p in picked})
        dup = [post(pid="x1", text="Same text on both networks about ETH"),
               post(source="bluesky", pid="b1", handle="vitalik.ca", text="Same text on both networks about ETH")]
        self.assertEqual(len(insights.select(dup, {}, c, NOW)), 1)
        full = "A note on recursive STARK mempools (EIP-8288) This is an EIP that I am hoping we get into I-star"
        cut = post(source="bluesky", pid="b2", handle="vitalik.ca", author="Vitalik", likes=9,
                   text="A note on recursive STARK mempools (EIP-8288) This is an EI... Read more: longer.blue")
        fc = post(source="farcaster", pid="0xf", handle="vitalik.eth", author="Vitalik", text=full, likes=75)
        self.assertEqual([p.id for p in insights.select([cut, fc], {}, c, NOW)], ["0xf"])

    def test_score_orders_by_engagement(self):
        hi = post(pid="h", text="Bitcoin", likes=100000)
        lo = post(pid="l", text="Bitcoin", likes=10)
        self.assertGreater(insights.score(hi, NOW), insights.score(lo, NOW))

    def test_farcaster_counts_as_insight(self):
        c = cfg()
        text = "New EIP for quantum-safe ETH signatures"
        fc = post(source="farcaster", pid="0xabc", handle="vitalik.eth", text=text, hours=60)
        xp = post(pid="x9", handle="saylor", text="Bitcoin is digital capital, always has been", hours=60)
        stale = post(source="bluesky", pid="b9", handle="vitalik.ca", text=text + " again", hours=120)
        self.assertEqual([q.id for q in insights.select([fc, xp, stale], {}, c, NOW)], ["0xabc"])

    def test_channel_news(self):
        c = cfg()
        tg = lambda pid, text, views, hours=1, ch="lookonchainchannel", **extra: post(  # noqa: E731
            source="telegram", pid=pid, handle=ch, text=text, hours=hours, likes=0, extra={"views": views, **extra})
        ps = [
            tg("a/1", "Whale bought 5,000 $ETH ($20M) on Binance", 30000),
            tg("a/2", "A fresh wallet withdrew 900 BTC from OKX", 20000),
            tg("a/3", "Smart money dumped $PEPE again", 10000),          # 채널당 2개 제한
            tg("a/4", "Old whale news about SOL", 90000, hours=20),     # 너무 오래됨
            tg("w/1", "Breaking: US jobs report beats estimates", 50000, ch="WatcherGuru", require_crypto=True),
            tg("w/2", "JUST IN: Bitcoin ETF sees $1B inflow", 40000, ch="WatcherGuru", require_crypto=True),
            tg("u/1", "Strategy acquired 1,000 $BTC. Bitcoin treasury season.", 40000, ch="wublockchainenglish"),
            post(source="x", pid="x1", text="reddit? no, X post about ETH"),
        ]
        insight = [post(pid="201", text="Strategy acquired 1,000 $BTC. Bitcoin treasury season.")]
        ids = [p.id for p in insights.channel_news(ps, {"telegram:a/9": "t"}, c, NOW, exclude=insight)]
        self.assertEqual(set(ids), {"a/1", "a/2", "w/2"})
        self.assertEqual(ids, ["w/2", "a/1", "a/2"])  # 조회수·코인언급 순
        self.assertEqual(insights.channel_news(ps, {f"telegram:{i}": "t" for i in ids}, c, NOW,
                                               exclude=insight)[0].id, "a/3")

    def test_reddit_top_order_and_seen(self):
        ps = [post(source="reddit", pid=str(i), rank=r) for i, r in enumerate([3, 1, 2])]
        top = insights.reddit_top(ps, {"reddit:1": "t"}, cfg())
        self.assertEqual([p.rank for p in top], [2, 3])


class TestSlackAndState(unittest.TestCase):
    def test_escape_and_link(self):
        self.assertEqual(slack.esc("a<b>&c"), "a&lt;b&gt;&amp;c")
        self.assertEqual(slack.link("https://u", "x|y<z>"), "<https://u|x¦y&lt;z&gt;>")

    def test_chunking_under_limit(self):
        blocks = slack.chunked_sections("T", ["x" * 1000 for _ in range(10)])
        self.assertGreater(len(blocks), 1)
        self.assertTrue(all(len(b["text"]["text"]) <= 3000 for b in blocks))

    def test_fmt_num(self):
        self.assertEqual(slack.fmt_num(15300), "15.3K")
        self.assertEqual(slack.fmt_num(1_200_000), "1.2M")
        self.assertEqual(slack.fmt_num(999), "999")

    def test_send_without_webhook(self):
        with mock.patch.dict(os.environ, {"SLACK_WEBHOOK_URL": ""}):
            ok, detail = slack.send([], "t")
        self.assertFalse(ok)

    def test_state_roundtrip_and_prune(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            st = state.load(path)
            st["seen"] = {"x:old": (NOW - timedelta(days=8)).isoformat(), "x:new": NOW.isoformat()}
            st["mentions"] = [{"ts": str(i), "total": 1, "counts": {}} for i in range(100)]
            state.save(st, path, now=NOW, history_runs=10)
            st2 = state.load(path)
            self.assertEqual(set(st2["seen"]), {"x:new"})
            self.assertEqual(len(st2["mentions"]), 20)


class TestPipeline(unittest.TestCase):
    """소스를 가짜로 바꿔 build_message/run 전체 흐름 검증 (네트워크·번역 차단)."""

    def results(self):
        return [
            SourceResult("x", True, [
                post(pid="201", text="Strategy acquired 1,000 $BTC. Bitcoin treasury season.", likes=15300),
                post(pid="202", handle="VitalikButerin", author="Vitalik",
                     text="New post on L2 rollups & <security> tradeoffs for ETH", likes=4000),
            ], "2/18"),
            SourceResult("reddit", True, [post(source="reddit", pid=f"r{i}", rank=i + 1, text=f"Bitcoin thread {i}",
                                               extra={"title": f"Bitcoin thread {i}"}) for i in range(8)], "8건"),
            SourceResult("bluesky", True, [], "0건"),
            SourceResult("farcaster", True, [post(source="farcaster", pid="0xfc1", handle="jessepollak",
                                                  text="Base is shipping onchain summer again for USDC")], "1/1"),
            SourceResult("telegram", True, [post(source="telegram", pid="lookonchainchannel/7",
                                                 handle="lookonchainchannel", author="Lookonchain", likes=0,
                                                 text="Whale deposited 3,000 $ETH to Binance", extra={"views": 12000})],
                         "1/1채널"),
            SourceResult("blogs", False, [], "실패: URLError"),
            SourceResult("coingecko", True, [], "2종", extra={"trending": [
                {"symbol": "BTC", "change_24h": 1.0}, {"symbol": "HYPE", "change_24h": 12.3}]}),
        ]

    def setUp(self):
        self.p1 = mock.patch("pulse.main.to_korean", side_effect=lambda t, *a, **k: t)
        self.p2 = mock.patch("pulse.main.summarize", return_value=[])
        self.p1.start()
        self.p2.start()

    def tearDown(self):
        mock.patch.stopall()

    def test_build_message(self):
        st = {"seen": {}, "mentions": [], "trending_prev": ["BTC"]}
        msg = main.build_message(cfg(), self.results(), st, NOW)
        text = json.dumps(msg["blocks"], ensure_ascii=False)
        self.assertTrue(msg["has_news"])
        self.assertEqual(msg["blocks"][0]["type"], "header")
        self.assertIn("03:00 KST", msg["blocks"][0]["text"]["text"])
        self.assertIn("유명인사 인사이트", text)
        self.assertIn("&lt;security&gt;", text)
        self.assertIn("HYPE(+12%)", text)
        self.assertIn("⚠️ 블로그", text)
        self.assertIn("🟪 *jessepollak*", text)
        self.assertIn("온체인·속보", text)
        self.assertIn("👁 12.0K", text)
        self.assertEqual([p.id for p in msg["news"]], ["lookonchainchannel/7"])
        self.assertEqual(len(msg["reddit"]), 5)
        self.assertLessEqual(len(msg["blocks"]), 50)

    def test_run_marks_seen_only_when_sent(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with mock.patch("pulse.main.collect_all", return_value=self.results()), \
                    mock.patch.object(state, "PATH", path), \
                    mock.patch("pulse.state.load", side_effect=lambda p=path: state.load.__wrapped__(p) if False else _load(path)), \
                    mock.patch("pulse.state.save", side_effect=lambda st, **kw: _save(st, path, **kw)), \
                    mock.patch("pulse.main.write_outbox"), \
                    mock.patch("pulse.main.load_dotenv"):
                with mock.patch("pulse.slack.send", return_value=(False, "HTTPError")):
                    code = main.run(now=NOW)
                self.assertEqual(code, 1)
                st = _load(path)
                self.assertEqual(st["seen"], {})
                self.assertEqual(len(st["mentions"]), 1)
                with mock.patch("pulse.slack.send", return_value=(True, "ok")):
                    self.assertEqual(main.run(now=NOW + timedelta(hours=2)), 0)
                st = _load(path)
                self.assertIn("x:201", st["seen"])
                self.assertIn("reddit:r0", st["seen"])
                self.assertIn("telegram:lookonchainchannel/7", st["seen"])
                self.assertEqual(st["trending_prev"], ["BTC", "HYPE"])


_real_load, _real_save = state.load, state.save


def _load(path):
    return _real_load(path)


def _save(st, path, **kw):
    return _real_save(st, path, **kw)


if __name__ == "__main__":
    unittest.main()
