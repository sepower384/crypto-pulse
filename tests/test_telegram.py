"""텔레그램 전송·변환·분할·말투·용어풀이 테스트 — 네트워크 없이 돈다."""
import glob
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.error
from datetime import timedelta
from html.parser import HTMLParser
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pulse import chart, main, state, wording  # noqa: E402
from pulse import telegram as tg  # noqa: E402
from test_pulse import NOW, TestPipeline, cfg  # noqa: E402

TG_ENV_KEYS = ("TELEGRAM_BOT_TOKEN_PULSE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_TOPIC_PULSE")
SPEC_CODES = ["globe_with_meridians", "fire", "satellite_antenna", "space_invader", "butterfly", "heart", "eye",
              "white_check_mark", "double_vertical_bar"]


class TagBalance(HTMLParser):
    ALLOWED = {"b", "i", "u", "s", "a", "code", "pre", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.text = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED:
            self.errors.append(f"허용 안 되는 태그 {tag}")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"짝 안 맞는 </{tag}>")
        else:
            self.stack.pop()

    def handle_data(self, data):
        self.text.append(data)


def balanced(html_text):
    p = TagBalance()
    p.feed(html_text)
    p.close()
    return not p.errors and not p.stack, "".join(p.text)


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def clear_tg_env():
    return mock.patch.dict(os.environ, {k: "" for k in TG_ENV_KEYS})


class TestConvert(unittest.TestCase):
    def test_spec_emoji_codes_mapped(self):
        for code in SPEC_CODES:
            out = tg.inline(f"x :{code}: y")
            self.assertNotIn(f":{code}:", out)
            self.assertIn(tg.EMOJI[code], out)

    def test_every_emoji_code_in_codebase_is_mapped(self):
        # 코드베이스(pulse/, 워크플로)에서 실제로 쓰는 :슬랙코드: 는 전부 매핑돼 있어야 한다
        used = set()
        files = glob.glob(os.path.join(ROOT, "pulse", "**", "*.py"), recursive=True)
        files += glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml"))
        for path in files:
            if path.endswith(os.path.join("pulse", "telegram.py")):
                continue
            with open(path, encoding="utf-8") as f:
                # ::error:: 같은 GitHub Actions 주석 명령은 이모지 코드가 아니다
                used |= set(re.findall(r"(?<![\w/:]):([a-z][a-z0-9_+\-]*):(?![\w:])", f.read()))
        self.assertEqual(used - set(tg.EMOJI), set())

    def test_unknown_or_time_colons_untouched(self):
        self.assertEqual(tg.emojize("03:00:12 :not_a_real_code:"), "03:00:12 :not_a_real_code:")

    def test_escape_without_double_escaping(self):
        self.assertEqual(tg.inline("a & b <c>"), "a &amp; b &lt;c&gt;")
        self.assertEqual(tg.inline("L2 &amp; &lt;security&gt;"), "L2 &amp; &lt;security&gt;")

    def test_formatting_and_links(self):
        self.assertEqual(tg.inline("*굵게* _기울임_ `코드` ~취소~"), "<b>굵게</b> <i>기울임</i> <code>코드</code> <s>취소</s>")
        self.assertEqual(tg.inline("<https://u.com/a?x=1&y=2|원문 &lt;보기&gt;>"),
                         '<a href="https://u.com/a?x=1&amp;y=2">원문 &lt;보기&gt;</a>')
        self.assertEqual(tg.inline("cz_binance_fan and 5 * 3 * 2"), "cz_binance_fan and 5 * 3 * 2")
        self.assertEqual(tg.inline("_(r/Bitcoin)_"), "<i>(r/Bitcoin)</i>")
        self.assertEqual(tg.inline("*2.0배* 늘었습니다"), "<b>2.0배</b> 늘었습니다")
        # 링크 라벨 속 밑줄·별표는 서식으로 오인하지 않는다
        self.assertEqual(tg.inline("<https://x.com/a_b_c|a_b*c>"), '<a href="https://x.com/a_b_c">a_b*c</a>')

    def test_blockquote_grouping(self):
        out = tg.slack_to_html("𝕏 *Vitalik*\n> 첫 줄\n> 둘째 &lt;줄&gt;\n&gt; 셋째\n끝")
        self.assertEqual(out, "𝕏 <b>Vitalik</b>\n<blockquote>첫 줄\n둘째 &lt;줄&gt;\n셋째</blockquote>\n끝")
        self.assertTrue(balanced(out)[0])


class TestSplit(unittest.TestCase):
    def test_short_text_single_message(self):
        self.assertEqual(tg.split_html("<b>a</b>\n\nb"), ["<b>a</b>\n\nb"])

    def test_splits_on_blank_lines_and_keeps_tags_balanced(self):
        items = [f"<b>항목 {i}</b> <a href=\"https://e.com/{i}\">원문</a>\n<blockquote>{'가나다 & ' * 40}</blockquote>"
                 for i in range(40)]
        text = "\n\n".join(items)
        chunks = tg.split_html(text)
        self.assertGreater(len(chunks), 1)
        joined = []
        for c in chunks:
            self.assertLessEqual(tg.u16(c), tg.MAX_MESSAGE)
            ok, plain = balanced(c)
            self.assertTrue(ok, c[:200])
            self.assertTrue(c.startswith("<b>항목"))  # 항목 경계에서 잘렸다
            joined.append(plain)
        self.assertEqual("".join(joined).replace("\n", ""), balanced(text)[1].replace("\n", ""))

    def test_giant_blockquote_is_closed_and_reopened(self):
        text = "<b>제목</b>\n<blockquote>" + ("🔥가나다라마바사 " * 900) + "</blockquote>"
        chunks = tg.split_html(text, limit=1000)
        self.assertGreater(len(chunks), 3)
        for c in chunks:
            self.assertLessEqual(tg.u16(c), 1000)
            self.assertTrue(balanced(c)[0], c[-80:])
        for c in chunks[1:]:
            self.assertTrue(c.startswith("<blockquote>"))

    def test_never_cuts_inside_entity_or_tag(self):
        text = ("&amp;<i>x</i>" * 800)
        for c in tg.split_html(text, limit=500):
            self.assertLessEqual(tg.u16(c), 500)
            self.assertTrue(balanced(c)[0])
            self.assertNotRegex(c, r"&[a-z]*$")


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeTelegram:
    def __init__(self, fail_methods=(), rate_limit_once=False):
        self.calls, self.fail_methods, self.rate_limit_once = [], set(fail_methods), rate_limit_once

    def __call__(self, req, timeout=None, context=None):
        method = req.full_url.rsplit("/", 1)[-1]
        payload = json.loads(req.data.decode("utf-8"))
        self.calls.append((method, payload))
        if self.rate_limit_once:
            self.rate_limit_once = False
            body = json.dumps({"ok": False, "error_code": 429, "parameters": {"retry_after": 7}}).encode()
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO(body))
        if method in self.fail_methods:
            body = json.dumps({"ok": False, "description": "Bad Request: wrong file"}).encode()
            raise urllib.error.HTTPError(req.full_url, 400, "Bad", {}, io.BytesIO(body))
        return FakeResp(json.dumps({"ok": True, "result": {}}).encode())


class TestSend(unittest.TestCase):
    CFG = {"token": "T", "chat_id": "-1001", "thread_id": 42}

    def test_token_selection_prefers_stream_token(self):
        base = {"TELEGRAM_CHAT_ID": "-100123", "TELEGRAM_TOPIC_PULSE": "7"}
        both = tg.config({**base, "TELEGRAM_BOT_TOKEN_PULSE": "pulse-tok", "TELEGRAM_BOT_TOKEN": "shared-tok"})
        self.assertEqual((both["token"], both["token_source"]), ("pulse-tok", "TELEGRAM_BOT_TOKEN_PULSE"))
        shared = tg.config({**base, "TELEGRAM_BOT_TOKEN_PULSE": "  ", "TELEGRAM_BOT_TOKEN": "shared-tok"})
        self.assertEqual((shared["token"], shared["token_source"]), ("shared-tok", "TELEGRAM_BOT_TOKEN"))
        self.assertEqual(shared["thread_id"], 7)
        self.assertIsNone(tg.config({**base}))
        self.assertIsNone(tg.config({"TELEGRAM_BOT_TOKEN_PULSE": "pulse-tok"}))  # 채팅 id 없음
        self.assertIsNone(tg.config({**base, "TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_TOPIC_PULSE": ""})["thread_id"])

    def test_unconfigured_skips_silently(self):
        fake = FakeTelegram()
        with clear_tg_env(), mock.patch.object(tg, "_urlopen", fake):
            r = tg.send_briefing(["hi"], {"url": "https://p", "caption": "c"})
        self.assertEqual((r["configured"], r["ok"], fake.calls), (False, False, []))

    def test_photo_first_then_messages_with_params(self):
        fake, sleeps = FakeTelegram(), []
        with mock.patch.object(tg, "_urlopen", fake):
            r = tg.send_briefing(["one", "two"], {"url": "https://quickchart.io/chart?c=1", "caption": "<b>t</b>"},
                                 cfg=self.CFG, sleep=sleeps.append)
        self.assertTrue(r["ok"])
        self.assertEqual([m for m, _ in fake.calls], ["sendPhoto", "sendMessage", "sendMessage"])
        photo, msg1 = fake.calls[0][1], fake.calls[1][1]
        self.assertEqual(photo["photo"], "https://quickchart.io/chart?c=1")
        self.assertEqual(msg1["message_thread_id"], 42)
        self.assertEqual(msg1["parse_mode"], "HTML")
        self.assertIs(msg1["disable_web_page_preview"], True)
        self.assertEqual(msg1["chat_id"], "-1001")
        self.assertEqual(sleeps, [1, 1])

    def test_photo_failure_does_not_block_text(self):
        fake = FakeTelegram(fail_methods={"sendPhoto"})
        with mock.patch.object(tg, "_urlopen", fake):
            r = tg.send_briefing(["body"], {"url": "https://bad", "caption": ""}, cfg=self.CFG, sleep=lambda s: None)
        self.assertTrue(r["ok"])
        self.assertIn("사진 실패", r["detail"])
        self.assertEqual(fake.calls[-1][0], "sendMessage")

    def test_429_waits_retry_after_then_retries_once(self):
        fake, sleeps = FakeTelegram(rate_limit_once=True), []
        with mock.patch.object(tg, "_urlopen", fake):
            ok, _ = tg.send_message(self.CFG, "hi", sleep=sleeps.append)
        self.assertTrue(ok)
        self.assertEqual(sleeps, [7.0])
        self.assertEqual(len(fake.calls), 2)

    def test_long_caption_trimmed(self):
        fake = FakeTelegram()
        with mock.patch.object(tg, "_urlopen", fake):
            tg.send_photo(self.CFG, "https://p", "<b>" + "가" * 2000 + "</b>", sleep=lambda s: None)
        self.assertLessEqual(tg.u16(fake.calls[0][1]["caption"]), tg.MAX_CAPTION)


class TestWording(unittest.TestCase):
    def test_glossary_first_occurrence_only(self):
        g = wording.Glossary()
        a = g.explain("RWA 이야기와 청산 소식, 스테이블코인")
        self.assertIn("RWA(부동산·채권 같은 실물자산을 코인으로 만든 것)", a)
        self.assertIn("청산(빌린 돈으로 투자했다가 손실이 커져 강제로 정리되는 것)", a)
        self.assertIn("스테이블코인(달러 등에 가치를 고정한 코인)", a)
        self.assertEqual(g.explain("RWA 또 청산 또 스테이블코인"), "RWA 또 청산 또 스테이블코인")

    def test_glossary_compound_label_and_eip_suffix(self):
        g = wording.Glossary()
        self.assertEqual(g.explain("청산·레버리지"),
                         "청산·레버리지(빌린 돈까지 끌어와 크게 투자했다가 손실이 커져 강제로 정리되는 것)")
        self.assertEqual(g.explain("레버리지"), "레버리지")  # 합성어로 이미 설명함
        self.assertEqual(g.explain("A note (EIP-8288) here"), "A note (EIP-8288(이더리움 개선 제안서)) here")

    def test_glossary_skips_urls_and_existing_parens(self):
        g = wording.Glossary()
        self.assertEqual(g.explain("see https://x.com/ETF/SEC"), "see https://x.com/ETF/SEC")
        self.assertEqual(g.explain("기준선(이미 설명)"), "기준선(이미 설명)")

    def test_polite_normalizer(self):
        self.assertEqual(wording.polite("상승세로 보여요. 매수세가 강해요, 조정 가능성이 있어요"),
                         "상승세로 보입니다. 매수세가 강합니다, 조정 가능성이 있습니다")
        self.assertEqual(wording.polite("관망세예요."), "관망세입니다.")
        self.assertIsNone(wording.HAEYO.search(wording.polite("급등했어요! 이건 호재예요")))


class TestChart(unittest.TestCase):
    def test_url_under_limit_with_korean(self):
        rows = [("비트코인(BTC)", 13, False), ("규제·SEC", 4, True)] + [(f"아주긴한국어라벨{i}" * 3, i, True) for i in range(20)]
        url = chart.bar_chart_url(rows, "가장 많이 나온 이야기")
        self.assertIsNotNone(url)
        self.assertLessEqual(len(url), 2000)
        self.assertTrue(url.startswith("https://quickchart.io/chart?w=800&h=400"))
        self.assertIn("%EB%B9%84%ED%8A%B8%EC%BD%94%EC%9D%B8", url)  # '비트코인' 인코딩


class TestBriefingOutput(TestPipeline):
    """실제 build_message 결과로 말투·변환·상태 기록 검증 (TestPipeline 의 가짜 소스 재사용)."""

    def setUp(self):
        super().setUp()
        mock.patch.dict(os.environ, {k: "" for k in TG_ENV_KEYS}).start()

    def msg(self, st=None, summary=None):
        st = st or {"seen": {}, "mentions": [], "trending_prev": ["BTC"]}
        if summary is not None:
            mock.patch("pulse.main.summarize", return_value=summary).start()
        return main.build_message(cfg(), self.results(), st, NOW)

    def test_no_haeyo_endings_in_slack_or_telegram(self):
        warm = [{"ts": str(i), "total": 100, "counts": {"BTC": 1}, "v": 2} for i in range(5)]
        for st in ({"seen": {}, "mentions": [], "trending_prev": ["BTC"]},
                   {"seen": {}, "mentions": warm, "trending_prev": ["BTC"]}):
            m = self.msg(st, summary=["비트코인 매수세가 강해 보여요", "ETF 자금 유입이 이어지고 있어요"])
            slack_txt = main.slack_text(m["blocks"])
            tg_txt = tg.strip_tags(tg.render(m["doc"]))
            for txt in (slack_txt, tg_txt, m["photo"]["caption"] if m["photo"] else ""):
                self.assertIsNone(wording.HAEYO.search(txt), wording.HAEYO.search(txt) and txt)
            self.assertIn("습니다", slack_txt + tg_txt)
            self.assertIn("정리했습니다", slack_txt)

    def test_glossary_applied_once_per_briefing(self):
        m = self.msg(summary=["스테이블코인 USDC 수요가 늘었습니다", "스테이블코인 발행량이 사상 최대입니다"])
        txt = main.slack_text(m["blocks"])
        self.assertEqual(txt.count("(달러 등에 가치를 고정한 코인)"), 1)

    def test_telegram_render_has_no_slack_markup(self):
        m = self.msg()
        chunks = main.telegram_messages(m)
        self.assertTrue(chunks)
        for c in chunks:
            self.assertLessEqual(tg.u16(c), tg.MAX_MESSAGE)
            self.assertTrue(balanced(c)[0])
            self.assertIsNone(re.search(r":[a-z_]+:", tg.strip_tags(c)))
            self.assertNotIn("<https://", c)
            self.assertNotRegex(c, r"(?m)^&gt; ")
        full = "\n".join(chunks)
        self.assertIn("<b>🌐 크립토마스 · 글로벌+국내 크립토 트렌드</b>", full)
        self.assertNotIn("세력", full)
        self.assertIn("<blockquote>", full)
        self.assertIn("&lt;security&gt;", full)
        self.assertIn('<a href="https://example.com/201">원문 보기</a>', full)

    def test_photo_is_chart_when_enough_topics(self):
        m = self.msg()
        self.assertEqual(m["photo"]["kind"], "chart")
        self.assertLessEqual(len(m["photo"]["url"]), 2000)
        self.assertLessEqual(tg.u16(m["photo"]["caption"]), tg.MAX_CAPTION)

    def _run(self, slack_ret, tg_ret):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            real_load, real_save = state.load, state.save
            with mock.patch("pulse.main.collect_all", return_value=self.results()), \
                    mock.patch("pulse.state.load", side_effect=lambda p=path: real_load(path)), \
                    mock.patch("pulse.state.save", side_effect=lambda st, **kw: real_save(st, path, **kw)), \
                    mock.patch("pulse.main.write_outbox"), mock.patch("pulse.main.load_dotenv"), \
                    mock.patch("pulse.slack.send", return_value=slack_ret), \
                    mock.patch("pulse.telegram.send_briefing", return_value=tg_ret) as sb:
                code = main.run(now=NOW)
            return code, real_load(path), sb

    def test_telegram_success_marks_seen_even_if_slack_fails(self):
        code, st, sb = self._run((False, "HTTPError"), {"configured": True, "ok": True, "detail": "본문 1/1"})
        self.assertEqual(code, 0)
        self.assertIn("x:201", st["seen"])
        messages, photo = sb.call_args[0]
        self.assertTrue(messages and photo["url"].startswith("https://quickchart.io/"))

    def test_both_fail_does_not_mark_seen(self):
        code, st, _ = self._run((False, "HTTPError"), {"configured": True, "ok": False, "detail": "HTTP 400"})
        self.assertEqual(code, 1)
        self.assertEqual(st["seen"], {})

    def test_telegram_only_configured(self):
        code, st, _ = self._run((False, "SLACK_WEBHOOK_URL 없음"), {"configured": True, "ok": True, "detail": "ok"})
        self.assertEqual(code, 0)
        self.assertIn("reddit:r0", st["seen"])

    def test_telegram_exception_does_not_block_slack(self):
        with mock.patch("pulse.slack.send", return_value=(True, "ok")), \
                mock.patch("pulse.telegram.send_briefing", side_effect=RuntimeError("boom")):
            res = main.deliver(self.msg())
        self.assertTrue(res["slack"]["ok"])
        self.assertFalse(res["telegram"]["ok"])
        self.assertIn("RuntimeError", res["telegram"]["detail"])

    def test_preview_writes_files_without_touching_state(self):
        with tempfile.TemporaryDirectory() as d:
            spath = os.path.join(d, "state.json")
            with open(spath, "w", encoding="utf-8") as f:
                json.dump({"seen": {}, "mentions": [], "trending_prev": ["BTC"]}, f)
            before = _sha(spath)
            real_load = state.load
            with mock.patch("pulse.main.collect_all", return_value=self.results()), \
                    mock.patch("pulse.state.load", side_effect=lambda p=spath: real_load(spath)), \
                    mock.patch("pulse.state.save") as save, mock.patch("pulse.main.load_dotenv"), \
                    mock.patch("pulse.slack.send") as ssend, mock.patch("pulse.telegram._post") as tpost:
                self.assertEqual(main.preview(now=NOW + timedelta(hours=1), out_dir=d), 0)
            save.assert_not_called()
            ssend.assert_not_called()
            tpost.assert_not_called()
            self.assertEqual(_sha(spath), before)
            with open(os.path.join(d, "preview_telegram.html"), encoding="utf-8") as f:
                page = f.read()
            self.assertIn("sendMessage", page)
            self.assertIn("quickchart.io", page)
            self.assertTrue(os.path.exists(os.path.join(d, "preview_slack.txt")))


# TestPipeline 을 상속했으니 부모 테스트가 여기서 두 번 돌지 않게 막는다
for _name in ("test_build_message", "test_run_marks_seen_only_when_sent"):
    setattr(TestBriefingOutput, _name, None)
del TestPipeline

if __name__ == "__main__":
    unittest.main()
