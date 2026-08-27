"""Deterministic tests for the admitted single-panel dataset landscape."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pytest

from figures import check_results_s1_dataset_landscape as harness
from figures import results_s1_dataset_landscape as landscape
from figures.standards_checker import StandardsViolation, check_output_pair


@pytest.fixture(scope="module")
def data() -> landscape.DatasetLandscapeData:
    return landscape.measure()


def test_measurement_recounts_every_drawn_population(
    data: landscape.DatasetLandscapeData,
) -> None:
    assert (
        data.cohort_polymers,
        data.grid_solvents,
        data.stored_pairs,
        data.stored_cells,
        data.evaluable_cells,
        data.raw_valid_cells,
        data.ceiling_cells,
        data.refused_cells,
    ) == (11, 990, 8_974, 251_272, 250_527, 246_525, 4_002, 745)
    assert (data.property_record_count, data.original_bp_count) == (1_006, 846)
    assert data.admission_record_count == 786
    assert len(data.heat_cells) == 11 * 28
    assert sum(cell.denominator for cell in data.heat_cells) == 250_527
    assert data.onset_order == landscape.COHORT
    assert dict(data.pair_counts)["NYLON6"] == 32
    assert dict(data.pair_counts)["PES"] == 32
    assert landscape.check_density_bin_contract() == {
        "raw_bin_count": 50,
        "lower_log10": -8.0,
        "upper_log10": 2.0,
        "served_ceiling_band_count": 1,
    }
    assert landscape.check_t5_category_contract() == {
        "stored_node_count": 28,
        "never_category_count": 1,
    }


def test_all_outputs_are_separate_one_axes_drawings(
    data: landscape.DatasetLandscapeData,
) -> None:
    drawings = landscape.draw_all(data)
    try:
        assert tuple(drawings) == landscape.ALL_STEMS
        for drawing in drawings.values():
            report = landscape.verify_geometry(drawing)
            assert report["axes_count"] == 1
            assert report["minimum_text_height_pt"] >= 7.0
    finally:
        for drawing in drawings.values():
            plt.close(drawing.fig)


def test_committed_png_svg_pairs_and_provenance_are_consistent() -> None:
    expected_generator_digest = landscape.sha256_file(
        Path(landscape.__file__).resolve()
    )
    for stem in landscape.ALL_STEMS:
        report = check_output_pair(
            landscape.HERE / f"{stem}.png",
            landscape.HERE / f"{stem}.svg",
        )
        sidecar = json.loads(
            (landscape.HERE / f"{stem}.provenance.json").read_text(encoding="utf-8")
        )
        assert sidecar["figure"] == stem
        assert sidecar["outputs"]["png"]["sha256"] == report["png_sha256"]
        assert sidecar["outputs"]["svg"]["sha256"] == report["svg_sha256"]
        assert sidecar["generator"]["sha256"] == expected_generator_digest
        assert sidecar["analysis_cohort"]["excluded_polymers"] == ["PU"]
        assert all(
            row["actual_sha256"] == row["expected_sha256"]
            for row in sidecar["sources"]
            if row["digest_lock"]
        )

    assert landscape.check_svg_vector_contract(
        landscape.HERE / f"{stem}.svg" for stem in landscape.ALL_STEMS
    ) == {"svg_count": len(landscape.ALL_STEMS)}


def test_semantic_mutations_fire_even_when_totals_still_close(
    data: landscape.DatasetLandscapeData,
) -> None:
    heat = data.heat_cells[0]
    with pytest.raises(
        landscape.DatasetLandscapeValidationError,
        match="heat payload digest changed",
    ):
        landscape.validate(
            replace(
                data,
                heat_cells=(
                    replace(heat, numerator=heat.numerator + 1),
                    *data.heat_cells[1:],
                ),
            )
        )

    polymer, counts = data.onset_counts[0]
    changed = list(counts)
    source = next(index for index, count in enumerate(changed) if count)
    changed[source] -= 1
    changed[(source + 1) % len(changed)] += 1
    with pytest.raises(
        landscape.DatasetLandscapeValidationError,
        match="onset payload digest changed",
    ):
        landscape.validate(
            replace(
                data,
                onset_counts=((polymer, tuple(changed)), *data.onset_counts[1:]),
            )
        )


def test_accept_all_injections_are_not_mistaken_for_checker_passes(
    data: landscape.DatasetLandscapeData,
) -> None:
    def accept_all(*args, **kwargs):
        return {}

    geometry_failures: list[str] = []
    harness._geometry_controls(
        data,
        figure_checker=accept_all,
        failures=geometry_failures,
    )
    assert any(
        "single axes: defective case was accepted" in item for item in geometry_failures
    )

    output_failures: list[str] = []
    harness._output_controls(
        output_checker=accept_all,
        svg_checker=accept_all,
        failures=output_failures,
    )
    assert any(
        "required PNG: defective case was accepted" in item for item in output_failures
    )
    assert any(
        "vector-only SVG: defective case was accepted" in item
        for item in output_failures
    )

    semantic_failures: list[str] = []
    harness._semantic_controls(
        data,
        semantic_validator=accept_all,
        density_bin_validator=accept_all,
        t5_category_validator=accept_all,
        failures=semantic_failures,
    )
    assert any(
        "cohort/PU mutation: defective case was accepted" in item
        for item in semantic_failures
    )
    assert any(
        "density bin edge: defective case was accepted" in item
        for item in semantic_failures
    )
    assert any(
        "interpolated T5 crossing: defective case was accepted" in item
        for item in semantic_failures
    )


def test_standalone_harness_passes() -> None:
    assert harness.main() == 0


def test_vector_checker_must_fire_on_embedded_image(tmp_path: Path) -> None:
    bad = tmp_path / "bad.svg"
    bad.write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'><image href='x.png'/></svg>",
        encoding="utf-8",
    )
    with pytest.raises(StandardsViolation, match="raster image content"):
        landscape.check_svg_vector_contract([bad])
