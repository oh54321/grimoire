from dataclasses import dataclass, field

_DESC_WIDTH = 80


def _trunc(text: str) -> str:
    s = " ".join(text.split())
    return s if len(s) <= _DESC_WIDTH else s[: _DESC_WIDTH - 3] + "..."


@dataclass(frozen=True)
class SearchHit:
    node_id: str
    name: str
    kind: str
    description: str
    score: float


@dataclass(frozen=True)
class TagHit:
    tag: str
    score: float


@dataclass(frozen=True)
class SearchPage:
    hits: list
    page: int
    num_pages: int
    total: int
    page_size: int
    query: str

    def render(self) -> str:
        start = self.page * self.page_size + 1 if self.hits else 0
        end = start + len(self.hits) - 1 if self.hits else 0
        header = (f'query: "{self.query}"  ·  page {self.page + 1}/{max(self.num_pages, 1)}'
                  f'  ·  showing {start}–{end} of {self.total}')
        lines = [header]
        for i, h in enumerate(self.hits, start=start):
            lines.append(f"  {i}. {h.kind:<10} {h.name:<18} [{h.node_id}]  {_trunc(h.description)}")
        if self.page + 1 < self.num_pages:
            lines.append(f"  (next page: page={self.page + 1})")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render()


@dataclass(frozen=True)
class TagPage:
    hits: list
    page: int
    num_pages: int
    total: int
    page_size: int
    query: str

    def render(self) -> str:
        start = self.page * self.page_size + 1 if self.hits else 0
        end = start + len(self.hits) - 1 if self.hits else 0
        lines = [f'query: "{self.query}"  ·  page {self.page + 1}/{max(self.num_pages, 1)}'
                 f'  ·  showing {start}–{end} of {self.total}']
        for i, h in enumerate(self.hits, start=start):
            lines.append(f"  {i}. {h.tag:<20} ({h.score:.2f})")
        if self.page + 1 < self.num_pages:
            lines.append(f"  (next page: page={self.page + 1})")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render()


@dataclass
class ImplementResult:
    node_id: str
    results: list
    all_passing: bool


@dataclass
class RebuildReport:
    rebuilt: list = field(default_factory=list)
    passed: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
