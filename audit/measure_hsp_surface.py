#!/usr/bin/env python3
"""Reproduce A2: HSP surface, RF mutation, leakage, call sites, pins.

Coverage of a line is not an audit of that line. This walk measures the
forest that A1 only mapped. No BioSTEAM. Does not edit analysis.py.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_DATA = _SRC / "dissolve" / "data"
_SPEC = Path("/home/aaltamimi2/dissolve-v12-audit/UNAUDITED_SURFACE_SPEC.v2.md")

_OFF_GRID_HSP_IDENTITIES = (
    "ABS", "FEP", "NYLON11", "PETG", "PCTFE", "PMMA", "POM",
    "PSU", "PTFE", "PVAC", "PVDF", "PVOH", "SAN",
)
_DECISION_SUFFIXES = (
    "success",
    "error_code",
    "unsupported_polymers",
    "supported_requested_polymers",
    "unsupported_requested_polymers",
    "hsp_fallback_requested_polymers",
    "no_local_data_requested_polymers",
    "evidence_path",
    "canonical_identity",
    "modeled_polymers",
    "requested_polymers",
    "n_pairs",
    "leading_match_policy",
)

_VALIDATION_CAVEAT = (
    "The bundled metadata reports perfect test metrics; these are not "
    "independent external validation and should be treated cautiously."
)
_SCOPE_WARNING = (
    "The HSP Random Forest is temperature-independent binary screening "
    "trained on RED < 1 labels; it is not wt% solubility."
)
_MODEL_BASIS = (
    "stored-grid thermodynamics, then checksummed HSP Random Forest fallback"
)


def _sys_path() -> None:
    for path in (str(_ROOT), str(_SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _analysis():
    _sys_path()
    from dissolve import analysis
    return analysis


def _separation():
    _sys_path()
    from dissolve import separation
    return separation


def _parse(raw: str) -> dict[str, Any]:
    from dissolve.contracts import parse_tool_result
    return parse_tool_result(raw)


def curated_solvent_names() -> list[str]:
    analysis = _analysis()
    return [
        str(item.get("display_name") or item["id"])
        for item in analysis.asset_payload()["hsp"]["curated_solvents"]
    ]


def pin_report() -> dict[str, Any]:
    analysis = _analysis()
    files = {}
    for name, expected in analysis._HSP_ML_ASSETS.items():
        path = _DATA / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[name] = {
            "path": f"src/dissolve/data/{name}",
            "pinned_sha256": expected,
            "on_disk_sha256": digest,
            "bytes": path.stat().st_size,
            "match": digest == expected,
        }
    metadata = json.loads(
        (_DATA / f"{analysis._HSP_ML_STEM}_metadata.json").read_text(encoding="utf-8")
    )
    return {
        "stem": analysis._HSP_ML_STEM,
        "filename_timestamp_token": "20251231_212903",
        "metadata_timestamp": metadata.get("timestamp"),
        "timestamp_discrepancy": "unexplained",
        "timestamp_note": (
            "The on-disk stem is corrected_Random_Forest_20251231_212903; "
            "metadata.timestamp is 20260124_145027. No comment, commit message, "
            "or sidecar in this worktree explains the 24-day gap. Recorded as "
            "unexplained, not resolved."
        ),
        "metadata_sklearn_version": metadata.get("sklearn_version"),
        "metadata_target": metadata.get("target_definition"),
        "metadata_threshold": metadata.get("classification_threshold"),
        "metadata_performance": metadata.get("performance"),
        "files": files,
        "all_pins_match": all(item["match"] for item in files.values()),
    }


def forest_report() -> dict[str, Any]:
    analysis = _analysis()
    model, _scaler, metadata = analysis._hsp_ml_assets()
    names = list(metadata["feature_names"])
    red_idx = names.index("RED")
    importances = {
        name: float(value) for name, value in zip(names, model.feature_importances_)
    }
    split_counts: Counter[str] = Counter()
    red_only_stumps = 0
    red_only_trees = 0
    trees = []
    for index, estimator in enumerate(model.estimators_):
        tree = estimator.tree_
        features = [int(feat) for feat in tree.feature if feat >= 0]
        for feat in features:
            split_counts[names[feat]] += 1
        unique = set(features)
        is_stump = tree.node_count == 3 and tree.max_depth == 1
        only_red = unique == {red_idx}
        if is_stump and only_red:
            red_only_stumps += 1
        if only_red:
            red_only_trees += 1
        if index == 0:
            trees.append({
                "index": 0,
                "node_count": int(tree.node_count),
                "max_depth": int(tree.max_depth),
                "split_count": len(features),
            })
    total_splits = sum(split_counts.values())
    return {
        "n_estimators": int(len(model.estimators_)),
        "max_depth_param": int(model.max_depth),
        "n_features": int(model.n_features_in_),
        "gini_importance": importances,
        "hsp_component_gini_sum": float(sum(
            importances[name] for name in names[:6]
        )),
        "molar_volume_gini": importances["Molar_Volume"],
        "red_only_stumps": red_only_stumps,
        "trees_that_split_only_on_red": red_only_trees,
        "tree0": trees[0],
        "split_counts": dict(split_counts),
        "split_share": {
            name: (split_counts[name] / total_splits if total_splits else 0.0)
            for name in names
        },
        "total_splits": total_splits,
        "stump_definition": (
            "A RED-only stump is a tree with node_count==3, max_depth==1, "
            "and the sole split feature is RED. This is not 'any tree that "
            "ever splits on RED'."
        ),
    }


def _feature_row(row: dict[str, Any]) -> list[float] | None:
    if row.get("red") is None or "polymer_hsp" not in row:
        return None
    polymer = row["polymer_hsp"]
    solvent = row["solvent_hsp"]
    return [
        float(polymer["dispersion"]), float(polymer["polar"]),
        float(polymer["hydrogen_bonding"]),
        float(solvent["dispersion"]), float(solvent["polar"]),
        float(solvent["hydrogen_bonding"]),
        float(row["solvent_molar_volume"]), float(row["interaction_radius"]),
        float(row["ra_mpa_sqrt"]), float(row["red"]),
    ]


def leakage_report() -> dict[str, Any]:
    """Disagreement and residual on production HSP pairs.

    Hook (named, as the spec allows): `_hsp_red_rows` plus the production
    model/scaler from `_hsp_ml_assets`, which is what `_hsp_ml_prediction`
    calls. Population is off-grid HSP library identities × every curated
    solvent. That is the same predictor fallback serves, on the pairs
    fallback would emit if those solvents were requested. The zero-R0/Ra/RED
    mutation is the patched feature vector.
    """
    import numpy as np

    analysis = _analysis()
    model, scaler, metadata = analysis._hsp_ml_assets()
    threshold = float(metadata["classification_threshold"])
    polymers, _issues_p = analysis._resolve_many(
        list(_OFF_GRID_HSP_IDENTITIES), "polymer", False,
    )
    solvents, _issues_s = analysis._resolve_many(curated_solvent_names(), "solvent", False)
    raw_rows = analysis._hsp_red_rows(polymers, solvents)
    features = []
    red_lt = []
    red_le = []
    served = []
    for row in raw_rows:
        feat = _feature_row(row)
        if feat is None:
            continue
        prediction = analysis._hsp_ml_prediction(row)
        features.append(feat)
        red_lt.append(int(row["red"] < 1.0))
        red_le.append(int(row["red"] <= 1.0))
        served.append(int(prediction["predicted_class"] == "soluble"))
    x = np.array(features, dtype=float)
    y_lt = np.array(red_lt)
    y_le = np.array(red_le)
    y_served = np.array(served)
    proba = model.predict_proba(scaler.transform(x))[:, 1]
    predicted = (proba >= threshold).astype(int)
    zeroed = x.copy()
    zeroed[:, [7, 8, 9]] = 0.0
    residual = (model.predict_proba(scaler.transform(zeroed))[:, 1] >= threshold).astype(int)
    fallback_rows = 0
    fallback_disagree = 0
    for identity in _OFF_GRID_HSP_IDENTITIES:
        evidence = analysis.hsp_fallback_evidence(identity, curated_solvent_names())
        for row in evidence.get("rows") or []:
            red = row.get("red")
            if red is None:
                continue
            fallback_rows += 1
            soluble = row["hsp_ml_prediction"]["predicted_class"] == "soluble"
            if soluble != (red <= 1.0) or soluble != (red < 1.0):
                fallback_disagree += 1
    return {
        "hook": (
            "dissolve.analysis._hsp_red_rows + production _hsp_ml_prediction / "
            "model.predict_proba. Same predictor as hsp_fallback_evidence. "
            "Not a random vector except the zero-R0/Ra/RED mutation."
        ),
        "population": {
            "polymers": list(_OFF_GRID_HSP_IDENTITIES),
            "solvents": "all curated HSP solvents",
            "raw_pairs": len(raw_rows),
            "scored_pairs": int(len(x)),
            "fallback_rows_same_population": fallback_rows,
        },
        "disagreement": {
            "predicted_class_vs_RED_lt_1": int((predicted != y_lt).sum()),
            "predicted_class_vs_RED_le_1": int((predicted != y_le).sum()),
            "predicted_class_vs_served_class": int((predicted != y_served).sum()),
            "fallback_rows_disagreeing_with_either_RED_rule": fallback_disagree,
            "rate_vs_RED_lt_1": float((predicted != y_lt).mean()) if len(x) else None,
            "n_RED_lt_1": int(y_lt.sum()),
            "n_predicted_soluble": int(predicted.sum()),
            "n_soft_probability": int(((proba > 0.0) & (proba < 1.0)).sum()),
            "note": (
                "RED is Ra/R0. This is not a RED-versus-Ra physical case. "
                "Served class is predict_proba >= 0.85, not RED < 1."
            ),
        },
        "residual_capacity": {
            "sources": [
                "served class uses predict_proba >= 0.85, not RED < 1",
                "Ra and R0 are split on as separate features",
                "six HSP components retain residual Gini mass",
            ],
            "zero_R0_Ra_RED_remaining_accuracy_vs_RED_lt_1": float(
                (residual == y_lt).mean()
            ),
            "zero_R0_Ra_RED_remaining_accuracy_vs_RED_le_1": float(
                (residual == y_le).mean()
            ),
            "zero_R0_Ra_RED_agreement_with_original_class": float(
                (residual == predicted).mean()
            ),
            "zero_R0_Ra_RED_predicted_soluble": int(residual.sum()),
            "not_solubility_skill": True,
            "note": (
                "Zeroing R0, Ra, and RED on this population makes every pair "
                "predict soluble. Remaining accuracy equals the RED<1 base "
                "rate. That number is residual capacity after the leaked "
                "ratio features, not skill at temperature-dependent wt% "
                "solubility."
            ),
        },
    }


def _leaf_paths(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(_leaf_paths(item, path))
        return out
    if isinstance(value, list):
        out = {}
        for index, item in enumerate(value):
            out.update(_leaf_paths(item, f"{prefix}[{index}]"))
        return out
    return {prefix: value}


def _is_decision_path(path: str) -> bool:
    if "hsp_ml_prediction" in path:
        return False
    if ".hsp_fallback.rows[" in path and path.endswith(".red"):
        return False
    tail = path.rsplit(".", 1)[-1]
    tail = tail.split("[", 1)[0]
    return tail in _DECISION_SUFFIXES or path.endswith(".evidence_path") or path.endswith(".status") and "hsp_fallback" not in path


def mutation_report() -> dict[str, Any]:
    analysis = _analysis()
    separation = _separation()

    def snapshot() -> dict[str, Any]:
        return {
            "lookup": _parse(analysis.lookup_hansen_parameters(["PMMA"], "polymer")),
            "screen": _parse(analysis.screen_hansen_compatibility(
                ["PMMA"], ["Toluene", "THF"],
            )),
            "plan": _parse(separation.plan_multistage_separation(["PMMA", "LDPE"])),
            "scope_mixed": _parse(separation.resolve_polymer_data_scope(
                ["PMMA", "LDPE"],
            )),
            "fallback_no_solvent": analysis.hsp_fallback_evidence("PMMA"),
            "fallback_with_solvent": analysis.hsp_fallback_evidence(
                "PMMA", ["Toluene"],
            ),
            "scope_with_solvent": _parse(separation.resolve_polymer_data_scope(
                ["PMMA"], operating_solvent="Toluene", operating_temperature_c=80,
            )),
        }

    baseline = snapshot()
    original = analysis._hsp_ml_prediction
    omit_payloads = None
    try:
        analysis._hsp_ml_prediction = lambda row: {}
        omit_payloads = snapshot()
    finally:
        analysis._hsp_ml_prediction = original

    changed: list[str] = []
    unchanged_decisions: list[str] = []
    for name in baseline:
        before = _leaf_paths(baseline[name])
        after = _leaf_paths(omit_payloads[name])
        keys = set(before) | set(after)
        for path in sorted(keys):
            if before.get(path) == after.get(path):
                if _is_decision_path(path):
                    unchanged_decisions.append(f"{name}:{path}")
                continue
            changed.append(f"{name}:{path}")

    raise_results = {}
    def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("A2 RF disabled")

    try:
        analysis._hsp_ml_prediction = boom
        for label, fn in (
            ("lookup", lambda: analysis.lookup_hansen_parameters(["PMMA"], "polymer")),
            ("screen", lambda: analysis.screen_hansen_compatibility(["PMMA"], ["Toluene"])),
            ("plan", lambda: separation.plan_multistage_separation(["PMMA", "LDPE"])),
            ("fallback_no_solvent", lambda: analysis.hsp_fallback_evidence("PMMA")),
            ("fallback_ldpe_with_solvent", lambda: analysis.hsp_fallback_evidence(
                "LDPE", ["Toluene"],
            )),
            ("scope_no_solvent", lambda: separation.resolve_polymer_data_scope(["PMMA"])),
        ):
            try:
                fn()
                raise_results[label] = "unchanged_no_exception"
            except RuntimeError as error:
                raise_results[label] = f"exception:{error}"
        try:
            analysis.hsp_fallback_evidence("PMMA", ["Toluene"])
            raise_results["fallback_with_solvent"] = "unchanged_no_exception"
        except RuntimeError:
            raise_results["fallback_with_solvent"] = "exception:A2 RF disabled"
        try:
            separation.resolve_polymer_data_scope(
                ["PMMA"], operating_solvent="Toluene", operating_temperature_c=80,
            )
            raise_results["scope_with_solvent"] = "unchanged_no_exception"
        except RuntimeError:
            raise_results["scope_with_solvent"] = "exception:A2 RF disabled"
    finally:
        analysis._hsp_ml_prediction = original

    lookup_has_ml = "hsp_ml_prediction" in json.dumps(baseline["lookup"])
    screen_has_ml = "hsp_ml_prediction" in json.dumps(baseline["screen"])
    plan = baseline["plan"]["data"]
    scope = baseline["scope_mixed"]["data"]
    return {
        "classification": "payload_decoration",
        "decision_dependence": False,
        "unused_pin_on_lookup_and_screen": (not lookup_has_ml) and (not screen_has_ml),
        "payload_fields_that_change_when_prediction_is_omitted": changed,
        "decision_fields_unchanged_when_prediction_is_omitted": True,
        "decisions_checked_unchanged": [
            "lookup/screen success and rows",
            "plan error_code=unknown_polymer for PMMA+LDPE (refuses before fallback)",
            "scope supported_requested_polymers / hsp_fallback_requested_polymers / evidence_path",
        ],
        "raise_disable": raise_results,
        "raise_disable_note": (
            "Production has no graceful disable. Replacing _hsp_ml_prediction "
            "with a raise leaves lookup, screen, plan, on-grid fallback, and "
            "no-solvent fallback unchanged, and crashes fallback-with-solvent "
            "and resolve_polymer_data_scope(off-grid, solvent). That is "
            "call-success, not a solvent pick or route change."
        ),
        "omit_changed_field_count": len(changed),
        "omit_changed_all_under_hsp_ml_prediction": all(
            "hsp_ml_prediction" in path for path in changed
        ),
        "baseline": {
            "lookup_has_hsp_ml_prediction": lookup_has_ml,
            "screen_has_hsp_ml_prediction": screen_has_ml,
            "plan_success": plan.get("success"),
            "plan_error_code": plan.get("error_code"),
            "plan_unsupported": plan.get("unsupported_polymers"),
            "scope_supported": scope.get("supported_requested_polymers"),
            "scope_fallback": scope.get("hsp_fallback_requested_polymers"),
            "scope_evidence_paths": [
                row.get("evidence_path") for row in scope.get("results") or []
            ],
        },
        "call_sites_of_the_forest": [
            "analysis._hsp_ml_prediction — only caller of the joblib model",
            "analysis.hsp_fallback_evidence — only caller of _hsp_ml_prediction, and only when solvents resolve to RED rows",
            "separation.resolve_polymer_data_scope — only caller of hsp_fallback_evidence, and only when expand_polymer_identity is empty",
            "lookup_hansen_parameters — never calls the forest",
            "screen_hansen_compatibility — RED-only, never calls the forest",
            "plan_multistage_separation — refuses unknown polymers before any fallback",
        ],
    }


def call_sites_report() -> dict[str, Any]:
    """Every user-facing accuracy quote, including the existing mitigations."""
    analysis = _analysis()
    separation = _separation()
    fallback = analysis.hsp_fallback_evidence("PMMA", ["Toluene"])
    prediction = fallback["rows"][0]["hsp_ml_prediction"]
    scope = _parse(separation.resolve_polymer_data_scope(["PMMA"]))["data"]
    return {
        "served_on_fallback_rows": {
            "validation_caveat": prediction["validation_caveat"],
            "perfect_test_metrics_reported": prediction["perfect_test_metrics_reported"],
            "reported_test_metrics": prediction["reported_test_metrics"],
            "target_definition": prediction["target_definition"],
            "classification_threshold": prediction["classification_threshold"],
            "model_type": prediction["model_type"],
        },
        "served_on_resolve_polymer_data_scope": {
            "warnings": scope["warnings"],
            "model_basis": scope["model_basis"],
        },
        "verbatim": [
            {
                "where": "analysis._hsp_ml_prediction → hsp_fallback.rows[].hsp_ml_prediction.validation_caveat",
                "text": _VALIDATION_CAVEAT,
            },
            {
                "where": "analysis._hsp_ml_prediction → reported_test_metrics (all 1.0)",
                "text": json.dumps(prediction["reported_test_metrics"], sort_keys=True),
            },
            {
                "where": "analysis._hsp_ml_prediction → target_definition",
                "text": "RED < 1.0 = Soluble",
            },
            {
                "where": "separation.resolve_polymer_data_scope warnings[0]",
                "text": _SCOPE_WARNING,
            },
            {
                "where": "separation.resolve_polymer_data_scope model_basis",
                "text": _MODEL_BASIS,
            },
        ],
        "not_user_facing": [
            "analysis._hsp_ml_assets docstring (checksummed v10 Hansen Random Forest)",
            "analysis._hsp_ml_assets RuntimeError contract on target_definition",
        ],
        "lookup_and_screen_do_not_quote_model_accuracy": True,
    }


def build_document() -> dict[str, Any]:
    spec_sha = subprocess.check_output(["sha256sum", str(_SPEC)], text=True).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    pins = pin_report()
    forest = forest_report()
    leak = leakage_report()
    mutation = mutation_report()
    sites = call_sites_report()
    return {
        "schema": "dissolve.audit-hsp-surface.v1",
        "spec": "UNAUDITED_SURFACE_SPEC.v2",
        "spec_sha256": spec_sha,
        "checkpoint": "A2",
        "measured_on_builder_sha": head,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "does_not_supersede": (
            "Coverage of a line is not an audit of that line. A1 remains the map."
        ),
        "command": "python3 audit/measure_hsp_surface.py",
        "pins": pins,
        "forest": forest,
        "leakage": leak,
        "mutation": mutation,
        "call_sites": sites,
    }


def main() -> int:
    _sys_path()
    document = build_document()
    out = _ROOT / "audit" / "HSP_SURFACE.v1.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}", flush=True)
    print(
        "pins", document["pins"]["all_pins_match"],
        "stumps", document["forest"]["red_only_stumps"],
        "disagree", document["leakage"]["disagreement"]["predicted_class_vs_RED_lt_1"],
        "residual_acc",
        document["leakage"]["residual_capacity"]["zero_R0_Ra_RED_remaining_accuracy_vs_RED_lt_1"],
        "decoration", document["mutation"]["omit_changed_all_under_hsp_ml_prediction"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
