"""S0F path rules. Constructs no substrate."""

from __future__ import annotations

import os
from pathlib import Path

from answer_adapter.constants import (
    AUTHORIZED_SUBSTRATE_ID,
    INDEX_FILENAME,
    KNOWLEDGEBASE,
    SHARED_CORPUS_ROOT,
)
from answer_adapter.halt import AdapterHalt


def _is_under(path: str, root: str) -> bool:
    resolved = os.path.realpath(os.path.abspath(path))
    root_resolved = os.path.realpath(os.path.abspath(os.path.expanduser(root)))
    return resolved == root_resolved or resolved.startswith(root_resolved + os.sep)


def resolve_substrate(substrate_id: str, research_home: object) -> dict:
    if research_home is None or research_home == "":
        raise AdapterHalt("research_home_unset")
    home = str(research_home)
    if _is_under(home, SHARED_CORPUS_ROOT) or _is_under(home, "~/dissolve-v12-audit/corpus"):
        raise AdapterHalt("shared_corpus_path")
    if substrate_id != AUTHORIZED_SUBSTRATE_ID:
        raise AdapterHalt("substrate_not_authorized")
    index_path = str(Path(home) / INDEX_FILENAME)
    return {
        "ok": True,
        "index_path": index_path,
        "env": {"DISSOLVE_RESEARCH_HOME": home},
        "knowledgebase": KNOWLEDGEBASE,
    }
