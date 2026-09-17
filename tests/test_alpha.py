"""크립토마스 확장(국내 커뮤니티·속보·매체·온체인·새 체인) 오프라인 테스트."""
import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pulse import alpha, main, state, trends  # noqa: E402
from pulse.model import SourceResult  # noqa: E402
from pulse.sources import community_kr, kr_news, onchain  # noqa: E402
from test_pulse import NOW, TestPipeline, cfg, fixture, post  # noqa: E402


def jfix(name):
    return json.loads(fixture(name))


class TestKrSources(unittest.TestCase):
    def test_dcinside_rows(self):
        posts = community_kr.parse_dc(fixture("dc_list.html"), "bitcoins_new1", "디시 비트코인갤")
        self.assertGreaterEqual(len(posts), 5)
        p = posts[0]
        self.assertEqual(p.source, "dcinside")
        self.assertTrue(p.id.startswith("bitcoins_new1/"))
        self.assertIn("gall.dcinside.com/board/view/?id=bitcoins_new1&no=", p.url)
        self.assertIsNotNone(p.created)
        self.assertEqual(p.created.tzinfo.utcoffset(None), timedelta(0))
        self.assertIn("views", p.extra)
        self.assertNotIn("<", p.text)

    def test_coinpan_skips_pinned_notices(self):
        posts = community_kr.parse_coinpan(fixture("coinpan_best.html"))
        self.assertTrue(posts)
        for p in posts:
            self.assertRegex(p.url, r"^https://www\.coinpan\.com/best/\d+$")
            self.assertNotIn("공식 어플", p.text)
            self.assertTrue(p.handle.startswith("코인판"))
            self.assertTrue(p.extra["date_only"])

    def test_upbit_kinds(self):
        posts = kr_news.parse_upbit(jfix("upbit_notices.json"))
        self.assertTrue(posts)
        self.assertEqual(kr_news.upbit_kind("렌조(REZ) 신규 거래지원 안내"), "listing")
        self.assertEqual(kr_news.upbit_kind("레이븐코인(RVN) 거래지원 종료 안내"), "delist")
        self.assertEqual(kr_news.upbit_kind("인젝티브(INJ) 거래 유의 종목 지정 안내"), "warning")
        self.assertEqual(kr_news.upbit_kind("서비스 점검 안내"), "")
        self.assertTrue(all(p.url.startswith("https://upbit.com/service_center/notice?id=") for p in posts))

    def test_coinness(self):
        posts = kr_news.parse_coinness(jfix("coinness.json"))
        self.assertTrue(posts)
        self.assertRegex(posts[0].url, r"^https://coinness\.com/news/\d+$")
        self.assertIsNotNone(posts[0].created)
        self.assertIn("bull", posts[0].extra)


class TestOnchainSources(unittest.TestCase):
    def test_trending_pools_joined_with_tokens(self):
        pools = onchain.parse_trending(jfix("gt_trending.json"), now=NOW)
        self.assertEqual(len(pools), 4)
        p = pools[0]
        self.assertTrue(p["symbol"])
        self.assertTrue(p["chain"])
        self.assertTrue(p["url"].startswith("https://www.geckoterminal.com/"))
        self.assertIsInstance(p["volume_24h"], float)

    def test_boost_pairs_best_liquidity(self):
        d = jfix("ds_boosts.json")
        rows = onchain.parse_boost_pairs(d["pairs"], {b["tokenAddress"].lower(): b for b in d["boosts"]}, now=NOW)
        self.assertTrue(rows)
        self.assertEqual(len({r["key"] for r in rows}), len(rows))
        self.assertTrue(all(r["url"].startswith("https://dexscreener.com/") for r in rows))

    def test_llama_and_networks(self):
        ll = onchain.parse_llama_chains([{"name": "A", "tvl": 5, "tokenSymbol": "AA"}, {"tvl": 1}])
        self.assertEqual(ll, [{"name": "A", "tvl": 5.0, "token": "AA", "gecko_id": None, "chain_id": None}])
        nets = onchain.parse_gt_networks({"data": [{"id": "x", "attributes": {"name": "X Chain"}}]})
        self.assertEqual(nets[0]["name"], "X Chain")


class TestNewChains(unittest.TestCase):
    def llama(self, *names):
        return [{"name": n, "tvl": 1e6 * (i + 1), "token": None, "gecko_id": None, "chain_id": None}
                for i, n in enumerate(names)]

    def gt(self, *pairs):
        return [{"id": i, "name": n, "cg_platform": None} for i, n in pairs]

    def test_first_run_seeds_silently(self):
        new, upd = alpha.detect_new_chains(self.llama("Ethereum", "Base"), self.gt(("eth", "Ethereum")), {}, NOW)
        self.assertEqual(new, [])
        self.assertEqual(set(upd), {"llama:Ethereum", "llama:Base", "gt:eth"})

    def test_new_chain_detected_and_merged(self):
        known = {"llama:Ethereum": "t", "gt:eth": "t"}
        new, upd = alpha.detect_new_chains(self.llama("Ethereum", "Megachain"),
                                           self.gt(("eth", "Ethereum"), ("megachain", "MegaChain")), known, NOW)
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["llama"]["name"], "Megachain")
        self.assertEqual(new[0]["gt"]["id"], "megachain")
        self.assertIn("gt:megachain", upd)

    def test_failed_source_is_not_a_signal(self):
        known = {"llama:Ethereum": "t", "gt:eth": "t"}
        new, upd = alpha.detect_new_chains([], [], known, NOW)
        self.assertEqual((new, upd), ([], {}))

    def test_known_on_other_source_is_not_new(self):
        known = {"llama:Robinhood": "t", "gt:eth": "t"}
        new, _ = alpha.detect_new_chains(self.llama("Robinhood"), self.gt(("eth", "Ethereum"), ("robinhood", "Robinhood")),
                                         known, NOW)
        self.assertEqual(new, [])

    def test_tvl_movers_uses_about_24h_ago(self):
        hist = [{"ts": (NOW - timedelta(hours=24)).isoformat(), "tvl": {"Up": 10_000_000, "Flat": 10_000_000}},
                {"ts": (NOW - timedelta(hours=2)).isoformat(), "tvl": {"Up": 1}}]
        ll = [{"name": "Up", "tvl": 15_000_000.0}, {"name": "Flat", "tvl": 10_500_000.0}]
        m = alpha.tvl_movers(ll, hist, NOW, cfg())
        self.assertEqual([c["name"] for c in m], ["Up"])
        self.assertEqual(m[0]["pct"], 50.0)
        self.assertEqual(alpha.tvl_movers(ll, hist[1:], NOW, cfg()), [])

    def test_mentions_and_launch_news(self):
        posts = [post(source="news", pid="n1", text="Megachain mainnet goes live", extra={"title": "Megachain mainnet goes live"}),
                 post(source="reddit", pid="r1", text="megachain airdrop?", rank=1),
                 post(source="news", pid="n2", text="Bitcoin ETF flows", extra={"title": "Bitcoin ETF flows"})]
        n, hits = alpha.chain_mentions("Megachain", posts)
        self.assertEqual(n, 2)
        self.assertEqual([p.id for p in alpha.launch_news(posts, {}, NOW)], ["n1"])
        self.assertEqual(alpha.chain_mentions("Sei", posts)[0], 0)  # 너무 짧은 이름은 오탐이 많아 안 센다


class TestPicks(unittest.TestCase):
    def tok(self, sym, liq, vol, key=None):
        return {"symbol": sym, "name": sym, "chain": "Solana", "chain_id": "solana", "change_24h": 10.0,
                "change_1h": 1.0, "volume_24h": vol, "liquidity": liq, "mcap": 1e6, "age_hours": 5,
                "url": "https://e.com/" + sym, "key": key or sym}

    def test_degen_filters_rugs_and_stables(self):
        pools = [self.tok("GOOD", 100_000, 500_000), self.tok("RUG", 4_000, 1_000_000),
                 self.tok("WETH", 1e8, 1e8), self.tok("GOOD", 200_000, 900_000, key="dup")]
        boosted = [self.tok("SHILL", 80_000, 300_000), self.tok("GOOD", 80_000, 300_000), self.tok("TINY", 10, 10)]
        hot, shill = alpha.degen_pick(pools, boosted, cfg())
        self.assertEqual([t["symbol"] for t in hot], ["GOOD"])
        self.assertEqual([t["symbol"] for t in shill], ["SHILL"])

    def test_kr_hot_min_views_and_seen(self):
        posts = [post(source="dcinside", pid="a", text="비트코인 간다", extra={"views": 900, "comments": 5}),
                 post(source="dcinside", pid="b", text="ㅇㅇ", extra={"views": 20}),
                 post(source="coinpan", pid="c", text="리플 전망", hours=20, extra={"views": 1500, "comments": 3}),
                 post(source="dcinside", pid="d", text="이미 보냄", extra={"views": 5000})]
        got = alpha.kr_hot(posts, {"dcinside:d": "t"}, cfg(), NOW)
        self.assertEqual({p.id for p in got}, {"a", "c"})

    def test_kr_mentions_in_trends(self):
        posts = [post(source="dcinside", pid=str(i), text="리플이 간다 이더리움은?") for i in range(3)]
        r = trends.analyze(trends.window(posts, cfg(), NOW), [], cfg())
        self.assertEqual(dict(r["kr_top"]), {"XRP": 3, "ETH": 3})
        self.assertEqual(r["kr_total"], 3)

    def test_old_snapshot_version_ignored(self):
        old = [{"ts": str(i), "total": 100, "counts": {"BTC": 1}} for i in range(5)]
        r = trends.analyze([post(text="$ZEC")], old, cfg())
        self.assertFalse(r["warm"])
        self.assertEqual(r["snapshot"]["v"], trends.SNAPSHOT_VERSION)

    def test_banned_word_filtered_and_scrubbed(self):
        posts = [post(source="dcinside", pid="a", text="세력이 올린다"), post(source="dcinside", pid="b", text="리플 간다")]
        self.assertEqual([p.id for p in alpha.clean_posts(posts)], ["b"])
        doc = [{"kind": "section", "title": "세력 움직임", "lines": ["강세 세력이 매수"]}, {"kind": "divider"}]
        alpha.scrub_doc(doc)
        self.assertNotIn("세력", json.dumps(doc, ensure_ascii=False))

    def test_kr_coin_counts_with_upbit_names(self):
        names = kr_news.parse_markets([{"market": "KRW-JPYC", "korean_name": "제이피와이코인"},
                                       {"market": "KRW-SEI", "korean_name": "세이"}])
        self.assertEqual(names, {"제이피와이코인": "JPYC"})
        posts = [post(source="dcinside", pid=str(i), text=t) for i, t in
                 enumerate(["제이피와이코인 간다", "도지사 선거", "비트겟 이벤트", "비트 떡락", "리플이 최고"])]
        top, total = alpha.kr_coin_counts(posts, names)
        self.assertEqual(dict(top), {"JPYC": 1, "BTC": 1, "XRP": 1})
        self.assertEqual(total, 5)

    def test_airdrop_pick(self):
        base = {"category": "Dexs", "chains": ["Base"], "change_30d": 10.0, "listed_at": None, "url": "u",
                "twitter": "", "slug": "s", "product": ""}
        tokenless = [{**base, "name": "Zeta Farm", "tvl": 5e7, "change_7d": 60.0},
                     {**base, "name": "Slowpoke", "tvl": 5e7, "change_7d": -10.0},
                     {**base, "name": "Coinbase Vault", "tvl": 9e9, "change_7d": 90.0},
                     {**base, "name": "Polymarket", "tvl": 3e8, "change_7d": -5.0},
                     {**base, "name": "Seen Before", "tvl": 9e8, "change_7d": 50.0}]
        posts = [post(source="reddit", pid="a", text="Zeta Farm airdrop season 2 is live, farming now"),
                 post(source="news", pid="b", text="Polymarket odds rose 5 percentage points today")]
        c = cfg()
        c["airdrop"] = {**c["airdrop"], "watch": ["Polymarket"], "max_items": 3}
        got = alpha.airdrop_pick(tokenless, posts, c, NOW, {"Seen Before": (NOW - timedelta(hours=3)).isoformat()})
        names = [d["name"] for d in got]
        self.assertEqual(names[0], "Zeta Farm")
        self.assertNotIn("Coinbase Vault", names)
        self.assertNotIn("Seen Before", names)
        self.assertEqual(got[0]["mentions"], 1)
        poly = [d for d in got if d["name"] == "Polymarket"][0]
        self.assertTrue(poly["watch"])
        self.assertEqual(poly["mentions"], 0)

    def test_tokenless_parse(self):
        data = {"parentProtocols": [{"id": "parent#tok", "name": "Tok", "symbol": "TOK"},
                                    {"id": "parent#free", "name": "Free", "symbol": "-", "twitter": "free"}],
                "protocols": [
                    {"name": "Tok V2", "symbol": "-", "parentProtocol": "parent#tok", "category": "Dexs", "tvl": 5e7},
                    {"name": "Free A", "symbol": "-", "parentProtocol": "parent#free", "category": "Dexs", "tvl": 3e7,
                     "tvlPrevWeek": 2e7, "chains": ["Base"]},
                    {"name": "Free B", "symbol": "-", "parentProtocol": "parent#free", "category": "Lending", "tvl": 2e7},
                    {"name": "Bridge X", "symbol": "-", "category": "Bridge", "tvl": 9e9},
                    {"name": "Tiny", "symbol": "-", "category": "Dexs", "tvl": 1e5}]}
        rows = onchain.parse_tokenless(data)
        self.assertEqual([r["name"] for r in rows], ["Free"])
        self.assertEqual(rows[0]["tvl"], 5e7)
        self.assertEqual(rows[0]["slug"], "free")
        self.assertEqual(rows[0]["twitter"], "free")

    def test_news_pick_per_outlet(self):
        posts = [post(source="news", pid=f"n{i}", author="Decrypt", handle="Decrypt", text=f"Bitcoin analysis {i}",
                      extra={"title": f"Bitcoin analysis {i}"}) for i in range(4)]
        self.assertEqual(len(alpha.news_pick(posts, {}, cfg(), NOW)), 2)


class TestPipelineAlpha(TestPipeline):
    def results(self):
        base = super().results()
        pools = onchain.parse_trending(jfix("gt_trending.json"), now=NOW)
        return base + [
            SourceResult("kr_community", True, [
                post(source="dcinside", pid="bitcoins_new1/1", handle="디시 비트코인갤", text="리플 떡상 가즈아",
                     extra={"views": 800, "comments": 12, "title": "리플 떡상 가즈아"})], "디시 1"),
            SourceResult("kr_news", True, [
                post(source="upbit", pid="u1", handle="upbit", text="렌조(REZ) 신규 거래지원 안내",
                     extra={"title": "렌조(REZ) 신규 거래지원 안내", "kind": "listing"}),
                post(source="coinness", pid="c1", handle="coinness", text="블랙록 ETH 매수\n본문",
                     extra={"title": "블랙록 ETH 매수", "bull": 5, "bear": 1, "important": True})], "2"),
            SourceResult("news", True, [
                post(source="news", pid="m1", author="The Block", handle="The Block",
                     text="Megachain mainnet launch draws DeFi deposits",
                     extra={"title": "Megachain mainnet launch draws DeFi deposits"})], "1/1"),
            SourceResult("onchain", True, [], "ok", extra={
                "trending_pools": pools, "boosted": [],
                "llama_chains": [{"name": "Ethereum", "tvl": 5e10, "token": "ETH", "gecko_id": "ethereum", "chain_id": 1},
                                 {"name": "Megachain", "tvl": 3.2e7, "token": "MEGA", "gecko_id": None, "chain_id": 777}],
                "gt_networks": [{"id": "eth", "name": "Ethereum", "cg_platform": None}],
                "tokenless": [{"name": "Zeta Farm", "product": "Zeta", "category": "Dexs", "chains": ["Base"],
                               "tvl": 5e7, "change_7d": 40.0, "change_30d": 80.0, "listed_at": None,
                               "url": "https://zeta.example", "twitter": "zeta", "slug": "zeta-farm"}]}),
        ]

    def setUp(self):
        super().setUp()
        mock.patch("pulse.sources.onchain.network_pools", return_value=[]).start()

    def st(self):
        return {"seen": {}, "mentions": [], "trending_prev": ["BTC"],
                "chains_known": {"llama:Ethereum": "t", "gt:eth": "t"}, "degen_prev": ["x"]}

    def test_build_message_has_alpha_sections(self):
        msg = main.build_message(cfg(), self.results(), self.st(), NOW)
        text = main.slack_text(msg["blocks"])
        self.assertNotIn("세력", text)
        self.assertIn("크립토마스", text)
        self.assertIn("🆕 새 체인·메인넷 알파", text)
        self.assertIn("*Megachain*", text)
        self.assertIn("토큰 MEGA", text)
        self.assertIn("defillama.com/chain/Megachain", text)
        self.assertIn("화제: 수집한 글 1건", text)
        self.assertIn("🎰 디젠 레이더", text)
        self.assertIn("🟢 업비트 상장", text)
        self.assertIn("호재 5 · 악재 1", text)
        self.assertIn("국내 커뮤니티 핫글", text)
        self.assertIn("🪂 에어드랍 파밍 레이더", text)
        self.assertIn("*Zeta Farm*", text)
        self.assertIn("국내 커뮤니티에서 많이 나온 코인", text)
        self.assertEqual([c["name"] for c in msg["new_chains"]], ["Megachain"])
        self.assertIn("llama:Megachain", msg["chain_updates"])
        self.assertLessEqual(len(msg["blocks"]), 50)

    def test_run_commits_chain_state_only_when_sent(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            real_load, real_save = state.load, state.save
            state.save(self.st(), path, now=NOW)
            with mock.patch("pulse.main.collect_all", return_value=self.results()), \
                    mock.patch("pulse.state.load", side_effect=lambda p=path: real_load(path)), \
                    mock.patch("pulse.state.save", side_effect=lambda st, **kw: real_save(st, path, **kw)), \
                    mock.patch("pulse.main.write_outbox"), mock.patch("pulse.main.load_dotenv"), \
                    mock.patch("pulse.telegram.send_briefing",
                               return_value={"configured": False, "ok": False, "detail": "x"}):
                with mock.patch("pulse.slack.send", return_value=(False, "HTTPError")):
                    main.run(now=NOW)
                st = real_load(path)
                self.assertNotIn("llama:Megachain", st["chains_known"])
                self.assertEqual(len(st["chain_tvl"]), 1)
                self.assertEqual(st["degen_prev"], ["x"])
                with mock.patch("pulse.slack.send", return_value=(True, "ok")):
                    main.run(now=NOW + timedelta(hours=2))
                st = real_load(path)
                self.assertIn("llama:Megachain", st["chains_known"])
                self.assertIn("upbit:u1", st["seen"])
                self.assertIn("dcinside:bitcoins_new1/1", st["seen"])
                self.assertIn("news:m1", st["seen"])
                self.assertNotEqual(st["degen_prev"], ["x"])
                self.assertIn("Zeta Farm", st["airdrop_shown"])


if __name__ == "__main__":
    unittest.main()
