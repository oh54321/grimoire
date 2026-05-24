"""NodeId type alias and id generator."""

import uuid

NodeId = str


def new_node_id() -> NodeId:
    """Return a short id usable as a Python module name: 'n' + 12 hex chars.

    The 'n' prefix guarantees the id is a valid Python identifier, so the Builder
    can safely emit `from build.<id> import ...` for any node.
    """
    return "n" + uuid.uuid4().hex[:12]
