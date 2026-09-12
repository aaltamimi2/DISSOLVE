"""Synthetic two-arm answer adapter. No product imports, no network, no keys."""

from answer_adapter.config import config_echo
from answer_adapter.draft import check_draft
from answer_adapter.ledger import new_ledger, normalize_draft, present_result
from answer_adapter.offer import build_offer
from answer_adapter.prompt import load_prompt
from answer_adapter.run import run_question
from answer_adapter.substrate import resolve_substrate

__all__ = [
    "build_offer",
    "load_prompt",
    "check_draft",
    "new_ledger",
    "present_result",
    "normalize_draft",
    "run_question",
    "config_echo",
    "resolve_substrate",
]
