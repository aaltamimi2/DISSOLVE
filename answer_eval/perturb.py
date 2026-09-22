"""§8.5 perturbation partition."""

from __future__ import annotations

from typing import Any

from answer_eval.canon import nfkc
from answer_eval.tables import load_json


def _exclusions(tables: dict[str, Any] | None = None) -> set[str]:
    if tables and "exclusions" in tables:
        return set(tables["exclusions"])
    data = load_json("fixtures/CASE_EXCLUSIONS.fixture.v1.json")
    return set(data["tokens"])


def _aliases(tables: dict[str, Any] | None = None, override: dict | None = None) -> dict[str, list[str]]:
    if override:
        return {k: list(v) for k, v in override.items()}
    if tables and "aliases" in tables:
        return tables["aliases"]
    data = load_json("fixtures/MATERIAL_ALIASES.fixture.v1.json")
    return {k: list(v) for k, v in data["aliases"].items()}


def perturb(text: str, mentions: list[str], transform: str, tables: dict[str, Any] | None = None) -> dict[str, Any]:
    exclusions = _exclusions(tables)
    aliases = _aliases(tables, (tables or {}).get("alias_table_override") if tables else None)
    if tables and isinstance(tables.get("alias_table_override"), dict):
        aliases = _aliases(None, tables["alias_table_override"])

    eligible: list[str] = []
    ineligible: list[str] = []
    for mention in mentions:
        if transform == "T1":
            if mention in exclusions:
                ineligible.append(mention)
            else:
                eligible.append(mention)
        elif transform == "T2":
            if mention in aliases and aliases[mention]:
                eligible.append(mention)
            else:
                ineligible.append(mention)
        else:
            ineligible.append(mention)

    if not eligible:
        reason = "all_excluded" if transform == "T1" else "no_alias"
        return {
            "not_applicable": 1,
            "not_applicable_reason": reason,
            "applicable": 0,
            "unchanged": 0,
            "invalid": 0,
            "in_denominator": False,
            "skipped": True,
        }

    new = text
    invalid_reason = None
    if transform == "T1":
        for mention in eligible:
            new = new.replace(mention, mention.casefold())
        extra = [tok for tok in exclusions if tok in new and tok not in text]
        if extra:
            invalid_reason = "introduced_excluded_token"
    else:
        for mention in eligible:
            repl = aliases[mention][0]
            if repl in exclusions:
                invalid_reason = "replacement_is_excluded_token"
                break
            others = [m for m in mentions if m != mention]
            if repl in others or (repl in text and repl != mention):
                invalid_reason = "replacement_equals_existing_mention"
                break
            new = new.replace(mention, repl)

    if invalid_reason:
        return {
            "applicable": 1,
            "unchanged": 0,
            "invalid": 1,
            "invalid_reason": invalid_reason,
            "in_denominator": False,
            "transformed_text": text,
        }

    unchanged = int(new == text)
    excluded_kept = [m for m in mentions if m in exclusions]
    out = {
        "applicable": 1,
        "unchanged": unchanged,
        "invalid": 0,
        "transformed_text": new,
        "in_denominator": True,
        "excluded_tokens_kept": excluded_kept,
    }
    if unchanged:
        out.pop("transformed_text", None)
        out["transformed_text"] = new
    return out
