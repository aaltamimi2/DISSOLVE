"""TEXT_CHUNKING_SPEC.v1 metrics. Fixtures. BM25 only. No gold v1. No F1."""
from __future__ import annotations

import json
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


def test_cross_fact_hits_cut_by_span_bucket():
    short_needles = {"subject": SUBJ, "qualifier": QUAL, "value": VAL}
    long_needles = {"subject": OTHER, "qualifier": "end.", "value": "y y"}
    shared = {"body": f"{SUBJ} {QUAL} {VAL} {OTHER} end. y y"}
    facts = [
        {
            "needles": short_needles,
            "needle_span_chars": 80,
            "query": SUBJ,
            "needle_first_char": 0,
            "needle_last_char": 10,
        },
        {
            "needles": long_needles,
            "needle_span_chars": 900,
            "query": OTHER,
            "needle_first_char": 0,
            "needle_last_char": 20,
        },
    ]
    scored = text_chunk_metrics.score_strategy([shared], facts, strategy="T5")
    assert scored["cross_fact_hits"] == 2
    assert scored["by_bucket"]["<200"]["cross_fact_hits"] == 1
    assert scored["by_bucket"]["600-1500"]["cross_fact_hits"] == 1
    assert scored["by_bucket"]["200-600"]["cross_fact_hits"] == 0
    assert scored["recall_at_k"]["5"] == scored["n_retrievable_at_k"]["5"] / scored["n_facts"]


def test_build_retrieval_curves_is_per_bucket_and_has_no_needles():
    gold = {
        "n_papers": 1,
        "n_facts": 2,
        "span_histogram": text_gold.span_histogram([
            {"needle_span_chars": 80},
            {"needle_span_chars": 900},
        ]),
    }
    arm = {
        "strategy": "T5",
        "params": {"target": 1400},
        "n_chunks": 4,
        "n_facts": 2,
        "n_contain_bound_fact": 2,
        "n_needle_span_preserved": 1,
        "n_retrievable_at_k": {"1": 1, "3": 2, "5": 2, "10": 2, "20": 2},
        "precision_at_k": {"1": 1.0, "3": 0.5, "5": 0.4, "10": 0.2, "20": 0.1},
        "cross_fact_hits": 1,
        "by_bucket": {
            "<200": {
                "n_facts": 1,
                "n_contain_bound_fact": 1,
                "n_needle_span_preserved": 1,
                "n_retrievable_at_k": {"1": 1, "3": 1, "5": 1, "10": 1, "20": 1},
                "precision_at_k": {"1": 1.0, "3": 0.3, "5": 0.2, "10": 0.1, "20": 0.05},
                "cross_fact_hits": 0,
            },
            "200-600": text_chunk_metrics._empty_bucket(),
            "600-1500": {
                "n_facts": 1,
                "n_contain_bound_fact": 1,
                "n_needle_span_preserved": 0,
                "n_retrievable_at_k": {"1": 0, "3": 1, "5": 1, "10": 1, "20": 1},
                "precision_at_k": {"1": 0.0, "3": 0.3, "5": 0.2, "10": 0.1, "20": 0.05},
                "cross_fact_hits": 1,
            },
            ">1500": text_chunk_metrics._empty_bucket(),
        },
    }
    artifact = text_chunk_metrics.build_retrieval_curves(
        gold=gold, arms=[arm], gold_sha256="ab" * 32,
    )
    assert artifact["schema"] == "dissolve.text-chunk-curves.retrieval.v1"
    assert artifact["ranker"] == "bm25_body"
    assert artifact["embedder_in_retrieval"] is False
    assert artifact["f1"] is None
    assert artifact["retrieval_ks"] == [1, 3, 5, 10, 20]
    buckets = {row["bucket"] for row in artifact["series"]}
    assert buckets == {"all", "<200", "200-600", "600-1500", ">1500"}
    all_row = next(row for row in artifact["series"] if row["bucket"] == "all")
    assert all_row["cross_fact_hits"] == 1
    assert all_row["recall_at_k"]["5"] == 2 / 2
    mid = next(row for row in artifact["series"] if row["bucket"] == "600-1500")
    assert mid["cross_fact_hits"] == 1
    assert mid["n_chunks"] == 4
    blob = json.dumps(artifact)
    assert "needles" not in blob
    assert "canonical_text" not in blob
    assert "query" not in blob
    assert "fact_id" not in blob


def test_emit_refuses_to_overwrite_leaked_curves_path(tmp_path):
    gold = {
        "n_papers": 1,
        "n_facts": 1,
        "span_histogram": text_gold.span_histogram([{"needle_span_chars": 400}]),
        "facts": [],
    }
    gold_path = tmp_path / "gold.json"
    gold_path.write_text("{}\n")
    import pytest
    from dissolve.gold_ensemble import GoldEnsembleError
    with pytest.raises(GoldEnsembleError) as error:
        text_chunk_metrics.emit_retrieval_curves(
            gold=gold,
            canonicals=[],
            gold_path=gold_path,
            out_path=text_chunk_metrics.CURVES_PATH,
        )
    assert error.value.code == "protected_persist"


def test_emit_refuses_to_overwrite_point_sweep(tmp_path):
    gold = {
        "n_papers": 1,
        "n_facts": 1,
        "span_histogram": text_gold.span_histogram([{"needle_span_chars": 400}]),
        "facts": [],
    }
    gold_path = tmp_path / "gold.json"
    gold_path.write_text("{}\n")
    import pytest
    from dissolve.gold_ensemble import GoldEnsembleError
    with pytest.raises(GoldEnsembleError) as error:
        text_chunk_metrics.emit_retrieval_curves(
            gold=gold,
            canonicals=[],
            gold_path=gold_path,
            out_path=text_chunk_metrics.POINT_SWEEP_PATH,
        )
    assert error.value.code == "protected_persist"


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


def test_wilson_interval_matches_the_v3_formula_and_is_null_on_empty():
    empty = text_chunk_metrics.wilson_interval(0, 0)
    assert empty == {"lo": None, "hi": None}
    lo_hi = text_chunk_metrics.wilson_interval(217, 228)
    assert abs(lo_hi["lo"] - 0.9156883608389312) <= 1e-12
    assert abs(lo_hi["hi"] - 0.9728498714852543) <= 1e-12
    assert lo_hi["hi"] <= 1.0
    sure = text_chunk_metrics.wilson_interval(29, 29)
    assert sure["hi"] == 1.0
    assert sure["lo"] > 0.8


def test_overlapping_intervals_are_tied_and_a_gap_of_two_facts_does_not_separate():
    t6 = text_chunk_metrics.wilson_interval(219, 228)
    t5 = text_chunk_metrics.wilson_interval(217, 228)
    t1 = text_chunk_metrics.wilson_interval(216, 228)
    assert text_chunk_metrics.intervals_overlap(t6, t5)
    assert text_chunk_metrics.intervals_overlap(t5, t1)
    assert text_chunk_metrics.intervals_overlap(t6, t1)
    assert text_chunk_metrics.interval_separates_above(t6, t5) is False


def test_index_i_drops_held_out_chunks():
    indexed = [{"body": f"{SUBJ} {QUAL} {VAL}", "char_start": 0, "char_end": 10}]
    held = [{"body": f"{OTHER} must not be searchable", "char_start": 0, "char_end": 10}]
    index = text_chunk_metrics.build_index_i(
        {"aa" * 32: indexed, "bb" * 32: held},
        indexed_shas={"aa" * 32},
        paper_order=["aa" * 32, "bb" * 32],
    )
    assert len(index) == 1
    assert index[0]["paper_sha256"] == "aa" * 32
    assert OTHER not in index[0]["body"]


def test_v3_scores_refuse_beside_recall_on_shared_index():
    sha_i = "aa" * 32
    sha_h = "bb" * 32
    index = text_chunk_metrics.tag_chunks(
        [{"body": f"{SUBJ} {QUAL} {VAL} unique indexed", "char_start": 0, "char_end": 40}],
        sha_i,
    )
    fire = [{
        "fact_id": "f-i",
        "paper_sha256": sha_i,
        "paper_status": "indexed",
        "needles": {"subject": SUBJ, "qualifier": QUAL, "value": VAL},
        "query": f"{SUBJ} {VAL}",
        "needle_span_chars": 80,
        "needle_first_char": 0,
        "needle_last_char": 20,
    }]
    refuse = [{
        "fact_id": "f-h",
        "paper_sha256": sha_h,
        "paper_status": "held_out",
        "needles": {"subject": OTHER, "qualifier": "end.", "value": "y y"},
        "query": OTHER,
        "needle_span_chars": 80,
        "needle_first_char": 0,
        "needle_last_char": 20,
    }]
    scored = text_chunk_metrics.score_index_arm(
        strategy="T5",
        params={"target": 1400},
        index=index,
        chunks_by_paper={sha_i: index, sha_h: [{"body": f"{OTHER} end. y y"}]},
        must_fire=fire,
        must_refuse=refuse,
        all_facts=fire + refuse,
    )
    all_row = next(row for row in scored["series"] if row["bucket"] == "all")
    assert all_row["n_facts"] == 1
    assert all_row["n_held_out_facts"] == 1
    assert all_row["n_retrievable_at_k"]["5"] == 1
    assert all_row["n_must_refuse_at_k"]["5"] == 1
    assert all_row["n_leaks_at_k"]["5"] == 0
    assert all_row["recall_ci_at_k"]["5"]["lo"] is not None
    assert all_row["must_refuse_ci_at_k"]["5"]["lo"] is not None
    assert all_row["f1"] if "f1" in all_row else True
    empty = next(row for row in scored["series"] if row["bucket"] == ">1500")
    assert empty["n_facts"] == 0
    assert empty["recall_at_k"]["5"] is None
    assert empty["must_refuse_at_k"]["5"] is None
    assert empty["recall_ci_at_k"]["5"] == {"lo": None, "hi": None}


def test_held_out_chunk_in_index_is_refused():
    sha_h = "bb" * 32
    bad = text_chunk_metrics.tag_chunks([{"body": "leak"}], sha_h)
    try:
        text_chunk_metrics.score_index_arm(
            strategy="T5",
            params={"target": 1400},
            index=bad,
            chunks_by_paper={sha_h: bad},
            must_fire=[],
            must_refuse=[{"fact_id": "h", "paper_sha256": sha_h, "needles": {}, "query": "x"}],
            all_facts=[],
        )
    except text_gold.TextGoldError as error:
        assert error.code == "held_out_in_index"
    else:
        raise AssertionError("held-out SHA in I must refuse")


def test_error_class_priority_is_gold_then_span_then_split_then_lexical():
    text = f"{SUBJ} {QUAL} {VAL} " + ("z" * 50)
    first = text.find(SUBJ)
    last = text.find(VAL) + len(VAL)
    own = [{"body": text, "char_start": 0, "char_end": len(text)}]
    gold_broken = text_chunk_metrics.classify_k10_miss(
        {
            "fact_id": "g",
            "needles": {"subject": "MISSINGZX", "qualifier": QUAL, "value": VAL},
            "needle_span_chars": 10,
            "needle_first_char": 0,
            "needle_last_char": 5,
        },
        canonical_text=text,
        own_chunks=own,
    )
    assert gold_broken["class"] == "ambiguous_gold"
    span_miss = text_chunk_metrics.classify_k10_miss(
        {
            "fact_id": "s",
            "needles": {"subject": SUBJ, "qualifier": QUAL, "value": VAL},
            "needle_span_chars": 10_000,
            "needle_first_char": first,
            "needle_last_char": last,
        },
        canonical_text=text,
        own_chunks=[{"body": "short", "char_start": 0, "char_end": 5}],
    )
    assert span_miss["class"] == "span_exceeds_chunk"
    split = text_chunk_metrics.classify_k10_miss(
        {
            "fact_id": "c",
            "needles": {"subject": SUBJ, "qualifier": QUAL, "value": VAL},
            "needle_span_chars": last - first,
            "needle_first_char": first,
            "needle_last_char": last,
        },
        canonical_text=text,
        own_chunks=[
            {"body": SUBJ + (" a" * 80), "char_start": 0, "char_end": 200},
            {"body": QUAL + (" b" * 80), "char_start": 200, "char_end": 400},
        ],
    )
    assert split["class"] == "needles_split"
    lexical = text_chunk_metrics.classify_k10_miss(
        {
            "fact_id": "l",
            "needles": {"subject": SUBJ, "qualifier": QUAL, "value": VAL},
            "needle_span_chars": last - first,
            "needle_first_char": first,
            "needle_last_char": last,
        },
        canonical_text=text,
        own_chunks=own,
    )
    assert lexical["class"] == "lexical_mismatch"


def test_v3_curves_artifact_has_no_needles_or_fact_ids():
    gold = {
        "n_papers": 2,
        "n_facts": 2,
        "span_histogram": text_gold.span_histogram([
            {"needle_span_chars": 80},
            {"needle_span_chars": 90},
        ]),
        "papers": [
            {"paper_sha256": "aa" * 32, "paper_status": "indexed"},
            {"paper_sha256": "bb" * 32, "paper_status": "held_out"},
        ],
        "facts": [
            {"fact_id": "keep-out", "paper_sha256": "aa" * 32, "paper_status": "indexed", "needle_span_chars": 80},
            {"fact_id": "hold-out", "paper_sha256": "bb" * 32, "paper_status": "held_out", "needle_span_chars": 90},
        ],
    }
    split = text_chunk_metrics.split_gold(gold)
    assert len(split["indexed_shas"]) == 1
    assert len(split["held_shas"]) == 1
    assert len(split["must_fire"]) == 1
    assert len(split["must_refuse"]) == 1
    report = {
        "split": split,
        "indexed_hist": text_gold.span_histogram(split["must_fire"]),
        "held_hist": text_gold.span_histogram(split["must_refuse"]),
        "all_hist": gold["span_histogram"],
        "n_value_collisions": 0,
        "holdout_too_thin": False,
        "series": [],
        "tie_groups_at_k": {},
        "t1_vs_t2": [],
    }
    artifact = text_chunk_metrics.build_retrieval_curves_v3(
        gold=gold, report=report, gold_sha256="ab" * 32,
    )
    assert artifact["schema"] == "dissolve.text-chunk-curves.retrieval.v3"
    assert artifact["index"] == "indexed_papers_only"
    assert artifact["ci_method"] == "wilson_score"
    assert artifact["z"] == text_chunk_metrics.WILSON_Z
    assert artifact["f1"] is None
    assert artifact["n_indexed_facts"] == 1
    assert artifact["n_held_out_facts"] == 1
    assert text_chunk_metrics._contains_forbidden_keys(
        artifact, {"needles", "canonical_text", "evidence_quote", "query", "fact_id"},
    ) is False
