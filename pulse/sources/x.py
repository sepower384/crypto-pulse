"""X(트위터): Playwright 로 프로필을 열고, 페이지가 부르는 UserTweets GraphQL 응답을 가로챈다.

실측(2026-09-13, 이 PC): 비로그인이면 headless/headful·chromium/chrome 전부 스켈레톤 <article> 만 뜨고
UserTweets 호출 자체가 안 나간다 → **로그인 쿠키(X_COOKIES) 필수**. 쿠키 없으면 이 소스는 건너뛴다.
DOM 셀렉터는 자주 바뀌니 1순위는 GraphQL JSON, 2순위가 DOM 파싱.
"""
import json
import os
import random
import re
import time
from datetime import datetime

from ..feeds import parse_date
from ..model import Post, SourceResult

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


# ---------- 쿠키 ----------
def parse_cookies(raw):
    """X_COOKIES 3가지 형식 허용 → Playwright add_cookies 용 리스트.
    1) "auth_token=...; ct0=..."   (브라우저 개발자도구에서 복사)
    2) {"auth_token": "...", "ct0": "..."}
    3) [{"name":..., "value":..., "domain":...}, ...]  (storage_state 의 cookies)
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    pairs = {}
    if raw[0] in "[{":
        data = json.loads(raw)
        if isinstance(data, dict) and "cookies" in data:
            data = data["cookies"]
        if isinstance(data, list):
            pairs = {c["name"]: c["value"] for c in data if "x.com" in c.get("domain", ".x.com")}
        else:
            pairs = {k: str(v) for k, v in data.items()}
    else:
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                pairs[k.strip()] = v.strip().strip('"')
    return [
        {"name": k, "value": v, "domain": ".x.com", "path": "/", "secure": True,
         "httpOnly": k == "auth_token", "sameSite": "None" if k != "auth_token" else "Lax"}
        for k, v in pairs.items() if v
    ]


# ---------- GraphQL 파싱 ----------
def _unwrap(res):
    if not isinstance(res, dict):
        return None
    if res.get("__typename") == "TweetWithVisibilityResults":
        res = res.get("tweet", {})
    return res if isinstance(res, dict) and "legacy" in res else None


def _screen_name(res):
    u = (((res.get("core") or {}).get("user_results") or {}).get("result") or {})
    return ((u.get("core") or {}).get("screen_name")
            or (u.get("legacy") or {}).get("screen_name") or "")


def _walk(node, out):
    if isinstance(node, dict):
        tr = node.get("tweet_results")
        if isinstance(tr, dict):
            t = _unwrap(tr.get("result"))
            if t:
                out.append(t)
        for v in node.values():
            if isinstance(v, (dict, list)):
                _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)


def parse_graphql(data, handle, name):
    """UserTweets 응답 → 해당 계정 본인 글(리트윗·답글 제외). 인용트윗은 포함."""
    found = []
    _walk(data, found)
    posts, seen = [], set()
    for t in found:
        lg = t["legacy"]
        tid = t.get("rest_id") or lg.get("id_str")
        sn = _screen_name(t)
        if not tid or tid in seen or sn.lower() != handle.lower():
            continue
        if lg.get("retweeted_status_result") or lg.get("full_text", "").startswith("RT @"):
            continue
        if lg.get("in_reply_to_status_id_str") and lg.get("in_reply_to_screen_name", "").lower() != handle.lower():
            continue
        seen.add(tid)
        text = ((((t.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}).get("text")
                or lg.get("full_text", ""))
        views = (t.get("views") or {}).get("count")
        quoted = _unwrap(((t.get("quoted_status_result") or {}).get("result")))
        extra = {"views": int(views) if views and str(views).isdigit() else 0}
        if quoted:
            extra["quote"] = {"handle": _screen_name(quoted), "text": quoted["legacy"].get("full_text", "")[:280]}
        posts.append(Post(
            source="x", id=tid, author=name, handle=sn, text=text,
            url=f"https://x.com/{sn}/status/{tid}", created=parse_date_x(lg.get("created_at")),
            likes=lg.get("favorite_count", 0), reposts=lg.get("retweet_count", 0), extra=extra,
        ))
    return posts


def parse_date_x(s):
    # "Wed Oct 10 20:19:24 +0000 2018" — RFC2822 와 달라서 직접 처리
    if not s:
        return None
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return parse_date(s)


# ---------- DOM 폴백 ----------
DOM_JS = """els => els.map(a => {
  const t = a.querySelector('time');
  const link = t && t.closest('a') ? t.closest('a').href : '';
  const txt = a.querySelector('[data-testid="tweetText"]');
  const grp = a.querySelector('[role="group"]');
  const sc = a.querySelector('[data-testid="socialContext"]');
  return {datetime: t ? t.getAttribute('datetime') : null, link,
          text: txt ? txt.innerText : '', stats: grp ? grp.getAttribute('aria-label') : '',
          social: sc ? sc.innerText : ''};
})"""


def _num_before(label, words):
    for w in words:
        m = re.search(r"([\d,.]+)\s*(?:" + w + ")", label or "", re.I)
        if m:
            return int(float(m.group(1).replace(",", "")))
    return 0


def parse_dom(rows, handle, name):
    posts = []
    for r in rows:
        link = r.get("link") or ""
        if f"/{handle.lower()}/status/" not in link.lower() or not r.get("text"):
            continue
        if r.get("social"):  # "~님이 재게시함" / pinned 등
            continue
        tid = link.rstrip("/").split("/")[-1]
        posts.append(Post(
            source="x", id=tid, author=name, handle=handle, text=r["text"], url=link,
            created=parse_date(r.get("datetime")),
            likes=_num_before(r.get("stats"), ["likes", "마음에 들어요", "좋아요"]),
            reposts=_num_before(r.get("stats"), ["reposts", "재게시"]),
        ))
    return posts


# ---------- 수집 ----------
def collect(cfg):
    cookies = parse_cookies(os.environ.get("X_COOKIES", ""))
    if not any(c["name"] == "auth_token" for c in cookies):
        return SourceResult("x", False, [], "건너뜀: X_COOKIES(auth_token) 없음")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return SourceResult("x", False, [], "건너뜀: playwright 미설치")

    xcfg = cfg["x"]
    headless = os.environ.get("PULSE_HEADLESS", "1") != "0"
    posts, fails, login_lost = [], [], False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1280, "height": 1800})
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        captured = []

        def on_response(resp):
            if "/UserTweets" in resp.url and resp.status == 200:
                try:
                    captured.append(resp.json())
                except Exception:  # noqa: BLE001
                    pass

        page.on("response", on_response)
        for h in xcfg["handles"]:
            handle, name = h["handle"], h.get("name", h["handle"])
            captured.clear()
            try:
                page.goto(f"https://x.com/{handle}", timeout=45000, wait_until="domcontentloaded")
                deadline = time.time() + 15
                while not captured and time.time() < deadline:
                    page.wait_for_timeout(500)
                if "/i/flow/login" in page.url or "/login" in page.url:
                    login_lost = True
                    break
                got = []
                for data in captured:
                    got += parse_graphql(data, handle, name)
                if not got:  # GraphQL 못 잡으면 DOM
                    try:
                        page.wait_for_selector('[data-testid="tweetText"]', timeout=8000)
                    except Exception:  # noqa: BLE001
                        pass
                    got = parse_dom(page.locator("article").evaluate_all(DOM_JS), handle, name)
                if not got:
                    fails.append(handle)
                for post in got:
                    post.extra["require_crypto"] = bool(h.get("require_crypto"))
                posts += got
            except Exception as e:  # noqa: BLE001
                fails.append(f"{handle}({type(e).__name__})")
            page.wait_for_timeout(random.randint(1500, 3500))  # 사람처럼 간격
        browser.close()

    if login_lost:
        return SourceResult("x", False, posts, "⚠️ 로그인 풀림 — X_COOKIES 갱신 필요")
    total = len(xcfg["handles"])
    ok = len(fails) < total
    note = f"{total - len(fails)}/{total}계정 {len(posts)}건" + (f", 실패 {', '.join(fails[:5])}" if fails else "")
    return SourceResult("x", ok, posts, note)
