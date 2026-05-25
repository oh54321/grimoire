from api.results import SearchHit, SearchPage, TagHit, TagPage


def _hit(i):
    return SearchHit(node_id=f"n{i}", name=f"fn{i}", kind="method",
                     description="x" * 200, score=0.9)


def test_search_page_render_has_nav_and_ids():
    hits = [_hit(1), _hit(2)]
    page = SearchPage(hits=hits, page=0, num_pages=3, total=25, page_size=2, query="q")
    text = page.render()
    assert "page 1/3" in text and "of 25" in text
    assert "n1" in text and "n2" in text
    assert "method" in text
    assert "x" * 200 not in text          # description truncated
    assert str(page) == text


def test_empty_search_page_renders():
    page = SearchPage(hits=[], page=0, num_pages=0, total=0, page_size=10, query="q")
    assert "of 0" in page.render()


def test_tag_page_render():
    page = TagPage(hits=[TagHit("statistics", 0.82)], page=0, num_pages=1, total=1,
                   page_size=10, query="stats")
    assert "statistics" in page.render() and "0.82" in page.render()
