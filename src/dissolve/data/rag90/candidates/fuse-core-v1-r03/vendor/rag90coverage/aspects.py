"""Split a question into aspects without a model call.

Clauses split on sentence boundaries, on ", and" / ", or" when the right
side is its own clause, and on "and" before a question word. Lists of
short names stay in one clause. Quantities and corpus abbreviations become
extra sub-queries only when they are not already covered by a clause.
"""

from __future__ import annotations

import re
from collections import Counter

_CONTENT_STOP = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "in", "is", "it", "of", "on", "or", "that", "the", "their",
    "this", "to", "was", "were", "what", "which", "with", "did", "does",
    "do", "has", "have", "had", "its", "into", "than", "then", "also",
})
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_QWORD_RE = re.compile(
    r"^(?:what|which|how|why|when|where|who|whether)\b",
    re.IGNORECASE,
)
_VERB_START_RE = re.compile(
    r"^(?:is|are|was|were|did|does|do|has|have|had|can|could|should|would)\b",
    re.IGNORECASE,
)
_SENT_RE = re.compile(r"(?<=[.?!])\s+(?=[A-Z])")
_AND_Q_RE = re.compile(
    r"\s+and\s+(?=(?:what|which|how|why|when|where|who|whether)\b)",
    re.IGNORECASE,
)
_COMMA_OR_RE = re.compile(r",\s+or\s+", re.IGNORECASE)
_DASH_RE = re.compile(r"\s+[—–]\s+|\s+;\s+")
_NUM_RE = re.compile(
    r"(?<![A-Za-z])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s*[-–]\s*(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?))?"
    r"\s*"
    r"(%|wt\.?\s*%|vol\.?\s*%|weight\s*%|°C|ºC|MPa|kPa|GPa|kDa|"
    r"nm|µm|μm|mm|cm|kg|mg|mL|mol|mM|ppm|ppb|bar|rpm|"
    r"kg/d|t/y|g/L|L/mg|eq/t|CO2-eq/t)",
    re.IGNORECASE,
)
_ABBR_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}|[A-Z][a-z]?[A-Z][A-Za-z0-9]{0,8})\b")
_ABBR_STOP = frozenset({
    "AND", "OR", "THE", "FOR", "NOT", "FROM", "WITH", "THIS", "THAT",
    "WERE", "WAS", "HAS", "HAVE", "HAD", "ARE", "BUT", "ALL", "ANY",
    "MAY", "CAN", "WHO", "HOW", "WHY", "FIG", "EQ", "REF", "VS", "ET",
    "AL", "OK", "NO", "YES", "II", "III", "IV", "VI", "VII",
})
_DEF_FORWARD = re.compile(
    r"([A-Za-z][A-Za-z0-9\-/ ]{2,80}?)\s*\(\s*([A-Z][A-Za-z0-9]{1,10})\s*\)"
)
_DEF_REVERSE = re.compile(
    r"\b([A-Z][A-Za-z0-9]{1,10})\s*\(\s*([A-Za-z][^)]{2,80})\)"
)
_BAD_EXPANSION = re.compile(
    r"\b(figure|table|equation|section|appendix|chapter|ref|ibid)\b",
    re.IGNORECASE,
)
_MAX_ASPECTS = 6


def content_tokens(value: str) -> list[str]:
    return [
        token for token in _TOKEN_RE.findall(str(value or "").casefold())
        if len(token) > 1 and token not in _CONTENT_STOP
    ]


def jaccard(left: str, right: str) -> float:
    a = set(content_tokens(left))
    b = set(content_tokens(right))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _clean_expansion(expansion: str) -> str:
    words = expansion.split()
    while words and (
        words[0].casefold().strip(",.;:") in {"and", "or", "the", "a", "an", "of", "in", "for", "s"}
        or (len(words[0].strip(",.;:")) == 1 and words[0][:1].isalpha())
    ):
        words = words[1:]
    return " ".join(words).strip(" ,.;")


def _abbr_agrees(abbr: str, expansion: str) -> bool:
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", abbr):
        return False
    words = [word for word in re.findall(r"[A-Za-z][A-Za-z\-]+", expansion) if word.casefold() not in _CONTENT_STOP]
    if len(words) < 2:
        return False
    initials = "".join(word[0].upper() for word in words)
    letters = "".join(ch for ch in abbr if ch.isalpha())
    if not letters or not initials or letters[0] != initials[0]:
        return False
    cursor = 0
    matched = 0
    for letter in letters:
        found = initials.find(letter, cursor)
        if found < 0:
            continue
        matched += 1
        cursor = found + 1
    return matched >= max(2, (len(letters) + 1) // 2)


def mine_abbreviations(texts: list[str], min_count: int = 2) -> list[dict]:
    """Repeated all-caps definitions whose expansion agrees with the abbreviation."""
    counts: Counter[tuple[str, str]] = Counter()
    for text in texts:
        for match in _DEF_FORWARD.finditer(text):
            _keep_pair(counts, match.group(2), match.group(1))
        for match in _DEF_REVERSE.finditer(text):
            _keep_pair(counts, match.group(1), match.group(2))
    best: dict[str, tuple[int, str]] = {}
    for (abbr, expansion), count in counts.items():
        expansion = _clean_expansion(expansion)
        if count < min_count or not _abbr_agrees(abbr, expansion):
            continue
        if re.search(r"https?://|supplementary|wiley|copyright", expansion, re.IGNORECASE):
            continue
        current = best.get(abbr)
        if current is None or count > current[0] or (count == current[0] and len(expansion) < len(current[1])):
            best[abbr] = (count, expansion)
    rows = [
        {"abbr": abbr, "expansion": expansion, "count": count}
        for abbr, (count, expansion) in best.items()
    ]
    rows.sort(key=lambda row: (-row["count"], row["abbr"]))
    return rows


def _keep_pair(counts: Counter[tuple[str, str]], abbr: str, expansion: str) -> None:
    abbr = abbr.strip()
    expansion = " ".join(expansion.strip().split())
    if abbr.upper() in _ABBR_STOP or len(abbr) < 2:
        return
    if not any(ch.isupper() for ch in abbr):
        return
    if _BAD_EXPANSION.search(expansion):
        return
    words = [word for word in re.findall(r"[A-Za-z][A-Za-z\-]+", expansion) if word.casefold() not in _CONTENT_STOP]
    if len(words) < 2 or len(words) > 8:
        return
    if len(expansion) > 80:
        return
    counts[(abbr, expansion)] += 1


_FINITE_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|did|does|do|has|have|had|can|could|may|might|must|shall|will|would|should)\b",
    re.IGNORECASE,
)
_YET_RE = re.compile(r"\s+(?:yet|but|whereas)\s+", re.IGNORECASE)
_WHILE_RE = re.compile(r",\s+(?:while|whereas)\s+", re.IGNORECASE)
_COMMA_Q_RE = re.compile(
    r",\s+(?=(?:what|which|how|why|when|where|who|whether)\b)",
    re.IGNORECASE,
)


def _has_finite(text: str) -> bool:
    return _FINITE_RE.search(text) is not None


def _clause_right(right: str) -> bool:
    text = right.strip()
    if re.search(r"\b(?:what|which|how|why|when|where|who|whether)\b", text, re.IGNORECASE) and content_tokens(text):
        return True
    if _VERB_START_RE.match(text) and len(content_tokens(text)) >= 2:
        return True
    if len(content_tokens(text)) >= 8:
        return True
    return False


def _keep_piece(text: str) -> bool:
    stripped = text.strip()
    if _QWORD_RE.match(stripped) and content_tokens(stripped):
        return True
    return len(content_tokens(stripped)) >= 2


def _list_continuation(right: str) -> bool:
    """True when ', and' is the last item of a name list, not a new clause."""
    text = right.strip()
    if _QWORD_RE.match(text) or _VERB_START_RE.match(text):
        return False
    if re.match(r"^(?:the|a|an|this|that|those|these|its|their|on|in|for|at|by)\b", text, re.IGNORECASE):
        return False
    if re.match(r"^[A-Z][A-Z0-9]{1,10}\s*,", text):
        return True
    name_comma = re.match(r"^((?:[A-Za-z][A-Za-z0-9\-]*\s+){0,3}[A-Za-z][A-Za-z0-9\-]*)\s*,", text)
    if name_comma and not re.search(
        r"\b(?:used|run|showed|shown|reported|listed|contain|contains|contained|measured|found|gave|give|gives|was|were|is|are)\b",
        name_comma.group(1),
        re.IGNORECASE,
    ):
        return True
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-]{0,24}(?:\s+[A-Za-z0-9][A-Za-z0-9\-]{0,24}){0,3}\.?", text):
        return True
    if re.match(
        r"^(?:[A-Za-z0-9][A-Za-z0-9\-]*\s+){0,3}[A-Za-z0-9][A-Za-z0-9\-]*\s+"
        r"(?:appear|appears|were|was|are|is|include|includes|included|remain|remains)\b",
        text,
        re.IGNORECASE,
    ):
        return True
    return False


def _split_marks(sentence: str) -> list[str]:
    parts = [sentence.strip()]
    changed = True
    while changed:
        changed = False
        nxt: list[str] = []
        for part in parts:
            pieces = _split_part(part)
            if len(pieces) > 1:
                changed = True
            nxt.extend(pieces)
        parts = nxt
    return [part for part in parts if part]


def _split_part(part: str) -> list[str]:
    for pattern in (_DASH_RE, _WHILE_RE, _COMMA_OR_RE, _COMMA_Q_RE, _YET_RE):
        match = pattern.search(part)
        if match and _both_sides(part[: match.start()], part[match.end() :]):
            return [part[: match.start()].strip(), part[match.end() :].strip()]
    match = _AND_Q_RE.search(part)
    if match and content_tokens(part[: match.start()]):
        return [part[: match.start()].strip(), part[match.end() :].strip()]
    for match in re.finditer(r",\s+and\s+", part, re.IGNORECASE):
        left, right = part[: match.start()].strip(), part[match.end() :].strip()
        if _list_continuation(right):
            continue
        if _clause_right(right) and len(content_tokens(left)) >= 3:
            return [left, right]
    both = re.search(r"^(?P<head>.*?\bboth\s+)(?P<left>.+?)\s+and\s+(?P<right>.+)$", part, re.IGNORECASE)
    if both:
        left = (both.group("head") + both.group("left")).strip()
        right = both.group("right").strip()
        if len(content_tokens(both.group("left"))) >= 2 and len(content_tokens(right)) >= 2:
            return [left, right]
    and_for = re.search(r"^(?P<left>.+?)\s+and\s+for\s+(?P<right>.+)$", part, re.IGNORECASE)
    if and_for and len(content_tokens(and_for.group("left"))) >= 3 and len(content_tokens(and_for.group("right"))) >= 3:
        return [and_for.group("left").strip(), "for " + and_for.group("right").strip()]
    for match in re.finditer(r"\s+and\s+", part, re.IGNORECASE):
        left, right = part[: match.start()].strip(), part[match.end() :].strip()
        if _list_continuation(right):
            continue
        if (
            _has_finite(left)
            and _has_finite(right)
            and len(content_tokens(left)) >= 3
            and len(content_tokens(right)) >= 3
        ):
            return [left, right]
    return [part]


def _both_sides(left: str, right: str) -> bool:
    if _list_continuation(right):
        return False
    return len(content_tokens(left)) >= 3 and (_clause_right(right) or len(content_tokens(right)) >= 3)


def _sentences(question: str) -> list[str]:
    text = " ".join(str(question or "").strip().split())
    if not text:
        return []
    return [part.strip(" -—") for part in _SENT_RE.split(text) if part.strip(" -—")]


def _sentence_span(question: str, index: int) -> tuple[int, int]:
    starts = [0]
    for match in _SENT_RE.finditer(question):
        starts.append(match.end())
    start = 0
    for cursor in starts:
        if cursor <= index:
            start = cursor
    end = len(question)
    for cursor in starts:
        if cursor > index:
            end = cursor
            break
    return start, end


def _quantity_windows(question: str) -> list[str]:
    windows = []
    for match in _NUM_RE.finditer(question):
        sent_start, sent_end = _sentence_span(question, match.start())
        left = re.findall(r"\S+", question[sent_start : match.start()])[-8:]
        right = re.findall(r"\S+", question[match.end() : sent_end])[:8]
        window = " ".join(left + [question[match.start() : match.end()]] + right).strip(" ,;.")
        if len(content_tokens(window)) >= 2:
            windows.append(window)
    return windows


def _entity_queries(question: str, expansions: dict[str, str]) -> list[str]:
    found = []
    seen = set()
    for match in _ABBR_RE.finditer(question):
        abbr = match.group(1)
        if abbr.upper() in _ABBR_STOP or abbr in seen:
            continue
        if len(abbr) < 2:
            continue
        seen.add(abbr)
        found.append(abbr)
    found.sort(key=lambda item: (-len(item), item))
    quantities = " ".join(match.group(0) for match in _NUM_RE.finditer(question))
    queries = []
    for abbr in found[:3]:
        expansion = expansions.get(abbr) or expansions.get(abbr.upper()) or ""
        extra = expansion if expansion and expansion.casefold() not in question.casefold() else ""
        query = " ".join(part for part in (abbr, extra, quantities) if part).strip()
        if len(content_tokens(query)) >= 2 and abbr not in queries:
            queries.append(query)
    return queries


def _expand_inplace(text: str, expansions: dict[str, str]) -> str:
    extras = []
    for match in _ABBR_RE.finditer(text):
        abbr = match.group(1)
        expansion = expansions.get(abbr) or expansions.get(abbr.upper())
        if not expansion:
            continue
        if expansion.casefold() in text.casefold():
            continue
        extras.append(expansion)
    if not extras:
        return text
    return text + " " + " ".join(dict.fromkeys(extras))


def decompose(question: str, expansions: dict[str, str] | None = None) -> list[dict]:
    """Return up to six aspects. Each item is {role, text}.

    role is clause, quantity, or entity. The full question is not repeated
    as an aspect; the caller retrieves it as the ranking prior.
    """
    expansions = expansions or {}
    question = " ".join(str(question or "").strip().split())
    if not question:
        return []
    clauses: list[str] = []
    for sentence in _sentences(question):
        clauses.extend(_split_marks(sentence))
    merged: list[str] = []
    for clause in clauses:
        if _keep_piece(clause):
            merged.append(clause.strip())
        elif merged:
            merged[-1] = (merged[-1] + " " + clause).strip()
    aspects: list[dict] = [{"role": "clause", "text": text} for text in merged]
    if not aspects:
        aspects = [{"role": "clause", "text": question}]

    for window in _quantity_windows(question):
        window_tokens = set(content_tokens(window))
        if any(jaccard(window, item["text"]) >= 0.72 for item in aspects):
            continue
        host = [
            item for item in aspects
            if item["role"] == "clause" and window_tokens <= set(content_tokens(item["text"]))
        ]
        if host and len(content_tokens(host[0]["text"])) < len(window_tokens) + 6:
            continue
        aspects.append({"role": "quantity", "text": window})
        if sum(item["role"] == "quantity" for item in aspects) >= 2:
            break

    clause_count = sum(item["role"] == "clause" for item in aspects)
    if clause_count < 2:
        for query in _entity_queries(question, expansions):
            if any(jaccard(query, item["text"]) >= 0.72 for item in aspects):
                continue
            aspects.append({"role": "entity", "text": query})
            if sum(item["role"] == "entity" for item in aspects) >= 2:
                break

    deduped: list[dict] = []
    for item in aspects:
        if any(jaccard(item["text"], prev["text"]) >= 0.9 for prev in deduped):
            continue
        deduped.append(item)
    clauses_first = [item for item in deduped if item["role"] == "clause"]
    rest = [item for item in deduped if item["role"] != "clause"]
    chosen = (clauses_first + rest)[:_MAX_ASPECTS]
    for item in chosen:
        item["text"] = _expand_inplace(item["text"], expansions)
    return chosen


def _self_check() -> None:
    cases = [
        ("What equation uses k1, and how is k1 tied to diffusivity?", 2, "clause"),
        ("Which solvent did the screen identify as poorest?", 1, "clause"),
        ("Which authors assessed the functional performance, and what is PET?", 2, "clause"),
        ("Streams of PE, EVOH, and PET were recovered together.", 1, "clause"),
        (
            "During the pilot campaign that varied temperature residence time and antisolvent identity "
            "the reported recovery was 90% at 5 kg/d according to the mass balance on the toluene wash.",
            1,
            "quantity",
        ),
    ]
    for text, expect_clauses, _role in cases:
        aspects = decompose(text, {"PET": "polyethylene terephthalate"})
        got = sum(item["role"] == "clause" for item in aspects)
        if got != expect_clauses:
            raise SystemExit(f"clause_count {got} != {expect_clauses} for {text!r} :: {aspects}")
    buried = cases[-1][0]
    qty = decompose(buried, {})
    if not any(item["role"] == "quantity" and "kg/d" in item["text"] for item in qty):
        raise SystemExit(f"missing_quantity {qty}")
    print("aspect_self_check_ok", len(cases))


if __name__ == "__main__":
    _self_check()
