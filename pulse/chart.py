"""quickchart.io 막대그래프 URL (키 불필요). 텔레그램 sendPhoto 에 URL 로 넘긴다.
한글 라벨 렌더링 확인함(2026-09-15)."""
import json
from urllib.parse import quote

BASE = "https://quickchart.io/chart"
COIN_COLOR, TOPIC_COLOR = "#f7931a", "#7c5cff"
MAX_URL = 2000


def bar_chart_url(rows, title, max_url=MAX_URL):
    """rows: [(라벨, 값, 주제여부)] → URL. 길면 뒤에서부터 항목을 줄인다. 만들 수 없으면 None."""
    rows = list(rows)
    while rows:
        c = {
            "type": "horizontalBar",
            "data": {
                "labels": [r[0] for r in rows],
                "datasets": [{"data": [r[1] for r in rows],
                              "backgroundColor": [TOPIC_COLOR if r[2] else COIN_COLOR for r in rows]}],
            },
            "options": {
                "legend": {"display": False},
                "title": {"display": True, "text": title, "fontSize": 18},
                "scales": {"xAxes": [{"ticks": {"beginAtZero": True, "precision": 0}}],
                           "yAxes": [{"ticks": {"fontSize": 14}}]},
                "plugins": {"datalabels": {"anchor": "end", "align": "right", "color": "#333",
                                           "font": {"weight": "bold"}}},
                "layout": {"padding": {"right": 30}},
            },
        }
        url = f"{BASE}?w=800&h=400&bkg=white&c=" + quote(json.dumps(c, ensure_ascii=False, separators=(",", ":")))
        if len(url) <= max_url:
            return url
        rows = rows[:-1]
    return None
