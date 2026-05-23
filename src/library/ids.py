"""NodeId type alias and id generator."""

import uuid

NodeId = str


def new_node_id() -> NodeId:
    """Return a short hex id (first 12 chars of uuid4)."""
    return uuid.uuid4().hex[:12]
