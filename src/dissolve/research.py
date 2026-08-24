"""Bounded scholarly, patent, and portable literature-corpus capabilities."""

from __future__ import annotations

import ast
import copy
import gzip
import hashlib
import html
import importlib
import io
import json
import math
import os
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import duckdb

from .contracts import tool_error, tool_success

_INDEX_SCHEMA = "dissolve.literature-index.v1"
_PARSED_DOCUMENT_SCHEMA = "dissolve.parsed-document.v1"
_CANONICAL_DOCUMENT_SCHEMA = "dissolve.canonical-document.v1"
_HTTP_LIMIT = 25 * 1024 * 1024
_MAX_DOCUMENTS = 40
_MAX_CHUNKS = 12_000
_USER_AGENT = "DISSOLVE-v11/0.4 literature client"
_SUPPORTED_SCHOLAR = {"arxiv", "google_scholar", "web_of_science"}
_SUPPORTED_PATENTS = {"google_patents", "patentsview"}
_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".html", ".htm"}
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "in", "is", "it", "of", "on", "or", "that", "the", "their",
    "this", "to", "was", "were", "what", "which", "with",
}
_HYBRID_DENSE_WEIGHT = 0.55
_HYBRID_SPARSE_WEIGHT = 0.40
_MINILM_DIM = 384
_REFUSE_RULE_SPARSE_GATED = "sparse_gated"


class ResearchNetworkError(RuntimeError):
    """Network failure whose message never contains a credential-bearing URL."""


class LiteratureContractError(ValueError):
    """Typed offline literature-contract failure."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any, limit: int = 4_000) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value]
        value = parsed
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _tokens(value: str) -> list[str]:
    return [
        token for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 1 and token not in _STOPWORDS
    ]


def _table(headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> str:
    if not rows:
        return ""
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
        *("| " + " | ".join(str(value) for value in row) + " |" for row in rows),
    ])


def _request_bytes(
    endpoint: str,
    *,
    source: str,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: int = 30,
) -> bytes:
    query = urlencode(params or {}, doseq=True)
    url = endpoint + (("&" if "?" in endpoint else "?") + query if query else "")
    request = Request(url, headers={"User-Agent": _USER_AGENT, **(headers or {})})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - explicit user search/download
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > _HTTP_LIMIT:
                raise ResearchNetworkError(f"{source} response exceeded the 25 MB limit")
            payload = response.read(_HTTP_LIMIT + 1)
    except HTTPError as error:
        raise ResearchNetworkError(f"{source} request failed with HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ResearchNetworkError(f"{source} request failed") from error
    if len(payload) > _HTTP_LIMIT:
        raise ResearchNetworkError(f"{source} response exceeded the 25 MB limit")
    return payload


def _request_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        value = json.loads(_request_bytes(*args, **kwargs).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ResearchNetworkError(f"{kwargs.get('source', 'remote')} returned invalid JSON") from error
    if not isinstance(value, dict):
        raise ResearchNetworkError(f"{kwargs.get('source', 'remote')} returned an invalid payload")
    return value


def _citation_result(
    *, title: Any, source: str, url: Any = None, authors: Any = None,
    year: Any = None, doi: Any = None, abstract: Any = None,
    pdf_url: Any = None, **extra: Any,
) -> dict[str, Any]:
    if isinstance(authors, str):
        authors = [part.strip() for part in re.split(r",| and ", authors) if part.strip()]
    authors = list(authors or [])[:12]
    try:
        parsed_year = int(str(year)[:4]) if year not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        parsed_year = None
    return {
        "title": _clean(title, 400) or "Untitled",
        "source": source,
        "authors": [_clean(item, 120) for item in authors],
        "year": parsed_year,
        "doi": _clean(doi, 200) or None,
        "url": _clean(url, 1_000) or None,
        "pdf_url": _clean(pdf_url, 1_000) or None,
        "abstract": _clean(abstract, 1_200) or None,
        **{key: value for key, value in extra.items() if value not in (None, "")},
    }


class _ArxivHTMLParser(HTMLParser):
    """Parse arXiv's structured search result cards without scraping prose."""

    def __init__(self) -> None:
        super().__init__(); self.rows: list[dict[str, Any]] = []
        self.row: Optional[dict[str, Any]] = None; self.field: Optional[str] = None
        self.abstract_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs); classes = set((attributes.get("class") or "").split())
        if tag == "li" and "arxiv-result" in classes:
            self.row = {key: [] for key in ("title", "authors", "abstract", "submitted")}; return
        if self.row is None: return
        if self.abstract_depth: self.abstract_depth += 1
        if tag == "span" and "abstract-full" in classes:
            self.field, self.abstract_depth = "abstract", 1
        elif tag == "p":
            self.field = ("title" if {"title", "is-5"} <= classes else
                          "authors" if "authors" in classes else
                          "submitted" if "is-size-7" in classes else None)
        elif tag == "a" and attributes.get("href"):
            href = str(attributes["href"])
            if "/abs/" in href and not self.row.get("url"): self.row["url"] = href
            if "/pdf/" in href and not self.row.get("pdf_url"): self.row["pdf_url"] = href

    def handle_endtag(self, tag: str) -> None:
        if self.row is None: return
        if self.abstract_depth:
            self.abstract_depth -= 1
            if not self.abstract_depth: self.field = None
        elif tag == "p": self.field = None
        if tag == "li":
            text = {key: _clean(" ".join(self.row[key])) for key in ("title", "abstract", "submitted")}
            authors = [_clean(value, 120) for value in self.row["authors"]
                       if re.search(r"[A-Za-z]", value) and "authors:" not in value.casefold()]
            year = re.search(r"\b(?:19|20)\d{2}\b", text["submitted"])
            self.rows.append(_citation_result(title=text["title"], source="arXiv", authors=authors,
                year=year.group() if year else None, url=self.row.get("url"),
                pdf_url=self.row.get("pdf_url"), abstract=text["abstract"])); self.row = None

    def handle_data(self, data: str) -> None:
        if self.row is not None and self.field and _clean(data): self.row[self.field].append(data)


def _search_arxiv_html(query: str, limit: int) -> list[dict[str, Any]]:
    parser = _ArxivHTMLParser()
    payload = _request_bytes("https://arxiv.org/search/", source="arXiv search",
        params={"query": query, "searchtype": "all", "abstracts": "show", "size": 25, "order": ""})
    parser.feed(payload.decode("utf-8", errors="replace"))
    return parser.rows[:limit]


def _search_arxiv(query: str, limit: int) -> list[dict[str, Any]]:
    terms = _tokens(query)[:10]
    search = " AND ".join(f"all:{term}" for term in terms) or f'all:"{query.strip()}"'
    try:
        payload = _request_bytes("https://export.arxiv.org/api/query", source="arXiv",
            params={"search_query": search, "start": 0, "max_results": limit, "sortBy": "relevance"})
        root = ElementTree.fromstring(payload)
    except (ResearchNetworkError, ElementTree.ParseError):
        return _search_arxiv_html(query, limit)
    atom = "{http://www.w3.org/2005/Atom}"
    arxiv = "{http://arxiv.org/schemas/atom}"
    rows: list[dict[str, Any]] = []
    for entry in root.findall(f"{atom}entry"):
        url = (entry.findtext(f"{atom}id") or "").replace("http://", "https://")
        links = {item.attrib.get("type"): item.attrib.get("href") for item in entry.findall(f"{atom}link")}
        rows.append(_citation_result(
            title=entry.findtext(f"{atom}title"), source="arXiv", url=url,
            authors=[node.findtext(f"{atom}name") for node in entry.findall(f"{atom}author")],
            year=entry.findtext(f"{atom}published"), doi=entry.findtext(f"{arxiv}doi"),
            abstract=entry.findtext(f"{atom}summary"), pdf_url=links.get("application/pdf"),
            journal=entry.findtext(f"{arxiv}journal_ref"),
        ))
    return rows


def _search_google_scholar(query: str, limit: int, year_low: int | None, year_high: int | None) -> list[dict[str, Any]]:
    key = os.getenv("SERPAPI_KEY")
    if not key:
        raise KeyError("SERPAPI_KEY")
    data = _request_json(
        "https://serpapi.com/search", source="Google Scholar",
        params={"engine": "google_scholar", "api_key": key, "q": query, "num": limit,
                "as_ylo": year_low or "", "as_yhi": year_high or ""},
    )
    rows = []
    for item in list(data.get("organic_results") or [])[:limit]:
        publication = item.get("publication_info") or {}
        raw_authors = publication.get("authors") or []
        authors = [entry.get("name") for entry in raw_authors if isinstance(entry, dict)]
        resources = item.get("resources") or []
        pdf = next((entry.get("link") for entry in resources if str(entry.get("file_format")).casefold() == "pdf"), None)
        summary = str(publication.get("summary") or "")
        year_match = re.search(r"\b(?:19|20)\d{2}\b", summary)
        rows.append(_citation_result(
            title=item.get("title"), source="Google Scholar", url=item.get("link"),
            authors=authors, year=year_match.group() if year_match else None,
            abstract=item.get("snippet"), pdf_url=pdf,
            cited_by=((item.get("inline_links") or {}).get("cited_by") or {}).get("total"),
        ))
    return rows


def _search_wos(query: str, limit: int, year_low: int | None, year_high: int | None) -> list[dict[str, Any]]:
    key = os.getenv("WOS_STARTER_API_KEY")
    if not key:
        raise KeyError("WOS_STARTER_API_KEY")
    wos_query = query if re.search(r"\b(?:TS|TI|AU|SO|PY)=", query, re.I) else f"TS=({query})"
    if (year_low or year_high) and "PY=" not in wos_query.upper():
        wos_query += f" AND PY=({year_low or 1900}-{year_high or 2100})"
    data = _request_json(
        "https://api.clarivate.com/apis/wos-starter/v1/documents", source="Web of Science",
        params={"db": "WOS", "q": wos_query, "limit": limit, "page": 1, "sortField": "PY+D"},
        headers={"X-ApiKey": key, "Accept": "application/json"},
    )
    rows = []
    for item in list(data.get("hits") or [])[:limit]:
        source = item.get("source") or {}
        identifiers = item.get("identifiers") or {}
        authors = [entry.get("displayName") for entry in ((item.get("names") or {}).get("authors") or [])]
        uid = item.get("uid")
        rows.append(_citation_result(
            title=item.get("title"), source="Web of Science",
            url=f"https://www.webofscience.com/wos/woscc/full-record/{uid}" if uid else None,
            authors=authors, year=source.get("publishYear"), doi=identifiers.get("doi"),
            journal=source.get("sourceTitle"),
            cited_by=((item.get("citations") or [{}])[0]).get("count", 0),
        ))
    return rows


def search_scholarly_literature(
    query: str,
    sources: Optional[list[str]] = None,
    max_results: int = 6,
    year_low: Optional[int] = None,
    year_high: Optional[int] = None,
    save_to_corpus: bool = False,
    knowledgebase: str = "user-library",
    max_save: int = 2,
) -> str:
    """Search arXiv, Google Scholar, and/or Web of Science with bounded metadata."""
    tool = "search_scholarly_literature"
    if not str(query or "").strip():
        return tool_error(tool, "Search query cannot be empty.", error_code="empty_query")
    requested = [item.casefold() for item in (_items(sources) or ["arxiv"])]
    if "all" in requested:
        requested = ["arxiv", "google_scholar", "web_of_science"]
    unknown = sorted(set(requested) - _SUPPORTED_SCHOLAR)
    if unknown:
        return tool_error(tool, f"Unknown scholarly source(s): {', '.join(unknown)}.", error_code="unknown_source")
    limit = max(1, min(int(max_results), 10))
    results: list[dict[str, Any]] = []
    completed: list[str] = []
    unavailable: list[dict[str, str]] = []
    clients = {
        "arxiv": lambda: _search_arxiv(query, limit),
        "google_scholar": lambda: _search_google_scholar(query, limit, year_low, year_high),
        "web_of_science": lambda: _search_wos(query, limit, year_low, year_high),
    }
    for source in dict.fromkeys(requested):
        try:
            rows = clients[source]()
            completed.append(source)
            results.extend(rows)
        except KeyError as error:
            unavailable.append({"source": source, "reason": "missing_credentials", "required_env": str(error).strip("'")})
        except (ResearchNetworkError, ElementTree.ParseError) as error:
            unavailable.append({"source": source, "reason": "request_failed", "message": str(error)})
    deduplicated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in results:
        key = str(row.get("doi") or row.get("url") or row.get("title")).casefold()
        if key and key not in seen:
            seen.add(key)
            deduplicated.append(row)
    if year_low is not None:
        deduplicated = [row for row in deduplicated if row.get("year") is None or row["year"] >= year_low]
    if year_high is not None:
        deduplicated = [row for row in deduplicated if row.get("year") is None or row["year"] <= year_high]
    deduplicated = deduplicated[:limit]
    for index, row in enumerate(deduplicated, 1):
        row["citation_id"] = f"C{index}"
    save_result = None
    if save_to_corpus:
        urls = [row["pdf_url"] for row in deduplicated if row.get("pdf_url")][:max(1, min(max_save, 5))]
        try:
            metadata = {row["pdf_url"]: row for row in deduplicated if row.get("pdf_url")}
            save_result = _ingest_inputs([], urls, knowledgebase, False, len(urls), False, metadata) if urls else {
                "documents_added": 0, "chunks_added": 0, "failures": ["No downloadable PDF was returned."],
            }
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            save_result = {"documents_added": 0, "chunks_added": 0, "failures": [str(error)[:200]]}
    if not completed and not deduplicated:
        return tool_error(
            tool, "No requested scholarly source was available.", error_code="research_sources_unavailable",
            sources_requested=requested, sources_unavailable=unavailable,
        )
    return tool_success(
        tool,
        display=_table(("Citation", "Year", "Source", "Title"), [
            (row["citation_id"], row.get("year") or "—", row["source"], row["title"])
            for row in deduplicated
        ]),
        analysis_type="scholarly_search", query=query, sources_requested=requested,
        sources_completed=completed, sources_unavailable=unavailable,
        result_count=len(deduplicated), results=deduplicated,
        evidence_scope="provider_metadata_and_available_abstracts",
        fetched_at=_now(), save_requested=bool(save_to_corpus), save_result=save_result,
        warnings=[
            "Search metadata and abstracts do not establish full experimental conditions; inspect the cited full text before lab use.",
            "Results are not attached automatically to thermodynamic calculations or plots.",
        ],
    )


def _search_google_patents(
    query: str, limit: int, after: str | None, before: str | None, assignee: str | None,
) -> list[dict[str, Any]]:
    key = os.getenv("SERPAPI_KEY")
    if not key:
        raise KeyError("SERPAPI_KEY")
    data = _request_json(
        "https://serpapi.com/search", source="Google Patents",
        params={"engine": "google_patents", "api_key": key, "q": query, "num": limit,
                "after": after or "", "before": before or "", "assignee": assignee or ""},
    )
    rows = []
    for item in list(data.get("organic_results") or [])[:limit]:
        patent_id = _clean(item.get("patent_id"), 100)
        rows.append(_citation_result(
            title=item.get("title"), source="Google Patents", url=item.get("link"),
            authors=item.get("inventor") or item.get("inventors"), year=item.get("publication_date") or item.get("filing_date"),
            abstract=item.get("snippet"), pdf_url=item.get("pdf"), patent_id=patent_id,
            assignee=_clean(item.get("assignee"), 200) or None,
        ))
    return rows


def _search_patentsview(
    query: str, limit: int, after: str | None, before: str | None, assignee: str | None,
    patent_number: str | None,
) -> list[dict[str, Any]]:
    key = os.getenv("PATENTSVIEW_API_KEY")
    if not key:
        raise KeyError("PATENTSVIEW_API_KEY")
    endpoint = "https://search.patentsview.org/api/v1/patent/"
    params: dict[str, Any] = {}
    if patent_number:
        endpoint += re.sub(r"^US", "", re.sub(r"[^A-Za-z0-9]", "", patent_number), flags=re.I) + "/"
    else:
        conditions: list[dict[str, Any]] = [{"_or": [
            {"_text_any": {"patent_title": query}}, {"_text_any": {"patent_abstract": query}},
        ]}]
        if after:
            conditions.append({"_gte": {"patent_date": after}})
        if before:
            conditions.append({"_lte": {"patent_date": before}})
        if assignee:
            conditions.append({"_contains": {"assignees.assignee_organization": assignee}})
        query_object = conditions[0] if len(conditions) == 1 else {"_and": conditions}
        fields = ["patent_id", "patent_title", "patent_date", "patent_abstract", "assignees.assignee_organization", "inventors.inventor_name_first", "inventors.inventor_name_last"]
        params = {"q": json.dumps(query_object), "f": json.dumps(fields), "o": json.dumps({"size": limit})}
    data = _request_json(endpoint, source="PatentsView", params=params, headers={"X-Api-Key": key, "Accept": "application/json"})
    hits = data.get("patents") if isinstance(data.get("patents"), list) else [data]
    rows = []
    for item in hits[:limit]:
        patent_id = _clean(item.get("patent_id"), 100)
        inventors = [
            " ".join(filter(None, (entry.get("inventor_name_first"), entry.get("inventor_name_last"))))
            for entry in (item.get("inventors") or [])
        ]
        assignees = item.get("assignees") or []
        rows.append(_citation_result(
            title=item.get("patent_title"), source="PatentsView",
            url=f"https://patents.google.com/patent/US{patent_id}", authors=inventors,
            year=item.get("patent_date"), abstract=item.get("patent_abstract"), patent_id=patent_id,
            assignee=_clean((assignees[0] if assignees else {}).get("assignee_organization"), 200) or None,
            pdf_url=f"https://patents.google.com/patent/US{patent_id}/download" if patent_id else None,
        ))
    return rows


def search_patent_literature(
    query: str = "",
    source: Literal["google_patents", "patentsview"] = "google_patents",
    patent_number: Optional[str] = None,
    max_results: int = 6,
    after: Optional[str] = None,
    before: Optional[str] = None,
    assignee: Optional[str] = None,
    save_to_corpus: bool = False,
    knowledgebase: str = "user-library",
    max_save: int = 2,
) -> str:
    """Search patents or retrieve a known patent through one explicit source."""
    tool = "search_patent_literature"
    source = str(source or "").casefold()
    if source not in _SUPPORTED_PATENTS:
        return tool_error(tool, f"Unknown patent source: {source}.", error_code="unknown_source")
    search_query = str(patent_number or query or "").strip()
    if not search_query:
        return tool_error(tool, "Patent query or patent_number is required.", error_code="empty_query")
    limit = max(1, min(int(max_results), 10))
    try:
        if source == "google_patents":
            rows = _search_google_patents(search_query, limit, after, before, assignee)
        else:
            rows = _search_patentsview(query or search_query, limit, after, before, assignee, patent_number)
    except KeyError as error:
        return tool_error(
            tool, f"{source} requires {str(error).strip(chr(39))}.", error_code="missing_credentials",
            source=source, required_env=str(error).strip("'"),
        )
    except ResearchNetworkError as error:
        return tool_error(tool, str(error), error_code="research_request_failed", source=source)
    for index, row in enumerate(rows, 1):
        row["citation_id"] = f"C{index}"
    save_result = None
    if save_to_corpus:
        urls = [row["pdf_url"] for row in rows if row.get("pdf_url")][:max(1, min(max_save, 5))]
        try:
            metadata = {row["pdf_url"]: row for row in rows if row.get("pdf_url")}
            save_result = _ingest_inputs([], urls, knowledgebase, False, len(urls), False, metadata) if urls else {
                "documents_added": 0, "chunks_added": 0, "failures": ["No downloadable patent PDF was returned."],
            }
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            save_result = {"documents_added": 0, "chunks_added": 0, "failures": [str(error)[:200]]}
    return tool_success(
        tool,
        display=_table(("Citation", "Patent", "Year", "Assignee", "Title"), [
            (row["citation_id"], row.get("patent_id") or "—", row.get("year") or "—", row.get("assignee") or "—", row["title"])
            for row in rows
        ]),
        analysis_type="patent_search", query=search_query, source=source,
        patent_number=patent_number, result_count=len(rows), results=rows,
        evidence_scope="provider_metadata_and_available_abstracts",
        fetched_at=_now(), save_requested=bool(save_to_corpus), save_result=save_result,
        warnings=[
            "Patent metadata and abstracts are not a claim-construction or freedom-to-operate analysis.",
            "Inspect the full patent and legal status before drawing process or legal conclusions.",
        ],
    )


def _research_root() -> Path:
    return Path(os.getenv("DISSOLVE_RESEARCH_HOME", "~/.dissolve/research")).expanduser().resolve()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").casefold()).strip("-.")
    if not slug or slug in {".", ".."}:
        raise ValueError("knowledgebase must contain letters or numbers")
    return slug[:80]


_PARSED_BLOCK_KINDS = {
    "title", "heading", "paragraph", "list_item", "caption", "footnote",
    "formula", "table", "claim", "header", "footer", "other",
}
_DOCLING_BLOCK_KINDS = {
    "title": "title", "section_header": "heading", "heading": "heading",
    "paragraph": "paragraph", "text": "paragraph", "list_item": "list_item",
    "caption": "caption", "footnote": "footnote", "formula": "formula",
    "table": "table", "tableitem": "table",
    "page_header": "header", "header": "header", "page_footer": "footer",
    "footer": "footer", "claim": "claim",
}
_EXPERIMENT_DOCLING_VERSION = "2.121.0"
_EXPERIMENT_PARSE_BACKENDS = {"docling", "pypdf"}
PEAK_RSS_CEILING_BYTES = 3_501_953_024  # C1 two-paper peak. Start-guard basis, not a corpus RSS cap.
PEAK_RSS_HEADROOM_BYTES = 512 * 1024 * 1024
_CANONICAL_STORED_KINDS = {
    "title", "heading", "paragraph", "list_item", "caption", "footnote",
    "formula", "table", "header", "footer", "other",
}
_M3_RULE_NAMES = (
    "white_bullet_to_degree",
    "strip_soft_hyphen",
    "elsevier_split_acute",
    "degree_c_collapse",
)
_SPLIT_ACUTE_RE = re.compile(r"\s+\u00B4\s+([A-Za-z])")
_DEGREE_C_WS_RE = re.compile(r"\u00B0\s+[Cc]")
_CANONICAL_TEMPERATURE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*°\s*C")
_HEADING_FILL_KEY = "_heading_fill"
_CANONICAL_DROP_KEYS = {
    "section_path", _HEADING_FILL_KEY, "text", "char_start", "char_end",
    "nearest_preceding_heading", "nearest_preceding_heading_origin",
    "caption_ref_origin",
}
_GOLD_FACTS_V1_SHA256 = "345b426bd66f995b3b78a10e796afb6df013299dd6769379192b59d0d97dfaab"
_CHUNK_TARGET = 1_400
_CHUNK_OVERLAP = 180
_ATOMIC_CHUNK_KINDS = frozenset({"table", "formula", "caption"})
_C6_STRATEGY_IDS = (
    "S0_production_pypdf",
    "S1_naive_char",
    "S2_block_pack",
    "S3_section_pack",
    "S4_sentence_pack",
    "S5_table_plus_neighbors",
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_TABLE_LABEL_RE = re.compile(r"^\s*Table\s+(\d+)\b", re.IGNORECASE)
_FOOTNOTE_MARKER_RE = re.compile(r"^[*†‡§]+$")
_PARSE_QUALITY_FLAGS = {
    "ocr_used", "rotation_corrected", "reading_order_uncertain",
    "table_grid_incomplete", "encrypted", "truncated", "low_confidence",
    "layout_degraded",
}
_QUANTITY_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_QUANTITY_RANGE_RE = re.compile(
    rf"^\s*({_QUANTITY_NUMBER})\s*(.*?)\s*(?:\bto\b|(?<![eE^])[-–—])\s*"
    rf"({_QUANTITY_NUMBER})\s*(.*?)\s*$",
    re.IGNORECASE,
)
_QUANTITY_SCALAR_RE = re.compile(rf"^\s*({_QUANTITY_NUMBER})\s*(.*?)\s*$")
_QUANTITY_FUNCTION_RE = re.compile(
    r"^\s*(?:χ|chi|flory[- ]huggins\s+chi)\s*=\s*(.+?)\s*$", re.IGNORECASE,
)
_UNIT_CANONICAL = {
    "c": "degC", "°c": "degC", "degc": "degC",
    "k": "K", "kelvin": "K",
    "wt%": "wt_percent", "wt.%": "wt_percent", "%w/w": "wt_percent",
    "mass%": "wt_percent", "vol%": "vol_percent", "%v/v": "vol_percent",
    "mpa^0.5": "MPa^0.5", "mpa^1/2": "MPa^0.5", "mpa½": "MPa^0.5",
    "k/min": "K_per_min", "kmin^-1": "K_per_min",
    "min": "min", "minute": "min", "minutes": "min",
    "h": "h", "hr": "h", "hour": "h", "hours": "h",
    "s": "s", "sec": "s", "second": "s", "seconds": "s",
    "g/l": "g_per_L", "mg/l": "mg_per_L", "kg/m3": "kg_per_m3",
    "pa": "Pa", "kpa": "kPa", "mpa": "MPa", "bar": "bar",
    "mol/l": "mol_per_L", "mol%": "mol_percent",
}


def _enum_text(value: Any) -> str:
    """Normalize enum-like third-party labels without depending on their classes."""
    text = str(getattr(value, "value", value) or "").strip().casefold()
    return text.rsplit(".", 1)[-1].replace("-", "_").replace(" ", "_")


def _object_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _reference_id(value: Any) -> str:
    return str(_object_value(value, "cref", value))


def _bbox_from(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping)):
        if len(value) < 4:
            raise LiteratureContractError(
                "invalid_parser_bbox", "Parser bounding-box sequence must have four coordinates."
            )
        value = {"x0": value[0], "y0": value[1], "x1": value[2], "y1": value[3]}
    names = (("x0", "y0", "x1", "y1"), ("l", "b", "r", "t"), ("left", "top", "right", "bottom"))
    for fields in names:
        coordinates = [_object_value(value, field) for field in fields]
        if all(item is not None for item in coordinates):
            try:
                x0, y0, x1, y1 = (float(item) for item in coordinates)
            except (TypeError, ValueError) as error:
                raise LiteratureContractError(
                    "invalid_parser_bbox", "Parser bounding-box coordinates must be numeric."
                ) from error
            return {
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "coordinate_space": str(_object_value(value, "coordinate_space", "page_points")),
            }
    raise LiteratureContractError(
        "invalid_parser_bbox", "Parser bounding box is missing a complete coordinate set."
    )


def _provenance(item: Any) -> tuple[int | None, dict[str, Any] | None, float]:
    rows = _object_value(item, "prov", []) or _object_value(item, "provenance", []) or []
    row = rows[0] if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)) and rows else None
    page = _object_value(item, "page") or _object_value(item, "page_no")
    bbox = _object_value(item, "bbox")
    confidence = _object_value(item, "confidence", 1.0)
    if row is not None:
        page = page or _object_value(row, "page_no") or _object_value(row, "page")
        bbox = bbox or _object_value(row, "bbox")
        confidence = _object_value(row, "confidence", confidence)
    try:
        page_value = int(page) if page is not None else None
        confidence_value = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError) as error:
        raise LiteratureContractError(
            "invalid_parser_provenance", "Parser page and confidence fields must be numeric."
        ) from error
    return page_value, _bbox_from(bbox), confidence_value


def _docling_table_cells(raw_cells: Any) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for cell in raw_cells or []:
        cells.append({
            "row": _object_value(cell, "start_row_offset_idx", _object_value(cell, "row", 0)),
            "column": _object_value(cell, "start_col_offset_idx", _object_value(cell, "column", 0)),
            "row_end": _object_value(cell, "end_row_offset_idx"),
            "column_end": _object_value(cell, "end_col_offset_idx"),
            "row_span": _object_value(cell, "row_span"),
            "column_span": _object_value(cell, "col_span", _object_value(cell, "column_span")),
            "is_header": bool(
                _object_value(cell, "column_header", False)
                or _object_value(cell, "row_header", False)
                or _object_value(cell, "row_section", False)
            ),
            "text": str(_object_value(cell, "text", "")),
            "block_refs": [],
        })
    return cells


def _table_grid_text(
    cells: Sequence[Mapping[str, Any]], *, table_id: Any, page: Any,
    row_count: Any, column_count: Any,
) -> str:
    """Serialize structured cells so a table item has retrievable text.

    Empty cells stay empty strings, not invented dashes. Offsets into a later
    canonical document can wrap this same span; this helper does not invent them.
    """
    if not cells:
        return ""
    parsed: list[tuple[int, int, str]] = []
    for cell in cells:
        try:
            row = int(cell.get("row") or 0)
            column = int(cell.get("column") or 0)
        except (TypeError, ValueError):
            continue
        parsed.append((row, column, str(cell.get("text") or "")))
    if not parsed:
        return ""
    try:
        n_rows = int(row_count) if row_count not in (None, "") else 0
        n_cols = int(column_count) if column_count not in (None, "") else 0
    except (TypeError, ValueError):
        n_rows, n_cols = 0, 0
    n_rows = n_rows or (max(row for row, _, _ in parsed) + 1)
    n_cols = n_cols or (max(column for _, column, _ in parsed) + 1)
    grid = [[""] * n_cols for _ in range(n_rows)]
    for row, column, text in parsed:
        if 0 <= row < n_rows and 0 <= column < n_cols:
            grid[row][column] = text
    lines = [f"[TABLE {table_id} page={page}]"]
    lines.extend("| " + " | ".join(row) + " |" for row in grid)
    lines.append("[/TABLE]")
    return "\n".join(lines)


def _docling_bridge(document: Any, *, version: str) -> dict[str, Any]:
    """Convert a DoclingDocument into the small backend-neutral bridge."""
    iterate = getattr(document, "iterate_items", None)
    if not callable(iterate):
        raise LiteratureContractError(
            "docling_contract_mismatch", "Docling result has no iterate_items() document interface."
        )
    items: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    for order, pair in enumerate(iterate()):
        item, level = pair if isinstance(pair, tuple) and len(pair) == 2 else (pair, 0)
        label = _enum_text(_object_value(item, "label", item.__class__.__name__))
        page, bbox, confidence = _provenance(item)
        data = _object_value(item, "data")
        raw_cells = _object_value(data, "table_cells", []) if data is not None else []
        if raw_cells or "table" in label:
            captions = _object_value(item, "captions", []) or []
            table_id = _object_value(item, "self_ref") or _object_value(item, "id")
            row_count = _object_value(data, "num_rows")
            column_count = _object_value(data, "num_cols")
            cells = _docling_table_cells(raw_cells)
            tables.append({
                "id": table_id,
                "page": page,
                "caption_id": _reference_id(captions[0]) if captions else None,
                "row_count": row_count,
                "column_count": column_count,
                "cells": cells,
            })
            # Tables must enter the item list. The previous `continue` stored
            # structure only in tables[] and dropped the item, so a later
            # text/chunk path could not see thermodynamic cells.
            text = _table_grid_text(
                cells, table_id=table_id, page=page,
                row_count=row_count, column_count=column_count,
            )
            if str(text).strip():
                items.append({
                    "id": table_id, "label": "table", "level": int(level or 0),
                    "order": order, "page": page, "bbox": bbox, "text": text,
                    "confidence": confidence,
                    "caption_ref": (
                        _reference_id(captions[0]) if captions
                        else _object_value(item, "caption_ref")
                    ),
                    "footnote_refs": [
                        _reference_id(value)
                        for value in (_object_value(item, "footnotes", []) or [])
                    ],
                })
            continue
        text = _object_value(item, "text") or _object_value(item, "orig") or ""
        if not str(text).strip():
            continue
        items.append({
            "id": _object_value(item, "self_ref") or _object_value(item, "id"),
            "label": label, "level": int(level or 0), "order": order,
            "page": page, "bbox": bbox, "text": str(text), "confidence": confidence,
            "caption_ref": _object_value(item, "caption_ref"),
            "footnote_refs": [_reference_id(value) for value in (_object_value(item, "footnotes", []) or [])],
        })
    return {
        "backend": "docling", "version": version, "items": items, "tables": tables,
        "quality_flags": [], "fallback_reason": None,
    }


def _run_docling(path: Path) -> dict[str, Any]:
    try:
        module = importlib.import_module("docling.document_converter")
        converter_type = module.DocumentConverter
        version = getattr(importlib.import_module("docling"), "__version__", "unknown")
    except (ImportError, AttributeError) as error:
        raise LiteratureContractError(
            "parser_backend_unavailable",
            "Docling is unavailable; install the pinned research extra before parsing documents.",
            backend="docling",
        ) from error
    try:
        result = converter_type().convert(path)
        bridge = _docling_bridge(result.document, version=str(version))
        if _enum_text(getattr(result, "status", "success")) not in {"success", "partial_success"}:
            raise LiteratureContractError(
                "parser_backend_failed", "Docling returned a non-success conversion status.",
                backend="docling", status=str(getattr(result, "status", "unknown")),
            )
        if _enum_text(getattr(result, "status", "success")) == "partial_success":
            bridge["quality_flags"] = ["truncated"]
        return bridge
    except LiteratureContractError:
        raise
    except Exception as error:  # backend failures vary by document and Docling release
        raise LiteratureContractError(
            "parser_backend_failed", "Docling could not parse the acquired artifact.",
            backend="docling", error_type=type(error).__name__,
        ) from error


def _deepdoc_bridge(result: Any, *, version: str, fallback_reason: str) -> dict[str, Any]:
    try:
        boxes, raw_tables = result
    except (TypeError, ValueError) as error:
        raise LiteratureContractError(
            "deepdoc_contract_mismatch", "DeepDoc did not return its boxes-and-tables result."
        ) from error
    items = []
    for order, box in enumerate(boxes or []):
        text = _object_value(box, "text") or _object_value(box, "content") or ""
        if not str(text).strip():
            continue
        layout = _enum_text(_object_value(box, "layout_type", _object_value(box, "type", "text")))
        items.append({
            "id": _object_value(box, "id"), "label": layout, "order": order,
            "page": _object_value(box, "page_number", _object_value(box, "page")),
            "bbox": _object_value(box, "bbox", _object_value(box, "position")),
            "section_path": list(_object_value(box, "section_path", []) or []),
            "text": str(text), "confidence": _object_value(box, "confidence", 1.0),
        })
    tables = []
    for raw in raw_tables or []:
        if not isinstance(raw, Mapping):
            continue
        tables.append(dict(raw))
    return {
        "backend": "deepdoc", "version": version, "items": items, "tables": tables,
        "quality_flags": ["ocr_used"], "fallback_reason": fallback_reason,
    }


def _run_deepdoc(path: Path, *, fallback_reason: str) -> dict[str, Any]:
    try:
        parser_type = importlib.import_module("deepdoc.parser").PdfParser
        version = getattr(importlib.import_module("deepdoc"), "__version__", "unknown")
    except (ImportError, AttributeError) as error:
        raise LiteratureContractError(
            "parser_backend_unavailable",
            "DeepDoc fallback is unavailable; install a compatible RAGFlow DeepDoc environment.",
            backend="deepdoc", fallback_reason=fallback_reason,
        ) from error
    try:
        return _deepdoc_bridge(
            parser_type()(str(path), auto_rotate_tables=False),
            version=str(version), fallback_reason=fallback_reason,
        )
    except LiteratureContractError:
        raise
    except Exception as error:  # optional fallback errors vary by RAGFlow release
        raise LiteratureContractError(
            "parser_backend_failed", "DeepDoc could not parse the acquired artifact.",
            backend="deepdoc", error_type=type(error).__name__,
            fallback_reason=fallback_reason,
        ) from error


def _source_artifact(acquisition: Mapping[str, Any]) -> Mapping[str, Any]:
    document = acquisition.get("document") or {}
    source_sha = document.get("content_sha256")
    artifacts = list(acquisition.get("artifacts") or [])
    matches = [item for item in artifacts if item.get("sha256") == source_sha]
    if len(matches) != 1:
        raise LiteratureContractError(
            "source_artifact_ambiguous",
            "AcquireEnvelope must contain exactly one artifact matching document.content_sha256.",
            source_sha256=source_sha, match_count=len(matches),
        )
    return matches[0]


def _normalize_parser_bridge(
    acquisition: Mapping[str, Any], bridge: Mapping[str, Any], *, parsed_at: str | None,
    stamp_heading_fill: bool = False,
) -> dict[str, Any]:
    document = acquisition.get("document") or {}
    library_id, document_id = acquisition.get("library_id"), document.get("document_id")
    if acquisition.get("schema") != "dissolve.acquire.v1" or not library_id or not document_id:
        raise LiteratureContractError(
            "invalid_acquire_envelope", "Structure parsing requires a scoped AcquireEnvelope."
        )
    if document.get("library_id") != library_id:
        raise LiteratureContractError(
            "library_scope_mismatch", "AcquireEnvelope and document library_id values must match."
        )
    source = _source_artifact(acquisition)
    backend = str(bridge.get("backend") or "").casefold()
    if backend not in {"docling", "deepdoc", "pypdf", "jats", "local_text"}:
        raise LiteratureContractError(
            "unknown_parser_backend",
            "Parser bridge backend must be docling, deepdoc, pypdf, jats, or local_text.",
            backend=backend,
        )
    fallback_reason = bridge.get("fallback_reason")
    if backend == "docling" and fallback_reason:
        raise LiteratureContractError(
            "parser_identity_lie",
            "A Docling parse cannot carry a fallback_reason; that is a fallback labeled as Docling.",
            fallback_reason=str(fallback_reason),
        )
    if backend in {"deepdoc", "pypdf"} and not fallback_reason:
        raise LiteratureContractError(
            "undisclosed_parser_fallback",
            "Fallback parser output requires a non-empty fallback_reason."
        )
    quality_flags = sorted(set(str(item) for item in bridge.get("quality_flags") or []))
    unknown_flags = set(quality_flags) - _PARSE_QUALITY_FLAGS
    if unknown_flags:
        raise LiteratureContractError(
            "unknown_parse_quality_flag", "Parser emitted unsupported quality flags.",
            quality_flags=sorted(unknown_flags),
        )

    current_sections: list[str] = []
    blocks: list[dict[str, Any]] = []
    block_ids: set[str] = set()
    items = sorted(
        list(bridge.get("items") or []),
        key=lambda item: (int(_object_value(item, "order", len(blocks))), str(_object_value(item, "id", ""))),
    )
    for raw in items:
        label = _enum_text(_object_value(raw, "label", "other"))
        kind = _DOCLING_BLOCK_KINDS.get(label, label if label in _PARSED_BLOCK_KINDS else "other")
        text = str(_object_value(raw, "text", ""))
        if not text.strip():
            continue
        if document.get("document_kind") == "patent" and (
            kind == "claim" or any(part.casefold().startswith("claim") for part in _object_value(raw, "section_path", []) or [])
        ):
            kind = "claim"
        level = max(1, int(_object_value(raw, "level", 1) or 1))
        supplied_path = list(_object_value(raw, "section_path", []) or [])
        if kind == "heading":
            current_sections = current_sections[:level - 1] + [text.strip()]
        section_path = supplied_path or list(current_sections)
        block_id = str(_object_value(raw, "id") or f"{document_id}-B{len(blocks) + 1:05d}")
        if block_id in block_ids:
            raise LiteratureContractError(
                "duplicate_parser_block_id", "Parser emitted a duplicate block identifier.",
                block_id=block_id,
            )
        page, bbox, confidence = _provenance(raw)
        block_ids.add(block_id)
        block = {
            "block_id": block_id, "kind": kind, "reading_order": len(blocks),
            "page": page, "bbox": bbox, "section_path": section_path,
            "text": text, "confidence": confidence,
            "caption_ref": _object_value(raw, "caption_ref"),
            "footnote_refs": list(_object_value(raw, "footnote_refs", []) or []),
        }
        if stamp_heading_fill:
            # Recorded at fill time. Filled section_path alone cannot recover this.
            block[_HEADING_FILL_KEY] = (
                "parser_supplied" if supplied_path else "inherited_from_stack"
            )
        blocks.append(block)

    tables: list[dict[str, Any]] = []
    table_ids: set[str] = set()
    for index, raw in enumerate(bridge.get("tables") or [], 1):
        table_id = str(_object_value(raw, "id") or f"{document_id}-T{index:05d}")
        if table_id in table_ids:
            raise LiteratureContractError(
                "duplicate_parser_table_id", "Parser emitted a duplicate table identifier.",
                table_id=table_id,
            )
        cells = []
        for raw_cell in _object_value(raw, "cells", []) or []:
            try:
                row = int(_object_value(raw_cell, "row", 0))
                column = int(_object_value(raw_cell, "column", 0))
                row_end = _object_value(raw_cell, "row_end")
                column_end = _object_value(raw_cell, "column_end")
                row_span = int(_object_value(raw_cell, "row_span") or (
                    int(row_end) - row if row_end is not None else 1
                ))
                column_span = int(_object_value(raw_cell, "column_span") or (
                    int(column_end) - column if column_end is not None else 1
                ))
            except (TypeError, ValueError) as error:
                raise LiteratureContractError(
                    "invalid_table_cell_coordinates", "Table-cell coordinates and spans must be integers."
                ) from error
            if min(row, column) < 0 or min(row_span, column_span) < 1:
                raise LiteratureContractError(
                    "invalid_table_cell_coordinates", "Table-cell coordinates and spans are out of bounds."
                )
            refs = list(_object_value(raw_cell, "block_refs", []) or [])
            if not set(refs) <= block_ids:
                raise LiteratureContractError(
                    "unknown_table_block_reference", "Table cell references an unknown parsed block.",
                    block_refs=refs,
                )
            cells.append({
                "row": row, "column": column, "row_span": row_span,
                "column_span": column_span,
                "is_header": bool(_object_value(raw_cell, "is_header", False)),
                "text": str(_object_value(raw_cell, "text", "")), "block_refs": refs,
            })
        if not cells:
            raise LiteratureContractError(
                "empty_parsed_table", "A parsed table must contain at least one cell.", table_id=table_id,
            )
        row_count = int(_object_value(raw, "row_count") or max(cell["row"] + cell["row_span"] for cell in cells))
        column_count = int(_object_value(raw, "column_count") or max(cell["column"] + cell["column_span"] for cell in cells))
        occupied = {
            (row, column)
            for cell in cells
            for row in range(cell["row"], cell["row"] + cell["row_span"])
            for column in range(cell["column"], cell["column"] + cell["column_span"])
        }
        grid_complete = len(occupied) == row_count * column_count and all(
            row < row_count and column < column_count for row, column in occupied
        )
        if not grid_complete and "table_grid_incomplete" not in quality_flags:
            quality_flags.append("table_grid_incomplete")
            quality_flags.sort()
        caption_id = _object_value(raw, "caption_id")
        if caption_id is not None and caption_id not in block_ids:
            raise LiteratureContractError(
                "unknown_table_caption_reference", "Parsed table references an unknown caption block.",
                caption_block_id=caption_id,
            )
        table_ids.add(table_id)
        tables.append({
            "table_id": table_id, "page": _object_value(raw, "page"),
            "caption_block_id": caption_id, "row_count": row_count,
            "column_count": column_count, "cells": cells, "grid_complete": grid_complete,
        })

    attachments = [
        dict(item) for item in acquisition.get("artifacts") or []
        if item.get("artifact_id") != source.get("artifact_id")
    ]
    return {
        "schema": _PARSED_DOCUMENT_SCHEMA, "library_id": library_id,
        "document_id": document_id, "source_sha256": source["sha256"],
        "parser_backend": backend, "parser_version": str(bridge.get("version") or "unknown"),
        "fallback_reason": str(fallback_reason) if fallback_reason else None,
        "blocks": blocks, "tables": tables, "attachments": attachments,
        "quality_flags": quality_flags,
        "parsed_at": parsed_at or str(bridge.get("parsed_at") or _now()),
    }


def parse_document_structure(
    acquisition: Mapping[str, Any], *, parser_payload: Mapping[str, Any] | None = None,
    asset_root: str | Path | None = None, parsed_at: str | None = None,
) -> dict[str, Any]:
    """Parse either a paper or patent through one backend-neutral structure path.

    ``parser_payload`` is the checksummed/offline fixture seam. Production parsing
    invokes Docling and raises on failure; DeepDoc and pypdf are not a fallback.
    The one-paper experiment calls ``parse_experiment_document(backend=...)``.
    """
    if parser_payload is not None:
        return _normalize_parser_bridge(acquisition, parser_payload, parsed_at=parsed_at)
    source = _source_artifact(acquisition)
    path = Path(str(source.get("packed_path") or ""))
    if not path.is_absolute():
        path = Path(asset_root or ".").resolve() / path
    if not path.is_file():
        raise LiteratureContractError(
            "parser_source_missing", "The acquired content artifact is not present on disk.",
            artifact_id=source.get("artifact_id"), packed_path=str(path),
        )
    bridge = _run_docling(path)
    return _normalize_parser_bridge(acquisition, bridge, parsed_at=parsed_at)


def _experiment_source_path(
    acquisition: Mapping[str, Any], *, asset_root: str | Path | None,
) -> Path:
    source = _source_artifact(acquisition)
    path = Path(str(source.get("packed_path") or ""))
    if not path.is_absolute():
        path = Path(asset_root or ".").resolve() / path
    if not path.is_file():
        raise LiteratureContractError(
            "parser_source_missing", "The acquired content artifact is not present on disk.",
            artifact_id=source.get("artifact_id"), packed_path=str(path),
        )
    return path


def mem_available_bytes() -> int:
    """Current MemAvailable. C3 refuses a parse when this is below the C1 guard."""
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise LiteratureContractError(
        "meminfo_missing",
        "MemAvailable is required before a Docling parse may start.",
    )


def parse_start_allowed(*, ceiling_bytes: int = PEAK_RSS_CEILING_BYTES) -> bool:
    """True iff MemAvailable >= C1 peak + 512 MiB. A false value defers; it does not hope."""
    return mem_available_bytes() >= int(ceiling_bytes) + PEAK_RSS_HEADROOM_BYTES


def parse_experiment_document(
    acquisition: Mapping[str, Any],
    *,
    backend: str,
    parsed_at: str | None = None,
    asset_root: str | Path | None = None,
) -> dict[str, Any]:
    """One-paper experiment parse. The cascade does not cascade.

    ``backend`` is required: ``docling`` or ``pypdf``. There is no default, and
    there is no fallback flag. A Docling failure raises; it does not call
    DeepDoc or ``_pypdf_bridge``. ``pypdf`` is the named step-3 control arm
    (``fallback_reason=explicit_control_arm``), never a failure path.
    """
    requested = str(backend or "").casefold()
    if requested not in _EXPERIMENT_PARSE_BACKENDS:
        raise LiteratureContractError(
            "unknown_experiment_backend",
            "Experiment parses name backend='docling' or backend='pypdf'.",
            backend=requested,
        )
    path = _experiment_source_path(acquisition, asset_root=asset_root)
    if requested == "docling":
        try:
            version = str(importlib.import_module("docling").__version__)
        except (ImportError, AttributeError) as error:
            raise LiteratureContractError(
                "parser_backend_unavailable",
                "Docling is unavailable; install the pinned research extra before parsing documents.",
                backend="docling",
            ) from error
        if version != _EXPERIMENT_DOCLING_VERSION:
            raise LiteratureContractError(
                "parser_version_mismatch",
                "Experiment Docling parses require exactly version 2.121.0.",
                backend="docling", parser_version=version,
                required=_EXPERIMENT_DOCLING_VERSION,
            )
        bridge = _run_docling(path)
    else:
        from .literature_ingest import _pypdf_bridge
        bridge = _pypdf_bridge(path, "explicit_control_arm")
    produced = str(bridge.get("backend") or "").casefold()
    if produced != requested:
        raise LiteratureContractError(
            "parser_identity_lie",
            "Experiment parse backend must match the named backend argument.",
            requested=requested, produced=produced,
        )
    parsed = _normalize_parser_bridge(
        acquisition, bridge, parsed_at=parsed_at, stamp_heading_fill=True,
    )
    if parsed.get("parser_backend") != requested:
        raise LiteratureContractError(
            "parser_identity_lie",
            "Persisted parser_backend must match the named experiment backend.",
            requested=requested, produced=parsed.get("parser_backend"),
        )
    return parsed


def _m3_repair(text: str) -> str:
    """Closed M3 list, in order, once. Named so C2.1b can spy and C2.1c can patch."""
    repaired = str(text).replace("\u25e6", "\u00b0").replace("\u00ad", "")
    repaired = _SPLIT_ACUTE_RE.sub(lambda match: match.group(1) + "\u0301", repaired)
    repaired = unicodedata.normalize("NFC", repaired)
    return _DEGREE_C_WS_RE.sub("\u00b0C", repaired)


def _mark_offset_assignment(char_start: int, char_end: int) -> None:
    """C2.1b hook. Production is a no-op; tests spy this after every span write."""
    del char_start, char_end
    return None


def _json_safe(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_safe(item) for key, item in value.items())
    return False


def _canonical_kind(kind: Any) -> str:
    label = str(kind or "other")
    if label in _CANONICAL_STORED_KINDS:
        return label
    return "other"


def _copy_parser_block_fields(block: Mapping[str, Any]) -> dict[str, Any]:
    """Copy parser keys. No allowlist. Drop production section_path and fill stamps."""
    copied: dict[str, Any] = {}
    for key, value in block.items():
        if key in _CANONICAL_DROP_KEYS:
            continue
        if _json_safe(value):
            copied[key] = copy.deepcopy(value)
    copied.setdefault("bbox", None)
    copied.setdefault("confidence", None)
    copied.setdefault("footnote_refs", [])
    return copied


def build_canonical_document(
    parsed: Mapping[str, Any],
    *,
    parse_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One-paper canonical document. Offsets into already-normalized canonical_text.

    Repair every block and cell string first, then concatenate with ``\\n\\n``
    and assign ``char_start``/``char_end``. Streaming (repair n, assign n, repair
    n+1) is a C2.1b failure.
    """
    if not isinstance(parsed, Mapping):
        raise LiteratureContractError(
            "invalid_parsed_document", "Canonical build requires a parsed document mapping.",
        )
    metrics = dict(parse_metrics or parsed.get("parse_metrics") or {})
    if "wall_s" not in metrics or "peak_rss_bytes" not in metrics:
        raise LiteratureContractError(
            "missing_parse_metrics",
            "Canonical envelope requires parse_metrics.wall_s and parse_metrics.peak_rss_bytes.",
        )

    tables_in = [copy.deepcopy(table) for table in (parsed.get("tables") or [])]
    table_by_id: dict[str, dict[str, Any]] = {}
    for table in tables_in:
        table_id = str(table.get("table_id") or "")
        if not table_id:
            raise LiteratureContractError(
                "missing_table_id", "A parsed table must carry table_id.",
            )
        for cell in table.get("cells") or []:
            cell["text"] = _m3_repair(str(cell.get("text") or ""))
        table_by_id[table_id] = table

    repaired_blocks: list[tuple[dict[str, Any], str]] = []
    for block in parsed.get("blocks") or []:
        fill = block.get(_HEADING_FILL_KEY)
        if fill not in {"parser_supplied", "inherited_from_stack"}:
            raise LiteratureContractError(
                "missing_heading_fill_stamp",
                "Canonical build requires heading origin stamped at fill time.",
                block_id=block.get("block_id"),
            )
        kind = _canonical_kind(block.get("kind"))
        if kind == "table":
            table = table_by_id.get(str(block.get("block_id") or ""))
            if table is None:
                text = _m3_repair(str(block.get("text") or ""))
            else:
                text = _m3_repair(_table_grid_text(
                    table.get("cells") or [],
                    table_id=table.get("table_id"),
                    page=table.get("page"),
                    row_count=table.get("row_count"),
                    column_count=table.get("column_count"),
                ))
        else:
            text = _m3_repair(str(block.get("text") or ""))
        if not str(text).strip():
            continue
        repaired_blocks.append((dict(block), text))

    parts: list[str] = []
    canonical_blocks: list[dict[str, Any]] = []
    table_spans: dict[str, tuple[int, int]] = {}
    cursor = 0
    for index, (raw_block, text) in enumerate(repaired_blocks):
        if index:
            parts.append("\n\n")
            cursor += 2
        char_start = cursor
        parts.append(text)
        cursor += len(text)
        char_end = cursor
        _mark_offset_assignment(char_start, char_end)
        heading = list(raw_block.get("section_path") or [])
        caption_ref = raw_block.get("caption_ref")
        out_block = _copy_parser_block_fields(raw_block)
        out_block.update({
            "kind": _canonical_kind(raw_block.get("kind")),
            "text": text,
            "char_start": char_start,
            "char_end": char_end,
            "nearest_preceding_heading": heading,
            "nearest_preceding_heading_origin": raw_block[_HEADING_FILL_KEY],
            "caption_ref_origin": "bound" if caption_ref not in (None, "") else "unbound",
        })
        canonical_blocks.append(out_block)
        if out_block["kind"] == "table":
            table_spans[str(out_block.get("block_id") or "")] = (char_start, char_end)

    canonical_text = "".join(parts)
    tables_out: list[dict[str, Any]] = []
    for table in tables_in:
        table_id = str(table.get("table_id") or "")
        span = table_spans.get(table_id)
        if span is None:
            raise LiteratureContractError(
                "table_block_missing",
                "Every tables[] row must have exactly one kind=table block with the same id.",
                table_id=table_id,
            )
        table["char_start"] = span[0]
        table["char_end"] = span[1]
        tables_out.append(table)

    pages = 0
    for block in canonical_blocks:
        page = block.get("page")
        if isinstance(page, int) and page > pages:
            pages = page
    for table in tables_out:
        page = table.get("page")
        if isinstance(page, int) and page > pages:
            pages = page

    return {
        "schema": _CANONICAL_DOCUMENT_SCHEMA,
        "source_pdf_sha256": str(parsed.get("source_sha256") or ""),
        "parser_backend": parsed.get("parser_backend"),
        "parser_version": parsed.get("parser_version"),
        "fallback_reason": parsed.get("fallback_reason"),
        "quality_flags": list(parsed.get("quality_flags") or []),
        "pages": pages,
        "canonical_text": canonical_text,
        "blocks": canonical_blocks,
        "tables": tables_out,
        "attachments": [],
        "parse_metrics": {
            "wall_s": metrics["wall_s"],
            "peak_rss_bytes": metrics["peak_rss_bytes"],
        },
        "parsed_at": parsed.get("parsed_at") or _now(),
        "normalization": {
            "applied_during_build": True,
            "rules": list(_M3_RULE_NAMES),
            "post_pass": False,
        },
    }


def load_sealed_gold_facts(path: str | Path) -> dict[str, Any]:
    """Load gold v1. Abort if the bytes are not the sealed digest."""
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != _GOLD_FACTS_V1_SHA256:
        raise LiteratureContractError(
            "gold_digest_mismatch",
            "Scorer aborts when gold_facts.v1.json is not the sealed digest.",
            path=str(path), digest=digest, required=_GOLD_FACTS_V1_SHA256,
        )
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("facts"), list):
        raise LiteratureContractError(
            "invalid_gold_facts", "Sealed gold must be an object with a facts list.",
        )
    return payload


def _chunk_header(heading: Sequence[str] | None) -> str:
    return " > ".join(str(part) for part in (heading or []) if str(part).strip())


def _make_chunk(
    *, strategy: str, index: int, body: str, header: str,
    char_start: int | None, char_end: int | None,
    atomic_overflow: bool = False, page: int | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "strategy": strategy,
        "chunk_id": f"{strategy}-{index:04d}",
        "body": body,
        "header": header,
        "char_start": char_start,
        "char_end": char_end,
        "atomic_overflow": atomic_overflow,
    }
    if page is not None:
        chunk["page"] = page
    return chunk


def _canonical_slice_ok(canonical: Mapping[str, Any], chunk: Mapping[str, Any]) -> bool:
    start, end = chunk.get("char_start"), chunk.get("char_end")
    if start is None or end is None:
        return False
    text = str(canonical.get("canonical_text") or "")
    return text[int(start):int(end)] == chunk.get("body")


def _unit_from_block(block: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(block.get("kind") or "other")
    return {
        "char_start": int(block["char_start"]),
        "char_end": int(block["char_end"]),
        "kind": kind,
        "heading": list(block.get("nearest_preceding_heading") or []),
        "atomic": kind in _ATOMIC_CHUNK_KINDS,
        "block_id": block.get("block_id"),
    }


def _sentence_units(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(block.get("text") or "")
    base = int(block["char_start"])
    if str(block.get("kind")) not in {"paragraph", "list_item"} or not text:
        return [_unit_from_block(block)]
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_SPLIT_RE.finditer(text):
        if match.start() > cursor:
            spans.append((base + cursor, base + match.start()))
        cursor = match.end()
    if cursor < len(text):
        spans.append((base + cursor, base + len(text)))
    if not spans:
        return [_unit_from_block(block)]
    heading = list(block.get("nearest_preceding_heading") or [])
    return [{
        "char_start": start, "char_end": end, "kind": str(block.get("kind") or "paragraph"),
        "heading": heading, "atomic": False, "block_id": block.get("block_id"),
    } for start, end in spans if end > start]


def _pack_units(
    units: Sequence[Mapping[str, Any]],
    canonical_text: str,
    *,
    strategy: str,
    target: int = _CHUNK_TARGET,
    start_new_on_heading: bool = False,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []

    def emit(start: int, end: int, heading: Sequence[str], overflow: bool) -> None:
        chunks.append(_make_chunk(
            strategy=strategy, index=len(chunks) + 1,
            body=canonical_text[start:end], header=_chunk_header(heading),
            char_start=start, char_end=end, atomic_overflow=overflow,
        ))

    buf_start: int | None = None
    buf_end: int | None = None
    buf_heading: list[str] = []
    buf_overflow = False
    buf_started_heading: tuple[str, ...] | None = None

    def flush() -> None:
        nonlocal buf_start, buf_end, buf_heading, buf_overflow, buf_started_heading
        if buf_start is None or buf_end is None:
            return
        emit(buf_start, buf_end, buf_heading, buf_overflow)
        buf_start = buf_end = None
        buf_heading = []
        buf_overflow = False
        buf_started_heading = None

    for unit in units:
        start, end = int(unit["char_start"]), int(unit["char_end"])
        heading = list(unit.get("heading") or [])
        heading_key = tuple(heading)
        atomic = bool(unit.get("atomic"))
        overflow = atomic and (end - start) > target
        if start_new_on_heading and buf_start is not None and unit.get("kind") == "heading":
            flush()
        if buf_start is not None and buf_end is not None and start > buf_end + 2:
            flush()
        if atomic:
            flush()
            emit(start, end, heading, overflow)
            continue
        if buf_start is None:
            buf_start, buf_end, buf_heading = start, end, heading
            buf_started_heading = heading_key
            continue
        if (end - buf_start) > target:
            flush()
            buf_start, buf_end, buf_heading = start, end, heading
            buf_started_heading = heading_key
            continue
        buf_end = end
    flush()
    return chunks


def chunk_s0_production_pypdf(
    page_texts: Sequence[str], *, target: int = _CHUNK_TARGET, overlap: int = _CHUNK_OVERLAP,
) -> list[dict[str, Any]]:
    """S0: production `_paragraph_chunks` on pypdf page text. Not a Docling function."""
    chunks: list[dict[str, Any]] = []
    for page_number, page_text in enumerate(page_texts, 1):
        for section, body in _paragraph_chunks(page_text, target=target, overlap=overlap):
            if not str(body).strip():
                continue
            chunks.append(_make_chunk(
                strategy="S0_production_pypdf", index=len(chunks) + 1,
                body=body, header=str(section or ""),
                char_start=None, char_end=None, page=page_number,
            ))
    return chunks


def chunk_s1_naive_char(
    canonical: Mapping[str, Any], *, target: int = _CHUNK_TARGET, overlap: int = _CHUNK_OVERLAP,
) -> list[dict[str, Any]]:
    """S1: sliding window on canonical_text, ignoring block kinds. Negative control."""
    text = str(canonical.get("canonical_text") or "")
    chunks: list[dict[str, Any]] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + target, n)
        chunks.append(_make_chunk(
            strategy="S1_naive_char", index=len(chunks) + 1,
            body=text[start:end], header="",
            char_start=start, char_end=end,
        ))
        if end >= n:
            break
        nxt = end - overlap
        start = end if nxt <= start else nxt
    return chunks


def chunk_s2_block_pack(
    canonical: Mapping[str, Any], *, target: int = _CHUNK_TARGET,
) -> list[dict[str, Any]]:
    units = [_unit_from_block(block) for block in canonical.get("blocks") or []]
    return _pack_units(
        units, str(canonical.get("canonical_text") or ""),
        strategy="S2_block_pack", target=target,
    )


def chunk_s3_section_pack(
    canonical: Mapping[str, Any], *, target: int = _CHUNK_TARGET,
) -> list[dict[str, Any]]:
    units = [_unit_from_block(block) for block in canonical.get("blocks") or []]
    return _pack_units(
        units, str(canonical.get("canonical_text") or ""),
        strategy="S3_section_pack", target=target, start_new_on_heading=True,
    )


def chunk_s4_sentence_pack(
    canonical: Mapping[str, Any], *, target: int = _CHUNK_TARGET,
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for block in canonical.get("blocks") or []:
        units.extend(_sentence_units(block))
    return _pack_units(
        units, str(canonical.get("canonical_text") or ""),
        strategy="S4_sentence_pack", target=target,
    )


def chunk_s5_table_plus_neighbors(
    canonical: Mapping[str, Any], *, target: int = _CHUNK_TARGET,
) -> list[dict[str, Any]]:
    text = str(canonical.get("canonical_text") or "")
    blocks = list(canonical.get("blocks") or [])
    by_id = {str(block.get("block_id")): i for i, block in enumerate(blocks)}
    covered: set[int] = set()
    table_chunks: list[dict[str, Any]] = []
    for table in canonical.get("tables") or []:
        table_id = str(table.get("table_id") or "")
        if table_id not in by_id:
            continue
        members = [blocks[by_id[table_id]]]
        idx = by_id[table_id]
        for prev in range(idx - 1, -1, -1):
            kind = str(blocks[prev].get("kind") or "")
            if kind in {"header", "footer"}:
                continue
            members.append(blocks[prev])
            break
        caption_id = table.get("caption_block_id") or blocks[idx].get("caption_ref")
        if caption_id and str(caption_id) in by_id:
            members.append(blocks[by_id[str(caption_id)]])
        start = min(int(item["char_start"]) for item in members)
        end = max(int(item["char_end"]) for item in members)
        heading = list(blocks[idx].get("nearest_preceding_heading") or [])
        table_chunks.append(_make_chunk(
            strategy="S5_table_plus_neighbors", index=len(table_chunks) + 1,
            body=text[start:end], header=_chunk_header(heading),
            char_start=start, char_end=end,
        ))
        for i, block in enumerate(blocks):
            if int(block["char_start"]) >= start and int(block["char_end"]) <= end:
                covered.add(i)
    remaining = [_unit_from_block(block) for i, block in enumerate(blocks) if i not in covered]
    rest = _pack_units(
        remaining, text, strategy="S5_table_plus_neighbors",
        target=target, start_new_on_heading=True,
    )
    combined = table_chunks + rest
    for index, chunk in enumerate(combined, 1):
        chunk["chunk_id"] = f"S5_table_plus_neighbors-{index:04d}"
        chunk["strategy"] = "S5_table_plus_neighbors"
    return combined


_C6_CHUNKERS = {
    "S0_production_pypdf": None,
    "S1_naive_char": chunk_s1_naive_char,
    "S2_block_pack": chunk_s2_block_pack,
    "S3_section_pack": chunk_s3_section_pack,
    "S4_sentence_pack": chunk_s4_sentence_pack,
    "S5_table_plus_neighbors": chunk_s5_table_plus_neighbors,
}


def _intersects(cs: int, ce: int, ss: int, se: int) -> bool:
    return cs < se and ss < ce


def _covers(cs: int, ce: int, ss: int, se: int) -> bool:
    return cs <= ss and ce >= se


def _proper_subset(cs: int, ce: int, ss: int, se: int) -> bool:
    return ss <= cs and ce <= se and (cs > ss or ce < se)


def _atomic_spans(canonical: Mapping[str, Any], kinds: set[str]) -> list[tuple[int, int]]:
    spans = []
    for block in canonical.get("blocks") or []:
        if str(block.get("kind")) in kinds:
            spans.append((int(block["char_start"]), int(block["char_end"])))
    return spans


def chunk_splits_atomic(chunks: Sequence[Mapping[str, Any]], canonical: Mapping[str, Any], kinds: set[str]) -> int:
    """Count chunks that intersect an atomic span without covering it."""
    splits = 0
    for span_start, span_end in _atomic_spans(canonical, kinds):
        for chunk in chunks:
            start, end = chunk.get("char_start"), chunk.get("char_end")
            if start is None or end is None:
                continue
            if _intersects(int(start), int(end), span_start, span_end) and not _covers(
                int(start), int(end), span_start, span_end,
            ):
                splits += 1
    return splits


def _chunk_body_text(chunk: Mapping[str, Any]) -> str:
    if chunk.get("body") is not None:
        return str(chunk.get("body") or "")
    return str(chunk.get("text") or "")


def _join_match_parts(*parts: Any) -> str:
    return "\n".join(str(part) for part in parts if part and str(part).strip())


def _is_footnote_marker(text: str) -> bool:
    return bool(_FOOTNOTE_MARKER_RE.match(str(text or "").strip()))


def _caption_needs_continuation(text: str) -> bool:
    """Stub `Table N` or a truncated table label. Not a complete caption sentence."""
    stripped = " ".join(str(text or "").split())
    if not stripped:
        return True
    if re.fullmatch(r"Table\s+\d+[.:]?", stripped, flags=re.IGNORECASE):
        return True
    match = re.match(r"^Table\s+\d+\b(.*)$", stripped, flags=re.IGNORECASE)
    if match is not None and len(match.group(1).strip()) < 24:
        return True
    return stripped[-1] not in ".!?"


def rebound_blocks_for_table(
    canonical: Mapping[str, Any], table: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Caption, orphan continuation, and following footnotes. Not the table body.

    Chunk-level association. Does not rewrite parser `caption_ref` / `unbound`.
    """
    blocks = list(canonical.get("blocks") or [])
    table_id = str(table.get("table_id") or "")
    by_index = {str(block.get("block_id")): i for i, block in enumerate(blocks)}
    idx = by_index.get(table_id)
    associated: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(block: Mapping[str, Any]) -> None:
        bid = str(block.get("block_id") or "")
        if not bid or bid == table_id or bid in seen:
            return
        if str(block.get("kind") or "") == "table":
            return
        seen.add(bid)
        associated.append(dict(block))

    table_block: Mapping[str, Any] = blocks[idx] if idx is not None else {}
    cap_id = table.get("caption_block_id") or table_block.get("caption_ref")
    caption_text = ""
    if cap_id not in (None, "") and str(cap_id) in by_index:
        cap_block = blocks[by_index[str(cap_id)]]
        add(cap_block)
        caption_text = str(cap_block.get("text") or "")
    for ref in table_block.get("footnote_refs") or []:
        if str(ref) in by_index:
            add(blocks[by_index[str(ref)]])
    unbound = cap_id in (None, "")
    needs = unbound or _caption_needs_continuation(caption_text)
    if idx is not None:
        for prev in range(idx - 1, -1, -1):
            block = blocks[prev]
            kind = str(block.get("kind") or "")
            text = str(block.get("text") or "")
            if kind in {"header", "footer"} or _is_footnote_marker(text):
                continue
            if kind in {"table", "heading", "footnote"}:
                break
            if kind == "caption":
                if _TABLE_LABEL_RE.match(text):
                    add(block)
                    if not caption_text:
                        caption_text = text
                        needs = needs or _caption_needs_continuation(text)
                break
            if kind == "paragraph" and needs:
                add(block)
                continue
            break
        for nxt in range(idx + 1, len(blocks)):
            block = blocks[nxt]
            kind = str(block.get("kind") or "")
            text = str(block.get("text") or "")
            if kind in {"header", "footer"} or _is_footnote_marker(text):
                continue
            if kind in {"table", "heading"}:
                break
            if kind == "footnote":
                add(block)
                continue
            if kind == "caption" and _TABLE_LABEL_RE.match(text):
                add(block)
                continue
            break
    associated.sort(key=lambda block: (int(block.get("char_start") or 0), str(block.get("block_id") or "")))
    return associated


def _table_rebound_text(canonical: Mapping[str, Any], table: Mapping[str, Any]) -> str:
    return _join_match_parts(*(block.get("text") for block in rebound_blocks_for_table(canonical, table)))


def _body_plus_rebound_text(
    body: str, canonical: Mapping[str, Any], table: Mapping[str, Any],
) -> str:
    table_start, table_end = int(table["char_start"]), int(table["char_end"])
    before: list[str] = []
    after: list[str] = []
    for block in rebound_blocks_for_table(canonical, table):
        text = str(block.get("text") or "")
        try:
            start, end = int(block["char_start"]), int(block["char_end"])
        except (KeyError, TypeError, ValueError):
            after.append(text)
            continue
        if end <= table_start:
            before.append(text)
        else:
            after.append(text)
    return _join_match_parts(*before, body, *after)


def apply_table_rebound(
    chunks: Sequence[Mapping[str, Any]],
    canonical: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Attach rebound sidecars. Does not change char_start/char_end or body/text."""
    tables_by_span = {
        (int(table["char_start"]), int(table["char_end"])): table
        for table in canonical.get("tables") or []
        if table.get("char_start") is not None and table.get("char_end") is not None
    }
    attached: list[dict[str, Any]] = []
    for chunk in chunks:
        item = dict(chunk)
        body = _chunk_body_text(item)
        start, end = item.get("char_start"), item.get("char_end")
        table = None
        if start is not None and end is not None:
            table = tables_by_span.get((int(start), int(end)))
        if table is None:
            item["rebound_block_ids"] = list(item.get("rebound_block_ids") or [])
            item["body_plus_rebound"] = body
            item.setdefault("footnotes", item.get("footnotes"))
            attached.append(item)
            continue
        associated = rebound_blocks_for_table(canonical, table)
        notes = _join_match_parts(
            *(block.get("text") for block in associated if str(block.get("kind")) == "footnote")
        )
        item["rebound_block_ids"] = [str(block.get("block_id")) for block in associated]
        item["footnotes"] = notes or None
        item["body_plus_rebound"] = _body_plus_rebound_text(body, canonical, table)
        attached.append(item)
    return attached


def _chunk_rebound_corpus(
    chunk: Mapping[str, Any],
    canonical: Mapping[str, Any],
    tables_by_span: Mapping[tuple[int, int], Mapping[str, Any]],
) -> str:
    if "body_plus_rebound" in chunk:
        return str(chunk.get("body_plus_rebound") or "")
    body = _chunk_body_text(chunk)
    start, end = chunk.get("char_start"), chunk.get("char_end")
    if start is None or end is None:
        return body
    table = tables_by_span.get((int(start), int(end)))
    if table is None:
        return body
    return _body_plus_rebound_text(body, canonical, table)


def table_atomic_fraction(chunks: Sequence[Mapping[str, Any]], canonical: Mapping[str, Any]) -> float | None:
    tables = list(canonical.get("tables") or [])
    if not tables:
        return None
    hits = 0
    for table in tables:
        ts, te = int(table["char_start"]), int(table["char_end"])
        if any(
            chunk.get("char_start") == ts and chunk.get("char_end") == te
            for chunk in chunks
        ):
            hits += 1
    return hits / len(tables)


def _needle_hit(corpus: str, needle: Any) -> bool:
    return str(needle).casefold() in str(corpus).casefold()


def _body_has_all_needles(body: str, needles: Mapping[str, Any]) -> bool:
    return all(_needle_hit(body, value) for value in needles.values())


def official_contain_bound_fact_eligible(fact: Mapping[str, Any]) -> bool:
    """Official contain_bound_fact is a bound-fact score, not string existence.

    Ensemble accept test 2: a ``scoring_class=string_existence`` row must not
    satisfy official ``contain_bound_fact``. Empty ``needles`` is vacuously
    true in ``_body_has_all_needles`` and must not be scored as bound. Gold v1
    rows have nonempty needles and no ``scoring_class`` and stay eligible.
    Ensemble ``gold`` / ``gold_unsealed`` ``bound_fact`` rows need all four named
    needles so a one-key row cannot dummy-score True if the set invariant is
    bypassed. ``awaiting_C`` table-cell candidates are not gold yet.
    """
    if str(fact.get("scoring_class") or "") == "string_existence":
        return False
    if str(fact.get("status") or "") in {
        "awaiting_C", "awaiting_needles", "disputed", "diagnostic_not_fact",
    }:
        return False
    needles = fact.get("needles") or {}
    filled = sum(
        1
        for key in ("polymer", "solvent", "temperature", "value")
        if str(needles.get(key) or "").strip()
    )
    # Ensemble gold_unsealed is four named keys. Gold v1 has no scoring_class
    # and stays on nonempty needles (two of twelve facts are not four-key).
    if (
        str(fact.get("scoring_class") or "") == "bound_fact"
        and str(fact.get("status") or "") in {"gold", "gold_unsealed"}
        and filled != 4
    ):
        return False
    if not any(str(value or "").strip() for value in needles.values()):
        return False
    return True


def _page6_table_spans(canonical: Mapping[str, Any]) -> list[tuple[int, int]]:
    return [
        (int(table["char_start"]), int(table["char_end"]))
        for table in canonical.get("tables") or []
        if table.get("page") == 6
    ]


def _locus_table_spans(canonical: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
    labels: dict[str, tuple[int, int]] = {}
    blocks = list(canonical.get("blocks") or [])
    by_id = {str(block.get("block_id")): block for block in blocks}
    for table in canonical.get("tables") or []:
        span = (int(table["char_start"]), int(table["char_end"]))
        candidates: list[str] = []
        caption_id = table.get("caption_block_id") or (by_id.get(str(table.get("table_id"))) or {}).get("caption_ref")
        if caption_id and str(caption_id) in by_id:
            candidates.append(str(by_id[str(caption_id)].get("text") or ""))
        table_idx = next(
            (i for i, block in enumerate(blocks) if block.get("block_id") == table.get("table_id")),
            None,
        )
        if table_idx is not None:
            for prev in reversed(blocks[:table_idx]):
                kind = str(prev.get("kind") or "")
                if kind == "table":
                    break
                candidates.append(str(prev.get("text") or ""))
                if kind == "heading":
                    break
            for nxt in blocks[table_idx + 1:]:
                kind = str(nxt.get("kind") or "")
                if kind in {"table", "heading"}:
                    break
                candidates.append(str(nxt.get("text") or ""))
        for text in candidates:
            match = _TABLE_LABEL_RE.match(text)
            if match:
                labels.setdefault(f"Table {match.group(1)}", span)
                break
    return labels


def chunk_sparse_corpus(chunk: Mapping[str, Any]) -> str:
    """C7c: rank on rebound when present. Official body-only scores do not use this."""
    rebound = str(chunk.get("body_plus_rebound") or "").strip()
    if rebound:
        return rebound
    return _chunk_body_text(chunk)


def _bm25_top5_on(
    query: str,
    chunks: Sequence[Mapping[str, Any]],
    corpus_of,
) -> list[Mapping[str, Any]]:
    rows = [{"text": str(corpus_of(chunk) or "")} for chunk in chunks]
    scores = _bm25(_tokens(query), rows)
    ranked = sorted(
        zip(scores, range(len(chunks)), chunks),
        key=lambda item: (-item[0], item[1]),
    )
    return [chunk for _, _, chunk in ranked[:5]]


def _bm25_top5(query: str, chunks: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Official v2 §7 ranking: chunk body only."""
    return _bm25_top5_on(query, chunks, _chunk_body_text)


def score_chunks_against_facts(
    chunks: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
    canonical: Mapping[str, Any],
    *,
    strategy: str,
) -> dict[str, Any]:
    """v2 §7 metrics. Does not blend an F1. Offset metrics skipped for S0."""
    page6 = _page6_table_spans(canonical)
    locus_spans = _locus_table_spans(canonical)
    tables_by_span = {
        (int(table["char_start"]), int(table["char_end"])): table
        for table in canonical.get("tables") or []
        if table.get("char_start") is not None and table.get("char_end") is not None
    }
    offsetful = strategy != "S0_production_pypdf"
    per_fact: list[dict[str, Any]] = []
    cross_hits = 0
    for fact in facts:
        fact_id = str(fact.get("fact_id") or "")
        needles = dict(fact.get("needles") or {})
        locus = str(fact.get("locus") or "")
        parse_gated = locus.casefold().startswith("fig") or int(fact.get("page") or 0) == 6
        parse_miss = parse_gated and not page6
        bodies = [_chunk_body_text(chunk) for chunk in chunks]
        if "value" in needles:
            contain_value = any(_needle_hit(body, needles.get("value")) for body in bodies)
        else:
            contain_value = any(
                _needle_hit(body, value) for body in bodies for value in needles.values()
            )
        bound_chunks = [chunk for chunk in chunks if _body_has_all_needles(_chunk_body_text(chunk), needles)]
        contain_bound_fact = bool(bound_chunks)
        rebound_bound_chunks = [
            chunk for chunk in chunks
            if _body_has_all_needles(_chunk_rebound_corpus(chunk, canonical, tables_by_span), needles)
        ]
        contain_bound_fact_rebound = bool(rebound_bound_chunks)
        if parse_gated and contain_bound_fact:
            contain_bound_fact = any(
                chunk.get("char_start") is not None
                and any(
                    _intersects(int(chunk["char_start"]), int(chunk["char_end"]), span[0], span[1])
                    for span in page6
                )
                for chunk in bound_chunks
            ) if page6 else False
        if parse_gated and contain_bound_fact_rebound:
            contain_bound_fact_rebound = any(
                chunk.get("char_start") is not None
                and any(
                    _intersects(int(chunk["char_start"]), int(chunk["char_end"]), span[0], span[1])
                    for span in page6
                )
                for chunk in rebound_bound_chunks
            ) if page6 else False
        header_only = False
        if not contain_bound_fact:
            header_only = any(
                _body_has_all_needles(str(chunk.get("header") or ""), needles)
                and not _body_has_all_needles(_chunk_body_text(chunk), needles)
                for chunk in chunks
            )
        query = str(fact.get("query") or "")
        top5 = _bm25_top5(query, chunks) if query else []
        top5_rebound = (
            _bm25_top5_on(
                query, chunks,
                lambda chunk: _chunk_rebound_corpus(chunk, canonical, tables_by_span),
            )
            if query else []
        )
        retrievable = any(_body_has_all_needles(_chunk_body_text(chunk), needles) for chunk in top5)
        retrievable_rebound = any(
            _body_has_all_needles(_chunk_rebound_corpus(chunk, canonical, tables_by_span), needles)
            for chunk in top5_rebound
        )
        if parse_gated and retrievable and page6:
            retrievable = any(
                _body_has_all_needles(_chunk_body_text(chunk), needles)
                and chunk.get("char_start") is not None
                and any(
                    _intersects(int(chunk["char_start"]), int(chunk["char_end"]), span[0], span[1])
                    for span in page6
                )
                for chunk in top5
            )
        if parse_gated and retrievable_rebound and page6:
            retrievable_rebound = any(
                _body_has_all_needles(_chunk_rebound_corpus(chunk, canonical, tables_by_span), needles)
                and chunk.get("char_start") is not None
                and any(
                    _intersects(int(chunk["char_start"]), int(chunk["char_end"]), span[0], span[1])
                    for span in page6
                )
                for chunk in top5_rebound
            )
        severed = False
        severed_ctx = False
        table_span = locus_spans.get(locus) if locus.startswith("Table ") else None
        table_obj = tables_by_span.get(table_span) if table_span else None
        if offsetful and table_span and "value" in needles:
            rebound_txt = _table_rebound_text(canonical, table_obj) if table_obj is not None else ""
            for chunk in chunks:
                start, end = chunk.get("char_start"), chunk.get("char_end")
                if start is None or end is None:
                    continue
                body = _chunk_body_text(chunk)
                value_hit = _needle_hit(body, needles.get("value"))
                bound_on_body = _body_has_all_needles(body, needles)
                subset = _proper_subset(int(start), int(end), table_span[0], table_span[1])
                if value_hit and not bound_on_body and subset:
                    severed = True
                if (
                    value_hit
                    and not bound_on_body
                    and not subset
                    and rebound_txt
                ):
                    missing = [value for value in needles.values() if not _needle_hit(body, value)]
                    if any(_needle_hit(rebound_txt, missing_needle) for missing_needle in missing):
                        severed_ctx = True
        if not official_contain_bound_fact_eligible(fact):
            contain_bound_fact = False
            contain_bound_fact_rebound = False
            retrievable = False
            retrievable_rebound = False
            header_only = False
        row = {
            "fact_id": fact_id,
            "parse_miss": parse_miss,
            "contain_value": contain_value,
            "contain_bound_fact": None if parse_miss else contain_bound_fact,
            "contain_bound_fact_rebound": None if parse_miss else contain_bound_fact_rebound,
            "retrievable": None if parse_miss else retrievable,
            "retrievable_rebound": None if parse_miss else retrievable_rebound,
            "condition_severed_inside_table": None if (parse_miss or not offsetful) else severed,
            "condition_severed_from_context": None if (parse_miss or not offsetful) else severed_ctx,
            "header_only_hit": header_only,
        }
        per_fact.append(row)
        if parse_miss:
            continue
        value_a = needles.get("value")
        if value_a is None or not contain_bound_fact:
            continue
        for other in facts:
            if other is fact:
                continue
            if str(other.get("locus") or "") == locus:
                continue
            other_needles = dict(other.get("needles") or {})
            other_value = other_needles.get("value")
            if other_value is None:
                continue
            for chunk in bound_chunks:
                if _needle_hit(_chunk_body_text(chunk), other_value):
                    cross_hits += 1
                    break
    scored = [row for row in per_fact if not row["parse_miss"]]
    return {
        "strategy": strategy,
        "n_chunks": len(chunks),
        "table_splits": chunk_splits_atomic(chunks, canonical, {"table"}) if offsetful else None,
        "formula_splits": chunk_splits_atomic(chunks, canonical, {"formula"}) if offsetful else None,
        "caption_splits": chunk_splits_atomic(chunks, canonical, {"caption"}) if offsetful else None,
        "table_atomic": table_atomic_fraction(chunks, canonical) if offsetful else None,
        "n_parse_miss": sum(1 for row in per_fact if row["parse_miss"]),
        "n_contain_value": sum(1 for row in scored if row["contain_value"]),
        "n_contain_bound_fact": sum(1 for row in scored if row["contain_bound_fact"]),
        "n_contain_bound_fact_rebound": sum(1 for row in scored if row["contain_bound_fact_rebound"]),
        "n_retrievable": sum(1 for row in scored if row["retrievable"]),
        "n_retrievable_rebound": sum(1 for row in scored if row["retrievable_rebound"]),
        "n_condition_severed_inside_table": sum(
            1 for row in scored if row["condition_severed_inside_table"]
        ),
        "n_condition_severed_from_context": sum(
            1 for row in scored if row["condition_severed_from_context"]
        ),
        "cross_fact_hits": cross_hits,
        "facts": per_fact,
    }


def pypdf_page_texts_from_persist(parsed: Mapping[str, Any]) -> list[str]:
    """Rebuild page strings from a saved pypdf persist. Does not call Docling."""
    by_page: dict[int, list[str]] = {}
    for block in parsed.get("blocks") or []:
        page = int(block.get("page") or 0)
        by_page.setdefault(page, []).append(str(block.get("text") or ""))
    return ["\n\n".join(by_page[page]) for page in sorted(by_page)]


def sweep_one_paper_chunking(
    canonical: Mapping[str, Any],
    pypdf_page_texts: Sequence[str],
    gold_path: str | Path,
) -> dict[str, Any]:
    """C6 one-paper sweep. Pure functions of saved artifacts. Names all six strategies."""
    gold = load_sealed_gold_facts(gold_path)
    facts = list(gold.get("facts") or [])
    built: dict[str, list[dict[str, Any]]] = {
        "S0_production_pypdf": chunk_s0_production_pypdf(pypdf_page_texts),
        "S1_naive_char": chunk_s1_naive_char(canonical),
        "S2_block_pack": chunk_s2_block_pack(canonical),
        "S3_section_pack": chunk_s3_section_pack(canonical),
        "S4_sentence_pack": chunk_s4_sentence_pack(canonical),
        "S5_table_plus_neighbors": chunk_s5_table_plus_neighbors(canonical),
    }
    if tuple(built) != _C6_STRATEGY_IDS:
        raise LiteratureContractError(
            "c6_strategy_omitted",
            "C6 record must name all six strategies S0–S5.",
            strategies=list(built),
        )
    strategies = {
        name: score_chunks_against_facts(chunks, facts, canonical, strategy=name)
        for name, chunks in built.items()
    }
    return {
        "schema": "dissolve.one-paper-chunk-sweep.v1",
        "checkpoint": "C6",
        "source_pdf_sha256": canonical.get("source_pdf_sha256"),
        "gold_sha256": _GOLD_FACTS_V1_SHA256,
        "strategies_named": list(_C6_STRATEGY_IDS),
        "did_not_invoke_docling": True,
        "did_not_start_C3": True,
        "did_not_start_C7": True,
        "c9_all_six_still_run": True,
        "probe_does_not_prune": (
            "This one-paper sweep does not drop a strategy from C9. "
            "S0–S5 all remain rows at corpus scale regardless of probe numbers."
        ),
        "strategies": strategies,
        "n_facts": len(facts),
        "page6_table_spans": len(_page6_table_spans(canonical)),
    }


def _normalize_reported_unit(unit: str) -> tuple[str, str | None]:
    reported = re.sub(r"\s+", " ", str(unit or "").strip())
    compact = reported.casefold().replace(" ", "").replace("·", "*")
    if (
        not reported
        or not re.match(r"^[A-Za-zµμ°%]", reported)
        or not re.search(r"[A-Za-zµμ°%]", reported)
        or re.search(r"[^A-Za-zµμ°%0-9.*/^_()+\-\s·½]", reported)
        or reported.count("(") != reported.count(")")
        or re.search(r"(?:\*\*|//|\^\^|%%)", compact)
        or re.search(r"[*/^]$", compact)
        or re.search(r"\^(?![+\-]?(?:\d|\.)+)", compact)
        or set(re.findall(r"[A-Za-z]+", reported.casefold())) & {"at", "from", "over", "to"}
    ):
        raise LiteratureContractError(
            "quantity_unit_malformed",
            "Reported quantity unit is malformed; retain one explicit unit token such as C, K, wt%, MPa^0.5, or g/L.",
            unit_reported=reported,
        )
    return reported, _UNIT_CANONICAL.get(compact)


def _canonical_unit(detected: str | None, declared: str | None, *, reported: str | None) -> str | None:
    if detected and declared and detected != declared:
        raise LiteratureContractError(
            "quantity_unit_canonical_mismatch",
            "Declared canonical unit conflicts with the reported unit; conversion belongs in a recorded transformation.",
            unit_reported=reported, detected_canonical=detected, declared_canonical=declared,
        )
    return declared or detected


def _quantity_shell(
    *, property_name: str, shape: str, unit_reported: str | None,
    unit_canonical: str | None, basis: str,
) -> dict[str, Any]:
    if not str(property_name or "").strip():
        raise LiteratureContractError(
            "quantity_property_missing", "Quantity property must be explicit."
        )
    if not str(basis or "").strip():
        raise LiteratureContractError(
            "quantity_basis_missing",
            "Quantity basis must be explicit; do not infer it from a value or unit.",
            property=property_name,
        )
    return {
        "property": str(property_name).strip(), "shape": shape,
        "unit_reported": unit_reported, "unit_canonical": unit_canonical,
        "basis": str(basis).strip(), "value": None, "lower": None, "upper": None,
        "expression": None, "coefficients": {}, "independent_variable": None,
    }


def _literal_number(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _literal_number(node.operand)
        if value is not None:
            return value if isinstance(node.op, ast.UAdd) else -value
    return None


def _parse_reciprocal_function(expression: str) -> tuple[dict[str, float], str]:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise LiteratureContractError(
            "quantity_function_malformed", "Quantity function is not a valid arithmetic expression.",
            expression=expression,
        ) from error
    allowed = (
        ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult,
        ast.Div, ast.Pow, ast.UAdd, ast.USub, ast.Constant, ast.Name, ast.Load,
    )
    unsupported = next((node for node in ast.walk(tree) if not isinstance(node, allowed)), None)
    if unsupported is not None:
        raise LiteratureContractError(
            "quantity_function_unsupported",
            "Only a numeric A + B/T reciprocal function is supported; calls, attributes, and subscripts are forbidden.",
            expression=expression, unsupported_node=type(unsupported).__name__,
        )
    body = tree.body
    if not isinstance(body, ast.BinOp) or not isinstance(body.op, (ast.Add, ast.Sub)):
        raise LiteratureContractError(
            "quantity_function_unsupported",
            "Function must have the reported form A + B/T with numeric A and B.",
            expression=expression,
        )
    coefficient_a = _literal_number(body.left)
    reciprocal = body.right
    if (
        coefficient_a is None
        or not isinstance(reciprocal, ast.BinOp)
        or not isinstance(reciprocal.op, ast.Div)
        or _literal_number(reciprocal.left) is None
        or not isinstance(reciprocal.right, ast.Name)
    ):
        raise LiteratureContractError(
            "quantity_function_unsupported",
            "Function must have the reported form A + B/T with numeric A and B and one named independent variable.",
            expression=expression,
        )
    coefficient_b = float(_literal_number(reciprocal.left))
    if isinstance(body.op, ast.Sub):
        coefficient_b *= -1.0
    return {"A": float(coefficient_a), "B": coefficient_b}, reciprocal.right.id


def parse_quantity(
    text: str, *, property_name: str, basis: str,
    unit_canonical: str | None = None, independent_variable: str | None = None,
) -> dict[str, Any]:
    """Parse one already-located quantity span without scanning surrounding prose."""
    source = str(text or "").strip()
    if not source:
        raise LiteratureContractError(
            "quantity_text_missing", "Quantity text cannot be empty.", property=property_name,
        )
    function_match = _QUANTITY_FUNCTION_RE.fullmatch(source)
    if function_match:
        coefficients, variable = _parse_reciprocal_function(function_match.group(1))
        canonical = unit_canonical or "dimensionless"
        quantity = _quantity_shell(
            property_name=property_name, shape="function", unit_reported=None,
            unit_canonical=canonical, basis=basis,
        )
        quantity.update({
            "expression": "A + B/T", "coefficients": coefficients,
            "independent_variable": independent_variable or (
                "temperature_K" if variable.casefold() in {"t", "temperature"} else variable
            ),
        })
        return quantity

    range_match = _QUANTITY_RANGE_RE.fullmatch(source)
    if range_match:
        lower, unit_left, upper, unit_right = range_match.groups()
        unit_left, unit_right = unit_left.strip(), unit_right.strip()
        if unit_left and unit_right:
            reported_left, canonical_left = _normalize_reported_unit(unit_left)
            reported_right, canonical_right = _normalize_reported_unit(unit_right)
            left_identity = canonical_left or re.sub(r"\s+", "", reported_left.casefold())
            right_identity = canonical_right or re.sub(r"\s+", "", reported_right.casefold())
            if left_identity != right_identity:
                raise LiteratureContractError(
                    "quantity_range_unit_mismatch",
                    "Range endpoints must use the same reported unit or one shared trailing unit.",
                    lower_unit=reported_left, upper_unit=reported_right,
                )
            reported, canonical = reported_right, canonical_right
        elif unit_left or unit_right:
            reported, canonical = _normalize_reported_unit(unit_left or unit_right)
        elif unit_canonical == "dimensionless":
            reported, canonical = None, "dimensionless"
        else:
            raise LiteratureContractError(
                "quantity_unit_missing", "Quantity range is missing its reported unit.",
                text=source,
            )
        lower_value, upper_value = float(lower), float(upper)
        if not all(math.isfinite(value) for value in (lower_value, upper_value)):
            raise LiteratureContractError(
                "quantity_nonfinite", "Quantity range endpoints must be finite.", text=source,
            )
        if lower_value > upper_value:
            raise LiteratureContractError(
                "quantity_range_inverted", "Quantity range lower endpoint exceeds its upper endpoint.",
                lower=lower_value, upper=upper_value,
            )
        quantity = _quantity_shell(
            property_name=property_name, shape="range", unit_reported=reported,
            unit_canonical=_canonical_unit(canonical, unit_canonical, reported=reported), basis=basis,
        )
        quantity.update({"lower": lower_value, "upper": upper_value})
        return quantity

    scalar_match = _QUANTITY_SCALAR_RE.fullmatch(source)
    if not scalar_match:
        raise LiteratureContractError(
            "quantity_syntax_malformed",
            "Quantity must be one scalar, one inclusive range, or an A + B/T function span.",
            text=source,
        )
    value_text, unit_text = scalar_match.groups()
    if unit_text:
        reported, canonical = _normalize_reported_unit(unit_text)
    elif unit_canonical == "dimensionless":
        reported, canonical = None, "dimensionless"
    else:
        raise LiteratureContractError(
            "quantity_unit_missing", "Scalar quantity is missing its reported unit.", text=source,
        )
    value = float(value_text)
    if not math.isfinite(value):
        raise LiteratureContractError(
            "quantity_nonfinite", "Scalar quantity must be finite.", text=source,
        )
    quantity = _quantity_shell(
        property_name=property_name, shape="scalar", unit_reported=reported,
        unit_canonical=_canonical_unit(canonical, unit_canonical, reported=reported), basis=basis,
    )
    quantity["value"] = value
    return quantity


_RECORD_CORE_FIELDS = {
    "library_id", "record_id", "record_class", "status", "method",
    "conditions", "evidence", "extraction_confidence", "extractor",
    "validation_issues", "cross_references",
}
_RECORD_CLASS_FIELDS = {
    "SolubilityPoint": {"polymer", "solvent", "temperature", "solubility"},
    "ChiParameter": {"component_a", "component_b", "chi"},
    "HSPRecord": {"material", "dD", "dP", "dH", "r0"},
    "PartitionRecord": {"contaminant", "phase_a", "phase_b", "metric", "partition_value"},
    "LeachingRecord": {
        "feed_material", "contaminant", "extraction_solvent", "response_metric", "response",
    },
    "TgRecord": {"material", "tg"},
    "ProcessClaim": {
        "patent_document_id", "claim_number", "claim_scope", "legal_effect",
        "operation", "inputs", "claimed_outcome",
    },
    "CompositionClaim": {
        "patent_document_id", "claim_number", "claim_scope", "legal_effect", "components",
    },
}
_NO_EVIDENCE_FIELDS = {
    "library_id", "record_id", "record_class", "status", "query_id", "query",
    "parameter_classes", "document_kinds", "adapter_scope", "date_bounds",
    "corpus_snapshot_sha256", "validated_result_count", "executed_at",
}
_QUANTITY_FIELDS = {
    "property", "shape", "unit_reported", "unit_canonical", "basis", "value",
    "lower", "upper", "expression", "coefficients", "independent_variable",
}
_MATERIAL_FIELDS = {
    "observed_label", "role", "identity_status", "canonical_id", "candidate_ids",
}


def _require_exact_fields(
    value: Any, required: set[str], *, code: str, field: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LiteratureContractError(
            code, f"{field} must be an object.", field=field,
            observed_type=type(value).__name__,
        )
    row = dict(value)
    missing, extra = sorted(required - set(row)), sorted(set(row) - required)
    if missing or extra:
        raise LiteratureContractError(
            code, f"{field} does not match its typed contract.", field=field,
            missing_fields=missing, extra_fields=extra,
        )
    return row


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validate_material_ref(value: Any, field: str) -> dict[str, Any]:
    row = _require_exact_fields(
        value, _MATERIAL_FIELDS, code="literature_material_ref_invalid", field=field,
    )
    status = row["identity_status"]
    candidates = row["candidate_ids"]
    if (
        not isinstance(row["observed_label"], str) or not row["observed_label"].strip()
        or row["role"] not in {"polymer", "solvent", "contaminant", "material", "phase", "component"}
        or status not in {"canonical", "pending", "ambiguous"}
        or not isinstance(candidates, list)
        or any(not isinstance(item, str) or not item for item in candidates)
        or len(set(candidates)) != len(candidates)
        or (status == "canonical" and (not row["canonical_id"] or candidates))
        or (status != "canonical" and row["canonical_id"] is not None)
        or (status == "ambiguous" and len(candidates) < 2)
    ):
        raise LiteratureContractError(
            "literature_material_ref_invalid",
            "Material identity status, canonical ID, and candidate IDs are inconsistent.",
            field=field, identity_status=status,
        )
    return row


def _validate_quantity(value: Any, field: str) -> dict[str, Any]:
    row = _require_exact_fields(
        value, _QUANTITY_FIELDS, code="literature_quantity_invalid", field=field,
    )
    shape = row["shape"]
    coefficients = row["coefficients"]
    if not isinstance(coefficients, Mapping) or any(
        not _finite_number(item) for item in coefficients.values()
    ):
        raise LiteratureContractError(
            "literature_quantity_invalid", "Quantity coefficients must be finite numbers.", field=field,
        )
    populated = {
        name: row[name] is not None for name in ("value", "lower", "upper", "expression", "independent_variable")
    }
    valid_shape = (
        shape == "scalar" and populated["value"] and not any(
            populated[name] for name in ("lower", "upper", "expression", "independent_variable")
        ) and not coefficients
    ) or (
        shape == "range" and populated["lower"] and populated["upper"]
        and not any(populated[name] for name in ("value", "expression", "independent_variable"))
        and not coefficients and _finite_number(row["lower"]) and _finite_number(row["upper"])
        and float(row["lower"]) <= float(row["upper"])
    ) or (
        shape == "function" and populated["expression"] and populated["independent_variable"]
        and not any(populated[name] for name in ("value", "lower", "upper")) and bool(coefficients)
    )
    if (
        shape not in {"scalar", "range", "function"}
        or (shape == "scalar" and not _finite_number(row["value"]))
        or not valid_shape
        or not isinstance(row["property"], str) or not row["property"].strip()
    ):
        raise LiteratureContractError(
            "literature_quantity_invalid",
            "Quantity scalar/range/function fields are inconsistent or non-finite.",
            field=field, shape=shape,
        )
    return row


def _quantity_values(quantity: Mapping[str, Any]) -> list[float]:
    return [
        float(quantity[name]) for name in ("value", "lower", "upper")
        if _finite_number(quantity.get(name))
    ]


def _issue(code: str, message: str, field: str, severity: str = "error") -> dict[str, Any]:
    return {"code": code, "message": message, "field": field, "severity": severity}


def _validate_record_evidence(
    evidence: Any, *, record_id: str, parsed_documents: Sequence[Mapping[str, Any]] | None,
) -> None:
    if not isinstance(evidence, list) or not evidence:
        raise LiteratureContractError(
            "literature_evidence_missing", "Typed positive records require exact evidence.",
            record_id=record_id,
        )
    parsed = {str(row.get("document_id")): row for row in parsed_documents or []}
    seen: set[str] = set()
    for index, item in enumerate(evidence):
        field = f"evidence[{index}]"
        row = _require_exact_fields(
            item,
            {"evidence_id", "document_id", "locator", "verbatim_span", "source_sha256",
             "extraction_confidence", "extraction_method"},
            code="literature_evidence_invalid", field=field,
        )
        evidence_id = str(row["evidence_id"])
        if not evidence_id or evidence_id in seen or not re.fullmatch(r"[0-9a-f]{64}", str(row["source_sha256"])):
            raise LiteratureContractError(
                "literature_evidence_invalid", "Evidence IDs must be unique and source hashes valid.",
                record_id=record_id, field=field,
            )
        seen.add(evidence_id)
        locator = _require_exact_fields(
            row["locator"],
            {"document_id", "block_id", "passage_id", "page", "section_path", "source_scope",
             "table_id", "cell_range", "claim_number"},
            code="literature_evidence_locator_invalid", field=f"{field}.locator",
        )
        if locator["document_id"] != row["document_id"] or not str(row["verbatim_span"]).strip():
            raise LiteratureContractError(
                "literature_evidence_locator_invalid",
                "Evidence document and locator identities must agree and include a verbatim span.",
                record_id=record_id, evidence_id=evidence_id,
            )
        if parsed_documents is None:
            continue
        document = parsed.get(str(row["document_id"]))
        if document is None:
            raise LiteratureContractError(
                "literature_evidence_document_missing",
                "Evidence document is absent from the parsed-document set.",
                record_id=record_id, evidence_id=evidence_id,
            )
        source_hashes = {str(document.get("source_sha256") or "")} | {
            str(attachment.get("sha256") or "") for attachment in document.get("attachments") or []
            if isinstance(attachment, Mapping)
        }
        if row["source_sha256"] not in source_hashes:
            raise LiteratureContractError(
                "literature_evidence_source_hash_mismatch",
                "Evidence source hash is absent from the parsed source and attachments.",
                record_id=record_id, evidence_id=evidence_id,
            )
        if locator["block_id"] is not None:
            block = next((
                block for block in document.get("blocks") or []
                if block.get("block_id") == locator["block_id"]
            ), None)
            if block is None or str(row["verbatim_span"]) not in str(block.get("text") or ""):
                raise LiteratureContractError(
                    "literature_evidence_span_unresolved",
                    "Evidence block and verbatim span must resolve in the normalized parse.",
                    record_id=record_id, evidence_id=evidence_id,
                )


def validate_literature_record(
    record: Mapping[str, Any], *,
    parsed_documents: Sequence[Mapping[str, Any]] | None = None,
    library_id: str | None = None,
) -> dict[str, Any]:
    """Validate one typed extraction and report whether it may ground a positive claim."""
    if not isinstance(record, Mapping):
        raise LiteratureContractError(
            "literature_record_invalid", "Extracted record must be an object.",
            observed_type=type(record).__name__,
        )
    row = dict(record)
    record_class = str(row.get("record_class") or "")
    record_id = str(row.get("record_id") or "")
    if record_class == "NoEvidenceRecord":
        _require_exact_fields(
            row, _NO_EVIDENCE_FIELDS, code="literature_record_contract_invalid", field="record",
        )
        if (
            row.get("status") != "validated" or row.get("validated_result_count") != 0
            or not record_id or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("corpus_snapshot_sha256") or ""))
        ):
            raise LiteratureContractError(
                "literature_no_evidence_contract_invalid",
                "NoEvidenceRecord must capture a validated zero-result scoped search.",
                record_id=record_id,
            )
        if library_id is not None and row.get("library_id") != library_id:
            raise LiteratureContractError(
                "literature_record_library_mismatch", "Record library differs from its extraction batch.",
                record_id=record_id,
            )
        return {
            "schema": "dissolve.record-validation.v1", "record_id": record_id,
            "record_class": record_class, "structurally_valid": True,
            "claim_eligible": True, "violations": [],
        }
    class_fields = _RECORD_CLASS_FIELDS.get(record_class)
    if class_fields is None:
        raise LiteratureContractError(
            "literature_record_class_unsupported",
            "Record class is outside the reviewed nine-class union.", record_class=record_class,
        )
    _require_exact_fields(
        row, _RECORD_CORE_FIELDS | class_fields,
        code="literature_record_contract_invalid", field="record",
    )
    if library_id is not None and row["library_id"] != library_id:
        raise LiteratureContractError(
            "literature_record_library_mismatch", "Record library differs from its extraction batch.",
            record_id=record_id,
        )
    if (
        not record_id or row["status"] not in {
            "validated", "unvalidated_extraction", "pending_identity", "superseded",
        }
        or not _finite_number(row["extraction_confidence"])
        or not 0 <= float(row["extraction_confidence"]) <= 1
        or not isinstance(row["extractor"], str) or not row["extractor"]
    ):
        raise LiteratureContractError(
            "literature_record_core_invalid", "Record identity, lifecycle, or extraction provenance is invalid.",
            record_id=record_id,
        )
    method = _require_exact_fields(
        row["method"], {"method_id", "label", "category"},
        code="literature_method_invalid", field="method",
    )
    if not method["label"] or method["category"] not in {
        "experimental", "model", "calculated", "reported_claim", "unknown",
    }:
        raise LiteratureContractError(
            "literature_method_invalid", "Record method is incomplete or unsupported.", record_id=record_id,
        )
    conditions = _require_exact_fields(
        row["conditions"], {"condition_id", "quantities", "notes"},
        code="literature_conditions_invalid", field="conditions",
    )
    if not isinstance(conditions["quantities"], list) or not isinstance(conditions["notes"], list):
        raise LiteratureContractError(
            "literature_conditions_invalid", "Condition quantities and notes must be arrays.", record_id=record_id,
        )
    for index, quantity in enumerate(conditions["quantities"]):
        _validate_quantity(quantity, f"conditions.quantities[{index}]")
    _validate_record_evidence(row["evidence"], record_id=record_id, parsed_documents=parsed_documents)
    if not isinstance(row["validation_issues"], list) or not isinstance(row["cross_references"], list) or not row["cross_references"]:
        raise LiteratureContractError(
            "literature_record_diagnostics_invalid",
            "Records require validation issue and cross-reference arrays.", record_id=record_id,
        )

    entity_fields = {
        "SolubilityPoint": ("polymer", "solvent"),
        "ChiParameter": ("component_a", "component_b"),
        "HSPRecord": ("material",),
        "PartitionRecord": ("contaminant", "phase_a", "phase_b"),
        "LeachingRecord": ("feed_material", "contaminant", "extraction_solvent"),
        "TgRecord": ("material",),
    }.get(record_class, ())
    entities = [_validate_material_ref(row[field], field) for field in entity_fields]
    if record_class == "ProcessClaim":
        if not isinstance(row["inputs"], list):
            raise LiteratureContractError("literature_claim_inputs_invalid", "Process inputs must be an array.")
        entities = [_validate_material_ref(value, f"inputs[{index}]") for index, value in enumerate(row["inputs"])]
    elif record_class == "CompositionClaim":
        if not isinstance(row["components"], list) or not row["components"]:
            raise LiteratureContractError("literature_composition_components_invalid", "Composition requires components.")
        entities = []
        for index, component in enumerate(row["components"]):
            component_row = _require_exact_fields(
                component, {"material", "role", "amount"},
                code="literature_composition_components_invalid", field=f"components[{index}]",
            )
            entities.append(_validate_material_ref(component_row["material"], f"components[{index}].material"))
            if component_row["amount"] is not None:
                _validate_quantity(component_row["amount"], f"components[{index}].amount")

    quantity_fields = {
        "SolubilityPoint": ("temperature", "solubility"),
        "ChiParameter": ("chi",), "HSPRecord": ("dD", "dP", "dH"),
        "PartitionRecord": ("partition_value",), "LeachingRecord": ("response",),
        "TgRecord": ("tg",),
    }.get(record_class, ())
    quantities = {field: _validate_quantity(row[field], field) for field in quantity_fields}
    if record_class == "HSPRecord" and row["r0"] is not None:
        quantities["r0"] = _validate_quantity(row["r0"], "r0")

    violations: list[dict[str, Any]] = []
    if any(entity["identity_status"] != "canonical" for entity in entities):
        violations.append(_issue(
            "UNRESOLVED_RECORD_IDENTITY", "Positive claims require canonical material identities.", "identity",
        ))
    if any(quantity.get("basis") in {None, ""} for quantity in quantities.values()):
        violations.append(_issue(
            "MISSING_QUANTITY_BASIS", "Reported quantities require an explicit scientific basis.", "basis",
        ))
    if record_class == "SolubilityPoint":
        values = _quantity_values(quantities["solubility"])
        if quantities["solubility"]["unit_canonical"] != "wt_percent" or any(value < 0 or value > 100 for value in values):
            violations.append(_issue(
                "SOLUBILITY_VALUE_INVALID", "Solubility must be 0–100 wt% on the declared solution basis.", "solubility",
            ))
    elif record_class == "ChiParameter" and quantities["chi"]["unit_canonical"] != "dimensionless":
        violations.append(_issue("CHI_UNIT_INVALID", "Flory–Huggins chi is dimensionless.", "chi"))
    elif record_class == "HSPRecord":
        if any(value < 0 for quantity in quantities.values() for value in _quantity_values(quantity)):
            violations.append(_issue("HSP_COMPONENT_NEGATIVE", "HSP components and radius cannot be negative.", "HSP"))
    elif record_class == "PartitionRecord":
        if row["phase_a"]["canonical_id"] == row["phase_b"]["canonical_id"]:
            violations.append(_issue("PARTITION_PHASES_IDENTICAL", "Partition phases must be distinct.", "phase_b"))
    elif record_class == "LeachingRecord":
        if row["response_metric"] == "feed_removal_fraction" and any(
            value < 0 or value > 100 for value in _quantity_values(quantities["response"])
        ):
            violations.append(_issue(
                "LEACHING_RESPONSE_INVALID", "Feed-removal response must remain between 0 and 100 wt%.", "response",
            ))
    elif record_class == "TgRecord":
        unit = quantities["tg"]["unit_canonical"]
        minimum = 0 if unit == "K" else -273.15 if unit == "degC" else None
        if minimum is None or any(value <= minimum for value in _quantity_values(quantities["tg"])):
            violations.append(_issue("TG_ABSOLUTE_TEMPERATURE_INVALID", "Tg must exceed absolute zero.", "tg"))
    elif record_class in {"ProcessClaim", "CompositionClaim"}:
        expected_scope = {
            "claimed_scope": {"independent", "dependent"},
            "non_claim_example": {"description_example"},
            "non_claim_abstract": {"abstract"},
        }.get(row["legal_effect"], set())
        if row["claim_scope"] not in expected_scope:
            violations.append(_issue(
                "PATENT_LEGAL_EFFECT_MISMATCH",
                "Patent claim scope and legal-effect classification are inconsistent.", "legal_effect",
            ))
    claim_eligible = row["status"] == "validated" and not violations
    return {
        "schema": "dissolve.record-validation.v1", "record_id": record_id,
        "record_class": record_class, "structurally_valid": True,
        "claim_eligible": claim_eligible, "violations": violations,
    }


def validate_extraction_batch(
    records: Sequence[Mapping[str, Any]], *, parsed_documents: Sequence[Mapping[str, Any]],
    library_id: str,
) -> dict[str, Any]:
    """Validate all extracted records while retaining honest rejected/pending candidates."""
    validations = [
        validate_literature_record(record, parsed_documents=parsed_documents, library_id=library_id)
        for record in records
    ]
    ids = [item["record_id"] for item in validations]
    if len(ids) != len(set(ids)):
        raise LiteratureContractError(
            "literature_record_ids_duplicate", "Extraction batch record IDs must be unique.",
        )
    invalid_positive = [
        item for record, item in zip(records, validations, strict=True)
        if record.get("status") == "validated" and not item["claim_eligible"]
    ]
    if invalid_positive:
        raise LiteratureContractError(
            "literature_validated_record_semantic_invalid",
            "A record marked validated violates typed scientific semantics.", records=invalid_positive,
        )
    return {
        "schema": "dissolve.extraction-validation.v1", "library_id": library_id,
        "record_count": len(records),
        "claim_eligible_count": sum(item["claim_eligible"] for item in validations),
        "retained_unvalidated_count": sum(not item["claim_eligible"] for item in validations),
        "records": validations,
    }


def _record_entities_and_quantities(record: Mapping[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    record_class = str(record["record_class"])
    entities: list[str] = []
    quantities: list[dict[str, Any]] = []
    for field in {
        "SolubilityPoint": ("polymer", "solvent"),
        "ChiParameter": ("component_a", "component_b"),
        "HSPRecord": ("material",),
        "PartitionRecord": ("contaminant", "phase_a", "phase_b"),
        "LeachingRecord": ("feed_material", "contaminant", "extraction_solvent"),
        "TgRecord": ("material",),
    }.get(record_class, ()):
        entities.append(str(record[field]["canonical_id"]))
    for field in {
        "SolubilityPoint": ("temperature", "solubility"),
        "ChiParameter": ("chi",), "HSPRecord": ("dD", "dP", "dH", "r0"),
        "PartitionRecord": ("partition_value",), "LeachingRecord": ("response",),
        "TgRecord": ("tg",),
    }.get(record_class, ()):
        if record.get(field) is not None:
            quantities.append(dict(record[field]))
    if record_class == "ProcessClaim":
        entities.extend(str(item["canonical_id"]) for item in record["inputs"])
        entities.append(str(record["patent_document_id"]))
    elif record_class == "CompositionClaim":
        entities.extend(str(item["material"]["canonical_id"]) for item in record["components"])
        entities.append(str(record["patent_document_id"]))
        quantities.extend(dict(item["amount"]) for item in record["components"] if item["amount"] is not None)
    quantities.extend(dict(item) for item in (record.get("conditions") or {}).get("quantities") or [])
    return list(dict.fromkeys(entities)), quantities


_GRAPH_TABLES = (
    "kg_ingest_runs", "kg_acquisitions", "kg_documents",
    "kg_parsed_documents", "kg_evidence", "kg_records", "kg_nodes",
    "kg_edges", "kg_summaries",
)


def _canonical_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_payload(value).encode("utf-8")).hexdigest()


def literature_graph_path(library_id: str = "owner-main", *, root: str | Path | None = None) -> Path:
    """Return the durable, library-scoped DuckDB asset path."""
    base = Path(root).expanduser().resolve() if root is not None else _research_root()
    return base / "libraries" / _slug(library_id) / "knowledge.duckdb"


def _ensure_graph_schema(connection: duckdb.DuckDBPyConnection) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS kg_ingest_runs (
            library_id VARCHAR NOT NULL, ingest_run_id VARCHAR NOT NULL,
            idempotency_key VARCHAR NOT NULL, batch_sha256 VARCHAR NOT NULL,
            source_document_ids_json VARCHAR NOT NULL, extractor_ids_json VARCHAR NOT NULL,
            provenance_json VARCHAR NOT NULL DEFAULT '{}',
            status VARCHAR NOT NULL, node_count BIGINT NOT NULL, edge_count BIGINT NOT NULL,
            record_count BIGINT NOT NULL, evidence_count BIGINT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ,
            PRIMARY KEY (library_id, ingest_run_id),
            UNIQUE (library_id, idempotency_key)
        )""",
        """CREATE TABLE IF NOT EXISTS kg_acquisitions (
            library_id VARCHAR NOT NULL, acquisition_id VARCHAR NOT NULL,
            document_id VARCHAR NOT NULL, adapter_name VARCHAR NOT NULL,
            adapter_version VARCHAR NOT NULL, source_sha256 VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL, content_sha256 VARCHAR NOT NULL,
            first_ingest_run_id VARCHAR NOT NULL, created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (library_id, acquisition_id)
        )""",
        """CREATE TABLE IF NOT EXISTS kg_documents (
            library_id VARCHAR NOT NULL, document_id VARCHAR NOT NULL,
            document_kind VARCHAR, canonical_key VARCHAR, label VARCHAR,
            payload_json VARCHAR NOT NULL, content_sha256 VARCHAR NOT NULL,
            first_ingest_run_id VARCHAR NOT NULL, created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (library_id, document_id)
        )""",
        """CREATE TABLE IF NOT EXISTS kg_parsed_documents (
            library_id VARCHAR NOT NULL, parse_id VARCHAR NOT NULL,
            document_id VARCHAR NOT NULL, source_sha256 VARCHAR NOT NULL,
            parser_backend VARCHAR NOT NULL, parser_version VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL, content_sha256 VARCHAR NOT NULL,
            first_ingest_run_id VARCHAR NOT NULL, created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (library_id, parse_id)
        )""",
        """CREATE TABLE IF NOT EXISTS kg_evidence (
            library_id VARCHAR NOT NULL, evidence_id VARCHAR NOT NULL,
            document_id VARCHAR, record_id VARCHAR, payload_json VARCHAR NOT NULL,
            content_sha256 VARCHAR NOT NULL, first_ingest_run_id VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (library_id, evidence_id)
        )""",
        """CREATE TABLE IF NOT EXISTS kg_records (
            library_id VARCHAR NOT NULL, record_id VARCHAR NOT NULL,
            record_class VARCHAR, status VARCHAR, payload_json VARCHAR NOT NULL,
            content_sha256 VARCHAR NOT NULL, first_ingest_run_id VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (library_id, record_id)
        )""",
        """CREATE TABLE IF NOT EXISTS kg_nodes (
            library_id VARCHAR NOT NULL, node_id VARCHAR NOT NULL,
            node_type VARCHAR NOT NULL, canonical_key VARCHAR NOT NULL, label VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL, declared_payload_sha256 VARCHAR NOT NULL,
            content_sha256 VARCHAR NOT NULL, first_ingest_run_id VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (library_id, node_id),
            UNIQUE (library_id, node_type, canonical_key)
        )""",
        """CREATE TABLE IF NOT EXISTS kg_edges (
            library_id VARCHAR NOT NULL, edge_id VARCHAR NOT NULL,
            edge_type VARCHAR NOT NULL, source_node_id VARCHAR NOT NULL,
            target_node_id VARCHAR NOT NULL, record_ids_json VARCHAR NOT NULL,
            evidence_ids_json VARCHAR NOT NULL, attributes_json VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL, declared_payload_sha256 VARCHAR NOT NULL,
            content_sha256 VARCHAR NOT NULL,
            first_ingest_run_id VARCHAR NOT NULL, created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (library_id, edge_id)
        )""",
        """CREATE TABLE IF NOT EXISTS kg_summaries (
            library_id VARCHAR NOT NULL, topic_key VARCHAR NOT NULL,
            summary_json VARCHAR, source_record_ids_json VARCHAR NOT NULL,
            status VARCHAR NOT NULL, stale_since_ingest_run_id VARCHAR,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (library_id, topic_key)
        )""",
    )
    for statement in statements:
        connection.execute(statement)
    connection.execute(
        "ALTER TABLE kg_ingest_runs ADD COLUMN IF NOT EXISTS "
        "provenance_json VARCHAR DEFAULT '{}'"
    )


def _batch_unique(rows: Sequence[Mapping[str, Any]], key: str, code: str) -> None:
    values = [str(row.get(key) or "") for row in rows]
    missing = [index for index, value in enumerate(values) if not value]
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if missing or duplicates:
        raise LiteratureContractError(
            code, f"Graph merge requires non-empty, unique {key} values within one batch.",
            missing_indexes=missing, duplicate_values=duplicates,
        )


def _graph_idempotency_material(batch: Mapping[str, Any]) -> dict[str, Any]:
    parsed = {
        str(row.get("document_id") or ""): row
        for row in batch.get("parsed_documents") or []
        if isinstance(row, Mapping)
    }
    sources = []
    for acquisition in batch.get("acquisitions") or []:
        document = acquisition.get("document") or {}
        document_id = str(document.get("document_id") or "")
        parser = parsed.get(document_id) or {}
        adapter = acquisition.get("adapter") or {}
        sources.append({
            "document_id": document_id,
            "source_sha256": document.get("content_sha256"),
            "adapter": {
                "name": adapter.get("adapter_name"),
                "version": adapter.get("adapter_version"),
            },
            "parser": {
                "backend": parser.get("parser_backend"),
                "version": parser.get("parser_version"),
                "source_sha256": parser.get("source_sha256"),
            },
        })
    return {
        "schema": batch.get("schema"),
        "library_id": batch.get("library_id"),
        "sources": sorted(sources, key=lambda row: row["document_id"]),
        "extractors": sorted(
            [dict(row) for row in batch.get("extractor_manifests") or []],
            key=lambda row: str(row.get("extractor_id") or ""),
        ),
    }


def derive_graph_idempotency_key(batch: Mapping[str, Any]) -> str:
    """Derive one stable merge identity from source and processing provenance."""
    return _payload_sha256(_graph_idempotency_material(batch))


def _graph_batch_parts(batch: Mapping[str, Any]) -> tuple[
    str, str, list[dict[str, Any]], list[dict[str, Any]],
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
    list[dict[str, Any]],
]:
    if batch.get("schema") != "dissolve.graph-merge.v1":
        raise LiteratureContractError(
            "graph_batch_schema_mismatch", "Graph merge requires dissolve.graph-merge.v1."
        )
    try:
        library_id = _slug(str(batch.get("library_id") or ""))
    except ValueError as error:
        raise LiteratureContractError(
            "graph_library_id_invalid", "Graph merge library_id must contain letters or numbers."
        ) from error
    ingest_run_id = str(batch.get("ingest_run_id") or "").strip()
    idempotency_key = str(batch.get("idempotency_key") or "").strip().casefold()
    if not ingest_run_id:
        raise LiteratureContractError(
            "graph_ingest_run_missing", "Graph merge ingest_run_id cannot be empty."
        )
    if not re.fullmatch(r"[0-9a-f]{64}", idempotency_key):
        raise LiteratureContractError(
            "graph_idempotency_key_invalid", "Graph merge idempotency_key must be one SHA-256 hex digest."
        )
    acquisitions = [dict(row) for row in batch.get("acquisitions") or []]
    parsed_documents = [dict(row) for row in batch.get("parsed_documents") or []]
    extractor_manifests = [dict(row) for row in batch.get("extractor_manifests") or []]
    if not acquisitions or not parsed_documents or not extractor_manifests:
        raise LiteratureContractError(
            "graph_provenance_missing",
            "Graph merge requires acquisitions, parsed documents, and extractor manifests."
        )
    acquisition_documents = [dict(row.get("document") or {}) for row in acquisitions]
    _batch_unique(acquisition_documents, "document_id", "graph_acquisition_documents_invalid")
    _batch_unique(parsed_documents, "document_id", "graph_parsed_documents_invalid")
    _batch_unique(extractor_manifests, "extractor_id", "graph_extractor_manifests_invalid")
    acquired = {str(row["document_id"]): row for row in acquisition_documents}
    parsed = {str(row["document_id"]): row for row in parsed_documents}
    if set(acquired) != set(parsed):
        raise LiteratureContractError(
            "graph_acquisition_parse_mismatch",
            "Every acquired document requires exactly one corresponding parsed document.",
            acquired_document_ids=sorted(acquired), parsed_document_ids=sorted(parsed),
        )
    for acquisition, document in zip(acquisitions, acquisition_documents, strict=True):
        document_id = str(document["document_id"])
        parser = parsed[document_id]
        adapter = acquisition.get("adapter") or {}
        source_sha = str(document.get("content_sha256") or "")
        artifact_hashes = {
            str(row.get("sha256") or "")
            for row in acquisition.get("artifacts") or [] if isinstance(row, Mapping)
        }
        if (
            acquisition.get("library_id") != library_id
            or document.get("library_id") != library_id
            or parser.get("library_id") != library_id
        ):
            raise LiteratureContractError(
                "graph_library_scope_mismatch",
                "Acquisition, document, parse, and batch must share one library_id.",
                document_id=document_id,
            )
        if (
            not re.fullmatch(r"[0-9a-f]{64}", source_sha)
            or source_sha not in artifact_hashes
            or parser.get("source_sha256") != source_sha
        ):
            raise LiteratureContractError(
                "graph_source_hash_mismatch",
                "Acquisition artifact, normalized document, and parse source hashes must correspond exactly.",
                document_id=document_id, document_sha256=source_sha,
                parsed_source_sha256=parser.get("source_sha256"),
            )
        if not adapter.get("adapter_name") or not adapter.get("adapter_version"):
            raise LiteratureContractError(
                "graph_adapter_manifest_invalid",
                "Every acquisition requires a named, versioned adapter manifest.",
                document_id=document_id,
            )
    for manifest in extractor_manifests:
        if (
            manifest.get("schema") != "dissolve.extractor-manifest.v1"
            or not manifest.get("name") or not manifest.get("version")
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(manifest.get("configuration_sha256") or "")
            )
            or (
                manifest.get("model_sha256") is not None
                and not re.fullmatch(r"[0-9a-f]{64}", str(manifest["model_sha256"]))
            )
        ):
            raise LiteratureContractError(
                "graph_extractor_manifest_invalid",
                "Extractor manifests require versioned configuration and model provenance.",
                extractor_id=manifest.get("extractor_id"),
            )
    records = [dict(row) for row in batch.get("records") or []]
    nodes = [dict(row) for row in batch.get("nodes") or []]
    edges = [dict(row) for row in batch.get("edges") or []]
    _batch_unique(records, "record_id", "graph_record_ids_invalid")
    _batch_unique(nodes, "node_id", "graph_node_ids_invalid")
    _batch_unique(edges, "edge_id", "graph_edge_ids_invalid")
    validate_extraction_batch(
        records, parsed_documents=parsed_documents, library_id=library_id,
    )
    required_node_fields = {"node_type", "canonical_key", "label", "payload", "payload_sha256"}
    required_edge_fields = {
        "edge_type", "source_node_id", "target_node_id", "record_ids",
        "evidence_ids", "attributes", "payload_sha256",
    }
    for node in nodes:
        missing = sorted(required_node_fields - set(node))
        if missing:
            raise LiteratureContractError(
                "graph_node_fields_missing", "Graph node is missing required storage fields.",
                node_id=node.get("node_id"), fields=missing,
            )
        expected_hash = _payload_sha256(node.get("payload"))
        if node.get("payload_sha256") != expected_hash:
            raise LiteratureContractError(
                "graph_node_payload_hash_mismatch",
                "Graph node payload_sha256 does not match canonical JSON payload.",
                node_id=node.get("node_id"), expected_sha256=expected_hash,
                observed_sha256=node.get("payload_sha256"),
            )
    for edge in edges:
        missing = sorted(required_edge_fields - set(edge))
        if missing:
            raise LiteratureContractError(
                "graph_edge_fields_missing", "Graph edge is missing required storage fields.",
                edge_id=edge.get("edge_id"), fields=missing,
            )
        expected_hash = _payload_sha256(edge.get("attributes"))
        if edge.get("payload_sha256") != expected_hash:
            raise LiteratureContractError(
                "graph_edge_payload_hash_mismatch",
                "Graph edge payload_sha256 does not match canonical JSON attributes.",
                edge_id=edge.get("edge_id"), expected_sha256=expected_hash,
                observed_sha256=edge.get("payload_sha256"),
            )
    canonical_keys = [(str(row.get("node_type")), str(row.get("canonical_key"))) for row in nodes]
    duplicates = sorted(key for key, count in Counter(canonical_keys).items() if count > 1)
    if duplicates:
        raise LiteratureContractError(
            "graph_node_canonical_keys_invalid",
            "One batch cannot assign multiple nodes to the same typed canonical key.",
            duplicate_keys=duplicates,
        )
    expected_idempotency = derive_graph_idempotency_key(batch)
    if idempotency_key != expected_idempotency:
        raise LiteratureContractError(
            "graph_idempotency_key_mismatch",
            "Graph merge idempotency_key does not match canonical source/parser/extractor provenance.",
            expected_sha256=expected_idempotency, observed_sha256=idempotency_key,
        )
    return (
        library_id, ingest_run_id, acquisitions, parsed_documents,
        extractor_manifests, records, nodes, edges,
    )


def _existing_row(
    connection: duckdb.DuckDBPyConnection, table: str, library_id: str,
    id_field: str, identifier: str, fields: Sequence[str],
) -> dict[str, Any] | None:
    if table not in _GRAPH_TABLES:
        raise ValueError(f"unknown graph table: {table}")
    row = connection.execute(
        f"SELECT {', '.join(fields)} FROM {table} WHERE library_id = ? AND {id_field} = ?",
        [library_id, identifier],
    ).fetchone()
    return dict(zip(fields, row)) if row else None


def _insert_immutable(
    connection: duckdb.DuckDBPyConnection, *, table: str, library_id: str,
    id_field: str, identifier: str, payload: Mapping[str, Any],
    conflict_code: str, columns: Sequence[str], values: Sequence[Any],
) -> bool:
    body = _canonical_payload(payload)
    content_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    existing = _existing_row(
        connection, table, library_id, id_field, identifier,
        ["content_sha256", "payload_json"],
    )
    if existing:
        if existing["content_sha256"] != content_sha or existing["payload_json"] != body:
            raise LiteratureContractError(
                conflict_code,
                f"Existing {table} identity has different immutable content; retain it under a new explicit identity or conflict edge.",
                identifier=identifier, existing_sha256=existing["content_sha256"],
                incoming_sha256=content_sha,
            )
        return False
    placeholders = ", ".join("?" for _ in range(len(columns) + 2))
    connection.execute(
        f"INSERT INTO {table} (library_id, {', '.join(columns)}, content_sha256) VALUES ({placeholders})",
        [library_id, *values, content_sha],
    )
    return True


def merge_literature_graph(
    batch: Mapping[str, Any], *, root: str | Path | None = None,
) -> dict[str, Any]:
    """Incrementally merge one GraphMergeBatch without rebuilding other library state."""
    (
        library_id, ingest_run_id, acquisitions, parsed_documents,
        extractor_manifests, records, nodes, edges,
    ) = _graph_batch_parts(batch)
    path = literature_graph_path(library_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path))
    try:
        _ensure_graph_schema(connection)
        prior = connection.execute(
            "SELECT ingest_run_id, batch_sha256, status FROM kg_ingest_runs "
            "WHERE library_id = ? AND idempotency_key = ?",
            [library_id, str(batch["idempotency_key"]).casefold()],
        ).fetchone()
        batch_sha = _payload_sha256(batch)
        prior_run = connection.execute(
            "SELECT idempotency_key, batch_sha256 FROM kg_ingest_runs "
            "WHERE library_id = ? AND ingest_run_id = ?",
            [library_id, ingest_run_id],
        ).fetchone()
        if prior_run and (prior_run[0] != str(batch["idempotency_key"]).casefold() or prior_run[1] != batch_sha):
            raise LiteratureContractError(
                "graph_ingest_run_conflict",
                "An ingest_run_id was reused for a different graph batch.",
                ingest_run_id=ingest_run_id,
            )
        if prior:
            if prior[1] != batch_sha:
                raise LiteratureContractError(
                    "graph_idempotency_conflict",
                    "An idempotency key was reused for a different graph batch.",
                    existing_ingest_run_id=prior[0], existing_batch_sha256=prior[1],
                    incoming_batch_sha256=batch_sha,
                )
            return {
                "schema": "dissolve.graph-merge-result.v1", "library_id": library_id,
                "ingest_run_id": prior[0], "status": "idempotent",
                "inserted": {
                    "acquisitions": 0, "documents": 0, "parsed_documents": 0,
                    "evidence": 0, "records": 0, "nodes": 0, "edges": 0,
                },
                "summaries_marked_stale": 0, "graph_path": str(path),
            }
        batch_node_ids = {str(row["node_id"]) for row in nodes}
        stored_node_ids = {
            row[0] for row in connection.execute(
                "SELECT node_id FROM kg_nodes WHERE library_id = ?", [library_id],
            ).fetchall()
        }
        missing_endpoints = sorted({
            endpoint
            for edge in edges
            for endpoint in (str(edge.get("source_node_id") or ""), str(edge.get("target_node_id") or ""))
            if endpoint not in batch_node_ids and endpoint not in stored_node_ids
        })
        if missing_endpoints:
            raise LiteratureContractError(
                "graph_edge_endpoint_missing", "Graph edges reference nodes absent from the batch and library.",
                node_ids=missing_endpoints,
            )

        connection.execute("BEGIN TRANSACTION")
        now = _now()
        source_document_ids = sorted(
            str((row.get("document") or {}).get("document_id"))
            for row in acquisitions
        )
        extractor_ids = sorted(str(row["extractor_id"]) for row in extractor_manifests)
        provenance = _graph_idempotency_material(batch)
        connection.execute(
            "INSERT INTO kg_ingest_runs (library_id, ingest_run_id, idempotency_key, "
            "batch_sha256, source_document_ids_json, extractor_ids_json, provenance_json, "
            "status, node_count, edge_count, record_count, evidence_count, started_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [library_id, ingest_run_id, str(batch["idempotency_key"]).casefold(), batch_sha,
             _canonical_payload(source_document_ids), _canonical_payload(extractor_ids),
             _canonical_payload(provenance), "running", 0, 0, 0, 0, now, None],
        )
        inserted = {
            "acquisitions": 0, "documents": 0, "parsed_documents": 0,
            "evidence": 0, "records": 0, "nodes": 0, "edges": 0,
        }

        for acquisition in acquisitions:
            document = dict(acquisition["document"])
            document_id = str(document["document_id"])
            adapter = acquisition["adapter"]
            acquisition_id = _payload_sha256(acquisition)
            inserted["acquisitions"] += int(_insert_immutable(
                connection, table="kg_acquisitions", library_id=library_id,
                id_field="acquisition_id", identifier=acquisition_id,
                payload=acquisition, conflict_code="graph_acquisition_conflict",
                columns=("acquisition_id", "document_id", "adapter_name", "adapter_version",
                         "source_sha256", "payload_json", "first_ingest_run_id", "created_at"),
                values=(acquisition_id, document_id, adapter["adapter_name"],
                        adapter["adapter_version"], document["content_sha256"],
                        _canonical_payload(acquisition), ingest_run_id, now),
            ))
            identifiers = document.get("source_identifiers") or {}
            canonical_key = next((
                f"{key}:{value}" for key, value in sorted(identifiers.items()) if value
            ), f"document:{document_id}")
            inserted["documents"] += int(_insert_immutable(
                connection, table="kg_documents", library_id=library_id,
                id_field="document_id", identifier=document_id, payload=document,
                conflict_code="graph_document_conflict",
                columns=("document_id", "document_kind", "canonical_key", "label", "payload_json", "first_ingest_run_id", "created_at"),
                values=(document_id, document.get("document_kind"), canonical_key,
                        document.get("title"), _canonical_payload(document),
                        ingest_run_id, now),
            ))

        for parsed_document in parsed_documents:
            parse_id = _payload_sha256({
                "document_id": parsed_document["document_id"],
                "source_sha256": parsed_document["source_sha256"],
                "parser_backend": parsed_document["parser_backend"],
                "parser_version": parsed_document["parser_version"],
            })
            inserted["parsed_documents"] += int(_insert_immutable(
                connection, table="kg_parsed_documents", library_id=library_id,
                id_field="parse_id", identifier=parse_id, payload=parsed_document,
                conflict_code="graph_parsed_document_conflict",
                columns=("parse_id", "document_id", "source_sha256", "parser_backend",
                         "parser_version", "payload_json", "first_ingest_run_id", "created_at"),
                values=(parse_id, parsed_document["document_id"],
                        parsed_document["source_sha256"], parsed_document["parser_backend"],
                        parsed_document["parser_version"], _canonical_payload(parsed_document),
                        ingest_run_id, now),
            ))

        for record in records:
            record_id = str(record["record_id"])
            inserted["records"] += int(_insert_immutable(
                connection, table="kg_records", library_id=library_id,
                id_field="record_id", identifier=record_id, payload=record,
                conflict_code="graph_record_conflict",
                columns=("record_id", "record_class", "status", "payload_json", "first_ingest_run_id", "created_at"),
                values=(record_id, record.get("record_class"), record.get("status"),
                        _canonical_payload(record), ingest_run_id, now),
            ))
            for evidence in record.get("evidence") or []:
                evidence_id = str(evidence.get("evidence_id") or "")
                if not evidence_id:
                    raise LiteratureContractError(
                        "graph_evidence_id_missing", "Record evidence must carry evidence_id before graph merge.",
                        record_id=record_id,
                    )
                inserted["evidence"] += int(_insert_immutable(
                    connection, table="kg_evidence", library_id=library_id,
                    id_field="evidence_id", identifier=evidence_id, payload=evidence,
                    conflict_code="graph_evidence_conflict",
                    columns=("evidence_id", "document_id", "record_id", "payload_json", "first_ingest_run_id", "created_at"),
                    values=(evidence_id, evidence.get("document_id"), record_id,
                            _canonical_payload(evidence), ingest_run_id, now),
                ))

        known_evidence = {
            row[0] for row in connection.execute(
                "SELECT evidence_id FROM kg_evidence WHERE library_id = ?", [library_id],
            ).fetchall()
        }
        missing_evidence_ids = sorted({
            str(value) for edge in edges for value in edge.get("evidence_ids") or []
        } - known_evidence)
        if missing_evidence_ids:
            raise LiteratureContractError(
                "graph_edge_evidence_missing",
                "Graph edge references evidence absent from the batch and library.",
                evidence_ids=missing_evidence_ids,
            )
        stored_record_ids = {
            row[0] for row in connection.execute(
                "SELECT record_id FROM kg_records WHERE library_id = ?", [library_id],
            ).fetchall()
        }
        missing_record_ids = sorted({
            str(value) for edge in edges for value in edge.get("record_ids") or []
        } - stored_record_ids)
        if missing_record_ids:
            raise LiteratureContractError(
                "graph_edge_record_missing",
                "Graph edge references records absent from the batch and library.",
                record_ids=missing_record_ids,
            )

        for node in nodes:
            node_id = str(node["node_id"])
            canonical_collision = connection.execute(
                "SELECT node_id FROM kg_nodes WHERE library_id = ? AND node_type = ? AND canonical_key = ?",
                [library_id, node.get("node_type"), node.get("canonical_key")],
            ).fetchone()
            if canonical_collision and canonical_collision[0] != node_id:
                raise LiteratureContractError(
                    "graph_node_canonical_conflict",
                    "Typed canonical key already belongs to a different node identity.",
                    canonical_key=node.get("canonical_key"), existing_node_id=canonical_collision[0],
                    incoming_node_id=node_id,
                )
            inserted["nodes"] += int(_insert_immutable(
                connection, table="kg_nodes", library_id=library_id,
                id_field="node_id", identifier=node_id, payload=node,
                conflict_code="graph_node_conflict",
                columns=("node_id", "node_type", "canonical_key", "label", "payload_json", "declared_payload_sha256", "first_ingest_run_id", "created_at"),
                values=(node_id, node.get("node_type"), node.get("canonical_key"), node.get("label"),
                        _canonical_payload(node), node.get("payload_sha256"), ingest_run_id, now),
            ))

        for edge in edges:
            edge_id = str(edge["edge_id"])
            inserted["edges"] += int(_insert_immutable(
                connection, table="kg_edges", library_id=library_id,
                id_field="edge_id", identifier=edge_id, payload=edge,
                conflict_code="graph_edge_conflict",
                columns=("edge_id", "edge_type", "source_node_id", "target_node_id",
                         "record_ids_json", "evidence_ids_json", "attributes_json", "payload_json",
                         "declared_payload_sha256",
                         "first_ingest_run_id", "created_at"),
                values=(edge_id, edge.get("edge_type"), edge.get("source_node_id"), edge.get("target_node_id"),
                        _canonical_payload(edge.get("record_ids") or []),
                        _canonical_payload(edge.get("evidence_ids") or []),
                        _canonical_payload(edge.get("attributes") or {}), _canonical_payload(edge),
                        edge.get("payload_sha256"),
                        ingest_run_id, now),
            ))

        stale_count = 0
        for topic in sorted(set(str(value) for value in batch.get("summary_topics_touched") or [])):
            existing = connection.execute(
                "SELECT topic_key FROM kg_summaries WHERE library_id = ? AND topic_key = ?",
                [library_id, topic],
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE kg_summaries SET status = 'stale', stale_since_ingest_run_id = ?, updated_at = ? "
                    "WHERE library_id = ? AND topic_key = ?",
                    [ingest_run_id, now, library_id, topic],
                )
            else:
                connection.execute(
                    "INSERT INTO kg_summaries VALUES (?, ?, NULL, '[]', 'stale', ?, ?)",
                    [library_id, topic, ingest_run_id, now],
                )
            stale_count += 1
        connection.execute(
            "UPDATE kg_ingest_runs SET status = 'complete', node_count = ?, edge_count = ?, "
            "record_count = ?, evidence_count = ?, completed_at = ? "
            "WHERE library_id = ? AND ingest_run_id = ?",
            [inserted["nodes"], inserted["edges"], inserted["records"], inserted["evidence"],
             _now(), library_id, ingest_run_id],
        )
        connection.execute("COMMIT")
        return {
            "schema": "dissolve.graph-merge-result.v1", "library_id": library_id,
            "ingest_run_id": ingest_run_id, "status": "merged", "inserted": inserted,
            "deferred_references": {"record_ids": [], "evidence_ids": []},
            "summaries_marked_stale": stale_count, "graph_path": str(path),
        }
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise
    finally:
        connection.close()


def inspect_literature_graph(
    library_id: str = "owner-main", *, root: str | Path | None = None,
) -> dict[str, Any]:
    """Return bounded storage counts for one named library without creating it."""
    scoped = _slug(library_id)
    path = literature_graph_path(scoped, root=root)
    if not path.is_file():
        return {
            "schema": "dissolve.graph-status.v1", "library_id": scoped,
            "graph_path": str(path), "exists": False,
            "counts": {name: 0 for name in _GRAPH_TABLES}, "stale_topics": [],
        }
    connection = duckdb.connect(str(path), read_only=True)
    try:
        counts = {
            table: connection.execute(
                f"SELECT count(*) FROM {table} WHERE library_id = ?", [scoped],
            ).fetchone()[0]
            for table in _GRAPH_TABLES
        }
        stale_topics = [
            row[0] for row in connection.execute(
                "SELECT topic_key FROM kg_summaries WHERE library_id = ? AND status = 'stale' ORDER BY topic_key",
                [scoped],
            ).fetchall()
        ]
    finally:
        connection.close()
    return {
        "schema": "dissolve.graph-status.v1", "library_id": scoped,
        "graph_path": str(path), "exists": True, "counts": counts,
        "stale_topics": stale_topics,
    }


def _date_parts(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            year = re.search(r"\b(?:18|19|20|21)\d{2}\b", value)
            return f"{year.group()}-01-01" if year else None
    if isinstance(value, Mapping):
        parts = value.get("date-parts") or value.get("date_parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list):
            parts = parts[0]
        if isinstance(parts, list) and parts:
            year, month, day = int(parts[0]), int(parts[1] if len(parts) > 1 else 1), int(parts[2] if len(parts) > 2 else 1)
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                return None
    return None


def normalize_recorded_metadata_response(source: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize recorded provider-shaped metadata without making a provider call."""
    source_key = str(source or "").casefold().replace("-", "_")
    rows: list[dict[str, Any]] = []
    if source_key == "crossref":
        native_rows = (payload.get("message") or {}).get("items") or []
        for item in native_rows:
            titles = item.get("title") or []
            title = titles[0] if isinstance(titles, list) and titles else titles
            authors = [
                " ".join(part for part in (str(row.get("given") or "").strip(), str(row.get("family") or "").strip()) if part)
                for row in item.get("author") or []
            ]
            published = _date_parts(item.get("published-print") or item.get("published-online") or item.get("issued"))
            rows.append({
                "document_kind": "paper", "source": "crossref",
                "source_record_id": str(item.get("DOI") or item.get("URL") or ""),
                "title": _clean(title, 500),
                "abstract": _clean(item.get("abstract"), 2_000) or None,
                "authors": [value for value in authors if value],
                "published_date": published, "updated_date": _date_parts(item.get("indexed", {}).get("date-time")),
                "doi": str(item.get("DOI") or "").casefold() or None,
                "arxiv_id": None, "patent_number": None, "family_id": None,
                "url": item.get("URL"),
            })
    elif source_key in {"semantic_scholar", "semanticscholar"}:
        for item in payload.get("data") or []:
            identifiers = item.get("externalIds") or {}
            rows.append({
                "document_kind": "paper", "source": "semantic_scholar",
                "source_record_id": str(item.get("paperId") or ""),
                "title": _clean(item.get("title"), 500),
                "abstract": _clean(item.get("abstract"), 2_000) or None,
                "authors": [_clean(row.get("name"), 160) for row in item.get("authors") or [] if row.get("name")],
                "published_date": _date_parts(item.get("publicationDate") or str(item.get("year") or "")),
                "updated_date": None,
                "doi": str(identifiers.get("DOI") or "").casefold() or None,
                "arxiv_id": str(identifiers.get("ArXiv") or "") or None,
                "patent_number": None, "family_id": None, "url": item.get("url"),
            })
    elif source_key in {"uspto", "patentsview"}:
        native_rows = payload.get("patents") or payload.get("results") or []
        for item in native_rows:
            patent_number = str(item.get("patent_number") or item.get("patent_id") or "")
            rows.append({
                "document_kind": "patent", "source": source_key,
                "source_record_id": patent_number,
                "title": _clean(item.get("patent_title") or item.get("title"), 500),
                "abstract": _clean(item.get("patent_abstract") or item.get("abstract"), 2_000) or None,
                "authors": [str(value) for value in item.get("inventors") or []],
                "published_date": _date_parts(item.get("publication_date") or item.get("patent_date")),
                "updated_date": _date_parts(item.get("updated_date")),
                "doi": None, "arxiv_id": None,
                "patent_number": patent_number or None,
                "family_id": str(item.get("family_id") or "") or None,
                "priority_dates": sorted(set(str(value) for value in item.get("priority_dates") or [])),
                "url": item.get("url"),
            })
    else:
        raise LiteratureContractError(
            "metadata_source_unsupported",
            "Recorded metadata source must be crossref, semantic_scholar, uspto, or patentsview.",
            source=source,
        )
    invalid = [index for index, row in enumerate(rows) if not row["title"] or not row["source_record_id"]]
    if invalid:
        raise LiteratureContractError(
            "metadata_record_identity_missing",
            "Recorded metadata rows require a title and source-native record identity.",
            source=source_key, indexes=invalid,
        )
    return rows


_METADATA_STRONG_IDENTITY_ORDER = ("patent_family", "doi", "arxiv", "patent")


def _metadata_identities(row: Mapping[str, Any]) -> dict[str, str]:
    """Return every usable identity rather than selecting one lossy winner."""
    kind = str(row.get("document_kind") or "unknown")
    identities: dict[str, str] = {}
    if row.get("family_id"):
        identities["patent_family"] = re.sub(
            r"[^A-Z0-9]", "", str(row["family_id"]).upper(),
        )
    if row.get("doi"):
        identities["doi"] = (
            str(row["doi"]).casefold()
            .removeprefix("https://doi.org/").removeprefix("doi:")
        )
    if row.get("arxiv_id"):
        identities["arxiv"] = re.sub(
            r"v\d+$", "", str(row["arxiv_id"]).casefold(),
        )
    if row.get("patent_number"):
        identities["patent"] = re.sub(
            r"[^A-Z0-9]", "", str(row["patent_number"]).upper(),
        )
    title = " ".join(_tokens(str(row.get("title") or "")))
    year = str(row.get("published_date") or "")[:4]
    identities[f"{kind}_title_year"] = f"{title}|{year}"
    return {basis: value for basis, value in identities.items() if value}


def _metadata_components(rows: Sequence[Mapping[str, Any]]) -> list[list[int]]:
    """Link compatible metadata identities while keeping ambiguous titles split."""
    identities = [_metadata_identities(row) for row in rows]
    parents = list(range(len(rows)))
    component_strong = [{
        basis: {identity[basis]} for basis in _METADATA_STRONG_IDENTITY_ORDER
        if identity.get(basis)
    } for identity in identities]

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> bool:
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return True
        shared_family = bool(
            component_strong[left_root].get("patent_family", set())
            & component_strong[right_root].get("patent_family", set())
        )
        for basis in _METADATA_STRONG_IDENTITY_ORDER:
            values = (
                component_strong[left_root].get(basis, set())
                | component_strong[right_root].get(basis, set())
            )
            # Different jurisdiction/publication numbers are expected members
            # of one explicitly shared patent family, not an identity conflict.
            if basis == "patent" and shared_family:
                continue
            if len(values) > 1:
                return False
        parents[right_root] = left_root
        for basis, values in component_strong[right_root].items():
            component_strong[left_root].setdefault(basis, set()).update(values)
        return True

    # Exact durable identifiers are authoritative links only when another
    # durable identifier of the same namespace does not contradict the link.
    strong_buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, identity in enumerate(identities):
        for basis in _METADATA_STRONG_IDENTITY_ORDER:
            if identity.get(basis):
                strong_buckets[(basis, identity[basis])].append(index)
    for bucket in strong_buckets.values():
        for index in bucket[1:]:
            union(bucket[0], index)

    weak_buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, identity in enumerate(identities):
        for basis, value in identity.items():
            if basis not in _METADATA_STRONG_IDENTITY_ORDER:
                weak_buckets[(basis, value)].append(index)
    for bucket in weak_buckets.values():
        roots = sorted({find(index) for index in bucket})
        if len(roots) < 2:
            continue
        root_members = {
            root: [index for index in range(len(rows)) if find(index) == root]
            for root in roots
        }
        strong_values = {
            basis: {
                identities[index][basis]
                for members in root_members.values() for index in members
                if identities[index].get(basis)
            }
            for basis in _METADATA_STRONG_IDENTITY_ORDER
        }
        # A title/year is useful corroboration only when it does not point at
        # multiple durable identifiers of the same kind. In the ambiguous case
        # even an identifier-free row remains separate instead of being joined
        # to whichever provider happened to sort first.
        if any(len(values) > 1 for values in strong_values.values()):
            continue
        for root in roots[1:]:
            union(roots[0], root)

    grouped: dict[int, list[int]] = defaultdict(list)
    for index in range(len(rows)):
        grouped[find(index)].append(index)
    return sorted(grouped.values(), key=lambda members: min(members))


def _metadata_component_identity(
    group: Sequence[Mapping[str, Any]],
) -> tuple[str, str, list[str]]:
    identities = [_metadata_identities(row) for row in group]
    keys = sorted({
        f"{basis}:{value}"
        for identity in identities for basis, value in identity.items()
    })
    for basis in _METADATA_STRONG_IDENTITY_ORDER:
        values = sorted({identity[basis] for identity in identities if identity.get(basis)})
        if values:
            return basis, values[0], keys
    basis, value = next(iter(identities[0].items()))
    return basis, value, keys


def _metadata_date(value: Any) -> date | None:
    normalized = _date_parts(value)
    return date.fromisoformat(normalized) if normalized else None


def merge_rank_literature_metadata(
    rows: Sequence[Mapping[str, Any]], *, query: str = "",
    date_from: str | None = None, date_through: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    """Deduplicate recorded metadata and rank it with explicit date handling."""
    try:
        lower = date.fromisoformat(date_from) if date_from else None
        upper = date.fromisoformat(date_through) if date_through else None
    except ValueError as error:
        raise LiteratureContractError(
            "metadata_date_bound_invalid", "Metadata date bounds must be ISO dates (YYYY-MM-DD)."
        ) from error
    if lower and upper and lower > upper:
        raise LiteratureContractError(
            "metadata_date_bounds_inverted", "Metadata date_from exceeds date_through."
        )
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1_000:
        raise LiteratureContractError(
            "metadata_limit_invalid", "Metadata merge limit must be an integer from 1 through 1000."
        )
    included, excluded = [], []
    for raw in rows:
        row = dict(raw)
        if not row.get("title") or not row.get("source") or not row.get("source_record_id"):
            raise LiteratureContractError(
                "metadata_record_identity_missing",
                "Normalized metadata requires title, source, and source_record_id.",
            )
        published = _metadata_date(row.get("published_date"))
        if (lower or upper) and published is None:
            excluded.append({
                "source": row["source"], "source_record_id": row["source_record_id"],
                "reason": "publication_date_unavailable_for_explicit_bound",
            })
            continue
        if lower and published and published < lower:
            excluded.append({"source": row["source"], "source_record_id": row["source_record_id"], "reason": "before_date_from"})
            continue
        if upper and published and published > upper:
            excluded.append({"source": row["source"], "source_record_id": row["source_record_id"], "reason": "after_date_through"})
            continue
        included.append(row)

    merged = []
    variant_fields = ("title", "abstract", "published_date", "updated_date", "doi", "arxiv_id", "patent_number", "family_id", "url")
    components = _metadata_components(included)
    component_groups = [[included[index] for index in members] for members in components]
    identified_groups = [(*_metadata_component_identity(group), group) for group in component_groups]
    for basis, key, identity_keys, group in sorted(
        identified_groups, key=lambda item: (item[0], item[1]),
    ):
        ranked_group = sorted(
            group,
            key=lambda row: (
                -sum(value not in (None, "", []) for value in row.values()),
                -len(str(row.get("abstract") or "")),
                str(row.get("source")), str(row.get("source_record_id")),
            ),
        )
        primary = ranked_group[0]
        variants = {
            field: sorted({_canonical_payload(row.get(field)) for row in group if row.get(field) not in (None, "", [])})
            for field in variant_fields
        }
        variants = {
            field: [json.loads(value) for value in values]
            for field, values in variants.items() if len(values) > 1
        }
        merged.append({
            "document_key": f"{basis}:{key}", "dedup_basis": basis,
            "identity_keys": identity_keys,
            **{field: primary.get(field) for field in variant_fields},
            "document_kind": primary.get("document_kind"),
            "authors": sorted({str(author) for row in group for author in row.get("authors") or []}),
            "sources": sorted({str(row["source"]) for row in group}),
            "source_records": [
                {"source": row["source"], "source_record_id": row["source_record_id"],
                 "normalized_sha256": _payload_sha256(row)}
                for row in sorted(group, key=lambda value: (str(value["source"]), str(value["source_record_id"])))
            ],
            "field_variants": variants,
            "metadata_conflict": bool(variants),
        })

    query_tokens = set(_tokens(query))
    known_dates = [_metadata_date(row.get("published_date")) for row in merged]
    anchor = upper or max((value for value in known_dates if value), default=date(2000, 1, 1))
    recency_intent = bool(query_tokens & {"current", "latest", "recent", "today", "review"})
    for row in merged:
        searchable = set(_tokens(" ".join([
            str(row.get("title") or ""), str(row.get("abstract") or ""),
            " ".join(row.get("authors") or []),
        ])))
        relevance = len(query_tokens & searchable) / len(query_tokens) if query_tokens else 0.0
        published = _metadata_date(row.get("published_date"))
        age_years = max(0.0, (anchor - published).days / 365.25) if published else 100.0
        recency = 1.0 / (1.0 + age_years / 5.0) if published else 0.0
        completeness = min(1.0, sum(row.get(field) not in (None, "", []) for field in variant_fields) / 6.0)
        weights = (0.70, 0.25, 0.05) if recency_intent else (0.85, 0.10, 0.05)
        row["ranking"] = {
            "query_relevance": round(relevance, 6), "date_recency": round(recency, 6),
            "metadata_completeness": round(completeness, 6),
            "score": round(weights[0] * relevance + weights[1] * recency + weights[2] * completeness, 6),
            "date_anchor": anchor.isoformat(), "recency_intent": recency_intent,
        }
    merged.sort(key=lambda row: (-row["ranking"]["score"], row["document_key"]))
    return {
        "schema": "dissolve.metadata-merge.v1", "query": query,
        "date_bounds": {"from": date_from, "through": date_through},
        "input_count": len(rows), "included_count": len(included),
        "deduplicated_count": len(merged), "excluded": excluded,
        "results": merged[:limit], "truncated": len(merged) > limit,
    }


def _index_path(knowledgebase: str) -> Path:
    return _research_root() / f"{_slug(knowledgebase)}.json.gz"


def _empty_index(knowledgebase: str) -> dict[str, Any]:
    return {"schema": _INDEX_SCHEMA, "knowledgebase": _slug(knowledgebase), "documents": [], "chunks": [], "dense": None}


def _load_index(knowledgebase: str) -> dict[str, Any]:
    path = _index_path(knowledgebase)
    if not path.exists():
        return _empty_index(knowledgebase)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema") != _INDEX_SCHEMA or payload.get("knowledgebase") != _slug(knowledgebase):
        raise ValueError("unsupported or mismatched literature index")
    return payload


def _save_index(index: dict[str, Any]) -> Path:
    path = _index_path(index["knowledgebase"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    body = json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    with temporary.open("wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as compressed:
            compressed.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    return path


def _pdf_pages(data: bytes) -> list[dict[str, Any]]:
    try:
        reader_type = importlib.import_module("pypdf").PdfReader
    except (ImportError, AttributeError) as error:
        raise RuntimeError("PDF ingestion requires pip install '.[research]'.") from error
    reader = reader_type(io.BytesIO(data))
    return [{"page": index + 1, "text": page.extract_text() or ""} for index, page in enumerate(reader.pages)]


def _json_documents(data: bytes, source: str) -> list[dict[str, Any]]:
    text = data.decode("utf-8")
    if source.casefold().endswith(".jsonl"):
        value: Any = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        value = json.loads(text)
    if isinstance(value, dict) and value.get("schema") == _CANONICAL_DOCUMENT_SCHEMA:
        return [{
            "title": Path(urlparse(source).path).stem or source,
            "source": source,
            "canonical_document": value,
            "url": value.get("url"), "doi": value.get("doi"), "year": value.get("year"),
        }]
    if isinstance(value, dict) and isinstance(value.get("documents"), list):
        value = value["documents"]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("JSON corpus input must be a document object or list")
    records = []
    for item in value:
        if not isinstance(item, dict):
            continue
        content = item.get("text") or item.get("content") or item.get("abstract")
        if not content:
            continue
        records.append({
            "title": _clean(item.get("title") or source, 300), "source": source,
            "url": item.get("url"), "doi": item.get("doi"), "year": item.get("year"),
            "pages": [{"page": item.get("page"), "text": str(content)}],
        })
    return records


def _records_from_bytes(data: bytes, source: str) -> list[dict[str, Any]]:
    suffix = Path(urlparse(source).path).suffix.casefold()
    if data.startswith(b"%PDF") or suffix == ".pdf":
        return [{"title": Path(urlparse(source).path).stem or source, "source": source, "pages": _pdf_pages(data)}]
    if suffix in {".json", ".jsonl"}:
        return _json_documents(data, source)
    if suffix not in _TEXT_SUFFIXES and suffix:
        raise ValueError(f"unsupported document type: {suffix}")
    text = data.decode("utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        text = re.sub(r"<[^>]+>", " ", text)
    return [{"title": Path(urlparse(source).path).stem or source, "source": source, "pages": [{"page": None, "text": text}]}]


def _expand_paths(paths: list[str], maximum: int) -> list[Path]:
    expanded: list[Path] = []
    allowed = _TEXT_SUFFIXES | {".pdf", ".json", ".jsonl"}
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            expanded.extend(item for item in sorted(path.rglob("*")) if item.is_file() and item.suffix.casefold() in allowed)
        else:
            expanded.append(path)
        if len(expanded) >= maximum:
            break
    return expanded[:maximum]


def _paragraph_chunks(text: str, target: int = 1_400, overlap: int = 180) -> list[tuple[str, str]]:
    cleaned = re.sub(r"\r\n?", "\n", str(text or ""))
    paragraphs = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"\n\s*\n", cleaned) if item.strip()]
    section = "body"
    chunks: list[tuple[str, str]] = []
    buffer = ""
    for paragraph in paragraphs:
        if paragraph.startswith("#") or (len(paragraph) < 100 and not re.search(r"[.!?]$", paragraph)):
            section = paragraph.lstrip("# ")[:120] or section
        while len(paragraph) > target:
            piece, paragraph = paragraph[:target], paragraph[target - overlap:]
            if buffer:
                chunks.append((section, buffer))
                buffer = ""
            chunks.append((section, piece))
        candidate = f"{buffer}\n\n{paragraph}".strip()
        if buffer and len(candidate) > target:
            chunks.append((section, buffer))
            buffer = f"{buffer[-overlap:]} {paragraph}".strip()
        else:
            buffer = candidate
    if buffer:
        chunks.append((section, buffer))
    return chunks


def _dense_vectors(texts: list[str], model_name: str | None = None) -> tuple[str, list[list[float]]]:
    try:
        model_type = importlib.import_module("sentence_transformers").SentenceTransformer
    except (ImportError, AttributeError) as error:
        raise RuntimeError("Dense indexing requires pip install '.[research]'.") from error
    selected = model_name or os.getenv("DISSOLVE_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    try:
        model = model_type(selected)
        encoded = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    except Exception as error:  # third-party model/cache failures vary by backend
        raise RuntimeError(f"Dense embedding model {selected!r} could not be loaded or evaluated.") from error
    return selected, [[round(float(value), 8) for value in row] for row in encoded]


def _first_overlapping_block(canonical: Mapping[str, Any], start: int, end: int) -> Mapping[str, Any] | None:
    for block in canonical.get("blocks") or []:
        try:
            b_start, b_end = int(block["char_start"]), int(block["char_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b_start < end and start < b_end:
            return block
    return None


def _table_caption_and_basis(canonical: Mapping[str, Any], table: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Caption and preceding prose carried alongside an atomic table chunk. Not in the span."""
    blocks = list(canonical.get("blocks") or [])
    by_id = {str(block.get("block_id")): i for i, block in enumerate(blocks)}
    table_id = str(table.get("table_id") or "")
    idx = by_id.get(table_id)
    caption = None
    cap_id = table.get("caption_block_id")
    if idx is not None:
        cap_id = cap_id or blocks[idx].get("caption_ref")
    if cap_id:
        for block in blocks:
            if str(block.get("block_id")) == str(cap_id):
                caption = str(block.get("text") or "") or None
                break
    basis = None
    if idx is None:
        return caption, basis
    window = blocks[max(0, idx - 3):idx]
    for block in reversed(window):
        kind = str(block.get("kind") or "")
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        if kind == "caption" and not caption:
            caption = text
        elif kind == "paragraph" and not basis:
            basis = text
    return caption, basis


def _index_chunks_from_canonical(canonical: Mapping[str, Any]) -> list[dict[str, Any]]:
    """C7 join + C7b rebound sidecar. Atomic table spans. No _paragraph_chunks."""
    packed = chunk_s2_block_pack(canonical)
    tables = {
        (int(table["char_start"]), int(table["char_end"])): table
        for table in canonical.get("tables") or []
        if table.get("char_start") is not None and table.get("char_end") is not None
    }
    rows: list[dict[str, Any]] = []
    for item in packed:
        start, end = int(item["char_start"]), int(item["char_end"])
        table = tables.get((start, end))
        block = _first_overlapping_block(canonical, start, end) or {}
        origin = block.get("nearest_preceding_heading_origin")
        heading = list(block.get("nearest_preceding_heading") or [])
        caption = basis = None
        if table is not None:
            caption, basis = _table_caption_and_basis(canonical, table)
        rows.append({
            "text": item["body"],
            "body": item["body"],
            "char_start": start,
            "char_end": end,
            "page": block.get("page"),
            "section": _chunk_header(heading) if origin == "parser_supplied" else "",
            "section_origin": origin,
            "nearest_preceding_heading": heading,
            "caption": caption,
            "basis": basis,
            "kind": "table" if table is not None else block.get("kind"),
        })
    return apply_table_rebound(rows, canonical)


def _ingest_inputs(
    paths: list[str], urls: list[str], knowledgebase: str, replace: bool,
    max_documents: int, build_dense_index: bool,
    metadata_by_url: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    maximum = max(1, min(int(max_documents or 1), _MAX_DOCUMENTS))
    index = _empty_index(knowledgebase) if replace else _load_index(knowledgebase)
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    for path in _expand_paths(paths, maximum):
        try:
            if not path.is_file():
                raise ValueError("path is not a file")
            records.extend(_records_from_bytes(path.read_bytes(), str(path)))
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            failures.append(f"{path.name}: {str(error)[:160]}")
    for url in urls[:max(0, maximum - len(records))]:
        if urlparse(url).scheme not in {"http", "https"}:
            failures.append("URL must use http or https")
            continue
        try:
            downloaded = _records_from_bytes(
                _request_bytes(url, source="document download", timeout=60), url,
            )
            metadata = (metadata_by_url or {}).get(url) or {}
            for record in downloaded:
                record.update({
                    key: metadata[key] for key in ("title", "url", "doi", "year")
                    if metadata.get(key) is not None
                })
            records.extend(downloaded)
        except (ResearchNetworkError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            failures.append(f"download: {str(error)[:160]}")
    existing_documents = {item["sha256"] for item in index["documents"]}
    existing_chunks = {item["sha256"] for item in index["chunks"]}
    documents_added = 0
    chunks_added = 0
    for record in records[:maximum]:
        canonical = record.get("canonical_document")
        if isinstance(canonical, Mapping) and canonical.get("schema") == _CANONICAL_DOCUMENT_SCHEMA:
            document_sha = str(canonical.get("source_pdf_sha256") or "")
            if not document_sha:
                document_sha = hashlib.sha256(
                    str(canonical.get("canonical_text") or "").encode()
                ).hexdigest()
            if not str(canonical.get("canonical_text") or "").strip() or document_sha in existing_documents:
                continue
            document_id = f"D{document_sha[:16]}"
            index["documents"].append({
                "document_id": document_id, "sha256": document_sha,
                "title": _clean(record.get("title"), 300), "source": str(record.get("source") or ""),
                "url": record.get("url") or (record.get("source") if str(record.get("source", "")).startswith("http") else None),
                "doi": record.get("doi"), "year": record.get("year"), "ingested_at": _now(),
                "parser_backend": canonical.get("parser_backend"),
                "parser_version": canonical.get("parser_version"),
                "fallback_reason": canonical.get("fallback_reason"),
            })
            existing_documents.add(document_sha)
            documents_added += 1
            chunk_index = 0
            for derived in _index_chunks_from_canonical(canonical):
                text = str(derived.get("text") or "")
                if not text.strip():
                    continue
                chunk_sha = hashlib.sha256(text.encode()).hexdigest()
                if chunk_sha in existing_chunks:
                    continue
                chunk_index += 1
                index["chunks"].append({
                    "chunk_id": f"K{document_sha[:10]}-{chunk_index:04d}", "sha256": chunk_sha,
                    "document_id": document_id, "title": _clean(record.get("title"), 300),
                    "source": str(record.get("source") or ""), "url": record.get("url"),
                    "doi": record.get("doi"), "year": record.get("year"),
                    "page": derived.get("page"),
                    "section": derived.get("section") or "",
                    "section_origin": derived.get("section_origin"),
                    "nearest_preceding_heading": derived.get("nearest_preceding_heading") or [],
                    "caption": derived.get("caption"),
                    "basis": derived.get("basis"),
                    "footnotes": derived.get("footnotes"),
                    "rebound_block_ids": list(derived.get("rebound_block_ids") or []),
                    "body_plus_rebound": derived.get("body_plus_rebound"),
                    "kind": derived.get("kind"),
                    "char_start": derived.get("char_start"),
                    "char_end": derived.get("char_end"),
                    "text": text,
                    "token_estimate": max(1, math.ceil(len(text) / 4)),
                })
                existing_chunks.add(chunk_sha)
                chunks_added += 1
                if len(index["chunks"]) >= _MAX_CHUNKS:
                    break
            continue
        joined = "\n".join(str(page.get("text") or "") for page in record.get("pages") or [])
        document_sha = hashlib.sha256(joined.encode()).hexdigest()
        if not joined.strip() or document_sha in existing_documents:
            continue
        document_id = f"D{document_sha[:16]}"
        index["documents"].append({
            "document_id": document_id, "sha256": document_sha,
            "title": _clean(record.get("title"), 300), "source": str(record.get("source") or ""),
            "url": record.get("url") or (record.get("source") if str(record.get("source", "")).startswith("http") else None),
            "doi": record.get("doi"), "year": record.get("year"), "ingested_at": _now(),
        })
        existing_documents.add(document_sha)
        documents_added += 1
        chunk_index = 0
        for page in record.get("pages") or []:
            for section, text in _paragraph_chunks(page.get("text") or ""):
                chunk_sha = hashlib.sha256(text.encode()).hexdigest()
                if chunk_sha in existing_chunks:
                    continue
                chunk_index += 1
                index["chunks"].append({
                    "chunk_id": f"K{document_sha[:10]}-{chunk_index:04d}", "sha256": chunk_sha,
                    "document_id": document_id, "title": _clean(record.get("title"), 300),
                    "source": str(record.get("source") or ""), "url": record.get("url"),
                    "doi": record.get("doi"), "year": record.get("year"),
                    "page": page.get("page"), "section": section, "text": text,
                    "token_estimate": max(1, math.ceil(len(text) / 4)),
                })
                existing_chunks.add(chunk_sha)
                chunks_added += 1
                if len(index["chunks"]) >= _MAX_CHUNKS:
                    break
            if len(index["chunks"]) >= _MAX_CHUNKS:
                break
    dense_warning = None
    if build_dense_index and index["chunks"]:
        model_name, vectors = _dense_vectors(
            [chunk_sparse_corpus(item) for item in index["chunks"]]
        )
        index["dense"] = {
            "model": model_name,
            "vectors": vectors,
            "built_at": _now(),
            "dim": _MINILM_DIM,
            "chunk_ids": [str(item["chunk_id"]) for item in index["chunks"]],
            "refuse_rule": _REFUSE_RULE_SPARSE_GATED,
        }
    elif chunks_added and index.get("dense"):
        index["dense"] = None
        dense_warning = "Dense vectors were invalidated by new chunks; rebuild explicitly."
    path = _save_index(index) if documents_added or replace or build_dense_index else _index_path(knowledgebase)
    return {
        "knowledgebase": index["knowledgebase"], "index_path": str(path),
        "documents_added": documents_added, "chunks_added": chunks_added,
        "document_count": len(index["documents"]), "chunk_count": len(index["chunks"]),
        "dense_index_built": bool(index.get("dense")), "dense_model": (index.get("dense") or {}).get("model"),
        "failures": failures, "warnings": [dense_warning] if dense_warning else [],
    }


def ingest_literature_documents(
    paths: Optional[list[str]] = None,
    urls: Optional[list[str]] = None,
    knowledgebase: str = "user-library",
    max_documents: int = 20,
    build_dense_index: bool = False,
) -> str:
    """Ingest explicit PDF/text/HTML/JSON sources into a portable local corpus."""
    tool = "ingest_literature_documents"
    path_items, url_items = _items(paths), _items(urls)
    if not path_items and not url_items:
        return tool_error(tool, "At least one path or URL is required.", error_code="missing_document_source")
    try:
        result = _ingest_inputs(path_items, url_items, knowledgebase, False, max_documents, build_dense_index)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        return tool_error(tool, str(error), error_code="corpus_ingestion_failed")
    if not result["documents_added"] and result["failures"]:
        return tool_error(tool, "No document was ingested.", error_code="corpus_ingestion_failed", **result)
    result_warnings = list(result.pop("warnings", []))
    return tool_success(
        tool,
        display=_table(("Knowledgebase", "Documents", "Chunks", "Dense"), [(
            result["knowledgebase"], result["document_count"], result["chunk_count"], result["dense_index_built"],
        )]),
        analysis_type="corpus_ingestion", **result,
        corpus_schema=_INDEX_SCHEMA,
        warnings=[
            *result_warnings,
            "The index is regenerable user state; source documents and provenance remain authoritative.",
        ],
    )


def _bm25(query_tokens: list[str], chunks: list[dict[str, Any]]) -> list[float]:
    token_lists = [_tokens(item["text"]) for item in chunks]
    lengths = [len(items) for items in token_lists]
    average = statistics.fmean(lengths) if lengths else 1.0
    document_frequency = Counter(token for items in token_lists for token in set(items))
    scores = []
    for items in token_lists:
        frequency = Counter(items)
        score = 0.0
        for token in set(query_tokens):
            count = frequency.get(token, 0)
            if not count:
                continue
            inverse = math.log(1.0 + (len(chunks) - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
            score += inverse * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * len(items) / max(average, 1.0)))
        scores.append(score)
    return scores


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _chunk_paper_sha256(index: Mapping[str, Any], chunk: Mapping[str, Any]) -> str | None:
    """Document identity. Never the chunk content hash."""
    sha = chunk.get("paper_sha256")
    if sha:
        return str(sha)
    doc_id = chunk.get("document_id")
    for document in index.get("documents") or []:
        if document.get("document_id") == doc_id:
            value = str(document.get("sha256") or "")
            return value or None
    return None


def _dense_query_scores(index: Mapping[str, Any], chunks: Sequence[Mapping[str, Any]], query: str) -> list[float]:
    """Query-embed only. Align by chunk_id set when the index records ids."""
    dense = index.get("dense") or {}
    vectors = list(dense.get("vectors") or [])
    if len(vectors) != len(chunks):
        raise ValueError("dense_index_unavailable")
    store_ids = [str(chunk["chunk_id"]) for chunk in chunks]
    recorded_ids = dense.get("chunk_ids")
    if recorded_ids is not None:
        recorded_ids = [str(item) for item in recorded_ids]
        if set(recorded_ids) != set(store_ids):
            raise ValueError("dense_index_unavailable")
        if len(recorded_ids) != len(vectors):
            raise ValueError("dense_index_unavailable")
        by_id = {chunk_id: vector for chunk_id, vector in zip(recorded_ids, vectors)}
        ordered = [by_id[chunk_id] for chunk_id in store_ids]
    else:
        ordered = vectors
    expected_model = dense.get("model")
    loaded_model, query_vectors = _dense_vectors([query], expected_model)
    if expected_model and loaded_model != expected_model:
        raise ValueError("dense_index_unavailable")
    query_vector = query_vectors[0]
    if len(query_vector) != _MINILM_DIM or any(len(vector) != _MINILM_DIM for vector in ordered):
        raise ValueError("dense_index_unavailable")
    recorded_dim = dense.get("dim")
    if recorded_dim is not None and int(recorded_dim) != _MINILM_DIM:
        raise ValueError("dense_index_unavailable")
    return [max(0.0, _cosine(query_vector, vector)) for vector in ordered]


def _search_index(index: dict[str, Any], query: str, top_k: int, mode: str) -> list[dict[str, Any]]:
    chunks = list(index.get("chunks") or [])
    query_tokens = _tokens(query)
    sparse_raw = _bm25(query_tokens, [{"text": chunk_sparse_corpus(chunk)} for chunk in chunks])
    if max(sparse_raw, default=0.0) <= 0:
        return []
    sparse_max = max(sparse_raw)
    sparse = [value / sparse_max for value in sparse_raw]
    dense_scores = [0.0] * len(chunks)
    if mode in {"dense", "hybrid"}:
        dense_scores = _dense_query_scores(index, chunks, query)
    ranked = []
    for chunk, sparse_score, dense_score in zip(chunks, sparse, dense_scores):
        origin = chunk.get("section_origin")
        if origin == "inherited_from_stack":
            section = ""
        else:
            section = str(chunk.get("section") or "").casefold()
        boost = 0.05 if any(word in section for word in ("abstract", "result", "conclusion", "method")) else 0.0
        if mode == "dense":
            score = dense_score + boost
        elif mode == "hybrid":
            score = _HYBRID_DENSE_WEIGHT * dense_score + _HYBRID_SPARSE_WEIGHT * sparse_score + boost
        else:
            score = sparse_score + boost
        if score <= 0:
            continue
        ranked.append((score, sparse_score, dense_score, boost, chunk))
    ranked.sort(key=lambda item: (-item[0], str(item[4].get("title")), item[4]["chunk_id"]))
    rows = []
    for index_number, (score, sparse_score, dense_score, boost, chunk) in enumerate(ranked[:top_k], 1):
        origin = chunk.get("section_origin")
        if origin == "inherited_from_stack":
            served_section = None
        else:
            served_section = chunk.get("section")
        excerpt_source = str(chunk.get("body_plus_rebound") or "").strip() or " ".join(
            str(part) for part in (
                chunk.get("caption"), chunk.get("basis"), chunk.get("footnotes"), chunk.get("text"),
            )
            if part
        )
        rows.append({
            "citation_id": f"C{index_number}", "chunk_id": chunk["chunk_id"],
            "paper_sha256": _chunk_paper_sha256(index, chunk),
            "title": chunk.get("title"), "source": chunk.get("source"),
            "url": chunk.get("url"), "doi": chunk.get("doi"), "year": chunk.get("year"),
            "page": chunk.get("page"), "section": served_section,
            "section_origin": origin,
            "char_start": chunk.get("char_start"),
            "char_end": chunk.get("char_end"),
            "caption": chunk.get("caption"),
            "basis": chunk.get("basis"),
            "footnotes": chunk.get("footnotes"),
            "excerpt": _clean(excerpt_source, 600),
            "sparse_score": round(sparse_score, 6), "dense_score": round(dense_score, 6),
            "section_boost": boost, "final_score": round(score, 6),
        })
    return rows


def search_literature_corpus(
    query: str,
    knowledgebase: str = "user-library",
    top_k: int = 5,
    retrieval_mode: Literal["sparse", "dense", "hybrid"] = "sparse",
) -> str:
    """Retrieve bounded, citable passages; the parent model authors the answer."""
    tool = "search_literature_corpus"
    if not str(query or "").strip():
        return tool_error(tool, "Corpus query cannot be empty.", error_code="empty_query")
    mode = str(retrieval_mode or "").casefold()
    if mode not in {"sparse", "dense", "hybrid"}:
        return tool_error(tool, f"Unknown retrieval mode: {mode}.", error_code="unknown_retrieval_mode")
    try:
        index = _load_index(knowledgebase)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return tool_error(tool, str(error), error_code="corpus_read_failed")
    if not index["chunks"]:
        return tool_error(
            tool, f"Knowledgebase {_slug(knowledgebase)!r} is empty.", error_code="empty_corpus",
            knowledgebase=_slug(knowledgebase),
        )
    try:
        rows = _search_index(index, query, max(1, min(int(top_k), 10)), mode)
    except (ValueError, RuntimeError) as error:
        if str(error) == "dense_index_unavailable":
            return tool_error(
                tool, "Dense retrieval was requested but this corpus has no complete dense index.",
                error_code="dense_index_unavailable", knowledgebase=index["knowledgebase"],
                remediation="Reingest with build_dense_index=true or use sparse retrieval.",
            )
        return tool_error(tool, str(error), error_code="retrieval_failed")
    top_score = rows[0]["final_score"] if rows else 0.0
    return tool_success(
        tool,
        display=_table(("Citation", "Score", "Source", "Section"), [
            (row["citation_id"], row["final_score"], row["title"], row.get("section") or "—") for row in rows
        ]),
        analysis_type="corpus_retrieval", query=query, knowledgebase=index["knowledgebase"],
        retrieval_mode=mode, dense_index_available=bool(index.get("dense")),
        refuse_rule=_REFUSE_RULE_SPARSE_GATED,
        hybrid_weights={"dense": _HYBRID_DENSE_WEIGHT, "sparse": _HYBRID_SPARSE_WEIGHT},
        result_count=len(rows), results=rows, top_score=top_score,
        low_retrieval_confidence=not rows or top_score < 0.15,
        evidence_scope="retrieved_corpus_passages",
        warnings=[
            "Cite passage identifiers and distinguish retrieved text from independent experimental validation.",
            "Validation or external-test results in a passage are author-reported and were not independently reproduced by DISSOLVE.",
            "Absence from the returned passages is not evidence that the corpus contains no relevant document.",
        ],
    )


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    mean_left, mean_right = statistics.fmean(left), statistics.fmean(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    denominator = math.sqrt(sum((a - mean_left) ** 2 for a in left) * sum((b - mean_right) ** 2 for b in right))
    return numerator / denominator if denominator else None


def _expanded_query(query: str) -> str:
    aliases = {
        "ldpe": "low density polyethylene", "hdpe": "high density polyethylene",
        "pet": "polyethylene terephthalate", "evoh": "ethylene vinyl alcohol",
        "strap": "solvent targeted recovery precipitation",
    }
    additions = [phrase for token, phrase in aliases.items() if token in _tokens(query)]
    return " ".join([query, *additions]).strip()


def inspect_literature_corpus(
    operation: Literal["status", "chunk_quality", "retrieval_comparison", "query_expansion", "document_similarity"] = "status",
    knowledgebase: str = "user-library",
    query: Optional[str] = None,
) -> str:
    """Inspect corpus state and retrieval quality without manufacturing prose."""
    tool = "inspect_literature_corpus"
    operation = str(operation or "").casefold()
    allowed = {"status", "chunk_quality", "retrieval_comparison", "query_expansion", "document_similarity"}
    if operation not in allowed:
        return tool_error(tool, f"Unknown diagnostic operation: {operation}.", error_code="unknown_diagnostic")
    if operation == "status":
        rows = []
        root = _research_root()
        for path in sorted(root.glob("*.json.gz")) if root.exists() else []:
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    item = json.load(handle)
                rows.append({
                    "knowledgebase": item.get("knowledgebase"), "documents": len(item.get("documents") or []),
                    "chunks": len(item.get("chunks") or []), "dense_index_available": bool(item.get("dense")),
                    "index_path": str(path),
                })
            except (OSError, json.JSONDecodeError):
                rows.append({"knowledgebase": path.stem, "error": "unreadable index", "index_path": str(path)})
        return tool_success(
            tool, display=_table(("Knowledgebase", "Documents", "Chunks", "Dense"), [
                (row.get("knowledgebase"), row.get("documents", "—"), row.get("chunks", "—"), row.get("dense_index_available", "—")) for row in rows
            ]), analysis_type="corpus_status", operation=operation,
            knowledgebase_count=len(rows), results=rows, corpus_root=str(root), corpus_schema=_INDEX_SCHEMA,
        )
    try:
        index = _load_index(knowledgebase)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return tool_error(tool, str(error), error_code="corpus_read_failed")
    chunks = list(index.get("chunks") or [])
    if not chunks:
        return tool_error(tool, "The requested knowledgebase is empty.", error_code="empty_corpus", knowledgebase=index["knowledgebase"])
    if operation == "chunk_quality":
        sizes = [len(item["text"]) for item in chunks]
        duplicate_count = len(sizes) - len({item["sha256"] for item in chunks})
        result = {
            "chunk_count": len(chunks), "document_count": len(index["documents"]),
            "minimum_chars": min(sizes), "median_chars": statistics.median(sizes), "maximum_chars": max(sizes),
            "short_chunks": sum(value < 120 for value in sizes), "long_chunks": sum(value > 2_000 for value in sizes),
            "duplicate_chunks": duplicate_count,
        }
        return tool_success(tool, analysis_type="corpus_chunk_quality", operation=operation,
                            knowledgebase=index["knowledgebase"], result=result,
                            warnings=["Chunk-length checks describe index structure, not retrieval relevance."])
    if operation == "document_similarity":
        by_document: dict[str, set[str]] = {}
        titles: dict[str, str] = {}
        for item in chunks:
            by_document.setdefault(item["document_id"], set()).update(_tokens(item["text"]))
            titles[item["document_id"]] = item.get("title") or item["document_id"]
        pairs = []
        ids = sorted(by_document)
        for left_index, left_id in enumerate(ids):
            for right_id in ids[left_index + 1:]:
                union = by_document[left_id] | by_document[right_id]
                score = len(by_document[left_id] & by_document[right_id]) / len(union) if union else 0.0
                pairs.append({"left": titles[left_id], "right": titles[right_id], "token_jaccard": round(score, 6)})
        pairs.sort(key=lambda item: (-item["token_jaccard"], item["left"], item["right"]))
        return tool_success(tool, analysis_type="corpus_document_similarity", operation=operation,
                            knowledgebase=index["knowledgebase"], results=pairs[:20],
                            similarity_basis="document token-set Jaccard, not semantic embedding")
    if not str(query or "").strip():
        return tool_error(tool, f"query is required for {operation}.", error_code="missing_query")
    if operation == "query_expansion":
        expanded = _expanded_query(query or "")
        original = _search_index(index, query or "", 10, "sparse")
        expanded_rows = _search_index(index, expanded, 10, "sparse")
        original_ids = {row["chunk_id"] for row in original}
        expanded_ids = {row["chunk_id"] for row in expanded_rows}
        return tool_success(
            tool, analysis_type="corpus_query_expansion", operation=operation,
            knowledgebase=index["knowledgebase"], query=query, expanded_query=expanded,
            original_result_count=len(original), expanded_result_count=len(expanded_rows),
            added_chunks=sorted(expanded_ids - original_ids), lost_chunks=sorted(original_ids - expanded_ids),
            expansion_basis="transparent polymer-name aliases only",
        )
    sparse = _search_index(index, query or "", 10, "sparse")
    if not index.get("dense"):
        return tool_success(
            tool, analysis_type="corpus_retrieval_comparison", operation=operation,
            knowledgebase=index["knowledgebase"], query=query, dense_index_available=False,
            results=sparse, correlation=None,
            warnings=["Dense-versus-sparse comparison is unavailable until an explicit dense index is built."],
        )
    hybrid = _search_index(index, query or "", 10, "hybrid")
    sparse_by_id = {row["chunk_id"]: row["sparse_score"] for row in sparse}
    common = [row for row in hybrid if row["chunk_id"] in sparse_by_id]
    correlation = _correlation([sparse_by_id[row["chunk_id"]] for row in common], [row["dense_score"] for row in common])
    return tool_success(
        tool, analysis_type="corpus_retrieval_comparison", operation=operation,
        knowledgebase=index["knowledgebase"], query=query, dense_index_available=True,
        results=hybrid, correlation=correlation,
        scoring_basis="0.55 dense + 0.40 BM25 + transparent section boost",
    )


def _declared_knowledgebase(
    context: dict[str, Any],
) -> str | None:
    value = context.get("declared_knowledgebase")
    if value is None:
        return None
    declared = str(value).strip()
    return declared or None


# This late import keeps the typed orchestration module free to reuse the
# contracts above lazily without a module-initialization cycle.
from .literature_ingest import ingest_literature_graph
