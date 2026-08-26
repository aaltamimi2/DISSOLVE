#!/usr/bin/env python3
"""Deterministic publication-figure standards checks for Matplotlib figures.

The checker operates on rendered artist extents, not on pixels inspected by a
person.  Figure builders register the shapes whose overlap is meaningful and
explicitly permit intentional layering (for example a filled bar over its
background track).  Every visible, non-empty text artist must be registered so
that unreviewed labels cannot bypass the collision checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from itertools import combinations
from pathlib import Path
from typing import Iterable

from matplotlib.artist import Artist
from matplotlib.collections import Collection
from matplotlib.colors import to_rgba
# Matplotlib exposes several concrete image artists (AxesImage, FigureImage,
# BboxImage, NonUniformImage) through one internal render base.  Using that
# family here closes discovery across axes- and figure-level images instead of
# enumerating today's concrete subclasses and leaving the next one unchecked.
from matplotlib.image import _ImageBase
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.text import Text
from matplotlib.transforms import Bbox


BLACK = "#000000"


class StandardsViolation(AssertionError):
    """One or more deterministic figure-standard checks failed."""

    def __init__(self, violations: Iterable[str]):
        self.violations = tuple(violations)
        super().__init__("\n".join(self.violations))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_output_pair(
    png_path: str | Path,
    svg_path: str | Path,
) -> dict[str, int | str]:
    """Assert one nonempty same-basename PNG/SVG output pair.

    This check is deliberately separate from artist geometry because it runs
    only after export.  It raises :class:`StandardsViolation`, allowing the
    same must-fire harness to reject a missing or misnamed output.
    """

    png = Path(png_path)
    svg = Path(svg_path)
    violations: list[str] = []
    if png.suffix.lower() != ".png":
        violations.append(f"PNG output has the wrong suffix: {png}")
    if svg.suffix.lower() != ".svg":
        violations.append(f"SVG output has the wrong suffix: {svg}")
    if png.parent != svg.parent or png.stem != svg.stem:
        violations.append("PNG and SVG outputs do not share one basename")

    sizes: dict[Path, int] = {}
    for kind, path in (("PNG", png), ("SVG", svg)):
        if not path.is_file():
            violations.append(f"missing {kind} output: {path}")
            continue
        size = path.stat().st_size
        sizes[path] = size
        if size <= 0:
            violations.append(f"empty {kind} output: {path}")

    if violations:
        raise StandardsViolation(violations)
    return {
        "basename": png.stem,
        "png_bytes": sizes[png],
        "png_sha256": _sha256(png),
        "svg_bytes": sizes[svg],
        "svg_sha256": _sha256(svg),
    }


def check_byte_identical(
    first_paths: Iterable[str | Path],
    second_paths: Iterable[str | Path],
) -> dict[str, int]:
    """Assert two ordered output sets have identical names and bytes."""

    first = tuple(Path(path) for path in first_paths)
    second = tuple(Path(path) for path in second_paths)
    violations: list[str] = []
    if len(first) != len(second):
        violations.append(
            f"determinism comparison has different file counts: "
            f"{len(first)} != {len(second)}"
        )
    for left, right in zip(first, second):
        if left.name != right.name:
            violations.append(
                f"determinism comparison has different names: "
                f"{left.name!r} != {right.name!r}"
            )
            continue
        if not left.is_file() or not right.is_file():
            violations.append(
                f"determinism comparison is missing {left.name!r}"
            )
            continue
        if _sha256(left) != _sha256(right):
            violations.append(
                f"determinism mismatch for {left.name!r}"
            )
    if violations:
        raise StandardsViolation(violations)
    return {"file_count": len(first)}


@dataclass(frozen=True)
class FigureStandards:
    """Mechanical thresholds evaluated at the figure's render DPI."""

    font_size_pt: float
    text_color: str = BLACK
    minimum_text_height_pt: float = 7.0
    minimum_data_mark_extent_pt: float = 7.0
    text_clearance_pt: float = 2.0
    element_clearance_pt: float = 1.0
    container_inset_pt: float = 4.0
    numeric_tolerance: float = 1e-6


@dataclass
class TextRecord:
    """A text artist and the geometry it is intentionally allowed to occupy."""

    artist: Artist
    container: Artist | None
    allowed_patches: set[int] = field(default_factory=set)


@dataclass
class DrawingRegistry:
    """Collision semantics supplied by a figure builder.

    ``collision_patches`` are checked against text, connectors, and one
    another.  An overlap exemption must name an exact pair of artist objects;
    broad class- or region-level exemptions are deliberately unsupported.
    """

    texts: list[TextRecord] = field(default_factory=list)
    panels: dict[str, Artist] = field(default_factory=dict)
    collision_patches: list[Artist] = field(default_factory=list)
    connectors: list[Artist] = field(default_factory=list)
    data_marks: list[Artist] = field(default_factory=list)
    structural_artists: list[Artist] = field(default_factory=list)
    allowed_patch_overlaps: set[frozenset[int]] = field(default_factory=set)

    def allow_patch_overlap(self, first: Artist, second: Artist) -> None:
        """Permit one intentional, exact shape-layering pair."""

        self.allowed_patch_overlaps.add(frozenset((id(first), id(second))))


def padded_bbox(box: Bbox, pixels: float) -> Bbox:
    return Bbox.from_extents(
        box.x0 - pixels,
        box.y0 - pixels,
        box.x1 + pixels,
        box.y1 + pixels,
    )


def inset_bbox(box: Bbox, pixels: float) -> Bbox:
    return Bbox.from_extents(
        box.x0 + pixels,
        box.y0 + pixels,
        box.x1 - pixels,
        box.y1 - pixels,
    )


def positive_overlap(first: Bbox, second: Bbox) -> bool:
    """Return true only for positive-area overlap; touching is handled by padding."""

    return (
        min(first.x1, second.x1) > max(first.x0, second.x0)
        and min(first.y1, second.y1) > max(first.y0, second.y0)
    )


def _inside(inner: Bbox, outer: Bbox, tolerance: float) -> bool:
    return (
        inner.x0 >= outer.x0 - tolerance
        and inner.y0 >= outer.y0 - tolerance
        and inner.x1 <= outer.x1 + tolerance
        and inner.y1 <= outer.y1 + tolerance
    )


def _artist_name(artist: Artist) -> str:
    if isinstance(artist, Text):
        return repr(artist.get_text())
    label = artist.get_label()
    if label and not str(label).startswith("_"):
        return f"{type(artist).__name__} {label!r}"
    return type(artist).__name__


def _pair_is_allowed(
    first: Artist,
    second: Artist,
    allowed: set[frozenset[int]],
) -> bool:
    return frozenset((id(first), id(second))) in allowed


def _matplotlib_scaffold_ids(fig: Artist) -> set[int]:
    """Return exact Matplotlib-owned framing artists, never user data artists."""

    scaffold = {id(fig.patch)}
    for axes in fig.axes:
        scaffold.add(id(axes.patch))
        scaffold.update(id(spine) for spine in axes.spines.values())
        for axis in (axes.xaxis, axes.yaxis):
            for tick in (*axis.get_major_ticks(), *axis.get_minor_ticks()):
                scaffold.update(
                    id(artist)
                    for artist in (tick.tick1line, tick.tick2line, tick.gridline)
                )
    return scaffold


def check_figure(
    fig: Artist,
    registry: DrawingRegistry,
    title: Artist,
    *,
    standards: FigureStandards,
    content_artists: Iterable[Artist] | None = None,
) -> dict[str, float | int]:
    """Render and assert the owner standards on one Matplotlib figure.

    Raises :class:`StandardsViolation` with every observed defect.  The return
    value is a compact, serializable measurement record for provenance logs.
    """

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    pixels_per_point = float(fig.dpi) / 72.0
    text_clearance = standards.text_clearance_pt * pixels_per_point
    element_clearance = standards.element_clearance_pt * pixels_per_point
    border_inset = standards.container_inset_pt * pixels_per_point
    tolerance = standards.numeric_tolerance
    figure_box = fig.bbox
    violations: list[str] = []

    axes_count = len(getattr(fig, "axes", ()))
    if axes_count != 1:
        violations.append(
            f"figure must contain exactly one axes; found {axes_count}"
        )

    registered_ids = [id(record.artist) for record in registry.texts]
    duplicate_text_ids = {
        artist_id for artist_id in registered_ids
        if registered_ids.count(artist_id) > 1
    }
    if duplicate_text_ids:
        violations.append("a text artist is registered more than once")

    visible_texts = [
        artist
        for artist in fig.findobj(match=Text)
        if artist.get_visible() and bool(artist.get_text())
    ]
    registered_id_set = set(registered_ids)
    visible_id_set = {id(artist) for artist in visible_texts}
    unregistered = [
        _artist_name(artist) for artist in visible_texts
        if id(artist) not in registered_id_set
    ]
    stale = [
        _artist_name(record.artist) for record in registry.texts
        if id(record.artist) not in visible_id_set
    ]
    if unregistered:
        violations.append(f"visible text is not registered: {unregistered}")
    if stale:
        violations.append(f"registered text is not visible in the figure: {stale}")

    # Closed-world control for rendered graphical artists. Matplotlib's exact
    # figure/axes framing objects are recognized as scaffold; every user-added
    # patch, line, collection, or image must have an explicit registry role.
    registered_graphic_ids = {
        *(id(artist) for artist in registry.panels.values()),
        *(id(artist) for artist in registry.collision_patches),
        *(id(artist) for artist in registry.connectors),
        *(id(artist) for artist in registry.structural_artists),
    }
    permitted_graphic_ids = registered_graphic_ids | _matplotlib_scaffold_ids(fig)
    visible_graphics = [
        artist
        for artist in fig.findobj(
            match=lambda item: isinstance(
                item, (Patch, Line2D, Collection, _ImageBase)
            )
        )
        if artist.get_visible()
    ]
    unregistered_graphics = [
        _artist_name(artist) for artist in visible_graphics
        if id(artist) not in permitted_graphic_ids
    ]
    if unregistered_graphics:
        violations.append(
            "visible graphical artist is not registered: "
            f"{unregistered_graphics}"
        )

    expected_rgba = to_rgba(standards.text_color)
    text_boxes: list[Bbox] = []
    for record in registry.texts:
        artist = record.artist
        try:
            actual_size = float(artist.get_fontsize())
        except (AttributeError, TypeError, ValueError):
            violations.append(f"registered text has no numeric font size: {_artist_name(artist)}")
            continue
        if abs(actual_size - standards.font_size_pt) > tolerance:
            violations.append(
                f"nonuniform text size for {_artist_name(artist)}: "
                f"{actual_size:g} pt != {standards.font_size_pt:g} pt"
            )
        try:
            actual_rgba = to_rgba(artist.get_color())
        except (AttributeError, ValueError) as error:
            violations.append(f"invalid text color for {_artist_name(artist)}: {error}")
        else:
            if any(
                abs(actual - expected) > tolerance
                for actual, expected in zip(actual_rgba, expected_rgba)
            ):
                violations.append(
                    f"non-black text for {_artist_name(artist)}: {actual_rgba}"
                )
        box = artist.get_window_extent(renderer)
        text_boxes.append(box)
        if box.width <= 0 or box.height <= 0:
            violations.append(f"text has an empty rendered extent: {_artist_name(artist)}")
            continue
        height_pt = box.height / pixels_per_point
        if height_pt + tolerance < standards.minimum_text_height_pt:
            violations.append(
                f"text is too small for print: {_artist_name(artist)} "
                f"renders at {height_pt:.3f} pt high"
            )
        if not _inside(box, figure_box, tolerance):
            violations.append(f"text leaves the figure: {_artist_name(artist)}")
        if record.container is not None:
            container_box = inset_bbox(
                record.container.get_window_extent(renderer), border_inset
            )
            if not _inside(box, container_box, tolerance):
                violations.append(
                    f"text escapes or touches its container: {_artist_name(artist)}"
                )

    # Require a full clearance between every pair of text extents.
    for (first_record, first_box), (second_record, second_box) in combinations(
        zip(registry.texts, text_boxes), 2
    ):
        if positive_overlap(
            padded_bbox(first_box, text_clearance / 2.0),
            padded_bbox(second_box, text_clearance / 2.0),
        ):
            violations.append(
                "text collision: "
                f"{_artist_name(first_record.artist)} and "
                f"{_artist_name(second_record.artist)}"
            )

    patch_boxes = {
        id(patch): patch.get_window_extent(renderer)
        for patch in registry.collision_patches
    }
    collision_patch_ids = set(patch_boxes)
    for mark in registry.data_marks:
        if id(mark) not in collision_patch_ids:
            violations.append(
                f"data mark is not registered as collision-relevant: "
                f"{_artist_name(mark)}"
            )
    for patch in registry.collision_patches:
        if not _inside(patch_boxes[id(patch)], figure_box, tolerance):
            violations.append(f"shape leaves the figure: {_artist_name(patch)}")
    for record, text_box in zip(registry.texts, text_boxes):
        expanded_text = padded_bbox(text_box, element_clearance / 2.0)
        for patch in registry.collision_patches:
            if id(patch) in record.allowed_patches:
                continue
            expanded_patch = padded_bbox(
                patch_boxes[id(patch)], element_clearance / 2.0
            )
            if positive_overlap(expanded_text, expanded_patch):
                violations.append(
                    f"text/shape collision: {_artist_name(record.artist)} and "
                    f"{_artist_name(patch)}"
                )

    # Registered shapes may not collide unless their exact layering is declared.
    for first, second in combinations(registry.collision_patches, 2):
        if _pair_is_allowed(first, second, registry.allowed_patch_overlaps):
            continue
        if positive_overlap(
            padded_bbox(patch_boxes[id(first)], element_clearance / 2.0),
            padded_bbox(patch_boxes[id(second)], element_clearance / 2.0),
        ):
            violations.append(
                f"shape collision: {_artist_name(first)} and {_artist_name(second)}"
            )

    connector_boxes = {
        id(connector): connector.get_window_extent(renderer)
        for connector in registry.connectors
    }
    for connector in registry.connectors:
        connector_box = connector_boxes[id(connector)]
        if not _inside(connector_box, figure_box, tolerance):
            violations.append(
                f"connector leaves the figure: {_artist_name(connector)}"
            )
        for record, text_box in zip(registry.texts, text_boxes):
            if positive_overlap(
                padded_bbox(text_box, element_clearance / 2.0),
                padded_bbox(connector_box, element_clearance / 2.0),
            ):
                violations.append(
                    f"text/connector collision: {_artist_name(record.artist)} and "
                    f"{_artist_name(connector)}"
                )
        for patch in registry.collision_patches:
            if positive_overlap(
                padded_bbox(connector_box, element_clearance / 2.0),
                padded_bbox(
                    patch_boxes[id(patch)], element_clearance / 2.0
                ),
            ):
                violations.append(
                    f"connector/shape collision: {_artist_name(connector)} and "
                    f"{_artist_name(patch)}"
                )
    for first, second in combinations(registry.connectors, 2):
        if positive_overlap(
            padded_bbox(connector_boxes[id(first)], element_clearance / 2.0),
            padded_bbox(connector_boxes[id(second)], element_clearance / 2.0),
        ):
            violations.append(
                f"connector collision: {_artist_name(first)} and "
                f"{_artist_name(second)}"
            )

    if id(title) not in registered_id_set:
        violations.append("title is not registered as checked text")
    content = list(content_artists or registry.panels.values())
    if not content:
        violations.append("title-width check has no registered content")
        title_box = title.get_window_extent(renderer)
        content_left = content_right = 0.0
    else:
        title_box = title.get_window_extent(renderer)
        content_boxes = [artist.get_window_extent(renderer) for artist in content]
        content_left = min(box.x0 for box in content_boxes)
        content_right = max(box.x1 for box in content_boxes)
        if title_box.width > content_right - content_left + tolerance:
            violations.append("title is wider than the content beneath it")
        if (
            title_box.x0 < content_left - tolerance
            or title_box.x1 > content_right + tolerance
        ):
            violations.append("title is not horizontally contained by its content")

    mark_extents_pt: list[float] = []
    for mark in registry.data_marks:
        box = mark.get_window_extent(renderer)
        extent_pt = max(box.width, box.height) / pixels_per_point
        mark_extents_pt.append(extent_pt)
        if extent_pt + tolerance < standards.minimum_data_mark_extent_pt:
            violations.append(
                f"data mark is too small for print: {_artist_name(mark)} "
                f"has maximum extent {extent_pt:.3f} pt"
            )
        if not _inside(box, figure_box, tolerance):
            violations.append(f"data mark leaves the figure: {_artist_name(mark)}")

    if violations:
        raise StandardsViolation(violations)

    text_heights_pt = [box.height / pixels_per_point for box in text_boxes]
    return {
        "axes_count": axes_count,
        "text_count": len(registry.texts),
        "shape_count": len(registry.collision_patches),
        "connector_count": len(registry.connectors),
        "data_mark_count": len(registry.data_marks),
        "minimum_text_height_pt": min(text_heights_pt),
        "minimum_data_mark_extent_pt": (
            min(mark_extents_pt) if mark_extents_pt else 0.0
        ),
        "title_width_pt": title_box.width / pixels_per_point,
        "content_width_pt": (content_right - content_left) / pixels_per_point,
    }
