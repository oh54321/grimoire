from __future__ import annotations

from typing import Generic, Iterator, List, TypeVar

T = TypeVar("T")


class Page(Generic[T]):
    def __init__(self, items: List[T]) -> None:
        self._items: List[T] = list(items)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __getitem__(self, index: int) -> T:
        return self._items[index]

    def __repr__(self) -> str:
        return f"Page({self._items!r})"


class PagedList(Generic[T]):
    def __init__(self, items: List[T], page_size: int) -> None:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        self._items: List[T] = list(items)
        self._page_size: int = page_size
        self._pages: List[Page[T]] = [
            Page(self._items[i : i + page_size])
            for i in range(0, len(self._items), page_size)
        ]

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __getitem__(self, index: int) -> T:
        return self._items[index]

    def __contains__(self, value: object) -> bool:
        return value in self._items

    def __repr__(self) -> str:
        return f"PagedList({self._items!r}, page_size={self._page_size})"

    @property
    def page_size(self) -> int:
        return self._page_size

    @property
    def num_pages(self) -> int:
        return len(self._pages)

    def get_page(self, n: int) -> Page[T]:
        if n < 0 or n >= len(self._pages):
            raise IndexError(f"page {n} out of range (0..{len(self._pages) - 1})")
        return self._pages[n]
