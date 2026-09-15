"""한국어화.
- 번역: 구글 gtx 공개 엔드포인트(키 불필요). 실패하면 원문 그대로.
- 요약: GEMINI_API_KEY 있을 때만 '지금 흐름' 3~4줄. 없으면 섹션 생략.
"""
import json
import os
import re
from urllib.parse import quote

from .net import fetch, fetch_json

GTX = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ko&dt=t&q="
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
_HANGUL = re.compile(r"[가-힣]")


def to_korean(text, max_chars=600):
    text = (text or "").strip()
    if not text or len(_HANGUL.findall(text)) > len(text) * 0.3:
        return text
    clipped = text[:max_chars]
    try:
        data = fetch_json(GTX + quote(clipped), retries=1, backoff=2, timeout=15)
        out = "".join(seg[0] for seg in data[0] if seg and seg[0])
        return out.strip() or text
    except Exception:  # noqa: BLE001
        return text


def summarize(lines):
    """lines: 수집 글 요약 문자열 리스트 → 한국어 bullet 리스트 (키 없으면 [])."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or not lines:
        return []
    prompt = (
        "너는 크립토 시장 애널리스트다. 아래는 최근 몇 시간 동안 글로벌 X(트위터) 유명인사 글, 레딧 인기글, "
        "언급 급상승 키워드다. 한국 투자자에게 '지금 크립토 여론이 어디로 움직이는지' 3~4개 bullet로 요약해라. "
        "각 bullet은 친절한 비서가 브리핑하듯 합니다체 한 문장('~입니다', '~합니다')으로 쓰고, "
        "구체적 코인/인물/사건 이름을 넣고, 추측은 '~으로 보입니다'로 표시. 해요체('~해요', '~예요') 금지. "
        "투자 권유 금지. JSON 배열(문자열)로만 답해라.\n\n" + "\n".join(lines[:60])
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json",
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={key}"
    try:
        res = json.loads(fetch(url, data=body, timeout=60, retries=1).decode("utf-8"))
        txt = res["candidates"][0]["content"]["parts"][0]["text"]
        items = json.loads(txt)
        return [str(x).strip() for x in items if str(x).strip()][:4]
    except Exception:  # noqa: BLE001
        return []
