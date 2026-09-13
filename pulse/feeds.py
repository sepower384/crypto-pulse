"""RSS 2.0 / Atom 파서 (xml.etree, 의존성 0)."""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ATOM = "{http://www.w3.org/2005/Atom}"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(s):
    if not s:
        return ""
    s = _TAG.sub(" ", s)
    return _WS.sub(" ", html.unescape(s)).strip()


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def parse_feed(xml_text):
    """[{title, link, published, summary, category, author, id}] 반환."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    items = []
    if root.tag == f"{ATOM}feed":
        for e in root.findall(f"{ATOM}entry"):
            link = ""
            for l in e.findall(f"{ATOM}link"):
                if l.get("rel") in (None, "alternate"):
                    link = l.get("href", "")
                    break
            cat = e.find(f"{ATOM}category")
            author = e.find(f"{ATOM}author/{ATOM}name")
            items.append({
                "id": (e.findtext(f"{ATOM}id") or link).strip(),
                "title": strip_html(e.findtext(f"{ATOM}title")),
                "link": link,
                "published": parse_date(e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated")),
                "summary": strip_html(e.findtext(f"{ATOM}content") or e.findtext(f"{ATOM}summary")),
                "category": cat.get("term", "") if cat is not None else "",
                "author": (author.text or "").strip() if author is not None else "",
            })
    else:
        for it in root.iter("item"):
            link = (it.findtext("link") or "").strip()
            items.append({
                "id": (it.findtext("guid") or link).strip(),
                "title": strip_html(it.findtext("title")),
                "link": link,
                "published": parse_date(it.findtext("pubDate")),
                "summary": strip_html(it.findtext("description")),
                "category": (it.findtext("category") or "").strip(),
                "author": (it.findtext("{http://purl.org/dc/elements/1.1/}creator") or "").strip(),
            })
    return items
