"""TEXT_CHUNKING_SPEC.v1 metrics. Fixtures. BM25 only. No gold v1. No F1."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import research, text_chunk_metrics, text_chunking, text_gold

SUBJ = "CALZXQ"
QUAL = "DODZXQ"
VAL = "16p2wt"
OTHER = "OTHZXQ"


def _canon_and_facts():
    pad_short = "x " * 20
    pad_long = "y " * 400
    text = (
        f"{SUBJ} opens. {pad_short}{QUAL} mid. {pad_short}{VAL} close. "
        f"{OTHER} other. {pad_long}end."
    )
    first = text.find(SUBJ)
    last = text.find(VAL) + len(VAL)
    far_first = text.find(OTHER)
    far_last = text.find("end.") + 4
    facts = [
        {
            "fact_id": "short-1",
            "needles": {"subject": SUBJ, "qualifier": QUAL, "value": VAL},
            "query": f"{SUBJ} {VAL}",
            "needle_span_chars": last - first,
            "needle_first_char": first,
            "needle_last_char": last,
        },
        {
            "fact_id": "long-1",
            "needles": {"subject": OTHER, "qualifier": "end.", "value": "y y"},
            "query": OTHER,
            "needle_span_chars": far_last - far_first,
            "needle_first_char": far_first,
            "needle_last_char": far_last,
        },
    ]
    return {"canonical_text": text, "blocks": []}, facts


def test_none_offsets_are_not_span_preserved():
    _, facts = _canon_and_facts()
    chunk = {"body": "anything", "char_start": None, "char_end": None}
    assert text_chunking.offsets_usable(chunk) is False
    assert text_chunk_metrics.needle_span_preserved(chunk, facts[0]) is False


def test_score_is_by_bucket_and_f1_is_none():
    canon, facts = _canon_and_facts()
    chunks = text_chunking.chunk_t1(canon, size=400, overlap_frac=0.0)
    scored = text_chunk_metrics.score_strategy(chunks, facts, strategy="T1", params={"size": 400})
    assert scored["f1"] is None
    assert set(scored["by_bucket"]) == set(text_gold.SPAN_BUCKETS)
    assert scored["n_facts"] == 2
    short_bucket = text_gold.span_bucket(facts[0]["needle_span_chars"])
    long_bucket = text_gold.span_bucket(facts[1]["needle_span_chars"])
    assert short_bucket != long_bucket
    assert scored["by_bucket"][short_bucket]["n_facts"] == 1
    assert scored["by_bucket"][long_bucket]["n_facts"] == 1


def test_small_window_keeps_short_and_drops_long():
    canon, facts = _canon_and_facts()
    chunks = text_chunking.chunk_t1(canon, size=400, overlap_frac=0.0)
    short, long = facts
    assert any(text_chunk_metrics.contain_bound_fact(c["body"], short["needles"]) for c in chunks)
    assert not any(text_chunk_metrics.contain_bound_fact(c["body"], long["needles"]) for c in chunks)
    scored = text_chunk_metrics.score_strategy(chunks, facts, strategy="T1")
    assert scored["n_contain_bound_fact"] == 1


def test_t1_is_control_for_t2_at_matched_pair():
    canon, facts = _canon_and_facts()
    t1 = text_chunk_metrics.score_strategy(
        text_chunking.chunk_t1(canon, size=800, overlap_frac=0.15),
        facts, strategy="T1", params={"size": 800, "overlap_frac": 0.15},
    )
    t2 = text_chunk_metrics.score_strategy(
        text_chunking.chunk_t2(canon, size=800, overlap_frac=0.15),
        facts, strategy="T2", params={"size": 800, "overlap_frac": 0.15},
    )
    pairs = text_chunk_metrics.t1_t2_control_rows([t1, t2])
    assert len(pairs) == 1
    assert pairs[0]["size"] == 800
    assert pairs[0]["overlap_frac"] == 0.15
    assert "tied_contain" in pairs[0]


def test_score_refuses_missing_or_all_short_histogram():
    import pytest
    from dissolve.gold_ensemble import GoldEnsembleError

    with pytest.raises(GoldEnsembleError) as error:
        text_chunk_metrics.require_histogram_before_score({"facts": []})
    assert error.value.code == "histogram_missing"
    with pytest.raises(GoldEnsembleError) as error:
        text_chunk_metrics.require_histogram_before_score({
            "span_histogram": text_gold.span_histogram([{"needle_span_chars": 10}]),
        })
    assert error.value.code == "gold_spans_all_short"


def test_run_arm_does_not_score_whole_gold_on_zero_fact_paper():
    canon, facts = _canon_and_facts()
    canon["source_pdf_sha256"] = "aa" * 32
    tagged = [{**facts[0], "paper_sha256": "bb" * 32}]
    chunks = text_chunking.chunk_t1(canon, size=400, overlap_frac=0.0)
    scored = text_chunk_metrics.run_arm(
        canon, tagged, strategy="T1", params={"size": 400}, chunks=chunks,
    )
    assert scored["n_facts"] == 0
    assert scored["n_contain_bound_fact"] == 0


def test_metrics_are_bm25_not_dense(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("retrievable@5 must not call _dense_vectors")

    monkeypatch.setattr(research, "_dense_vectors", boom)
    canon, facts = _canon_and_facts()
    chunks = text_chunking.chunk_t1(canon, size=800, overlap_frac=0.0)
    scored = text_chunk_metrics.score_strategy(chunks, facts, strategy="T1")
    assert "retrievable@5" in scored["facts"][0]
    assert scored["f1"] is None
    assert scored["retrieval_ks"] == [1, 3, 5, 10, 20]
    assert scored["n_retrievable_at_5"] == scored["n_retrievable_at_k"]["5"]


def test_retrieval_curve_is_not_a_single_point():
    needles = {"subject": SUBJ, "qualifier": QUAL, "value": VAL}
    filler = {"body": "zzzz filler with no needles"}
    bound = {"body": f"{SUBJ} {QUAL} {VAL}"}
    # Relevant chunk sits at rank 4. retr@5 is a hit; retr@1 and retr@3 are misses.
    ranked = [filler, filler, filler, bound, filler, filler]
    retr, prec = text_chunk_metrics.retrieval_at_ks(ranked, needles)
    assert list(retr) == ["1", "3", "5", "10", "20"]
    assert retr["1"] is False
    assert retr["3"] is False
    assert retr["5"] is True
    assert retr["10"] is True
    assert retr["20"] is True
    assert prec["1"] == 0.0
    assert prec["3"] == 0.0
    assert prec["5"] == 1 / 5
    assert prec["10"] == 1 / 10
    assert prec["20"] == 1 / 20
    # Standard P@k still divides by k when k exceeds the list.
    assert all(0.0 <= prec[key] <= 1.0 for key in prec)


def test_recall_at_k_is_monotone_and_precision_is_binds_over_k():
    needles = {"subject": SUBJ, "qualifier": QUAL, "value": VAL}
    bound = {"body": f"{SUBJ} {QUAL} {VAL}"}
    other = {"body": "no needles here"}
    ranked = [bound, other, bound, other]
    retr, prec = text_chunk_metrics.retrieval_at_ks(ranked, needles)
    labels = ["1", "3", "5", "10", "20"]
    hits = [retr[key] for key in labels]
    assert hits == sorted(hits)  # False cannot follow True
    assert prec["1"] == 1.0
    assert prec["3"] == 2 / 3
    assert prec["5"] == 2 / 5


def test_retrievable_at_5_matches_production_bm25_top5():
    canon, facts = _canon_and_facts()
    chunks = text_chunking.chunk_t1(canon, size=800, overlap_frac=0.0)
    fact = facts[0]
    query = fact["query"]
    needles = fact["needles"]
    top5 = research._bm25_top5(query, chunks)
    expected = any(
        text_chunk_metrics.contain_bound_fact(chunk["body"], needles) for chunk in top5
    )
    assert text_chunk_metrics.retrievable_at_5(query, chunks, needles) is expected
    ranked = text_chunk_metrics.bm25_ranked(query, chunks)
    assert [c["body"] for c in ranked[:5]] == [c["body"] for c in top5]
