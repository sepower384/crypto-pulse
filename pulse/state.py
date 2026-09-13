"""data/state.json — 이미 보낸 글, 언급량 히스토리, 직전 트렌딩.
GitHub Actions 에서는 실행 후 이 파일을 커밋해서 다음 실행으로 넘긴다."""
import json
import os
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "state.json")
SEEN_DAYS = 7


def load(path=PATH):
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    st.setdefault("seen", {})
    st.setdefault("mentions", [])
    st.setdefault("trending_prev", [])
    return st


def save(st, path=PATH, now=None, history_runs=36):
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=SEEN_DAYS)).isoformat()
    st["seen"] = {k: v for k, v in st["seen"].items() if v >= cutoff}
    st["mentions"] = st["mentions"][-(history_runs * 2):]
    st["last_run"] = now.isoformat()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def mark_seen(st, posts, now=None):
    ts = (now or datetime.now(timezone.utc)).isoformat()
    for p in posts:
        st["seen"][p.key] = ts
