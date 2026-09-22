"""Pinned synthetic-adapter constants. Values are the admitted pins, not lookups."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = PACKAGE_ROOT / "prompts"

ADAPTER_VERSION = "answer_adapter.v2"
ENVELOPE_VERSION = "AdapterEnvelope.v1"
LEDGER_SCHEMA = "RunLedger.v1"
PRESENTATION_ID = "ledger_relabel.v1"
DEFAULT_MAX_TOOL_ROUNDS = 8
CLOSED_BOOK_SENTINEL = "none"
KNOWLEDGEBASE = "t5-indexed-unsealed"
INDEX_FILENAME = "t5-indexed-unsealed.json.gz"
SHARED_CORPUS_ROOT = "/home/aaltamimi2/dissolve-v12-audit/corpus"
AUTHORIZED_SUBSTRATE_ID = "S0F"

API_KEY_ENV_NAME = "META_MUSE_API_KEY"
PINNED_MODEL_ID = "openai:muse-spark-1.2"
PINNED_CENSUS_VERSION = "CENSUS.v3"
PINNED_STORE_DIGEST = "91851dbf3b979450d087f86acca8c146205e0d0a8c41cc49c3aab1fc9cf73a40"

REGISTRY_ROSTER = (
    "analyze_numeric_samples",
    "compare_contaminant_removal_modes",
    "compare_solvent_safety_at_conditions",
    "evaluate_process",
    "fetch_solvent_safety_by_cid",
    "get_solvent_safety_card",
    "ingest_literature_documents",
    "ingest_literature_graph",
    "inspect_literature_corpus",
    "list_thermal_evidence",
    "lookup_glass_transition",
    "lookup_hansen_parameters",
    "lookup_material_database_membership",
    "plan_multistage_separation",
    "rank_landscape",
    "resolve_polymer_data_scope",
    "screen_contaminant_leaching",
    "screen_contaminant_strap_removal",
    "screen_cool_then_reheat_getter",
    "screen_green_solvent_candidates",
    "screen_hansen_compatibility",
    "screen_pairwise_solubility_overlap",
    "screen_polymer_separation",
    "screen_precipitation_order",
    "screen_route_solvent_substitutions",
    "search_literature_corpus",
    "search_patent_literature",
    "search_scholarly_literature",
    "solubility_query",
)

LITERATURE_CORPUS_TOOLS = (
    "inspect_literature_corpus",
    "search_literature_corpus",
)

ARMS = {
    "rag_alone": {
        "prompt": "PROMPT.rag_alone.v1",
        "envelope_profile": "AnswerEnvelope.v1",
        "offered": list(LITERATURE_CORPUS_TOOLS),
    },
    "closed_book": {
        "prompt": "PROMPT.closed_book.v1",
        "envelope_profile": "ENVELOPE.closed_book.v1",
        "offered": [],
    },
}

DEFERRED_ARMS = ("chemistry_only", "integration_full_agent")

PROMPT_SHA256 = {
    "PROMPT.rag_alone.v1": "6f97cc6a148d6b0d706bd318d93771ce2df631179b92a4e3b4b6847dc33f85b0",
    "PROMPT.closed_book.v1": "e50416decacb122937e640b4e79c1888f8c47992e92547883b1d412c381c9529",
}

DRAFT_TOP_LEVEL_KEYS = ("claims", "limitations", "status", "unanswered_subparts")
DRAFT_STATUS = (
    "ambiguous_request",
    "complete",
    "insufficient_evidence",
    "partial",
)
UNANSWERED_REASONS = (
    "budget_exhausted",
    "conflicting_evidence",
    "no_evidence_in_scope",
    "parser_limited",
    "tool_error",
)
STAMP_KEYS = (
    "corpus_snapshot",
    "envelope_profile",
    "envelope_version",
    "generated_at",
    "request_id",
    "request_text_hash",
    "resolved_scope",
)
CONFIG_ECHO_KEYS = (
    "adapter_version",
    "api_base",
    "api_key_env",
    "arm",
    "census_version",
    "decoding",
    "envelope_profile",
    "max_tool_rounds",
    "model_alias",
    "model_id",
    "offered_tools",
    "presentation",
    "prompt_name",
    "prompt_sha256",
    "research_home",
    "store_digest",
    "substrate_id",
    "substrate_index_sha256",
    "substrate_manifest_sha256",
    "tool_config_hash",
)
CONFIG_ECHO_FROM_CONFIG = (
    "adapter_version",
    "api_base",
    "api_key_env",
    "census_version",
    "decoding",
    "max_tool_rounds",
    "model_alias",
    "model_id",
    "presentation",
    "research_home",
    "store_digest",
    "substrate_id",
    "substrate_index_sha256",
    "substrate_manifest_sha256",
)
SERVED_FIELDS = (
    "paper_sha256",
    "page",
    "char_start",
    "char_end",
    "section",
    "section_origin",
)
PRESENTED_ROW_FIELDS = (
    "citation_id",
    "title",
    "year",
    "doi",
    "page",
    "section",
    "excerpt",
)
KEY_MATERIAL_FIELDS = ("api_key",)
