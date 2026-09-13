"""표준 라이브러리만 쓰는 HTTP 헬퍼 (의존성 0)."""
import json
import ssl
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def _ctx():
    # 윈도우 사내망/백신 SSL 가로채기 대비: 시스템 기본 컨텍스트 사용
    return ssl.create_default_context()


def fetch(url, *, headers=None, data=None, timeout=25, retries=2, backoff=4.0):
    """bytes 반환. 429/5xx 는 backoff 후 재시도, 최종 실패 시 예외."""
    hdrs = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt == retries:
                raise
        time.sleep(backoff * (attempt + 1))
    raise last  # pragma: no cover


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw).decode("utf-8"))


def fetch_text(url, **kw):
    return fetch(url, **kw).decode("utf-8", errors="replace")
