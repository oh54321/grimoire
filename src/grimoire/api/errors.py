from dataclasses import dataclass, field


class ApiError(Exception):
    """Base class for api-layer errors (also raised for a corrupt multi-root tree)."""


@dataclass
class ImplementationFailed(ApiError):
    node_id: str
    results: list = field(default_factory=list)   # list[library.TestResult]
    detail: str = ""

    def __str__(self) -> str:
        return f"implementation failed for {self.node_id}: {self.detail}"


@dataclass
class InvalidMove(ApiError):
    node_id: str
    target_id: str
    reason: str

    def __str__(self) -> str:
        return f"cannot move {self.node_id} -> {self.target_id}: {self.reason}"
