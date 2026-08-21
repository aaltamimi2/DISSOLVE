"""C5 vision channels C and C'. Named APIs only. Assembler does not look at pages."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable, Mapping

from .gold_ensemble import (
    GoldEnsembleError,
    VISION_SPEND_SHA256,
    file_sha256,
    scan_firewall_text,
    validate_fact,
)

VISION_SPEND_PATH = Path(
    "/home/aaltamimi2/dissolve-v12-audit/VISION_SPEND_AUTHORIZATION.v1.md"
)
CHANNEL_C_MODEL = "claude-opus-5-thinking-max"
CHANNEL_C_PRIME_MODEL = "cursor-grok-4.6-xhigh"
CHANNEL_C_PRIME_CLI = ("/home/aaltamimi2/.local/bin/agent",)

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_NEEDLE_KEYS = ("polymer", "solvent", "temperature", "value")
_FIG4 = re.compile(r"\bFig(?:ure)?\.?\s*4\b", re.IGNORECASE)


def require_vision_spend(
    path: Path | None = None,
    expected: str | None = None,
) -> str:
    spend = Path(path or VISION_SPEND_PATH)
    digest = file_sha256(spend)
    want = expected or VISION_SPEND_SHA256
    if digest != want:
        raise GoldEnsembleError(
            "vision_spend_digest_mismatch",
            "Accept test 8: billed vision requires the owner spend authorization.",
            got=digest, expected=want,
        )
    return digest


def assert_distinct_vision_models(model_c: str, model_c_prime: str) -> None:
    if model_c == model_c_prime:
        raise GoldEnsembleError(
            "same_vision_model_twice",
            "Accept test 3: C and C' must be two different named models.",
            model=model_c,
        )


def render_pages(pdf: Path, pages: list[int], dest_dir: Path, *, dpi: int = 140) -> list[Path]:
    """Raster selected pages. Parent does not decode the PNGs."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    leftover = [p.name for p in dest_dir.iterdir()]
    if leftover:
        raise GoldEnsembleError(
            "vision_stage_not_empty",
            "Vision workspace must start empty.",
            leftover=leftover,
        )
    written: list[Path] = []
    for page in pages:
        prefix = dest_dir / f"page-{page:04d}"
        completed = subprocess.run(
            ["pdftoppm", "-png", "-f", str(page), "-l", str(page), "-r", str(dpi),
             str(pdf), str(prefix)],
            check=False, capture_output=True, text=True,
        )
        if completed.returncode != 0:
            raise GoldEnsembleError(
                "pdftoppm_failed",
                "pdftoppm failed.",
                page=page, stderr=completed.stderr[-400:],
            )
        matches = sorted(dest_dir.glob(f"page-{page:04d}*.png"))
        if not matches:
            raise GoldEnsembleError("pdftoppm_no_png", "pdftoppm wrote no PNG.", page=page)
        written.append(matches[0])
    return written


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped == "null" or stripped == "```json\nnull\n```":
        raise GoldEnsembleError(
            "vision_null_reading",
            "A null vision payload is a channel error, not a dispute.",
        )
    fenced = _JSON_FENCE.search(stripped)
    if fenced:
        stripped = fenced.group(1)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        raise GoldEnsembleError("vision_json_missing", "Vision stdout had no JSON object.")
    try:
        payload = json.loads(stripped[start:end + 1])
    except json.JSONDecodeError as error:
        raise GoldEnsembleError("vision_json_invalid", "Vision stdout was not JSON.") from error
    if not isinstance(payload, dict):
        raise GoldEnsembleError("vision_json_invalid", "Vision JSON must be an object.")
    return payload


def _extract_prompt(pages: list[int]) -> str:
    listed = ", ".join(f"page-{page:04d}*.png" for page in pages)
    return (
        "You are a gold-construction vision channel. Read ONLY the PNG files in "
        "this directory. Do not read any other path. Do not use memory of any "
        "paper. Files: "
        + listed
        + ". Reply with one JSON object, never null, never prose.\n"
        "Contract — both vision channels answer this same question in this "
        "same shape:\n"
        '{"tables":[{"locus":"Table N or Fig. N","page":<int>,'
        '"columns":["polymer","solvent","temperature","value"],'
        '"rows":[{"polymer":"...","solvent":"...","temperature":"...","value":"...",'
        '"cells":["polymer","solvent","temperature","value"]}]}]}\n'
        "Rules: (1) polymer, solvent, temperature, and value are THE fact. "
        "cells is a copy, not a substitute. (2) Omit a row if any of those four "
        "fields is not visible. Do not store the row only in cells. "
        "(3) Do not invent values. (4) If no complete row is visible, "
        'return {"tables":[]}.'
    )


def _agent_command(model: str) -> list[str]:
    """Named Cursor agent CLI. C and C' differ by --model, never by this wrapper."""
    return [
        CHANNEL_C_PRIME_CLI[0], "-p", "--mode", "ask", "--trust",
        "--output-format", "text",
        "--model", model,
    ]


def _run_named_model(
    *,
    command: list[str],
    cwd: Path,
    prompt: str,
    timeout: int,
) -> str:
    hits = scan_firewall_text(" ".join(command) + " " + str(cwd))
    if hits:
        raise GoldEnsembleError(
            "firewall_source_path",
            "Vision command or cwd names a forbidden store.",
            hits=hits,
        )
    completed = subprocess.run(
        command + [prompt],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        raise GoldEnsembleError(
            "vision_cli_failed",
            "Named vision CLI failed.",
            returncode=completed.returncode,
            stderr=(completed.stderr or "")[-600:],
        )
    return completed.stdout


def channel_c(
    *,
    image_dir: Path,
    pages: list[int],
    model: str = CHANNEL_C_MODEL,
    runner: Callable[..., str] | None = None,
    timeout: int = 600,
    **kwargs: Any,
) -> dict[str, Any]:
    """Opus 5 max reasoning via the named Claude CLI. Not a v3 visual look."""
    if "page_image" in kwargs:
        raise GoldEnsembleError(
            "vision_not_authorized",
            "The assembler must not load a page image into the v3 session.",
        )
    require_vision_spend()
    prompt = _extract_prompt(pages)
    command = _agent_command(model)
    raw = (runner or _run_named_model)(command=command, cwd=Path(image_dir), prompt=prompt, timeout=timeout)
    payload = parse_json_object(raw)
    reading = normalize_vision_reading(payload)
    return {
        "channel": "C",
        "read_by": model,
        "pages": list(pages),
        "reading": reading,
        "dropped_incomplete_rows": reading.get("dropped_incomplete_rows"),
        "raw_chars": len(raw),
    }


def channel_c_prime(
    *,
    image_dir: Path,
    pages: list[int],
    model: str = CHANNEL_C_PRIME_MODEL,
    runner: Callable[..., str] | None = None,
    timeout: int = 600,
    **kwargs: Any,
) -> dict[str, Any]:
    """Cursor Grok 4.6 extra-high via the named agent CLI. Not this v3 session."""
    if "page_image" in kwargs:
        raise GoldEnsembleError(
            "vision_not_authorized",
            "The assembler must not load a page image into the v3 session.",
        )
    require_vision_spend()
    assert_distinct_vision_models(CHANNEL_C_MODEL, model)
    prompt = _extract_prompt(pages)
    command = _agent_command(model)
    raw = (runner or _run_named_model)(command=command, cwd=Path(image_dir), prompt=prompt, timeout=timeout)
    payload = parse_json_object(raw)
    reading = normalize_vision_reading(payload)
    return {
        "channel": "C_prime",
        "read_by": model,
        "pages": list(pages),
        "reading": reading,
        "dropped_incomplete_rows": reading.get("dropped_incomplete_rows"),
        "raw_chars": len(raw),
    }


def _norm(value: Any) -> str:
    """Compare B and C without looking at the page. Degree/hyphen/NFKC only."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("℃", " deg c").replace("°C", " deg c").replace("° C", " deg c")
    text = text.replace("°", " deg ")
    text = re.sub(r"[–—−‐]", "-", text)
    return re.sub(r"\s+", " ", text).casefold().strip()


def _column_to_needle_key(column: Any) -> str | None:
    text = _norm(column)
    if text in _NEEDLE_KEYS:
        return text
    if "polymer" in text or "resin" in text:
        return "polymer"
    if "solvent" in text:
        return "solvent"
    if "temp" in text:
        return "temperature"
    if text in {"value", "yield"} or text.startswith("wt"):
        return "value"
    return None


def _needles_from_row(row: Mapping[str, Any], columns: list[Any] | None = None) -> dict[str, str]:
    needles: dict[str, str] = {}
    for key in _NEEDLE_KEYS:
        text = str(row.get(key) or "").strip()
        if text:
            needles[key] = text
    if len(needles) == 4:
        return needles
    cells = [str(cell).strip() for cell in (row.get("cells") or [])]
    if columns and cells and len(columns) == len(cells):
        mapped: dict[str, str] = {}
        for column, cell in zip(columns, cells):
            key = _column_to_needle_key(column)
            if key and cell:
                mapped[key] = cell
        if len(mapped) == 4:
            return mapped
    return needles


def normalize_vision_reading(payload: Any) -> dict[str, Any]:
    """Drop incomplete rows. null is not a reading."""
    if payload is None:
        raise GoldEnsembleError(
            "vision_null_reading",
            "A null vision payload is a channel error, not a dispute.",
        )
    if not isinstance(payload, dict):
        raise GoldEnsembleError("vision_json_invalid", "Vision JSON must be an object.")
    tables_out: list[dict[str, Any]] = []
    dropped = 0
    for table in payload.get("tables") or []:
        columns = list(table.get("columns") or [])
        rows_out: list[dict[str, Any]] = []
        for row in table.get("rows") or []:
            needles = _needles_from_row(row, columns or None)
            if len(needles) != 4:
                dropped += 1
                continue
            item = dict(row)
            item.update(needles)
            item.setdefault("locus", table.get("locus"))
            item.setdefault("page", table.get("page"))
            rows_out.append(item)
        if rows_out:
            tables_out.append({
                "locus": table.get("locus"),
                "page": table.get("page"),
                "columns": columns or list(_NEEDLE_KEYS),
                "rows": rows_out,
            })
    return {"tables": tables_out, "dropped_incomplete_rows": dropped}


def b_and_c_agree(b_line: str, c_row: Mapping[str, Any]) -> dict[str, str] | None:
    """B+C binding: the four C needles must appear in B. Extra C cells are ignored."""
    hay = _norm(b_line)
    if not hay:
        return None
    needles = _needles_from_row(c_row)
    if len(needles) == 4 and all(_norm(value) in hay for value in needles.values()):
        return needles
    cells = [str(cell).strip() for cell in (c_row.get("cells") or []) if str(cell).strip()]
    numeric = [cell for cell in cells if any(ch.isdigit() for ch in cell)]
    if numeric and all(_norm(cell) in hay for cell in numeric) and len(needles) >= 2:
        if all(_norm(value) in hay for value in needles.values()):
            return needles
    return None


def bind_table_cells(
    *,
    paper: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
    channel_c_result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Promote awaiting-C table-cell rows that B and C agree on. No adjudication."""
    model = str(channel_c_result.get("read_by") or "")
    if not model:
        raise GoldEnsembleError("vision_model_unrecorded", "C facts must record read_by.")
    try:
        reading = normalize_vision_reading(channel_c_result.get("reading"))
    except GoldEnsembleError:
        reading = {"tables": []}
    rows: list[Mapping[str, Any]] = []
    for table in reading.get("tables") or []:
        for row in table.get("rows") or []:
            item = dict(row)
            item.setdefault("locus", table.get("locus"))
            item.setdefault("page", table.get("page"))
            rows.append(item)
    b_lines: list[tuple[Mapping[str, Any], str]] = []
    for candidate in candidates:
        b_line = str((candidate.get("readings") or {}).get("B") or candidate.get("evidence_quote") or "")
        b_lines.append((candidate, b_line))
    bound: list[dict[str, Any]] = []
    used_b: set[int] = set()
    used_c: set[int] = set()
    for index, row in enumerate(rows):
        needles = _needles_from_row(row)
        if len(needles) != 4:
            continue
        match_b = None
        for bi, (candidate, b_line) in enumerate(b_lines):
            if bi in used_b:
                continue
            if b_and_c_agree(b_line, row):
                match_b = (bi, candidate, b_line)
                break
        if match_b is None:
            continue
        bi, candidate, b_line = match_b
        used_b.add(bi)
        used_c.add(index)
        fact = dict(candidate)
        fact["scoring_class"] = "bound_fact"
        fact["kind"] = "table_cell"
        fact["needles"] = needles
        fact["status"] = "gold_unsealed"
        fact["confidence"] = "unanimous"
        fact["channels_used"] = ["B", "C"]
        fact["read_by"] = {"B": "pdftotext -layout", "C": model}
        fact["readings"] = {
            **dict(candidate.get("readings") or {}),
            "C": row,
        }
        fact["sealed"] = False
        fact["awaiting"] = []
        validate_fact(fact)
        bound.append(fact)
    for bi, (candidate, b_line) in enumerate(b_lines):
        if bi in used_b:
            continue
        updated = dict(candidate)
        updated["readings"] = {
            **dict(candidate.get("readings") or {}),
            "C": None,
        }
        updated["status"] = "awaiting_C"
        updated["channels_used"] = ["B"]
        bound.append(updated)
    return bound


def c_and_cprime_agree(row_c: Mapping[str, Any], row_cp: Mapping[str, Any]) -> dict[str, str] | None:
    needles_c = _needles_from_row(row_c)
    needles_p = _needles_from_row(row_cp)
    if (
        len(needles_c) == 4
        and len(needles_p) == 4
        and all(_norm(needles_c[key]) == _norm(needles_p[key]) for key in _NEEDLE_KEYS)
    ):
        return needles_c
    cells_c = [_norm(cell) for cell in (row_c.get("cells") or []) if str(cell).strip()]
    cells_p = [_norm(cell) for cell in (row_cp.get("cells") or []) if str(cell).strip()]
    if cells_c and cells_c == cells_p and len(needles_c) == 4:
        return needles_c
    return None


def bind_figure_facts(
    *,
    paper: Mapping[str, Any],
    channel_c_result: Mapping[str, Any],
    channel_c_prime_result: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """C+C' figure-embedded facts. Disputes kept. Same model refused."""
    model_c = str(channel_c_result.get("read_by") or "")
    model_p = str(channel_c_prime_result.get("read_by") or "")
    if not model_c or not model_p:
        raise GoldEnsembleError("vision_model_unrecorded", "C+C' facts must record read_by.")
    assert_distinct_vision_models(model_c, model_p)
    def _complete_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        reading = result.get("reading") or {}
        try:
            reading = normalize_vision_reading(reading)
        except GoldEnsembleError:
            return []
        for table in reading.get("tables") or []:
            for row in table.get("rows") or []:
                if len(_needles_from_row(row, table.get("columns"))) != 4:
                    continue
                item = dict(row)
                item.setdefault("locus", table.get("locus"))
                item.setdefault("page", table.get("page"))
                rows.append(item)
        return rows

    rows_c = _complete_rows(channel_c_result)
    rows_p = _complete_rows(channel_c_prime_result)
    agreed: list[dict[str, Any]] = []
    disputed: list[dict[str, Any]] = []
    used_p: set[int] = set()
    paper_sha = str(paper["sha256"])
    if not rows_c or not rows_p:
        return agreed, disputed
    for row_c in rows_c:
        found = None
        found_idx = None
        for index, row_p in enumerate(rows_p):
            if index in used_p:
                continue
            needles = c_and_cprime_agree(row_c, row_p)
            if needles is None:
                continue
            found = (row_p, needles)
            found_idx = index
            break
        locus = str(row_c.get("locus") or "Fig. 4")
        if found is None:
            disputed.append({
                "fact_id": f"fig-{paper_sha[:12]}-{len(disputed):04d}",
                "paper_sha256": paper_sha,
                "paper_status": paper.get("status"),
                "genre": paper.get("genre"),
                "kind": "table_cell",
                "scoring_class": "bound_fact",
                "locus": locus,
                "page": row_c.get("page"),
                "needles": {},
                "channels_used": ["C", "C_prime"],
                "read_by": {"C": model_c, "C_prime": model_p},
                "readings": {"A": None, "B": None, "C": row_c, "C_prime": None},
                "confidence": "disputed",
                "status": "disputed",
                "sealed": False,
            })
            continue
        used_p.add(found_idx)
        row_p, needles = found
        if len(needles) != 4:
            continue
        fact = {
            "fact_id": f"fig-{paper_sha[:12]}-{len(agreed):04d}",
            "paper_sha256": paper_sha,
            "paper_status": paper.get("status"),
            "genre": paper.get("genre"),
            "kind": "table_cell",
            "scoring_class": "bound_fact",
            "locus": locus,
            "page": row_c.get("page"),
            "needles": needles,
            "channels_used": ["C", "C_prime"],
            "read_by": {"C": model_c, "C_prime": model_p},
            "readings": {"A": None, "B": None, "C": row_c, "C_prime": row_p},
            "confidence": "unanimous",
            "status": "gold_unsealed",
            "sealed": False,
        }
        validate_fact(fact)
        agreed.append(fact)
    return agreed, disputed


def rebind_paper(paper_out: dict[str, Any]) -> dict[str, Any]:
    """Re-apply B+C / C+C' bind from stored readings. No new vision spend."""
    out = dict(paper_out)
    stored = out.get("channel_c_tables")
    candidates = list(out.get("table_cell_candidates") or [])
    if stored and candidates:
        out["table_cell_candidates"] = bind_table_cells(
            paper={"sha256": out.get("paper_sha256"), "status": out.get("paper_status"), "genre": out.get("genre")},
            candidates=candidates,
            channel_c_result=stored,
        )
        out["n_table_cell_gold"] = sum(
            1 for row in out["table_cell_candidates"] if row.get("status") == "gold_unsealed"
        )
    stored_c = out.get("channel_c_figures")
    stored_p = out.get("channel_c_prime_figures")
    if stored_c and stored_p:
        facts, disputes = bind_figure_facts(
            paper={"sha256": out.get("paper_sha256"), "status": out.get("paper_status"), "genre": out.get("genre")},
            channel_c_result=stored_c,
            channel_c_prime_result=stored_p,
        )
        out["figure_embedded"] = facts
        out["figure_disputes"] = disputes
        out["n_figure_embedded"] = len(facts)
        out["n_figure_disputes"] = len(disputes)
    return out


def rebind_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(artifact)
    papers = [rebind_paper(dict(row)) for row in out.get("papers") or []]
    out["papers"] = papers
    density = dict(out.get("density") or {})
    density["n_table_cell_gold"] = sum(int(row.get("n_table_cell_gold") or 0) for row in papers)
    density["n_figure_embedded"] = sum(int(row.get("n_figure_embedded") or 0) for row in papers)
    density["n_figure_disputes"] = sum(int(row.get("n_figure_disputes") or 0) for row in papers)
    out["density"] = density
    out["sealed"] = False
    out["seal_forbidden"] = True
    return out


def figure_pages_from_text(pages: list[str]) -> list[int]:
    found: list[int] = []
    for number, text in enumerate(pages, 1):
        if _FIG4.search(text or ""):
            found.append(number)
    return found


def apply_vision(
    *,
    paper: Mapping[str, Any],
    assembled: Mapping[str, Any],
    staged_pdf: Path,
    work_root: Path,
    run_c: Callable[..., dict[str, Any]] = channel_c,
    run_c_prime: Callable[..., dict[str, Any]] = channel_c_prime,
    tables: bool = True,
    figures: bool = True,
) -> dict[str, Any]:
    """Run C on table-candidate pages and C+C' on Fig. 4 pages. No seal."""
    require_vision_spend()
    spend: list[dict[str, Any]] = list(assembled.get("vision_spend") or [])
    out = dict(assembled)
    candidates = list(assembled.get("table_cell_candidates") or [])
    table_pages = sorted({int(row.get("page") or 0) for row in candidates if row.get("page")})
    table_pages = [page for page in table_pages if page > 0]
    fig_pages = [int(page) for page in assembled.get("fig4_pages") or [] if int(page) > 0]

    if tables and table_pages:
        image_dir = Path(work_root) / "c-tables"
        image_dir.mkdir(parents=True)
        render_pages(staged_pdf, table_pages, image_dir)
        started = time.time()
        c_result = run_c(image_dir=image_dir, pages=table_pages)
        spend.append({
            "channel": "C", "model": c_result["read_by"],
            "paper_sha256": paper["sha256"], "pages": table_pages,
            "role": "table_cell", "wall_s": round(time.time() - started, 3),
        })
        out["channel_c_tables"] = {
            "read_by": c_result.get("read_by"),
            "pages": table_pages,
            "reading": c_result.get("reading"),
        }
        out["table_cell_candidates"] = bind_table_cells(
            paper=paper, candidates=candidates, channel_c_result=c_result,
        )
        out["n_table_cell_gold"] = sum(
            1 for row in out["table_cell_candidates"] if row.get("status") == "gold_unsealed"
        )

    figure_facts: list[dict[str, Any]] = list(assembled.get("figure_embedded") or [])
    figure_disputes: list[dict[str, Any]] = list(assembled.get("figure_disputes") or [])
    if figures and fig_pages:
        image_dir = Path(work_root) / "c-figures"
        image_dir.mkdir(parents=True)
        render_pages(staged_pdf, fig_pages, image_dir)
        started = time.time()
        c_result = run_c(image_dir=image_dir, pages=fig_pages)
        spend.append({
            "channel": "C", "model": c_result["read_by"],
            "paper_sha256": paper["sha256"], "pages": fig_pages,
            "role": "figure_embedded", "wall_s": round(time.time() - started, 3),
        })
        started = time.time()
        cp_result = run_c_prime(image_dir=image_dir, pages=fig_pages)
        spend.append({
            "channel": "C_prime", "model": cp_result["read_by"],
            "paper_sha256": paper["sha256"], "pages": fig_pages,
            "role": "figure_embedded", "wall_s": round(time.time() - started, 3),
        })
        out["channel_c_figures"] = {
            "read_by": c_result.get("read_by"),
            "pages": fig_pages,
            "reading": c_result.get("reading"),
        }
        out["channel_c_prime_figures"] = {
            "read_by": cp_result.get("read_by"),
            "pages": fig_pages,
            "reading": cp_result.get("reading"),
        }
        figure_facts, figure_disputes = bind_figure_facts(
            paper=paper,
            channel_c_result=c_result,
            channel_c_prime_result=cp_result,
        )
    if figures:
        out["figure_embedded"] = figure_facts
        out["figure_disputes"] = figure_disputes
        out["n_figure_embedded"] = len(figure_facts)
        out["n_figure_disputes"] = len(figure_disputes)
    out["vision_spend"] = spend
    out["vision_called"] = bool(spend)
    return out


def demote_string_existence(paper_out: dict[str, Any]) -> dict[str, Any]:
    """Bare A+B numeric tokens are a diagnostic, not a fact class."""
    out = dict(paper_out)
    existing = list(out.get("string_existence_diagnostic") or [])
    minted = list(out.get("string_existence") or [])
    merged = existing + minted
    for row in merged:
        row["status"] = "diagnostic_not_fact"
        row["role"] = "diagnostic"
        row["needles"] = {}
    out["string_existence"] = []
    out["string_existence_diagnostic"] = merged
    out["n_string_existence"] = 0
    out["n_string_existence_diagnostic"] = len(merged)
    return out


def repair_c5_draft(
    *,
    draft_path: Path,
    pdf_root: Path,
    figures: bool = True,
    tables: bool = False,
) -> dict[str, Any]:
    """Re-run vision with the shared contract. Does not seal. No new papers."""
    from .gold_ensemble import file_sha256, iter_pdfs

    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))
    disk: dict[str, Path] = {}
    for path in iter_pdfs(pdf_root):
        disk.setdefault(file_sha256(path), path)
    papers_out: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="c5-repair-") as raw:
        stage_root = Path(raw)
        for paper in draft.get("papers") or []:
            paper = demote_string_existence(dict(paper))
            digest = str(paper.get("paper_sha256") or "")
            source = disk.get(digest)
            need_fig = figures and bool(paper.get("fig4_pages"))
            need_tab = tables and bool(paper.get("table_cell_candidates"))
            if source is None or (not need_fig and not need_tab):
                papers_out.append(rebind_paper(paper))
                print(
                    f"C5 repair skip {len(papers_out)} {digest[:16]} fig={need_fig} tab={need_tab}",
                    file=sys.stderr, flush=True,
                )
                continue
            work = stage_root / digest
            work.mkdir()
            try:
                repaired = apply_vision(
                    paper={
                        "sha256": digest,
                        "status": paper.get("paper_status"),
                        "genre": paper.get("genre"),
                        "filename": paper.get("filename"),
                    },
                    assembled=paper,
                    staged_pdf=source,
                    work_root=work,
                    tables=need_tab,
                    figures=need_fig,
                )
            except GoldEnsembleError as error:
                paper["repair_error"] = error.code
                papers_out.append(rebind_paper(paper))
                continue
            papers_out.append(rebind_paper(repaired))
            print(
                f"C5 repair {len(papers_out)} {digest[:16]} "
                f"tcg={repaired.get('n_table_cell_gold')} "
                f"fig={repaired.get('n_figure_embedded')} "
                f"fd={repaired.get('n_figure_disputes')}",
                file=sys.stderr, flush=True,
            )
            Path(draft_path).write_text(
                json.dumps({
                    "schema": "dissolve.gold-ensemble-c5.partial.v1",
                    "sealed": False,
                    "n_papers_done": len(papers_out),
                    "papers": papers_out + list((draft.get("papers") or [])[len(papers_out):]),
                }, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    draft["papers"] = papers_out
    draft = rebind_artifact(draft)
    density = dict(draft.get("density") or {})
    density["n_string_existence"] = 0
    density["n_string_existence_diagnostic"] = sum(
        int(row.get("n_string_existence_diagnostic") or 0) for row in papers_out
    )
    density["n_table_cell_gold"] = sum(int(row.get("n_table_cell_gold") or 0) for row in papers_out)
    density["n_figure_embedded"] = sum(int(row.get("n_figure_embedded") or 0) for row in papers_out)
    density["n_figure_disputes"] = sum(int(row.get("n_figure_disputes") or 0) for row in papers_out)
    draft["density"] = density
    draft["sealed"] = False
    draft["seal_forbidden"] = True
    draft["vision_spend"] = [item for row in papers_out for item in row.get("vision_spend") or []]
    Path(draft_path).write_text(json.dumps(draft, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    draft["wrote"] = str(draft_path)
    draft["wrote_sha256"] = file_sha256(Path(draft_path))
    return draft
