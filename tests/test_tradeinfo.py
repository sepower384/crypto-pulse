"""코인별 거래 장소·투자 방법·차익거래 정리 테스트 (오프라인)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pulse import tradeinfo  # noqa: E402
from pulse.sources import markets  # noqa: E402


def tk(market, mid, usd, vol=1e6, target="USDT", **kw):
    return {"market": market, "id": mid, "base": "ZEC", "target": target, "usd": usd, "volume": vol,
            "trust": kw.get("trust"), "stale": kw.get("stale", False), "anomaly": False, "url": ""}


ROWS = [tk("Binance", "binance", 100.0), tk("Upbit", "upbit", 103.0, target="KRW"),
        tk("Uniswap V3 (Ethereum)", "uniswap_v3", 102.0, target="0XA0B86991C6218B36C1D19D4A2E9EB0CE3606EB48"),
        tk("Toobit", "toobit", 100.5), tk("OKX", "okex", 100.2), tk("Tiny", "tiny", 150.0, vol=10),
        tk("Old", "old", 90.0, stale=True)]


class TestTradeInfo(unittest.TestCase):
    def test_venues(self):
        v = tradeinfo.venues(ROWS)
        self.assertEqual(v["kr"], ["업비트"])
        self.assertEqual(v["global"], ["바이낸스", "오케이엑스"])
        self.assertEqual(v["dex"], ["유니스왑(이더리움)"])
        self.assertEqual(v["other"], 2)  # Toobit, Tiny (오래된 가격은 제외)
        odd = tradeinfo.venues([tk("Up V3 (Robinhood)", "up_v3", 1.0, target="0X" + "A" * 40)])
        self.assertEqual((odd["dex"], odd["other"]), ([], 1))  # 모르는 블록체인 거래소 이름은 숨김

    def test_spread_ignores_krw_stale_and_tiny(self):
        sp = tradeinfo.spread(ROWS)
        self.assertAlmostEqual(sp["pct"], 2.0)
        self.assertEqual(sp["low"]["id"], "binance")
        self.assertEqual(tradeinfo.market_label(sp["high"]), "유니스왑 V3(이더리움)")
        same = [tk("Uniswap V3 (Robinhood)", "u3", 1.0, target="0X" + "A" * 40),
                tk("Uniswap V3 (Robinhood)", "u3b", 1.05, target="0X" + "B" * 40)]
        self.assertIsNone(tradeinfo.spread(same))
        self.assertIsNone(tradeinfo.spread(ROWS[:1]))

    def test_futures_and_lines(self):
        deriv = markets.parse_derivatives([
            {"market": "Binance (Futures)", "index_id": "ZEC", "contract_type": "perpetual",
             "funding_rate": 0.01, "volume_24h": 9e9, "price": 101},
            {"market": "Bybit (Futures)", "index_id": "ZEC", "contract_type": "perpetual",
             "funding_rate": -0.002, "volume_24h": 5e9, "price": 100},
            {"market": "MEXC (Futures)", "index_id": "ZEC", "contract_type": "perpetual",
             "funding_rate": 0.5, "volume_24h": 1e9, "price": 3},
            {"market": "Binance (Futures)", "index_id": "ZEC", "contract_type": "quarterly"}])
        names, n, funding = tradeinfo.futures("zec", deriv, ref=100)
        self.assertEqual((names, n, funding), (["바이낸스", "바이비트"], 2, 0.01))  # 가격이 딴판인 같은 이름 코인은 제외
        lines, sp = tradeinfo.lines_for("ZEC", ROWS, deriv)
        txt = "\n".join(lines)
        self.assertIn("국내 거래소: 업비트", txt)
        self.assertIn("선물 매매(오를 때·내릴 때 모두 가능): 바이낸스 · 바이비트", txt)
        self.assertIn("오른다에 건 쪽이 수수료를 내는 중", txt)
        self.assertIn("에어드랍 작업: 이미 코인이 나와 해당 없음", txt)
        self.assertIn("거래소 간 가격 차이 *2.0%*: 바이낸스 100달러 → 유니스왑 V3(이더리움) 102달러", txt)
        no_kr, _ = tradeinfo.lines_for("ZEC", ROWS[:1], {})
        self.assertIn("국내 거래소: 아직 없음", "\n".join(no_kr))

    def test_kimchi_rows(self):
        rows = tradeinfo.kimchi_rows({"BTC": {"global": 100.0, "upbit": 103.0, "bithumb": 102.0},
                                      "ETH": {"global": 10.0}})
        self.assertEqual(rows, [("BTC", "업비트 +3.0% · 빗썸 +2.0%", 3.0000000000000027)])
        flat = tradeinfo.kimchi_rows({"BTC": {"global": 100.0, "upbit": 100.01}})
        self.assertEqual(flat[0][1], "업비트 거의 같음")

    def test_dex_howto(self):
        txt = tradeinfo.dex_howto({"chain_id": "solana", "dex": "pumpswap"})
        self.assertIn("팬텀 같은 솔라나 지갑 준비 → 솔라나(SOL) 넣기 → 펌프스왑에서 교환", txt)
        self.assertIn("그 블록체인을 지원하는 지갑", tradeinfo.dex_howto({"chain_id": "zzz", "dex": ""}))

    def test_parse_tickers(self):
        rows = markets.parse_tickers({"tickers": [
            {"market": {"name": "Binance", "identifier": "binance"}, "base": "ZEC", "target": "USDT",
             "converted_last": {"usd": 1350.9}, "converted_volume": {"usd": 7e8}, "trust_score": "green",
             "is_stale": False, "is_anomaly": False, "trade_url": "u"}]})
        self.assertEqual(rows[0]["usd"], 1350.9)
        self.assertEqual(rows[0]["id"], "binance")


if __name__ == "__main__":
    unittest.main()
