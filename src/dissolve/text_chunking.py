"""TEXT_CHUNKING_SPEC.v1 T0–T6. Pure functions of saved canonical_text + offsets.

T6 may load a pinned local embedder solely to place boundaries. It does not
invoke Docling, a frontier CLI, or ``research._dense_vectors``. Embeddings
never enter a chunk body, BM25, or an index. New persist — not the C6 series.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Mapping, Sequence

from . import research

SPEC_SHA256 = "f7bdc3f7ce519cfc456ff7249d804848dfadaaca21a5a4f57cc44518b8f50b55"

# Segmentation only. Not the retrieval / C8 path. Do not pass these vectors
# to ``_dense_vectors``, BM25, or any index builder.
T6_EMBEDDER_ID = "sentence-transformers/all-MiniLM-L6-v2"
T6_EMBEDDER_ROLE = "segmentation_only"
T6_PERCENTILES = (80, 95)

T0_TARGET = research._CHUNK_TARGET
T0_OVERLAP = research._CHUNK_OVERLAP
T1_SIZES = (400, 800, 1400, 2000)
T1_OVERLAP_FRACS = (0.0, 0.10, 0.25)
T2_SIZES = (800, 1400)
T2_OVERLAP_FRACS = (0.0, 0.15)
T3_SENTENCE_COUNTS = (3, 5, 8)
T4_MAX_SIZE = 1400
T5_TARGET = 1400

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_PARA_RE = re.compile(r"\n\n")


def _text(canonical: Mapping[str, Any]) -> str:
    return str(canonical.get("canonical_text") or "")


def _make(
    *,
    strategy: str,
    index: int,
    text: str,
    start: int,
    end: int,
    header: str = "",
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "strategy": strategy,
        "chunk_id": f"{strategy}-{index:04d}",
        "body": text[start:end],
        "header": header,
        "char_start": start,
        "char_end": end,
        "params": dict(params or {}),
    }
    return chunk


def _window(text: str, *, size: int, overlap: int, strategy: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    n = len(text)
    if n == 0 or size < 1:
        return []
    step = size - overlap
    if step < 1:
        step = 1
    chunks: list[dict[str, Any]] = []
    start = 0
    while start < n:
        end = min(start + size, n)
        chunks.append(_make(
            strategy=strategy, index=len(chunks) + 1, text=text,
            start=start, end=end, params=params,
        ))
        if end >= n:
            break
        start += step
    return chunks


def _align_collapsed(text: str, body: str, cursor: int) -> tuple[int, int] | None:
    """Map a whitespace-collapsed production body onto ``canonical_text``.

    ``_paragraph_chunks`` collapses ``\\s+`` to a single space, so ``str.find``
    misses on real documents. Offsets are the covering span of the same
    non-whitespace characters. ``body`` remains the production string and may
    differ from ``text[start:end]``.
    """
    needle = "".join(char for char in body if not char.isspace())
    if not needle:
        return None
    origin = max(0, cursor)
    limit = len(text)
    search = origin
    while search < limit:
        if text[search].isspace():
            search += 1
            continue
        pos = search
        matched = 0
        first = None
        last = None
        while pos < limit and matched < len(needle):
            if text[pos].isspace():
                pos += 1
                continue
            if text[pos] != needle[matched]:
                break
            if first is None:
                first = pos
            last = pos
            pos += 1
            matched += 1
        if matched == len(needle) and first is not None and last is not None:
            return first, last + 1
        search += 1
    return None


def chunk_t0(canonical: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Production ``_paragraph_chunks`` 1400 / 180 on ``canonical_text``."""
    text = _text(canonical)
    packed = research._paragraph_chunks(text, target=T0_TARGET, overlap=T0_OVERLAP)
    chunks: list[dict[str, Any]] = []
    cursor = 0
    params = {"target": T0_TARGET, "overlap": T0_OVERLAP}
    for header, body in packed:
        if not str(body).strip():
            continue
        start = text.find(body, cursor)
        if start < 0:
            start = text.find(body)
        if start >= 0:
            end = start + len(body)
            chunks.append(_make(
                strategy="T0", index=len(chunks) + 1, text=text,
                start=start, end=end, header=str(header or ""), params=params,
            ))
            cursor = end
            continue
        aligned = _align_collapsed(text, body, cursor)
        if aligned is None and cursor:
            aligned = _align_collapsed(text, body, 0)
        if aligned is None:
            chunks.append({
                "strategy": "T0",
                "chunk_id": f"T0-{len(chunks) + 1:04d}",
                "body": body,
                "header": str(header or ""),
                "char_start": None,
                "char_end": None,
                "params": dict(params),
                "offset_recovery": "failed",
            })
            continue
        start, end = aligned
        chunk = {
            "strategy": "T0",
            "chunk_id": f"T0-{len(chunks) + 1:04d}",
            "body": body,
            "header": str(header or ""),
            "char_start": start,
            "char_end": end,
            "params": dict(params),
            "offset_recovery": "whitespace_collapsed",
        }
        chunks.append(chunk)
        cursor = end
    return chunks


def chunk_t1(
    canonical: Mapping[str, Any],
    *,
    size: int,
    overlap_frac: float,
) -> list[dict[str, Any]]:
    """Fixed sliding window. ``overlap_frac`` is a fraction of ``size``."""
    text = _text(canonical)
    overlap = int(size * overlap_frac)
    return _window(
        text, size=size, overlap=overlap, strategy="T1",
        params={"size": size, "overlap_frac": overlap_frac, "overlap": overlap},
    )


def _split_once(text: str, start: int, end: int, kind: str) -> list[tuple[int, int]]:
    fragment = text[start:end]
    if kind == "para":
        matches = list(_PARA_RE.finditer(fragment))
    elif kind == "line":
        matches = list(re.finditer(r"\n", fragment))
    elif kind == "sentence":
        matches = list(_SENTENCE_RE.finditer(fragment))
    elif kind == "space":
        matches = list(re.finditer(r" ", fragment))
    else:
        return [(start, end)] if end > start else []
    if not matches:
        return [(start, end)] if end > start else []
    parts: list[tuple[int, int]] = []
    last = 0
    for match in matches:
        if match.start() > last:
            parts.append((start + last, start + match.start()))
        last = match.end()
    if last < len(fragment):
        parts.append((start + last, start + len(fragment)))
    return [(a, b) for a, b in parts if b > a]


def _hard_window(start: int, end: int, size: int, overlap: int) -> list[tuple[int, int]]:
    if end <= start:
        return []
    step = size - overlap
    if step < 1:
        step = 1
    spans: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        stop = min(cursor + size, end)
        spans.append((cursor, stop))
        if stop >= end:
            break
        cursor += step
    return spans


def _recursive_spans(
    text: str, start: int, end: int, size: int, overlap: int, seps: tuple[str, ...],
) -> list[tuple[int, int]]:
    if end <= start:
        return []
    if end - start <= size:
        return [(start, end)]
    if not seps:
        return _hard_window(start, end, size, overlap)
    parts = _split_once(text, start, end, seps[0])
    if len(parts) <= 1:
        return _recursive_spans(text, start, end, size, overlap, seps[1:])
    spans: list[tuple[int, int]] = []
    buf_s: int | None = None
    buf_e: int | None = None

    def flush() -> None:
        nonlocal buf_s, buf_e
        if buf_s is None or buf_e is None:
            return
        if buf_e - buf_s > size:
            spans.extend(_recursive_spans(text, buf_s, buf_e, size, overlap, seps[1:]))
        else:
            spans.append((buf_s, buf_e))
        buf_s = buf_e = None

    for part_s, part_e in parts:
        if part_e - part_s > size:
            flush()
            spans.extend(_recursive_spans(text, part_s, part_e, size, overlap, seps[1:]))
            continue
        if buf_s is None:
            buf_s, buf_e = part_s, part_e
            continue
        if part_e - buf_s > size:
            flush()
            if overlap and spans:
                back = max(part_s - overlap, start)
                buf_s, buf_e = back, part_e
            else:
                buf_s, buf_e = part_s, part_e
            continue
        buf_e = part_e
    flush()
    return spans


def chunk_t2(
    canonical: Mapping[str, Any],
    *,
    size: int,
    overlap_frac: float,
) -> list[dict[str, Any]]:
    """Recursive character split: ``\\n\\n`` → ``\\n`` → sentence → space."""
    text = _text(canonical)
    overlap = int(size * overlap_frac)
    params = {"size": size, "overlap_frac": overlap_frac, "overlap": overlap}
    spans = _recursive_spans(
        text, 0, len(text), size, overlap, ("para", "line", "sentence", "space"),
    )
    return [
        _make(strategy="T2", index=i, text=text, start=a, end=b, params=params)
        for i, (a, b) in enumerate(spans, 1) if b > a
    ]


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    if not text:
        return []
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_RE.finditer(text):
        if match.start() > cursor:
            spans.append((cursor, match.start()))
        cursor = match.end()
    if cursor < len(text):
        spans.append((cursor, len(text)))
    return [(a, b) for a, b in spans if b > a]


def chunk_t3(
    canonical: Mapping[str, Any],
    *,
    n_sentences: int,
    stride: int,
) -> list[dict[str, Any]]:
    """Sentence window of ``n_sentences`` with the given stride."""
    text = _text(canonical)
    units = _sentence_spans(text)
    if not units or n_sentences < 1 or stride < 1:
        return []
    params = {"n_sentences": n_sentences, "stride": stride}
    chunks: list[dict[str, Any]] = []
    index = 0
    while index < len(units):
        window = units[index:index + n_sentences]
        if not window:
            break
        start, end = window[0][0], window[-1][1]
        chunks.append(_make(
            strategy="T3", index=len(chunks) + 1, text=text,
            start=start, end=end, params=params,
        ))
        if index + n_sentences >= len(units):
            break
        index += stride
    return chunks


def _heading_key(block: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(item) for item in (block.get("nearest_preceding_heading") or []))


def _blocks(canonical: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in canonical.get("blocks") or []:
        try:
            start, end = int(block["char_start"]), int(block["char_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        rows.append({
            "char_start": start,
            "char_end": end,
            "heading": _heading_key(block),
            "kind": str(block.get("kind") or ""),
        })
    rows.sort(key=lambda item: (item["char_start"], item["char_end"]))
    return rows


def chunk_t4(canonical: Mapping[str, Any], *, max_size: int = T4_MAX_SIZE) -> list[dict[str, Any]]:
    """Never cross a ``nearest_preceding_heading`` boundary. Oversize → T2."""
    text = _text(canonical)
    blocks = _blocks(canonical)
    if not blocks:
        return chunk_t2(canonical, size=max_size, overlap_frac=0.0)
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_key: tuple[str, ...] | None = None
    for block in blocks:
        key = block["heading"]
        if current and key != current_key:
            groups.append(current)
            current = []
        current.append(block)
        current_key = key
    if current:
        groups.append(current)
    chunks: list[dict[str, Any]] = []
    params = {"max_size": max_size}
    for group in groups:
        start, end = group[0]["char_start"], group[-1]["char_end"]
        header = " / ".join(group[0]["heading"])
        if end - start <= max_size:
            chunks.append(_make(
                strategy="T4", index=len(chunks) + 1, text=text,
                start=start, end=end, header=header, params=params,
            ))
            continue
        inner = {"canonical_text": text[start:end]}
        for piece in chunk_t2(inner, size=max_size, overlap_frac=0.0):
            a = start + int(piece["char_start"])
            b = start + int(piece["char_end"])
            chunks.append(_make(
                strategy="T4", index=len(chunks) + 1, text=text,
                start=a, end=b, header=header, params=params,
            ))
    return chunks


def chunk_t5(canonical: Mapping[str, Any], *, target: int = T5_TARGET) -> list[dict[str, Any]]:
    """Never split a block. Pack whole blocks to ``target``."""
    text = _text(canonical)
    blocks = _blocks(canonical)
    if not blocks:
        return []
    chunks: list[dict[str, Any]] = []
    params = {"target": target}
    buf_s: int | None = None
    buf_e: int | None = None

    def flush() -> None:
        nonlocal buf_s, buf_e
        if buf_s is None or buf_e is None:
            return
        chunks.append(_make(
            strategy="T5", index=len(chunks) + 1, text=text,
            start=buf_s, end=buf_e, params=params,
        ))
        buf_s = buf_e = None

    for block in blocks:
        start, end = block["char_start"], block["char_end"]
        if buf_s is None:
            buf_s, buf_e = start, end
            continue
        if end - buf_s > target:
            flush()
            buf_s, buf_e = start, end
            continue
        buf_e = end
    flush()
    return chunks


def _t6_sentence_transformer() -> Callable[..., Any]:
    """Pinned segmentation embedder class. Not ``research._dense_vectors``."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return float(sum(a * b for a, b in zip(left, right)))


def _l2(vector: Sequence[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def _cosine_distance(left: Sequence[float], right: Sequence[float]) -> float:
    denom = _l2(left) * _l2(right)
    if denom == 0:
        return 1.0
    similarity = max(-1.0, min(1.0, _dot(left, right) / denom))
    return 1.0 - similarity


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (percentile / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _t6_segment_vectors(
    sentences: Sequence[str],
    *,
    encoder: Callable[[Sequence[str]], Sequence[Sequence[float]]] | None = None,
) -> list[list[float]]:
    """Encode sentences for boundary placement only. Never writes an index."""
    if encoder is not None:
        rows = encoder(sentences)
        return [list(row) for row in rows]
    model_type = _t6_sentence_transformer()
    model = model_type(T6_EMBEDDER_ID)
    encoded = model.encode(
        list(sentences),
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [[float(value) for value in row] for row in encoded]


def chunk_t6(
    canonical: Mapping[str, Any],
    *,
    percentile: float,
    encoder: Callable[[Sequence[str]], Sequence[Sequence[float]]] | None = None,
) -> list[dict[str, Any]]:
    """Semantic boundaries at a distance percentile. Segmentation only.

    ``encoder`` is a test seam. Production loads ``T6_EMBEDDER_ID`` and never
    calls ``research._dense_vectors``. Chunk dicts carry no vectors.
    """
    text = _text(canonical)
    units = _sentence_spans(text)
    params = {
        "percentile": percentile,
        "embedder": T6_EMBEDDER_ID,
        "embedder_role": T6_EMBEDDER_ROLE,
    }
    if not units:
        return []
    if len(units) == 1:
        start, end = units[0]
        return [_make(
            strategy="T6", index=1, text=text, start=start, end=end, params=params,
        )]
    sentences = [text[start:end] for start, end in units]
    vectors = _t6_segment_vectors(sentences, encoder=encoder)
    if len(vectors) != len(units):
        raise ValueError("T6 encoder must return one vector per sentence.")
    distances = [
        _cosine_distance(vectors[index], vectors[index + 1])
        for index in range(len(vectors) - 1)
    ]
    threshold = _percentile(distances, percentile)
    cuts = {index for index, dist in enumerate(distances) if dist > threshold}
    chunks: list[dict[str, Any]] = []
    group_start = 0
    for index in range(len(units)):
        at_cut = index in cuts
        at_end = index == len(units) - 1
        if not (at_cut or at_end):
            continue
        start, end = units[group_start][0], units[index][1]
        chunks.append(_make(
            strategy="T6", index=len(chunks) + 1, text=text,
            start=start, end=end, params=params,
        ))
        group_start = index + 1
    return chunks


def t1_grid() -> list[tuple[int, float]]:
    return [(size, frac) for size in T1_SIZES for frac in T1_OVERLAP_FRACS]


def t2_grid() -> list[tuple[int, float]]:
    return [(size, frac) for size in T2_SIZES for frac in T2_OVERLAP_FRACS]


def t1_control_pairs_for_t2() -> list[tuple[int, float]]:
    """T1 runs that match T2 size and overlap. Overlap 0 is on the T1 grid.

    T2's 15% overlap is not a T1-grid member; the control is still T1 at that
    same size and 15% so the pair is matched, not a nearby 10%/25%.
    """
    return list(t2_grid())


def t6_grid() -> list[float]:
    return list(T6_PERCENTILES)


def slice_ok(canonical: Mapping[str, Any], chunk: Mapping[str, Any]) -> bool:
    start, end = chunk.get("char_start"), chunk.get("char_end")
    if start is None or end is None:
        return False
    text = _text(canonical)
    return text[int(start):int(end)] == chunk.get("body")


def offsets_usable(chunk: Mapping[str, Any]) -> bool:
    """None offsets are not a preserved span. Auditor hold-to on T0."""
    start, end = chunk.get("char_start"), chunk.get("char_end")
    if start is None or end is None:
        return False
    try:
        return int(end) > int(start)
    except (TypeError, ValueError):
        return False
