"""§4.6.1 public writer and legacy harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from answer_eval.resample import public_interval_from_serialized, serialize_endpoint
from answer_eval.tables import INPUT_ROOT, load_json

SCHEMA_FILES = {
    "EVAL_PUBLIC.v3": "fixtures/EVAL_PUBLIC.v3.schema.json",
    "EVAL_PUBLIC.v4": "fixtures/EVAL_PUBLIC.v4.schema.json",
    "EVAL_PUBLIC.v5": "fixtures/EVAL_PUBLIC.v5.schema.json",
    "EVAL_PUBLIC.v6": "fixtures/EVAL_PUBLIC.v6.schema.json",
    "EVAL_PUBLIC.v7": "fixtures/EVAL_PUBLIC.v7.schema.json",
}

DUPLICATE_AXES = (
    "metric_id",
    "run",
    "partition",
    "backbone",
    "cell",
    "arm",
    "transform",
    "stratum",
    "comparison",
    "frame",
    "variant",
)

_SCHEMA_CACHE: dict[str, dict[str, Any]] = {}


def _schema_for(ref: str) -> dict[str, Any]:
    key = ref
    if ref.endswith(".json"):
        rel = ref if not ref.startswith("/") else None
        if ref.startswith("fixtures/"):
            rel = ref
        else:
            name = Path(ref).name
            rel = f"fixtures/{name}"
        if rel not in _SCHEMA_CACHE:
            _SCHEMA_CACHE[ref] = load_json(rel)
        return _SCHEMA_CACHE[ref]
    if key not in SCHEMA_FILES:
        if key in SCHEMA_FILES.values():
            return load_json(key)
        raise KeyError(ref)
    rel = SCHEMA_FILES[key]
    if rel not in _SCHEMA_CACHE:
        _SCHEMA_CACHE[rel] = load_json(rel)
    return _SCHEMA_CACHE[rel]


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "number" if False else "number"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


def _field_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "number"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


def _schema_props(schema: dict[str, Any]) -> set[str]:
    return set((schema.get("properties") or {}).keys())


def _pattern_ok(name: str, schema: dict[str, Any]) -> bool:
    pats = schema.get("patternProperties") or {}
    import re

    for pat in pats:
        if re.match(pat, name):
            return True
    return False


def _unknown_keys(obj: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    known = _schema_props(schema)
    unknown = []
    for key in obj:
        if key in known:
            continue
        if _pattern_ok(key, schema):
            continue
        unknown.append(key)
    unknown.sort()
    return unknown


def _pointer_unknown(prefix: str, obj: dict[str, Any], key: str) -> str:
    unknown = _unknown_keys(obj, {"properties": {}, "patternProperties": {}})
    # n among unknown keys after codepoint sort
    # caller passes already-unknown key list
    return f"{prefix}/<unknown_key#1>"


def _first_violation(obj: Any, schema: dict[str, Any], pointer: str = "") -> dict[str, str] | None:
    if schema.get("type") == "object" or "properties" in schema or "patternProperties" in schema:
        if not isinstance(obj, dict):
            return {"code": "public_schema_violation", "pointer": pointer or "/", "field_type": _field_type(obj)}
        unknown = _unknown_keys(obj, schema)
        if unknown and schema.get("additionalProperties") is False:
            key = unknown[0]
            n = unknown.index(key) + 1
            return {
                "code": "public_schema_violation",
                "pointer": f"{pointer}/<unknown_key#{n}>",
                "field_type": _field_type(obj[key]),
            }
        for req in schema.get("required") or []:
            if req not in obj:
                return {
                    "code": "public_schema_violation",
                    "pointer": f"{pointer}/{req}",
                    "field_type": "null",
                }
        props = schema.get("properties") or {}
        for key, sub in props.items():
            if key in obj:
                hit = _first_violation(obj[key], sub, f"{pointer}/{key}")
                if hit:
                    return hit
        if "patternProperties" in schema:
            import re

            for pat, sub in (schema.get("patternProperties") or {}).items():
                for key, val in obj.items():
                    if key in props:
                        continue
                    if re.match(pat, key):
                        hit = _first_violation(val, sub, f"{pointer}/{key}")
                        if hit:
                            return hit
        for clause in schema.get("allOf") or []:
            hit = _apply_if_then(obj, clause, pointer)
            if hit:
                return hit
        if "const" in schema and obj != schema["const"]:
            return {"code": "public_schema_violation", "pointer": pointer or "/", "field_type": _field_type(obj)}
        if "enum" in schema and obj not in schema["enum"]:
            return {"code": "public_schema_violation", "pointer": pointer or "/", "field_type": _field_type(obj)}
        return None
    if schema.get("type") == "array":
        if not isinstance(obj, list):
            return {"code": "public_schema_violation", "pointer": pointer or "/", "field_type": _field_type(obj)}
        item_schema = schema.get("items") or {}
        if schema.get("minItems") and len(obj) < schema["minItems"]:
            return {"code": "public_schema_violation", "pointer": pointer or "/", "field_type": "array"}
        if schema.get("maxItems") and len(obj) > schema["maxItems"]:
            return {"code": "public_schema_violation", "pointer": pointer or "/", "field_type": "array"}
        for i, item in enumerate(obj):
            hit = _first_violation(item, item_schema, f"{pointer}/{i}")
            if hit:
                return hit
            if isinstance(item_schema, dict) and "minimum" in item_schema and isinstance(item, (int, float)):
                if item < item_schema["minimum"] or ("maximum" in item_schema and item > item_schema["maximum"]):
                    return {"code": "public_schema_violation", "pointer": f"{pointer}/{i}", "field_type": "number"}
        return None
    if "const" in schema and obj != schema["const"]:
        return {"code": "public_schema_violation", "pointer": pointer or "/", "field_type": _field_type(obj)}
    if "enum" in schema and obj not in schema["enum"]:
        return {"code": "public_schema_violation", "pointer": pointer or "/", "field_type": _field_type(obj)}
    if "pattern" in schema and isinstance(obj, str):
        import re

        if not re.match(schema["pattern"], obj):
            return {"code": "public_schema_violation", "pointer": pointer or "/", "field_type": "string"}
    if "minimum" in schema and isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if obj < schema["minimum"]:
            return {"code": "public_schema_violation", "pointer": pointer or "/", "field_type": "number"}
    if "maximum" in schema and isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if obj > schema["maximum"]:
            return {"code": "public_schema_violation", "pointer": pointer or "/", "field_type": "number"}
    return None


def _matches_if(obj: dict[str, Any], iff: dict[str, Any]) -> bool:
    if "required" in iff:
        if not all(k in obj for k in iff["required"]):
            return False
    props = iff.get("properties") or {}
    for key, sub in props.items():
        if "const" in sub:
            if obj.get(key) != sub["const"]:
                return False
        if "enum" in sub:
            if key not in obj or obj[key] not in sub["enum"]:
                return False
    return True


def _apply_if_then(obj: dict[str, Any], clause: dict[str, Any], pointer: str) -> dict[str, str] | None:
    iff = clause.get("if")
    then = clause.get("then")
    otherwise = clause.get("else")
    if iff is None:
        return _first_violation(obj, clause, pointer)
    matched = _matches_if(obj, iff)
    branch = then if matched else otherwise
    if not branch:
        return None
    if "not" in branch:
        inner = branch["not"]
        if _matches_if(obj, inner) or (
            "required" in inner and all(k in obj for k in inner["required"])
        ):
            req = (inner.get("required") or ["_"])[0]
            val = obj.get(req)
            return {
                "code": "public_schema_violation",
                "pointer": f"{pointer}/{req}",
                "field_type": _field_type(val),
            }
    if "required" in branch:
        for req in branch["required"]:
            if req not in obj:
                return {
                    "code": "public_schema_violation",
                    "pointer": f"{pointer}/{req}",
                    "field_type": "null",
                }
    props = branch.get("properties") or {}
    for key, sub in props.items():
        if key in obj:
            hit = _first_violation(obj[key], sub, f"{pointer}/{key}")
            if hit:
                return hit
        if "const" in sub and obj.get(key) != sub["const"]:
            if key not in obj:
                return {
                    "code": "public_schema_violation",
                    "pointer": f"{pointer}/{key}",
                    "field_type": "null",
                }
            return {
                "code": "public_schema_violation",
                "pointer": f"{pointer}/{key}",
                "field_type": _field_type(obj.get(key)),
            }
        if "enum" in sub and key in obj and obj[key] not in sub["enum"]:
            return {
                "code": "public_schema_violation",
                "pointer": f"{pointer}/{key}",
                "field_type": _field_type(obj[key]),
            }
        if "minimum" in sub and key in obj and isinstance(obj[key], (int, float)) and not isinstance(obj[key], bool):
            if obj[key] < sub["minimum"] or ("maximum" in sub and obj[key] > sub["maximum"]):
                return {
                    "code": "public_schema_violation",
                    "pointer": f"{pointer}/{key}",
                    "field_type": "number",
                }
        if "maximum" in sub and key in obj and isinstance(obj[key], (int, float)) and not isinstance(obj[key], bool):
            if obj[key] > sub["maximum"]:
                return {"code": "public_schema_violation", "pointer": f"{pointer}/{key}", "field_type": "number"}
    if "not" in branch and "required" in (branch.get("not") or {}):
        pass
    return None


def _row_axes(row: dict[str, Any]) -> tuple:
    return tuple(row.get(k) for k in DUPLICATE_AXES)


def _writer_predicates(doc: dict[str, Any], schema_name: str) -> dict[str, str] | None:
    if "v6" not in schema_name and "v7" not in schema_name:
        return None
    rows = doc.get("metrics") or []
    seen: dict[tuple, int] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        key = _row_axes(row)
        if key in seen:
            return {"code": "duplicate_row", "pointer": f"/metrics/{i}", "field_type": "object"}
        seen[key] = i
    return None


def _unexpected_name(message: str) -> str | None:
    import re

    m = re.search(r"Additional properties are not allowed \('([^']+)' was unexpected\)", message)
    if m:
        return m.group(1)
    m = re.search(r"^'([^']+)' does not match any of the regexes", message)
    if m:
        return m.group(1)
    m = re.search(r"Additional properties are not allowed \((.+) (?:was|were) unexpected\)", message)
    if m:
        names = re.findall(r"'([^']+)'", m.group(1))
        if names:
            return sorted(names)[0]
    return None


def _prefer_error(errors: list) -> jsonschema.ValidationError:
    def rank(e):
        path = list(e.absolute_path)
        v = e.validator
        if v == "additionalProperties":
            return (0, path)
        if v in {"required"}:
            return (1, path)
        if v in {"const", "enum"}:
            return (2, path)
        if v == "not":
            return (3, path)
        if v in {"minimum", "maximum"}:
            return (4, path)
        return (5, path)

    return sorted(errors, key=rank)[0]


def _sanitize_pointer(doc: dict[str, Any], schema: dict[str, Any], err: jsonschema.ValidationError) -> dict[str, str]:
    path = list(err.absolute_path)
    validator = err.validator
    unexpected = _unexpected_name(err.message) if validator == "additionalProperties" else None
    if unexpected is not None:
        parent = doc
        for seg in path:
            try:
                parent = parent[seg]
            except Exception:
                parent = None
                break
        unknown = []
        if isinstance(parent, dict):
            if path and path[0] == "states":
                states_schema = (schema.get("properties") or {}).get("states") or {}
                unknown = [k for k in parent if not _pattern_ok(k, states_schema)]
            elif path and path[0] == "flags":
                flags_schema = (schema.get("properties") or {}).get("flags") or {}
                unknown = [k for k in parent if not _pattern_ok(k, flags_schema)]
            else:
                metric_schema = ((schema.get("properties") or {}).get("metrics") or {}).get("items") or {}
                known = set((metric_schema.get("properties") or {}).keys())
                unknown = [k for k in parent if k not in known]
            unknown.sort()
            n = unknown.index(unexpected) + 1 if unexpected in unknown else 1
        else:
            n = 1
        prefix = "/" + "/".join(str(s) for s in path) if path else ""
        parent_val = parent.get(unexpected) if isinstance(parent, dict) else None
        from answer_eval.mutation import get as mut

        if mut() == "copy_unknown_key_text":
            ptr = f"{prefix}/{unexpected}"
        else:
            ptr = f"{prefix}/<unknown_key#{n}>"
        return {
            "code": "public_schema_violation",
            "pointer": ptr,
            "field_type": _field_type(parent_val),
        }
    if validator == "required":
        missing = None
        if isinstance(err.validator_value, list):
            loc = err.instance if isinstance(err.instance, dict) else {}
            for req in err.validator_value:
                if isinstance(loc, dict) and req not in loc:
                    missing = req
                    break
        if missing is None:
            token = err.message.split(" ")[0].strip("'\"")
            missing = token
        segs = [str(s) for s in path] + ([missing] if missing else [])
        pointer = "/" + "/".join(segs) if segs else "/"
        return {"code": "public_schema_violation", "pointer": pointer, "field_type": "null"}
    if validator == "not":
        inner = err.validator_value if isinstance(err.validator_value, dict) else {}
        reqs = inner.get("required") or []
        inst = err.instance if isinstance(err.instance, dict) else {}
        for req in reqs:
            if req in inst:
                segs = [str(s) for s in path] + [req]
                return {
                    "code": "public_schema_violation",
                    "pointer": "/" + "/".join(segs),
                    "field_type": _field_type(inst.get(req)),
                }
    segs = [str(s) for s in path]
    pointer = "/" + "/".join(segs) if segs else "/"
    return {
        "code": "public_schema_violation",
        "pointer": pointer,
        "field_type": _field_type(err.instance),
    }


def validate_public_document(doc: dict[str, Any], schema_ref: str) -> tuple[str, dict[str, str] | None]:
    schema = _schema_for(schema_ref)
    title = schema.get("title") or ""
    writer = _writer_predicates(doc, title or schema_ref)
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(doc))
    from answer_eval.mutation import get as mut

    m = mut()
    if not errors:
        if m == "accept_schema":
            return "accepted", None
        if writer:
            if m == "accept_duplicate_axes" and writer.get("code") == "duplicate_row":
                return "accepted", None
            return f"refused:{writer['code']}", writer
        return "accepted", None
    if m == "accept_schema":
        return "accepted", None
    rec = _sanitize_pointer(doc, schema, _prefer_error(errors))
    return "refused:public_schema_violation", rec


def write_attempts(attempts: list[dict[str, Any]], schema_ref: str) -> dict[str, Any]:
    from answer_eval.mutation import get as mut

    m = mut()
    results = []
    records = []
    for attempt in attempts:
        obj = attempt["object"] if "object" in attempt else attempt
        if m == "drop_negative_kappa":
            metrics = obj.get("metrics") or []
            if metrics and metrics[0].get("value_kind") == "kappa":
                val = metrics[0].get("value")
                if isinstance(val, (int, float)) and val < 0:
                    rec = {"code": "public_schema_violation", "pointer": "/metrics/0/value", "field_type": "number"}
                    results.append("refused:public_schema_violation")
                    records.append(rec)
                    continue
        status, rec = validate_public_document(obj, schema_ref)
        results.append(status)
        if rec:
            records.append(rec)
    log_text = json.dumps(records, ensure_ascii=False)
    marker_absent = "SYNTHETIC-PRIVATE-MARKER-7f3a" not in log_text
    return {
        "write_results": results,
        "error_log_records": records,
        "marker_absent_from_log": marker_absent,
        "error_log_guard_violations": None,
        "duplicate_axes": list(DUPLICATE_AXES),
    }


def emit_public(result: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    """Production writer: EVAL_PUBLIC.v7 only. Refuses internal sentinels."""
    schema_ref = "fixtures/EVAL_PUBLIC.v7.schema.json"
    doc = result if result.get("schema") == "EVAL_PUBLIC.v7" else _project_v7(result)
    for row in doc.get("metrics") or []:
        if row.get("status") == "not_implemented_in_wp1":
            rec = {
                "code": "public_schema_violation",
                "pointer": "/metrics/0/status",
                "field_type": "string",
            }
            return {"write_results": "refused:public_schema_violation", "error_log_records": [rec], "document": None}
        if "interval_serialized" in row:
            return {"write_results": "refused:public_schema_violation", "document": None}
        if "interval" in row and row["interval"] and isinstance(row["interval"][0], str):
            return {"write_results": "refused:public_schema_violation", "document": None}
    status, rec = validate_public_document(doc, schema_ref)
    if status != "accepted":
        return {"write_results": status, "error_log_records": [rec] if rec else [], "document": None}
    if path is not None:
        Path(path).write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"write_results": "accepted", "document": doc}


def _project_v7(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("metrics"):
        doc = {
            "schema": "EVAL_PUBLIC.v7",
            "spec_sha256": result.get("spec_sha256") or "0" * 64,
            "subject_id": result.get("subject_id") or "fx",
            "input_digests": result.get("input_digests") or {},
            "metrics": result["metrics"],
        }
        if "states" in result:
            doc["states"] = result["states"]
        if "flags" in result:
            doc["flags"] = result["flags"]
        return doc
    raise ValueError("unprojectable result")


def wrap_metric_row_v3(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "EVAL_PUBLIC.v3",
        "spec_sha256": "0" * 64,
        "subject_id": "fx",
        "input_digests": {},
        "metrics": [row],
    }
