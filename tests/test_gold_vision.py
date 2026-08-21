"""C5 vision bind. Mocked CLIs. Gold v1 unmoved. No page look by the test."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import gold_ensemble, gold_vision, research

TOKEN_P = "PEZXQ"
TOKEN_S = "SOLZXQ"
TOKEN_T = "81.25"
TOKEN_V = "12.875"


def test_spend_auth_digest_matches():
    assert gold_vision.require_vision_spend() == gold_ensemble.VISION_SPEND_SHA256


def test_page_image_kwarg_is_not_a_channel():
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_vision.channel_c(image_dir=Path("/tmp"), pages=[1], page_image=b"no")
    assert caught.value.code == "vision_not_authorized"


def test_normalize_drops_empty_named_fields():
    raw = {"tables": [{"locus": "Fig. 4", "page": 6, "rows": [{
        "polymer": "", "solvent": "", "temperature": "", "value": "",
        "cells": [TOKEN_P, TOKEN_S, TOKEN_T, TOKEN_V],
    }]}]}
    out = gold_vision.normalize_vision_reading(raw)
    assert out["tables"] == []
    assert out["dropped_incomplete_rows"] == 1


def test_normalize_null_is_channel_error():
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_vision.normalize_vision_reading(None)
    assert caught.value.code == "vision_null_reading"


def test_one_json_retry_is_recorded():
    calls = {"n": 0}

    def runner(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "{not-json}"
        return json.dumps({"tables": []})

    out = gold_vision.channel_c(image_dir=Path("/tmp"), pages=[1], runner=runner)
    assert calls["n"] == 2
    assert out["retried_json_fail"] == "vision_json_invalid"
    assert out["reading"]["tables"] == []


def test_second_json_fail_is_not_absorbed():
    def runner(**kwargs):
        return "{not-json}"

    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_vision.channel_c(image_dir=Path("/tmp"), pages=[1], runner=runner)
    assert caught.value.code == "vision_json_invalid"


def test_labeled_columns_recover_needles_from_cells():
    raw = {"tables": [{"columns": ["polymer", "solvent", "temperature", "value"],
                       "rows": [{"polymer": "", "solvent": "", "temperature": "",
                                 "value": "", "cells": [TOKEN_P, TOKEN_S, TOKEN_T, TOKEN_V]}]}]}
    out = gold_vision.normalize_vision_reading(raw)
    assert len(out["tables"][0]["rows"]) == 1
    assert out["tables"][0]["rows"][0]["polymer"] == TOKEN_P


def test_same_model_twice_fails():
    with pytest.raises(gold_ensemble.GoldEnsembleError) as caught:
        gold_vision.assert_distinct_vision_models("same", "same")
    assert caught.value.code == "same_vision_model_twice"


def test_b_and_c_bind_four_needles():
    paper = {"sha256": "ab" * 32, "status": "indexed", "genre": "experimental"}
    candidates = [{
        "fact_id": "tc-1",
        "kind": "table_cell",
        "scoring_class": "bound_fact",
        "status": "awaiting_C",
        "page": 2,
        "locus": "Table 2",
        "evidence_quote": f"{TOKEN_P}    {TOKEN_S}    {TOKEN_T}    {TOKEN_V}",
        "readings": {"A": None, "B": f"{TOKEN_P}    {TOKEN_S}    {TOKEN_T}    {TOKEN_V}", "C": None},
        "needles": {},
        "channels_used": ["B"],
        "sealed": False,
    }]
    channel_c = {
        "read_by": gold_vision.CHANNEL_C_MODEL,
        "reading": {"tables": [{"locus": "Table 2", "page": 2, "rows": [{
            "polymer": TOKEN_P, "solvent": TOKEN_S,
            "temperature": TOKEN_T, "value": TOKEN_V,
            "cells": [TOKEN_P, TOKEN_S, TOKEN_T, TOKEN_V],
        }]}]},
    }
    bound = gold_vision.bind_table_cells(
        paper=paper, candidates=candidates, channel_c_result=channel_c,
    )
    assert bound[0]["status"] == "gold_unsealed"
    assert bound[0]["needles"] == {
        "polymer": TOKEN_P, "solvent": TOKEN_S,
        "temperature": TOKEN_T, "value": TOKEN_V,
    }
    assert bound[0]["read_by"]["C"] == gold_vision.CHANNEL_C_MODEL
    assert bound[0]["sealed"] is False


def test_extra_c_cells_do_not_block_four_needles():
    paper = {"sha256": "ab" * 32, "status": "indexed", "genre": "experimental"}
    line = f"{TOKEN_P}    {TOKEN_S}    {TOKEN_T}    {TOKEN_V}"
    candidates = [{
        "fact_id": "tc-extra",
        "kind": "table_cell",
        "scoring_class": "bound_fact",
        "status": "awaiting_C",
        "page": 2,
        "evidence_quote": line,
        "readings": {"B": line},
        "needles": {},
        "channels_used": ["B"],
    }]
    channel_c = {
        "read_by": gold_vision.CHANNEL_C_MODEL,
        "reading": {"tables": [{"rows": [{
            "polymer": TOKEN_P, "solvent": TOKEN_S,
            "temperature": TOKEN_T, "value": TOKEN_V,
            "cells": ["Table 2", TOKEN_P, TOKEN_S, TOKEN_T, TOKEN_V],
        }]}]},
    }
    bound = gold_vision.bind_table_cells(
        paper=paper, candidates=candidates, channel_c_result=channel_c,
    )
    assert bound[0]["status"] == "gold_unsealed"


def test_degree_spacing_does_not_block_bind():
    paper = {"sha256": "ab" * 32, "status": "indexed", "genre": "experimental"}
    line = f"{TOKEN_P}  {TOKEN_S}  80 °C  {TOKEN_V}"
    candidates = [{
        "fact_id": "tc-deg",
        "kind": "table_cell",
        "scoring_class": "bound_fact",
        "status": "awaiting_C",
        "page": 1,
        "evidence_quote": line,
        "readings": {"B": line},
        "needles": {},
        "channels_used": ["B"],
    }]
    channel_c = {
        "read_by": gold_vision.CHANNEL_C_MODEL,
        "reading": {"tables": [{"rows": [{
            "polymer": TOKEN_P, "solvent": TOKEN_S,
            "temperature": "80°C", "value": TOKEN_V,
            "cells": [],
        }]}]},
    }
    bound = gold_vision.bind_table_cells(
        paper=paper, candidates=candidates, channel_c_result=channel_c,
    )
    assert bound[0]["status"] == "gold_unsealed"


def test_string_existence_still_unbound_after_vision_needles():
    text = f"{TOKEN_P} {TOKEN_S} {TOKEN_T} {TOKEN_V}"
    fact = {
        "fact_id": "se-still",
        "scoring_class": "string_existence",
        "kind": "token",
        "locus": "",
        "page": 1,
        "query": TOKEN_V,
        "needles": {
            "polymer": TOKEN_P, "solvent": TOKEN_S,
            "temperature": TOKEN_T, "value": TOKEN_V,
        },
    }
    from dissolve import research
    chunks = [{
        "chunk_id": "c1", "text": text, "body": text, "header": "",
        "char_start": 0, "char_end": len(text),
    }]
    canonical = {
        "schema": "dissolve.canonical-document.v1",
        "source_pdf_sha256": "ab" * 32,
        "canonical_text": text,
        "blocks": [],
        "tables": [],
        "pages": 1,
        "parser_backend": "fixture",
        "parser_version": "0",
        "fallback_reason": None,
    }
    row = research.score_chunks_against_facts(
        chunks, [fact], canonical, strategy="S2_block_pack",
    )["facts"][0]
    assert row["contain_bound_fact"] is False


def test_cell_agree_without_four_keys_is_awaiting_needles():
    paper = {"sha256": "cd" * 32, "status": "indexed", "genre": "experimental"}
    row_c = {
        "polymer": TOKEN_P, "solvent": "", "temperature": "", "value": "",
        "cells": [TOKEN_P, TOKEN_S], "locus": "Fig. 4", "page": 6,
    }
    row_p = {
        "polymer": TOKEN_P, "solvent": "", "temperature": "", "value": "",
        "cells": [TOKEN_P, TOKEN_S], "locus": "Fig. 4", "page": 6,
    }
    agreed, disputed = gold_vision.bind_figure_facts(
        paper=paper,
        channel_c_result={"read_by": gold_vision.CHANNEL_C_MODEL, "reading": {"tables": [{"rows": [row_c]}]}},
        channel_c_prime_result={
            "read_by": gold_vision.CHANNEL_C_PRIME_MODEL,
            "reading": {"tables": [{"rows": [row_p]}]},
        },
    )
    assert disputed == []
    assert agreed[0]["status"] == "awaiting_needles"
    assert agreed[0]["needles"] == {}
    assert agreed[0]["partial_needles"]["polymer"] == TOKEN_P
    assert research.official_contain_bound_fact_eligible(agreed[0]) is False


def test_leftover_cell_does_not_close_fourth_key():
    """05bc844 / 33e3acf residual: leftover cell is not a named fourth key."""
    paper = {"sha256": "cd" * 32, "status": "indexed", "genre": "experimental"}
    row = {
        "polymer": TOKEN_P, "solvent": TOKEN_S, "temperature": TOKEN_T, "value": "",
        "cells": [TOKEN_P, TOKEN_S, TOKEN_T, TOKEN_V], "locus": "Fig. 4", "page": 6,
    }
    agreed, disputed = gold_vision.bind_figure_facts(
        paper=paper,
        channel_c_result={"read_by": gold_vision.CHANNEL_C_MODEL, "reading": {"tables": [{"rows": [row]}]}},
        channel_c_prime_result={
            "read_by": gold_vision.CHANNEL_C_PRIME_MODEL,
            "reading": {"tables": [{"rows": [row]}]},
        },
    )
    assert disputed == []
    assert agreed[0]["status"] == "awaiting_needles"
    assert agreed[0]["needles"] == {}
    assert agreed[0]["partial_needles"].get("value") in (None, "")
    assert research.official_contain_bound_fact_eligible(agreed[0]) is False


def test_leftover_unit_cell_does_not_become_value():
    paper = {"sha256": "cd" * 32, "status": "indexed", "genre": "experimental"}
    row = {
        "polymer": TOKEN_P, "solvent": TOKEN_S, "temperature": TOKEN_T, "value": "",
        "cells": [TOKEN_P, TOKEN_S, TOKEN_T, "wt%"], "locus": "Fig. 4", "page": 6,
    }
    agreed, disputed = gold_vision.bind_figure_facts(
        paper=paper,
        channel_c_result={"read_by": gold_vision.CHANNEL_C_MODEL, "reading": {"tables": [{"rows": [row]}]}},
        channel_c_prime_result={
            "read_by": gold_vision.CHANNEL_C_PRIME_MODEL,
            "reading": {"tables": [{"rows": [row]}]},
        },
    )
    assert disputed == []
    assert agreed[0]["status"] == "awaiting_needles"
    assert agreed[0]["needles"] == {}
    assert agreed[0]["partial_needles"].get("value") != "wt%"
    assert research.official_contain_bound_fact_eligible(agreed[0]) is False


def test_figure_c_plus_cprime_and_dispute():
    paper = {"sha256": "cd" * 32, "status": "indexed", "genre": "experimental"}
    row = {
        "polymer": TOKEN_P, "solvent": TOKEN_S,
        "temperature": TOKEN_T, "value": TOKEN_V,
        "cells": [TOKEN_V], "locus": "Fig. 4", "page": 6,
    }
    agreed, disputed = gold_vision.bind_figure_facts(
        paper=paper,
        channel_c_result={"read_by": gold_vision.CHANNEL_C_MODEL, "reading": {"tables": [{"rows": [row]}]}},
        channel_c_prime_result={
            "read_by": gold_vision.CHANNEL_C_PRIME_MODEL,
            "reading": {"tables": [{"rows": [row]}]},
        },
    )
    assert len(agreed) == 1
    assert agreed[0]["channels_used"] == ["C", "C_prime"]
    assert agreed[0]["read_by"]["C"] != agreed[0]["read_by"]["C_prime"]
    assert agreed[0]["sealed"] is False
    other = dict(row)
    other["value"] = "0.01"
    other["cells"] = ["0.01"]
    agreed, disputed = gold_vision.bind_figure_facts(
        paper=paper,
        channel_c_result={"read_by": gold_vision.CHANNEL_C_MODEL, "reading": {"tables": [{"rows": [row]}]}},
        channel_c_prime_result={
            "read_by": gold_vision.CHANNEL_C_PRIME_MODEL,
            "reading": {"tables": [{"rows": [other]}]},
        },
    )
    assert agreed == []
    assert disputed[0]["status"] == "disputed"
    assert disputed[0]["readings"]["C"]["value"] == TOKEN_V
    assert disputed[0]["readings"]["C_prime"]["value"] == "0.01"


def test_empty_cprime_does_not_drop_c_rows():
    paper = {"sha256": "cd" * 32, "status": "indexed", "genre": "experimental"}
    row = {
        "polymer": TOKEN_P, "solvent": TOKEN_S,
        "temperature": TOKEN_T, "value": TOKEN_V,
        "cells": [TOKEN_V], "locus": "Fig. 4", "page": 6,
    }
    agreed, disputed = gold_vision.bind_figure_facts(
        paper=paper,
        channel_c_result={"read_by": gold_vision.CHANNEL_C_MODEL, "reading": {"tables": [{"rows": [row]}]}},
        channel_c_prime_result={
            "read_by": gold_vision.CHANNEL_C_PRIME_MODEL,
            "reading": {"tables": []},
        },
    )
    assert agreed == []
    assert len(disputed) == 1
    assert disputed[0]["readings"]["C"]["value"] == TOKEN_V
    assert disputed[0]["readings"]["C_prime"] is None
    assert research.official_contain_bound_fact_eligible(disputed[0]) is False


def test_empty_c_does_not_drop_cprime_rows():
    paper = {"sha256": "cd" * 32, "status": "indexed", "genre": "experimental"}
    row = {
        "polymer": TOKEN_P, "solvent": TOKEN_S,
        "temperature": TOKEN_T, "value": TOKEN_V,
        "cells": [TOKEN_V], "locus": "Fig. 4", "page": 6,
    }
    agreed, disputed = gold_vision.bind_figure_facts(
        paper=paper,
        channel_c_result={"read_by": gold_vision.CHANNEL_C_MODEL, "reading": {"tables": []}},
        channel_c_prime_result={
            "read_by": gold_vision.CHANNEL_C_PRIME_MODEL,
            "reading": {"tables": [{"rows": [row]}]},
        },
    )
    assert agreed == []
    assert len(disputed) == 1
    assert disputed[0]["readings"]["C"] is None
    assert disputed[0]["readings"]["C_prime"]["value"] == TOKEN_V


def test_apply_vision_uses_mocks_not_cli(tmp_path, monkeypatch):
    paper = {
        "sha256": "ef" * 32, "status": "indexed", "genre": "experimental",
        "filename": "x.pdf",
    }
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    assembled = {
        "table_cell_candidates": [],
        "fig4_pages": [1],
        "string_existence": [],
        "disputes": [],
    }
    c_row = {
        "polymer": TOKEN_P, "solvent": TOKEN_S,
        "temperature": TOKEN_T, "value": TOKEN_V,
        "cells": [TOKEN_V], "locus": "Fig. 4", "page": 1,
    }

    def fake_render(pdf_path, pages, dest, dpi=140):
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"page-{pages[0]:04d}.png"
        path.write_bytes(b"png")
        return [path]

    def run_c(**kwargs):
        return {"read_by": gold_vision.CHANNEL_C_MODEL, "reading": {"tables": [{"rows": [c_row]}]}}

    def run_cp(**kwargs):
        return {"read_by": gold_vision.CHANNEL_C_PRIME_MODEL, "reading": {"tables": [{"rows": [c_row]}]}}

    monkeypatch.setattr(gold_vision, "render_pages", fake_render)
    out = gold_vision.apply_vision(
        paper=paper,
        assembled=assembled,
        staged_pdf=pdf,
        work_root=tmp_path / "vis",
        run_c=run_c,
        run_c_prime=run_cp,
    )
    assert out["n_figure_embedded"] == 1
    assert out["vision_called"] is True
    assert out["vision_spend"][0]["model"] == gold_vision.CHANNEL_C_MODEL
    assert out["vision_spend"][1]["model"] == gold_vision.CHANNEL_C_PRIME_MODEL
