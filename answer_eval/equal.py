"""§5.5 equality, both runs."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, Inexact, Rounded, localcontext
from typing import Any

from answer_eval.canon import fold_ws, label_key
from answer_eval.errors import ConversionInexact

SUPER_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
MINUS_CHARS = "−–—‐‑‒―"
TYPOGRAPHY_PREFIX = re.compile(
    r"^(?P<mant>.+?)(?:\s*[×·xX]\s*10(?:\^(?P<asc>-?\d+)|(?P<super>[⁰¹²³⁴⁵⁶⁷⁸⁹]+))|\s*[eE]\s*(?P<eexp>[+-]?\d+))\s*$"
)

_CONVERSIONS: dict[str, tuple[str, Decimal, Decimal]] | None = None


def load_conversions(table: dict[str, Any] | None = None) -> dict[str, tuple[str, Decimal, Decimal]]:
    global _CONVERSIONS
    if table is None and _CONVERSIONS is not None:
        return _CONVERSIONS
    if table is None:
        from answer_eval.tables import load_json

        table = load_json("fixtures/CONVERSIONS.v2.json")
    out: dict[str, tuple[str, Decimal, Decimal]] = {}
    for alias, spec in table["aliases"].items():
        key = label_key(alias)
        out[key] = (spec["canonical"], Decimal(spec["scale"]), Decimal(spec["offset"]))
    _CONVERSIONS = out
    return out


def _sign_and_body(text: str) -> tuple[str, str]:
    body = text.strip()
    for ch in MINUS_CHARS:
        body = body.replace(ch, "-")
    sign = ""
    if body.startswith("+"):
        body = body[1:]
    if body.startswith("-"):
        sign = "-"
        body = body[1:]
    return sign, body


def parse_typography(raw: str) -> tuple[str, str, int]:
    """Return (sign, mantissa_text, exponent) from the raw string before NFKC."""
    from answer_eval.mutation import get as mut

    if mut() == "nfkc_before_exp":
        raw = unicodedata.normalize("NFKC", raw)
    sign, body = _sign_and_body(raw)
    m = TYPOGRAPHY_PREFIX.match(body)
    if m:
        mant = m.group("mant").strip()
        if m.group("asc") is not None:
            exp = int(m.group("asc"))
        elif m.group("super"):
            exp = int(m.group("super").translate(SUPER_DIGITS))
        else:
            exp = int(m.group("eexp"))
        return sign, mant, exp
    if any(ch in "⁰¹²³⁴⁵⁶⁷⁸⁹" for ch in body) and "10" in body:
        body2 = body.translate(SUPER_DIGITS)
        m2 = re.match(
            r"^(?P<mant>.+?)\s*[×·xX]\s*10(?:\^(?P<asc>-?\d+)|(?P<digits>-?\d+))\s*$",
            body2 if False else body,
        )
        _ = body2
    return sign, body, 0


def _nfkc_mantissa(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    for ch in MINUS_CHARS:
        t = t.replace(ch, "-")
    t = t.strip()
    t = re.sub(r"\s+", "", t)
    return t


def _split_exp_after_nfkc(mant: str, exp: int) -> tuple[str, int]:
    m = re.match(r"^(?P<a>.*)[eE](?P<e>[+-]?\d+)$", mant)
    if m:
        return m.group("a"), exp + int(m.group("e"))
    return mant, exp


def normalize_exact_number(raw: str) -> tuple[str, str, int]:
    sign, mant, exp = parse_typography(raw)
    mant = _nfkc_mantissa(mant)
    if sign == "" and mant.startswith("-"):
        sign, mant = "-", mant[1:]
    mant, exp = _split_exp_after_nfkc(mant, exp)
    if "." in mant:
        whole, frac = mant.split(".", 1)
        mant = (whole or "0") + "." + frac
    return sign, mant, exp


def exact_numbers_equal(a: str, b: str) -> bool:
    sa, ma, ea = normalize_exact_number(a)
    sb, mb, eb = normalize_exact_number(b)
    return sa == sb and ma == mb and ea == eb


def last_place_step(raw: str) -> Decimal:
    sign, mant, exp = normalize_exact_number(raw)
    if "." in mant:
        frac = mant.split(".", 1)[1]
        places = len(frac)
    else:
        places = 0
    return Decimal(10) ** (exp - places)


def to_decimal_value(raw: str) -> Decimal:
    sign, mant, exp = normalize_exact_number(raw)
    if not mant:
        raise ConversionInexact("empty mantissa")
    try:
        base = Decimal(sign + mant)
    except Exception:
        from answer_eval.mutation import get as mut

        if mut() == "nfkc_before_exp":
            raise ConversionInexact("nfkc misparse")
        raise
    if exp:
        base = base * (Decimal(10) ** exp)
    return base


def convert_value(reported: str, unit_label: str | None, conversions: dict[str, tuple[str, Decimal, Decimal]]):
    if unit_label is None or unit_label == "":
        return to_decimal_value(reported), None, Decimal(1), Decimal(0), True
    key = label_key(unit_label)
    if key not in conversions:
        return to_decimal_value(reported), key, Decimal(1), Decimal(0), False
    canon, scale, offset = conversions[key]
    with localcontext() as ctx:
        ctx.prec = 50
        ctx.rounding = "ROUND_HALF_EVEN"
        from answer_eval.mutation import get as mut

        if mut() != "silent_inexact":
            ctx.traps[Inexact] = True
            ctx.traps[Rounded] = True
        try:
            value = to_decimal_value(reported) * scale + offset
        except (Inexact, Rounded, ArithmeticError) as exc:
            if mut() == "silent_inexact":
                ctx.traps[Inexact] = False
                ctx.traps[Rounded] = False
                value = to_decimal_value(reported) * scale + offset
            else:
                raise ConversionInexact("affine conversion") from exc
    return value, canon, scale, offset, True


def labels_absent(status: str | None, unit: str | None) -> bool:
    if status in {"dimensionless", "units_absent_in_source"}:
        return True
    return not unit


def canon_pair_equal(a: dict[str, Any], b: dict[str, Any], conversions: dict[str, tuple[str, Decimal, Decimal]] | None = None) -> bool:
    conversions = conversions or load_conversions()
    ua = a.get("unit_reported")
    ub = b.get("unit_reported")
    sa = a.get("dimension_status")
    sb = b.get("dimension_status")
    ra = a.get("reported")
    rb = b.get("reported")
    if ra is None or rb is None:
        return False
    a_abs = labels_absent(sa, ua)
    b_abs = labels_absent(sb, ub)
    if a_abs and b_abs:
        va, vb = to_decimal_value(ra), to_decimal_value(rb)
        step = last_place_step(ra)
        return _signed_half_step(ra, rb, va, vb, step)
    na, nb = (label_key(ua) if ua else None), (label_key(ub) if ub else None)
    from answer_eval.mutation import get as mut

    if mut() == "unknown_unconvertible" and na is not None and nb is not None and na == nb:
        if na not in conversions:
            return False
    if mut() == "canon_unit_rejected":
        conversions = {
            k: v
            for k, v in conversions.items()
            if k != v[0]
        }
    if na is not None and nb is not None and na == nb:
        va, vb = to_decimal_value(ra), to_decimal_value(rb)
        step = last_place_step(ra)
        return _signed_half_step(ra, rb, va, vb, step)
    if na in conversions and nb in conversions:
        va, ca, scale_a, off_a, oka = convert_value(ra, ua, conversions)
        vb, cb, scale_b, off_b, okb = convert_value(rb, ub, conversions)
        if not (oka and okb) or ca != cb:
            return False
        step = last_place_step(ra)
        if off_a == 0:
            step = step * scale_a
        return _signed_half_step(ra, rb, va, vb, step)
    return False


def _signed_half_step(ra: str, rb: str, va: Decimal, vb: Decimal, step: Decimal) -> bool:
    sa, _, _ = normalize_exact_number(ra)
    sb, _, _ = normalize_exact_number(rb)
    from answer_eval.mutation import get as mut

    if mut() == "signed_zero_canon" and va == 0 and vb == 0:
        return abs(va - vb) <= (step / 2)
    if sa != sb:
        return False
    return abs(va - vb) <= (step / 2)


def _shape_fields_equal(a: dict[str, Any], b: dict[str, Any], run: str, conversions) -> bool:
    shape = a.get("shape") or b.get("shape") or "scalar"
    if a.get("shape") != b.get("shape"):
        return False
    if shape == "scalar":
        return _number_pair(a, b, run, conversions)
    if shape == "range":
        if a.get("endpoints_open") != b.get("endpoints_open"):
            return False
        return _num_field(a, b, "lower", run, conversions) and _num_field(a, b, "upper", run, conversions)
    if shape == "bound":
        if a.get("bound_direction") != b.get("bound_direction"):
            return False
        return _num_field(a, b, "bound_value", run, conversions) or _number_pair(a, b, run, conversions)
    if shape == "function":
        ca, cb = a.get("coefficients") or [], b.get("coefficients") or []
        if len(ca) != len(cb):
            return False
        if a.get("independent_variable") != b.get("independent_variable"):
            iva, ivb = a.get("independent_variable") or {}, b.get("independent_variable") or {}
            if fold_ws(str(iva.get("name", ""))) != fold_ws(str(ivb.get("name", ""))) or iva.get("unit") != ivb.get("unit"):
                return False
        return all(_raw_num_equal(x, y, run, conversions, a.get("unit_reported"), b.get("unit_reported"), a, b) for x, y in zip(ca, cb))
    if shape == "vector":
        return False
    return _number_pair(a, b, run, conversions)


def _num_field(a, b, key, run, conversions) -> bool:
    if key not in a or key not in b:
        return False
    left = dict(a)
    right = dict(b)
    left["reported"] = a[key]
    right["reported"] = b[key]
    return _number_pair(left, right, run, conversions)


def _raw_num_equal(x, y, run, conversions, ua, ub, a, b) -> bool:
    left = dict(a)
    right = dict(b)
    left["reported"] = x
    right["reported"] = y
    return _number_pair(left, right, run, conversions)


def _number_pair(a: dict[str, Any], b: dict[str, Any], run: str, conversions) -> bool:
    ra, rb = a.get("reported"), b.get("reported")
    if not isinstance(ra, str) or not isinstance(rb, str):
        return False
    if run == "exact_reported":
        if not exact_numbers_equal(ra, rb):
            return False
        sa, sb = a.get("dimension_status"), b.get("dimension_status")
        if sa == "dimensionless" or sb == "dimensionless":
            if a.get("unit_reported") or b.get("unit_reported"):
                return False
            return True
        if sa == "units_absent_in_source" or sb == "units_absent_in_source":
            return b.get("dimension_status") == "units_absent_in_source" and a.get("dimension_status") == "units_absent_in_source"
        ua, ub = a.get("unit_reported"), b.get("unit_reported")
        if not ua or not ub:
            return False
        return fold_ws(str(ua)) == fold_ws(str(ub)) or label_key(str(ua)) == label_key(str(ub))
    return canon_pair_equal(a, b, conversions)


def _is_unknown(value: Any) -> bool:
    if isinstance(value, dict) and (value.get("unknown") is True or value.get("applicability", "").startswith("unknown")):
        return True
    if isinstance(value, dict) and "unknown" in value and value.get("unknown"):
        return True
    return False


def equal(a: Any, b: Any, run: str) -> bool:
    conversions = load_conversions()
    if _is_unknown(a) or _is_unknown(b):
        return _is_unknown(a) and _is_unknown(b)
    if isinstance(a, dict) and isinstance(b, dict) and ("reported" in a or "shape" in a or "reported" in b):
        if a.get("dimension_status") == "dimensionless" and b.get("unit_reported"):
            if run == "exact_reported":
                return False
        if a.get("dimension_status") == "units_absent_in_source":
            if b.get("dimension_status") != "units_absent_in_source":
                return False
        return _shape_fields_equal(a, b, run, conversions)
    if isinstance(a, str) and isinstance(b, str):
        if run == "exact_reported":
            return fold_ws(a) == fold_ws(b) or a == b
        return fold_ws(a) == fold_ws(b)
    return a == b
