"""Deterministic tests for the shared publication-figure checker."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.patches import FancyArrowPatch, Rectangle
import numpy as np
import pytest

from figures.standards_checker import (
    DrawingRegistry,
    FigureStandards,
    StandardsViolation,
    TextRecord,
    check_byte_identical,
    check_figure,
    check_output_pair,
)
from figures import unified_solubility_query
from figures import check_existing_figures


def _fixture(
    *,
    title_text: str = "Short title",
    body_size: float = 10.0,
    body_color: str = "#000000",
    second_body: bool = False,
    small_mark: bool = False,
) -> tuple[plt.Figure, DrawingRegistry, object]:
    fig = plt.figure(figsize=(4.0, 3.0), dpi=100, facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    content = Rectangle(
        (0.10, 0.10), 0.80, 0.70, transform=ax.transAxes,
        facecolor="white", edgecolor="#999999",
    )
    ax.add_patch(content)
    title = ax.text(
        0.5, 0.90, title_text, transform=ax.transAxes,
        ha="center", va="center", fontsize=10, color="#000000",
    )
    body = ax.text(
        0.25, 0.55, "Body", transform=ax.transAxes,
        ha="left", va="center", fontsize=body_size, color=body_color,
    )
    registry = DrawingRegistry(
        texts=[TextRecord(title, None), TextRecord(body, content)],
        panels={"content": content},
    )
    if second_body:
        other = ax.text(
            0.25, 0.55, "Collision", transform=ax.transAxes,
            ha="left", va="center", fontsize=10, color="#000000",
        )
        registry.texts.append(TextRecord(other, content))

    mark_size = 0.005 if small_mark else 0.05
    mark = Rectangle(
        (0.70, 0.45), mark_size, mark_size, transform=ax.transAxes,
        facecolor="#4E79A7", edgecolor="none",
    )
    ax.add_patch(mark)
    registry.collision_patches.append(mark)
    registry.data_marks.append(mark)
    return fig, registry, title


def _violations(**kwargs: object) -> tuple[str, ...]:
    fig, registry, title = _fixture(**kwargs)
    try:
        with pytest.raises(StandardsViolation) as caught:
            check_figure(
                fig,
                registry,
                title,
                standards=FigureStandards(font_size_pt=10.0),
            )
        return caught.value.violations
    finally:
        plt.close(fig)


def test_current_figure_passes_shared_checker() -> None:
    """Exercise the checker against every existing publication figure artist."""

    data = unified_solubility_query.measure()
    fig, registry, title = unified_solubility_query.draw(data)
    try:
        result = unified_solubility_query.verify_geometry(fig, registry, title)
    finally:
        plt.close(fig)
    assert result["text_count"] == 47
    assert result["shape_count"] == 31
    assert result["minimum_text_height_pt"] >= 7.0
    assert result["minimum_data_mark_extent_pt"] >= 7.0
    assert result["title_width_pt"] <= result["content_width_pt"]
    assert result["axes_count"] == 1


def test_rejects_nonuniform_text_size() -> None:
    assert any(
        "nonuniform text size" in item
        for item in _violations(body_size=9.0)
    )


def test_rejects_nonblack_text() -> None:
    assert any(
        "non-black text" in item
        for item in _violations(body_color="#4E79A7")
    )


def test_rejects_text_overlap() -> None:
    assert any(
        "text collision" in item
        for item in _violations(second_body=True)
    )


def test_rejects_text_shape_overlap() -> None:
    fig, registry, title = _fixture()
    registry.collision_patches[0].set_xy((0.25, 0.53))
    try:
        with pytest.raises(StandardsViolation) as caught:
            check_figure(
                fig, registry, title,
                standards=FigureStandards(font_size_pt=10),
            )
    finally:
        plt.close(fig)
    assert any("text/shape collision" in item for item in caught.value.violations)


def test_rejects_shape_overlap() -> None:
    fig, registry, title = _fixture()
    other = Rectangle(
        (0.72, 0.47), 0.05, 0.05, transform=fig.axes[0].transAxes,
        facecolor="#E69F00", edgecolor="none",
    )
    fig.axes[0].add_patch(other)
    registry.collision_patches.append(other)
    try:
        with pytest.raises(StandardsViolation) as caught:
            check_figure(
                fig, registry, title,
                standards=FigureStandards(font_size_pt=10),
            )
    finally:
        plt.close(fig)
    assert any("shape collision" in item for item in caught.value.violations)


def test_rejects_connector_overlap() -> None:
    fig, registry, title = _fixture()
    connector = FancyArrowPatch(
        (0.20, 0.55), (0.40, 0.55), transform=fig.axes[0].transAxes,
        arrowstyle="-|>", color="#555555",
    )
    fig.axes[0].add_patch(connector)
    registry.connectors.append(connector)
    try:
        with pytest.raises(StandardsViolation) as caught:
            check_figure(
                fig, registry, title,
                standards=FigureStandards(font_size_pt=10),
            )
    finally:
        plt.close(fig)
    assert any("text/connector collision" in item for item in caught.value.violations)


def test_rejects_container_escape() -> None:
    fig, registry, title = _fixture()
    registry.texts[1].artist.set_position((0.02, 0.55))
    try:
        with pytest.raises(StandardsViolation) as caught:
            check_figure(
                fig, registry, title,
                standards=FigureStandards(font_size_pt=10),
            )
    finally:
        plt.close(fig)
    assert any("text escapes or touches" in item for item in caught.value.violations)


def test_rejects_small_data_mark() -> None:
    assert any(
        "data mark is too small" in item
        for item in _violations(small_mark=True)
    )


def test_rejects_data_mark_that_bypasses_collision_registry() -> None:
    fig, registry, title = _fixture()
    registry.collision_patches.clear()
    try:
        with pytest.raises(StandardsViolation) as caught:
            check_figure(
                fig, registry, title,
                standards=FigureStandards(font_size_pt=10),
            )
    finally:
        plt.close(fig)
    assert any(
        "data mark is not registered as collision-relevant" in item
        for item in caught.value.violations
    )


def test_rejects_wholly_unregistered_visible_artist() -> None:
    fig, registry, title = _fixture()
    mark = Rectangle(
        (0.25, 0.53), 0.10, 0.08, transform=fig.axes[0].transAxes,
        facecolor="#E69F00", edgecolor="none",
    )
    fig.axes[0].add_patch(mark)
    try:
        with pytest.raises(StandardsViolation) as caught:
            check_figure(
                fig, registry, title,
                standards=FigureStandards(font_size_pt=10),
            )
    finally:
        plt.close(fig)
    assert any(
        "visible graphical artist is not registered" in item
        for item in caught.value.violations
    )


def test_rejects_wholly_unregistered_figure_image() -> None:
    fig, registry, title = _fixture()
    pixels = np.ones((50, 120, 4), dtype=float)
    pixels[:, :, :3] = (0.90, 0.62, 0.00)
    fig.figimage(pixels, xo=80, yo=140, origin="lower")
    try:
        with pytest.raises(StandardsViolation) as caught:
            check_figure(
                fig, registry, title,
                standards=FigureStandards(font_size_pt=10),
            )
    finally:
        plt.close(fig)
    assert any(
        "visible graphical artist is not registered" in item
        for item in caught.value.violations
    )


def test_rejects_title_wider_than_content() -> None:
    assert any(
        "title is wider" in item
        for item in _violations(title_text="A title " * 30)
    )


def test_rejects_unregistered_visible_text() -> None:
    fig, registry, title = _fixture()
    ax = fig.axes[0]
    ax.text(0.5, 0.25, "Unregistered", fontsize=10, color="#000000")
    try:
        with pytest.raises(StandardsViolation) as caught:
            check_figure(
                fig,
                registry,
                title,
                standards=FigureStandards(font_size_pt=10.0),
            )
    finally:
        plt.close(fig)
    assert any("visible text is not registered" in item for item in caught.value.violations)


def test_rejects_multiple_axes() -> None:
    fig, registry, title = _fixture()
    fig.add_axes((0.82, 0.82, 0.10, 0.10)).axis("off")
    try:
        with pytest.raises(StandardsViolation) as caught:
            check_figure(
                fig,
                registry,
                title,
                standards=FigureStandards(font_size_pt=10.0),
            )
    finally:
        plt.close(fig)
    assert any("exactly one axes" in item for item in caught.value.violations)


def test_output_pair_requires_png_and_svg(tmp_path) -> None:
    png = tmp_path / "figure.png"
    svg = tmp_path / "figure.svg"
    png.write_bytes(b"png")
    with pytest.raises(StandardsViolation, match="missing SVG output"):
        check_output_pair(png, svg)
    png.unlink()
    svg.write_bytes(b"svg")
    with pytest.raises(StandardsViolation, match="missing PNG output"):
        check_output_pair(png, svg)


def test_output_pair_requires_same_basename(tmp_path) -> None:
    png = tmp_path / "first.png"
    svg = tmp_path / "second.svg"
    png.write_bytes(b"png")
    svg.write_bytes(b"svg")
    with pytest.raises(StandardsViolation, match="do not share one basename"):
        check_output_pair(png, svg)


def test_byte_comparator_rejects_altered_output(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    left = first / "figure.svg"
    right = second / "figure.svg"
    left.write_bytes(b"same")
    right.write_bytes(b"same")
    assert check_byte_identical((left,), (right,)) == {"file_count": 1}
    right.write_bytes(b"different")
    with pytest.raises(StandardsViolation, match="determinism mismatch"):
        check_byte_identical((left,), (right,))


def test_must_fire_harness() -> None:
    assert check_existing_figures.main() == 0


def test_must_fire_harness_rejects_accept_all_figure_checker() -> None:
    def accept_all(*args, **kwargs):
        return {}

    assert check_existing_figures.main(figure_checker=accept_all) == 1


def test_must_fire_harness_rejects_accept_all_output_checker() -> None:
    def accept_all(*args, **kwargs):
        return {}

    assert check_existing_figures.main(output_checker=accept_all) == 1
