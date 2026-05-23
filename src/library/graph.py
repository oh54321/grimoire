from dataclasses import dataclass
import numpy as np
from typing import Optional, Set, List, Literal
from pathlib import Path

NodeId = str
ObjectType = Literal['class', 'method', 'executable']


class CodeDiskCache:
    def __init__(self, root_path: Path, max_local_mb: int) -> None:
        self.root_path = root_path
        self.local_cache = {}
        self.max_local_mb = max_local_mb
    
    def get_local_cache(self, code_filepath: Path) -> Path:
        return self.local_cache[code_filepath]
    
    def set_local_cache(self, code_filepath: Path, local_cache: Path) -> None:
        self.local_cache[code_filepath] = local_cache
    
    def prune_local_cache(self) -> None:
        if len(self.local_cache) > self.max_local_mb:
            self.local_cache.pop(min(self.local_cache.keys(), key=lambda x: self.local_cache[x].size))

@dataclass
class CodeImplementation:
    cache_id: NodeId
    dependencies: Set[NodeId]
    code_filepath: Path


@dataclass
class Tag:
    text: str
    v: np.ndarray


class Buildable:
    is_built: bool = False
    cache_id: NodeId

    def set_built(self) -> None:
        self.is_built = True

class Node:
    parent_id: Optional[NodeId]
    node_type: str
    name: str
    description: str
    tags: Set[Tag]

    def has_parent(self) -> bool:
        return self.parent_id is not None


class FolderNode(Node):
    children: Set[NodeId]
    node_type: str = "folder"

    def add_child(self, child_id: NodeId) -> None:
        self.children.add(child_id)
    
    def remove_child(self, child_id: NodeId) -> None:
        self.children.remove(child_id)

class CodeNode(Node, Buildable):
    dependencies: Set[NodeId]
    object_type: ObjectType
    tests: List["Test"]
    node_type: str = "code"

    def add_dependency(self, dependency_id: NodeId) -> None:
        self.dependencies.add(dependency_id)
    
    def remove_dependency(self, dependency_id: NodeId) -> None:
        self.dependencies.remove(dependency_id)

    def set_built(self) -> None:
        self.is_built = True
        for test in self.tests:
            test.set_built()

class Test(Buildable):
    code_node: CodeNode
    description: str
