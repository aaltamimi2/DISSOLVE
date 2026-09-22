"""Deterministic positive and must-fire tests for the coverage first drafts."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pytest

from figures import results_s1_screening_coverage as coverage
from figures.standards_checker import (
    StandardsViolation,
    check_byte_identical,
    check_output_pair,
)


@pytest.fixture(autouse=True)
def _historical_non_numeric_inbox_snapshot():
    """Keep accepted coverage drafts independent of later inbox appends."""

    path = coverage.INBOX_PATH
    historical = coverage.EXPECTED_SOURCE_DIGESTS.pop(path, None)
    try:
        yield
    finally:
        if historical is not None:
            coverage.EXPECTED_SOURCE_DIGESTS[path] = historical


def test_measurement_matches_admitted_stored_counts() -> None:
    data = coverage.measure()
    assert dict(data.pair_counts) == coverage.EXPECTED_PAIR_COUNTS
    assert data.pair_length_distribution == ((28, 9052),)
    assert data.temperature_nodes == tuple(range(25, 161, 5))
    assert data.evaluable_cells == 248378 + 4096 == 252474
    assert data.refused_cells == data.nonpositive_cells == 982
    assert data.not_measured_cells == 79184
    assert data.envelope_cells == 332640
    assert (
        ((990 - 78) + (990 - 32) + (990 - 32)) * 28
        == data.not_measured_cells
    )


def test_figure_queries_never_select_solubility_values() -> None:
    for query in (
        coverage.PAIR_COUNTS_SQL,
        coverage.PAIR_LENGTH_SQL,
        coverage.TEMPERATURE_NODES_SQL,
        coverage.DISPOSITIONS_SQL,
    ):
        assert "solubility_pct" not in query.lower()


@pytest.mark.parametrize(
    "drawer, expected_marks",
    ((coverage.draw_pair_catalog, 12), (coverage.draw_cell_fates, 3)),
)
def test_each_coverage_figure_is_one_axes_and_passes_geometry(
    drawer,
    expected_marks: int,
) -> None:
    fig, registry, title = drawer(coverage.measure())
    try:
        report = coverage.verify_geometry(fig, registry, title)
    finally:
        plt.close(fig)
    assert report["axes_count"] == 1
    assert report["data_mark_count"] == expected_marks
    assert report["minimum_text_height_pt"] >= 7.0
    assert report["minimum_data_mark_extent_pt"] >= 7.0
    assert report["title_width_pt"] <= report["content_width_pt"]


def test_wrong_source_digest_must_fire_before_measurement() -> None:
    expected = dict(coverage.EXPECTED_SOURCE_DIGESTS)
    expected[coverage.DB_PATH] = "0" * 64
    with pytest.raises(coverage.SourceDigestError, match="source digest mismatch"):
        coverage.measure(expected_digests=expected)


def test_sparse_hole_mutation_must_fire() -> None:
    data = coverage.measure()
    changed = tuple(
        (polymer, count + 1 if polymer == "PU" else count)
        for polymer, count in data.pair_counts
    )
    with pytest.raises(coverage.CoverageValidationError):
        coverage.validate(replace(data, pair_counts=changed))


def test_exact_100_refusal_mutation_must_fire() -> None:
    data = coverage.measure()
    wrong = replace(
        data,
        evaluable_cells=data.raw_valid_cells,
        refused_cells=data.nonpositive_cells + data.exact_100_artifact_cells,
    )
    with pytest.raises(coverage.CoverageValidationError):
        coverage.validate(wrong)


def test_committed_output_pairs_and_sidecars_trace_sources() -> None:
    for stem in (coverage.PAIR_STEM, coverage.FATE_STEM):
        png = coverage.HERE / f"{stem}.png"
        svg = coverage.HERE / f"{stem}.svg"
        report = check_output_pair(png, svg)
        sidecar_path = coverage.HERE / f"{stem}.provenance.json"
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert sidecar["outputs"]["png"]["sha256"] == report["png_sha256"]
        assert sidecar["outputs"]["svg"]["sha256"] == report["svg_sha256"]
        assert sidecar["generator"]["sha256"] == coverage.sha256_file(
            Path(coverage.__file__).resolve()
        )
        for source in sidecar["sources"]:
            assert source["actual_sha256"] == source["expected_sha256"]


def test_two_clean_generations_are_byte_identical_and_mutation_fails(
    tmp_path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    coverage.generate(first)
    coverage.generate(second)
    names = tuple(
        f"{stem}.{suffix}"
        for stem in (coverage.PAIR_STEM, coverage.FATE_STEM)
        for suffix in ("png", "svg", "provenance.json")
    )
    left = tuple(first / name for name in names)
    right = tuple(second / name for name in names)
    assert check_byte_identical(left, right)["file_count"] == 6

    altered = second / f"{coverage.PAIR_STEM}.png"
    altered.write_bytes(altered.read_bytes() + b"must-fire")
    with pytest.raises(StandardsViolation, match="determinism mismatch"):
        check_byte_identical(left, right)
