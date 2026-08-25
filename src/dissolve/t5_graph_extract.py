"""G-3 overlay entity graph. Registry-span matcher. No Muse. Persist GRAPH is read-only."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from . import contaminants, t5_corpus_graph, t5_graph_lookup
from . import thermodynamics as thermo
from .gold_ensemble import file_sha256
from .text_gold import DEFAULT_OUT_DIR, TextGoldError

SPEC_SHA256 = "81b8ea013821e853b0b6a1a3337961f08fec4ec2421b00ca58a09feb15e839c3"
GRAPH_SHA256 = "3e7914dc0fe42b7e3a779e9a24ce2942b91f807645f74dee2e62197bebaf0c25"
SCHEMA = "dissolve.t5-entity-graph.overlay.v1"
OVERLAY_NAME = "GRAPH.t5.entities.overlay.v1.json"
REPORT_NAME = "GRAPH.t5.entities.report.v1.json"
OVERLAY_PATH = DEFAULT_OUT_DIR / OVERLAY_NAME
REPORT_PATH = DEFAULT_OUT_DIR / REPORT_NAME
EDGE_TYPES = frozenset({"mentions", "same_entity", "pair_mentioned"})
REGISTRIES = frozenset({"polymer", "solvent", "contaminant"})
_BOUNDARY = r"(?<![A-Za-z0-9]){surface}(?![A-Za-z0-9])"


def _refuse_gold_v2() -> None:
    t5_graph_lookup._refuse_gold_v2()


def _refuse_url(value: str) -> None:
    t5_graph_lookup._refuse_url(value)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _dump(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _protected_dests() -> set[Path]:
    protected = {
        t5_corpus_graph.GRAPH_PATH.resolve(),
        t5_corpus_graph.STORE_PATH.resolve(),
        t5_corpus_graph.MANIFEST_PATH.resolve(),
        t5_corpus_graph.GOLD_PATH.resolve(),
        t5_corpus_graph.CENSUS_PATH.resolve(),
        Path(thermo._ASSET).expanduser().resolve(),
        Path(contaminants._ASSET).expanduser().resolve(),
        (DEFAULT_OUT_DIR / "OFFDOMAIN.queries.v1.json").resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.abstention.v1.json").resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.weights.v1.json").resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.v3.json").resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.v4.json").resolve(),
        (DEFAULT_OUT_DIR / "CURVES.retrieval.d3.json").resolve(),
    }
    manifest = t5_corpus_graph.MANIFEST_PATH
    if manifest.is_file():
        index_path = json.loads(manifest.read_text(encoding="utf-8")).get("index_path")
        if index_path:
            protected.add(Path(str(index_path)).expanduser().resolve())
    return protected


def _refuse_pin_dest(dest: Path) -> None:
    if dest == t5_corpus_graph.GRAPH_PATH.resolve():
        raise TextGoldError("persist_graph_refused", "G-3 does not rewrite persist GRAPH.")
    if dest in _protected_dests():
        raise TextGoldError("persist_graph_refused", "G-3 does not overwrite a pinned dest.")


def _report_path(dest: Path) -> Path:
    if dest == OVERLAY_PATH.resolve():
        return REPORT_PATH
    return dest.with_name(dest.stem + ".report.json")


def _compile_lexicon(
    surfaces: list[tuple[str, str]],
) -> tuple[re.Pattern[str] | None, dict[str, str]]:
    mapping: dict[str, str] = {}
    parts: list[str] = []
    for surface, key in surfaces:
        folded = surface.casefold()
        if not folded:
            continue
        mapping.setdefault(folded, key)
        parts.append(re.escape(surface))
    if not parts:
        return None, {}
    pattern = re.compile(
        _BOUNDARY.format(surface="(" + "|".join(parts) + ")"),
        re.I,
    )
    return pattern, mapping


def _scan_lexicon(
    text: str,
    scanner: tuple[re.Pattern[str] | None, dict[str, str]],
) -> list[tuple[int, int, str]]:
    pattern, mapping = scanner
    if pattern is None:
        return []
    hits: list[tuple[int, int, str]] = []
    for match in pattern.finditer(text):
        hits.append((match.start(), match.end(), mapping.get(match.group(1).casefold(), "")))
    return hits


@lru_cache(maxsize=1)
def _solvent_scanner() -> tuple[re.Pattern[str] | None, dict[str, str]]:
    return _compile_lexicon(_solvent_surfaces())


@lru_cache(maxsize=1)
def _contaminant_scanner() -> tuple[re.Pattern[str] | None, dict[str, str]]:
    return _compile_lexicon(_contaminant_surfaces())


@lru_cache(maxsize=1)
def _polymer_scanner() -> tuple[re.Pattern[str] | None, dict[str, str]]:
    return _compile_lexicon(list(thermo._polymer_mention_surfaces()))


def _polymer_hits(text: str) -> list[tuple[int, int, str]]:
    selected = [
        item for item in _scan_lexicon(text, _polymer_scanner()) if item[2]
    ]
    identities = {item[2] for item in selected}
    return [
        item
        for item in selected
        if not (
            thermo.POLYMER_IDENTITIES[item[2]].get("thermodynamic_members")
            and any(
                member in identities
                for member in thermo.POLYMER_IDENTITIES[item[2]]["thermodynamic_members"]
            )
        )
    ]


@lru_cache(maxsize=1)
def _solvent_surfaces() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for alias, row in thermo._aliases().items():
        rows.append((str(alias), str(row.get("interp_key") or "")))
    for alias, key in thermo.SOURCE_SOLVENT_ALIASES.items():
        rows.append((str(alias), str(key)))
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for surface, key in sorted(rows, key=lambda item: (-len(item[0]), item[0].casefold())):
        folded = surface.casefold()
        if not folded or folded in seen:
            continue
        seen.add(folded)
        unique.append((surface, key))
    return unique


@lru_cache(maxsize=1)
def _contaminant_surfaces() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for folded, identity in contaminants._contaminant_lookup().items():
        rows.append((str(folded), str(identity.key)))
    return sorted(rows, key=lambda item: (-len(item[0]), item[0].casefold()))


def _resolve_solvent(surface: str, table_key: str) -> tuple[str | None, str]:
    folded = str(surface or "").strip().lower()
    if folded in thermo._ambiguous_property_identity_labels():
        return None, "identity_ambiguous"
    key = str(table_key or "").strip()
    if not key:
        alias = thermo._aliases().get(folded, {})
        key = str(alias.get("interp_key") or "").strip()
    if not key:
        mapped = thermo.SOURCE_SOLVENT_ALIASES.get(folded)
        if mapped:
            key = str(mapped)
    if not key:
        return None, "unresolved"
    return key, "ok"


def _mentions_for_chunk(row: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    body = str(row.get("body") or "")
    chunk_id = str(row["chunk_id"])
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    polymers = [
        {"registry": "polymer", "canonical_key": identity, "char_start": start, "char_end": end}
        for start, end, identity in _polymer_hits(body)
    ]
    solvents: list[dict[str, Any]] = []
    for start, end, table_key in _scan_lexicon(body, _solvent_scanner()):
        key, reason = _resolve_solvent(body[start:end], table_key)
        item = {
            "registry": "solvent",
            "canonical_key": key or "",
            "char_start": start,
            "char_end": end,
        }
        if key is None:
            unresolved.append({**item, "chunk_id": chunk_id, "reason": reason})
        else:
            item["canonical_key"] = key
            solvents.append(item)
    contaminants_hits: list[dict[str, Any]] = []
    for start, end, key in _scan_lexicon(body, _contaminant_scanner()):
        if not key:
            unresolved.append({
                "registry": "contaminant",
                "canonical_key": "",
                "char_start": start,
                "char_end": end,
                "chunk_id": chunk_id,
                "reason": "unresolved",
            })
            continue
        contaminants_hits.append({
            "registry": "contaminant",
            "canonical_key": key,
            "char_start": start,
            "char_end": end,
        })
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for item in polymers + solvents + contaminants_hits:
        groups[(int(item["char_start"]), int(item["char_end"]))].append(item)
    for span, items in groups.items():
        keys = {(str(item["registry"]), str(item["canonical_key"])) for item in items}
        if len(keys) > 1:
            for item in items:
                unresolved.append({
                    **item,
                    "chunk_id": chunk_id,
                    "reason": "identity_ambiguous",
                })
            continue
        resolved.append({**items[0], "chunk_id": chunk_id})
    return resolved, unresolved


def _union_find_components(papers: set[str], edges: list[tuple[str, str]]) -> list[set[str]]:
    parent = {paper: paper for paper in papers}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for left, right in edges:
        if left not in parent or right not in parent:
            continue
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a
    groups: dict[str, set[str]] = defaultdict(set)
    for paper in papers:
        groups[find(paper)].add(paper)
    return list(groups.values())


def extract_t5_entity_graph(
    *,
    dest: str | Path | None = None,
    graph_path: str | Path | None = None,
    store_path: str | Path | None = None,
) -> dict[str, Any]:
    """Scan store bodies against held registries. Write overlay dest. No persist GRAPH."""
    _refuse_gold_v2()
    dest_path = Path(dest).expanduser().resolve() if dest is not None else OVERLAY_PATH.resolve()
    _refuse_url(str(dest_path))
    _refuse_pin_dest(dest_path)
    source_path = (
        Path(graph_path).expanduser().resolve()
        if graph_path is not None
        else t5_corpus_graph.GRAPH_PATH.resolve()
    )
    store_file = (
        Path(store_path).expanduser().resolve()
        if store_path is not None
        else t5_corpus_graph.STORE_PATH.resolve()
    )
    _refuse_url(str(source_path))
    _refuse_url(str(store_file))
    if dest_path == source_path:
        raise TextGoldError("persist_graph_refused", "G-3 overlay dest must not be the source GRAPH.")
    source = _load_json(source_path)
    store = _load_json(store_file)
    source_digest = file_sha256(source_path)
    resolved_all: list[dict[str, Any]] = []
    unresolved_all: list[dict[str, Any]] = []
    chunk_paper: dict[str, str] = {}
    for row in store.get("chunks") or []:
        if not isinstance(row, Mapping):
            continue
        chunk_id = str(row.get("chunk_id") or "")
        paper = str(row.get("paper_sha256") or "")
        if not chunk_id or not paper:
            continue
        chunk_paper[chunk_id] = paper
        resolved, unresolved = _mentions_for_chunk(row)
        resolved_all.extend(resolved)
        unresolved_all.extend(unresolved)

    entity_nodes: dict[str, dict[str, Any]] = {}
    mentions: list[dict[str, Any]] = []
    for item in resolved_all:
        registry = str(item["registry"])
        key = str(item["canonical_key"])
        if registry not in REGISTRIES or not key:
            continue
        node_id = f"entity:{registry}:{key}"
        entity_nodes[node_id] = {
            "node_id": node_id,
            "node_type": "entity",
            "canonical_key": key,
            "payload": {"registry": registry, "canonical_key": key},
        }
        chunk_id = str(item["chunk_id"])
        start = int(item["char_start"])
        end = int(item["char_end"])
        mentions.append({
            "edge_id": f"mentions:{chunk_id}:{registry}:{key}:{start}:{end}",
            "edge_type": "mentions",
            "source_node_id": f"chunk:{chunk_id}",
            "target_node_id": node_id,
            "payload": {
                "chunk_id": chunk_id,
                "char_start": start,
                "char_end": end,
                "registry": registry,
                "canonical_key": key,
            },
        })

    identity_papers: dict[tuple[str, str], dict[str, tuple[str, int, int]]] = defaultdict(dict)
    for edge in mentions:
        payload = edge["payload"]
        paper = chunk_paper.get(str(payload["chunk_id"]) or "")
        if not paper:
            continue
        ident = (str(payload["registry"]), str(payload["canonical_key"]))
        span = (str(payload["chunk_id"]), int(payload["char_start"]), int(payload["char_end"]))
        current = identity_papers[ident].get(paper)
        if current is None or span < current:
            identity_papers[ident][paper] = span

    same_entity: list[dict[str, Any]] = []
    for (registry, key), papers in identity_papers.items():
        names = sorted(papers)
        for index, left in enumerate(names):
            for right in names[index + 1:]:
                left_span = papers[left]
                right_span = papers[right]
                same_entity.append({
                    "edge_id": f"same_entity:{left}:{right}:{registry}:{key}",
                    "edge_type": "same_entity",
                    "source_node_id": f"paper:{left}",
                    "target_node_id": f"paper:{right}",
                    "payload": {
                        "chunk_id": left_span[0],
                        "char_start": left_span[1],
                        "char_end": left_span[2],
                        "registry": registry,
                        "canonical_key": key,
                        "provenance": [
                            {
                                "chunk_id": left_span[0],
                                "char_start": left_span[1],
                                "char_end": left_span[2],
                            },
                            {
                                "chunk_id": right_span[0],
                                "char_start": right_span[1],
                                "char_end": right_span[2],
                            },
                        ],
                    },
                })

    by_chunk: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in resolved_all:
        by_chunk[str(item["chunk_id"])].append(item)
    pair_edges: list[dict[str, Any]] = []
    pair_papers: dict[tuple[str, str], set[str]] = defaultdict(set)
    for chunk_id, items in by_chunk.items():
        polymers = [item for item in items if item["registry"] == "polymer"]
        solvents = [item for item in items if item["registry"] == "solvent"]
        paper = chunk_paper.get(chunk_id, "")
        seen_pairs: set[tuple[str, str]] = set()
        for polymer in polymers:
            for solvent in solvents:
                pair = (str(polymer["canonical_key"]), str(solvent["canonical_key"]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                if paper:
                    pair_papers[pair].add(paper)
                p_start = int(polymer["char_start"])
                s_start = int(solvent["char_start"])
                pair_edges.append({
                    "edge_id": (
                        f"pair_mentioned:{chunk_id}:{pair[0]}:{pair[1]}:{p_start}:{s_start}"
                    ),
                    "edge_type": "pair_mentioned",
                    "source_node_id": f"entity:polymer:{pair[0]}",
                    "target_node_id": f"entity:solvent:{pair[1]}",
                    "payload": {
                        "chunk_id": chunk_id,
                        "polymer": {
                            "char_start": p_start,
                            "char_end": int(polymer["char_end"]),
                            "canonical_key": pair[0],
                        },
                        "solvent": {
                            "char_start": s_start,
                            "char_end": int(solvent["char_end"]),
                            "canonical_key": pair[1],
                        },
                    },
                })

    extra_nodes = sorted(entity_nodes.values(), key=lambda row: str(row["node_id"]))
    extra_edges = sorted(
        mentions + same_entity + pair_edges,
        key=lambda row: str(row["edge_id"]),
    )
    overlay = _clone(source)
    overlay["schema"] = SCHEMA
    overlay["spec_sha256"] = SPEC_SHA256
    overlay["source_graph_sha256"] = source_digest
    overlay["nodes"] = list(source.get("nodes") or []) + extra_nodes
    overlay["edges"] = list(source.get("edges") or []) + extra_edges
    t5_corpus_graph._walk_forbidden(overlay)

    paper_nodes = {
        str(node.get("canonical_key") or "")
        for node in source.get("nodes") or []
        if str(node.get("node_type") or "") == "paper"
    }
    same_pairs = [
        (
            str(edge["source_node_id"]).removeprefix("paper:"),
            str(edge["target_node_id"]).removeprefix("paper:"),
        )
        for edge in same_entity
    ]
    components = _union_find_components(paper_nodes, same_pairs)
    largest = max((len(item) for item in components), default=0)
    papers_with_edge = {paper for pair in same_pairs for paper in pair}
    n_gt1 = sum(1 for papers in identity_papers.values() if len(papers) > 1)
    n_raw = len(resolved_all) + len(unresolved_all)
    n_unresolved = len(unresolved_all)
    rate = (n_unresolved / n_raw) if n_raw else 0.0
    n_shared_pairs = sum(1 for papers in pair_papers.values() if len(papers) > 1)
    connectivity = "disjoint" if largest == 1 else "connected"
    report = {
        "schema": "dissolve.t5-entity-graph.report.v1",
        "spec_sha256": SPEC_SHA256,
        "source_graph_sha256": source_digest,
        "n_entity_nodes": len(extra_nodes),
        "n_mentions_edges": len(mentions),
        "n_same_entity_edges": len(same_entity),
        "n_entities_in_gt1_paper": n_gt1,
        "n_papers_with_same_entity": len(papers_with_edge),
        "n_same_entity_components": len(components),
        "largest_same_entity_component": largest,
        "n_pair_mentioned_edges": len(pair_edges),
        "n_pair_shared_cross_paper": n_shared_pairs,
        "n_raw_mentions": n_raw,
        "n_unresolved_mentions": n_unresolved,
        "unresolved_rate": rate,
        "corpus_connectivity": connectivity,
        "unresolved": [
            {
                "chunk_id": str(item["chunk_id"]),
                "char_start": int(item["char_start"]),
                "char_end": int(item["char_end"]),
                "reason": str(item.get("reason") or "unresolved"),
                "registry": str(item.get("registry") or ""),
            }
            for item in unresolved_all
        ],
    }
    t5_corpus_graph._walk_forbidden(report)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(_dump(overlay), encoding="utf-8")
    report_file = _report_path(dest_path)
    report["dest_sha256"] = file_sha256(dest_path)
    report_file.write_text(_dump(report), encoding="utf-8")
    header = {
        "dest": str(dest_path),
        "dest_sha256": report["dest_sha256"],
        "report_path": str(report_file),
        "source_graph_sha256": source_digest,
        "spec_sha256": SPEC_SHA256,
        "n_entity_nodes": report["n_entity_nodes"],
        "n_mentions_edges": report["n_mentions_edges"],
        "n_same_entity_edges": report["n_same_entity_edges"],
        "n_entities_in_gt1_paper": report["n_entities_in_gt1_paper"],
        "n_papers_with_same_entity": report["n_papers_with_same_entity"],
        "n_same_entity_components": report["n_same_entity_components"],
        "largest_same_entity_component": report["largest_same_entity_component"],
        "n_pair_mentioned_edges": report["n_pair_mentioned_edges"],
        "n_pair_shared_cross_paper": report["n_pair_shared_cross_paper"],
        "n_raw_mentions": report["n_raw_mentions"],
        "n_unresolved_mentions": report["n_unresolved_mentions"],
        "unresolved_rate": report["unresolved_rate"],
        "corpus_connectivity": report["corpus_connectivity"],
    }
    t5_corpus_graph._walk_forbidden(header)
    return header
