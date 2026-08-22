"""TEXT_CHUNKING_SPEC.v1 text gold. Fixtures only. No gold v1. No scores."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import research, text_gold
from dissolve.gold_ensemble import GoldEnsembleError

SUBJ = "CALZXQ"
QUAL = "DODZXQ"
VAL = "16p2wt"


def _text() -> str:
    return (
        f"Methods. {SUBJ} uses the PE model. "
        + ("word " * 60)
        + f"The reference feed is {QUAL} at 120 C. "
        + ("note " * 80)
        + f"The reported input is {VAL} for that calibration."
    )


def _raw(**overrides):
    quote = _text()[len("Methods. "):]
    row = {
        "fact_class": "model_calibration",
        "needles": {"subject": SUBJ, "qualifier": QUAL, "value": VAL},
        "evidence_quote": quote,
        "section": "Methods",
        "query": "What reference input is used for the PE model?",
    }
    row.update(overrides)
    return row


def test_needle_span_is_first_to_last_inside_quote():
    text = _text()
    start = text.find(SUBJ)
    end = text.find(VAL) + len(VAL)
    located = text_gold.locate_needles(
        text, {"subject": SUBJ, "qualifier": QUAL, "value": VAL},
        span_start=start, span_end=end,
    )
    assert located is not None
    assert located["needle_span_chars"] == (text.find(VAL) + len(VAL)) - text.find(SUBJ)
    assert located["needles_separated"] is True
    assert located["needle_span_chars"] > 200


def test_adjacent_needles_are_not_separated():
    text = f"{SUBJ} {QUAL} {VAL}"
    located = text_gold.locate_needles(
        text, {"subject": SUBJ, "qualifier": QUAL, "value": VAL},
        span_start=0, span_end=len(text),
    )
    assert located is not None
    assert located["needles_separated"] is False


def test_histogram_all_short_is_refused():
    facts = [{"needle_span_chars": 40}, {"needle_span_chars": 90}]
    histogram = text_gold.span_histogram(facts)
    assert histogram["all_short"] is True
    assert histogram["buckets"]["<200"]["n"] == 2
    with pytest.raises(GoldEnsembleError) as error:
        text_gold.refuse_all_short(histogram)
    assert error.value.code == "gold_spans_all_short"


def test_histogram_mixed_spans_is_kept():
    facts = [
        {"needle_span_chars": 40},
        {"needle_span_chars": 350},
        {"needle_span_chars": 900},
        {"needle_span_chars": 1800},
    ]
    histogram = text_gold.span_histogram(facts)
    assert histogram["all_short"] is False
    assert histogram["buckets"]["<200"]["n"] == 1
    assert histogram["buckets"]["200-600"]["n"] == 1
    assert histogram["buckets"]["600-1500"]["n"] == 1
    assert histogram["buckets"][">1500"]["n"] == 1
    text_gold.refuse_all_short(histogram)


def test_finalize_drops_scores_and_bound_fact_class():
    text = _text()
    assert text_gold.finalize_fact(
        _raw(contain_bound_fact=True),
        canonical_text=text, paper_sha256="aa", paper_status="indexed",
        read_by=text_gold.READER_MODEL, confirmed_by=text_gold.CONFIRMER_MODEL,
        fact_id="tg-x-001",
    ) is None
    assert text_gold.finalize_fact(
        _raw(scoring_class="bound_fact"),
        canonical_text=text, paper_sha256="aa", paper_status="indexed",
        read_by=text_gold.READER_MODEL, confirmed_by=text_gold.CONFIRMER_MODEL,
        fact_id="tg-x-001",
    ) is None
    fact = text_gold.finalize_fact(
        _raw(),
        canonical_text=text, paper_sha256="aa", paper_status="indexed",
        read_by=text_gold.READER_MODEL, confirmed_by=text_gold.CONFIRMER_MODEL,
        fact_id="tg-x-001",
    )
    assert fact is not None
    assert "scoring_class" not in fact
    assert fact["status"] == "gold_unsealed"
    assert fact["kind"] == "prose"
    assert fact["read_by"] != fact["confirmed_by"]


def test_same_model_twice_is_refused():
    with pytest.raises(GoldEnsembleError) as error:
        text_gold.finalize_fact(
            _raw(),
            canonical_text=_text(), paper_sha256="aa", paper_status="indexed",
            read_by=text_gold.READER_MODEL, confirmed_by=text_gold.READER_MODEL,
            fact_id="tg-x-001",
        )
    assert error.value.code == "same_text_model_twice"


def test_mint_drops_disagreement_and_publishes_histogram():
    text = _text()
    extract = {
        "facts": [
            _raw(),
            _raw(needles={"subject": SUBJ, "qualifier": "NOPE", "value": VAL}),
        ]
    }
    confirm = {"verdicts": [{"index": 0, "confirm": True}, {"index": 1, "confirm": False}]}
    calls = []

    def runner(*, command, cwd, prompt, timeout):
        calls.append(command[command.index("--model") + 1])
        if "gold reader" in prompt:
            return json.dumps(extract)
        return json.dumps(confirm)

    paper = text_gold.mint_one_paper(
        canonical={"canonical_text": text},
        paper_sha256="ab" * 32,
        paper_status="indexed",
        paper_index=0,
        runner=runner,
    )
    assert paper["n_kept"] == 1
    assert paper["n_dropped"] == 1
    assert paper["read_by"] == text_gold.READER_MODEL
    assert paper["confirmed_by"] == text_gold.CONFIRMER_MODEL
    assert calls == [text_gold.READER_MODEL, text_gold.CONFIRMER_MODEL]
    assert paper["span_histogram"]["n_facts"] == 1
    assert paper["span_histogram"]["all_short"] is False
    gold = text_gold.assemble_gold([paper])
    assert gold["sealed"] is False
    assert gold["seal_forbidden"] is True
    assert "span_histogram" in gold
    assert gold["n_facts"] == 1


def test_assemble_refuses_all_short_gold():
    short = {
        "facts": [{"needle_span_chars": 10}, {"needle_span_chars": 20}],
        "paper_sha256": "x",
    }
    with pytest.raises(GoldEnsembleError) as error:
        text_gold.assemble_gold([short])
    assert error.value.code == "gold_spans_all_short"


def test_run_corpus_writes_histogram_before_any_score(tmp_path):
    text = _text()
    extract = {"facts": [_raw()]}
    confirm = {"verdicts": [{"index": 0, "confirm": True}]}

    def runner(*, command, cwd, prompt, timeout):
        if "gold reader" in prompt:
            return json.dumps(extract)
        return json.dumps(confirm)

    paper_sha = "ab" * 32
    canon_dir = tmp_path / "canonical"
    canon_dir.mkdir()
    body = {"canonical_text": text, "source_pdf_sha256": paper_sha}
    canon_path = canon_dir / f"{paper_sha}.v1.json"
    canon_path.write_text(json.dumps(body))
    from dissolve.gold_ensemble import file_sha256
    digest = file_sha256(canon_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "documents": [{
            "pdf_sha256": paper_sha,
            "canonical_sha256": digest,
            "status": "indexed",
            "filename": "probe.pdf",
        }]
    }))
    out = tmp_path / "out"
    monkey_canon = text_gold.CANONICAL_DIR
    monkey_manifest = text_gold.MANIFEST_PATH
    text_gold.CANONICAL_DIR = canon_dir
    text_gold.MANIFEST_PATH = manifest
    try:
        result = text_gold.run_corpus(out_dir=out, runner=runner, timeout=5)
    finally:
        text_gold.CANONICAL_DIR = monkey_canon
        text_gold.MANIFEST_PATH = monkey_manifest
    assert (out / "NEEDLE_SPAN_HISTOGRAM.v1.json").is_file()
    assert (out / "GOLD.text.v1.unsealed.json").is_file()
    assert not (out / "GOLD.v2.json").exists()
    hist = json.loads((out / "NEEDLE_SPAN_HISTOGRAM.v1.json").read_text())
    assert "buckets" in hist
    assert result["all_short"] is False
    gold = json.loads((out / "GOLD.text.v1.unsealed.json").read_text())
    assert "contain_bound_fact" not in gold
    assert gold["span_histogram"]["n_facts"] == gold["n_facts"]


def test_run_corpus_resumes_without_rebilling(tmp_path):
    text = _text()
    extract = {"facts": [_raw()]}
    confirm = {"verdicts": [{"index": 0, "confirm": True}]}
    calls = []

    def runner(*, command, cwd, prompt, timeout):
        calls.append(1)
        if "gold reader" in prompt:
            return json.dumps(extract)
        return json.dumps(confirm)

    paper_sha = "cd" * 32
    canon_dir = tmp_path / "canonical"
    canon_dir.mkdir()
    canon_path = canon_dir / f"{paper_sha}.v1.json"
    canon_path.write_text(json.dumps({"canonical_text": text, "source_pdf_sha256": paper_sha}))
    from dissolve.gold_ensemble import file_sha256
    digest = file_sha256(canon_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "documents": [{
            "pdf_sha256": paper_sha,
            "canonical_sha256": digest,
            "status": "indexed",
            "filename": "probe.pdf",
        }]
    }))
    out = tmp_path / "out"
    prev_canon, prev_manifest = text_gold.CANONICAL_DIR, text_gold.MANIFEST_PATH
    text_gold.CANONICAL_DIR = canon_dir
    text_gold.MANIFEST_PATH = manifest
    try:
        first = text_gold.run_corpus(out_dir=out, runner=runner, timeout=5)
        n_first = len(calls)
        second = text_gold.run_corpus(out_dir=out, runner=runner, timeout=5)
    finally:
        text_gold.CANONICAL_DIR = prev_canon
        text_gold.MANIFEST_PATH = prev_manifest
    assert first["n_facts"] == second["n_facts"]
    assert len(calls) == n_first


def test_cli_argv_strips_embedded_nul(monkeypatch):
    seen: dict[str, list[str]] = {}

    class _Result:
        returncode = 0
        stdout = '{"facts":[]}'
        stderr = ""

    def fake_run(args, **_kwargs):
        seen["args"] = list(args)
        return _Result()

    monkeypatch.setattr(text_gold.subprocess, "run", fake_run)
    prompt = "canonical_text:\nhello\x00world"
    out = text_gold.run_named_text_model(
        model=text_gold.READER_MODEL,
        prompt=prompt,
        cwd=Path("/tmp"),
        timeout=5,
    )
    assert "\x00" not in seen["args"][-1]
    assert seen["args"][-1] == "canonical_text:\nhelloworld"
    assert out == '{"facts":[]}'
    dirty = _text() + "\x00"
    bound = text_gold.locate_needles(
        dirty,
        {"subject": SUBJ, "qualifier": QUAL, "value": VAL},
        span_start=0,
        span_end=len(dirty),
    )
    assert bound is not None


def test_text_gold_does_not_call_docling_dense_or_vision(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("gold mint must not parse, embed, or vision-read")

    monkeypatch.setattr(research, "_run_docling", boom)
    monkeypatch.setattr(research, "_dense_vectors", boom)
    text = _text()
    located = text_gold.locate_needles(
        text, {"subject": SUBJ, "qualifier": QUAL, "value": VAL},
        span_start=0, span_end=len(text),
    )
    assert located is not None
