#!/usr/bin/env python3
"""Synthetic stand-in for instruments/gold_value_guard.py — for builder-side tests ONLY.

Same CLI, same exit codes, same matching rules (NFKC + casefold + whitespace collapse;
exact reference >= 3 chars; substring reference >= 8 chars; 25-char sliding window),
but the protected reference is the SYNTHETIC needle list below, never the real gold.
The real guard loads its protected reference internally and is run only by the
acceptance custodian (HANDOFF.wp1.v2 section 4). This file reads nothing but its argument.

Exit codes: 0 = clean, 2 = violations, 3 = load error (wrong arg count or unreadable JSON).
"""
from __future__ import annotations
import json, sys, unicodedata
from pathlib import Path
from typing import Any, Iterable

WINDOW = 25
MIN_SUBSTRING_REFERENCE = 8
MIN_EXACT_REFERENCE = 3

# Synthetic protected reference: invented needles, queries and quotes. None is real.
SYNTHETIC_REFERENCE = {
    "needles": ["SYNTHETIC-PRIVATE-MARKER-7f3a", "9917.4 zorks at 613 K", "polymer_Q"],
    "queries": ["zzq synthetic query alpha beta gamma delta epsilon"],
    "quotes": ["zzq synthetic quote: polymer_Q reached 9917.4 zorks at 613 K in the invented run"],
}


def normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(folded.split()).strip()


def _candidate_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _candidate_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _candidate_strings(item)


class SyntheticCorpus:
    def __init__(self, ref: dict[str, list[str]]) -> None:
        refs = {normalize(s) for group in ref.values() for s in group if normalize(s)}
        self.substring_refs = tuple(sorted((r for r in refs if len(r) >= MIN_SUBSTRING_REFERENCE),
                                           key=lambda r: (-len(r), r)))
        self.exact_refs = frozenset(r for r in refs if len(r) >= MIN_EXACT_REFERENCE)
        self.windows = frozenset(r[i:i + WINDOW] for r in refs if len(r) >= WINDOW
                                 for i in range(len(r) - WINDOW + 1))

    def violates(self, candidate: str) -> bool:
        n = normalize(candidate)
        if not n:
            return False
        if n in self.exact_refs or any(r in n for r in self.substring_refs):
            return True
        return len(n) >= WINDOW and any(n[i:i + WINDOW] in self.windows
                                        for i in range(len(n) - WINDOW + 1))

    def count(self, candidate: Any) -> int:
        return sum(self.violates(s) for s in _candidate_strings(candidate))


def count_violations(candidate: Any) -> int:
    return SyntheticCorpus(SYNTHETIC_REFERENCE).count(candidate)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("violations=1")
        return 3
    try:
        candidate = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    except Exception:
        print("violations=1")
        return 3
    count = count_violations(candidate)
    print(f"violations={count}")
    return 0 if count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
