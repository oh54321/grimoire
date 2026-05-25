from grimoire.api.errors import ApiError, ImplementationFailed, InvalidMove


def test_implementation_failed_carries_results():
    e = ImplementationFailed("n1", results=[], detail="boom")
    assert isinstance(e, ApiError)
    assert e.node_id == "n1" and e.detail == "boom" and e.results == []


def test_invalid_move_reason():
    e = InvalidMove("n1", "n2", "into-own-subtree")
    assert isinstance(e, ApiError)
    assert "n1" in str(e) and "into-own-subtree" in str(e)
