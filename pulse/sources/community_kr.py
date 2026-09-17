"""국내 커뮤니티: 디시 비트코인 갤러리(최신 글 목록) + 코인판 추천게시물.

실측(2026-09-17, 이 PC)
- 디시 list_num=100 한 페이지 ≈ 1.5시간치. 개념글(recommend) 목록은 몇 주에 한 개꼴이라 안 쓴다.
- 코인판 자유게시판 목록은 JS 로 그려서 비어 있다 → 추천게시물(best)만. 날짜만 있고 시각은 없다.
"""
import html
import re
from datetime import datetime, timedelta, timezone

from ..model import Post, SourceResult
from ..net import fetch_text

KST = timezone(timedelta(hours=9))
DC_URL = "https://gall.dcinside.com/board/lists?id={gid}&list_num={n}&page={page}"
DC_VIEW = "https://gall.dcinside.com/board/view/?id={gid}&no={no}"
COINPAN_URL = "https://www.coinpan.com/index.php?mid=best"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _clean(s):
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", s or ""))).strip()


def _int(s):
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else 0


def parse_dc(page, gid, name):
    posts = []
    for row in page.split('<tr class="ub-content')[1:]:
        m_no = re.search(r'<td class="gall_num">\s*(\d+)\s*</td>', row)
        if not m_no or "icon_notice" in row:     # 공지·설문·광고 줄
            continue
        m_tit = re.search(r'<td class="gall_tit[^"]*">.*?<a[^>]*href="/board/view/[^"]*"[^>]*>(.*?)</a>', row, re.S)
        m_date = re.search(r'class="gall_date" title="([^"]+)"', row)
        if not m_tit or not m_date:
            continue
        title = _clean(m_tit.group(1))
        if not title:
            continue
        try:
            created = datetime.strptime(m_date.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).astimezone(timezone.utc)
        except ValueError:
            continue
        m_nick = re.search(r'data-nick="([^"]*)"', row)
        m_views = re.search(r'class="gall_count">([^<]*)<', row)
        m_rec = re.search(r'class="gall_recommend">([^<]*)<', row)
        m_rep = re.search(r'class="reply_num">\[(\d+)', row)
        no = m_no.group(1)
        posts.append(Post(
            source="dcinside", id=f"{gid}/{no}", author=html.unescape(m_nick.group(1)) if m_nick else "",
            handle=name, text=title, url=DC_VIEW.format(gid=gid, no=no), created=created,
            likes=_int(m_rec.group(1)) if m_rec else 0,
            extra={"title": title, "views": _int(m_views.group(1)) if m_views else 0,
                   "comments": _int(m_rep.group(1)) if m_rep else 0},
        ))
    return posts


def parse_coinpan(page, name="코인판"):
    posts = []
    for row in re.split(r"<tr[\s>]", page)[1:]:
        m_link = re.search(r'<td class="title">\s*<a href="/best/(\d+)">(.*?)</a>', row, re.S)
        m_num = re.search(r'<td class="no">\s*<span class="number">', row)
        m_date = re.search(r'<td class="time">\s*<span class="number">\s*(\d{4})\.(\d{2})\.(\d{2})', row)
        if not m_link or not m_num or not m_date:   # 고정 공지(번호 없음)
            continue
        title = _clean(m_link.group(2))
        if not title:
            continue
        y, mo, d = (int(g) for g in m_date.groups())
        # 날짜만 있다 → 그날 정오(KST)로 둔다. 나이 계산이 ±12시간 어긋날 수 있어 창을 넉넉히 잡는다.
        created = datetime(y, mo, d, 12, tzinfo=KST).astimezone(timezone.utc)
        m_board = re.search(r'<span class="mid">.*?<strong>([^<]+)</strong>', row, re.S)
        m_views = re.search(r'<td class="readed">\s*<span class="number">([^<]*)<', row)
        m_vote = re.search(r'<td class="voted">\s*<span class="number">\s*(\d+)', row)
        m_rep = re.search(r'#comment"[^>]*>\s*<span class="number">(\d+)', row)
        m_author = re.search(r'<td class="author">.*?/>\s*([^<]+?)\s*</a>', row, re.S)
        pid = m_link.group(1)
        posts.append(Post(
            source="coinpan", id=pid, author=_clean(m_author.group(1)) if m_author else "",
            handle=f"{name} {_clean(m_board.group(1))}" if m_board else name, text=title,
            url=f"https://www.coinpan.com/best/{pid}", created=created,
            likes=int(m_vote.group(1)) if m_vote else 0,
            extra={"title": title, "views": _int(m_views.group(1)) if m_views else 0,
                   "comments": int(m_rep.group(1)) if m_rep else 0, "date_only": True},
        ))
    return posts


def collect(cfg):
    kcfg = cfg.get("kr_community", {})
    posts, notes, fails = [], [], []
    dc = kcfg.get("dcinside")
    if dc:
        got = []
        try:
            for page in range(1, dc.get("pages", 2) + 1):
                got += parse_dc(fetch_text(DC_URL.format(gid=dc["gallery"], n=dc.get("list_num", 100), page=page),
                                           retries=1), dc["gallery"], dc.get("name", "디시"))
        except Exception as e:  # noqa: BLE001
            fails.append(f"디시({type(e).__name__})")
        uniq = {p.id: p for p in got}
        posts += uniq.values()
        notes.append(f"디시 {len(uniq)}")
    if kcfg.get("coinpan", True):
        try:
            got = parse_coinpan(fetch_text(COINPAN_URL, retries=1))
            posts += got
            notes.append(f"코인판 {len(got)}")
        except Exception as e:  # noqa: BLE001
            fails.append(f"코인판({type(e).__name__})")
    ok = bool(posts)
    return SourceResult("kr_community", ok, posts, " · ".join(notes) + (f", 실패 {', '.join(fails)}" if fails else ""))
