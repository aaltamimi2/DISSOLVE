"""Pinned input loader: rehash at load, fail closed."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from answer_eval.errors import InputDigestMismatch

INPUT_ROOT = Path("/home/aaltamimi2/dissolve-v12-audit")

PINS: dict[str, str] = {
    "RAG_AUDIT_SPEC.v2.4.3.md": "442496491132e9ddbd05ac4701bd7435bb39fc87fa5191d36a61fbfe02101de1",
    "RAG_AUDIT_SPEC.v2.4.2.md": "1eaf627eeb748c55a213aa9317038cd5c8dd0bfa31f8d1d94f72c521d8846fda",
    "RAG_AUDIT_SPEC.v2.4.1.md": "0bd69630b6a88bf6291e3f6afd81ab78b4edacb4b8f155f2615e123aea630ca2",
    "RAG_AUDIT_SPEC.v2.4.md": "73cbf00ffb0cd0ed80bb650f2bbc3307e0004a3da8630fe67b56e45efcd4ca0b",
    "RAG_AUDIT_SPEC.v2.3.3.md": "9a9b685bf6b4f9199d1abb06a7d333615bd729e89b47ad2be45f692dd759eb51",
    "RAG_AUDIT_SPEC.v2.3.2.md": "e9ab90588121aa510b7101af2979ae00b7ff9d1ecdddd34bc525e07bcc1719e0",
    "RAG_AUDIT_SPEC.v2.3.1.md": "f3ccde78498a1c280cb3e24cc47ffddab1f3a3455e1a7a9db2e5475d62bc3e64",
    "RAG_AUDIT_SPEC.v2.3.md": "3db53deb08bf45acd6012cae355214eecd9ca53878576ef3097cdf4137316f43",
    "fixtures/FIXTURES.eval.v9.json": "f5dbe2d0d5215ed88d067ab85664f1337ea3b5ff6cf25da425ff6ef5c09250c2",
    "fixtures/FIXTURES.eval.v8.json": "83b9243da3c4dd5dc641ba537f18b494e89f20091a501fc8e2ee038dd16cdb71",
    "fixtures/FIXTURES.eval.v7.json": "4e176af8520fd5df8bd43a4bfdbd4e34c7385c439f5b52ddbd3de174a101cd5a",
    "fixtures/FIXTURES.eval.v6.json": "1d8c6cb4ef502a48b06b9bbbe568ace1e3a5041c1abbab382ee58abc3f44d346",
    "fixtures/MATERIAL_ALIASES.fixture.v1.json": "0c5ed9da42790787c83c1efb8f8f62e4696c80c559abb4288c68bf7b616bec73",
    "fixtures/CASE_EXCLUSIONS.fixture.v1.json": "b298b8d53aec066dc36f6a237b2864c9a3db0cced47b194ba6ef540d13a0320f",
    "fixtures/FIXTURES.eval.v5.json": "7f2f073c130b7b15a068ad4a1029361a281f92d8f8f78f85a85eb54a16d968ec",
    "fixtures/FIXTURES.eval.v4.json": "a5f72e3378386894657571b9bca96a03544addd0fe273f0d541fb7ecdd85812a",
    "fixtures/FIXTURES.eval.v3.json": "0d59e8aab91af69ba66e22cbce3d777760ad9d733d1c01c3e3ac6a23c3beac7a",
    "fixtures/CONVERSIONS.v2.json": "7cfdd84c710ad9427ab3721f88a262742c1fa7955e5b7c9a2892e3217da3166f",
    "fixtures/QUANTITY_ALIASES.v1.json": "5a8f059fe0c48ec3a587a1464149c2593b38c99a4a4515204021f57f9289e29f",
    "fixtures/REFUSAL_PHRASES.v1.json": "b4696b82b2429c4a315727cc38729ec045607f2f57dd3aee43b6efb2ee18b1cd",
    "fixtures/EVAL_PUBLIC.v7.schema.json": "d4a42cd74b3330d301508e6dc9bd0f8d9feb17d9c4578e19b3611ff521f9f1b3",
    "fixtures/EVAL_PUBLIC.v6.schema.json": "ba6f5044ee2524c4d5afd497280930e2ab01f591733ab76afd2b1a0fb1459a23",
    "fixtures/EVAL_PUBLIC.v5.schema.json": "2fbe5416c4de2064da2ac6235c6df218063ddf51212c752ce74f38ca8c2c95d7",
    "fixtures/EVAL_PUBLIC.v4.schema.json": "feaef821fcc0dd390d0eb0b78b75f822f679fd217586b385b785840eaff21460",
    "fixtures/EVAL_PUBLIC.v3.schema.json": "b58e06e3bb31a31fd751ba5453fe023cb01157db0e1b952170240e4f5f1e59e0",
    "fixtures/EVAL_PUBLIC.v2.schema.json": "ae23cf9ca3765031ea42bae167684d109a6d9a8e304ebdaafba217a6d62d26ec",
    "fixtures/synthetic_guard.py": "8f434541004c67e61d79ec5356bd98adafae5e0949fc8f6747c55d7e1aa9f365",
    "instruments/gold_value_guard.py": "961ef0aeb8842d785e7806d8b57da7c0a62dcba393b282ec9490f04f578847a9",
}

PACKAGE_IDENTITY = "403857963c6b3909ae46034f80d87251bd4fc47d"
PRODUCT_COMMIT = "849ecdccb4d68204d0f68e56362adb90d4ef5296"
HANDOFF_SHA256 = "955b45ae9a57598afdff827c9e4b12aeaab6e0fc28913e9a829048e0d0d928b9"
DISPATCH_SHA256 = "f60bb84799446f13c73603de247466cd7897eb48c3ad97a396f8ad88f0911fe7"
PYTHON_EXECUTABLE_SHA256 = "97f591df758773004a502807c0453f716e496cb188ef74674b06719b90f465a4"
PRODUCTION_B = 2000
DEFAULT_SEED = 20260905


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_pinned(rel: str) -> bytes:
    expected = PINS[rel]
    path = INPUT_ROOT / rel
    data = path.read_bytes()
    digest = sha256_bytes(data)
    if digest != expected:
        raise InputDigestMismatch(f"{rel}")
    return data


def load_json(rel: str) -> Any:
    return json.loads(load_pinned(rel).decode("utf-8"))


def verify_all_pins() -> dict[str, str]:
    out: dict[str, str] = {}
    for rel, expected in PINS.items():
        digest = sha256_bytes((INPUT_ROOT / rel).read_bytes())
        if digest != expected:
            raise InputDigestMismatch(rel)
        out[rel] = digest
    return out
