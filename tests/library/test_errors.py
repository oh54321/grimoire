from grimoire.library.errors import (
    NodeNotFound,
    DuplicateNodeId,
    CorruptMetaFile,
    DescriptionTooLong,
    InvalidNodeName,
    MissingDependency,
    BuildError,
)


def test_node_not_found_carries_id():
    err = NodeNotFound("abc123")
    assert err.node_id == "abc123"
    assert "abc123" in str(err)


def test_duplicate_node_id_carries_id():
    err = DuplicateNodeId("abc123")
    assert err.node_id == "abc123"
    assert "abc123" in str(err)


def test_corrupt_meta_file_carries_reason():
    err = CorruptMetaFile("abc123", "missing field 'name'")
    assert err.node_id == "abc123"
    assert err.reason == "missing field 'name'"


def test_description_too_long_carries_counts():
    err = DescriptionTooLong("abc123", actual=250, limit=200)
    assert err.node_id == "abc123"
    assert err.actual == 250
    assert err.limit == 200


def test_invalid_node_name_carries_name():
    err = InvalidNodeName("abc123", "1foo")
    assert err.node_id == "abc123"
    assert err.name == "1foo"


def test_missing_dependency_carries_dep_id():
    err = MissingDependency("abc123", "def456")
    assert err.node_id == "abc123"
    assert err.missing_dep_id == "def456"


def test_build_error_carries_reason():
    err = BuildError("abc123", "duplicate dep symbol: foo")
    assert err.node_id == "abc123"
    assert "duplicate" in err.reason
