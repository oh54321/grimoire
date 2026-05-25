"""Node graph data model. All node types are dataclasses for uniform equality and serialization."""

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Literal

import numpy as np

from grimoire.library.ids import NodeId

ObjectType = Literal["class", "method", "executable"]


class TestStatus(Enum):
    __test__ = False  # tell pytest this isn't a test class
    UNRUN = "unrun"
    PASSING = "passing"
    FAILING = "failing"


@dataclass(frozen=True, eq=False)
class Tag:
    """A tag attached to a node. `v` is an optional embedding vector (unused by the v1 index).

    Hash and equality are by `text` only — ndarrays aren't hashable, and two tags
    with the same text but different vectors are still the same logical tag.
    """

    text: str
    v: np.ndarray

    def __hash__(self) -> int:
        return hash(self.text)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Tag) and other.text == self.text


@dataclass
class Test:
    """A single test entry on a CodeNode. The actual test code lives in <root>/<node_id>/tests.py.

    `name` matches the pytest function name without the `test_` prefix.
    """

    __test__ = False  # tell pytest this isn't a test class

    name: str
    status: TestStatus = TestStatus.UNRUN


@dataclass
class Node:
    node_id: NodeId
    name: str
    description: str
    parent_id: NodeId | None = None
    tags: set[Tag] = field(default_factory=set)
    searchable: bool = True


@dataclass
class FolderNode(Node):
    children: set[NodeId] = field(default_factory=set)
    node_type: ClassVar[str] = "folder"


@dataclass
class CodeNode(Node):
    dependencies: set[NodeId] = field(default_factory=set)
    object_type: ObjectType = "method"
    tests: list[Test] = field(default_factory=list)
    is_tool: bool = True
    node_type: ClassVar[str] = "code"
