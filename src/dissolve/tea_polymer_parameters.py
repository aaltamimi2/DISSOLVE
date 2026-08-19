"""Live STRAP TEA — per-polymer parameters.

THIS IS THE FILE TO EDIT.

A chemical engineer updating polymer assumptions for live BioSTEAM TEA
opens this file and this file only. Do not put these numbers in
``tea_worker.py``. Do not treat the unpublished plastics package as the
place we record our standing. The package is the plant; this table is
what we are willing to run, every number we assume, and whether that
number is VALIDATED or PROVISIONAL.

Every solubility-grid polymer appears here, including polymers we
refuse to simulate. Absence from this table is a refusal, not a
fall-through to the generic factory.

Standing (same shape as ``lca_metric_status`` / D-8)
----------------------------------------------------
``validated``     a number produced from this parameter may be treated
                  as a defended process assumption.
``provisional``   the number is servable, but every emitted costed
                  metric MUST carry ``process_parameter_status``.
                  ``can_cite_as_validated_process`` is False. A caller
                  cannot treat it as a validated design point.

Admission is a predicate, not a remembered name list
----------------------------------------------------
Live execution proceeds only when all of these are true:

1. This table has a row for the requested grid name.
2. That row's ``admission`` is ``live`` (not ``refuse``).
3. After ``identity_in_model`` mapping, the unpublished package's
   ``STRAP_chemicals_outline`` already contains that chemical ID.
   Otherwise the generic factory PET-clones it, which we refuse.
4. The oligomer ID is already in that outline, or this row supplies
   ``inject_if_missing`` so the worker can append it at runtime
   without editing the package.

A name on neither this table nor the package outline cannot fall
through. That was the PES (package-ready, wrapper-blind) / PU
(no draft, PET-cloned) defect.

Do not import BioSTEAM, dissolve engines, or the plastics package
from this module. The live worker file-loads it as a sibling.
"""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import AbstractSet, Any, Iterable, Mapping, Optional

VALIDATED = "validated"
PROVISIONAL = "provisional"

ADMISSION_LIVE = "live"
ADMISSION_REFUSE = "refuse"

PROCESS_PARAMETER_STATUS_CODE = "provisional_live_process_parameters"
PROCESS_PARAMETER_STATUS_DEFINITION = {
    "status": PROVISIONAL,
    "reason": (
        "One or more live STRAP process parameters for this polymer are "
        "provisional (generic factory steps, an injected oligomer proxy, "
        "and/or a chemist-sign-off flag). The MSP/TCI/AOC/GWP number is "
        "the simulation of those assumptions, not a validated design point."
    ),
    "served_value_role": (
        "provisional_process_assumption_not_validated_design_point"
    ),
    "current_scientific_use": "not_admitted_as_validated_process",
    "can_cite_as_validated_process": False,
}

# Costed fields that inherit process-parameter standing. Parallel to
# lca_metric_status: the number is present; the code travels with it.
LIVE_COSTED_METRIC_FIELDS = (
    "msp_usd_per_kg",
    "tci_usd",
    "aoc_usd_per_yr",
    "gwp_kg_co2e_per_kg",
    "htc_ctuh_per_kg",
    "htnc_ctuh_per_kg",
    "etox_ctue_per_kg",
    "electricity_consumed_mj_per_kg",
    "heating_duty_mj_per_kg",
    "cooling_duty_mj_per_kg",
    "total_energy_mj_per_kg",
)


# ---------------------------------------------------------------------------
# Package factory defaults (plastics 0.1.4 define_dissolution /
# define_precipitation). Used for every pair that is not a committed
# named step. These numbers are PROVISIONAL for every polymer.
# ---------------------------------------------------------------------------

GENERIC_FACTORY_DISSOLUTION_T_C = 130.0
GENERIC_FACTORY_CAPACITY_WT_PER_VOL = 0.05
GENERIC_FACTORY_TAU_H = 0.5
GENERIC_FACTORY_PRECIPITATION_T_C = 35.0  # 308.15 K
GENERIC_FACTORY_PRECIPITATION_SOLUBILITY = 0.0
GENERIC_FACTORY_PRECIPITATION_TAU_H = 0.5


@dataclass(frozen=True)
class ChemicalAssumptions:
    """Solid-film ThermoSTEAM draft. IDs are case-sensitive in the package."""

    formula: str
    rho_kg_m3: float
    cp_j_per_g_k: float
    tm_k: float
    tb_k: Optional[float]
    lhv_note: str
    standing: str
    source: str


@dataclass(frozen=True)
class OligomerAssumptions:
    """Dissolved-state liquid proxy. Reaction is {plastic} → {plastic}oligomer."""

    chemical_id: str
    search_id: str
    in_package_outline: bool
    standing: str
    inject_if_missing: bool
    source: str
    note: str = ""


@dataclass(frozen=True)
class ProcessAssumptions:
    """Dissolution / precipitation knobs the live plant actually uses."""

    dissolution_temperature_c: float
    dissolution_capacity_wt_per_vol: float
    dissolution_tau_h: float
    precipitation_temperature_c: float
    precipitation_solubility_wt_wt: float
    precipitation_tau_h: float
    standing: str
    source: str
    committed_pair: Optional[str] = None


@dataclass(frozen=True)
class PolymerRow:
    """One grid polymer. Admission, numbers, standing, judgement flags."""

    grid_name: str
    identity_in_model: str
    admission: str
    chemical: Optional[ChemicalAssumptions]
    oligomer: Optional[OligomerAssumptions]
    process: Optional[ProcessAssumptions]
    chemist_signoff_required: bool = False
    chemist_signoff_note: str = ""
    refuse_reason: str = ""
    notes: str = ""


@dataclass(frozen=True)
class LiveAdmission:
    """Result of the admission predicate. Never a raw-name fall-through."""

    admitted: bool
    grid_name: str
    identity_in_model: Optional[str]
    error_type: Optional[str]
    error: str
    package_chemical_present: Optional[bool]
    package_oligomer_present: Optional[bool]
    oligomer_injection_required: bool
    row: Optional[PolymerRow] = None
    extra: dict[str, Any] = field(default_factory=dict)


def _generic_process() -> ProcessAssumptions:
    return ProcessAssumptions(
        dissolution_temperature_c=GENERIC_FACTORY_DISSOLUTION_T_C,
        dissolution_capacity_wt_per_vol=GENERIC_FACTORY_CAPACITY_WT_PER_VOL,
        dissolution_tau_h=GENERIC_FACTORY_TAU_H,
        precipitation_temperature_c=GENERIC_FACTORY_PRECIPITATION_T_C,
        precipitation_solubility_wt_wt=GENERIC_FACTORY_PRECIPITATION_SOLUBILITY,
        precipitation_tau_h=GENERIC_FACTORY_PRECIPITATION_TAU_H,
        standing=PROVISIONAL,
        source=(
            "plastics 0.1.4 define_dissolution/define_precipitation "
            "generic factory (capacity 0.05, 130 C, tau 0.5 h, "
            "precipitation 35 C, solubility 0)"
        ),
        committed_pair=None,
    )


def _pe_chemical() -> ChemicalAssumptions:
    return ChemicalAssumptions(
        formula="C2H4",
        rho_kg_m3=920.0,  # 0.5 * (880 + 960), LDPE–HDPE midpoint in the draft
        cp_j_per_g_k=1.865,  # 0.5 * (1.330 + 2.400)
        tm_k=398.15,  # 0.5 * (115 + 135) + 273.15
        tb_k=None,
        lhv_note="261.0 * MW(C2H4); NIST jresv78An5p611 cited in the draft",
        standing=VALIDATED,
        source="plastics 0.1.4 property_package.PE",
    )


def _pe_oligomer() -> OligomerAssumptions:
    return OligomerAssumptions(
        chemical_id="PEoligomer",
        search_id="1-Hexene",
        in_package_outline=True,
        standing=VALIDATED,
        inject_if_missing=False,
        source="plastics 0.1.4 property_package.PEoligomer",
    )


# Committed pair-specific steps in dissolution_steps.py / precipitation_steps.py.
# Only these (identity, solvent-id) pairs are VALIDATED process steps.
# Solvent IDs are the process-model identifiers, casefolded for matching.
COMMITTED_PAIR_STEPS: dict[tuple[str, str], ProcessAssumptions] = {
    ("PE", "toluene"): ProcessAssumptions(
        dissolution_temperature_c=95.0,  # 368.15 K
        dissolution_capacity_wt_per_vol=0.03,
        dissolution_tau_h=0.5,
        precipitation_temperature_c=35.0,
        precipitation_solubility_wt_wt=0.0,
        precipitation_tau_h=0.5,
        standing=VALIDATED,
        source="plastics 0.1.4 PE_Toluene_dissolution / PE_Toluene_precipitation",
        committed_pair="PE/Toluene",
    ),
    ("EVOH", "dmsowater"): ProcessAssumptions(
        dissolution_temperature_c=110.0,  # 383.15 K
        dissolution_capacity_wt_per_vol=0.03,
        dissolution_tau_h=4.0,
        precipitation_temperature_c=35.0,
        precipitation_solubility_wt_wt=0.0,
        precipitation_tau_h=0.5,
        standing=VALIDATED,
        source=(
            "plastics 0.1.4 EVOH_DMSOWater_dissolution / "
            "EVOH_DMSOWater_precipitation"
        ),
        committed_pair="EVOH/DMSOWater",
    ),
    ("PC", "thf"): ProcessAssumptions(
        dissolution_temperature_c=63.0,  # 336.15 K
        dissolution_capacity_wt_per_vol=0.03,
        dissolution_tau_h=0.5,
        precipitation_temperature_c=35.0,
        precipitation_solubility_wt_wt=0.0,
        precipitation_tau_h=0.5,
        standing=VALIDATED,
        source="plastics 0.1.4 PC_THF_dissolution / PC_THF_precipitation",
        committed_pair="PC/THF",
    ),
}


# ===========================================================================
# THE TABLE. Grid name → everything we assume. Edit rows here.
# ===========================================================================

POLYMERS: dict[str, PolymerRow] = {
    "LDPE": PolymerRow(
        grid_name="LDPE",
        identity_in_model="PE",
        admission=ADMISSION_LIVE,
        chemical=_pe_chemical(),
        oligomer=_pe_oligomer(),
        process=_generic_process(),
        notes=(
            "LDPE and HDPE share the PE chemical and the PE process. "
            "The live engine has no LDPE-vs-HDPE distinction. Pair-specific "
            "steps are validated only for PE/Toluene; every other solvent "
            "uses the generic factory (provisional)."
        ),
    ),
    "HDPE": PolymerRow(
        grid_name="HDPE",
        identity_in_model="PE",
        admission=ADMISSION_LIVE,
        chemical=_pe_chemical(),
        oligomer=_pe_oligomer(),
        process=_generic_process(),
        notes="Same PE identity as LDPE. See LDPE notes.",
    ),
    "EVOH": PolymerRow(
        grid_name="EVOH",
        identity_in_model="EVOH",
        admission=ADMISSION_LIVE,
        chemical=ChemicalAssumptions(
            formula="C2H4OC2H4",
            rho_kg_m3=1130.0,  # 0.5 * (1120 + 1140)
            cp_j_per_g_k=2.4,
            tm_k=450.62,
            tb_k=None,
            lhv_note="21285 * MW(C2H4OC2H4); draft TODO to adjust and cite",
            standing=VALIDATED,
            source="plastics 0.1.4 property_package.EVOH",
        ),
        oligomer=OligomerAssumptions(
            chemical_id="EVOHoligomer",
            search_id="3-buten-2-ol",
            in_package_outline=True,
            standing=VALIDATED,
            inject_if_missing=False,
            source="plastics 0.1.4 property_package.EVOHoligomer",
            note="Approximate monomer.",
        ),
        process=_generic_process(),
        notes="Committed dissolution is the DMSOWater mixture, not catalog DMSO.",
    ),
    "PC": PolymerRow(
        grid_name="PC",
        identity_in_model="PC",
        admission=ADMISSION_LIVE,
        chemical=ChemicalAssumptions(
            formula="C16H14O3",
            rho_kg_m3=1200.0,
            cp_j_per_g_k=1.2,
            tm_k=573.15,
            tb_k=None,
            lhv_note="not in package draft (burn path only; worker uses no-burn)",
            standing=VALIDATED,
            source="plastics 0.1.4 property_package.PC",
        ),
        oligomer=OligomerAssumptions(
            chemical_id="PColigomer",
            search_id="1-Heptene",
            in_package_outline=True,
            standing=VALIDATED,
            inject_if_missing=False,
            source="plastics 0.1.4 property_package.PColigomer",
        ),
        process=_generic_process(),
    ),
    "PET": PolymerRow(
        grid_name="PET",
        identity_in_model="PET",
        admission=ADMISSION_LIVE,
        chemical=ChemicalAssumptions(
            formula="C10H8O4",
            rho_kg_m3=1380.0,
            cp_j_per_g_k=1.0,
            tm_k=523.0,
            tb_k=623.0,
            lhv_note="21285 * 192.16812 in the draft",
            standing=VALIDATED,
            source="plastics 0.1.4 property_package.PET",
        ),
        oligomer=OligomerAssumptions(
            chemical_id="PEToligomer",
            search_id="Dimethyl terephthalate",
            in_package_outline=False,
            standing=PROVISIONAL,
            inject_if_missing=True,
            source="this table; plastics 0.1.4 has no PEToligomer draft",
            note=(
                "The package will not invent an oligomer for an ID already "
                "in the outline. We inject this liquid-phase proxy at "
                "runtime so STRAP can compile. This is STRAP dissolution, "
                "not PET glycolysis. A chemist should confirm the proxy."
            ),
        ),
        process=_generic_process(),
        notes=(
            "A PET number from this model is STRAP on PET film with a "
            "provisional oligomer, not a glycolysis / methanolysis plant."
        ),
    ),
    "PP": PolymerRow(
        grid_name="PP",
        identity_in_model="PP",
        admission=ADMISSION_LIVE,
        chemical=ChemicalAssumptions(
            formula="C3H6",
            rho_kg_m3=905.0,
            cp_j_per_g_k=1.9,
            tm_k=460.7,
            tb_k=None,
            lhv_note="not in package draft (burn path only)",
            standing=VALIDATED,
            source="plastics 0.1.4 property_package.PP",
        ),
        oligomer=OligomerAssumptions(
            chemical_id="PPoligomer",
            search_id="4-Methyl-1-pentene",
            in_package_outline=True,
            standing=VALIDATED,
            inject_if_missing=False,
            source="plastics 0.1.4 property_package.PPoligomer",
        ),
        process=_generic_process(),
        notes="Chemical drafts exist. Process steps are the generic factory.",
    ),
    "PS": PolymerRow(
        grid_name="PS",
        identity_in_model="PS",
        admission=ADMISSION_LIVE,
        chemical=ChemicalAssumptions(
            formula="C8H8",
            rho_kg_m3=1050.0,
            cp_j_per_g_k=1.3,
            tm_k=516.2,
            tb_k=None,
            lhv_note="not in package draft (burn path only)",
            standing=VALIDATED,
            source="plastics 0.1.4 property_package.PS",
        ),
        oligomer=OligomerAssumptions(
            chemical_id="PSoligomer",
            search_id="Styrene",
            in_package_outline=True,
            standing=VALIDATED,
            inject_if_missing=False,
            source="plastics 0.1.4 property_package.PSoligomer",
        ),
        process=_generic_process(),
        notes="Chemical drafts exist. Process steps are the generic factory.",
    ),
    "PVC": PolymerRow(
        grid_name="PVC",
        identity_in_model="PVC",
        admission=ADMISSION_LIVE,
        chemical=ChemicalAssumptions(
            formula="C2H3Cl",
            rho_kg_m3=1380.0,
            cp_j_per_g_k=0.9,
            tm_k=546.0,
            tb_k=None,
            lhv_note="not in package draft (burn path only)",
            standing=VALIDATED,
            source="plastics 0.1.4 property_package.PVC",
        ),
        oligomer=OligomerAssumptions(
            chemical_id="PVColigomer",
            search_id="1,2-Dichloroethane",
            in_package_outline=True,
            standing=VALIDATED,
            inject_if_missing=False,
            source="plastics 0.1.4 property_package.PVColigomer",
            note="Chlorinated liquid-phase proxy.",
        ),
        process=_generic_process(),
        chemist_signoff_required=True,
        chemist_signoff_note=(
            "Whether STRAP-style dissolution is the right plant for PVC "
            "(HCl, plasticizer, different solvents) is engineering "
            "judgement, not a missing ThermoSTEAM field. Admitted as "
            "provisional until a chemist signs off. Do not upgrade this "
            "row to validated without that sign-off."
        ),
        notes=(
            "Package authors put PVC in the property package next to PS "
            "and PP. That is intent to represent the species, not a "
            "process sign-off that dissolution is the plant."
        ),
    ),
    "NYLON6": PolymerRow(
        grid_name="NYLON6",
        identity_in_model="Nylon6",
        admission=ADMISSION_LIVE,
        chemical=ChemicalAssumptions(
            formula="C6H11NO",
            rho_kg_m3=1130.0,
            cp_j_per_g_k=1.7,
            tm_k=533.0,
            tb_k=None,
            lhv_note="not in package draft (burn path only)",
            standing=VALIDATED,
            source="plastics 0.1.4 property_package.Nylon6",
        ),
        oligomer=OligomerAssumptions(
            chemical_id="Nylon6oligomer",
            search_id="Caprolactam",
            in_package_outline=True,
            standing=VALIDATED,
            inject_if_missing=False,
            source="plastics 0.1.4 property_package.Nylon6oligomer",
        ),
        process=_generic_process(),
        notes=(
            "Grid/worker ID is NYLON6; package ID is Nylon6. Passing "
            "NYLON6 through unchanged would PET-clone a new solid and "
            "ignore the real draft. Always map."
        ),
    ),
    "NYLON66": PolymerRow(
        grid_name="NYLON66",
        identity_in_model="Nylon66",
        admission=ADMISSION_LIVE,
        chemical=ChemicalAssumptions(
            formula="C12H22N2O2",
            rho_kg_m3=1140.0,
            cp_j_per_g_k=1.7,
            tm_k=574.0,
            tb_k=None,
            lhv_note="not in package draft (burn path only)",
            standing=VALIDATED,
            source="plastics 0.1.4 property_package.Nylon66",
        ),
        oligomer=OligomerAssumptions(
            chemical_id="Nylon66oligomer",
            search_id="Hexamethylenediamine",
            in_package_outline=True,
            standing=VALIDATED,
            inject_if_missing=False,
            source="plastics 0.1.4 property_package.Nylon66oligomer",
        ),
        process=_generic_process(),
        notes="Grid/worker ID is NYLON66; package ID is Nylon66. Always map.",
    ),
    "PES": PolymerRow(
        grid_name="PES",
        identity_in_model="PES",
        admission=ADMISSION_LIVE,
        chemical=ChemicalAssumptions(
            formula="C12H8O3S",
            rho_kg_m3=1370.0,
            cp_j_per_g_k=1.37,
            tm_k=613.15,
            tb_k=None,
            lhv_note="not in package draft (burn path only)",
            standing=VALIDATED,
            source="plastics 0.1.4 property_package.PES",
        ),
        oligomer=OligomerAssumptions(
            chemical_id="PESoligomer",
            search_id="Diphenyl sulfone",
            in_package_outline=True,
            standing=VALIDATED,
            inject_if_missing=False,
            source="plastics 0.1.4 property_package.PESoligomer",
        ),
        process=_generic_process(),
        notes=(
            "On the grid and in the package. The old denylist/default "
            "wrapper never named PES, so a live call reached the generic "
            "factory unlabelled. Now admitted through this table with "
            "provisional process standing."
        ),
    ),
    "PU": PolymerRow(
        grid_name="PU",
        identity_in_model="PU",
        admission=ADMISSION_REFUSE,
        chemical=None,
        oligomer=None,
        process=None,
        refuse_reason=(
            "No PU chemical draft and no PUoligomer in plastics 0.1.4. "
            "A live call with the raw name would PET-clone the solid "
            "(formula C10H8O4, PET LHV) and 1-hexene-clone the oligomer. "
            "That is the silent substitution this table exists to stop. "
            "Do not invent PU ρ/Cp/Tm here without a domain source."
        ),
        notes="On the solubility grid (thin: 78 solvents). Not modellable.",
    ),
}


def live_grid_targets() -> tuple[str, ...]:
    """Grid names we will attempt to simulate. Sorted, no fall-through names."""
    return tuple(sorted(
        name for name, row in POLYMERS.items()
        if row.admission == ADMISSION_LIVE
    ))


def live_identity_map() -> dict[str, str]:
    """Admitted grid name → process-model ID. No default identity."""
    return {
        name: row.identity_in_model
        for name, row in POLYMERS.items()
        if row.admission == ADMISSION_LIVE
    }


def polymer_row(name: str) -> Optional[PolymerRow]:
    token = str(name or "").strip().upper()
    return POLYMERS.get(token)


def committed_process_for(
    identity_in_model: str, solvent: Optional[str],
) -> Optional[ProcessAssumptions]:
    if not solvent:
        return None
    return COMMITTED_PAIR_STEPS.get(
        (identity_in_model, str(solvent).strip().casefold())
    )


def process_assumptions_for(
    row: PolymerRow, solvent: Optional[str] = None,
) -> Optional[ProcessAssumptions]:
    committed = committed_process_for(row.identity_in_model, solvent)
    return committed if committed is not None else row.process


def parameters_are_provisional(
    row: PolymerRow, solvent: Optional[str] = None,
) -> bool:
    """True if any parameter that feeds a live number is provisional."""
    if row.chemist_signoff_required:
        return True
    if row.chemical is not None and row.chemical.standing != VALIDATED:
        return True
    if row.oligomer is not None and row.oligomer.standing != VALIDATED:
        return True
    process = process_assumptions_for(row, solvent)
    if process is None or process.standing != VALIDATED:
        return True
    return False


def admit_live_target(
    name: str,
    package_chemical_ids: Optional[AbstractSet[str]] = None,
) -> LiveAdmission:
    """Predicate: table row + package outline, never raw-name default.

    ``package_chemical_ids`` is the set of IDs already in
    ``STRAP_chemicals_outline``. Pass ``None`` only when the package
    has not been inspected yet (doctor / parent classify). The worker
    must inspect before constructing a process.
    """
    token = str(name or "").strip().upper()
    row = POLYMERS.get(token)
    if row is None:
        return LiveAdmission(
            admitted=False,
            grid_name=token,
            identity_in_model=None,
            error_type="unsupported_live_target",
            error=(
                f"{token} is not on the live TEA parameter surface; "
                "refusing to pass the raw name into the generic STRAP "
                "factory (that path PET-clones unknown plastics)."
            ),
            package_chemical_present=None,
            package_oligomer_present=None,
            oligomer_injection_required=False,
        )
    if row.admission != ADMISSION_LIVE:
        return LiveAdmission(
            admitted=False,
            grid_name=row.grid_name,
            identity_in_model=row.identity_in_model,
            error_type="unsupported_live_target",
            error=(
                f"Live TEA will not simulate {row.grid_name}: "
                f"{row.refuse_reason}"
            ),
            package_chemical_present=False,
            package_oligomer_present=False,
            oligomer_injection_required=False,
            row=row,
        )

    chemical_present: Optional[bool] = None
    oligomer_present: Optional[bool] = None
    if package_chemical_ids is not None:
        chemical_present = row.identity_in_model in package_chemical_ids
        oligomer_id = row.oligomer.chemical_id if row.oligomer else None
        oligomer_present = (
            False if oligomer_id is None else oligomer_id in package_chemical_ids
        )
        if not chemical_present:
            return LiveAdmission(
                admitted=False,
                grid_name=row.grid_name,
                identity_in_model=row.identity_in_model,
                error_type="unsupported_live_target",
                error=(
                    f"{row.grid_name} maps to process-model ID "
                    f"{row.identity_in_model}, which is not in the "
                    "package chemical outline. Refusing to let the "
                    "generic factory PET-clone it."
                ),
                package_chemical_present=False,
                package_oligomer_present=oligomer_present,
                oligomer_injection_required=False,
                row=row,
            )
        if oligomer_present is False:
            can_inject = bool(
                row.oligomer and row.oligomer.inject_if_missing
            )
            if not can_inject:
                return LiveAdmission(
                    admitted=False,
                    grid_name=row.grid_name,
                    identity_in_model=row.identity_in_model,
                    error_type="unsupported_live_target",
                    error=(
                        f"{row.grid_name} chemical {row.identity_in_model} "
                        "is in the outline but "
                        f"{oligomer_id} is not, and this table does not "
                        "supply an oligomer injection. Refusing to compile "
                        "a reaction against a missing species."
                    ),
                    package_chemical_present=True,
                    package_oligomer_present=False,
                    oligomer_injection_required=False,
                    row=row,
                )

    inject = bool(
        row.oligomer
        and row.oligomer.inject_if_missing
        and oligomer_present is not True
    )
    return LiveAdmission(
        admitted=True,
        grid_name=row.grid_name,
        identity_in_model=row.identity_in_model,
        error_type=None,
        error="",
        package_chemical_present=chemical_present,
        package_oligomer_present=oligomer_present,
        oligomer_injection_required=inject,
        row=row,
    )


def live_parameter_standing_payload(
    row: PolymerRow,
    *,
    solvent: Optional[str] = None,
    present_metrics: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Attach standing to a live success payload.

    Validated runs emit ``can_cite_as_validated_process: true`` and no
    per-metric status codes (same as unqualified LCA fields).
    Provisional runs emit the code on every present costed metric and
    ``can_cite_as_validated_process: false``.
    """
    process = process_assumptions_for(row, solvent)
    provisional = parameters_are_provisional(row, solvent)
    chemical_standing = (
        row.chemical.standing if row.chemical is not None else PROVISIONAL
    )
    oligomer_standing = (
        row.oligomer.standing if row.oligomer is not None else PROVISIONAL
    )
    process_standing = process.standing if process is not None else PROVISIONAL
    provisional_parameters = [
        name for name, standing in (
            ("chemical", chemical_standing),
            ("oligomer", oligomer_standing),
            ("process_steps", process_standing),
        )
        if standing != VALIDATED
    ]
    if row.chemist_signoff_required:
        provisional_parameters.append("chemist_signoff_required")
    standing = {
        "target_plastic": row.grid_name,
        "identity_in_model": row.identity_in_model,
        "chemical": chemical_standing,
        "oligomer": oligomer_standing,
        "process_steps": process_standing,
        "committed_pair": process.committed_pair if process else None,
        "chemist_signoff_required": row.chemist_signoff_required,
        "provisional_parameters": provisional_parameters,
        "parameter_surface": "dissolve.tea_polymer_parameters",
    }
    if row.chemist_signoff_required:
        standing["chemist_signoff_note"] = row.chemist_signoff_note
    payload: dict[str, Any] = {
        "can_cite_as_validated_process": not provisional,
        "live_parameter_standing": standing,
    }
    if not provisional:
        return payload
    metrics = list(present_metrics or LIVE_COSTED_METRIC_FIELDS)
    payload["process_parameter_status"] = {
        field: PROCESS_PARAMETER_STATUS_CODE for field in metrics
    }
    payload["process_parameter_status_definitions"] = {
        PROCESS_PARAMETER_STATUS_CODE: dict(
            PROCESS_PARAMETER_STATUS_DEFINITION,
        ),
    }
    return payload


def attach_live_parameter_standing(
    result: dict[str, Any],
    row: PolymerRow,
    *,
    solvent: Optional[str] = None,
) -> dict[str, Any]:
    """Write standing onto the live payload *and* onto ``tea``.

    A caller who only copies ``result["tea"]`` still sees
    ``can_cite_as_validated_process`` next to the MSP. Absence of the
    flag on a live ``tea`` object is a defect; validated and provisional
    must not serialize identically.
    """
    attached = live_parameter_standing_payload(
        row,
        solvent=solvent,
        present_metrics=present_costed_metrics(result),
    )
    result.update(attached)
    tea = dict(result.get("tea") or {})
    tea["can_cite_as_validated_process"] = attached["can_cite_as_validated_process"]
    if attached.get("process_parameter_status"):
        tea["process_parameter_status"] = dict(attached["process_parameter_status"])
    result["tea"] = tea
    return result


def live_number_standing_defects(payload: Mapping[str, Any]) -> list[str]:
    """Defects if a served live MSP is not bound to standing on the same object.

    Bound to the subject (the MSP and its standing), not to a nearby
    lookup flag. A payload whose ``tea`` object contains an MSP and no
    ``can_cite_as_validated_process`` is the unlabelled-provisional
    failure: it serializes like a validated number.
    """
    defects: list[str] = []
    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        return defects
    tea = payload.get("tea") if isinstance(payload.get("tea"), Mapping) else {}
    if tea.get("msp_usd_per_kg") is None:
        return defects
    top_cite = payload.get("can_cite_as_validated_process")
    tea_cite = tea.get("can_cite_as_validated_process")
    top_status = (payload.get("process_parameter_status") or {})
    tea_status = (tea.get("process_parameter_status") or {})
    if not isinstance(top_status, Mapping):
        top_status = {}
    if not isinstance(tea_status, Mapping):
        tea_status = {}
    if tea_cite is None:
        defects.append(
            "tea.can_cite_as_validated_process is absent beside msp_usd_per_kg"
        )
    if top_cite is None:
        defects.append(
            "can_cite_as_validated_process is absent on the served payload"
        )
    provisional = tea_cite is False or top_cite is False
    if provisional:
        if not tea_status.get("msp_usd_per_kg"):
            defects.append(
                "provisional MSP on tea without process_parameter_status"
                "[msp_usd_per_kg]"
            )
        if not top_status.get("msp_usd_per_kg"):
            defects.append(
                "provisional MSP on payload without process_parameter_status"
                "[msp_usd_per_kg]"
            )
    return defects


def live_standing_signature(payload: Mapping[str, Any]) -> str:
    """Identity of the standing a caller can see, not of the MSP value."""
    tea = payload.get("tea") if isinstance(payload.get("tea"), Mapping) else {}
    return json.dumps(
        {
            "can_cite_as_validated_process": payload.get(
                "can_cite_as_validated_process"
            ),
            "tea_can_cite_as_validated_process": tea.get(
                "can_cite_as_validated_process"
            ),
            "process_parameter_status": payload.get("process_parameter_status"),
            "tea_process_parameter_status": tea.get("process_parameter_status"),
        },
        sort_keys=True,
        default=str,
    )


def present_costed_metrics(result: Mapping[str, Any]) -> tuple[str, ...]:
    """Metric names that actually appear on a live worker payload."""
    tea = result.get("tea") or {}
    lca = result.get("lca") or {}
    operations = result.get("operations") or {}
    sections = {**tea, **lca, **operations}
    return tuple(
        field for field in LIVE_COSTED_METRIC_FIELDS
        if sections.get(field) is not None
    )


# ===========================================================================
# Package inspection. Not the expert table. Reads unpublished 0.1.4 source
# without importing BioSTEAM. Used by admission and by doctor.
# ===========================================================================

_PROPERTY_PACKAGE_RELATIVE = (
    Path("plastics") / "strap" / "property_package.py",
    Path("strap") / "property_package.py",
)
_PROCESS_MODEL_RELATIVE = (
    Path("plastics") / "strap" / "process_model.py",
    Path("strap") / "process_model.py",
)


def resolve_plastics_path(path: Optional[str] = None) -> Optional[Path]:
    raw = str(
        path if path is not None else os.getenv("DISSOLVE_PLASTICS_PATH") or ""
    ).strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def resolve_strap_file(
    root: Path, candidates: tuple[Path, ...],
) -> Optional[Path]:
    for relative in candidates:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def plastics_layout_diagnosis(root: Path) -> dict[str, Any]:
    """Say which layout DISSOLVE_PLASTICS_PATH actually is."""
    inner = resolve_strap_file(root, (Path("strap") / "property_package.py",))
    outer = resolve_strap_file(
        root, (Path("plastics") / "strap" / "property_package.py",),
    )
    if outer is not None:
        layout = "package_parent"
        expected_import = "plastics.strap (PYTHONPATH = this directory)"
    elif inner is not None:
        layout = "inner_package_dir"
        expected_import = (
            "plastics.strap requires DISSOLVE_PLASTICS_PATH to be the "
            "parent of this directory (the folder that contains the "
            "`plastics` package), not the inner package dir"
        )
    else:
        layout = "unrecognised"
        expected_import = (
            "neither plastics/strap/property_package.py nor "
            "strap/property_package.py exists under this path"
        )
    return {
        "root": str(root),
        "layout": layout,
        "property_package_path": str(outer or inner) if (outer or inner) else None,
        "process_model_path": (
            str(resolve_strap_file(root, _PROCESS_MODEL_RELATIVE))
            if resolve_strap_file(root, _PROCESS_MODEL_RELATIVE) else None
        ),
        "import_note": expected_import,
    }


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _outline_entry_id(node: ast.AST) -> Optional[str]:
    """One ChemicalsOutline element: a bare ID string or ChemicalDraft's ID."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and _call_name(node.func) == "ChemicalDraft":
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                return value
    return None


def _chemical_draft_ids(node: ast.AST) -> list[str]:
    """IDs in STRAP_chemicals_outline = ChemicalsOutline([ ... ])."""
    ids: list[str] = []
    if not isinstance(node, ast.Call) or _call_name(node.func) != "ChemicalsOutline":
        return ids
    if not node.args:
        return ids
    elements = node.args[0]
    if isinstance(elements, (ast.List, ast.Tuple)):
        for elt in elements.elts:
            ident = _outline_entry_id(elt)
            if ident:
                ids.append(ident)
    return ids


def package_chemical_ids_from_source(property_package: Path) -> frozenset[str]:
    """IDs declared on STRAP_chemicals_outline, from source, no BioSTEAM."""
    tree = ast.parse(property_package.read_text(encoding="utf-8"))
    collected: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "STRAP_chemicals_outline"
            for target in node.targets
        ):
            continue
        collected.extend(_chemical_draft_ids(node.value))
    return frozenset(collected)


def package_chemical_ids(
    plastics_root: Optional[Path] = None,
) -> Optional[frozenset[str]]:
    root = plastics_root or resolve_plastics_path()
    if root is None:
        return None
    source = resolve_strap_file(root, _PROPERTY_PACKAGE_RELATIVE)
    if source is None:
        return None
    return package_chemical_ids_from_source(source)
