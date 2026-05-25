"""On-disk node store. Each node lives at <root>/<node_id>/."""

import json
import keyword
import os
import shutil
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from library.config import LibraryConfig
from library.errors import (
    CorruptMetaFile,
    DescriptionTooLong,
    InvalidNodeName,
    NodeNotFound,
)
from library.ids import NodeId
from library.nodes import CodeNode, FolderNode, Node, Tag, Test, TestStatus
from library.tokens import count_tokens


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data)
    os.replace(tmp, path)


def _serialize_tags(tags: set[Tag]) -> list[dict]:
    return [{"text": t.text, "v": t.v.tolist()} for t in tags]


def _deserialize_tags(raw: list[dict]) -> set[Tag]:
    return {Tag(text=t["text"], v=np.asarray(t["v"], dtype=float)) for t in raw}


def _serialize_tests(tests: list[Test]) -> list[dict]:
    return [{"name": t.name, "status": t.status.value} for t in tests]


def _deserialize_tests(raw: list[dict]) -> list[Test]:
    return [Test(name=t["name"], status=TestStatus(t["status"])) for t in raw]


def _node_to_dict(node: Node) -> dict[str, Any]:
    base: dict[str, Any] = {
        "node_id": node.node_id,
        "node_type": node.node_type,
        "name": node.name,
        "description": node.description,
        "parent_id": node.parent_id,
        "tags": _serialize_tags(node.tags),
        "searchable": node.searchable,
    }
    if isinstance(node, FolderNode):
        base["children"] = sorted(node.children)
    elif isinstance(node, CodeNode):
        base["dependencies"] = sorted(node.dependencies)
        base["object_type"] = node.object_type
        base["tests"] = _serialize_tests(node.tests)
        base["is_tool"] = node.is_tool
    return base


def _dict_to_node(d: dict[str, Any]) -> Node:
    node_type = d.get("node_type")
    common = dict(
        node_id=d["node_id"],
        name=d["name"],
        description=d["description"],
        parent_id=d.get("parent_id"),
        tags=_deserialize_tags(d.get("tags", [])),
        searchable=d.get("searchable", True),
    )
    if node_type == "folder":
        return FolderNode(**common, children=set(d.get("children", [])))
    if node_type == "code":
        return CodeNode(
            **common,
            dependencies=set(d.get("dependencies", [])),
            object_type=d.get("object_type", "method"),
            tests=_deserialize_tests(d.get("tests", [])),
            is_tool=d.get("is_tool", True),
        )
    raise CorruptMetaFile(d.get("node_id", "<unknown>"), f"unknown node_type: {node_type!r}")


class NodeStore:
    def __init__(self, config: LibraryConfig) -> None:
        self.config = config
        self.root = config.root_path
        self.root.mkdir(parents=True, exist_ok=True)

    def node_dir(self, node_id: NodeId) -> Path:
        return self.root / node_id

    def exists(self, node_id: NodeId) -> bool:
        return (self.node_dir(node_id) / "meta.json").exists()

    def load(self, node_id: NodeId) -> Node:
        path = self.node_dir(node_id) / "meta.json"
        if not path.exists():
            raise NodeNotFound(node_id)
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise CorruptMetaFile(node_id, f"invalid json: {e}") from e
        try:
            return _dict_to_node(data)
        except (KeyError, TypeError) as e:
            raise CorruptMetaFile(node_id, f"missing/invalid field: {e}") from e

    def load_code(self, node_id: NodeId) -> str:
        if not self.exists(node_id):
            raise NodeNotFound(node_id)
        path = self.node_dir(node_id) / "code.py"
        return path.read_text() if path.exists() else ""

    def load_tests(self, node_id: NodeId) -> str:
        if not self.exists(node_id):
            raise NodeNotFound(node_id)
        path = self.node_dir(node_id) / "tests.py"
        return path.read_text() if path.exists() else ""

    def save(self, node: Node, code: str | None = None, tests: str | None = None) -> None:
        self._validate(node)
        d = self.node_dir(node.node_id)
        d.mkdir(parents=True, exist_ok=True)
        _atomic_write(d / "meta.json", json.dumps(_node_to_dict(node), indent=2, sort_keys=True))
        if code is not None:
            _atomic_write(d / "code.py", code)
        if tests is not None:
            _atomic_write(d / "tests.py", tests)

    def delete(self, node_id: NodeId) -> None:
        if not self.exists(node_id):
            raise NodeNotFound(node_id)
        shutil.rmtree(self.node_dir(node_id))

    def iter_ids(self) -> Iterator[NodeId]:
        for entry in self.root.iterdir():
            if not entry.is_dir():
                continue
            if entry.name == "build":
                continue
            if (entry / "meta.json").exists():
                yield entry.name

    def size_on_disk(self, node_id: NodeId) -> int:
        if not self.exists(node_id):
            raise NodeNotFound(node_id)
        total = 0
        for f in self.node_dir(node_id).iterdir():
            if f.is_file():
                total += f.stat().st_size
        return total

    def _validate(self, node: Node) -> None:
        tokens_in_desc = count_tokens(node.description, self.config.tokenizer_encoding)
        if tokens_in_desc > self.config.max_description_tokens:
            raise DescriptionTooLong(node.node_id, tokens_in_desc, self.config.max_description_tokens)
        if isinstance(node, CodeNode):
            if not node.name.isidentifier() or keyword.iskeyword(node.name):
                raise InvalidNodeName(node.node_id, node.name)
