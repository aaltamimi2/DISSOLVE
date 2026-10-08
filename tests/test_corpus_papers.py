"""The corpus papers: shipped records, the resolver that confirms them against the papers' own text, the paper list,
the full-text search grouped by paper, and the paper records the literature search attaches to its passages."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from dissolve import corpus_papers, research

SHIPPED = research._SHIPPED_CORPUS_DIR
SHA_A = "a" * 64
SHA_B = "b" * 64


def _write_release(directory: Path, fronts: dict[str, list[tuple[str, str]]]) -> Path:
    """A release with one document per SHA and its (text, page) passages, in order."""
    directory.mkdir(parents=True, exist_ok=True)
    chunks = []
    for sha, passages in fronts.items():
        for n, (text, page) in enumerate(passages, 1):
            chunks.append({"chunk_id": f"T5-{sha[:12]}-{n:04d}", "paper_sha256": sha, "body": text, "text": text,
                           "page": page, "title": "", "source": ""})
    index = {"documents": [{"document_id": f"D{sha[:16]}", "sha256": sha, "title": "", "source": "",
                            "parser_backend": "docling"} for sha in fronts], "chunks": chunks}
    with gzip.open(directory / "index.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(index, handle)
    return directory


def _work(doi: str, title: str, year: int = 2024, authors=("Ada Smith", "Bo Jones", "Cy Lee")) -> dict:
    return {"id": f"https://openalex.org/W{abs(hash(doi)) % 10**8}", "doi": f"https://doi.org/{doi}", "title": title,
            "publication_year": year, "publication_date": f"{year}-01-01", "type": "article", "cited_by_count": 3,
            "authorships": [{"author": {"display_name": a}} for a in authors],
            "primary_location": {"source": {"display_name": "Waste Management"}},
            "best_oa_location": None, "abstract_inverted_index": None, "is_retracted": False}


def test_shipped_records_cover_every_paper_of_the_shipped_release_and_each_is_confirmed():
    payload = json.loads((SHIPPED / corpus_papers.PAPERS_FILE).read_text(encoding="utf-8"))
    manifest = json.loads((SHIPPED / "manifest.json").read_text(encoding="utf-8"))
    with gzip.open(SHIPPED / "index.json.gz", "rt", encoding="utf-8") as handle:
        documents = {d["sha256"] for d in json.load(handle)["documents"]}
    assert payload["schema"] == corpus_papers.SCHEMA
    assert payload["release_gzip_sha256"] == manifest["gzip_sha256"]  # built for exactly this release
    assert set(payload["papers"]) | set(payload["unresolved"]) == documents
    assert not payload["unresolved"]
    for record in payload["papers"].values():
        assert record["title"] and record["doi"] and record["openalex_id"] and record["retrieved_at"]
        assert record["source"] == "openalex" and record["match"]["title_support"] >= corpus_papers.SUPPORT_MIN
    dois = [record["doi"] for record in payload["papers"].values()]
    assert len(dois) == len(set(dois))


def test_title_support_reads_the_title_in_the_text_and_nothing_else():
    text = "Research Paper  A solvent-targeted recovery and precipitation scheme for the recycling of up to ten polymers"
    assert corpus_papers.title_support("A solvent-targeted recovery and precipitation scheme", text) == 1.0
    assert corpus_papers.title_support("A solvent targeted recovery & precipitation schemes", text) >= 0.92
    assert corpus_papers.title_support("Pyrolysis of polystyrene over zeolite catalysts", text) < 0.6
    assert corpus_papers.title_support("short", text) == 0.0


def test_the_resolver_keeps_only_what_the_paper_text_confirms(tmp_path, monkeypatch):
    """Paper A prints its DOI and title; paper B's only candidate is a record whose title its text never mentions, so
    B stays unresolved instead of being given that record. A second build repeats no request."""
    release = _write_release(tmp_path / "release", {
        SHA_A: [("Full length article Selective dissolution of polyolefins from multilayer packaging films "
                 "https://doi.org/10.1000/aaa.1 Ada Smith, Bo Jones", "1")],
        SHA_B: [("Contents lists available. A study of something the records do not describe at all.", "1")],
    })
    calls = []

    def fake_request_json(url, *, source, params, timeout):
        calls.append((source, dict(params)))
        if source == "crossref":
            return {"message": {"items": [{"DOI": "10.1000/bbb.2"}]}}
        return {"results": [_work("10.1000/aaa.1", "Selective dissolution of polyolefins from multilayer packaging films"),
                            _work("10.1000/bbb.2", "Catalytic pyrolysis of mixed plastic waste in a fluidized bed")]}

    monkeypatch.setattr(research, "_request_json", fake_request_json)
    monkeypatch.setattr(corpus_papers.time, "sleep", lambda s: None)
    summary = corpus_papers.build_papers(release, cache=tmp_path / "cache", openalex_key="secret-key", log=lambda m: None)
    payload = json.loads((release / corpus_papers.PAPERS_FILE).read_text(encoding="utf-8"))
    assert summary["resolved"] == 1 and set(payload["papers"]) == {SHA_A}
    record = payload["papers"][SHA_A]
    assert record["doi"] == "10.1000/aaa.1" and "doi_in_text" in record["match"]["via"]
    assert record["authors"] == ["Ada Smith", "Bo Jones", "Cy Lee"] and record["venue"] == "Waste Management"
    assert payload["unresolved"][SHA_B].startswith("no candidate confirmed by the paper's own text")
    assert all("api_key" not in params or source == "openalex" for source, params in calls)
    before = len(calls)
    corpus_papers.build_papers(release, cache=tmp_path / "cache", openalex_key="secret-key", log=lambda m: None)
    assert len(calls) == before  # every response came from the cache
    cached = [gzip.open(path, "rt", encoding="utf-8").read() for path in (tmp_path / "cache").rglob("*.json.gz")]
    assert cached and not any("secret-key" in text for text in cached)
    assert "secret-key" not in (release / corpus_papers.PAPERS_FILE).read_text(encoding="utf-8")


def test_the_cache_key_never_holds_the_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "_request_json", lambda url, **kw: {"results": []})
    monkeypatch.setattr(corpus_papers.time, "sleep", lambda s: None)
    store = corpus_papers._Cache(tmp_path)
    store.get_json("https://api.openalex.org/works", {"filter": "doi:x", "api_key": "k1"}, source="openalex",
                   interval=0, secret_params=("api_key",))
    calls = []
    monkeypatch.setattr(research, "_request_json", lambda url, **kw: calls.append(kw) or {"results": []})
    store.get_json("https://api.openalex.org/works", {"filter": "doi:x", "api_key": "k2"}, source="openalex",
                   interval=0, secret_params=("api_key",))
    assert calls == []  # a different key is the same request


def test_the_paper_list_names_unresolved_papers_without_inventing_them(tmp_path):
    release = _write_release(tmp_path / "release", {
        SHA_A: [("first", "1"), ("second", "3-4"), ("third", "7")],
        SHA_B: [("only", "2")],
    })
    (release / corpus_papers.PAPERS_FILE).write_text(json.dumps({
        "schema": corpus_papers.SCHEMA, "built_at": "2026-10-08T00:00:00Z",
        "papers": {SHA_A: {"title": "Paper A", "year": 2020, "authors": ["Ada Smith"], "doi": "10.1/a"}},
        "unresolved": {SHA_B: "no candidate confirmed by the paper's own text"},
    }))
    with gzip.open(release / "index.json.gz", "rt", encoding="utf-8") as handle:
        index = json.load(handle)
    listing = corpus_papers.load_papers(index, release)
    assert (listing["count"], listing["resolved"]) == (2, 1)
    first, second = listing["papers"]
    assert first["sha256"] == SHA_A and first["title"] == "Paper A" and first["resolved"] is True
    assert (first["passages"], first["pages"]) == (3, 7)
    assert second["sha256"] == SHA_B and second["resolved"] is False and "title" not in second
    assert second["reason"] == "no candidate confirmed by the paper's own text"


def test_full_text_search_groups_passages_by_paper_in_order_of_their_best_passage(tmp_path, monkeypatch):
    release = _write_release(tmp_path / "release", {
        SHA_A: [(f"a{n}", str(n)) for n in range(1, 6)],
        SHA_B: [("b1", "2")],
    })
    with gzip.open(release / "index.json.gz", "rt", encoding="utf-8") as handle:
        index = json.load(handle)
    monkeypatch.setattr(research, "_load_index", lambda kb: index)
    monkeypatch.setattr(corpus_papers, "release_dir", lambda: release)
    order = [f"T5-{SHA_A[:12]}-0001", f"T5-{SHA_B[:12]}-0001", f"T5-{SHA_A[:12]}-0002", f"T5-{SHA_A[:12]}-0003",
             f"T5-{SHA_A[:12]}-0004"]
    monkeypatch.setattr(research, "_search_index", lambda index, query, top_k, mode, **kw: [
        {"chunk_id": cid, "page": "1", "section": None, "excerpt": cid} for cid in order][:top_k])
    found = corpus_papers.search_papers("anything", per_paper=3)
    assert [hit["paper"]["sha256"] for hit in found["results"]] == [SHA_A, SHA_B]
    assert [p["rank"] for p in found["results"][0]["passages"]] == [1, 3, 4]
    with pytest.raises(ValueError):
        corpus_papers.search_papers("  ")


def test_search_results_carry_their_papers_after_ranking(monkeypatch):
    index = {"chunks": [{"chunk_id": "c1", "paper_sha256": SHA_A}, {"chunk_id": "c2", "paper_sha256": SHA_B}]}
    monkeypatch.setattr(research, "_paper_records", lambda: {SHA_A: {
        "title": "Paper A", "year": 2021, "venue": "Polymers", "doi": "10.1/a", "authors": ["Ada Smith", "Bo Jones", "Cy Lee"]}})
    rows = [{"chunk_id": "c2", "title": "", "final_score": 0.9}, {"chunk_id": "c1", "title": "", "final_score": 0.5}]
    research._attach_paper_records(index, rows)
    assert [row["chunk_id"] for row in rows] == ["c2", "c1"] and [row["final_score"] for row in rows] == [0.9, 0.5]
    assert rows[0]["title"] == ""  # no record for that paper: nothing is filled in
    assert rows[1] | {} == {"chunk_id": "c1", "title": "Paper A", "final_score": 0.5, "year": 2021, "venue": "Polymers",
                            "doi": "10.1/a", "url": "https://doi.org/10.1/a", "authors": "Smith et al."}
