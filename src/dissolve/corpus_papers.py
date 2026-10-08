"""The papers of the literature corpus: their bibliographic records, a list to browse and a search over their text.

The served release holds each paper as text passages identified by the SHA-256 of its file. papers.json, next to the
release's index.json.gz, gives each paper its title, authors, year, venue, DOI and abstract. ``build_papers`` resolves
them from public records and keeps only what the paper's own text confirms:

- candidates: DOIs printed in the paper's first passages, DOIs and titles from hint files (the requests and census of
  the corpus intake), and Crossref's bibliographic match on the first passages;
- each candidate DOI is looked up in OpenAlex and kept only if its title appears in the paper's first passages, or in
  the title its hint gives, with a fuzzy partial match of at least SUPPORT_MIN;
- a paper without a confirmed candidate stays unresolved, with the reason. Nothing is inferred.

Every record carries its source, the OpenAlex id, the time it was retrieved and how it was matched.

    python -m dissolve.corpus papers [--corpus DIR] [--hint FILE ...] [--mailto EMAIL]
"""

from __future__ import annotations

import csv
import difflib
import gzip
import hashlib
import json
import os
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import research

PAPERS_FILE = "papers.json"
SCHEMA = "dissolve.corpus-papers.v1"
SUPPORT_MIN = 0.92
FRONT_PASSAGES = 3
SHIPPED = research._SHIPPED_CORPUS_DIR
OPENALEX_WORKS = "https://api.openalex.org/works"
CROSSREF_WORKS = "https://api.crossref.org/works"
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>|]+)", re.I)
#: Crossref's pattern for modern DOIs; a candidate outside it is dropped (a comma would split an OpenAlex filter).
_SAFE_DOI = re.compile(r"^10\.\d{4,9}/[-._;()/:a-z0-9]+$")
_SELECT = ",".join([
    "id", "doi", "title", "publication_year", "publication_date", "type", "cited_by_count", "authorships",
    "primary_location", "best_oa_location", "abstract_inverted_index", "is_retracted",
])


# --- reading ----------------------------------------------------------------------------------------------------------


def release_dir() -> Path:
    """The release the literature search serves (DISSOLVE_BGE10_MANIFEST, the working release, else the shipped one)."""
    manifest = research._declared_bge10_manifest_path()
    return manifest.parent if manifest is not None else SHIPPED


def _records(directory: Path) -> dict[str, Any]:
    path = Path(directory) / PAPERS_FILE
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if payload.get("schema") == SCHEMA else {}


def load_papers(index: Mapping[str, Any] | None = None, directory: Path | None = None) -> dict[str, Any]:
    """Every paper of the served index with its record (or why it has none), its passage count and its last page.

    Records come from the release's papers.json, and from the shipped one for papers it does not cover, matched by
    SHA-256, so a corpus grown with ``add`` lists its new papers as unresolved until ``papers`` runs again.
    """
    index = index if index is not None else research._load_index(research._PRODUCT_KNOWLEDGEBASE)
    own = _records(directory if directory is not None else release_dir())
    shipped = _records(SHIPPED)
    records = {**(shipped.get("papers") or {}), **(own.get("papers") or {})}
    reasons = {**(shipped.get("unresolved") or {}), **(own.get("unresolved") or {})}
    passages: dict[str, int] = defaultdict(int)
    pages: dict[str, int] = {}
    for chunk in index.get("chunks") or []:
        sha = str(chunk.get("paper_sha256") or "")
        passages[sha] += 1
        for page in re.findall(r"\d+", str(chunk.get("page") or "")):
            pages[sha] = max(pages.get(sha, 0), int(page))
    out = []
    for document in index.get("documents") or []:
        sha = str(document.get("sha256") or "")
        record = records.get(sha)
        row = {"document_id": document.get("document_id"), "sha256": sha, "passages": passages.get(sha, 0),
               "pages": pages.get(sha)}
        if record:
            row.update(record, resolved=True)
        else:
            row.update(resolved=False, reason=reasons.get(sha) or "not resolved for this release")
        out.append(row)
    out.sort(key=lambda r: (not r["resolved"], -(r.get("year") or 0), _norm(r.get("title") or "")))
    return {"schema": SCHEMA, "built_at": own.get("built_at") or shipped.get("built_at"), "count": len(out),
            "resolved": sum(1 for r in out if r["resolved"]), "papers": out}


def search_papers(query: str, *, limit: int = 20, per_paper: int = 3, depth: int = 50) -> dict[str, Any]:
    """Papers whose text matches ``query``: the served ranking over passages (as the literature tool ranks them, to
    ``depth``), grouped by paper in the order of each paper's best passage, with up to ``per_paper`` passages each."""
    if not str(query or "").strip():
        raise ValueError("empty_query")
    index = research._load_index(research._PRODUCT_KNOWLEDGEBASE)
    mode, rerank = "hybrid", "off"
    if research._bge10_hybrid_fusion_enabled(index) and research.pair_rerank_enabled():
        rerank = research.PAIR_RERANK_MODE
    rows = research._search_index(index, query, depth, mode, rerank_mode=rerank)
    by_sha = {str(chunk["chunk_id"]): str(chunk.get("paper_sha256") or "") for chunk in index.get("chunks") or []}
    papers = {row["sha256"]: row for row in load_papers(index)["papers"]}
    grouped: dict[str, dict[str, Any]] = {}
    for rank, row in enumerate(rows, 1):
        sha = by_sha.get(str(row["chunk_id"]), "")
        if sha not in papers:
            continue
        hit = grouped.setdefault(sha, {"paper": papers[sha], "best_rank": rank, "passages": []})
        if len(hit["passages"]) < per_paper:
            hit["passages"].append({"chunk_id": row["chunk_id"], "rank": rank, "page": row.get("page"),
                                    "section": row.get("section"), "excerpt": row.get("excerpt")})
    results = sorted(grouped.values(), key=lambda hit: hit["best_rank"])[:max(1, int(limit))]
    return {"query": query, "depth": depth, "passages_ranked": len(rows), "results": results}


# --- building -----------------------------------------------------------------------------------------------------------


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def title_support(title: str, *texts: str) -> float:
    """How well ``title`` appears inside any of ``texts``: the best SequenceMatcher ratio of the normalized title
    against a same-length window of the normalized text (1.0 = verbatim)."""
    needle = _norm(title)
    if len(needle) < 12:
        return 0.0
    best = 0.0
    for text in texts:
        hay = _norm(text)
        if not hay:
            continue
        if needle in hay:
            return 1.0
        words = hay.split(" ")
        span = len(needle.split(" "))
        for start in range(max(1, len(words) - span + 1)):
            for width in (span - 1, span, span + 1):
                window = " ".join(words[start:start + max(1, width)])
                best = max(best, difflib.SequenceMatcher(None, needle, window).ratio())
    return best


def _hints(paths: Sequence[Path]) -> dict[str, dict[str, str]]:
    """sha256 -> {"title", "doi"} from CSV rows (sha256 or pdf_sha256; title; doi or doi_or_url) or a JSON list under
    "papers" (sha256; title or filename)."""
    out: dict[str, dict[str, str]] = {}
    for path in paths:
        path = Path(path)
        if path.suffix.lower() == ".csv":
            with path.open(newline="", encoding="utf-8") as handle:
                rows: Iterable[Mapping[str, Any]] = list(csv.DictReader(handle))
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("papers") if isinstance(payload, dict) else payload
        for row in rows or []:
            sha = str(row.get("sha256") or row.get("pdf_sha256") or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                continue
            title = str(row.get("title") or "").strip()
            if not title and row.get("filename"):
                title = re.sub(r"[_]+", " ", Path(str(row["filename"])).stem)
            doi = _DOI.search(str(row.get("doi") or row.get("doi_or_url") or ""))
            hint = out.setdefault(sha, {})
            if title and "title" not in hint:
                hint["title"] = title
            if doi and "doi" not in hint:
                hint["doi"] = _clean_doi(doi.group(1))
    return out


def _clean_doi(doi: str) -> str:
    return doi.strip().rstrip(".,;)]}").lower()


class _Cache:
    """Responses as gzip JSON keyed by sha1 of the request, so a rebuild repeats no request."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def get_json(self, url: str, params: Mapping[str, Any], *, source: str, interval: float,
                 secret_params: Sequence[str] = ()) -> dict[str, Any]:
        kept = {k: v for k, v in sorted(params.items()) if k not in {"mailto", *secret_params}}
        key = hashlib.sha1(json.dumps([url, kept], sort_keys=True).encode()).hexdigest()
        path = self.root / source / f"{key}.json.gz"
        if path.is_file():
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                return json.load(handle)
        time.sleep(interval)
        payload = research._request_json(url, source=source, params=dict(params), timeout=60)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            json.dump({"retrieved_at": _now(), "data": payload}, handle)
        temporary.replace(path)
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _abstract(inverted: Mapping[str, Sequence[int]] | None) -> str | None:
    if not inverted:
        return None
    slots = {position: word for word, positions in inverted.items() for position in positions}
    text = re.sub(r"\s+", " ", " ".join(slots[i] for i in sorted(slots))).strip()
    text = re.sub(r"(?i)^abstract[:.\s]+", "", text)
    return text if len(text) >= 80 else None


def _record(work: Mapping[str, Any], retrieved_at: str, match: Mapping[str, Any]) -> dict[str, Any]:
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    oa = work.get("best_oa_location") or {}
    authors = [str((a.get("author") or {}).get("display_name") or "").strip() for a in work.get("authorships") or []]
    authors = [name for name in authors if name]
    return {
        "title": re.sub(r"\s+", " ", str(work.get("title") or "")).strip(),
        "authors": authors[:20], "author_count": len(authors),
        "year": work.get("publication_year"), "date": work.get("publication_date"),
        "venue": source.get("display_name"), "type": work.get("type"),
        "doi": _clean_doi(str(work.get("doi") or "").replace("https://doi.org/", "")) or None,
        "openalex_id": str(work.get("id") or "").rsplit("/", 1)[-1] or None,
        "cited_by_count": work.get("cited_by_count"),
        "open_access_url": oa.get("landing_page_url") or oa.get("pdf_url"),
        "abstract": _abstract(work.get("abstract_inverted_index")),
        "is_retracted": bool(work.get("is_retracted")),
        "source": "openalex", "retrieved_at": retrieved_at, "match": dict(match),
    }


def build_papers(directory: Path, *, hints: Sequence[Path] = (), mailto: str | None = None,
                 openalex_key: str | None = None, cache: Path | None = None,
                 log: Callable[[str], None] = print) -> dict[str, Any]:
    """Resolve every paper of the release in ``directory`` and write its papers.json; returns the summary."""
    directory = Path(directory)
    with gzip.open(directory / "index.json.gz", "rt", encoding="utf-8") as handle:
        index = json.load(handle)
    store = _Cache(cache or Path("~/.cache/dissolve/corpus-papers").expanduser())
    hint = _hints(hints)
    chunks: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for chunk in index["chunks"]:
        chunks[str(chunk["paper_sha256"])].append(chunk)
    polite = {"mailto": mailto} if mailto else {}
    candidates: dict[str, dict[str, list[str]]] = {}
    fronts: dict[str, str] = {}
    for document in index["documents"]:
        sha = document["sha256"]
        ordered = sorted(chunks.get(sha, []), key=lambda c: str(c["chunk_id"]))
        front = " ".join(str(c.get("body") or c.get("text") or "") for c in ordered[:FRONT_PASSAGES])
        fronts[sha] = front
        found: dict[str, list[str]] = defaultdict(list)  # candidate DOI -> how it was found
        for match in _DOI.finditer(front):
            found[_clean_doi(match.group(1))].append("doi_in_text")
        if hint.get(sha, {}).get("doi"):
            found[hint[sha]["doi"]].append("hint_doi")
        _crossref(store, found, {"query.bibliographic": front[:600], "rows": 3}, polite, "crossref_bibliographic")
        candidates[sha] = found
    works: dict[str, tuple[Mapping[str, Any], str]] = {}
    _openalex(store, works, candidates, polite, openalex_key)
    papers, unresolved = _confirm(candidates, works, fronts, hint)
    # Second pass, for papers still unresolved: the opening lines alone (title and first authors) and the hint title.
    for sha in list(unresolved):
        found = candidates[sha]
        _crossref(store, found, {"query.bibliographic": fronts[sha][:220], "rows": 5}, polite, "crossref_opening")
        if hint.get(sha, {}).get("title"):
            _crossref(store, found, {"query.title": hint[sha]["title"], "rows": 5}, polite, "crossref_hint_title")
    _openalex(store, works, {sha: candidates[sha] for sha in unresolved}, polite, openalex_key)
    second, unresolved = _confirm({sha: candidates[sha] for sha in unresolved}, works, fronts, hint)
    papers.update(second)
    payload = {"schema": SCHEMA, "built_at": _now(), "release_gzip_sha256": _sha256_file(directory / "index.json.gz"),
               "support_min": SUPPORT_MIN,
               "sources": ["crossref query.bibliographic and query.title", "openalex works by DOI"],
               "papers": dict(sorted(papers.items())), "unresolved": dict(sorted(unresolved.items()))}
    temporary = directory / (PAPERS_FILE + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    temporary.replace(directory / PAPERS_FILE)
    log(f"{len(papers)} of {len(candidates)} papers resolved; {len(unresolved)} unresolved -> {directory / PAPERS_FILE}")
    return {"resolved": len(papers), "unresolved": unresolved, "path": str(directory / PAPERS_FILE)}


def _crossref(store: "_Cache", found: dict[str, list[str]], query: Mapping[str, Any], polite: Mapping[str, str],
              via: str) -> None:
    payload = store.get_json(CROSSREF_WORKS, {**query, "select": "DOI,title,score", **polite},
                             source="crossref", interval=0.25)
    for item in ((payload.get("data") or {}).get("message") or {}).get("items") or []:
        if item.get("DOI"):
            found[_clean_doi(item["DOI"])].append(via)


def _openalex(store: "_Cache", works: dict[str, tuple[Mapping[str, Any], str]],
              candidates: Mapping[str, Mapping[str, list[str]]], polite: Mapping[str, str],
              openalex_key: str | None) -> None:
    """Look up every candidate DOI not yet looked up, 40 per request (one OpenAlex credit each)."""
    wanted = sorted({doi for found in candidates.values() for doi in found if _SAFE_DOI.match(doi)} - set(works))
    for start in range(0, len(wanted), 40):
        batch = wanted[start:start + 40]
        params = {"filter": "doi:" + "|".join(batch), "per-page": 50, "select": _SELECT, **polite}
        if openalex_key:
            params["api_key"] = openalex_key
        payload = store.get_json(OPENALEX_WORKS, params, source="openalex", interval=0.12, secret_params=("api_key",))
        for work in (payload.get("data") or {}).get("results") or []:
            doi = _clean_doi(str(work.get("doi") or "").replace("https://doi.org/", ""))
            if doi:
                works[doi] = (work, payload["retrieved_at"])


def _confirm(candidates: Mapping[str, Mapping[str, list[str]]], works: Mapping[str, tuple[Mapping[str, Any], str]],
             fronts: Mapping[str, str], hint: Mapping[str, Mapping[str, str]]) -> tuple[dict[str, Any], dict[str, str]]:
    """The best candidate of each paper whose title its own text (or its hint title) confirms; the rest, with why."""
    papers: dict[str, Any] = {}
    unresolved: dict[str, str] = {}
    for sha, found in candidates.items():
        title_hint = hint.get(sha, {}).get("title", "")
        scored = []
        for doi, via in found.items():
            if doi not in works:
                continue
            work, retrieved_at = works[doi]
            support = title_support(str(work.get("title") or ""), fronts[sha], title_hint)
            scored.append((support, len(via), doi, via, work, retrieved_at))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        if scored and scored[0][0] >= SUPPORT_MIN:
            support, _n, doi, via, work, retrieved_at = scored[0]
            papers[sha] = _record(work, retrieved_at, {"via": sorted(set(via)), "title_support": round(support, 3)})
        else:
            best = f"best candidate title support {scored[0][0]:.2f}" if scored else "no candidate DOI found in OpenAlex"
            unresolved[sha] = f"no candidate confirmed by the paper's own text ({best})"
    return papers, unresolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def openalex_key_from_file(path: Path | None = None) -> str | None:
    """The OpenAlex key in ~/.config/openalex/api_key, if any (optional: OpenAlex answers without one)."""
    path = Path(path or os.path.expanduser("~/.config/openalex/api_key"))
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None
