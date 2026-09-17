"""RTI-1 owner promote of one E2E-5 ingested paper into a sidecar searchable I.

ADMIT `4cfa76c7…`. Ingest stays off the agent. No URL fetch. No MiniLM rewrite
of the product 922. Floor is re-derived on the union, not typed by hand.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import abstention_a2a4, abstention_a3, dense_d2, engine_e2e, engine_e2e5, research, t5_corpus_graph, text_chunk_metrics
from .gold_ensemble import file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

SPEC_SHA256 = "4cfa76c769ad3b8fae9919e900c6fa6237714c5f7ab0557cec093b20aa2fd346"
SIDECAR_STORE_NAME = "CHUNKS.t5.promoted.unsealed.v1.json"
SIDECAR_GZIP_NAME = "t5-promoted-unsealed.json.gz"
SIDECAR_INDEX_NAME = "INDEX.t5.promoted.v1.json"
SIDECAR_KNOWLEDGEBASE = "t5-promoted-unsealed"
PROMOTE_CURVES_NAME = "CURVES.retrieval.abstention.promote.v1.json"
PRODUCT_INDEX_NAME = "INDEX.t5.unsealed.v1.json"
INDEXED_STATUS = "indexed"
_FORBIDDEN_EMIT_KEYS = frozenset({
    "needles", "query", "evidence_quote", "canonical_text", "fact_id",
})
_PROTECTED_NAMES = frozenset({
    "CENSUS.v3.json",
    "GOLD.text.v1.unsealed.json",
    "GOLD.v2.json",
    "CHUNKS.t5.indexed.unsealed.v1.json",
    "t5-indexed-unsealed.json.gz",
    "CURVES.retrieval.abstention.v1.json",
    "CURVES.retrieval.weights.v1.json",
    "CURVES.retrieval.v3.json",
    "CURVES.retrieval.v4.json",
    "CURVES.retrieval.d3.json",
    "OFFDOMAIN.queries.v1.json",
})
_SUPPORTED_MODELS = frozenset({engine_e2e.MINILM_ID, research._BGE_MODEL_ID})


def _refuse_gold_v2() -> None:
    if text_chunk_metrics.GOLD_V2_PATH.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")
    extra = Path("/home/aaltamimi2/dissolve-v12-audit/corpus/GOLD.v2.json")
    if extra.exists():
        raise TextGoldError("gold_v2_present", "Sealing GOLD.v2.json is an owner stop.")


def _refuse_rti_dest(dest: Path) -> None:
    dest = Path(dest).expanduser().resolve()
    if dest.name in _PROTECTED_NAMES:
        raise TextGoldError("protected_persist", "RTI-1 must not overwrite a pinned persist name.")
    banned = {
        engine_e2e.CENSUS_PATH.resolve(),
        engine_e2e.STORE_PATH.resolve(),
        text_chunk_metrics.GOLD_UNSEALED_PATH.resolve(),
        text_chunk_metrics.GOLD_V2_PATH.resolve(),
        dense_d2.INDEX_GZIP_PATH.resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json").resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json").resolve(),
        text_chunk_metrics.CURVES_V3_PATH.resolve(),
        dense_d2.CURVES_V4_PATH.resolve(),
        Path("/home/aaltamimi2/dissolve-v12-audit/corpus/CENSUS.v3.json").resolve(),
    }
    if dest in banned:
        raise TextGoldError("protected_persist", "RTI-1 must not overwrite published persist.")


def _refuse_url(paper_sha256: str) -> None:
    folded = str(paper_sha256 or "").strip().casefold()
    if folded.startswith(("http://", "https://", "ftp://")):
        raise TextGoldError("url_fetch_refused", "Promote does not fetch URLs.")


def _wrap_contract(error: BaseException) -> None:
    if isinstance(error, research.LiteratureContractError):
        raise TextGoldError(error.code, str(error)) from error
    raise error


def _write_json(dest: Path, payload: Mapping[str, Any]) -> str:
    dest = Path(dest)
    _refuse_rti_dest(dest)
    if text_chunk_metrics._contains_forbidden_keys(payload, set(_FORBIDDEN_EMIT_KEYS)):
        raise TextGoldError("gold_quoted", "RTI-1 dest must not carry gold identifiers.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return file_sha256(dest)


def _write_gzip(dest: Path, index: Mapping[str, Any]) -> str:
    dest = Path(dest)
    _refuse_rti_dest(dest)
    if dest.name == "t5-indexed-unsealed.json.gz":
        raise TextGoldError("protected_persist", "RTI-1 must not rewrite the product gzip.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    temporary = dest.with_name(dest.name + ".tmp")
    with temporary.open("wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as compressed:
            compressed.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(dest)
    return file_sha256(dest)


def _empty_sidecar_store() -> dict[str, Any]:
    return {
        "schema": engine_e2e.STORE_SCHEMA,
        "n_chunks": 0,
        "indexed_paper_sha256": [],
        "ingested_paper_sha256": [],
        "chunks": [],
    }


def _select_promote_model(
    model_name: str | None,
    expected_dim: int | None,
) -> tuple[str, int]:
    selected = engine_e2e.MINILM_ID if model_name is None else str(model_name)
    if selected not in _SUPPORTED_MODELS:
        raise TextGoldError("dense_model", "Promote model is not supported.")
    compatible = research._compatible_embedding_dim(selected)
    if expected_dim is not None and expected_dim != compatible:
        raise TextGoldError("dense_dim", "Embedding dim conflicts with the selected model.")
    return selected, compatible


def _canonical_corpus_dir() -> Path:
    return engine_e2e.MANIFEST_PATH.expanduser().resolve().parent


def _refuse_nonlegacy_dest(dest: Path) -> None:
    dest = Path(dest).expanduser().resolve()
    if dest == _canonical_corpus_dir():
        raise TextGoldError(
            "protected_persist",
            "Nonlegacy promote dest must not be the canonical corpus directory.",
        )
    dest_index = (dest / PRODUCT_INDEX_NAME).expanduser().resolve()
    dest_gzip = (dest / "indexes" / SIDECAR_GZIP_NAME).expanduser().resolve()
    if dest_index == engine_e2e.MANIFEST_PATH.expanduser().resolve():
        raise TextGoldError("protected_persist", "Nonlegacy promote must not overwrite the MiniLM manifest.")
    if dest_gzip == dense_d2.INDEX_GZIP_PATH.expanduser().resolve():
        raise TextGoldError("protected_persist", "Nonlegacy promote must not overwrite the MiniLM index.")
    if dest_gzip == research._canonical_product_index_path().expanduser().resolve():
        raise TextGoldError("protected_persist", "Nonlegacy promote must not overwrite the MiniLM index.")


def _parse_gzip_index(raw: bytes) -> dict[str, Any]:
    try:
        payload = research._read_gzip_json_bytes(raw)
    except json.JSONDecodeError as error:
        raise TextGoldError("bge10_index_malformed", "Product index is malformed.") from error
    except gzip.BadGzipFile as error:
        raise TextGoldError("bge10_index_malformed", "Product index is malformed.") from error
    except zlib.error as error:
        raise TextGoldError("bge10_index_malformed", "Product index is malformed.") from error
    except OSError as error:
        raise TextGoldError("bge10_index_unreadable", "Product index is unreadable.") from error
    except (UnicodeDecodeError, EOFError) as error:
        raise TextGoldError("bge10_index_unreadable", "Product index is unreadable.") from error
    if not isinstance(payload, dict):
        raise TextGoldError("bge10_index_invalid", "Product index is not an object.")
    return payload


def _require_dense_metadata(
    block: Mapping[str, Any],
    *,
    selected: str,
    expected_dim: int,
) -> None:
    model = block.get("model")
    if str(model or "") != selected:
        raise TextGoldError("dense_model", "Dense block model is incompatible.")
    try:
        recorded_dim = research._finite_int(
            block.get("dim"),
            "dense_dim",
            "Dense block dim is incompatible.",
        )
    except research.LiteratureContractError as error:
        raise TextGoldError("dense_dim", "Dense block dim is incompatible.") from error
    if recorded_dim != expected_dim:
        raise TextGoldError("dense_dim", "Dense block dim is incompatible.")
    if selected == research._BGE_MODEL_ID:
        try:
            research._require_bge_recipe_fields(block, "bge10_recipe_mismatch")
        except research.LiteratureContractError as error:
            _wrap_contract(error)


def _pending_vectors(
    store: Mapping[str, Any],
    *,
    selected: str,
    expected_dim: int,
) -> dict[str, list[float]]:
    pending = store.get("pending_dense") or {}
    if pending in (None, {}):
        return {}
    if not isinstance(pending, Mapping):
        raise TextGoldError("dense_dim", "Pending dense block is invalid.")
    ids_raw = pending.get("chunk_ids")
    vectors_raw = pending.get("vectors")
    if not ids_raw and not vectors_raw:
        return {}
    if not isinstance(ids_raw, list) or not isinstance(vectors_raw, list):
        raise TextGoldError("n_chunks_mismatch", "Pending dense IDs and vectors are not aligned.")
    ids = [str(item) for item in ids_raw]
    if any(not item for item in ids):
        raise TextGoldError("bge10_chunk_membership", "Pending dense IDs are invalid.")
    if len(ids) != len(set(ids)):
        raise TextGoldError("bge10_chunk_membership", "Pending dense IDs are not unique.")
    if len(ids) != len(vectors_raw):
        raise TextGoldError("n_chunks_mismatch", "Pending dense IDs and vectors are not aligned.")
    store_ids: list[str] = []
    seen: set[str] = set()
    for row in store.get("chunks") or []:
        chunk_id = str(row.get("chunk_id") or "")
        if not chunk_id:
            raise TextGoldError("bge10_chunk_membership", "Incremental store membership is invalid.")
        if chunk_id in seen:
            raise TextGoldError("bge10_chunk_membership", "Incremental store membership is invalid.")
        store_ids.append(chunk_id)
        seen.add(chunk_id)
    for chunk_id in ids:
        if chunk_id not in seen:
            raise TextGoldError("bge10_chunk_membership", "Pending dense ID is not in the incremental store.")
    _require_dense_metadata(pending, selected=selected, expected_dim=expected_dim)
    try:
        rows = research._validate_dense_rows(
            ids,
            list(vectors_raw),
            dim=expected_dim,
            require_bge_geometry=selected == research._BGE_MODEL_ID,
        )
        research._assert_generated_dense_vectors(
            rows,
            expected_count=len(ids),
            model_name=selected,
            expected_dim=expected_dim,
        )
    except research.LiteratureContractError as error:
        raise TextGoldError("dense_dim", "Pending dense vectors are invalid.") from error
    return {chunk_id: list(vector) for chunk_id, vector in zip(ids, rows)}


def _previous_sidecar_vectors(
    previous: Mapping[str, Any],
    sidecar_chunks: Sequence[Mapping[str, Any]],
    *,
    selected: str,
    expected_dim: int,
) -> dict[str, list[float]]:
    try:
        validated = research._validated_dense_side(previous, sidecar_chunks)
    except research.LiteratureContractError as error:
        _wrap_contract(error)
    _require_dense_metadata(validated["block"], selected=selected, expected_dim=expected_dim)
    if validated["dim"] != expected_dim:
        raise TextGoldError("dense_dim", "Previous sidecar dim is incompatible.")
    if str(validated["model"] or "") != selected:
        raise TextGoldError("dense_model", "Previous sidecar model is incompatible.")
    return {chunk_id: list(vector) for chunk_id, vector in validated["by_id"].items()}


def _load_explicit_product_pair(
    *,
    index_path: Path,
    manifest_path: Path,
    selected: str,
    expected_dim: int,
) -> tuple[dict[str, Any], dict[str, Any], str, Path, Path]:
    index_file = Path(index_path).expanduser().resolve()
    manifest_file = Path(manifest_path).expanduser().resolve()
    if not manifest_file.is_file():
        raise TextGoldError("bge_product_pair", "Product manifest is missing.")
    if not index_file.is_file():
        raise TextGoldError("bge_product_pair", "Product index is missing.")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise TextGoldError("bge10_manifest_unreadable", "Product manifest is unreadable.") from error
    except json.JSONDecodeError as error:
        raise TextGoldError("bge10_manifest_malformed", "Product manifest is malformed.") from error
    if not isinstance(manifest, dict):
        raise TextGoldError("bge10_manifest_invalid", "Product manifest is not an object.")
    try:
        raw = index_file.read_bytes()
    except OSError as error:
        raise TextGoldError("bge10_index_unreadable", "Product index is unreadable.") from error
    digest = hashlib.sha256(raw).hexdigest()
    try:
        declared = research._bge10_index_digest(manifest.get("gzip_sha256"))
    except research.LiteratureContractError as error:
        _wrap_contract(error)
    if digest != declared:
        raise TextGoldError("bge10_index_digest", "Product index digest does not match the manifest.")
    raw_path = manifest.get("index_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise TextGoldError("bge10_index_path_missing", "Product index path is missing.")
    resolved = research._resolve_against(manifest_file.parent, raw_path)
    if resolved != index_file:
        raise TextGoldError("bge10_index_path_missing", "Product manifest index_path does not match the supplied index.")
    index = _parse_gzip_index(raw)
    try:
        engine_e2e._require_product_identity(index)
    except TextGoldError:
        raise
    declared_kb = manifest.get("knowledgebase")
    try:
        if not isinstance(declared_kb, str) or research._slug(declared_kb) != research._PRODUCT_KNOWLEDGEBASE:
            raise TextGoldError("bge10_index_knowledgebase", "Product knowledgebase is mismatched.")
    except ValueError as error:
        raise TextGoldError("bge10_index_knowledgebase", "Product knowledgebase is mismatched.") from error
    if index.get("knowledgebase") != research._PRODUCT_KNOWLEDGEBASE:
        raise TextGoldError("bge10_index_knowledgebase", "Product knowledgebase is mismatched.")
    index_dense = index.get("dense")
    manifest_dense = manifest.get("dense")
    if not isinstance(index_dense, Mapping) or not isinstance(manifest_dense, Mapping):
        raise TextGoldError("bge10_recipe_mismatch", "Product dense recipe is incompatible.")
    _require_dense_metadata(index_dense, selected=selected, expected_dim=expected_dim)
    _require_dense_metadata(manifest_dense, selected=selected, expected_dim=expected_dim)
    if selected == research._BGE_MODEL_ID:
        try:
            index_recipe = research._require_bge_recipe_fields(index_dense, "bge10_recipe_mismatch")
            manifest_recipe = research._require_bge_recipe_fields(manifest_dense, "bge10_recipe_mismatch")
        except research.LiteratureContractError as error:
            _wrap_contract(error)
        if index_recipe != manifest_recipe:
            raise TextGoldError("bge10_recipe_mismatch", "Product manifest recipe does not match the index.")
    else:
        if str(index_dense.get("model") or "") == research._BGE_MODEL_ID:
            raise TextGoldError("dense_model", "MiniLM product must not carry BGE vectors.")
        if str(manifest_dense.get("model") or "") == research._BGE_MODEL_ID:
            raise TextGoldError("dense_model", "MiniLM product must not carry a BGE recipe.")
    try:
        research._validated_dense_side(index, index.get("chunks") or [])
    except research.LiteratureContractError as error:
        _wrap_contract(error)
    return json.loads(json.dumps(index)), json.loads(json.dumps(manifest)), digest, index_file, manifest_file


def _sidecar_manifest_dense(*, selected: str, dim: int, n_vectors: int) -> dict[str, Any]:
    block: dict[str, Any] = {
        "model": selected,
        "dim": dim,
        "n_vectors": n_vectors,
    }
    if selected == research._BGE_MODEL_ID:
        block.update(research._bge_generated_recipe_fields())
    return block


def _product_chunk_ids(product: Mapping[str, Any] | None, *, fallback_store: Mapping[str, Any]) -> set[str]:
    if product is not None:
        return {str(row.get("chunk_id") or "") for row in (product.get("chunks") or []) if row.get("chunk_id")}
    return {str(row.get("chunk_id") or "") for row in (fallback_store.get("chunks") or [])}


def _mark_incremental_indexed(census: Mapping[str, Any], paper_sha256: str) -> dict[str, Any]:
    working = json.loads(json.dumps(census))
    found = False
    previous = None
    for row in working.get("papers") or []:
        if str(row.get("sha256") or "") != paper_sha256:
            continue
        previous = str(row.get("status") or "")
        if previous == INDEXED_STATUS:
            return working
        if previous != engine_e2e5.INGESTED_STATUS:
            raise TextGoldError("census_status", "Promote expects an ingested incremental row.")
        row["status"] = INDEXED_STATUS
        found = True
        break
    if not found:
        raise TextGoldError("ingested_missing", "Paper SHA is not in the incremental census.")
    counts = dict(working.get("counts_by_status") or {})
    ingested = max(0, int(counts.get(engine_e2e5.INGESTED_STATUS) or 0) - 1)
    counts[engine_e2e5.INGESTED_STATUS] = ingested
    counts[INDEXED_STATUS] = int(counts.get(INDEXED_STATUS) or 0) + 1
    working["counts_by_status"] = counts
    del previous
    return working


def _ensure_dest_index(
    dest_dir: Path,
    *,
    product_manifest_path: Path | None = None,
) -> Path:
    dest_index = dest_dir / PRODUCT_INDEX_NAME
    source = (
        Path(product_manifest_path).expanduser().resolve()
        if product_manifest_path is not None
        else engine_e2e.MANIFEST_PATH.resolve()
    )
    if dest_index.resolve() != source and not dest_index.is_file():
        _refuse_rti_dest(dest_index)
        dest_index.parent.mkdir(parents=True, exist_ok=True)
        dest_index.write_bytes(source.read_bytes())
    return dest_index


def _update_product_index(
    dest_index: Path,
    *,
    promoted: Mapping[str, Any],
    floor: float,
) -> str:
    payload = json.loads(dest_index.read_text(encoding="utf-8"))
    before_keys = set(payload)
    indexed = list(payload.get("indexed_paper_sha256") or [])
    n_chunks = payload.get("n_chunks")
    payload["promoted"] = dict(promoted)
    block = dict(payload.get("abstention") or {})
    block["statistic"] = "query_idf_coverage"
    block["percentile"] = abstention_a2a4.SHIPPED_PERCENTILE
    block["floor"] = float(floor)
    block["calibrated_at"] = datetime.now(timezone.utc).isoformat()
    payload["abstention"] = block
    extra = set(payload) - before_keys
    if extra - {"promoted"}:
        raise TextGoldError("manifest_keys_moved", "INDEX.t5 may gain promoted only.")
    if list(payload.get("indexed_paper_sha256") or []) != indexed:
        raise TextGoldError("index_sha_mismatch", "Product indexed SHA list must not move.")
    if payload.get("n_chunks") != n_chunks:
        raise TextGoldError("n_chunks_mismatch", "Product INDEX n_chunks must not move.")
    return _write_json(dest_index, payload)


def _union_floor(
    *,
    sidecar_index: Mapping[str, Any],
    sidecar_store_sha256: str,
    sidecar_gzip_sha256: str,
    dest_curves: Path,
    product_index: Mapping[str, Any] | None = None,
    product_gzip_path: Path | str | None = None,
    product_gzip_sha256: str | None = None,
) -> dict[str, Any]:
    gold = json.loads(text_chunk_metrics.GOLD_UNSEALED_PATH.read_text(encoding="utf-8"))
    split = text_chunk_metrics.split_gold(gold)
    off_payload = json.loads(abstention_a3.OFFDOMAIN_PATH.read_text(encoding="utf-8"))
    fire = [str(fact.get("query") or "") for fact in split["must_fire"]]
    off = [str(row.get("query") or "") for row in (off_payload.get("queries") or [])]
    if product_index is None:
        product = dense_d2.load_gzip_index()
    else:
        product = product_index
    if product_gzip_sha256 is not None:
        gzip_pin = str(product_gzip_sha256)
    elif product_gzip_path is not None:
        gzip_pin = file_sha256(Path(product_gzip_path))
    else:
        gzip_pin = file_sha256(dense_d2.INDEX_GZIP_PATH)
    union = research._union_product_and_sidecar(product, sidecar_index)
    pins = {
        "gold_sha256": file_sha256(text_chunk_metrics.GOLD_UNSEALED_PATH),
        "census_sha256": file_sha256(engine_e2e.CENSUS_PATH),
        "store_sha256": file_sha256(engine_e2e.STORE_PATH),
        "gzip_sha256": gzip_pin,
        "offdomain_sha256": file_sha256(abstention_a3.OFFDOMAIN_PATH),
        "sidecar_store_sha256": sidecar_store_sha256,
        "sidecar_gzip_sha256": sidecar_gzip_sha256,
        "set_digest": str(off_payload.get("set_digest") or abstention_a3.SET_DIGEST),
    }
    artifact = abstention_a2a4.build_abstention_artifact(
        index=union, fire_queries=fire, offdomain_queries=off, pins=pins,
    )
    _write_json(dest_curves, artifact)
    return artifact


def _header_from_dests(
    *,
    status: str,
    chunks_added: int,
    vectors_embedded: int,
    dest_dir: Path,
    floor: float | None,
    census_status: str | None,
) -> dict[str, Any]:
    sidecar_store = dest_dir / SIDECAR_STORE_NAME
    sidecar_gzip = dest_dir / "indexes" / SIDECAR_GZIP_NAME
    sidecar_index = dest_dir / SIDECAR_INDEX_NAME
    product_index = dest_dir / PRODUCT_INDEX_NAME
    graph = dest_dir / t5_corpus_graph.GRAPH_NAME
    curves = dest_dir / PROMOTE_CURVES_NAME
    n_papers = 0
    n_chunks = 0
    if sidecar_store.is_file():
        store = json.loads(sidecar_store.read_text(encoding="utf-8"))
        n_chunks = len(store.get("chunks") or [])
        n_papers = len({str(row.get("paper_sha256") or "") for row in (store.get("chunks") or []) if row.get("paper_sha256")})
    payload = {
        "status": status,
        "chunks_added": int(chunks_added),
        "vectors_embedded": int(vectors_embedded),
        "n_sidecar_papers": n_papers,
        "n_sidecar_chunks": n_chunks,
        "statistic": "query_idf_coverage",
        "percentile": abstention_a2a4.SHIPPED_PERCENTILE,
        "floor": floor,
        "census_status": census_status,
        "sidecar_store_sha256": file_sha256(sidecar_store) if sidecar_store.is_file() else None,
        "sidecar_gzip_sha256": file_sha256(sidecar_gzip) if sidecar_gzip.is_file() else None,
        "sidecar_index_sha256": file_sha256(sidecar_index) if sidecar_index.is_file() else None,
        "index_sha256": file_sha256(product_index) if product_index.is_file() else None,
        "graph_sha256": file_sha256(graph) if graph.is_file() else None,
        "promote_curves_sha256": file_sha256(curves) if curves.is_file() else None,
    }
    if text_chunk_metrics._contains_forbidden_keys(payload, set(_FORBIDDEN_EMIT_KEYS)):
        raise TextGoldError("gold_quoted", "RTI-1 return must not carry gold identifiers.")
    return payload


def promote_ingested_paper(
    *,
    paper_sha256: str,
    dest_dir: str | Path | None = None,
    embedder=None,
    model_name: str | None = None,
    expected_dim: int | None = None,
    product_index_path: str | Path | None = None,
    product_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    _refuse_gold_v2()
    _refuse_url(paper_sha256)
    selected, compatible_dim = _select_promote_model(model_name, expected_dim)
    dest = Path(dest_dir).expanduser().resolve() if dest_dir is not None else DEFAULT_OUT_DIR.resolve()
    is_bge = selected == research._BGE_MODEL_ID
    index_supplied = product_index_path is not None
    manifest_supplied = product_manifest_path is not None
    if index_supplied != manifest_supplied:
        raise TextGoldError("bge_product_pair", "Product index and manifest must be supplied together.")
    if is_bge and not index_supplied:
        raise TextGoldError("bge_product_pair", "BGE promote requires an explicit product index and manifest.")
    if is_bge:
        if dest_dir is None:
            raise TextGoldError(
                "protected_persist",
                "BGE promote requires an explicit noncanonical destination.",
            )
        _refuse_nonlegacy_dest(dest)
    selected_product: dict[str, Any] | None = None
    selected_product_gzip_sha256: str | None = None
    selected_product_index_path: Path | None = None
    selected_product_manifest_path: Path | None = None
    if index_supplied:
        (
            selected_product,
            _selected_manifest,
            selected_product_gzip_sha256,
            selected_product_index_path,
            selected_product_manifest_path,
        ) = _load_explicit_product_pair(
            index_path=Path(product_index_path),
            manifest_path=Path(product_manifest_path),
            selected=selected,
            expected_dim=compatible_dim,
        )
        del _selected_manifest
    incremental_census_path = dest / engine_e2e5.INCREMENTAL_CENSUS_PATH.name
    incremental_store_path = dest / engine_e2e5.INCREMENTAL_STORE_PATH.name
    sidecar_store_path = dest / SIDECAR_STORE_NAME
    sidecar_gzip_path = dest / "indexes" / SIDECAR_GZIP_NAME
    sidecar_index_path = dest / SIDECAR_INDEX_NAME
    dest_curves = dest / PROMOTE_CURVES_NAME
    graph_path = dest / t5_corpus_graph.GRAPH_NAME
    if not incremental_census_path.is_file():
        return _header_from_dests(
            status="ingested_missing",
            chunks_added=0,
            vectors_embedded=0,
            dest_dir=dest,
            floor=None,
            census_status=None,
        )
    census = json.loads(incremental_census_path.read_text(encoding="utf-8"))
    status = engine_e2e5.census_status_for(census, paper_sha256)
    if status is None:
        return _header_from_dests(
            status="ingested_missing",
            chunks_added=0,
            vectors_embedded=0,
            dest_dir=dest,
            floor=None,
            census_status=None,
        )
    gold = json.loads(text_chunk_metrics.GOLD_UNSEALED_PATH.read_text(encoding="utf-8"))
    product_census = json.loads(engine_e2e.CENSUS_PATH.read_text(encoding="utf-8"))
    blocked = engine_e2e5.forbidden_shas(gold, product_census) | engine_e2e5.forbidden_shas(gold, census)
    if paper_sha256 in blocked:
        raise TextGoldError("held_out_in_store", "Refusing to promote a non-indexed paper.")
    sidecar = json.loads(sidecar_store_path.read_text(encoding="utf-8")) if sidecar_store_path.is_file() else _empty_sidecar_store()
    if paper_sha256 in engine_e2e5.store_paper_shas(sidecar):
        floor = None
        dest_index = dest / PRODUCT_INDEX_NAME
        if dest_index.is_file():
            block = json.loads(dest_index.read_text(encoding="utf-8")).get("abstention") or {}
            if isinstance(block, Mapping) and block.get("floor") is not None:
                floor = float(block["floor"])
        return _header_from_dests(
            status="noop",
            chunks_added=0,
            vectors_embedded=0,
            dest_dir=dest,
            floor=floor,
            census_status=engine_e2e5.census_status_for(census, paper_sha256),
        )
    if status != engine_e2e5.INGESTED_STATUS:
        return _header_from_dests(
            status="ingested_missing",
            chunks_added=0,
            vectors_embedded=0,
            dest_dir=dest,
            floor=None,
            census_status=status,
        )
    if not incremental_store_path.is_file():
        raise TextGoldError("ingested_chunks_missing", "Incremental store is missing for this SHA.")
    incremental = json.loads(incremental_store_path.read_text(encoding="utf-8"))
    new_chunks = [
        dict(row) for row in (incremental.get("chunks") or [])
        if str(row.get("paper_sha256") or "") == paper_sha256
    ]
    if not new_chunks:
        raise TextGoldError("ingested_chunks_missing", "Incremental store has no chunks for this SHA.")
    if selected_product is None:
        product_store = json.loads(engine_e2e.STORE_PATH.read_text(encoding="utf-8"))
    else:
        product_store = {"chunks": list(selected_product.get("chunks") or [])}
    product_ids = _product_chunk_ids(selected_product, fallback_store=product_store)
    new_ids = {str(row["chunk_id"]) for row in new_chunks}
    if not new_ids.isdisjoint(product_ids):
        raise TextGoldError("sidecar_overlap", "Sidecar chunk_id set must be disjoint from the product 922.")
    known_sidecar = {str(row.get("chunk_id") or "") for row in (sidecar.get("chunks") or [])}
    if not new_ids.isdisjoint(known_sidecar):
        raise TextGoldError("sidecar_overlap", "Sidecar chunk_id set is not unique.")
    pending = _pending_vectors(incremental, selected=selected, expected_dim=compatible_dim)
    id_to_vec: dict[str, list[float]] = {}
    if sidecar_gzip_path.is_file():
        try:
            previous_raw = sidecar_gzip_path.read_bytes()
        except OSError as error:
            raise TextGoldError("bge10_index_unreadable", "Previous sidecar index is unreadable.") from error
        previous = _parse_gzip_index(previous_raw)
        id_to_vec = _previous_sidecar_vectors(
            previous,
            sidecar.get("chunks") or [],
            selected=selected,
            expected_dim=compatible_dim,
        )
    current_pending = {chunk_id: pending[chunk_id] for chunk_id in new_ids if chunk_id in pending}
    id_to_vec.update(current_pending)
    missing_rows = [row for row in new_chunks if str(row["chunk_id"]) not in id_to_vec]
    n_embedded = 0
    if missing_rows:
        texts = [research.chunk_sparse_corpus(row) for row in missing_rows]
        if embedder is None:
            model_id, vectors = research._dense_vectors(texts, selected)
        else:
            model_id, vectors = embedder(texts, selected)
        if str(model_id) != selected:
            raise TextGoldError("dense_model", "Embedder returned a substitute model id.")
        if len(vectors) != len(missing_rows):
            raise TextGoldError("embed_align", "Promote embed count is not the missing chunk count.")
        try:
            research._assert_generated_dense_vectors(
                vectors,
                expected_count=len(missing_rows),
                model_name=selected,
                expected_dim=compatible_dim,
            )
        except research.LiteratureContractError as error:
            raise TextGoldError("dense_dim", "Generated dense vectors are invalid.") from error
        for row, vector in zip(missing_rows, vectors):
            id_to_vec[str(row["chunk_id"])] = list(vector)
        n_embedded = len(missing_rows)
    working = json.loads(json.dumps(sidecar))
    working.setdefault("chunks", []).extend(new_chunks)
    papers = {str(sha) for sha in (working.get("indexed_paper_sha256") or []) if sha}
    papers.add(paper_sha256)
    working["indexed_paper_sha256"] = sorted(papers)
    working["n_chunks"] = len(working["chunks"])
    working["schema"] = engine_e2e.STORE_SCHEMA
    working_ids = [str(row["chunk_id"]) for row in working["chunks"]]
    if len(working_ids) != len(set(working_ids)):
        raise TextGoldError("bge10_chunk_membership", "Sidecar chunk_id set is not unique.")
    for chunk_id in working_ids:
        if chunk_id not in id_to_vec:
            raise TextGoldError("embed_align", "Sidecar dense membership is incomplete.")
    index = engine_e2e.store_to_literature_index(working, knowledgebase=SIDECAR_KNOWLEDGEBASE)
    dense_ids = [str(row["chunk_id"]) for row in (index.get("chunks") or [])]
    if dense_ids != working_ids:
        raise TextGoldError("bge10_chunk_membership", "Sidecar dense membership is conflicting.")
    ordered_vectors = [id_to_vec[chunk_id] for chunk_id in dense_ids]
    try:
        dim = research._assert_generated_dense_vectors(
            ordered_vectors,
            expected_count=len(dense_ids),
            model_name=selected,
            expected_dim=compatible_dim,
        )
        research._validate_dense_rows(
            dense_ids,
            ordered_vectors,
            dim=compatible_dim,
            require_bge_geometry=is_bge,
        )
    except research.LiteratureContractError as error:
        raise TextGoldError("dense_dim", "Generated dense vectors are invalid.") from error
    index["dense"] = research._attach_generated_recipe({
        "model": selected,
        "dim": dim,
        "chunk_ids": dense_ids,
        "vectors": ordered_vectors,
    })
    if selected_product is not None:
        try:
            research._union_product_and_sidecar(selected_product, index)
        except research.LiteratureContractError as error:
            _wrap_contract(error)
    sidecar_store_sha = _write_json(sidecar_store_path, working)
    sidecar_gzip_sha = _write_gzip(sidecar_gzip_path, index)
    sidecar_manifest = {
        "schema": engine_e2e.MANIFEST_SCHEMA,
        "knowledgebase": SIDECAR_KNOWLEDGEBASE,
        "index_path": str(sidecar_gzip_path),
        "n_indexed_papers": len(working["indexed_paper_sha256"]),
        "n_chunks": working["n_chunks"],
        "indexed_paper_sha256": list(working["indexed_paper_sha256"]),
        "store_sha256": sidecar_store_sha,
        "gzip_sha256": sidecar_gzip_sha,
        "embedder_in_index": True,
        "dense": _sidecar_manifest_dense(
            selected=selected,
            dim=compatible_dim,
            n_vectors=len(dense_ids),
        ),
    }
    _write_json(sidecar_index_path, sidecar_manifest)
    artifact = _union_floor(
        sidecar_index=index,
        sidecar_store_sha256=sidecar_store_sha,
        sidecar_gzip_sha256=sidecar_gzip_sha,
        dest_curves=dest_curves,
        product_index=selected_product,
        product_gzip_path=selected_product_index_path,
        product_gzip_sha256=selected_product_gzip_sha256,
    )
    floor = float(artifact["shipped_floor"])
    dest_index = _ensure_dest_index(dest, product_manifest_path=selected_product_manifest_path)
    promoted = {
        "knowledgebase": SIDECAR_KNOWLEDGEBASE,
        "index_path": str(sidecar_gzip_path),
        "n_papers": len(working["indexed_paper_sha256"]),
        "n_chunks": working["n_chunks"],
        "store_sha256": sidecar_store_sha,
        "gzip_sha256": sidecar_gzip_sha,
    }
    _update_product_index(dest_index, promoted=promoted, floor=floor)
    working_census = _mark_incremental_indexed(census, paper_sha256)
    _write_json(incremental_census_path, working_census)
    t5_corpus_graph.emit_t5_corpus_graph(
        dest=graph_path,
        store_path=engine_e2e.STORE_PATH,
        manifest_path=dest_index,
        sidecar_store_path=sidecar_store_path,
    )
    return _header_from_dests(
        status="ok",
        chunks_added=len(new_chunks),
        vectors_embedded=n_embedded,
        dest_dir=dest,
        floor=floor,
        census_status=INDEXED_STATUS,
    )


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise TextGoldError("one_sha", "RTI-1 takes exactly one paper SHA.")
    result = promote_ingested_paper(paper_sha256=args[0])
    print(json.dumps({
        "status": result["status"],
        "chunks_added": result["chunks_added"],
        "vectors_embedded": result["vectors_embedded"],
        "n_sidecar_papers": result["n_sidecar_papers"],
        "n_sidecar_chunks": result["n_sidecar_chunks"],
        "floor": result["floor"],
        "statistic": result["statistic"],
        "percentile": result["percentile"],
        "sidecar_store_sha256": result["sidecar_store_sha256"],
        "sidecar_gzip_sha256": result["sidecar_gzip_sha256"],
        "index_sha256": result["index_sha256"],
        "graph_sha256": result["graph_sha256"],
    }, indent=2))
    return result


if __name__ == "__main__":
    main()
