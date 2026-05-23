"""Exception types raised by the library. All carry the offending node_id."""

from dataclasses import dataclass


class LibraryError(Exception):
    """Base class for all library exceptions."""


@dataclass
class NodeNotFound(LibraryError):
    node_id: str

    def __str__(self) -> str:
        return f"node not found: {self.node_id}"


@dataclass
class DuplicateNodeId(LibraryError):
    node_id: str

    def __str__(self) -> str:
        return f"duplicate node id: {self.node_id}"


@dataclass
class CorruptMetaFile(LibraryError):
    node_id: str
    reason: str

    def __str__(self) -> str:
        return f"corrupt meta.json for {self.node_id}: {self.reason}"


@dataclass
class DescriptionTooLong(LibraryError):
    node_id: str
    actual: int
    limit: int

    def __str__(self) -> str:
        return f"description for {self.node_id} is {self.actual} tokens (limit {self.limit})"


@dataclass
class InvalidNodeName(LibraryError):
    node_id: str
    name: str

    def __str__(self) -> str:
        return f"node {self.node_id} name {self.name!r} is not a valid Python identifier"


@dataclass
class MissingDependency(LibraryError):
    node_id: str
    missing_dep_id: str

    def __str__(self) -> str:
        return f"node {self.node_id} declares missing dep {self.missing_dep_id}"


@dataclass
class BuildError(LibraryError):
    node_id: str
    reason: str

    def __str__(self) -> str:
        return f"build failed for {self.node_id}: {self.reason}"
