"""One typed visualization vocabulary shared by planning and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class VisualizationViewContract:
    """One canonical view, its planner aliases, and consuming tool surface."""

    name: str
    tools: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    argument: str | None = "view"
    required_answer_roles: tuple[str, ...] = ()
    stored_result_referent: str | None = None


@dataclass(frozen=True)
class VisualizationArtifactKindContract:
    """One canonical visual artifact kind and its planner aliases."""

    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class VisualizationOptionContract:
    """One presentation option or invariant admitted by a plotting surface."""

    name: str
    tools: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    argument: str | None = None
    views: tuple[str, ...] = ()


VISUALIZATION_ARTIFACT_KIND_REGISTRY = (
    VisualizationArtifactKindContract("none"),
    VisualizationArtifactKindContract(
        "plot", aliases=("graph", "overlay"),
    ),
    VisualizationArtifactKindContract("chart"),
    VisualizationArtifactKindContract("curve"),
    VisualizationArtifactKindContract("diagram"),
    VisualizationArtifactKindContract("figure"),
    VisualizationArtifactKindContract(
        "heatmap",
        aliases=("hsp_heatmap", "red_heatmap", "heat map"),
    ),
)


VISUALIZATION_VIEW_REGISTRY = (
    VisualizationViewContract(
        "auto", ("plot_analysis_results",),
    ),
    VisualizationViewContract(
        "heatmap",
        ("plot_analysis_results", "plot_separation_analysis"),
        aliases=(
            "hsp_red_heatmap",
            "hsp_red_heatmap_publication",
            "hansen_heatmap",
            "heatmap publication: materials as columns, solvent classes "
            "grouped, revised RED encoding",
        ),
    ),
    VisualizationViewContract(
        "radar",
        ("plot_analysis_results",),
        aliases=(
            "hsp_radar_normalized_pe", "normalized_2d_radar",
            "normalized_radar",
        ),
    ),
    VisualizationViewContract(
        "sphere",
        ("plot_analysis_results",),
        aliases=(
            "3d_hansen_red_sphere",
            "hansen_red_sphere_3d",
            "true_3d_red_sphere",
            "3d_hansen_red_sphere_PE",
            "true 3D RED sphere view",
            "3d red sphere",
            "RED sphere",
            "interaction sphere",
        ),
    ),
    VisualizationViewContract("gauge", ("plot_analysis_results",)),
    VisualizationViewContract("summary", ("plot_analysis_results",)),
    VisualizationViewContract("pair_gap", ("plot_analysis_results",)),
    VisualizationViewContract("family", ("plot_analysis_results",)),
    VisualizationViewContract(
        "method_comparison", ("plot_analysis_results",),
    ),
    VisualizationViewContract(
        "red_vs_solubility", ("plot_analysis_results",),
    ),
    VisualizationViewContract(
        "safety_margin_bars",
        ("plot_analysis_results",),
        aliases=("flash_point_margin_bars",),
    ),
    VisualizationViewContract(
        "safety_card",
        ("plot_analysis_results",),
        aliases=(
            "solvent_safety_card",
            "safety_data_sheet_card",
        ),
        stored_result_referent="stored:safety_card",
    ),
    VisualizationViewContract(
        "metric_scatter",
        ("plot_analysis_results",),
        aliases=(
            "solvent_metric_scatter",
            "solubility_g_score_scatter",
        ),
        required_answer_roles=(
            "x_axis_provenance",
            "y_axis_provenance",
        ),
    ),
    VisualizationViewContract(
        "tea_case_bars",
        ("plot_analysis_results",),
        aliases=(
            "tea_energy_case_bars",
            "process_record_case_bars",
        ),
        stored_result_referent="stored:admitted_process_records",
    ),
    VisualizationViewContract(
        "tea_record_card",
        ("plot_analysis_results",),
        aliases=(
            "process_record_card",
            "admitted_process_record_card",
        ),
        stored_result_referent="stored:admitted_process_records",
    ),
    VisualizationViewContract(
        "tea_pareto",
        ("plot_analysis_results",),
        aliases=(
            "tea_pareto_frontier",
            "process_record_pareto",
        ),
        stored_result_referent="stored:admitted_process_records",
    ),
    VisualizationViewContract(
        "tea_sensitivity_tornado",
        ("plot_analysis_results",),
        aliases=(
            "tea_tornado",
            "sensitivity_tornado",
        ),
        stored_result_referent="stored:admitted_process_records",
    ),
    VisualizationViewContract(
        "contaminant_criteria_heatmap",
        ("plot_analysis_results",),
        aliases=(
            "contaminant_heatmap",
            "contaminant_family_stage_heatmap",
        ),
    ),
    VisualizationViewContract(
        # Short canonical name so the declaration enum stays inside its
        # character ceiling; the spoken long forms are aliases, which cost
        # nothing because aliases are never enumerated in the prompt.
        "partitioning_map",
        ("plot_analysis_results",),
        aliases=(
            "contaminant_partitioning_map",
            "contaminant_partition_map",
            "partitioning_plot",
            "logd_map",
        ),
        stored_result_referent="stored:contaminant_screen_analysis",
    ),
    VisualizationViewContract(
        "contaminant_screen_card",
        ("plot_analysis_results",),
        aliases=(
            "contaminant_decision_card",
            "contaminant_removal_card",
        ),
        stored_result_referent="stored:contaminant_screen_analysis",
    ),
    VisualizationViewContract(
        "solubility_curves",
        ("plot_solubility_curves",),
        aliases=(
            "solubility_curve", "solubility_curve_panel",
            "solubility_vs_temperature",
            "solubility_vs_temperature_color_highlight",
        ),
        argument=None,
    ),
    VisualizationViewContract(
        "route", ("plot_separation_analysis",),
    ),
    VisualizationViewContract(
        "tree", ("plot_separation_analysis",),
    ),
    VisualizationViewContract(
        # plot_separation_analysis rewrites pfd, process_flow_diagram and
        # tree onto the same "route" mode, so advertising them as separate
        # views spent declaration-prompt characters on synonyms. Demoted to
        # an alias: still accepted and canonicalized, no longer enumerated.
        "pfd",
        ("plot_separation_analysis",),
        aliases=("process_flow", "process_flow_diagram"),
    ),
    VisualizationViewContract(
        "state_map", ("plot_separation_analysis",),
    ),
    VisualizationViewContract(
        "dp_state_map", ("plot_separation_analysis",),
    ),
    VisualizationViewContract(
        "selectivity_heatmap", ("plot_separation_analysis",),
    ),
    VisualizationViewContract(
        "precipitation", ("plot_separation_analysis",),
    ),
    VisualizationViewContract(
        "precipitation_curves", ("plot_separation_analysis",),
    ),
    VisualizationViewContract(
        "precipitation_ladder", ("plot_separation_analysis",),
    ),
    VisualizationViewContract(
        "atmospheric_feasibility", ("plot_separation_analysis",),
    ),
    VisualizationViewContract(
        "feasibility", ("plot_separation_analysis",),
    ),
    VisualizationViewContract(
        # route_window, window_comparison and route_landscape_comparison all
        # resolve to the route_windows mode. The longest synonym becomes an
        # alias; it still resolves, it is just no longer advertised.
        "route_windows",
        ("plot_separation_analysis",),
        aliases=("route_landscape_comparison",),
    ),
    VisualizationViewContract(
        "route_window", ("plot_separation_analysis",),
    ),
    VisualizationViewContract(
        "window_comparison", ("plot_separation_analysis",),
    ),
)


VISUALIZATION_OPTION_REGISTRY = (
    VisualizationOptionContract(
        "y_axis_max", ("plot_solubility_curves",),
        argument="y_axis_max", views=("solubility_curves",),
    ),
    VisualizationOptionContract(
        "reference_temperature_c",
        ("plot_solubility_curves",),
        aliases=(
            "vertical_reference_145c",
            "vertical_reference_145_C",
        ),
        argument="reference_temperature_c",
        views=("solubility_curves",),
    ),
    VisualizationOptionContract(
        "reference_temperature_label",
        ("plot_solubility_curves",),
        argument="reference_temperature_label",
        views=("solubility_curves",),
    ),
    VisualizationOptionContract(
        "reference_solubility_wt_pct",
        ("plot_solubility_curves",),
        aliases=(
            "horizontal_screening_5wtpct",
            "horizontal_reference_5wtpct",
            "horizontal_screening_target_5_wt%",
        ),
        argument="reference_solubility_wt_pct",
        views=("solubility_curves",),
    ),
    VisualizationOptionContract(
        "reference_solubility_label",
        ("plot_solubility_curves",),
        argument="reference_solubility_label",
        views=("solubility_curves",),
    ),
    VisualizationOptionContract(
        "highlight_solvent",
        ("plot_solubility_curves",),
        aliases=(
            "highlight_dodecane_full_color_others_gray",
            "highlight_Dodecane",
            "highlight_Dodecane_full_color",
            "other_series_gray",
            "gray_others",
        ),
        argument="highlight_solvent",
        views=("solubility_curves",),
    ),
    VisualizationOptionContract(
        "show_markers",
        ("plot_solubility_curves",),
        aliases=("no_markers",),
        argument="show_markers",
        views=("solubility_curves",),
    ),
    VisualizationOptionContract(
        "direct_end_labels",
        ("plot_solubility_curves",),
        aliases=("direct_label_curve_ends", "direct_label_ends"),
        views=("solubility_curves",),
    ),
    VisualizationOptionContract(
        "hsp_heatmap_orientation",
        ("plot_analysis_results",),
        aliases=("materials_as_columns",),
        argument="hsp_heatmap_orientation",
        views=("heatmap",),
    ),
    VisualizationOptionContract(
        "highlight_pairs",
        ("plot_analysis_results",),
        aliases=(
            "highlight_PE_Dodecane",
            "highlight_EVOH_Ethylene_glycol",
            "highlight_PET_Cyclohexanol",
            "highlight_PET_Mylar_Cyclohexanol",
        ),
        argument="highlight_pairs",
        views=("heatmap",),
    ),
    VisualizationOptionContract(
        "highlight_solvents",
        ("plot_analysis_results",),
        aliases=(
            "direct_label_highlighted_solvents",
            "highlighted_solvent_labels",
        ),
        argument="highlight_solvents",
        views=("metric_scatter",),
    ),
    VisualizationOptionContract(
        "pareto_note",
        ("plot_analysis_results",),
        aliases=(
            "provenance_note",
            "show_provenance_note",
        ),
        argument="show_provenance_note",
        views=("tea_pareto",),
    ),
    VisualizationOptionContract(
        "group_solvent_classes",
        ("plot_analysis_results",),
        aliases=("solvent_classes_grouped",),
        views=("heatmap",),
    ),
    VisualizationOptionContract(
        "black_text",
        ("plot_analysis_results", "plot_solubility_curves"),
        aliases=("black_text_labels",),
        views=(
            "heatmap", "radar", "sphere", "metric_scatter",
            "safety_margin_bars", "safety_card", "tea_case_bars",
            "tea_record_card", "tea_pareto",
            "tea_sensitivity_tornado", "contaminant_criteria_heatmap",
            "contaminant_screen_card",
            "partitioning_map",
            "solubility_curves",
        ),
    ),
    VisualizationOptionContract(
        "publication_style",
        ("plot_analysis_results", "plot_solubility_curves"),
        aliases=(
            "publication_heatmap",
            "publication_hsp_sphere",
            "publication_hsp_radar",
            "publication_panel",
            "publication_curve_panel",
        ),
        views=(
            "heatmap", "radar", "sphere", "metric_scatter",
            "safety_margin_bars", "safety_card", "tea_case_bars",
            "tea_record_card", "tea_pareto",
            "tea_sensitivity_tornado", "contaminant_criteria_heatmap",
            "contaminant_screen_card",
            "partitioning_map",
            "solubility_curves",
        ),
    ),
    VisualizationOptionContract(
        "solid_red_inside_sphere",
        ("plot_analysis_results",),
        aliases=(
            "red_below_one_solid",
            "RED_lt1_one_solid_red_no_gradient",
            "RED_lt1_solid_red_no_gradient",
        ),
        views=("heatmap",),
    ),
    VisualizationOptionContract(
        "blue_ramp_outside_sphere",
        ("plot_analysis_results",),
        aliases=(
            "blue_ramp_above_one",
            "RED_gte1_light_to_dark_sequential_blue_ramp",
            "RED_gte1_blue_ramp",
        ),
        views=("heatmap",),
    ),
    VisualizationOptionContract(
        "hard_break_at_red_one",
        ("plot_analysis_results",),
        aliases=(
            "colorbar_break",
            "colorbar_break_at_one",
            "colorbar_break_at_1",
            "hard_colorbar_break_at_1",
        ),
        views=("heatmap",),
    ),
    VisualizationOptionContract(
        "true_r0_sphere",
        ("plot_analysis_results",),
        aliases=(
            "3d_red_sphere",
            "r0_boundary",
            "R0 interaction boundary",
            "actual R0 interaction boundary",
        ),
        views=("sphere",),
    ),
    VisualizationOptionContract(
        "hansen_space_positioning",
        ("plot_analysis_results",),
        aliases=(
            "hansen_positioning",
            "solvents positioned in Hansen space",
        ),
        views=("sphere",),
    ),
    VisualizationOptionContract(
        "inside_sphere_markers",
        ("plot_analysis_results",),
        aliases=("inside_markers", "distinct inside-sphere markers"),
        views=("sphere",),
    ),
    VisualizationOptionContract(
        "legend_outside_axes",
        ("plot_analysis_results",),
        aliases=(
            "legend_outside",
            "legend outside the axes",
        ),
        views=("sphere",),
    ),
    VisualizationOptionContract(
        "per_axis_normalization",
        ("plot_analysis_results",),
        aliases=(
            "normalized_radar",
            "per_axis_normalization_disclosure",
            "normalized_2d_radar",
            "normalized",
        ),
        views=("radar",),
    ),
    VisualizationOptionContract(
        "distinct_material_polygon",
        ("plot_analysis_results",),
        aliases=("pe_polygon_distinct", "distinct_polygon"),
        views=("radar",),
    ),
    VisualizationOptionContract(
        "series_cap_disclosure",
        ("plot_analysis_results",),
        aliases=(
            "solvent_series_cap_disclosure",
            "solvent_cap_disclosure",
            "solvent_cap",
        ),
        views=("radar",),
    ),
    VisualizationOptionContract(
        "publication_palette",
        (
            "plot_analysis_results", "plot_solubility_curves",
            "plot_separation_analysis",
        ),
        aliases=(
            "shared_publication_palette",
            "publication_color_palette",
            "publication_colour_palette",
        ),
        views=(
            "heatmap", "radar", "sphere", "metric_scatter",
            "safety_margin_bars", "safety_card", "tea_case_bars",
            "tea_record_card", "tea_pareto",
            "tea_sensitivity_tornado", "contaminant_criteria_heatmap",
            "contaminant_screen_card",
            "partitioning_map",
            "solubility_curves", "pfd",
        ),
    ),
    VisualizationOptionContract(
        "publication_typeface",
        (
            "plot_analysis_results", "plot_solubility_curves",
            "plot_separation_analysis",
        ),
        aliases=(
            "shared_publication_typeface",
            "publication_font",
        ),
        views=(
            "heatmap", "radar", "sphere", "metric_scatter",
            "safety_margin_bars", "safety_card", "tea_case_bars",
            "tea_record_card", "tea_pareto",
            "tea_sensitivity_tornado", "contaminant_criteria_heatmap",
            "contaminant_screen_card",
            "partitioning_map",
            "solubility_curves", "pfd",
        ),
    ),
    VisualizationOptionContract(
        "selectivity_0_100_range",
        ("plot_separation_analysis",),
        aliases=("selectivity_0_100",),
        views=("pfd",),
    ),
    VisualizationOptionContract(
        "progress_bar_legend",
        ("plot_separation_analysis",),
        views=("pfd",),
    ),
    VisualizationOptionContract(
        "pfd_view",
        ("plot_separation_analysis",),
        aliases=("pfd",),
        views=("pfd",),
    ),
)


def normalize_visualization_name(value: object) -> str:
    """Normalize case, spacing, and punctuation without inferring semantics."""
    return "_".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _index_contracts(
    contracts: Iterable[
        VisualizationArtifactKindContract
        | VisualizationViewContract
        | VisualizationOptionContract
    ],
) -> dict[
    str,
    VisualizationArtifactKindContract
    | VisualizationViewContract
    | VisualizationOptionContract,
]:
    indexed: dict[
        str,
        VisualizationArtifactKindContract
        | VisualizationViewContract
        | VisualizationOptionContract,
    ] = {}
    for contract in contracts:
        for name in (contract.name, *contract.aliases):
            normalized = normalize_visualization_name(name)
            if not normalized or normalized in indexed:
                raise ValueError(
                    f"Duplicate visualization vocabulary name: {name!r}",
                )
            indexed[normalized] = contract
    return indexed


_ARTIFACT_KIND_BY_NAME = _index_contracts(
    VISUALIZATION_ARTIFACT_KIND_REGISTRY
)
_VIEW_BY_NAME = _index_contracts(VISUALIZATION_VIEW_REGISTRY)
_OPTION_BY_NAME = _index_contracts(VISUALIZATION_OPTION_REGISTRY)

CANONICAL_VISUALIZATION_ARTIFACT_KINDS = frozenset(
    item.name for item in VISUALIZATION_ARTIFACT_KIND_REGISTRY
)
CANONICAL_VISUALIZATION_VIEWS = frozenset(
    item.name for item in VISUALIZATION_VIEW_REGISTRY
)
CANONICAL_VISUALIZATION_OPTIONS = frozenset(
    item.name for item in VISUALIZATION_OPTION_REGISTRY
)
PLANNER_VISUALIZATION_ARTIFACT_KINDS = tuple(
    sorted(_ARTIFACT_KIND_BY_NAME)
)
PLANNER_VISUALIZATION_VIEWS = tuple(sorted(_VIEW_BY_NAME))
PLANNER_VISUALIZATION_OPTIONS = tuple(sorted(_OPTION_BY_NAME))
_TOOLS_BY_VISUALIZATION_VIEW = {
    contract.name: frozenset(contract.tools)
    for contract in VISUALIZATION_VIEW_REGISTRY
}
VISUALIZATION_OPTION_SURFACES = {
    contract.name: frozenset(
        (tool_name, view)
        for tool_name in contract.tools
        for view in contract.views
        if tool_name in _TOOLS_BY_VISUALIZATION_VIEW.get(view, ())
    )
    for contract in VISUALIZATION_OPTION_REGISTRY
}
VISUALIZATION_OPTIONS_BY_SURFACE = {
    (tool_name, view): frozenset(
        contract.name
        for contract in VISUALIZATION_OPTION_REGISTRY
        if tool_name in contract.tools and view in contract.views
    )
    for view_contract in VISUALIZATION_VIEW_REGISTRY
    for tool_name in view_contract.tools
    for view in (view_contract.name,)
}


def visualization_artifact_kind_contract(
    value: object,
) -> VisualizationArtifactKindContract | None:
    return _ARTIFACT_KIND_BY_NAME.get(  # type: ignore[return-value]
        normalize_visualization_name(value),
    )


def visualization_view_contract(
    value: object,
) -> VisualizationViewContract | None:
    return _VIEW_BY_NAME.get(  # type: ignore[return-value]
        normalize_visualization_name(value),
    )


def visualization_option_contract(
    value: object,
) -> VisualizationOptionContract | None:
    return _OPTION_BY_NAME.get(  # type: ignore[return-value]
        normalize_visualization_name(value),
    )


def canonical_visualization_artifact_kind(value: object) -> str:
    contract = visualization_artifact_kind_contract(value)
    return (
        contract.name
        if contract is not None
        else normalize_visualization_name(value)
    )


def canonical_visualization_view(value: object) -> str:
    contract = visualization_view_contract(value)
    return (
        contract.name
        if contract is not None
        else normalize_visualization_name(value)
    )


def canonical_visualization_options(values: object) -> object:
    if not isinstance(values, list):
        return values
    canonical = []
    for value in values:
        contract = visualization_option_contract(value)
        name = (
            contract.name
            if contract is not None
            else normalize_visualization_name(value)
        )
        if name not in canonical:
            canonical.append(name)
    return canonical


def partition_visualization_options(
    values: object,
    *,
    view: str,
    tool_name: str | None = None,
) -> tuple[list[str], list[str]]:
    """Separate known wrong-surface options without hiding unknown names."""
    canonical = canonical_visualization_options(values)
    if not isinstance(canonical, list):
        return [], []
    retained: list[str] = []
    dropped: list[str] = []
    for option in canonical:
        contract = visualization_option_contract(option)
        if contract is None or not view:
            retained.append(option)
            continue
        surfaces = VISUALIZATION_OPTION_SURFACES[contract.name]
        supported = (
            (tool_name, view) in surfaces
            if tool_name is not None
            else any(surface_view == view for _, surface_view in surfaces)
        )
        (retained if supported else dropped).append(option)
    return retained, dropped


def visualization_option_is_supported(
    contract: VisualizationOptionContract,
    *,
    tool_name: str,
    view: str,
) -> bool:
    return (tool_name, view) in VISUALIZATION_OPTION_SURFACES.get(
        contract.name, (),
    )


def validate_visualization_registry() -> None:
    """Fail import-time on an incomplete or internally divergent registry."""
    if set(_ARTIFACT_KIND_BY_NAME) != set(
        PLANNER_VISUALIZATION_ARTIFACT_KINDS
    ):
        raise ValueError("Visualization artifact planner index is incomplete")
    if set(_VIEW_BY_NAME) != set(PLANNER_VISUALIZATION_VIEWS):
        raise ValueError("Visualization view planner index is incomplete")
    if set(_OPTION_BY_NAME) != set(PLANNER_VISUALIZATION_OPTIONS):
        raise ValueError("Visualization option planner index is incomplete")
    expected_surfaces = {
        contract.name: frozenset(
            (tool_name, view)
            for tool_name in contract.tools
            for view in contract.views
            if tool_name in _TOOLS_BY_VISUALIZATION_VIEW.get(view, ())
        )
        for contract in VISUALIZATION_OPTION_REGISTRY
    }
    if VISUALIZATION_OPTION_SURFACES != expected_surfaces:
        raise ValueError("Visualization option surface index is incomplete")
    view_surfaces = {
        (tool_name, contract.name)
        for contract in VISUALIZATION_VIEW_REGISTRY
        for tool_name in contract.tools
    }
    if set().union(*expected_surfaces.values()) - view_surfaces:
        raise ValueError("Visualization option names an unknown surface")
    expected_options_by_surface = {
        surface: frozenset(
            name for name, surfaces in expected_surfaces.items()
            if surface in surfaces
        )
        for surface in view_surfaces
    }
    if VISUALIZATION_OPTIONS_BY_SURFACE != expected_options_by_surface:
        raise ValueError("Visualization surface option map diverged")
    if {
        item.name for item in _ARTIFACT_KIND_BY_NAME.values()
    } != CANONICAL_VISUALIZATION_ARTIFACT_KINDS:
        raise ValueError("Visualization artifact validator index is incomplete")
    if {
        item.name for item in _VIEW_BY_NAME.values()
    } != CANONICAL_VISUALIZATION_VIEWS:
        raise ValueError("Visualization view validator index is incomplete")
    for contract in VISUALIZATION_VIEW_REGISTRY:
        if (
            len(contract.required_answer_roles)
            != len(set(contract.required_answer_roles))
            or any(
                not role or role.strip() != role
                for role in contract.required_answer_roles
            )
        ):
            raise ValueError(
                "Visualization answer-role contract is invalid",
            )
    if {
        item.name for item in _OPTION_BY_NAME.values()
    } != CANONICAL_VISUALIZATION_OPTIONS:
        raise ValueError("Visualization option validator index is incomplete")


validate_visualization_registry()
