"""Arithmetic on values the tools returned: one deterministic tool, so no number in an answer is worked out by the
model itself.

Until 2026-10-13 the agent had no way to subtract or divide two tool values and was told not to try. Asked for a gap
or a ratio it gave the two numbers and declined, which cost 8 of 100 evaluation questions their one missing value,
and where it did its own arithmetic over a temperature range it sampled the range and miscounted. The tool takes
numbers or whole columns of a stored result, so a curve is never retyped.
"""
from __future__ import annotations

import math
import re
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Any, Literal, Optional

from .contracts import tool_error, tool_success
from .session import current_tool_session, handle_rows, load_handle

TOOL = "calculate"
EVIDENCE_CLASS = "calculated_from_supplied_values"
_MAX_VALUES = 2000
_ELEMENTWISE = {
    "difference": "a - b", "ratio": "a / b", "sum": "a + b", "product": "a * b",
    "percent_change": "100 * (a - b) / b", "absolute": "|a|",
}
_REDUCE = {"min": "smallest of a", "max": "largest of a", "total": "sum of a", "count": "number of values in a"}
_FILTER = {
    "keep_above": "a > threshold", "keep_at_least": "a >= threshold", "keep_below": "a < threshold",
    "keep_at_most": "a <= threshold", "keep_between": "threshold <= a <= upper",
}
_ROUND = {
    "round": "a to the given decimals", "round_down_to_step": "largest multiple of step not above a",
    "round_up_to_step": "smallest multiple of step not below a", "round_to_step": "nearest multiple of step to a",
}
_WHERE = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*(>=|<=|!=|=|>|<)\s*(.+?)\s*$")


class _Refuse(Exception):
    def __init__(self, code: str, message: str, **data: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def _clean(value: float) -> float:
    """A float without binary noise in its last digits (189.0 - 25.0 is 164, 0.3 - 0.1 is 0.2)."""
    return float(f"{value:.12g}")


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _Refuse("non_numeric_value", f"{where} is not a number: {value!r}", value=repr(value))
    return float(value)


def _cell(row: dict[str, Any], column: str) -> Any:
    value: Any = row
    for part in column.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(column)
        value = value[part]
    return value


def _same(cell: Any, text: str) -> bool:
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        try:
            return float(cell) == float(text)
        except ValueError:
            return False
    return str(cell).strip().casefold() == text.strip().casefold()


def _keep(row: dict[str, Any], conditions: list[tuple[str, str, str]]) -> bool:
    for column, op, text in conditions:
        try:
            cell = _cell(row, column)
        except KeyError:
            return False
        if op in ("=", "!="):
            if _same(cell, text) != (op == "="):
                return False
            continue
        try:
            left, right = float(cell), float(text)
        except (TypeError, ValueError):
            return False
        if not {">": left > right, "<": left < right, ">=": left >= right, "<=": left <= right}[op]:
            return False
    return True


def _from_handle(name: str, source: str, where: Optional[list[str]], label_columns: list[str]):
    """Values of one column of a stored result, with the labels of the rows they came from."""
    handle, _, column = str(source).partition(":")
    handle, column = handle.strip(), column.strip()
    if not handle or not column:
        raise _Refuse("bad_source", f"{name}_from is written handle:column, for example h:solubility_pct; got {source!r}")
    record = current_tool_session()
    stored = load_handle(record, handle) if record is not None else None
    if stored is None:
        raise _Refuse("unknown_handle", f"{name}_from names a handle this chat does not hold: {handle!r}", handle=handle)
    rows = handle_rows(stored)
    conditions = []
    for item in where or []:
        hit = _WHERE.match(str(item))
        if not hit:
            raise _Refuse("bad_condition", f"{name}_where entries are written column=value (also != > >= < <=); got {item!r}")
        conditions.append(hit.groups())
    kept = [row for row in rows if _keep(row, conditions)]
    columns = sorted({key for row in rows[:50] for key, value in row.items() if not isinstance(value, (dict, list))})
    if not kept:
        raise _Refuse("no_rows_match", f"No row of {handle} meets {name}_where {list(where or [])}.", handle=handle,
                      rows_in_handle=len(rows), columns=columns)
    values, labels = [], []
    for n, row in enumerate(kept, 1):
        try:
            cell = _cell(row, column)
            label = " | ".join(str(_cell(row, c)) for c in label_columns)
        except KeyError as error:
            raise _Refuse("unknown_column", f"{handle} has no column {error.args[0]!r}.", handle=handle,
                          columns=columns) from None
        values.append(_number(cell, f"{handle}:{column} in matching row {n}"))
        labels.append(label)
    return values, (labels if label_columns else None), {"handle": handle, "column": column, "where": list(where or []),
                                                          "rows_used": len(kept), "rows_in_handle": len(rows)}


def _step_round(value: float, step: float, mode: str) -> float:
    q = Decimal(str(value)) / Decimal(str(step))
    how = {"round_down_to_step": ROUND_FLOOR, "round_up_to_step": ROUND_CEILING, "round_to_step": ROUND_HALF_EVEN}[mode]
    return float(q.to_integral_value(rounding=how) * Decimal(str(step)))


def calculate(
    operation: Literal[
        "difference", "ratio", "sum", "product", "percent_change", "absolute",
        "min", "max", "total", "count",
        "keep_above", "keep_at_least", "keep_below", "keep_at_most", "keep_between",
        "round", "round_down_to_step", "round_up_to_step", "round_to_step",
    ],
    a: Optional[list[float]] = None,
    b: Optional[list[float]] = None,
    labels: Optional[list[str]] = None,
    a_from: Optional[str] = None,
    b_from: Optional[str] = None,
    labels_from: Optional[str] = None,
    a_where: Optional[list[str]] = None,
    b_where: Optional[list[str]] = None,
    threshold: Optional[float] = None,
    upper: Optional[float] = None,
    step: Optional[float] = None,
    decimals: Optional[int] = None,
) -> str:
    """Do arithmetic on tool values, one number or a whole column: difference (a-b), ratio (a/b), sum, product, percent_change, absolute; min, max, total, count of a; keep_above/at_least/below/at_most/between a threshold (returns what is kept and how many); round, or round down/up/to a step. Give values as lists (a=[189.0], b=[25.0]) or take a column of a stored result with a_from='handle:column', narrowed by a_where=['polymer=PVC','temperature_c>=25'] and labelled by labels_from='temperature_c' (b_from, b_where likewise; with labels_from, b is matched to a by label). A one-value b applies to every a. A column result comes back with a handle whose column 'value' can feed the next call. Never interpolates or fits."""
    try:
        if operation not in {**_ELEMENTWISE, **_REDUCE, **_FILTER, **_ROUND}:
            raise _Refuse("unknown_operation", f"Unknown operation {operation!r}.",
                          operations=[*_ELEMENTWISE, *_REDUCE, *_FILTER, *_ROUND])
        # an empty list is "not given", as an empty text already is: some models write every unused argument so
        a, b, labels = (None if isinstance(v, (list, tuple)) and not v else v for v in (a, b, labels))
        label_columns = [c.strip() for c in str(labels_from or "").split(",") if c.strip()]
        sources: dict[str, Any] = {}
        sides: dict[str, tuple[list[float], Optional[list[str]]]] = {}
        for name, direct, source, where in (("a", a, a_from, a_where), ("b", b, b_from, b_where)):
            if direct is not None and source:
                raise _Refuse("two_sources", f"Give {name} or {name}_from, not both.")
            if source:
                values, side_labels, sources[name] = _from_handle(name, source, where, label_columns)
            elif direct is not None:
                if where:
                    raise _Refuse("where_without_source", f"{name}_where narrows {name}_from; {name} was typed in.")
                items = direct if isinstance(direct, (list, tuple)) else [direct]
                values = [_number(v, f"{name}[{i}]") for i, v in enumerate(items)]
                side_labels, sources[name] = None, "typed in the call"
            else:
                continue
            if not values:
                raise _Refuse("empty_input", f"{name} holds no value.")
            if len(values) > _MAX_VALUES:
                raise _Refuse("too_many_values", f"{name} holds {len(values)} values; the limit is {_MAX_VALUES}.")
            sides[name] = (values, side_labels)
        if "a" not in sides:
            raise _Refuse("missing_input", "a (or a_from) is required.")
        two = operation in _ELEMENTWISE and operation != "absolute"
        if two and "b" not in sides:
            raise _Refuse("missing_input", f"{operation} needs b (or b_from) as well as a.")
        if not two and "b" in sides:
            raise _Refuse("unused_input", f"{operation} works on a alone; leave b out.")
        av, alabels = sides["a"]
        bv, blabels = sides.get("b", ([], None))
        if labels is not None:
            if label_columns:
                raise _Refuse("two_sources", "Give labels or labels_from, not both.")
            if len(labels) != max(len(av), len(bv)):
                raise _Refuse("length_mismatch", f"{len(labels)} labels for {max(len(av), len(bv))} values.")
            alabels = [str(x) for x in labels] if len(av) == len(labels) else alabels
            blabels = [str(x) for x in labels] if len(bv) == len(labels) else blabels
        if two:
            if alabels and blabels and len(av) > 1 and len(bv) > 1 and "b" in sources and isinstance(sources["b"], dict) \
                    and isinstance(sources["a"], dict):
                # two columns of stored results: pair them by label, never by position
                if len(set(alabels)) != len(alabels) or len(set(blabels)) != len(blabels):
                    raise _Refuse("labels_not_unique", "labels_from does not tell the rows apart; add a column to it "
                                  "(for example 'solvent,temperature_c') or narrow a_where and b_where.")
                if set(alabels) != set(blabels):
                    raise _Refuse("labels_do_not_match", "a and b do not cover the same labels.",
                                  only_in_a=sorted(set(alabels) - set(blabels))[:20],
                                  only_in_b=sorted(set(blabels) - set(alabels))[:20])
                at = dict(zip(blabels, bv))
                bv = [at[label] for label in alabels]
            n = max(len(av), len(bv))
            if len(av) not in (1, n) or len(bv) not in (1, n):
                raise _Refuse("length_mismatch", f"a holds {len(av)} values and b holds {len(bv)}; they must match in "
                              "length, or one of them must be a single value.")
            row_labels = alabels if alabels and len(alabels) == n else blabels if blabels and len(blabels) == n else None
            av, bv = (av * n if len(av) == 1 else av), (bv * n if len(bv) == 1 else bv)
        else:
            n, row_labels = len(av), alabels
        row_labels = row_labels or ([str(i + 1) for i in range(n)] if n > 1 else None)

        def label(i: int) -> Optional[str]:
            return row_labels[i] if row_labels else None

        common = {"operation": operation, "evidence_class": EVIDENCE_CLASS, "inputs": sources, "n_values": n,
                  "note": "Arithmetic on the values supplied; it is exactly as right as they are."}
        if operation in _ELEMENTWISE:
            rows = []
            for i in range(n):
                x, y = av[i], (bv[i] if two else None)
                if operation in ("ratio", "percent_change") and y == 0:
                    raise _Refuse("division_by_zero", f"b is zero{f' at {label(i)}' if label(i) else ''}.")
                value = {"difference": lambda: x - y, "ratio": lambda: x / y, "sum": lambda: x + y,
                         "product": lambda: x * y, "percent_change": lambda: 100.0 * (x - y) / y,
                         "absolute": lambda: abs(x)}[operation]()
                rows.append({"label": label(i), "a": x, **({"b": y} if two else {}), "value": _clean(value)})
            return _vector_or_scalar(common, _ELEMENTWISE[operation], rows)
        if operation in _ROUND:
            if operation == "round":
                if decimals is None or isinstance(decimals, bool) or not -6 <= int(decimals) <= 12:
                    raise _Refuse("missing_input", "round needs decimals (an integer from -6 to 12).")
                rows = [{"label": label(i), "a": x, "value": _clean(round(x, int(decimals)))} for i, x in enumerate(av)]
                formula = f"a rounded to {int(decimals)} decimals"
            else:
                if step is None or isinstance(step, bool) or not float(step) > 0:
                    raise _Refuse("missing_input", f"{operation} needs a step greater than zero.")
                rows = [{"label": label(i), "a": x, "value": _clean(_step_round(x, float(step), operation))}
                        for i, x in enumerate(av)]
                formula = f"{_ROUND[operation]} (step {float(step):g})"
            return _vector_or_scalar(common, formula, rows)
        if operation in _REDUCE:
            if operation == "count":
                return tool_success(TOOL, display=f"count = {n}", **common, formula=_REDUCE[operation], result=n)
            if operation == "total":
                total = _clean(math.fsum(av))
                return tool_success(TOOL, display=f"total = {total:g}", **common, formula=_REDUCE[operation], result=total)
            best = min(av) if operation == "min" else max(av)
            at = [label(i) for i, x in enumerate(av) if x == best] if row_labels else []
            return tool_success(
                TOOL, display=f"{operation} = {best:g}" + (f" at {', '.join(at)}" if at else ""), **common,
                formula=_REDUCE[operation], result=best, at_labels=at, tied=len(at) > 1)
        if threshold is None or isinstance(threshold, bool):
            raise _Refuse("missing_input", f"{operation} needs threshold.")
        low = float(threshold)
        if operation == "keep_between":
            if upper is None or isinstance(upper, bool) or float(upper) < low:
                raise _Refuse("missing_input", "keep_between needs upper, not below threshold.")
            test, condition = (lambda x: low <= x <= float(upper)), f"{low:g} <= a <= {float(upper):g}"
        else:
            test = {"keep_above": lambda x: x > low, "keep_at_least": lambda x: x >= low,
                    "keep_below": lambda x: x < low, "keep_at_most": lambda x: x <= low}[operation]
            condition = _FILTER[operation].replace("threshold", f"{low:g}")
        rows = [{"label": label(i) if row_labels else "1", "value": x} for i, x in enumerate(av) if test(x)]
        return tool_success(
            TOOL, display=f"{len(rows)} of {n} values meet {condition}", **common, formula=condition,
            kept_count=len(rows), dropped_count=n - len(rows), kept_labels=[row["label"] for row in rows][:200],
            rows=rows)
    except _Refuse as refusal:
        return tool_error(TOOL, refusal.message, error_code=refusal.code, **refusal.data)


def _vector_or_scalar(common: dict[str, Any], formula: str, rows: list[dict[str, Any]]) -> str:
    if len(rows) == 1:
        row = rows[0]
        shown = f"{formula}: " + (f"a = {row['a']:g}, b = {row['b']:g}" if "b" in row else f"a = {row['a']:g}")
        return tool_success(TOOL, display=f"{shown}; result = {row['value']:g}", **common, formula=formula,
                            a=row["a"], **({"b": row["b"]} if "b" in row else {}), result=row["value"],
                            **({"label": row["label"]} if row["label"] else {}))
    return tool_success(TOOL, display=f"{formula} for {len(rows)} values; column 'value' holds the results",
                        **common, formula=formula, rows=rows)
