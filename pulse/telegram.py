"""텔레그램 봇 전송 (표준 라이브러리만).

※ pulse/sources/telegram.py 는 '수집'(공개채널 읽기), 이 파일은 '전송'이다.

환경변수
  TELEGRAM_BOT_TOKEN_PULSE  이 스트림 전용 봇 토큰 (우선)
  TELEGRAM_BOT_TOKEN        공용 봇 토큰 (전용 토큰 없을 때)
  TELEGRAM_CHAT_ID          슈퍼그룹 id (-100…)
  TELEGRAM_TOPIC_PULSE      토픽(스레드) id — 🌐 크립토마스
토큰·채팅 id 가 없으면 조용히 건너뛴다.

슬랙 mrkdwn 으로 만든 브리핑 문서를 텔레그램 HTML 로 바꾸고(slack_to_html),
4096자 제한에 맞춰 섹션·빈 줄 경계에서 나눈다(split_html).
"""
import html
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE = 4096
MAX_CAPTION = 1024
_urlopen = urllib.request.urlopen  # 테스트에서 바꿔 끼운다

# 슬랙 이모지 코드 → 유니코드. 텔레그램은 :fire: 를 글자 그대로 보여 준다.
EMOJI = {
    "globe_with_meridians": "🌐", "fire": "🔥", "satellite_antenna": "📡", "satellite": "📡",
    "space_invader": "👾", "butterfly": "🦋", "heart": "❤️", "eye": "👁", "eyes": "👀",
    "white_check_mark": "✅", "double_vertical_bar": "⏸️", "pause_button": "⏸️",
    "compass": "🧭", "new": "🆕", "chart_with_upwards_trend": "📈", "chart_with_downwards_trend": "📉",
    "1234": "🔢", "bar_chart": "📊", "speaking_head_in_silhouette": "🗣️", "speaking_head": "🗣️",
    "purple_square": "🟪", "large_purple_square": "🟪", "memo": "📝", "pencil": "📝",
    "warning": "⚠️", "rotating_light": "🚨", "x": "❌", "no_entry": "⛔", "arrow_right": "➡️",
    "arrow_up": "⬆️", "arrow_down": "⬇️", "leftwards_arrow_with_hook": "↩️", "arrow_right_hook": "↪️",
    "rocket": "🚀", "moneybag": "💰", "whale": "🐳", "whale2": "🐋", "newspaper": "📰", "mag": "🔍",
    "bell": "🔔", "zap": "⚡", "star": "⭐", "sparkles": "✨", "bulb": "💡", "pushpin": "📌",
    "link": "🔗", "clock3": "🕒", "hourglass_flowing_sand": "⏳", "large_green_circle": "🟢",
    "red_circle": "🔴", "large_yellow_circle": "🟡", "large_orange_circle": "🟠", "white_circle": "⚪",
    "small_red_triangle": "🔺", "small_red_triangle_down": "🔻", "thumbsup": "👍", "+1": "👍",
    "thumbsdown": "👎", "-1": "👎", "dollar": "💵", "coin": "🪙", "bank": "🏦", "robot_face": "🤖",
    "busts_in_silhouette": "👥", "loudspeaker": "📢", "mega": "📣", "information_source": "ℹ️",
    "calendar": "📆", "date": "📅", "hammer_and_wrench": "🛠️", "lock": "🔒", "skull": "💀",
    "chart": "💹", "trophy": "🏆", "crown": "👑", "gem": "💎", "boom": "💥", "ok": "🆗",
}
_EMOJI_CODE = re.compile(r":([a-z0-9_+\-]+):")
_LINK = re.compile(r"<((?:https?|tg|mailto):[^|>\s]+)(?:\|([^>]*))?>")
_SLACK_SPECIAL = re.compile(r"<[!#@][^>]*>")
_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"(?<![A-Za-z0-9*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![A-Za-z0-9*])")
_ITALIC = re.compile(r"(?<![A-Za-z0-9_])_(?=\S)([^_\n]+?)(?<=\S)_(?![A-Za-z0-9_])")
_STRIKE = re.compile(r"(?<![A-Za-z0-9~])~(?=\S)([^~\n]+?)(?<=\S)~(?![A-Za-z0-9~])")
_QUOTE = re.compile(r"^(?:>|&gt;) ?(.*)$")


# ---------------------------------------------------------------- 설정
def config(env=None):
    """전용 토큰(TELEGRAM_BOT_TOKEN_PULSE) → 공용 토큰(TELEGRAM_BOT_TOKEN) 순. 없으면 None."""
    env = os.environ if env is None else env
    token = (env.get("TELEGRAM_BOT_TOKEN_PULSE") or "").strip() or (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return None
    topic = (env.get("TELEGRAM_TOPIC_PULSE") or "").strip()
    return {"token": token, "chat_id": chat,
            "thread_id": int(topic) if re.fullmatch(r"-?\d+", topic) else None,
            "token_source": "TELEGRAM_BOT_TOKEN_PULSE" if (env.get("TELEGRAM_BOT_TOKEN_PULSE") or "").strip()
            else "TELEGRAM_BOT_TOKEN"}


# ---------------------------------------------------------------- 변환
def esc(s):
    return html.escape(s or "", quote=False)


def emojize(text):
    return _EMOJI_CODE.sub(lambda m: EMOJI.get(m.group(1), m.group(0)), text or "")


def _slack_unescape(s):
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def inline(text):
    """슬랙 mrkdwn 한 줄 → 텔레그램 HTML (인용 처리 제외)."""
    text = emojize(text)
    slots = []

    def keep(html_piece):
        slots.append(html_piece)
        return f"\x00{len(slots) - 1}\x00"

    def link(m):
        url, label = m.group(1), m.group(2)
        label = esc(_slack_unescape(label if label is not None else url))
        return keep(f'<a href="{html.escape(_slack_unescape(url), quote=True)}">{label}</a>')

    text = _LINK.sub(link, text)
    text = _SLACK_SPECIAL.sub("", text)
    text = esc(_slack_unescape(text))
    text = _CODE.sub(lambda m: keep(f"<code>{m.group(1)}</code>"), text)
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: slots[int(m.group(1))], text)


def slack_to_html(text):
    """슬랙 mrkdwn(여러 줄) → 텔레그램 HTML. 줄머리 `>`/`&gt;` 연속 줄은 <blockquote> 하나로 묶는다."""
    out, quote = [], []

    def flush():
        if quote:
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote.clear()

    for line in (text or "").split("\n"):
        m = _QUOTE.match(line)
        if m:
            quote.append(inline(m.group(1)))
        else:
            flush()
            out.append(inline(line))
    flush()
    return "\n".join(out)


def render(doc):
    """브리핑 문서(main.build_message 의 doc) → 텔레그램 HTML 한 덩어리."""
    parts = []
    for d in doc:
        kind = d["kind"]
        if kind == "header":
            head = f"<b>{esc(d.get('tg_title') or d['text'])}</b>"
            if d.get("tg_sub"):
                head += f"\n<i>{esc(d['tg_sub'])}</i>"
            parts.append(head)
        elif kind == "text":
            parts.append(slack_to_html(d["text"]))
        elif kind == "section":
            body = "\n".join(slack_to_html(ln) for ln in d["lines"])
            parts.append(f"<b>{inline(d['title'])}</b>\n{body}")
        elif kind == "context":
            parts.append(f"<i>{inline(d['text'])}</i>")
        # divider: 텔레그램에선 빈 줄로 충분
    text = "\n\n".join(p.strip("\n") for p in parts if p.strip())
    return re.sub(r"\n{3,}", "\n\n", text)


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or ""))


# ---------------------------------------------------------------- 분할
_TAG_TOKEN = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)[^>]*>")
_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);")


def u16(s):
    """텔레그램 글자수는 UTF-16 단위로 센다 (이모지 = 2)."""
    return len(s.encode("utf-16-le")) // 2


def _closing(stack):
    return "".join(f"</{name}>" for name, _ in reversed(stack))


def _cut_point(text, limit):
    """limit 안에서 자를 위치와 그 지점에서 열려 있는 태그 스택."""
    stack, units, i, n = [], 0, 0, len(text)
    blank = nl = nl_any = anywhere = None
    while i < n:
        ch = text[i]
        if ch == "<":
            m = _TAG_TOKEN.match(text, i)
            tok = m.group(0) if m else ch
        elif ch == "&":
            m = _ENTITY.match(text, i)
            tok = m.group(0) if m else ch
        else:
            m, tok = None, ch
        if i > 0:
            snap = list(stack)
            fits = units + u16(_closing(snap)) <= limit
            if ch == "\n":
                if not stack:
                    if text[i - 1] == "\n":
                        blank = (i, snap)
                    else:
                        nl = (i, snap)
                elif fits:
                    nl_any = (i, snap)
            if fits and ch != "\n":
                anywhere = (i, snap)
        w = u16(tok)
        if units + w > limit:
            break
        if ch == "<" and m:
            name = m.group(2).lower()
            if m.group(1):
                for k in range(len(stack) - 1, -1, -1):
                    if stack[k][0] == name:
                        del stack[k:]
                        break
            elif not tok.endswith("/>"):
                stack.append((name, tok))
        units += w
        i += len(tok)
    if blank and nl and blank[0] < nl[0] * 0.4:
        blank = None  # 너무 앞쪽 빈 줄에서 자르면 메시지가 쪼그라든다
    for cand in (blank, nl, nl_any, anywhere):
        if cand and cand[0] > 0:
            return cand
    return max(i, 1), list(stack)


def split_html(text, limit=MAX_MESSAGE):
    """텔레그램 HTML → limit(UTF-16) 이하 메시지 목록. 빈 줄 > 줄바꿈 > (태그 닫고 다시 여는) 강제 분할 순으로 자른다."""
    text = (text or "").strip("\n")
    out = []
    while text:
        if u16(text) <= limit:
            out.append(text)
            break
        pos, stack = _cut_point(text, limit)
        head = text[:pos].rstrip("\n") + _closing(stack)
        text = "".join(tok for _, tok in stack) + text[pos:].lstrip("\n")
        if head.strip():
            out.append(head)
    return out


# ---------------------------------------------------------------- 전송
def _post(token, method, payload, timeout=20, sleep=time.sleep):
    """(ok, 설명). 429 는 retry_after 만큼 쉬고 1회만 재시도."""
    url = API.format(token=token, method=method)
    body = json.dumps(payload).encode("utf-8")
    for attempt in (0, 1):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with _urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
                data = json.loads(r.read().decode("utf-8"))
            return bool(data.get("ok")), data.get("description", "ok")
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                data = {}
            if e.code == 429 and attempt == 0:
                wait = (data.get("parameters") or {}).get("retry_after", 5)
                sleep(float(wait))
                continue
            return False, f"HTTP {e.code} {str(data.get('description', ''))[:150]}"
        except Exception as e:  # noqa: BLE001  (토큰이 든 URL 은 메시지에 싣지 않는다)
            return False, f"{type(e).__name__}: {str(e)[:100]}"
    return False, "429 재시도 실패"


def _base(cfg):
    p = {"chat_id": cfg["chat_id"]}
    if cfg.get("thread_id") is not None:
        p["message_thread_id"] = cfg["thread_id"]
    return p


def send_photo(cfg, photo_url, caption="", sleep=time.sleep):
    if caption and u16(caption) > MAX_CAPTION:
        caption = esc(strip_tags(caption)[:MAX_CAPTION - 10])
    payload = {**_base(cfg), "photo": photo_url, "caption": caption, "parse_mode": "HTML"}
    return _post(cfg["token"], "sendPhoto", payload, timeout=30, sleep=sleep)


def send_message(cfg, text, sleep=time.sleep):
    payload = {**_base(cfg), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    ok, detail = _post(cfg["token"], "sendMessage", payload, sleep=sleep)
    if not ok and "parse" in detail.lower():
        # HTML 파싱 거절 시 서식 없이라도 보낸다
        plain = {**_base(cfg), "text": strip_tags(text)[:MAX_MESSAGE], "disable_web_page_preview": True}
        ok, detail2 = _post(cfg["token"], "sendMessage", plain, sleep=sleep)
        detail = f"{detail} → 서식 없이 재전송 {'성공' if ok else '실패: ' + detail2}"
    return ok, detail


def send_briefing(messages, photo=None, cfg=None, sleep=time.sleep):
    """사진(선택) 1장 → 본문 메시지들. 반환 {configured, ok, sent, total, detail}.
    ok = 본문이 1개라도 나갔는가 (부분 전송도 '보냄'으로 쳐서 다음 회차 중복을 막는다)."""
    cfg = cfg if cfg is not None else config()
    if not cfg:
        return {"configured": False, "ok": False, "sent": 0, "total": len(messages),
                "detail": "텔레그램 설정 없음 — 건너뜀"}
    notes = []
    if photo and photo.get("url"):
        ok, d = send_photo(cfg, photo["url"], photo.get("caption", ""), sleep=sleep)
        notes.append("사진 ok" if ok else f"사진 실패(무시): {d}")
        if messages:
            sleep(1)
    sent = 0
    for i, m in enumerate(messages):
        if i:
            sleep(1)
        ok, d = send_message(cfg, m, sleep=sleep)
        if ok:
            sent += 1
        else:
            notes.append(f"본문 {i + 1}/{len(messages)} 실패: {d}")
    return {"configured": True, "ok": sent > 0, "sent": sent, "total": len(messages),
            "detail": f"본문 {sent}/{len(messages)}" + (" · " + " · ".join(notes) if notes else "")}
