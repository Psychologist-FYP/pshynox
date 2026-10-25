from fastapi.testclient import TestClient

from services.api.main import app


client = TestClient(app)


def test_browse_home_and_hierarchy_have_expected_depth() -> None:
    home = client.get("/api/browse/home")
    assert home.status_code == 200
    assert home.json()["segment_count"] >= 2
    assert {item["id"] for item in home.json()["categories"]} >= {"everyday-functioning", "risk-related-language"}

    tree = client.get("/api/browse/tree", params={"node_id": "root"})
    assert tree.status_code == 200
    category_id = next(item["id"] for item in tree.json()["children"] if item["count"])
    category = client.get(f"/api/browse/node/{category_id}")
    assert category.status_code == 200
    session_id = category.json()["children"][0]["id"]
    session = client.get(f"/api/browse/node/{session_id}")
    assert session.status_code == 200
    section_id = session.json()["children"][0]["id"]
    section = client.get(f"/api/browse/node/{section_id}")
    assert section.status_code == 200
    assert section.json()["breadcrumb"][0] == {"id": "root", "label": "FYP Repository"}
    assert section.json()["documents"]


def test_browse_documents_and_scoped_search_use_same_document_shape() -> None:
    documents = client.get("/api/browse/documents", params={"node_id": "category:everyday-functioning"})
    assert documents.status_code == 200
    assert documents.json()["total"] >= 2

    search = client.get("/api/browse/search", params={"q": "sleep difficulty", "scope": "category:everyday-functioning", "k": 3})
    assert search.status_code == 200
    results = search.json()["results"]
    assert results
    assert results[0]["segment_id"] == "seg-demo-001"
    assert results[0]["category_id"] == "everyday-functioning"
    assert results[0]["context"][0]["segment_id"] == "seg-demo-001"

    empty = client.get("/api/browse/search", params={"q": "zzzz-nonexistent-term"})
    assert empty.status_code == 200
    assert empty.json()["results"] == []


def test_fuzzy_search_returns_additive_memberships_and_parsed_query() -> None:
    response = client.get("/api/browse/fuzzy", params={"q": "sleep OR worry"})

    assert response.status_code == 200
    result = response.json()[0]
    assert {"segment_id", "session_id", "score", "section", "query", "memberships", "parsed_query"} <= result.keys()
    assert 0 <= result["score"] <= 1
    assert result["parsed_query"]["tree"]["operator"] == "OR"
    assert all(0 <= value <= 1 for value in result["memberships"].values())


def test_fuzzy_search_does_not_enable_strict_not_for_risk_candidates() -> None:
    response = client.get("/api/browse/fuzzy", params={"q": "risk NOT absent", "scope": "category:risk-related-language", "not_mode": "strict-exclude"})

    assert response.status_code == 200
    assert response.json() == []


def test_preview_related_links_and_graph_have_resolvable_ids() -> None:
    preview = client.get("/api/browse/segment/seg-demo-001")
    assert preview.status_code == 200
    assert preview.json()["document"]["text"]
    assert [item["segment_id"] for item in preview.json()["context"]] == ["seg-demo-001", "seg-demo-002"]

    related = client.get("/api/browse/related/seg-demo-001")
    assert related.status_code == 200
    graph = client.get("/api/browse/graph")
    assert graph.status_code == 200
    node_ids = {node["id"] for node in graph.json()["nodes"]}
    assert all(link["source_id"] in node_ids and link["target_id"] in node_ids for link in graph.json()["links"])
    assert all(link["source_id"] == "seg-demo-001" for link in related.json()["links"])


def test_unknown_browse_nodes_and_segments_are_rejected() -> None:
    empty_category = client.get("/api/browse/node/category:risk-related-language")
    assert empty_category.status_code == 200
    assert empty_category.json()["documents"] == []
    assert client.get("/api/browse/node/category:missing").status_code == 404
    assert client.get("/api/browse/segment/seg-missing").status_code == 404
