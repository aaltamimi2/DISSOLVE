"""Planted-defect demonstrations against exported adapter paths."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from pathlib import Path

import answer_adapter.config as config_mod
import answer_adapter.draft as draft_mod
import answer_adapter.ledger as ledger_mod
import answer_adapter.offer as offer_mod
import answer_adapter.prompt as prompt_mod
import answer_adapter.run as run_mod
import answer_adapter.substrate as substrate_mod
from answer_adapter.constants import ARMS, REGISTRY_ROSTER
from answer_adapter.halt import AdapterHalt

HERE = Path(__file__).resolve().parent


def _load_harness():
    import importlib.util

    spec = importlib.util.spec_from_file_location("aa_harness", HERE / "harness.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_harness = _load_harness()
activating_value = _harness.activating_value
load_fixture_file = _harness.load_fixture_file
run_fixture = _harness.run_fixture
values_equal = _harness.values_equal


def _fixture_by_id(bundle, fixture_id: str) -> dict:
    return next(f for f in bundle["fixtures"] if f["fixture_id"] == fixture_id)


@contextmanager
def _patch(module, name: str, replacement):
    original = getattr(module, name)
    setattr(module, name, replacement)
    try:
        yield original
    finally:
        setattr(module, name, original)


def _failing(fixture, key: str, actual) -> bool:
    expected = fixture["expected"]
    if key in expected:
        return not values_equal(actual.get(key) if "[" not in key else activating_value(actual, key), expected[key] if "[" not in key else activating_value(fixture["expected"], key) if key in fixture["expected"] else activating_value({"envelope": fixture["expected"].get("envelope"), "ledger_after": fixture["expected"].get("ledger_after"), "presented_tool_messages": fixture["expected"].get("presented_tool_messages")}, key) if False else activating_value(fixture["expected"] if key in fixture["expected"] else actual, key))
    got = activating_value(actual, key)
    want = activating_value(fixture["expected"], key)
    return not values_equal(got, want)


def key_failed(fixture, key: str, actual) -> bool:
    want = activating_value(fixture["expected"], key)
    got = activating_value(actual, key)
    return not values_equal(got, want)


def demo_row_1(bundle):
    fx = _fixture_by_id(bundle, "F-OFFER-1")

    def mutant(by_name, arm):
        out = offer_mod.build_offer.__wrapped__(by_name, arm) if False else None
        real = offer_mod.build_offer
        # wrap after patch target: call stored original via closure after patch
        raise RuntimeError("replaced")

    original = offer_mod.build_offer

    def mutant_offer(by_name, arm):
        out = original(by_name, arm)
        if arm == "rag_alone":
            out = dict(out)
            out["offered"] = list(out["offered"]) + ["solubility_query"]
        return out

    with _patch(offer_mod, "build_offer", mutant_offer), _patch(run_mod.offer_mod, "build_offer", mutant_offer):
        actual = run_fixture(fx, {"build_offer": mutant_offer})
    return [("F-OFFER-1", "offered", key_failed(fx, "offered", actual))]


def _wrap_offer(fn):
    @contextmanager
    def ctx():
        with _patch(offer_mod, "build_offer", fn):
            yield

    return ctx


def demonstrate_all(bundle) -> list[dict]:
    original_offer = offer_mod.build_offer
    original_load = prompt_mod.load_prompt
    original_echo = config_mod.config_echo
    original_resolve = substrate_mod.resolve_substrate
    original_check = draft_mod.check_draft
    original_present = ledger_mod.present_result
    original_normalize = ledger_mod.normalize_draft
    original_passage = ledger_mod.passage_rows
    original_stamp = config_mod.stamp_values
    original_unanswered = draft_mod.valid_unanswered_entries
    original_unanswered_errors = draft_mod.unanswered_errors
    original_assemble = run_mod._assemble_envelope
    original_ref = ledger_mod.normalize_reference
    original_adapter_ref = ledger_mod.adapter_ref_from_entry

    rows = []

    def add(row, fixture_id, key, actual, fx):
        rows.append(
            {
                "row": row,
                "fixture_id": fixture_id,
                "key": key,
                "failed": key_failed(fx, key, actual),
            }
        )

    # 1
    def offer_chem(by_name, arm):
        out = original_offer(by_name, arm)
        if arm == "rag_alone":
            out = dict(out)
            out["offered"] = list(out["offered"]) + ["solubility_query"]
        return out

    fx = _fixture_by_id(bundle, "F-OFFER-1")
    add(1, "F-OFFER-1", "offered", run_fixture(fx, {"build_offer": offer_chem}), fx)

    # 2
    def offer_closed(by_name, arm):
        out = original_offer(by_name, arm)
        if arm == "closed_book":
            out = dict(out)
            out["offered"] = ["search_literature_corpus"]
        return out

    fx = _fixture_by_id(bundle, "F-OFFER-2")
    add(2, "F-OFFER-2", "offered", run_fixture(fx, {"build_offer": offer_closed}), fx)
    fx = _fixture_by_id(bundle, "F-RUN-3")
    with _wrap_offer(offer_closed)():
        add(2, "F-RUN-3", "tools_passed_to_model", run_fixture(fx), fx)

    # 3
    def load_accept(name, text):
        try:
            return original_load(name, text)
        except AdapterHalt:
            return {"accepted": True, "sha256": prompt_mod.sha256_text(text)}

    fx = _fixture_by_id(bundle, "F-PROMPT-3")
    add(3, "F-PROMPT-3", "halt", run_fixture(fx, {"load_prompt": load_accept}), fx)

    # 4
    def echo_skip_key(arm, config):
        cfg = {k: v for k, v in config.items() if k != "api_key"}
        return original_echo(arm, cfg)

    fx = _fixture_by_id(bundle, "F-CFG-3")
    add(4, "F-CFG-3", "halt", run_fixture(fx, {"config_echo": echo_skip_key}), fx)

    # 5
    def resolve_open(substrate_id, research_home):
        try:
            return original_resolve(substrate_id, research_home)
        except AdapterHalt as exc:
            if exc.halt in {"shared_corpus_path", "research_home_unset"}:
                return {"ok": True, "index_path": str(research_home or "") + "/t5-indexed-unsealed.json.gz", "env": {}, "knowledgebase": "t5-indexed-unsealed"}
            raise

    fx = _fixture_by_id(bundle, "F-SUB-1")
    add(5, "F-SUB-1", "halt", run_fixture(fx, {"resolve_substrate": resolve_open}), fx)
    fx = _fixture_by_id(bundle, "F-SUB-2")
    add(5, "F-SUB-2", "halt", run_fixture(fx, {"resolve_substrate": resolve_open}), fx)

    # 6
    def offer_ignore_drift(by_name, arm):
        try:
            return original_offer(by_name, arm)
        except AdapterHalt as exc:
            if exc.halt == "registry_drift":
                return {"offered": list(ARMS["rag_alone"]["offered"]), "result_read_offered": False, "tool_config_hash": "x"}
            raise

    fx = _fixture_by_id(bundle, "F-OFFER-3")
    add(6, "F-OFFER-3", "halt", run_fixture(fx, {"build_offer": offer_ignore_drift}), fx)
    add(6, "F-OFFER-3", "extra", run_fixture(fx, {"build_offer": offer_ignore_drift}), fx)

    # 7
    def offer_all(by_name, arm):
        out = original_offer(by_name, arm) if arm != "closed_book" else original_offer(by_name, "rag_alone")
        out = dict(out)
        out["offered"] = list(REGISTRY_ROSTER) + ["result_read"]
        return out

    fx = _fixture_by_id(bundle, "F-RUN-2")
    with _wrap_offer(offer_all)():
        add(7, "F-RUN-2", "executor_calls", run_fixture(fx), fx)
        add(7, "F-RUN-2", "refused_tool_calls", run_fixture(fx), fx)
    fx = _fixture_by_id(bundle, "F-RUN-4")
    with _wrap_offer(offer_all)():
        add(7, "F-RUN-4", "executor_calls", run_fixture(fx), fx)
        add(7, "F-RUN-4", "refused_tool_calls", run_fixture(fx), fx)

    # 8
    def echo_omit_sub(arm, config):
        echo = original_echo(arm, config)
        echo = dict(echo)
        echo.pop("substrate_id", None)
        return echo

    fx = _fixture_by_id(bundle, "F-CFG-2")

    def echo_fill_missing(arm, config):
        cfg = dict(config)
        cfg.setdefault("substrate_id", "S0F")
        echo = original_echo(arm, cfg)
        echo = dict(echo)
        echo.pop("substrate_id", None)
        return echo

    add(8, "F-CFG-2", "halt", run_fixture(fx, {"config_echo": echo_fill_missing}), fx)
    add(8, "F-CFG-2", "missing", run_fixture(fx, {"config_echo": echo_fill_missing}), fx)

    # 9
    def check_skip_empty(draft, profile):
        out = original_check(draft, profile)
        out = dict(out)
        out["claim_errors"] = {}
        out["draft_structure_valid"] = False if profile == "AnswerEnvelope.v1" and any(
            (isinstance(c, dict) and not c.get("evidence_refs")) for c in (draft.get("claims") or [])
        ) else out["draft_structure_valid"]
        return out

    fx = _fixture_by_id(bundle, "F-ENV-2")
    add(9, "F-ENV-2", "claim_errors", run_fixture(fx, {"check_draft": check_skip_empty}), fx)
    add(9, "F-ENV-2", "draft_structure_valid", run_fixture(fx, {"check_draft": check_skip_empty}), fx)

    # 10
    def check_skip_cite(draft, profile):
        out = original_check(draft, profile)
        out = dict(out)
        out["claim_flags"] = {}
        return out

    fx = _fixture_by_id(bundle, "F-ENV-4")
    add(10, "F-ENV-4", "claim_flags", run_fixture(fx, {"check_draft": check_skip_cite}), fx)

    # 11
    def max_rounds_open(config):
        return 99

    fx = _fixture_by_id(bundle, "F-RUN-5")
    with _patch(run_mod, "_max_rounds", max_rounds_open):
        add(11, "F-RUN-5", "executor_calls", run_fixture(fx), fx)
        add(11, "F-RUN-5", "adapter_error", run_fixture(fx), fx)

    # 12
    def resolve_union(substrate_id, research_home):
        if substrate_id == "R01-union":
            return original_resolve("S0F", research_home)
        return original_resolve(substrate_id, research_home)

    fx = _fixture_by_id(bundle, "F-SUB-3")
    add(12, "F-SUB-3", "halt", run_fixture(fx, {"resolve_substrate": resolve_union}), fx)

    # 13
    def offer_deferred(by_name, arm):
        if arm in {"chemistry_only", "integration_full_agent"}:
            return original_offer(by_name, "rag_alone")
        return original_offer(by_name, arm)

    fx = _fixture_by_id(bundle, "F-OFFER-5")
    add(13, "F-OFFER-5", "halt", run_fixture(fx, {"build_offer": offer_deferred}), fx)

    # 14
    def assemble_draft(stamps, normalized, status, draft=None):
        env = original_assemble(stamps, normalized, status)
        if isinstance(draft, dict):
            for k in ("request_id", "generated_at", "corpus_snapshot"):
                if k in draft:
                    env[k] = draft[k]
        return env

    fx = _fixture_by_id(bundle, "F-STAMP-3")

    def run_with_draft_stamps(*args, **kwargs):
        def assemble(stamps, normalized, status):
            env = original_assemble(stamps, normalized, status)
            draft = args[0] if False else None
            return env

        # patch run_question path: merge discarded keys back
        def mutant_run(question, arm, model, executor, config, clock, run_ordinal):
            out = run_mod.run_question(question, arm, model, executor, config, clock, run_ordinal)
            if isinstance(out.get("envelope"), dict) and "discarded_draft_keys" in out:
                env = dict(out["envelope"])
                env["request_id"] = "from-draft"
                env["generated_at"] = "from-draft"
                env["corpus_snapshot"] = {"from": "draft"}
                out = dict(out)
                out["envelope"] = env
                out["discarded_draft_keys"] = []
            return out

        return mutant_run(*args, **kwargs)

    # Row 14 must change the implementation path, not post-edit. Patch assemble + keep discarded empty in normalize.
    def normalize_keep(draft, ledger, arm, substrate_manifest_sha256):
        out = original_normalize(draft, ledger, arm, substrate_manifest_sha256)
        out = dict(out)
        out["discarded_draft_keys"] = []
        return out

    def assemble_from_normalized(stamps, normalized, status):
        env = original_assemble(stamps, normalized, status)
        # pull through any draft-supplied stamps that normalize failed to drop
        return env

    with _patch(ledger_mod, "normalize_draft", normalize_keep), _patch(run_mod.ledger_mod, "normalize_draft", normalize_keep):
        def assemble_prefers_draft_fields(stamps, normalized, status):
            env = original_assemble(stamps, normalized, status)
            env = dict(env)
            env["request_id"] = "from-draft"
            env["generated_at"] = "from-draft"
            env["corpus_snapshot"] = {"from": "draft"}
            return env

        with _patch(run_mod, "_assemble_envelope", assemble_prefers_draft_fields):
            fx = _fixture_by_id(bundle, "F-STAMP-3")
            actual = run_fixture(fx)
            add(14, "F-STAMP-3", "envelope.request_id", actual, fx)
            add(14, "F-STAMP-3", "envelope.corpus_snapshot", actual, fx)
            add(14, "F-STAMP-3", "envelope.generated_at", actual, fx)
            add(14, "F-STAMP-3", "discarded_draft_keys", actual, fx)

    # 15
    def stamp_accept(arm, question, config, clock, run_ordinal, echo):
        cfg = dict(config)
        cfg["store_digest"] = "91851dbf3b979450d087f86acca8c146205e0d0a8c41cc49c3aab1fc9cf73a40"
        return original_stamp(arm, question, cfg, clock, run_ordinal, echo)

    fx = _fixture_by_id(bundle, "F-STAMP-1")
    with _patch(config_mod, "stamp_values", stamp_accept), _patch(run_mod.config_mod, "stamp_values", stamp_accept):
        add(15, "F-STAMP-1", "halt", run_fixture(fx), fx)
        add(15, "F-STAMP-1", "field", run_fixture(fx), fx)

    # 16
    def stamp_default(arm, question, config, clock, run_ordinal, echo):
        return original_stamp(arm, question, config, clock or "1970-01-01T00:00:00Z", run_ordinal, echo)

    fx = _fixture_by_id(bundle, "F-STAMP-2")
    with _patch(config_mod, "stamp_values", stamp_default), _patch(run_mod.config_mod, "stamp_values", stamp_default):
        add(16, "F-STAMP-2", "halt", run_fixture(fx), fx)
        add(16, "F-STAMP-2", "field", run_fixture(fx), fx)

    # 17
    def unanswered_repair(draft):
        entries = []
        for entry in draft.get("unanswered_subparts") or []:
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            if "subpart_id" not in item and "subpart" in item:
                item["subpart_id"] = item["subpart"]
            entries.append(item)
        repaired = dict(draft)
        repaired["unanswered_subparts"] = entries
        return original_unanswered(repaired)

    def unanswered_errors_repair(draft):
        return []

    fx = _fixture_by_id(bundle, "F-DRAFT-1")
    with _patch(draft_mod, "valid_unanswered_entries", unanswered_repair), _patch(
        ledger_mod, "valid_unanswered_entries", unanswered_repair
    ), _patch(draft_mod, "unanswered_errors", unanswered_errors_repair), _patch(
        ledger_mod, "unanswered_errors", unanswered_errors_repair
    ):
        add(17, "F-DRAFT-1", "draft_errors", run_fixture(fx), fx)
        add(17, "F-DRAFT-1", "envelope.unanswered_subparts", run_fixture(fx), fx)

    # 18
    def unanswered_any(draft):
        kept = []
        for entry in draft.get("unanswered_subparts") or []:
            if isinstance(entry, dict) and "subpart_id" in entry and "reason" in entry:
                item = {"subpart_id": entry["subpart_id"], "reason": entry["reason"]}
                kept.append(item)
        return kept

    fx = _fixture_by_id(bundle, "F-DRAFT-2")
    with _patch(draft_mod, "valid_unanswered_entries", unanswered_any), _patch(
        ledger_mod, "valid_unanswered_entries", unanswered_any
    ), _patch(draft_mod, "unanswered_errors", unanswered_errors_repair), _patch(
        ledger_mod, "unanswered_errors", unanswered_errors_repair
    ):
        add(18, "F-DRAFT-2", "draft_errors", run_fixture(fx), fx)
        add(18, "F-DRAFT-2", "envelope.unanswered_subparts", run_fixture(fx), fx)

    # 19
    def normalize_drop_unknown(ref, arm, entries, substrate_manifest_sha256):
        out = original_ref(ref, arm, entries, substrate_manifest_sha256)
        if isinstance(out, dict) and out.get("reason") == "unknown_token":
            return None
        return out

    def normalize_draft_drop(draft, ledger, arm, substrate_manifest_sha256):
        out = original_normalize(draft, ledger, arm, substrate_manifest_sha256)
        out = dict(out)
        claims = []
        for claim in out.get("claims") or []:
            if not isinstance(claim, dict):
                claims.append(claim)
                continue
            c = dict(claim)
            refs = [r for r in (c.get("evidence_refs") or []) if not (isinstance(r, dict) and r.get("reason") == "unknown_token")]
            c["evidence_refs"] = refs
            claims.append(c)
        out["claims"] = claims
        return out

    fx = _fixture_by_id(bundle, "F-NORM-1")
    with _patch(ledger_mod, "normalize_draft", normalize_draft_drop), _patch(run_mod.ledger_mod, "normalize_draft", normalize_draft_drop):
        add(19, "F-NORM-1", "envelope.claims[0].evidence_refs", run_fixture(fx), fx)

    # 20
    def normalize_repair(draft, ledger, arm, substrate_manifest_sha256):
        out = original_normalize(draft, ledger, arm, substrate_manifest_sha256)
        out = dict(out)
        claims = []
        for claim in out.get("claims") or []:
            if not isinstance(claim, dict):
                claims.append(claim)
                continue
            c = dict(claim)
            refs = []
            for r in c.get("evidence_refs") or []:
                if isinstance(r, dict) and r.get("reason") == "malformed_ref":
                    continue
                refs.append(r)
            # drop neighbours too
            c["evidence_refs"] = []
            claims.append(c)
        out["claims"] = claims
        return out

    fx = _fixture_by_id(bundle, "F-NORM-2")
    with _patch(ledger_mod, "normalize_draft", normalize_repair), _patch(run_mod.ledger_mod, "normalize_draft", normalize_repair):
        add(20, "F-NORM-2", "envelope.claims[0].evidence_refs", run_fixture(fx), fx)

    # 21
    def present_c_labels(result, ledger, call):
        rows = ledger_mod.passage_rows(result if isinstance(result, dict) else {})
        out = original_present(result, ledger, call)
        if not rows:
            return out
        ledger2 = copy.deepcopy(out["ledger"])
        for i, entry in enumerate(ledger2["entries"]):
            entry["evidence_token"] = f"C{(i % 1) + 1}"
        msg = copy.deepcopy(out["message"])
        if isinstance(msg, dict) and isinstance(msg.get("results"), list):
            for row in msg["results"]:
                row["citation_id"] = "C1"
        return {"message": msg, "ledger": ledger2}

    fx = _fixture_by_id(bundle, "F-NORM-3")
    with _patch(ledger_mod, "present_result", present_c_labels), _patch(run_mod.ledger_mod, "present_result", present_c_labels):
        add(21, "F-NORM-3", "presented_tool_messages", run_fixture(fx), fx)
        add(21, "F-NORM-3", "envelope.claims[0].evidence_refs", run_fixture(fx), fx)

    # 22
    def normalize_closed_as_rag(draft, ledger, arm, substrate_manifest_sha256):
        fake = {
            "schema": "RunLedger.v1",
            "executed_calls": 1,
            "entries": [
                {
                    "evidence_token": "E1",
                    "tool": "search_literature_corpus",
                    "call_ordinal": 1,
                    "row_ordinal": 1,
                    "retrieval_unit_id": "invented-chunk",
                    "served": {
                        "paper_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "page": 1,
                        "char_start": 0,
                        "char_end": 1,
                        "section": "Results",
                        "section_origin": "own",
                    },
                    "excerpt_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                }
            ],
        }
        return original_normalize(draft, fake, "rag_alone", substrate_manifest_sha256)

    fx = _fixture_by_id(bundle, "F-NORM-4")
    with _patch(ledger_mod, "normalize_draft", normalize_closed_as_rag), _patch(run_mod.ledger_mod, "normalize_draft", normalize_closed_as_rag):
        add(22, "F-NORM-4", "envelope.claims[0].evidence_refs", run_fixture(fx), fx)
        add(22, "F-NORM-4", "envelope.support_refs", run_fixture(fx), fx)

    # 23
    def echo_index_as_manifest(arm, config):
        cfg = dict(config)
        if cfg.get("substrate_manifest_sha256") == cfg.get("substrate_index_sha256"):
            manifest = dict(cfg["substrate_manifest"])
            # accept by rewriting computed identity
            cfg = dict(cfg)
            cfg["substrate_manifest_sha256"] = config_mod.manifest_file_digest(cfg["substrate_manifest"])
        return original_echo(arm, cfg)

    fx = _fixture_by_id(bundle, "F-IDENT-1")
    add(23, "F-IDENT-1", "halt", run_fixture(fx, {"config_echo": echo_index_as_manifest}), fx)

    # 24
    def echo_skip_index(arm, config):
        cfg = dict(config)
        manifest = dict(cfg["substrate_manifest"])
        cfg["substrate_index_sha256"] = manifest.get("index_sha256")
        return original_echo(arm, cfg)

    fx = _fixture_by_id(bundle, "F-IDENT-2")
    add(24, "F-IDENT-2", "halt", run_fixture(fx, {"config_echo": echo_skip_index}), fx)

    # 25
    def stamp_s0f_on_closed(arm, question, config, clock, run_ordinal, echo):
        out = original_stamp("rag_alone" if arm == "closed_book" else arm, question, config, clock, run_ordinal, echo)
        if arm == "closed_book":
            out = dict(out)
            out["envelope_profile"] = ARMS["closed_book"]["envelope_profile"]
            out["request_id"] = f"{question.get('question_id')}:{arm}:r{run_ordinal}"
        return out

    fx = _fixture_by_id(bundle, "F-RUN-3")
    with _patch(config_mod, "stamp_values", stamp_s0f_on_closed), _patch(run_mod.config_mod, "stamp_values", stamp_s0f_on_closed):
        add(25, "F-RUN-3", "envelope.corpus_snapshot", run_fixture(fx), fx)

    # 26
    def adapter_ref_bound(entry, substrate_manifest_sha256):
        ref = original_adapter_ref(entry, substrate_manifest_sha256)
        ref = dict(ref)
        ref["binding_status"] = "bound"
        ref["source_sha256"] = entry.get("served", {}).get("paper_sha256")
        return ref

    fx = _fixture_by_id(bundle, "F-RUN-1")
    with _patch(ledger_mod, "adapter_ref_from_entry", adapter_ref_bound), _patch(
        run_mod.ledger_mod, "adapter_ref_from_entry", adapter_ref_bound
    ):
        add(26, "F-RUN-1", "envelope.support_refs", run_fixture(fx), fx)
        add(26, "F-RUN-1", "envelope.claims[0].evidence_refs", run_fixture(fx), fx)

    # 27
    def present_passage_ordinal(result, ledger, call):
        call2 = dict(call) if isinstance(call, dict) else call
        rows = ledger_mod.passage_rows(result if isinstance(result, dict) else {})
        if rows:
            # use passage numbers as call numbers: first passage call_ordinal=1 regardless of executed calls
            start = len((ledger or {}).get("entries") or [])
            call2 = dict(call2)
            call2["call_ordinal"] = start + 1
        return original_present(result, ledger, call2)

    def present_seq(result, ledger, call):
        out = original_present(result, ledger, call)
        ledger2 = copy.deepcopy(out["ledger"])
        for i, entry in enumerate(ledger2["entries"], start=1):
            entry["call_ordinal"] = i
        return {"message": out["message"], "ledger": ledger2}

    fx = _fixture_by_id(bundle, "F-RUN-7")
    with _patch(ledger_mod, "present_result", present_seq), _patch(run_mod.ledger_mod, "present_result", present_seq):
        add(27, "F-RUN-7", "ledger_after.entries[*].call_ordinal", run_fixture(fx), fx)
        add(27, "F-RUN-7", "envelope.support_refs[*].call_ordinal", run_fixture(fx), fx)

    # 28
    def present_search_tool(result, ledger, call):
        call2 = dict(call) if isinstance(call, dict) else {"tool": "search_literature_corpus", "call_ordinal": 1}
        call2["tool"] = "search_literature_corpus"
        return original_present(result, ledger, call2)

    fx = _fixture_by_id(bundle, "F-RUN-7")
    with _patch(ledger_mod, "present_result", present_search_tool), _patch(run_mod.ledger_mod, "present_result", present_search_tool):
        add(28, "F-RUN-7", "ledger_after.entries[1..2].tool", run_fixture(fx), fx)
        add(28, "F-RUN-7", "envelope.support_refs[1..2].tool", run_fixture(fx), fx)

    # 29
    def passage_no_top(result):
        if not isinstance(result, dict):
            return []
        data = result.get("data")
        if isinstance(data, dict) and "results" in data:
            return list(ledger_mod._walk_passage_rows(data.get("results")))
        return []

    fx = _fixture_by_id(bundle, "F-RUN-7")
    with _patch(ledger_mod, "passage_rows", passage_no_top), _patch(run_mod.ledger_mod, "passage_rows", passage_no_top):
        add(29, "F-RUN-7", "envelope.support_refs", run_fixture(fx), fx)
        add(29, "F-RUN-7", "envelope.claims[0].evidence_refs", run_fixture(fx), fx)
        add(29, "F-RUN-7", "presented_tool_messages[2]", run_fixture(fx), fx)

    return rows
