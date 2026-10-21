from liara_agent.liara import docs
from liara_agent.tools.project import ProjectTools, tool_schemas


def test_the_snapshot_exists_and_finds_the_right_page_for_a_question():
    assert len(docs.index().chunks) > 1000
    hits = docs.search("liara.json port platform node", 5)
    assert hits and any("liara.json" in (h["text"] + h["title"]).lower() for h in hits)
    assert all(h["url"].startswith("https://docs.liara.ir/llms/") for h in hits)


def test_persian_and_unknown_queries():
    assert docs.search("دامنه اختصاصی و گواهی SSL", 3)
    assert docs.search("qqqzzzxxx", 3) == [] and docs.as_text([]) == "no matching documentation found"


def test_no_page_fills_the_whole_answer():
    hits = docs.search("database", 5)
    from collections import Counter
    assert max(Counter(h["path"] for h in hits).values()) <= 2


def test_the_agent_has_the_tool(tmp_path):
    assert "search_docs" in {t["function"]["name"] for t in tool_schemas()}
    out = ProjectTools(tmp_path).call("search_docs", {"query": "disk persistent storage", "max_results": 2})
    assert out.startswith("[") and "docs.liara.ir" in out
