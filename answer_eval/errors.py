"""Evaluator halt codes. Never serialize as public status."""

from __future__ import annotations


class EvalHalt(Exception):
    def __init__(self, code: str, detail: str = "", payload: dict | None = None) -> None:
        self.code = code
        self.detail = detail
        self.payload = payload or {}
        self.payload = payload or {}
        super().__init__(code if not detail else f"{code}: {detail}")


class CanonicalizationCollision(EvalHalt):
    def __init__(self, detail: str = "") -> None:
        super().__init__("canonicalization_collision", detail)


class IdCollision(EvalHalt):
    def __init__(self, detail: str = "", payload: dict | None = None) -> None:
        super().__init__("id_collision", detail, payload)


class ReferenceDuplicateAtom(EvalHalt):
    def __init__(self, detail: str = "") -> None:
        super().__init__("reference_duplicate_atom", detail)


class ReferenceAssignmentAmbiguous(EvalHalt):
    def __init__(self, detail: str = "") -> None:
        super().__init__("reference_assignment_ambiguous", detail)


class ConversionInexact(EvalHalt):
    def __init__(self, detail: str = "") -> None:
        super().__init__("conversion_inexact", detail)


class StubVisibilityConflict(EvalHalt):
    def __init__(self, detail: str = "") -> None:
        super().__init__("stub_visibility_conflict", detail)


class InvariantViolation(EvalHalt):
    def __init__(self, detail: str = "") -> None:
        super().__init__("invariant_violation", detail)


class InputDigestMismatch(EvalHalt):
    def __init__(self, detail: str = "") -> None:
        super().__init__("input_digest_mismatch", detail)
