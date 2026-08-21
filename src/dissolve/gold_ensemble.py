"""C5 A+B gold assembler. Local channels only. No vision, no Docling, no seal."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

# GATE c1v2-census-ceiling OBJECT (2026-08-21T18:52:03Z). Pinning these
# digests is not an accept-test-7 close. A later C1.v2 must be new bytes.
OBJECTED_CENSUS_V2_SHA256 = "f8568ad7def8157e5dc54563eaf6b4a7571337e41ef2d3e5c85efb68ceca829e"
OBJECTED_CEILING_V2_SHA256 = "f3a477d6423fded3cd25675dd68bc6a953422f750e75e6837569199416732b47"
CENSUS_V2_SHA256 = OBJECTED_CENSUS_V2_SHA256
CEILING_V2_SHA256 = OBJECTED_CEILING_V2_SHA256
CONTAMINANT_SHA256 = "7419fa5c9ffac0beb2722f73c1b446064e612b972c64e8f04595141f4f9c8421"
START_GUARD_PEAK_RSS_BYTES = 3_501_953_024
ALLOWED_PAPER_STATUS = frozenset({"indexed", "held_out", "excluded"})
CENSUS_V1_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/CENSUS.v1.json")
C3_SET_NOTE = (
    "C3 CANONICAL_MANIFEST.v1 covered the v1 in-scope set (21 documents). "
    "It is not silently treated as covering CENSUS.v2."
)

# Firewall C5.1c — any hit in a gold-work transcript fails that paper.
TRANSCRIPT_FORBIDDEN = (
    "parses/",
    "canonical",
    "measurements/",
    "gold_facts",
    "ONE_PAPER_EXTRACTION_VS_READ",
    "docling_parsed_document",
    "pypdf_parsed_document",
    "GATE_AUDITS.md",
)

_C3_STATUSES = frozenset({"indexed", "held_out"})
_NUMERIC_TOKEN = re.compile(
    r"(?<![\w.])(\d+\.\d+|\d+\s*%|\d+\s*°\s*C)(?![\w.])",
)
_TABLE_CAPTION = re.compile(r"\bTable\s+(\d+[A-Za-z]?)\b", re.IGNORECASE)
_NUMERIC_CELL = re.compile(r"^[-+]?\d+(?:\.\d+)?%?$")


class GoldEnsembleError(ValueError):
    """Typed C5 assembler failure. Not a gold seal."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_firewall_text(text: str) -> list[str]:
    """Return forbidden substrings found in a gold-work transcript."""
    lowered = text
    return [token for token in TRANSCRIPT_FORBIDDEN if token in lowered]


def refuse_source_path(path: Path) -> None:
    rendered = str(path)
    hits = scan_firewall_text(rendered)
    if hits:
        raise GoldEnsembleError(
            "firewall_source_path",
            "Gold work may not read a path that names a forbidden store.",
            path=rendered, hits=hits,
        )


def _reused_c3_peaks(text: str) -> bool:
    folded = text.casefold()
    return "during c3" in folded or "c3 persist" in folded


def assert_time_v_persist(ceiling: Mapping[str, Any]) -> Path:
    """Option 1 is a /usr/bin/time -v persist, not a measurement_method label."""
    raw_path = ceiling.get("time_v_persist_path")
    expected = str(ceiling.get("time_v_persist_sha256") or "")
    if not raw_path or not expected:
        raise GoldEnsembleError(
            "c35_option1_label_only",
            "Option 1 is not a measurement_method string. Publish a "
            "/usr/bin/time -v persist and pin its SHA-256.",
        )
    persist = Path(raw_path)
    if not persist.is_file():
        raise GoldEnsembleError(
            "c35_option1_persist_missing",
            "Option 1 persist path is not a file.",
            path=str(persist),
        )
    got = file_sha256(persist)
    if got != expected:
        raise GoldEnsembleError(
            "c35_option1_persist_digest_mismatch",
            "Option 1 persist digest does not match the ceiling pin.",
            got=got, expected=expected,
        )
    body = persist.read_text(encoding="utf-8", errors="replace")
    if "Maximum resident set size" not in body:
        raise GoldEnsembleError(
            "c35_option1_not_time_v",
            "Option 1 persist is not /usr/bin/time -v output.",
        )
    if _reused_c3_peaks(body):
        raise GoldEnsembleError(
            "c35_not_closed",
            "Option 1 persist reuses C3 peaks. That is not a re-measure.",
        )
    return persist


def assert_c35_closed(ceiling: Mapping[str, Any]) -> str:
    """Accept test 7: C3.5 closes only by option 1 or option 2. Not a C3 reuse raise."""
    try:
        peak = int(ceiling["PEAK_RSS_CEILING_BYTES"])
    except (KeyError, TypeError, ValueError) as error:
        raise GoldEnsembleError(
            "c35_not_closed",
            "C5 refuses to start: CEILING has no usable PEAK_RSS_CEILING_BYTES.",
        ) from error
    retraction = str((ceiling.get("RETRACTION") or {}).get("status") or "")
    method = str(ceiling.get("measurement_method") or "")
    basis = str(ceiling.get("basis") or "")
    reused_c3 = _reused_c3_peaks(basis)
    if peak == START_GUARD_PEAK_RSS_BYTES and "RETRACTED" in retraction:
        return "option_2_start_guard"
    if "/usr/bin/time -v" in method and not reused_c3 and "RETRACTED" in retraction:
        assert_time_v_persist(ceiling)
        return "option_1_remeasure"
    raise GoldEnsembleError(
        "c35_not_closed",
        "C3.5 is not closed. Keep PEAK_RSS_CEILING_BYTES 3501953024 as the "
        "start-guard, or re-measure with /usr/bin/time -v. Reusing C3 peaks "
        "to raise the ceiling is not a close.",
        peak=peak, measurement_method=method, reused_c3=reused_c3,
    )


def assert_papers_classified(census: Mapping[str, Any]) -> None:
    """§5 / accept test 7: every paper is indexed, held_out, or excluded."""
    bad = [
        {
            "sha256": row.get("sha256"),
            "status": row.get("status"),
            "filename": row.get("filename"),
        }
        for row in census.get("papers") or []
        if str(row.get("status") or "") not in ALLOWED_PAPER_STATUS
    ]
    if bad:
        raise GoldEnsembleError(
            "unclassified_paper",
            "Accept test 7: every paper must be indexed, held_out, or excluded. "
            "NEW-UNCLASSIFIED is a labelled hole, not a classification.",
            papers=bad,
        )


def require_c1_v2(census_path: Path, ceiling_path: Path) -> dict[str, Any]:
    """Refuse to start C5 unless accept test 7 is actually closed.

    File-digest match plus a RETRACTED substring is not a close. GATE
    c1v2-census-ceiling already OBJECTed the current v2 pins.
    """
    census_path = Path(census_path)
    ceiling_path = Path(ceiling_path)
    census_digest = file_sha256(census_path)
    ceiling_digest = file_sha256(ceiling_path)
    if (
        census_digest == OBJECTED_CENSUS_V2_SHA256
        or ceiling_digest == OBJECTED_CEILING_V2_SHA256
    ):
        raise GoldEnsembleError(
            "c1v2_objected",
            "GATE c1v2-census-ceiling OBJECT. Pinning those digests does not "
            "close accept test 7. C3.5 is unclosed and 7419fa5c is NEW-UNCLASSIFIED.",
            census_sha256=census_digest,
            ceiling_sha256=ceiling_digest,
        )
    census = json.loads(census_path.read_text(encoding="utf-8"))
    ceiling = json.loads(ceiling_path.read_text(encoding="utf-8"))
    close = assert_c35_closed(ceiling)
    assert_papers_classified(census)
    return {
        "census": census,
        "ceiling": ceiling,
        "census_sha256": census_digest,
        "ceiling_sha256": ceiling_digest,
        "c35_close": close,
    }


def iter_pdfs(pdf_root: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(Path(pdf_root).rglob("*.pdf")):
        if "Zone.Identifier" in path.name:
            continue
        found.append(path)
    return found


def pdf_set_identity(census: Mapping[str, Any], pdf_root: Path) -> dict[str, list[str]]:
    """Census paper SHA set must equal the SHA set of every PDF under pdf_root."""
    census_shas = {str(row["sha256"]) for row in census["papers"]}
    disk: dict[str, list[str]] = {}
    for path in iter_pdfs(pdf_root):
        digest = file_sha256(path)
        disk.setdefault(digest, []).append(str(path))
    disk_shas = set(disk)
    if census_shas != disk_shas:
        raise GoldEnsembleError(
            "pdf_set_identity_mismatch",
            "Census SHA set does not equal the PDF-set on disk.",
            only_census=sorted(census_shas - disk_shas),
            only_disk=sorted(disk_shas - census_shas),
        )
    return disk


def c3_covered_shas(census_v1_path: Path | None = None) -> set[str]:
    """In-scope SHAs C3 actually parsed. CENSUS.v1, not the canonical store."""
    path = Path(census_v1_path or CENSUS_V1_PATH)
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row["sha256"])
        for row in data["papers"]
        if row.get("status") in _C3_STATUSES
    }


def c3_papers(
    census: Mapping[str, Any],
    covered: set[str] | None = None,
) -> list[dict[str, Any]]:
    """C3-covered in-scope papers. A v2 addition that is indexed/held_out refuses."""
    covered_shas = set(covered) if covered is not None else c3_covered_shas()
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    extra: list[str] = []
    for row in census["papers"]:
        digest = str(row["sha256"])
        if digest in seen:
            continue
        if row.get("status") not in _C3_STATUSES:
            continue
        seen.add(digest)
        if digest not in covered_shas:
            extra.append(digest)
            continue
        rows.append(row)
    if extra:
        raise GoldEnsembleError(
            "c3_extension_required",
            "An in-scope v2 paper is not in the C3 coverage set. "
            "Classifying it indexed or held_out requires a C3 extension.",
            extra=extra,
        )
    return rows


def contaminant_pending(census: Mapping[str, Any]) -> dict[str, Any]:
    row = next(
        (item for item in census["papers"] if item.get("sha256") == CONTAMINANT_SHA256),
        None,
    )
    status = (row or {}).get("status", "NEW-UNCLASSIFIED")
    classified = status in ALLOWED_PAPER_STATUS
    return {
        "sha256": CONTAMINANT_SHA256,
        "filename": (row or {}).get("filename", "contaminant.pdf"),
        "status": status,
        "classified": classified,
        "owner_call": None if classified else "required",
        "c3_extension": None if status == "excluded" else "required_before_indexed",
        "ran_c5": False,
        "note": (
            "C5 refuses to start while this SHA is not indexed, held_out, or "
            "excluded. Recording a pending addition is not permission to start."
            if not classified else
            "Classified. Indexed or held_out still requires a C3 extension "
            "before this SHA may enter the C5 run set."
        ),
    }


def stage_one_pdf(source: Path, stage_dir: Path, pdf_sha256: str) -> Path:
    """Copy one PDF into a workspace whose listing is exactly that file."""
    refuse_source_path(source)
    stage_dir = Path(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    leftover = [p.name for p in stage_dir.iterdir()]
    if leftover:
        raise GoldEnsembleError(
            "stage_not_empty",
            "Staging workspace must start empty.",
            leftover=leftover,
        )
    dest = stage_dir / f"{pdf_sha256}.pdf"
    shutil.copyfile(source, dest)
    assert_stage_exactly_one_pdf(stage_dir)
    got = file_sha256(dest)
    if got != pdf_sha256:
        raise GoldEnsembleError(
            "staged_sha_mismatch",
            "Staged PDF digest does not match the census row.",
            got=got, expected=pdf_sha256,
        )
    return dest


def assert_stage_exactly_one_pdf(stage_dir: Path) -> Path:
    entries = [path for path in Path(stage_dir).iterdir() if path.name not in {".", ".."}]
    if len(entries) != 1 or entries[0].suffix.lower() != ".pdf":
        raise GoldEnsembleError(
            "stage_not_exactly_one_pdf",
            "Gold workspace listing must be exactly one PDF.",
            listing=[path.name for path in entries],
        )
    return entries[0]


def refuse_vision(*_args: Any, **_kwargs: Any) -> None:
    raise GoldEnsembleError(
        "vision_not_authorized",
        "Channel C/C' is a billed vision API. ADMIT is not spend authorization.",
    )


def channel_c(*_args: Any, **_kwargs: Any) -> None:
    refuse_vision()


def channel_c_prime(*_args: Any, **_kwargs: Any) -> None:
    refuse_vision()


def channel_a_pypdf(pdf: Path) -> dict[str, Any]:
    """Embedded text layer via pypdf. Not literature_ingest._parse. tables=[]."""
    from pypdf import PdfReader
    import pypdf

    reader = PdfReader(str(pdf))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return {
        "channel": "A",
        "read_by": "pypdf",
        "version": getattr(pypdf, "__version__", "unknown"),
        "pages": pages,
        "text": "\f".join(pages),
        "tables": [],
        "n_pages": len(pages),
    }


def channel_b_pdftotext(pdf: Path) -> dict[str, Any]:
    """Column-preserving embedded text via local pdftotext -layout."""
    exe = Path(shutil.which("pdftotext") or "/usr/bin/pdftotext")
    if exe.name != "pdftotext":
        raise GoldEnsembleError(
            "subprocess_refused",
            "Channel B may invoke only pdftotext.",
            exe=str(exe),
        )
    command = [str(exe), "-layout", "-enc", "UTF-8", str(pdf), "-"]
    completed = subprocess.run(
        command, check=False, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise GoldEnsembleError(
            "pdftotext_failed",
            "pdftotext -layout failed.",
            returncode=completed.returncode,
            stderr=completed.stderr[-500:],
        )
    text = completed.stdout
    pages = text.split("\f")
    if pages and pages[-1] == "":
        pages = pages[:-1]
    return {
        "channel": "B",
        "read_by": "pdftotext -layout",
        "version": "poppler",
        "pages": pages,
        "text": text,
        "n_pages": len(pages),
    }


def normalize_token(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()


def tokens_on_pages(pages: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for index, page in enumerate(pages, 1):
        for match in _NUMERIC_TOKEN.finditer(page):
            token = normalize_token(match.group(1))
            found.setdefault(token, index)
    return found


def _looks_like_table_row(line: str) -> bool:
    stripped = line.rstrip()
    if "  " not in stripped and "\t" not in stripped:
        return False
    parts = [part.strip() for part in re.split(r"\s{2,}|\t+", stripped.strip()) if part.strip()]
    if len(parts) < 2:
        return False
    numeric = sum(1 for part in parts if _NUMERIC_CELL.match(part))
    return numeric >= 2


def table_candidates_from_b(pages: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page_number, page in enumerate(pages, 1):
        locus = None
        for line in page.splitlines():
            caption = _TABLE_CAPTION.search(line)
            if caption:
                locus = f"Table {caption.group(1)}"
            if not _looks_like_table_row(line):
                continue
            rows.append({
                "page": page_number,
                "locus": locus,
                "b_reading": line.rstrip(),
            })
    return rows


def _fact_id(prefix: str, paper_sha: str, key: str) -> str:
    digest = hashlib.sha256(f"{paper_sha}\0{key}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{paper_sha[:12]}-{digest}"


def validate_fact(fact: Mapping[str, Any]) -> None:
    channels = [str(item) for item in fact.get("channels_used") or []]
    lowered = {item.casefold() for item in channels}
    if "docling" in lowered:
        raise GoldEnsembleError(
            "docling_in_channels",
            "Docling is not a gold channel.",
            fact_id=fact.get("fact_id"),
        )
    kind = str(fact.get("kind") or "")
    locus = str(fact.get("locus") or "")
    scoring = str(fact.get("scoring_class") or "")
    table_claim = kind == "table_cell" or locus.casefold().startswith("table ")
    if table_claim and scoring != "bound_fact":
        raise GoldEnsembleError(
            "table_cell_not_bound_fact",
            "kind=table_cell or a Table-N locus is scoring_class=bound_fact.",
            fact_id=fact.get("fact_id"), scoring_class=scoring,
        )
    if scoring == "string_existence" and table_claim:
        raise GoldEnsembleError(
            "table_cell_graded_as_string_existence",
            "A table-cell fact graded only as string-existence fails.",
            fact_id=fact.get("fact_id"),
        )
    if scoring == "string_existence" and fact.get("needles"):
        raise GoldEnsembleError(
            "string_existence_needles",
            "string_existence must not populate needles for official contain_bound_fact.",
            fact_id=fact.get("fact_id"),
        )


def assemble_facts(
    *,
    paper: Mapping[str, Any],
    channel_a: Mapping[str, Any],
    channel_b: Mapping[str, Any],
) -> dict[str, Any]:
    """Build A+B string_existence, B table-cell candidates, and disputes. No seal."""
    paper_sha = str(paper["sha256"])
    a_pages = list(channel_a["pages"])
    b_pages = list(channel_b["pages"])
    a_tokens = tokens_on_pages(a_pages)
    b_tokens = tokens_on_pages(b_pages)
    agreed = sorted(set(a_tokens) & set(b_tokens))
    disputed_tokens = sorted(set(a_tokens) ^ set(b_tokens))

    string_facts: list[dict[str, Any]] = []
    for token in agreed:
        page = min(a_tokens[token], b_tokens[token])
        fact = {
            "fact_id": _fact_id("se", paper_sha, token),
            "paper_sha256": paper_sha,
            "paper_status": paper.get("status"),
            "genre": paper.get("genre"),
            "kind": "token",
            "scoring_class": "string_existence",
            "locus": None,
            "page": page,
            "section": None,
            "query": None,
            "evidence_quote": token,
            "tokens": [token],
            "needles": {},
            "channels_used": ["A", "B"],
            "read_by": {"A": "pypdf", "B": "pdftotext -layout"},
            "readings": {"A": token, "B": token, "C": None, "C_prime": None},
            "confidence": "unanimous",
            "status": "agreed_not_gold_bound",
            "sealed": False,
        }
        validate_fact(fact)
        string_facts.append(fact)

    candidates: list[dict[str, Any]] = []
    for index, row in enumerate(table_candidates_from_b(b_pages), 1):
        locus = row["locus"]
        fact = {
            "fact_id": _fact_id("tc", paper_sha, f"{index}:{row['b_reading']}"),
            "paper_sha256": paper_sha,
            "paper_status": paper.get("status"),
            "genre": paper.get("genre"),
            "kind": "table_cell",
            "scoring_class": "bound_fact",
            "locus": locus,
            "page": row["page"],
            "section": None,
            "query": None,
            "evidence_quote": row["b_reading"],
            "tokens": [],
            "needles": {},
            "channels_used": ["B"],
            "read_by": {"B": "pdftotext -layout"},
            "readings": {
                "A": None,
                "B": row["b_reading"],
                "C": None,
                "C_prime": None,
            },
            "confidence": "single-channel",
            "status": "awaiting_C",
            "awaiting": ["C"],
            "sealed": False,
            "note": "B reading recorded. Not gold until B+C agree. A has no table structure.",
        }
        validate_fact(fact)
        candidates.append(fact)

    disputes: list[dict[str, Any]] = []
    for token in disputed_tokens:
        in_a = token in a_tokens
        in_b = token in b_tokens
        fact = {
            "fact_id": _fact_id("dp", paper_sha, token),
            "paper_sha256": paper_sha,
            "paper_status": paper.get("status"),
            "genre": paper.get("genre"),
            "kind": "token",
            "scoring_class": "string_existence",
            "locus": None,
            "page": a_tokens.get(token) or b_tokens.get(token),
            "section": None,
            "query": None,
            "evidence_quote": token,
            "tokens": [token],
            "needles": {},
            "channels_used": (["A"] if in_a else []) + (["B"] if in_b else []),
            "read_by": {
                **({"A": "pypdf"} if in_a else {}),
                **({"B": "pdftotext -layout"} if in_b else {}),
            },
            "readings": {
                "A": token if in_a else None,
                "B": token if in_b else None,
                "C": None,
                "C_prime": None,
            },
            "confidence": "disputed",
            "status": "disputed",
            "sealed": False,
            "note": "No majority across A and B. Recorded, not resolved.",
        }
        validate_fact(fact)
        disputes.append(fact)

    return {
        "paper_sha256": paper_sha,
        "filename": paper.get("filename"),
        "paper_status": paper.get("status"),
        "genre": paper.get("genre"),
        "string_existence": string_facts,
        "table_cell_candidates": candidates,
        "disputes": disputes,
        "n_string_existence": len(string_facts),
        "n_table_cell_candidates": len(candidates),
        "n_disputes": len(disputes),
        "sealed": False,
    }


def run_one_paper(paper: Mapping[str, Any], source: Path, stage_root: Path) -> dict[str, Any]:
    paper_sha = str(paper["sha256"])
    stage_dir = Path(stage_root) / paper_sha
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    try:
        staged = stage_one_pdf(source, stage_dir, paper_sha)
        listing = [path.name for path in stage_dir.iterdir()]
        channel_a = channel_a_pypdf(staged)
        channel_b = channel_b_pdftotext(staged)
        assembled = assemble_facts(paper=paper, channel_a=channel_a, channel_b=channel_b)
        assembled["stage_listing"] = listing
        assembled["n_pages_a"] = channel_a["n_pages"]
        assembled["n_pages_b"] = channel_b["n_pages"]
        assembled["a_empty"] = not any(page.strip() for page in channel_a["pages"])
        assembled["b_empty"] = not any(page.strip() for page in channel_b["pages"])
        return assembled
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)


def run_c5_ab(
    *,
    census_path: Path,
    ceiling_path: Path,
    pdf_root: Path,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Run A+B only after accept test 7 closes. Does not seal. Does not call vision."""
    loaded = require_c1_v2(census_path, ceiling_path)
    census = loaded["census"]
    disk = pdf_set_identity(census, pdf_root)
    papers = c3_papers(census)
    pending = contaminant_pending(census)
    papers_out: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="c5-ab-") as raw_stage:
        stage_root = Path(raw_stage)
        for paper in papers:
            digest = str(paper["sha256"])
            sources = disk[digest]
            source = Path(sources[0])
            papers_out.append(run_one_paper(paper, source, stage_root))

    density = {
        "n_papers": len(papers_out),
        "n_string_existence": sum(row["n_string_existence"] for row in papers_out),
        "n_table_cell_candidates": sum(row["n_table_cell_candidates"] for row in papers_out),
        "n_disputes": sum(row["n_disputes"] for row in papers_out),
        "agreement_rate_string_tokens": None,
        "note": (
            "Density floors in corpus §6.4 apply to sealed GOLD.v2 bound_facts, "
            "not to this A+B draft. Table-cell rows are awaiting-C, not gold."
        ),
    }
    n_agreed = density["n_string_existence"]
    n_disp = density["n_disputes"]
    denom = n_agreed + n_disp
    if denom:
        density["agreement_rate_string_tokens"] = n_agreed / denom

    artifact = {
        "schema": "dissolve.gold-ensemble-ab.v1",
        "checkpoint": "C5-AB",
        "sealed": False,
        "seal_forbidden": True,
        "vision_called": False,
        "channels_run": ["A", "B"],
        "channels_not_run": ["C", "C_prime"],
        "docling_used": False,
        "census_v2_sha256": loaded["census_sha256"],
        "ceiling_v2_sha256": loaded["ceiling_sha256"],
        "c35_close": loaded["c35_close"],
        "c3_coverage": C3_SET_NOTE,
        "contaminant_pending": pending,
        "skipped": [
            {
                "sha256": "b95603201907ce4de4f4a62671d0ba8c20879b723da7b15fee5d8af2fa2f199f",
                "reason": "excluded / text_layer none — C+C' not authorized",
            },
        ],
        "density": density,
        "papers": papers_out,
    }
    if out_path is not None:
        dest = Path(out_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        artifact["wrote"] = str(dest)
        artifact["wrote_sha256"] = file_sha256(dest)
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C5 A+B assembler. No vision. No seal.")
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--ceiling", type=Path, required=True)
    parser.add_argument("--pdf-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    artifact = run_c5_ab(
        census_path=args.census,
        ceiling_path=args.ceiling,
        pdf_root=args.pdf_root,
        out_path=args.out,
    )
    print(json.dumps({
        "wrote": artifact.get("wrote"),
        "wrote_sha256": artifact.get("wrote_sha256"),
        "density": artifact["density"],
        "sealed": False,
        "vision_called": False,
        "contaminant_pending": artifact["contaminant_pending"]["sha256"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
