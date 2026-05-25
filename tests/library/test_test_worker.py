from grimoire.library import Graph, CodeNode, TestStatus, new_node_id


def _impl(g, name, body, tests):
    nid = new_node_id()
    g.add_node(CodeNode(node_id=nid, name=name, description="d"), code=body, tests=tests)
    return nid


def test_worker_picks_up_rebuilt_code(tmp_path):
    g = Graph.open(tmp_path, use_test_worker=True)
    nid = _impl(g, "f", "def f():\n    return 1\n", "def test_f():\n    assert f() == 1\n")
    assert [r.status for r in g.run_tests(nid)] == [TestStatus.PASSING]
    g.update_node(g.get(nid), code="def f():\n    return 2\n",
                  tests="def test_f():\n    assert f() == 2\n")
    assert [r.status for r in g.run_tests(nid)] == [TestStatus.PASSING]


def test_worker_and_oneshot_agree_on_failure(tmp_path):
    body, tests = "def f():\n    return 1\n", "def test_f():\n    assert f() == 99\n"
    g1 = Graph.open(tmp_path / "w", use_test_worker=True)
    n1 = _impl(g1, "f", body, tests)
    g2 = Graph.open(tmp_path / "o", use_test_worker=False)
    n2 = _impl(g2, "f", body, tests)
    assert [r.status for r in g1.run_tests(n1)] == [TestStatus.FAILING]
    assert [r.status for r in g2.run_tests(n2)] == [TestStatus.FAILING]
