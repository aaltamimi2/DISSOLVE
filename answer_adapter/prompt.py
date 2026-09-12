"""Prompt loader with sha256 pins."""

from __future__ import annotations

import hashlib
from pathlib import Path

from answer_adapter.constants import PROMPTS_DIR, PROMPT_SHA256
from answer_adapter.halt import AdapterHalt


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def packaged_prompt_text(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.txt"
    return Path(path).read_text(encoding="utf-8")


def load_prompt(name: str, text: str) -> dict:
    observed = sha256_text(text)
    pinned = PROMPT_SHA256.get(name)
    if pinned is None or observed != pinned:
        raise AdapterHalt("prompt_hash_mismatch", observed_sha256=observed)
    return {"accepted": True, "sha256": observed}


def load_packaged_prompt(name: str) -> dict:
    return load_prompt(name, packaged_prompt_text(name))
