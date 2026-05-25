from library import Graph, CodeNode, TestStatus, new_node_id


def test_trial_run_does_not_commit(tmp_path):
    g = Graph.open(tmp_path)
    nid = new_node_id()
    g.add_node(CodeNode(node_id=nid, name="inc", description="add one"))  # abstraction only, no code
    results = g.trial_run(nid, "def inc(x):\n    return x + 1\n",
                          "def test_inc():\n    assert inc(1) == 2\n")
    assert [r.status for r in results] == [TestStatus.PASSING]
    assert g.get_code(nid) == ""        # trial wrote nothing canonical


def test_discard_trial_first_impl_removes_scratch(tmp_path):
    g = Graph.open(tmp_path)
    nid = new_node_id()
    g.add_node(CodeNode(node_id=nid, name="bad", description="d"))
    g.trial_run(nid, "def bad():\n    return 1\n", "def test_bad():\n    assert bad() == 2\n")
    g.discard_trial(nid)
    assert not (tmp_path / "build" / f"{nid}.py").exists()


def test_trial_run_on_folder_raises(tmp_path):
    import pytest
    from library import FolderNode, BuildError
    g = Graph.open(tmp_path)
    fid = new_node_id()
    g.add_node(FolderNode(node_id=fid, name="f", description="d"))
    with pytest.raises(BuildError):
        g.trial_run(fid, "x = 1\n", "")
