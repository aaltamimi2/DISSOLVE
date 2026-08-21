"""Autonomous, typed literature extraction into the durable DISSOLVE graph."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from xml.etree import ElementTree

from .contracts import parse_tool_result, tool_error, tool_success

_SCHEMA = "dissolve.literature-graph-ingest.v1"
_MODEL_ID = "openai:muse-spark-1.2"
_MODEL_LABEL = "muse-spark-1.2"
_BASE_URL = "https://api.meta.ai/v1"
_PROMPT_VERSION = "typed-literature-extraction-v2"
_RECORD_CLASSES = (
    "SolubilityPoint", "ChiParameter", "HSPRecord", "PartitionRecord",
    "LeachingRecord", "TgRecord", "ProcessClaim", "CompositionClaim",
)
_ANCHORS = {
    "SolubilityPoint": "cosmo_fitted_thermodynamics",
    "ChiParameter": "cosmo_fitted_thermodynamics",
    "HSPRecord": "integrated_hsp_asset",
    "PartitionRecord": "zhou_partitioning",
    "LeachingRecord": "zhou_partitioning",
    "ProcessClaim": "tea_lca_engine",
    "CompositionClaim": "tea_lca_engine",
    "TgRecord": "none",
}


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")
    if not text:
        raise ValueError("library_id must contain letters or numbers")
    return text


def _local_acquisition(path: Path, library_id: str) -> dict[str, Any]:
    """Build one immutable AcquireEnvelope without a network acquisition step."""
    source = path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(str(source))
    payload = source.read_bytes()
    source_sha = _sha_bytes(payload)
    suffix = source.suffix.casefold()
    kind = "patent" if "patent" in source.name.casefold() else "paper"
    media = {
        ".pdf": "application/pdf", ".xml": "application/xml",
        ".txt": "text/plain", ".md": "text/markdown",
    }.get(suffix) or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    title = source.stem.replace("_", " ").replace("-", " ").strip()
    if suffix == ".xml":
        try:
            root = ElementTree.fromstring(payload)
            title_node = root.find(".//article-title") or root.find(".//title")
            if title_node is not None:
                title = " ".join("".join(title_node.itertext()).split()) or title
        except ElementTree.ParseError:
            pass
    document_id = f"DOC-{source_sha[:20].upper()}"
    from datetime import datetime, timezone
    acquired_at = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat()
    return {
        "schema": "dissolve.acquire.v1", "library_id": library_id,
        "adapter": {
            "adapter_name": "local_file", "adapter_version": "1.0",
            "document_kinds": [kind], "operations": ["acquire"],
            "structured_extras": [], "recency_fields": [],
            "authentication": "none", "pagination": "none",
        },
        "document": {
            "library_id": library_id, "document_id": document_id,
            "document_kind": kind, "title": title, "source_adapter": "local_file",
            "source_identifiers": {"source_sha256": source_sha}, "authors": [],
            "published_date": None, "updated_date": None, "journal": None,
            "content_sha256": source_sha, "acquired_at": acquired_at,
        },
        "artifacts": [{
            "artifact_id": f"ART-{source_sha[:20].upper()}", "role": "full_text",
            "media_type": media, "sha256": source_sha, "packed_path": str(source),
            "byte_count": len(payload),
        }],
        "structured_extras": {"reference_ids": [], "supplement_artifact_ids": []},
        "warnings": [],
    }


def _jats_bridge(path: Path) -> dict[str, Any]:
    """Normalize JATS/XML or plain text into the shared parser bridge."""
    items: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    if path.suffix.casefold() == ".xml":
        try:
            root = ElementTree.parse(path).getroot()
        except ElementTree.ParseError as error:
            from .research import LiteratureContractError
            raise LiteratureContractError(
                "parser_xml_invalid", "Local XML input is not well formed.", path=str(path),
            ) from error
        order = 0
        for element in root.iter():
            tag = str(element.tag).rsplit("}", 1)[-1]
            if tag not in {"article-title", "title", "p", "caption", "abstract", "claim"}:
                continue
            text = " ".join("".join(element.itertext()).split())
            if not text or any(text == row["text"] for row in items[-2:]):
                continue
            label = "heading" if tag in {"article-title", "title"} else "claim" if tag == "claim" else "caption" if tag == "caption" else "text"
            items.append({"id": f"XML-B{order + 1:05d}", "label": label, "order": order, "text": text, "confidence": 1.0})
            order += 1
        for table_index, wrapper in enumerate(
            (node for node in root.iter() if str(node.tag).rsplit("}", 1)[-1] == "table-wrap"), 1,
        ):
            rows = [
                node for node in wrapper.iter()
                if str(node.tag).rsplit("}", 1)[-1] == "tr"
            ]
            if not rows:
                continue
            block_id = f"XML-T{table_index:05d}-B"
            table_id = f"XML-T{table_index:05d}"
            cells, rendered_rows = [], []
            for row_index, row in enumerate(rows):
                columns = [
                    node for node in list(row)
                    if str(node.tag).rsplit("}", 1)[-1] in {"th", "td"}
                ]
                values = [" ".join("".join(cell.itertext()).split()) for cell in columns]
                rendered_rows.append(" | ".join(values))
                for column_index, (cell, text) in enumerate(zip(columns, values, strict=True)):
                    cells.append({
                        "row": row_index, "column": column_index, "row_span": int(cell.attrib.get("rowspan", 1)),
                        "column_span": int(cell.attrib.get("colspan", 1)),
                        "is_header": str(cell.tag).rsplit("}", 1)[-1] == "th",
                        "text": text, "block_refs": [block_id],
                    })
            caption = next((
                " ".join("".join(node.itertext()).split())
                for node in wrapper.iter() if str(node.tag).rsplit("}", 1)[-1] == "caption"
            ), "")
            table_text = "Table" + (f" ({caption})" if caption else "") + ": " + " ;; ".join(rendered_rows)
            items.append({"id": block_id, "label": "text", "order": order, "text": table_text, "confidence": 1.0})
            order += 1
            tables.append({"id": table_id, "cells": cells})
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        for order, paragraph in enumerate(part for part in re.split(r"\n\s*\n", text) if part.strip()):
            items.append({"id": f"TXT-B{order + 1:05d}", "label": "text", "order": order,
                          "text": " ".join(paragraph.split()), "confidence": 1.0})
    if not items:
        from .research import LiteratureContractError
        raise LiteratureContractError("parser_text_empty", "Local text/XML input contains no extractable text.")
    return {
        "backend": "jats" if path.suffix.casefold() == ".xml" else "local_text",
        "version": "stdlib-1", "items": items, "tables": tables,
        "quality_flags": [], "fallback_reason": None,
    }


def _pypdf_bridge(path: Path, fallback_reason: str) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
        import pypdf
    except ImportError as error:
        from .research import LiteratureContractError
        raise LiteratureContractError(
            "parser_backend_unavailable", "The typed pypdf fallback is unavailable.",
            backend="pypdf", fallback_reason=fallback_reason,
        ) from error
    try:
        reader = PdfReader(str(path))
        items = []
        for page_number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            for paragraph in (part for part in re.split(r"\n\s*\n|(?<=\.)\s*\n", text) if part.strip()):
                items.append({
                    "id": f"PDF-P{page_number:04d}-B{len(items) + 1:05d}", "label": "text",
                    "order": len(items), "page": page_number, "text": " ".join(paragraph.split()),
                    "confidence": 0.75,
                })
        if not items:
            from .research import LiteratureContractError
            raise LiteratureContractError(
                "parser_backend_failed", "pypdf extracted no text from the PDF.",
                backend="pypdf", fallback_reason=fallback_reason,
            )
        return {
            "backend": "pypdf", "version": getattr(pypdf, "__version__", "unknown"),
            "items": items, "tables": [], "quality_flags": ["layout_degraded"],
            "fallback_reason": fallback_reason,
        }
    except Exception as error:
        from .research import LiteratureContractError
        if isinstance(error, LiteratureContractError):
            raise
        raise LiteratureContractError(
            "parser_backend_failed", "pypdf could not parse the acquired PDF.",
            backend="pypdf", error_type=type(error).__name__, fallback_reason=fallback_reason,
        ) from error


def _parse(acquisition: Mapping[str, Any]) -> dict[str, Any]:
    """Production cascade: Docling, then DeepDoc, then pypdf.

    The one-paper experiment must not call this. It uses
    ``research.parse_experiment_document(backend=...)``, which raises instead
    of falling through.
    """
    from . import research
    source = Path(str(research._source_artifact(acquisition)["packed_path"]))
    if source.suffix.casefold() in {".xml", ".txt", ".md"}:
        return research.parse_document_structure(
            acquisition, parser_payload=_jats_bridge(source),
            parsed_at=str(acquisition["document"]["acquired_at"]),
        )
    try:
        bridge = research._run_docling(source)
    except research.LiteratureContractError as docling_error:
        docling_reason = f"docling_{docling_error.code}"
        try:
            bridge = research._run_deepdoc(source, fallback_reason=docling_reason)
        except research.LiteratureContractError as deepdoc_error:
            bridge = _pypdf_bridge(source, f"{docling_reason};deepdoc_{deepdoc_error.code}")
    return research.parse_document_structure(
        acquisition, parser_payload=bridge,
        parsed_at=str(acquisition["document"]["acquired_at"]),
    )


def _prompt(parsed: Mapping[str, Any]) -> str:
    """Class-general extraction prompt. Gold values and paper-specific hints never enter it."""
    blocks = []
    budget = 70_000
    keywords = re.compile(
        r"solubil|dissolv|flory|huggins|hansen|partition|leach|extract|glass transition|\btg\b|claim|composition|wt\s*%|temperature",
        re.I,
    )
    ordered = sorted(
        parsed.get("blocks") or [],
        key=lambda row: (0 if keywords.search(str(row.get("text") or "")) else 1, int(row.get("reading_order") or 0)),
    )
    used = 0
    for block in ordered:
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        line = json.dumps({
            "block_id": block.get("block_id"), "page": block.get("page"),
            "section_path": block.get("section_path") or [], "text": text,
        }, ensure_ascii=False)
        if used + len(line) > budget:
            continue
        blocks.append(line); used += len(line)
    tables = json.dumps(parsed.get("tables") or [], ensure_ascii=False)[:25_000]
    return f"""You are a scientific information extractor. Return JSON only.
Extract every explicit, document-supported candidate in these record classes:
{', '.join(_RECORD_CLASSES)}.

Never infer an unreported value. Preserve observed material labels, units, bases,
methods, conditions, patent legal scope, and exact evidence wording. A demonstrated
dissolved loading is not an equilibrium solubility. When explicitly reported polymer
and solvent masses were fully dissolved at a numeric temperature, emit their computed
solution mass percent as a SolubilityPoint with basis demonstrated_dissolved_loading;
retain both reported masses as conditions. This arithmetic transformation is allowed
only from those exact reported masses. Every temperature and quantity field must be a
numeric scalar/range/function with its reported unit; never write placeholders such as
"dissolution temperature". Return no candidate when the
document lacks a class-supported fact. Every candidate must use this compact shape:
{{"record_class":"...","fields":{{...}},"method":{{"label":"...","category":"experimental|model|calculated|reported_claim|unknown"}},"conditions":[{{"property":"...","text":"number unit","basis":"..."}}],"evidence":{{"block_id":"...","table_id":null,"verbatim_span":"exact substring of that block"}},"confidence":0.0}}

Class fields:
- SolubilityPoint: polymer, solvent, temperature_text, solubility_text, solubility_basis
- ChiParameter: component_a, component_b, chi_text, chi_basis
- HSPRecord: material, dD_text, dP_text, dH_text, optional r0_text
- PartitionRecord: contaminant, phase_a, phase_b, metric, partition_value_text, basis
- LeachingRecord: feed_material, contaminant, extraction_solvent, response_metric, response_text, basis
- TgRecord: material, tg_text, basis
- ProcessClaim: patent_document_id, claim_number, claim_scope, legal_effect, operation, inputs, claimed_outcome
- CompositionClaim: patent_document_id, claim_number, claim_scope, legal_effect, components; each component has material, role, optional amount_text and basis

Quantities must include their reported units. Use legal_effect claimed_scope only
for actual numbered claims; examples/abstracts are non_claim_example or
non_claim_abstract. Do not emit NoEvidenceRecord. Output exactly
{{"records":[...]}}.

DOCUMENT_ID: {parsed['document_id']}
BLOCKS:
{chr(10).join(blocks)}
TABLES:
{tables}
"""


def _message_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(item.get("text") or "") if isinstance(item, Mapping) else str(item) for item in content)
    return str(content or "")


def _json_object(text: str) -> dict[str, Any]:
    source = text.strip()
    source = re.sub(r"^```(?:json)?\s*|\s*```$", "", source, flags=re.I)
    try:
        value = json.loads(source)
    except json.JSONDecodeError:
        start, end = source.find("{"), source.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("extractor response contains no JSON object")
        value = json.loads(source[start:end + 1])
    if not isinstance(value, Mapping) or not isinstance(value.get("records"), list):
        raise ValueError("extractor response must be an object with a records array")
    return dict(value)


def _usage(response: Any) -> dict[str, Any]:
    metadata = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("usage") or {}
    input_tokens = int(metadata.get("input_tokens") or metadata.get("prompt_tokens") or 0)
    output_tokens = int(metadata.get("output_tokens") or metadata.get("completion_tokens") or 0)
    cached = int(metadata.get("input_token_details", {}).get("cache_read") or metadata.get("cached_tokens") or 0)
    # Meta pricing used by the existing v11 warehouse accounting; cached input is discounted 75%.
    cost = ((input_tokens - cached) * 0.15 + cached * 0.15 * 0.25 + output_tokens * 0.60) / 1_000_000
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "cached_tokens": cached, "total_tokens": input_tokens + output_tokens,
            "estimated_cost_usd": round(cost, 6)}


def _default_extractor() -> Any:
    if not os.getenv("META_MUSE_API_KEY"):
        raise RuntimeError("META_MUSE_API_KEY is required for typed literature extraction")
    from langchain.chat_models import init_chat_model
    return init_chat_model(
        _MODEL_ID, api_key=os.environ["META_MUSE_API_KEY"], base_url=_BASE_URL,
        max_tokens=8_192, max_retries=0, reasoning_effort="medium",
        extra_body={"prompt_cache_key": "dissolve-v11-literature"},
    )


def _extract(model: Any, parsed: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prompt = _prompt(parsed)
    attempts, usage = [], Counter()
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(2):
        started = time.monotonic()
        response = model.invoke(messages)
        stats = _usage(response)
        usage.update({key: value for key, value in stats.items() if key != "estimated_cost_usd"})
        text = _message_text(response)
        attempts.append({"attempt": attempt + 1, "latency_s": round(time.monotonic() - started, 4), **stats})
        try:
            payload = _json_object(text)
            return [dict(row) for row in payload["records"] if isinstance(row, Mapping)], {
                "attempts": attempts, "usage": {**dict(usage), "estimated_cost_usd": round(sum(row["estimated_cost_usd"] for row in attempts), 6)},
                "prompt_sha256": _sha(prompt), "repair_used": attempt == 1,
            }
        except (ValueError, json.JSONDecodeError) as error:
            if attempt:
                raise
            messages.extend([
                {"role": "assistant", "content": text[:20_000]},
                {"role": "user", "content": f"Repair only the JSON contract error: {error}. Return exactly one object with a records array; do not add or change scientific claims."},
            ])
    raise AssertionError("bounded extraction loop exhausted")


def _material(label: Any, role: str) -> dict[str, Any]:
    from . import thermodynamics
    observed = str(label or "").strip()
    canonical = None
    if role == "polymer":
        canonical = thermodynamics.resolve_polymer_identity(observed)
    elif role == "solvent":
        resolved = thermodynamics.resolve_solvent(observed)
        labels = thermodynamics.solvent_identity_labels(resolved or observed)
        if resolved and thermodynamics.polymer_label_key(observed) in {
            thermodynamics.polymer_label_key(item) for item in labels
        }:
            canonical = resolved
    elif role in {"material", "component"}:
        # The single identity authority is reused rather than inventing a
        # literature-only alias table. Generic roles may resolve to either
        # fitted polymer or solvent identities when that resolution is unique.
        canonical = thermodynamics.resolve_polymer_identity(observed)
        if canonical is None:
            resolved = thermodynamics.resolve_solvent(observed)
            labels = thermodynamics.solvent_identity_labels(resolved or observed)
            if resolved and thermodynamics.polymer_label_key(observed) in {
                thermodynamics.polymer_label_key(item) for item in labels
            }:
                canonical = resolved
    elif role == "phase":
        resolved = thermodynamics.resolve_solvent(observed)
        labels = thermodynamics.solvent_identity_labels(resolved or observed)
        if resolved and thermodynamics.polymer_label_key(observed) in {
            thermodynamics.polymer_label_key(item) for item in labels
        }:
            canonical = resolved
    if role in {"polymer", "material", "component"} and canonical:
        identity = thermodynamics.POLYMER_IDENTITIES.get(canonical)
        members = list(identity.get("thermodynamic_members") or []) if identity else []
        if len(members) > 1:
            return {"observed_label": observed, "role": role, "identity_status": "ambiguous", "canonical_id": None, "candidate_ids": members}
    if canonical:
        return {"observed_label": observed, "role": role, "identity_status": "canonical", "canonical_id": canonical, "candidate_ids": []}
    return {"observed_label": observed or "unreported", "role": role, "identity_status": "pending", "canonical_id": None, "candidate_ids": []}


def _quantity(text: Any, prop: str, basis: str, canonical: str | None = None) -> dict[str, Any]:
    from .research import parse_quantity
    source = re.sub(r"(?<=\d)[*†‡]+(?=\s|$)", "", str(text or "").strip())
    return parse_quantity(source, property_name=prop, basis=str(basis or "reported"), unit_canonical=canonical)


def _evidence(candidate: Mapping[str, Any], parsed: Mapping[str, Any], record_id: str, confidence: float) -> list[dict[str, Any]]:
    raw = dict(candidate.get("evidence") or {})
    block_id = raw.get("block_id")
    block = next((row for row in parsed.get("blocks") or [] if row.get("block_id") == block_id), None)
    span = str(raw.get("verbatim_span") or "").strip()
    if block is None and span:
        block = next((row for row in parsed.get("blocks") or [] if span in str(row.get("text") or "")), None)
        block_id = block.get("block_id") if block else block_id
    if block is not None and span not in str(block.get("text") or ""):
        normalized_span = " ".join(span.split())
        normalized_block = " ".join(str(block.get("text") or "").split())
        if normalized_span and normalized_span in normalized_block:
            # The normalized parser block is the smallest exact durable span
            # available after whitespace-only model normalization.
            span = str(block.get("text") or "")
    if block is None or not span or span not in str(block.get("text") or ""):
        raise ValueError("candidate evidence span does not resolve to its parsed block")
    locator = {
        "document_id": parsed["document_id"], "block_id": block_id,
        "passage_id": f"PASS-{block_id}", "page": block.get("page"),
        "section_path": block.get("section_path") or [], "source_scope": "table" if raw.get("table_id") else "full_text",
        "table_id": raw.get("table_id"), "cell_range": None, "claim_number": None,
    }
    evidence_id = f"EVID-{_sha({'record': record_id, 'locator': locator, 'span': span})[:24].upper()}"
    return [{
        "evidence_id": evidence_id, "document_id": parsed["document_id"], "locator": locator,
        "verbatim_span": span, "source_sha256": parsed["source_sha256"],
        "extraction_confidence": confidence, "extraction_method": _PROMPT_VERSION,
    }]


def _anchor(record_class: str, record: Mapping[str, Any]) -> dict[str, Any]:
    kind = _ANCHORS[record_class]
    status, verdict, ids, comparison, reason = "anchor_deferred", None, [], None, None
    if kind == "none":
        status, reason = "no_anchor_applicable", "No DISSOLVE engine is configured for this record class."
    elif record_class == "SolubilityPoint":
        polymer, solvent = record["polymer"], record["solvent"]
        temperature, value = record["temperature"], record["solubility"]
        if polymer["identity_status"] == solvent["identity_status"] == "canonical" and temperature["shape"] == value["shape"] == "scalar":
            from .thermodynamics import get_solubility
            engine = get_solubility(polymer["canonical_id"], solvent["canonical_id"], float(temperature["value"]))
            if engine is not None:
                literature = float(value["value"])
                tolerance = max(1.0, abs(literature) * 0.15)
                status = "cross_referenced"
                verdict = "agrees" if abs(engine - literature) <= tolerance else "conflicts"
                ids = [f"thermo:{polymer['canonical_id']}:{solvent['canonical_id']}:{temperature['value']}C"]
                comparison = {"literature_value": literature, "engine_value": engine, "unit": value["unit_canonical"], "tolerance_basis": "15pct_or_1wt"}
            else:
                reason = "The configured thermodynamic engine has no fitted value for this exact pair and temperature."
        else:
            reason = "Cross-reference requires canonical polymer/solvent identities and scalar values."
    else:
        reason = f"The configured {kind} comparison is not yet implemented for autonomous ingestion."
    anchor_id = f"ANCHOR-{_sha({'record': record['record_id'], 'kind': kind})[:24].upper()}"
    return {"anchor_id": anchor_id, "anchor_kind": kind, "anchor_status": status,
            "verdict": verdict, "anchor_record_ids": ids, "comparison": comparison, "reason": reason}


def _normalize_candidate(candidate: Mapping[str, Any], parsed: Mapping[str, Any], library_id: str, index: int) -> dict[str, Any]:
    record_class = str(candidate.get("record_class") or "")
    if record_class not in _RECORD_CLASSES:
        raise ValueError(f"unsupported record_class: {record_class}")
    fields = dict(candidate.get("fields") or {})
    confidence = max(0.0, min(1.0, float(candidate.get("confidence", 0.5))))
    seed = {"document_id": parsed["document_id"], "class": record_class, "fields": fields,
            "index": index, "extractor": _PROMPT_VERSION}
    record_id = f"REC-{_sha(seed)[:24].upper()}"
    method = dict(candidate.get("method") or {})
    category = str(method.get("category") or "unknown")
    if category not in {"experimental", "model", "calculated", "reported_claim", "unknown"}:
        category = "unknown"
    conditions = []
    issues = []
    for condition in candidate.get("conditions") or []:
        try:
            conditions.append(_quantity(condition.get("text"), condition.get("property") or "condition", condition.get("basis") or "reported"))
        except Exception as error:
            issues.append({"code": "CONDITION_QUANTITY_REJECTED", "message": str(error), "field": str(condition.get("property") or "condition"), "severity": "warning"})
    row: dict[str, Any] = {
        "library_id": library_id, "record_id": record_id, "record_class": record_class,
        "status": "validated", "method": {"method_id": f"METHOD-{_sha(method)[:16].upper()}", "label": str(method.get("label") or "reported method"), "category": category},
        "conditions": {"condition_id": f"COND-{record_id[4:]}", "quantities": conditions, "notes": []},
        "evidence": _evidence(candidate, parsed, record_id, confidence),
        "extraction_confidence": confidence, "extractor": _PROMPT_VERSION,
        "validation_issues": issues, "cross_references": [],
    }
    if record_class == "SolubilityPoint":
        row.update(polymer=_material(fields.get("polymer"), "polymer"), solvent=_material(fields.get("solvent"), "solvent"),
                   temperature=_quantity(fields.get("temperature_text"), "temperature", "measurement_temperature"),
                   solubility=_quantity(fields.get("solubility_text"), "solubility", fields.get("solubility_basis") or "reported_solution_basis"))
    elif record_class == "ChiParameter":
        row.update(component_a=_material(fields.get("component_a"), "component"), component_b=_material(fields.get("component_b"), "component"),
                   chi=_quantity(fields.get("chi_text"), "flory_huggins_chi", fields.get("chi_basis") or "reported", "dimensionless"))
    elif record_class == "HSPRecord":
        row.update(material=_material(fields.get("material"), "material"),
                   dD=_quantity(fields.get("dD_text"), "hsp_dispersion", "reported"), dP=_quantity(fields.get("dP_text"), "hsp_polar", "reported"),
                   dH=_quantity(fields.get("dH_text"), "hsp_hydrogen_bonding", "reported"),
                   r0=_quantity(fields.get("r0_text"), "hsp_radius", "reported") if fields.get("r0_text") else None)
    elif record_class == "PartitionRecord":
        row.update(contaminant=_material(fields.get("contaminant"), "contaminant"), phase_a=_material(fields.get("phase_a"), "phase"),
                   phase_b=_material(fields.get("phase_b"), "phase"), metric=str(fields.get("metric") or "reported_partition_metric"),
                   partition_value=_quantity(fields.get("partition_value_text"), "partition", fields.get("basis") or "reported", "dimensionless"))
    elif record_class == "LeachingRecord":
        row.update(feed_material=_material(fields.get("feed_material"), "material"), contaminant=_material(fields.get("contaminant"), "contaminant"),
                   extraction_solvent=_material(fields.get("extraction_solvent"), "solvent"), response_metric=str(fields.get("response_metric") or "reported_response"),
                   response=_quantity(fields.get("response_text"), "leaching_response", fields.get("basis") or "reported"))
    elif record_class == "TgRecord":
        row.update(material=_material(fields.get("material"), "material"), tg=_quantity(fields.get("tg_text"), "glass_transition_temperature", fields.get("basis") or "reported"))
    elif record_class == "ProcessClaim":
        row.update(patent_document_id=str(fields.get("patent_document_id") or parsed["document_id"]), claim_number=str(fields.get("claim_number") or "unreported"),
                   claim_scope=str(fields.get("claim_scope") or "abstract"), legal_effect=str(fields.get("legal_effect") or "non_claim_abstract"),
                   operation=str(fields.get("operation") or "unreported"), inputs=[_material(item, "material") for item in fields.get("inputs") or []],
                   claimed_outcome=str(fields.get("claimed_outcome") or "unreported"))
    else:
        components = []
        for item in fields.get("components") or []:
            components.append({"material": _material(item.get("material"), "material"), "role": str(item.get("role") or "component"),
                               "amount": _quantity(item.get("amount_text"), "composition_amount", item.get("basis") or "reported") if item.get("amount_text") else None})
        row.update(patent_document_id=str(fields.get("patent_document_id") or parsed["document_id"]), claim_number=str(fields.get("claim_number") or "unreported"),
                   claim_scope=str(fields.get("claim_scope") or "abstract"), legal_effect=str(fields.get("legal_effect") or "non_claim_abstract"), components=components)
    def refs(value: Any) -> list[Mapping[str, Any]]:
        if isinstance(value, Mapping):
            return ([value] if "identity_status" in value else []) + [
                ref for item in value.values() for ref in refs(item)
            ]
        if isinstance(value, list):
            return [ref for item in value for ref in refs(item)]
        return []

    entity_refs = refs(row)
    if any(ref["identity_status"] != "canonical" for ref in entity_refs):
        row["status"] = "pending_identity"
        row["validation_issues"].append({
            "code": "UNRESOLVED_RECORD_IDENTITY",
            "message": "One or more observed material labels require identity review.",
            "field": "identity", "severity": "error",
        })
    row["cross_references"] = [_anchor(record_class, row)]
    return row


def _retained_rejection(candidate: Mapping[str, Any], parsed: Mapping[str, Any], library_id: str, index: int, error: Exception) -> dict[str, Any] | None:
    """Best-effort full record retention; candidates without resolvable evidence stay in telemetry."""
    try:
        row = _normalize_candidate(candidate, parsed, library_id, index)
    except Exception:
        return None
    row["status"] = "unvalidated_extraction"
    row["validation_issues"].append({"code": "EXTRACTION_CANDIDATE_REJECTED", "message": str(error), "field": "record", "severity": "error"})
    return row


def _graph(rows: Sequence[Mapping[str, Any]], acquisitions: Sequence[Mapping[str, Any]], library_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes, edges = [], []
    document_nodes = {}
    for acquisition in acquisitions:
        doc = acquisition["document"]
        node_id = f"NODE-{doc['document_id'][4:]}"
        payload = {"document_id": doc["document_id"]}
        node = {"node_id": node_id, "node_type": "Patent" if doc["document_kind"] == "patent" else "Paper",
                "canonical_key": f"source_sha256:{doc['content_sha256']}", "label": doc["title"], "payload": payload, "payload_sha256": _sha(payload)}
        nodes.append(node); document_nodes[doc["document_id"]] = node_id
    for row in rows:
        node_id = f"NODE-{row['record_id'][4:]}"
        payload = {"record_id": row["record_id"]}
        nodes.append({"node_id": node_id, "node_type": "ParameterRecord", "canonical_key": f"record:{row['record_id']}",
                      "label": f"{row['record_class']} {row['record_id']}", "payload": payload, "payload_sha256": _sha(payload)})
        evidence = row["evidence"][0]
        attributes = {"record_status": row["status"]}
        edges.append({"edge_id": f"EDGE-{_sha({'doc': evidence['document_id'], 'record': row['record_id']})[:24].upper()}", "edge_type": "REPORTS",
                      "source_node_id": document_nodes[evidence["document_id"]], "target_node_id": node_id,
                      "record_ids": [row["record_id"]], "evidence_ids": [evidence["evidence_id"]],
                      "attributes": attributes, "payload_sha256": _sha(attributes)})
    return nodes, edges


def ingest_literature_graph_data(
    paths: Sequence[str | Path], *, library_id: str = "owner-main", root: str | Path | None = None,
    extractor: Any | None = None,
) -> dict[str, Any]:
    """Run acquire→parse→Muse extraction→validation→anchors→incremental merge."""
    from . import research
    library_id = _slug(library_id)
    if not paths:
        raise ValueError("paths must contain at least one local document")
    acquisitions = [_local_acquisition(Path(path), library_id) for path in paths]
    parsed_documents = [_parse(row) for row in acquisitions]
    model = extractor or _default_extractor()
    records, document_reports, rejection_codes = [], [], Counter()
    total_usage = Counter()
    prompt_hashes = []
    for parsed in parsed_documents:
        started = time.monotonic()
        candidates, extraction = _extract(model, parsed)
        prompt_hashes.append(extraction["prompt_sha256"])
        for key, value in extraction["usage"].items():
            total_usage[key] += value
        accepted, retained, dropped = [], [], []
        for index, candidate in enumerate(candidates):
            try:
                row = _normalize_candidate(candidate, parsed, library_id, index)
                validation = research.validate_literature_record(row, parsed_documents=[parsed], library_id=library_id)
                if row["status"] == "validated" and not validation["claim_eligible"]:
                    row["status"] = "unvalidated_extraction"
                    row["validation_issues"].extend(validation["violations"])
                records.append(row)
                (accepted if validation["claim_eligible"] else retained).append(row["record_id"])
            except Exception as error:
                code = error.code if isinstance(error, research.LiteratureContractError) else type(error).__name__
                rejection_codes[code] += 1
                fallback = _retained_rejection(candidate, parsed, library_id, index, error)
                if fallback is not None:
                    records.append(fallback); retained.append(fallback["record_id"])
                else:
                    dropped.append({
                        "candidate_index": index, "record_class": candidate.get("record_class"),
                        "code": code, "message": str(error), "candidate_sha256": _sha(candidate),
                        "candidate": candidate,
                    })
        document_reports.append({
            "document_id": parsed["document_id"], "source_sha256": parsed["source_sha256"],
            "parse": {
                "backend": parsed["parser_backend"], "version": parsed["parser_version"],
                "fallback_reason": parsed["fallback_reason"],
                "quality_flags": list(parsed["quality_flags"]),
                "block_count": len(parsed["blocks"]), "table_count": len(parsed["tables"]),
            },
            "candidate_count": len(candidates), "validated_record_ids": accepted,
            "retained_unvalidated_record_ids": retained,
            "unrepresentable_candidates": dropped, "extractor": extraction,
            "latency_s": round(time.monotonic() - started, 4),
        })
    validation = research.validate_extraction_batch(records, parsed_documents=parsed_documents, library_id=library_id)
    prompt_sha = _sha(sorted(prompt_hashes))
    manifest = {
        "schema": "dissolve.extractor-manifest.v1", "extractor_id": f"muse-{prompt_sha[:20]}",
        "name": _PROMPT_VERSION, "version": "1.0.0", "configuration_sha256": prompt_sha,
        "model_name": _MODEL_LABEL, "model_sha256": None,
    }
    nodes, edges = _graph(records, acquisitions, library_id)
    batch = {
        "schema": "dissolve.graph-merge.v1", "library_id": library_id,
        "ingest_run_id": f"INGEST-{_sha({'sources': [row['source_sha256'] for row in parsed_documents], 'prompt': prompt_sha})[:24].upper()}",
        "acquisitions": acquisitions, "parsed_documents": parsed_documents,
        "extractor_manifests": [manifest], "records": records, "nodes": nodes, "edges": edges,
        "summary_topics_touched": sorted(set(row["record_class"] for row in records)),
    }
    batch["idempotency_key"] = research.derive_graph_idempotency_key(batch)
    merge = research.merge_literature_graph(batch, root=root)
    by_class = Counter(row["record_class"] for row in records)
    by_status = Counter(row["status"] for row in records)
    anchors = Counter(anchor["anchor_status"] for row in records for anchor in row["cross_references"])
    return {
        "schema": _SCHEMA, "status": "completed", "library_id": library_id,
        "input_paths": [str(Path(path).expanduser().resolve()) for path in paths],
        "documents": document_reports, "records": records, "validation": validation,
        "telemetry": {"record_classes": dict(sorted(by_class.items())), "record_statuses": dict(sorted(by_status.items())),
                      "rejection_codes": dict(sorted(rejection_codes.items())), "anchor_statuses": dict(sorted(anchors.items())),
                      "usage": dict(total_usage), "benchmark_status": "unbenchmarked_interim"},
        "extractor_manifest": manifest, "merge": merge,
    }


def ingest_literature_graph(
    paths: Sequence[str], library_id: str = "owner-main",
) -> str:
    """Ingest local papers/patents into the typed durable graph with Muse extraction."""
    try:
        result = ingest_literature_graph_data(paths, library_id=library_id)
    except Exception as error:
        code = getattr(error, "code", type(error).__name__)
        return tool_error("ingest_literature_graph", str(error), error_code=str(code), library_id=library_id)
    return tool_success(
        "ingest_literature_graph", display=(
            f"Ingested {len(result['documents'])} document(s); "
            f"{result['validation']['claim_eligible_count']} validated and "
            f"{result['validation']['retained_unvalidated_count']} retained unvalidated record(s)."
        ), **result,
    )


def compare_literature_engines(
    paths: Sequence[str | Path], *, library_id: str, knowledgebase: str,
    probe_queries: Sequence[str], root: str | Path | None = None, extractor: Any | None = None,
) -> dict[str, Any]:
    """Run identical sources through legacy passage RAG and the typed graph path."""
    from . import research
    started = time.monotonic()
    prior_root = os.environ.get("DISSOLVE_RESEARCH_HOME")
    if root is not None:
        os.environ["DISSOLVE_RESEARCH_HOME"] = str(Path(root).expanduser().resolve() / "legacy")
    try:
        legacy_payload = parse_tool_result(research.ingest_literature_documents(
            [str(Path(path).expanduser().resolve()) for path in paths], knowledgebase=knowledgebase,
        ))
        legacy_ingest_s = time.monotonic() - started
        probes = []
        for query in probe_queries:
            result = parse_tool_result(research.search_literature_corpus(query, knowledgebase=knowledgebase, top_k=5))
            probes.append({"query": query, "result": result["data"]})
    finally:
        if prior_root is None:
            os.environ.pop("DISSOLVE_RESEARCH_HOME", None)
        else:
            os.environ["DISSOLVE_RESEARCH_HOME"] = prior_root
    graph_started = time.monotonic()
    graph = ingest_literature_graph_data(paths, library_id=library_id, root=root, extractor=extractor)
    return {
        "schema": "dissolve.literature-engine-comparison.v1", "library_id": library_id,
        "sources": [str(Path(path).expanduser().resolve()) for path in paths],
        "legacy": {
            "ingest": legacy_payload["data"], "probe_results": probes,
            "latency_s": round(legacy_ingest_s, 4), "estimated_cost_usd": 0.0,
        },
        "graph": {"documents": graph["documents"], "records": graph["records"], "validation": graph["validation"], "telemetry": graph["telemetry"],
                  "merge": graph["merge"], "latency_s": round(time.monotonic() - graph_started, 4)},
    }


def build_benchmark_predictions(
    graph_result: Mapping[str, Any], gold: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one graph run into the existing P3 scorer container without changing records."""
    scored_classes = set((gold.get("matching_policies") or {}).keys())
    source_to_gold: dict[str, str] = {}
    gold_documents = []
    for item in gold.get("documents") or []:
        document = dict(item.get("document") or {})
        document_id = str(document.get("document_id") or "")
        gold_documents.append(document_id)
        for artifact in item.get("source_artifacts") or []:
            if artifact.get("sha256"):
                source_to_gold[str(artifact["sha256"])] = document_id
    annotations: dict[str, list[dict[str, Any]]] = {document_id: [] for document_id in gold_documents}
    for index, record in enumerate(graph_result.get("records") or []):
        if record.get("record_class") not in scored_classes:
            continue
        evidence = list(record.get("evidence") or [])
        source_hash = str(evidence[0].get("source_sha256") or "") if evidence else ""
        document_id = source_to_gold.get(source_hash)
        if document_id is None:
            continue
        status = record.get("status")
        disposition = (
            "positive" if status == "validated"
            else "pending_identity" if status == "pending_identity"
            else "rejected_candidate"
        )
        annotations[document_id].append({
            "prediction_id": f"PRED-{_sha({'record': record.get('record_id'), 'index': index})[:24].upper()}",
            "disposition": disposition, "record": dict(record),
        })
    return {
        "schema": "dissolve.literature-benchmark-predictions.v1",
        "gold_set_id": gold.get("gold_set_id"),
        "documents": [{
            "document_id": document_id, "recovered_landmarks": [],
            "annotations": annotations[document_id],
        } for document_id in gold_documents],
    }
