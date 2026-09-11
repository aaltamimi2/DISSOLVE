"""§3.2.0(a) identity recipes."""

from __future__ import annotations

import hashlib
from typing import Any

from answer_eval.canon import canonical_dumps
from answer_eval.errors import IdCollision

PRODUCTION_HEX_WIDTH = 16


def _sha(text: str, width: int) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:width]


def observation_id(
    question_id: str,
    study_family_id: str,
    source_role: str,
    comparison_key: Any,
    occurrence_index: int,
    id_hex_width: int = PRODUCTION_HEX_WIDTH,
) -> str:
    payload = (
        f"{question_id}|{study_family_id}|{source_role}|"
        f"{canonical_dumps(comparison_key)}|{occurrence_index}"
    )
    return _sha(payload, id_hex_width)


def atom_id(
    question_id: str,
    observation_id_value: str,
    field_path: str,
    id_hex_width: int = PRODUCTION_HEX_WIDTH,
) -> str:
    return _sha(f"{question_id}|{observation_id_value}|{field_path}", id_hex_width)


def slot_id(
    question_id: str,
    envelope_path: str,
    id_hex_width: int = PRODUCTION_HEX_WIDTH,
) -> str:
    return _sha(f"{question_id}|{envelope_path}", id_hex_width)


def assert_unique_ids(pairs: list[tuple[str, str, str | None]], code: str = "id_collision") -> None:
    """Halt if two distinct canonical strings share an id. pairs: (id, canon, path)."""
    by_id: dict[str, tuple[str, str | None]] = {}
    for item in pairs:
        ident, canon = item[0], item[1]
        path = item[2] if len(item) > 2 else None
        prior = by_id.get(ident)
        if prior is not None and prior[0] != canon:
            raise IdCollision(
                ident,
                payload={
                    "colliding_width1_id": ident,
                    "colliding_paths": [prior[1], path],
                    "id_hex_width": None,
                },
            )
        by_id[ident] = (canon, path)
