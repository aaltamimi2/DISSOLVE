"""Deterministic positive and must-fire tests for the landscape drafts."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pytest

from figures import results_s1_landscape as landscape
from figures.standards_checker import StandardsViolation, check_output_pair


@pytest.fixture(scope="module")
def data() -> landscape.LandscapeData:
    return landscape.measure()


def test_measurement_recounts_every_plotted_value(
    data: landscape.LandscapeData,
) -> None:
    assert len(data.cells) == data.raw_valid_cells + data.exact_100_artifact_cells
    assert len(data.cells) == 252_474
    assert data.raw_valid_cells == 248_378
    assert data.exact_100_artifact_cells == 4_096
    assert data.nonpositive_cells == 982
    assert sum(cell.solubility_pct >= 5.0 for cell in data.cells) == 96_325
    assert len(data.fractions) == 12 * 28
    assert sum(point.numerator for point in data.fractions) == 96_325
    assert {point.polymer for point in data.fractions} == set(landscape.POLYMER_ORDER)
    for point in data.fractions:
        assert point.denominator == landscape.EXPECTED_DENOMINATORS[point.polymer]
        assert point.fraction == point.numerator / point.denominator


def test_threshold_defaults_are_parsed_without_importing_screens(
    data: landscape.LandscapeData,
) -> None:
    assert {
        (item.function, item.parameter, item.value)
        for item in data.threshold_defaults
    } == {
        ("_screen_direction", "solubility_threshold_pct", 5.0),
        (
            "screen_precipitation_order",
            "min_dissolution_solubility_wt_pct",
            5.0,
        ),
    }


def test_figures_pass_geometry_style_layer_and_displacement_contracts(
    data: landscape.LandscapeData,
) -> None:
    scatter = landscape.draw_solubility_scatter(data)
    fraction = landscape.draw_dissolving_fraction(data)
    try:
        for drawing in (scatter, fraction):
            report = landscape.verify_geometry(drawing)
            assert report["axes_count"] == 1
            assert report["minimum_text_height_pt"] >= 7.0
            assert report["minimum_data_mark_extent_pt"] >= 7.0 - 1e-12
        layer_report = landscape.check_scatter_layer_contract(
            data, scatter.registry
        )
        assert layer_report == {
            "source_row_count": 252_474,
            "raw_layer_count": 248_378,
            "served_ceiling_layer_count": 4_096,
        }
        assert landscape.check_polymer_style_contract(
            scatter.registry, fraction.registry, landscape.POLYMER_STYLE_MAP
        ) == {"polymer_count": 12}
        manifest = landscape.displacement_manifest(data.cells, scatter.rendered_x)
        displacement = landscape.check_display_displacement(
            data.cells, scatter.rendered_x, manifest
        )
        assert displacement["row_count"] == 252_474
        assert displacement["maximum_absolute_displacement_c"] < 2.5
    finally:
        plt.close(scatter.fig)
        plt.close(fraction.fig)


def test_committed_outputs_and_provenance_are_self_consistent(
    data: landscape.LandscapeData,
) -> None:
    for stem in (landscape.SCATTER_STEM, landscape.FRACTION_STEM):
        report = check_output_pair(
            landscape.HERE / f"{stem}.png",
            landscape.HERE / f"{stem}.svg",
        )
        sidecar = json.loads(
            (landscape.HERE / f"{stem}.provenance.json").read_text(
                encoding="utf-8"
            )
        )
        assert sidecar["outputs"]["png"]["sha256"] == report["png_sha256"]
        assert sidecar["outputs"]["svg"]["sha256"] == report["svg_sha256"]
        assert sidecar["generator"]["sha256"] == landscape.sha256_file(
            Path(landscape.__file__).resolve()
        )
        for source in sidecar["sources"]:
            if source["digest_lock"]:
                assert source["actual_sha256"] == source["expected_sha256"]
        assert sidecar["polymer_style_map"] == landscape.POLYMER_STYLE_MAP
        assert len(sidecar["fraction_rows"]) == 336
        assert set(sidecar["queries"]) == {
            "scatter_cells", "fractions", "dispositions"
        }
        assert sidecar["displacement"]["row_count"] == 252_474
        assert set(sidecar["per_layer_row_counts"]["scatter"]) == set(
            landscape.POLYMER_ORDER
        )
    scatter_sidecar = json.loads(
        (
            landscape.HERE / f"{landscape.SCATTER_STEM}.provenance.json"
        ).read_text(encoding="utf-8")
    )
    assert len(scatter_sidecar["unshifted_temperature_c_by_source_row"]) == 252_474
    assert landscape.check_svg_raster_contract(
        landscape.HERE / f"{landscape.SCATTER_STEM}.svg",
        landscape.HERE / f"{landscape.FRACTION_STEM}.svg",
    ) == {"scatter_image_count": 1, "fraction_image_count": 0}


def test_wrong_digest_and_semantic_mutations_must_fire(
    data: landscape.LandscapeData,
) -> None:
    wrong = dict(landscape.EXPECTED_SOURCE_DIGESTS)
    wrong[landscape.DB_PATH] = "0" * 64
    with pytest.raises(landscape.SourceDigestError, match="source digest mismatch"):
        landscape.measure(expected_digests=wrong)

    point = data.fractions[0]
    changed = replace(
        point,
        denominator=point.denominator - 1,
        fraction=point.numerator / (point.denominator - 1),
    )
    with pytest.raises(
        landscape.LandscapeValidationError, match="fixed denominator changed"
    ):
        landscape.validate(replace(data, fractions=(changed, *data.fractions[1:])))


def test_wrong_display_offset_is_rejected_even_with_matching_wrong_digest(
    data: landscape.LandscapeData,
) -> None:
    rendered = tuple(landscape.display_x(cell) for cell in data.cells)
    changed = (rendered[0] + 0.01, *rendered[1:])
    wrong_manifest = landscape.displacement_manifest(data.cells, changed)
    with pytest.raises(StandardsViolation, match="display displacement differs"):
        landscape.check_display_displacement(data.cells, changed, wrong_manifest)
