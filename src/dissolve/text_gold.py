"""TEXT_CHUNKING_SPEC.v1 text gold. Two named frontier reads. No scores. No seal.

The v3 pair is not a gold reader and does not adjudicate. Extraction is a
named CLI. Needle spans are computed here from saved canonical_text before
any sweep score exists. A gold of only <200-char spans is refused.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .gold_ensemble import GoldEnsembleError, file_sha256

SPEC_SHA256 = "f7bdc3f7ce519cfc456ff7249d804848dfadaaca21a5a4f57cc44518b8f50b55"
READER_MODEL = "claude-opus-5-thinking-high"
CONFIRMER_MODEL = "cursor-grok-4.6-high"
AGENT_CLI = "/home/aaltamimi2/.local/bin/agent"
NEEDLE_KEYS = ("subject", "qualifier", "value")
OPTIONAL_NEEDLE_KEY = "context"
ALLOWED_NEEDLE_KEYS = NEEDLE_KEYS + (OPTIONAL_NEEDLE_KEY,)
FACT_CLASSES = (
    "model_calibration",
    "chosen_versus_cited",
    "screening_rationale",
    "contamination",
    "process_provenance",
)
SPAN_BUCKETS = ("<200", "200-600", "600-1500", ">1500")
SCHEMA = "dissolve.text-gold.v1.unsealed"
GOLD_V1_SHA256 = "345b426bd66f995b3b78a10e796afb6df013299dd6769379192b59d0d97dfaab"
CANONICAL_DIR = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/canonical")
MANIFEST_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/CANONICAL_MANIFEST.v1.json")
DEFAULT_OUT_DIR = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/text_chunking")
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_SCORE_KEYS = (
    "contain_bound_fact", "retrievable", "retrievable@5", "retrievable_at_k",
    "recall_at_k", "precision_at_k", "n_retrievable_at_k", "cross_fact_hits",
    "needle_span_preserved", "f1", "sweep", "scores",
)


class TextGoldError(GoldEnsembleError):
    """Typed text-gold failure. Not a seal and not a score."""


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse the first JSON object from model stdout. Not a vision channel."""
    stripped = str(text or "").strip()
    fenced = _JSON_FENCE.search(stripped)
    if fenced:
        stripped = fenced.group(1)
    start = stripped.find("{")
    if start < 0:
        raise TextGoldError("text_json_missing", "Named text CLI stdout had no JSON object.")
    try:
        payload, _consumed = json.JSONDecoder().raw_decode(stripped, start)
    except json.JSONDecodeError as error:
        raise TextGoldError("text_json_invalid", "Named text CLI stdout was not JSON.") from error
    if not isinstance(payload, dict):
        raise TextGoldError("text_json_invalid", "Named text CLI JSON must be an object.")
    return payload


def span_bucket(span: int) -> str:
    if span < 200:
        return "<200"
    if span < 600:
        return "200-600"
    if span < 1500:
        return "600-1500"
    return ">1500"


def nonempty_needles(needles: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (needles or {}).items():
        text = str(value or "").strip()
        if not text:
            continue
        if key not in ALLOWED_NEEDLE_KEYS:
            continue
        out[str(key)] = text
    return out


def locate_needles(
    canonical_text: str,
    needles: Mapping[str, Any],
    *,
    span_start: int,
    span_end: int,
) -> dict[str, Any] | None:
    """Offsets are Unicode code points into saved canonical_text."""
    values = nonempty_needles(needles)
    if not 2 <= len(values) <= 4:
        return None
    region = canonical_text[span_start:span_end]
    folded = region.casefold()
    hits: list[dict[str, Any]] = []
    for key, value in values.items():
        idx = folded.find(value.casefold())
        if idx < 0:
            return None
        start = span_start + idx
        end = start + len(value)
        hits.append({"key": key, "char_start": start, "char_end": end})
    first = min(hit["char_start"] for hit in hits)
    last = max(hit["char_end"] for hit in hits)
    span = last - first
    ordered = sorted(hits, key=lambda hit: hit["char_start"])
    gaps = [
        ordered[index + 1]["char_start"] - ordered[index]["char_end"]
        for index in range(len(ordered) - 1)
    ]
    # Adjacent tokens (single spaces) are not a chunking test.
    separated = bool(gaps) and max(gaps) >= 8
    return {
        "needle_offsets": hits,
        "needle_first_char": first,
        "needle_last_char": last,
        "needle_span_chars": span,
        "needles_separated": separated,
    }


def bind_evidence(canonical_text: str, evidence_quote: str) -> tuple[int, int] | None:
    quote = str(evidence_quote or "")
    if not quote.strip():
        return None
    start = canonical_text.find(quote)
    if start < 0:
        return None
    return start, start + len(quote)


def finalize_fact(
    raw: Mapping[str, Any],
    *,
    canonical_text: str,
    paper_sha256: str,
    paper_status: str,
    read_by: str,
    confirmed_by: str,
    fact_id: str,
) -> dict[str, Any] | None:
    """Mechanical bind. Does not rewrite needles and does not score."""
    if read_by == confirmed_by:
        raise TextGoldError(
            "same_text_model_twice",
            "read_by must differ from confirmed_by.",
            model=read_by,
        )
    quote = str(raw.get("evidence_quote") or "")
    bound = bind_evidence(canonical_text, quote)
    if bound is None:
        return None
    start, end = bound
    located = locate_needles(
        canonical_text, raw.get("needles") or {}, span_start=start, span_end=end,
    )
    if located is None or not located["needles_separated"]:
        return None
    fact_class = str(raw.get("fact_class") or raw.get("class") or "")
    if fact_class not in FACT_CLASSES:
        return None
    section = str(raw.get("section") or "").strip()
    if not section:
        return None
    query = str(raw.get("query") or "").strip()
    if not query:
        return None
    if any(raw.get(key) for key in _SCORE_KEYS):
        return None
    scoring = str(raw.get("scoring_class") or "")
    if scoring in {"string_existence", "bound_fact"}:
        return None
    return {
        "fact_id": fact_id,
        "paper_sha256": paper_sha256,
        "paper_status": paper_status,
        "kind": "prose",
        "fact_class": fact_class,
        "needles": nonempty_needles(raw.get("needles") or {}),
        "evidence_quote": quote,
        "char_start": start,
        "char_end": end,
        "needle_span_chars": located["needle_span_chars"],
        "needle_first_char": located["needle_first_char"],
        "needle_last_char": located["needle_last_char"],
        "needle_offsets": located["needle_offsets"],
        "query": query,
        "section": section,
        "read_by": read_by,
        "confirmed_by": confirmed_by,
        "status": "gold_unsealed",
    }


def span_histogram(facts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Publish with the gold, before any score is read."""
    spans = [int(fact["needle_span_chars"]) for fact in facts]
    counts = {bucket: 0 for bucket in SPAN_BUCKETS}
    for span in spans:
        counts[span_bucket(span)] += 1
    n = len(spans)
    ordered = sorted(spans)
    return {
        "schema": "dissolve.text-gold-span-histogram.v1",
        "spec_sha256": SPEC_SHA256,
        "n_facts": n,
        "buckets": {
            bucket: {
                "n": counts[bucket],
                "frac": (counts[bucket] / n) if n else 0.0,
            }
            for bucket in SPAN_BUCKETS
        },
        "min": ordered[0] if ordered else None,
        "max": ordered[-1] if ordered else None,
        "median": ordered[n // 2] if ordered else None,
        "all_short": bool(n) and counts["<200"] == n,
    }


def refuse_all_short(histogram: Mapping[str, Any]) -> None:
    """A gold of only <200-char spans has not tested chunking."""
    if histogram.get("all_short"):
        raise TextGoldError(
            "gold_spans_all_short",
            "A gold whose needle_span_chars are all <200 has not tested chunking.",
            n_facts=histogram.get("n_facts"),
        )


def density_record(facts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sections = {str(fact.get("section") or "") for fact in facts if fact.get("section")}
    classes = {str(fact.get("fact_class") or "") for fact in facts}
    n = len(facts)
    short = n < 8 or len(sections) < 3
    reason = None
    if n < 8:
        reason = "fewer_than_8_prose_facts"
    elif len(sections) < 3:
        reason = "fewer_than_3_sections"
    return {
        "n_facts": n,
        "n_sections": len(sections),
        "sections": sorted(sections),
        "fact_classes": sorted(classes),
        "has_chosen_versus_cited": "chosen_versus_cited" in classes,
        "short": short,
        "short_reason": reason,
    }


def _agent_command(model: str) -> list[str]:
    return [
        AGENT_CLI, "-p", "--mode", "ask", "--trust",
        "--output-format", "text",
        "--model", model,
    ]


def argv_safe_prompt(prompt: str) -> str:
    """OS argv cannot contain NUL. Bind still uses the saved canonical_text."""
    return prompt.replace("\x00", "")


def run_named_text_model(
    *,
    model: str,
    prompt: str,
    cwd: Path,
    timeout: int,
    runner: Callable[..., str] | None = None,
) -> str:
    if model not in {READER_MODEL, CONFIRMER_MODEL}:
        raise TextGoldError(
            "unnamed_text_model",
            "Text gold may call only the two owner-named slugs.",
            model=model,
        )
    if runner is not None:
        return runner(command=_agent_command(model), cwd=cwd, prompt=prompt, timeout=timeout)
    completed = subprocess.run(
        _agent_command(model) + [argv_safe_prompt(prompt)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        raise TextGoldError(
            "text_cli_failed",
            "Named text CLI failed.",
            returncode=completed.returncode,
            stderr=(completed.stderr or "")[-600:],
        )
    return completed.stdout


def extract_prompt(canonical_text: str) -> str:
    return (
        "You are a text-only gold reader. Read ONLY the canonical_text below. "
        "Do not use memory of any paper. Do not extract tables or figures. "
        "Reply with one JSON object, never null, never prose.\n"
        'Contract: {"facts":[{"fact_class":"model_calibration|chosen_versus_cited|'
        'screening_rationale|contamination|process_provenance",'
        '"needles":{"subject":"...","qualifier":"...","value":"..."},'
        '"evidence_quote":"<verbatim substring of the text>",'
        '"section":"...","query":"<natural-language retrieval query>"}]}\n'
        "Rules: (1) 2 to 4 nonempty needles from subject, qualifier, value, "
        "and optional context. (2) Needles MUST be separated in the text — "
        "not five adjacent words. Prefer facts whose first-to-last needle "
        "span is hundreds or thousands of characters. (3) evidence_quote is "
        "verbatim from the text and must contain every needle. (4) Five "
        "prose classes only. Include chosen_versus_cited when the text has "
        "a cited condition that was not used. (5) No table cells, no figure "
        "values, no invented numbers. (6) At least 8 facts across at least "
        "3 sections when the text supports it; do not pad. (7) Do not emit "
        "scores, F1, or chunking metrics.\n"
        "canonical_text:\n"
        + canonical_text
    )


def confirm_prompt(canonical_text: str, facts: Sequence[Mapping[str, Any]]) -> str:
    payload = []
    for index, fact in enumerate(facts):
        payload.append({
            "index": index,
            "fact_class": fact.get("fact_class"),
            "needles": fact.get("needles"),
            "evidence_quote": fact.get("evidence_quote"),
            "section": fact.get("section"),
            "query": fact.get("query"),
        })
    return (
        "You are a text-only gold confirmer. Read ONLY the canonical_text and "
        "the candidate facts. Do not rewrite needles. Do not resolve a dispute "
        "by inventing a third reading. Reply with one JSON object.\n"
        'Contract: {"verdicts":[{"index":<int>,"confirm":true|false}]}\n'
        "confirm=true only if the evidence_quote is verbatim in the text, "
        "every needle appears in that quote, the needles are separated, the "
        "fact is prose (not a table or figure), and the claim is true to the "
        "text. Otherwise confirm=false. Do not add facts.\n"
        "candidates:\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\ncanonical_text:\n"
        + canonical_text
    )


def parse_facts_payload(raw: str) -> list[dict[str, Any]]:
    payload = parse_json_object(raw)
    rows = payload.get("facts")
    if not isinstance(rows, list):
        raise TextGoldError("text_json_missing_facts", "Extractor JSON needs a facts list.")
    return [row for row in rows if isinstance(row, dict)]


def parse_verdicts(raw: str, n_facts: int) -> dict[int, bool]:
    payload = parse_json_object(raw)
    rows = payload.get("verdicts")
    if not isinstance(rows, list):
        raise TextGoldError("text_json_missing_verdicts", "Confirmer JSON needs verdicts.")
    out: dict[int, bool] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row["index"])
        except (KeyError, TypeError, ValueError):
            continue
        if index < 0 or index >= n_facts:
            continue
        out[index] = bool(row.get("confirm"))
    return out


def models_for_paper(paper_index: int) -> tuple[str, str]:
    if paper_index % 2 == 0:
        return READER_MODEL, CONFIRMER_MODEL
    return CONFIRMER_MODEL, READER_MODEL


def mint_one_paper(
    *,
    canonical: Mapping[str, Any],
    paper_sha256: str,
    paper_status: str,
    paper_index: int,
    runner: Callable[..., str] | None,
    timeout: int = 600,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Two named reads. Disagreements dropped, not resolved. No scores."""
    text = str(canonical.get("canonical_text") or "")
    if not text:
        raise TextGoldError("empty_canonical_text", "Saved canonical_text is empty.")
    reader, confirmer = models_for_paper(paper_index)
    work = Path(cwd or "/tmp")
    calls: list[dict[str, Any]] = []
    started = time.time()
    extract_raw = run_named_text_model(
        model=reader, prompt=extract_prompt(text), cwd=work,
        timeout=timeout, runner=runner,
    )
    calls.append({"model": reader, "role": "read", "wall_s": round(time.time() - started, 3)})
    extracted = parse_facts_payload(extract_raw)
    confirm_started = time.time()
    confirm_raw = run_named_text_model(
        model=confirmer, prompt=confirm_prompt(text, extracted), cwd=work,
        timeout=timeout, runner=runner,
    )
    calls.append({
        "model": confirmer, "role": "confirm",
        "wall_s": round(time.time() - confirm_started, 3),
    })
    verdicts = parse_verdicts(confirm_raw, len(extracted))
    kept: list[dict[str, Any]] = []
    dropped = 0
    for index, raw in enumerate(extracted):
        if not verdicts.get(index):
            dropped += 1
            continue
        fact = finalize_fact(
            raw, canonical_text=text, paper_sha256=paper_sha256,
            paper_status=paper_status, read_by=reader, confirmed_by=confirmer,
            fact_id=f"tg-{paper_sha256[:12]}-{len(kept) + 1:03d}",
        )
        if fact is None:
            dropped += 1
            continue
        kept.append(fact)
    density = density_record(kept)
    histogram = span_histogram(kept)
    return {
        "paper_sha256": paper_sha256,
        "paper_status": paper_status,
        "read_by": reader,
        "confirmed_by": confirmer,
        "n_extracted": len(extracted),
        "n_dropped": dropped,
        "n_kept": len(kept),
        "facts": kept,
        "density": density,
        "span_histogram": histogram,
        "calls": calls,
    }


def assemble_gold(papers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    facts = [fact for paper in papers for fact in paper.get("facts") or []]
    histogram = span_histogram(facts)
    refuse_all_short(histogram)
    if any(key in (paper or {}) for paper in papers for key in _SCORE_KEYS):
        raise TextGoldError("scores_in_gold", "Gold must not carry sweep scores.")
    return {
        "schema": SCHEMA,
        "spec_sha256": SPEC_SHA256,
        "sealed": False,
        "seal_forbidden": True,
        "gold_v1_unmoved": GOLD_V1_SHA256,
        "n_papers": len(papers),
        "n_facts": len(facts),
        "span_histogram": histogram,
        "papers": list(papers),
        "facts": facts,
    }


def write_gold_with_histogram(gold: Mapping[str, Any], out_dir: Path) -> dict[str, str]:
    """Persist gold and histogram together. Does not write a score file."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gold_path = out_dir / "GOLD.text.v1.unsealed.json"
    hist_path = out_dir / "NEEDLE_SPAN_HISTOGRAM.v1.json"
    if (out_dir / "GOLD.v2.json").exists():
        raise TextGoldError("gold_v2_present", "Do not write GOLD.v2.json.")
    gold_path.write_text(json.dumps(gold, indent=2, ensure_ascii=False) + "\n")
    hist_path.write_text(
        json.dumps(gold["span_histogram"], indent=2, ensure_ascii=False) + "\n"
    )
    return {
        "gold_sha256": file_sha256(gold_path),
        "histogram_sha256": file_sha256(hist_path),
        "gold_path": str(gold_path),
        "histogram_path": str(hist_path),
    }


def load_canonical(paper_sha256: str, canonical_dir: Path | None = None) -> dict[str, Any]:
    path = Path(canonical_dir or CANONICAL_DIR) / f"{paper_sha256}.v1.json"
    if not path.is_file():
        raise TextGoldError("canonical_missing", "Saved canonical is missing.", path=str(path))
    return json.loads(path.read_text())


def manifest_papers(manifest_path: Path | None = None) -> list[dict[str, Any]]:
    data = json.loads(Path(manifest_path or MANIFEST_PATH).read_text())
    rows = []
    for row in data.get("documents") or []:
        rows.append({
            "pdf_sha256": row["pdf_sha256"],
            "canonical_sha256": row["canonical_sha256"],
            "status": row.get("status") or "indexed",
            "filename": row.get("filename") or "",
        })
    return rows


def write_spend(calls: Sequence[Mapping[str, Any]], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "dissolve.text-gold-spend.v1",
        "grant": "TEXT_CHUNKING_SPEC.v1 ADMIT spec-text-chunking-f7bdc3f7",
        "models": [READER_MODEL, CONFIRMER_MODEL],
        "n_calls": len(calls),
        "calls": list(calls),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def write_draft(papers: Sequence[Mapping[str, Any]], out_dir: Path) -> dict[str, str]:
    """Incremental persist. Does not refuse all-short until assemble_gold."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    facts = [fact for paper in papers for fact in paper.get("facts") or []]
    draft = {
        "schema": SCHEMA + ".draft",
        "spec_sha256": SPEC_SHA256,
        "sealed": False,
        "seal_forbidden": True,
        "n_papers": len(papers),
        "n_facts": len(facts),
        "span_histogram": span_histogram(facts),
        "papers": list(papers),
        "facts": facts,
    }
    draft_path = out_dir / "GOLD.text.v1.draft.json"
    hist_path = out_dir / "NEEDLE_SPAN_HISTOGRAM.v1.json"
    draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False) + "\n")
    hist_path.write_text(
        json.dumps(draft["span_histogram"], indent=2, ensure_ascii=False) + "\n"
    )
    return {
        "draft_sha256": file_sha256(draft_path),
        "histogram_sha256": file_sha256(hist_path),
    }


def run_corpus(
    *,
    only: Sequence[str] | None = None,
    limit: int | None = None,
    out_dir: Path | None = None,
    runner: Callable[..., str] | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    """Mint text gold for saved C3 canonicals. Does not score. Does not seal."""
    dest = Path(out_dir or DEFAULT_OUT_DIR)
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / "GOLD.v2.json").exists():
        raise TextGoldError("gold_v2_present", "Do not write GOLD.v2.json.")
    rows = manifest_papers()
    want = set(only) if only else None
    papers: list[dict[str, Any]] = []
    spend: list[dict[str, Any]] = []
    draft_path = dest / "GOLD.text.v1.draft.json"
    if draft_path.is_file():
        prior = json.loads(draft_path.read_text())
        papers = list(prior.get("papers") or [])
        spend_path = dest / "TEXT_GOLD_SPEND.v1.json"
        if spend_path.is_file():
            spend = list(json.loads(spend_path.read_text()).get("calls") or [])
    done = {str(paper.get("paper_sha256") or "") for paper in papers}
    seen = 0
    for index, row in enumerate(rows):
        sha = row["pdf_sha256"]
        if want is not None and sha not in want:
            continue
        if sha in done:
            continue
        if limit is not None and seen >= int(limit):
            break
        seen += 1
        canonical = load_canonical(sha)
        if file_sha256(CANONICAL_DIR / f"{sha}.v1.json") != row["canonical_sha256"]:
            raise TextGoldError(
                "canonical_digest_mismatch",
                "Saved canonical does not match the manifest pin.",
                paper=sha,
            )
        work = Path("/tmp") / f"text_gold_{sha[:12]}"
        work.mkdir(parents=True, exist_ok=True)
        for leftover in work.iterdir():
            leftover.unlink()
        paper = mint_one_paper(
            canonical=canonical,
            paper_sha256=sha,
            paper_status=str(row["status"]),
            paper_index=index,
            runner=runner,
            timeout=timeout,
            cwd=work,
        )
        paper["filename"] = row["filename"]
        papers.append(paper)
        for call in paper["calls"]:
            spend.append({"paper_sha256": sha, **call})
        write_spend(spend, dest / "TEXT_GOLD_SPEND.v1.json")
        write_draft(papers, dest)
    facts = [fact for paper in papers for fact in paper.get("facts") or []]
    histogram = span_histogram(facts)
    summary = {
        "n_papers": len(papers),
        "n_facts": len(facts),
        "n_calls": len(spend),
        "all_short": histogram["all_short"],
        "span_histogram": histogram,
    }
    if histogram["all_short"] or not facts:
        summary["status"] = "draft_only"
        return summary
    gold = assemble_gold(papers)
    written = write_gold_with_histogram(gold, dest)
    written.update(summary)
    written["status"] = "unsealed"
    return written
