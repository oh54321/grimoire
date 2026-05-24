from library.ids import NodeId, new_node_id


def test_new_node_id_format():
    nid = new_node_id()
    assert isinstance(nid, str)
    assert len(nid) == 13
    assert nid.startswith("n")
    assert nid.isidentifier()
    int(nid[1:], 16)  # remainder must parse as hex


def test_new_node_id_no_collisions_in_10000_samples():
    ids = {new_node_id() for _ in range(10_000)}
    assert len(ids) == 10_000


def test_nodeid_is_str_alias():
    nid: NodeId = "abc123"
    assert isinstance(nid, str)
