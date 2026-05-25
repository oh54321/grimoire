from library import Graph, CodeNode, new_node_id


def _code(graph, name, body, deps=()):
    nid = new_node_id()
    graph.add_node(
        CodeNode(node_id=nid, name=name, description="d", dependencies=set(deps)),
        code=body, tests="",
    )
    return nid


def test_is_build_stale_tracks_self_and_deps(tmp_path):
    g = Graph.open(tmp_path)
    dep = _code(g, "dep", "def dep():\n    return 1\n")
    main = _code(g, "main", "def main():\n    return dep()\n", deps=[dep])
    g.ensure_built(main)
    assert g.is_build_stale(main) is False
    node = g.get(dep)
    g.update_node(node, code="def dep():\n    return 2\n")
    assert g.is_build_stale(main) is True


def test_iter_code_ids_excludes_folders(tmp_path):
    from library import FolderNode
    g = Graph.open(tmp_path)
    fid = new_node_id()
    g.add_node(FolderNode(node_id=fid, name="f", description="d"))
    cid = _code(g, "fn", "def fn():\n    return 0\n")
    assert set(g.iter_code_ids()) == {cid}
    assert fid in set(g.iter_ids())
