"""소스 공통 데이터 구조."""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


@dataclass
class Post:
    source: str            # x | reddit | bluesky | blog
    id: str                # 소스 내 고유 id (dedupe 키는 source:id)
    author: str            # 표시 이름
    handle: str            # @핸들 / r/서브 / 블로그명
    text: str
    url: str
    created: datetime | None = None
    likes: int = 0
    reposts: int = 0
    rank: int = 0          # 레딧 top 순위 등 (0 = 없음)
    extra: dict = field(default_factory=dict)

    @property
    def key(self):
        return f"{self.source}:{self.id}"

    def age_hours(self, now=None):
        if not self.created:
            return None
        now = now or datetime.now(timezone.utc)
        return (now - self.created).total_seconds() / 3600

    def to_dict(self):
        d = asdict(self)
        d["created"] = self.created.isoformat() if self.created else None
        return d


@dataclass
class SourceResult:
    name: str
    ok: bool
    posts: list = field(default_factory=list)
    note: str = ""          # 슬랙 하단 상태줄에 표시
    extra: dict = field(default_factory=dict)
