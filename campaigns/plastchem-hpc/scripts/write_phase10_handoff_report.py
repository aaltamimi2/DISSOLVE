"""Write the A-10 handoff from completed, verified receipts; never alter releases."""
import datetime
import csv
import hashlib
import json
import shutil
from pathlib import Path

R = Path(__file__).resolve().parents[1]
B = Path('/mnt/r/plastchem-euler')
D = B / 'phase10-v1'
OUT = R / 'reports/phase10-2026-09-24'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    watcher = read(D / 'final-validation-watch-status.json')
    assert watcher['status'] == 'supplements_complete_agent_and_figure_review_required'
    experiment = D / 'experimental-validation-v1'
    reconciliation = D / 'final-outcome-reconciliation/summary.json'
    assert sha(reconciliation) == watcher['reconciliation_sha256']
    assert sha(experiment / 'statistics.json') == watcher['statistics_sha256']
    outcome = read(reconciliation)
    stats = read(experiment / 'statistics.json')
    original = B / 'promotion-v1'
    extension = B / 'promotion-ext39-v1'
    primary_pin = sha(original / 'manifest.json')
    extension_pin = sha(extension / 'manifest.json')
    assert stats['primary_manifest_sha256'] == primary_pin
    assert stats['extension_manifest_sha256'] == extension_pin
    assert outcome['status'] == 'final_outcomes_reconciled'
    seal = read(D / 'release-seal.json')
    verified = read(D / 'delivery-verification.json')
    assert seal['manifest_sha256'] == extension_pin == verified['manifest_sha256']
    assert verified['status'] == 'complete_delivery_verified'
    assert read(D / 'raw-audit-watch-status.json')['status'] == 'complete_raw_audit_agent_review_required'
    summary = read(extension / 'summary.json')
    raw = read(D / 'raw-lle-audit-v1/summary.json')
    cost = read(D / 'terminal-cost-accounting/summary.json')
    gate = read(D / 'calibration-clearance.json')
    assert summary['partition_rows'] == 4547400 and summary['LLE_rows'] == 227370
    assert outcome['combined_unresolved_rows'] == 327 and outcome['extension_exceptions_matched'] == 76
    assert stats['no_recalibration'] and stats['all_prior_reference_choices_preserved']
    files = {
        'extension-release-seal.json': D / 'release-seal.json',
        'extension-delivery-verification.json': D / 'delivery-verification.json',
        'final-outcome-reconciliation.json': reconciliation,
        'experimental-statistics.json': experiment / 'statistics.json',
        'octanol-parity.png': experiment / 'parity.png',
        'octanol-residuals.png': experiment / 'residuals.png',
    }
    for name, source in files.items():
        assert source.stat().st_size < 10_000_000
        shutil.copyfile(source, OUT / name)
        assert sha(source) == sha(OUT / name)
    s = stats['statistics']
    same = stats['connectivity_equal_weight_statistics']
    qualified = outcome['contaminants_with_all_LLE_qualified']
    affected = outcome['contaminants_with_any_unresolved_LLE']
    source_rows = '\n'.join(
        f"| {name} | {v['n']} | {v['MAE']:.4f} | {v['RMSE']:.4f} | {v['bias']:+.4f} |"
        for name, v in stats['source_class_statistics'].items())
    status_rows = '\n'.join(f'| {name} | {n:,} |' for name, n in sorted(raw['statuses'].items()))
    anchors = list(csv.DictReader((experiment / 'anchors.csv').open()))
    anchor_rows = '\n'.join(
        f"| {r['anchor']} | {float(r['measured_logKow']):.2f} | {float(r['predicted_logKow']):.4f} | {float(r['residual']):+.4f} |"
        for r in sorted(anchors, key=lambda r: ['DEP','DBP','BBP','DEHP'].index(r['anchor'])))
    outlier_rows = '\n'.join(
        f"| {r['name'].replace('|','/')} | {r['measured_logKow']:.2f} | {r['predicted_logKow']:.4f} | {r['residual']:+.4f} |"
        for r in stats['named_largest_residuals'][:8])
    report = f'''# A-10 complete handoff

Both separately keyed releases are sealed and independently verified. The original **promotion-v1** remains unchanged; **promotion-ext39-v1** supplies the authorized additional solvents. Product promotion belongs to the orchestrator and is not claimed here.

| Package | Contaminants | Solvents | Frozen polymers | Partition rows | LLE status rows |
|---|---:|---:|---:|---:|---:|
| promotion-v1 | 5,830 | 32 | 10 | 3,731,200 | 373,120 |
| promotion-ext39-v1 | 5,830 | 39 | 10 | 4,547,400 | 227,370 |
| Union | 5,830 | 71 | 10 | 8,278,600 | 600,490 |

Partition rows retain both conventions, with mole-fraction and concentration values as separate columns, at 298.15 K and exact solute x=0. The original LLE panel retains room and literal workbook high temperatures; the extension has room temperature only. The 71-key union covers all 69 common solvents plus the two retained panel identities. Cohort and polymer-product-map files are byte-identical across packages. The frozen cohort is 5,803 main-tier plus 27 tier-2 accepted structures; later accepted returns and later-completed polymer ensembles are separate work.

Identity acceptance follows the owner's connectivity policy: input and coordinate-perceived identities and perception-engine agreement remain recorded, rather than silently equating full stereochemical keys. The nine deuterated structures remain explicitly excluded under the isotope decision; no parent mapping or interpolated molecule was added.

The ten ensembles are EVOH, nylon6, nylon66, PC, PE, PET, PP, PS, PVC and PVDF, with 236 conformers. PETG, polyethersulfone, polyurethane and nitrocellulose are absent from this frozen snapshot, not negative predictions. PE's mapping to LDPE and HDPE does not introduce crystallinity data. Existing polymer S(T) remains the legacy source; this release does not replace it or add PFAS calculations.

## Release identities and timing

- Original: `/mnt/r/plastchem-euler/promotion-v1/`, manifest `{primary_pin}`; sealed 2026-09-24 17:18:27 UTC and independently verified at 17:20:09 UTC.
- Extension: `/mnt/r/plastchem-euler/promotion-ext39-v1/`, manifest `{extension_pin}`; manifest timestamp {summary['utc']}; independent verification {verified['utc']}.
- Extension payload: {seal['file_count']} files including manifest, {seal['total_bytes']:,} bytes. Both releases remain immutable.
- Cost gate: {gate['projected_CPU_h']:.3f} projected CPU-hours, below the 510 limit. Actual extension allocations: **{cost['total_extension_allocated_CPU_h']:.3f} CPU-hours**, including calibration, the failed initial control checks, original tasks and tail helpers. No production/helper task failed. ORCA surface generation and local export/transport costs are separate.

## Numerical qualification and limits

All 5,830 contaminants were evaluated in both packages. The extension qualifies every requested LLE system for **{summary['fully_qualified_contaminants']:,}/5,830** contaminants; its 76 unresolved systems affect 29. Across both packages, **{qualified:,}/5,830** have all LLE systems qualified and **{affected}/5,830** have at least one unresolved system. All 327 unresolved rows remain explicit in the combined exception table. Every extension exception matches its terminal record by identity, solvent, temperature and status.

| Extension LLE status | Systems |
|---|---:|
{status_rows}

Independent arithmetic/provenance checks cover every delivered partition and LLE row, exact coverage and uniqueness, input and surface hashes, CPU provenance, sign and volume conventions, and preservation of null unresolved verdicts. All 11,660 original water/hexane production controls agree exactly. The extension raw audit reconstructed {raw['reconstructed_counts']['checked_grids']:,} saved grids, {raw['reconstructed_counts']['checked_ties']:,} tie lines and {raw['reconstructed_counts']['checked_cache_points']:,} cached points. Maximum endpoint chemical-potential residual is {raw['maximum_errors']['maximum_mu_residual']:.3g} RT. Raw-audit snapshots match the delivered tables. These checks validate numerical consistency and provenance, not universal experimental accuracy; they do not constitute a second COSMOspace solve.

The earlier 8.2 comparison against commercial computed references covered 256 solvent/PVC pairs: normalized MAE 0.2410 and RMSE 0.3184, existing-convention MAE 0.5581 and RMSE 0.6355. Its LLE verdict comparisons retain both workbook layout readings and both 15% bases. Those references are computed, not experimental. The 59 historical water differences remain documented finite-dilution reference differences. The two comparison routes differ simultaneously in engine, parameterisation and re-optimized geometry; no parameterisation-only attribution is supported.

## Experimental octanol–water comparison

All 5,830 water/octanol predictions were reconstructed from the verified releases. The pinned measured-reference compilation matches **{s['n']:,} entries / {stats['reference_connectivity_blocks']:,} connectivity blocks**. The other 4,651 frozen structures lack selected references in that compilation; their predictions are present.

**MAE {s['MAE']:.4f}; RMSE {s['RMSE']:.4f}; bias {s['bias']:+.4f}; predicted-on-measured slope {s['predicted_on_measured_slope']:.4f}; intercept {s['intercept']:+.4f}.** Equal weight per connectivity gives MAE {same['MAE']:.4f}, RMSE {same['RMSE']:.4f}, bias {same['bias']:+.4f}. No outlier was trimmed and no empirical correction was applied. Each row retains its citation, URL, retrieval time and source digest; prior point-value choices and historical validation packages are unchanged.

| Reference source class | n | MAE | RMSE | Bias |
|---|---:|---:|---:|---:|
{source_rows}

| Anchor | Selected measured logKow | Predicted | Residual |
|---|---:|---:|---:|
{anchor_rows}

The largest absolute residuals are retained and named here; full keys and individual citations are in the CSVs.

| Contaminant | Selected measured logKow | Predicted | Residual |
|---|---:|---:|---:|
{outlier_rows}

This larger reference set is distinct from the historical 21-molecule subset. Its overall slope is below one; the earlier subset's slope pattern must not be generalized to this cohort. High-logKow entries above 7 still show mean overprediction of {stats['measured_range_statistics']['above_7']['bias']:+.4f} ({stats['measured_range_statistics']['above_7']['n']} entries), while several of the largest residuals are polar molecules. These are findings, not a basis for trimming or recalibration.

Only explicitly selected measured/observed source columns enter this comparison. Primary papers and conditions have not all been rechecked individually. Aliases and overlapping compilations are not independent replicates. The model uses neutral species and dry pure octanol; experimental phase saturation, ionization and measurement-method differences remain limitations. This octanol–water check does not validate every solvent/polymer prediction or establish solid-liquid solubility.

The physical concentration correction is log10(V_water/V_octanol) = **{stats['volume_correction_log10']:.8f}**. Every molecule was checked against raw water/octanol activities and all ten polymer cancellations under both conventions. Maximum cross-polymer/convention mole-fraction difference: {stats['maximum_cross_polymer_convention_logK_x_difference']:.3g}. The experimental comparison uses normalized physical volumes; cavity-based values are kept separate.

![Predicted versus measured logKow](octanol-parity.png)

![Residuals](octanol-residuals.png)

Both plots are 300 dpi. Named anchors, named largest residuals, source/range strata and all CSV values are in `/mnt/r/plastchem-euler/phase10-v1/experimental-validation-v1/`. The exact combined exception table is `/mnt/r/plastchem-euler/phase10-v1/final-outcome-reconciliation/combined-unresolved.csv`.

## Owner questions and reproduction

Generic xylene identity and the didecyl-phthalate measured-reference choice remain open. Both partition conventions and the original workbook layout/basis ambiguity remain visible. The new normalized volumes include documented near-melting/subcooled-liquid caveats; dipentene represents the computed limonene stereoisomer, not an arbitrary commercial mixture. No unresolved LLE row should be used as a qualified verdict; no fusion correction or solid-liquid equilibrium is claimed.

All scripts are in `/home/aaltamimi2/plastchem-euler/scripts/`, using `/home/aaltamimi2/.venvs/cosmo-logp/bin/python`: `audit_phase10_results.py`, `build_phase10_release.py`, `verify_phase10_delivery.py`, `audit_phase10_raw_lle.py`, `reconcile_phase10_final_outcomes.py`, `validate_phase10_experimental.py`, and `write_phase10_handoff_report.py`. Read the scripts' immutable-output guards before rerunning; preserve existing packages and reconcile any partial output. Collection, execution, surface/code pins and detailed chronology are in `/mnt/r/plastchem-euler/phase10-v1/` and `REPORT.md` beside this handoff. COSMObase and surface files remain outside git. No product writes or repository push occurred.

The sealed extension preserves its pre-verification checker source. Its parameterized CREATE VIEW call failed under DuckDB 1.5.5 before numerical SQL assertions. The corrected workspace verifier uses the equivalent relation API, with all assertions unchanged; its exact hash is in the successful verification receipt. Corrected companion code and reproduction evidence are at `/mnt/r/plastchem-euler/phase10-v1/verification-compatibility-v1/`. No sealed data or manifest was changed to repair this tooling error.
'''
    (OUT / 'HANDOFF.md').write_text(report)
    receipt = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   primary_manifest_sha256=primary_pin, extension_manifest_sha256=extension_pin,
                   script_sha256=sha(Path(__file__)), handoff_sha256=sha(OUT / 'HANDOFF.md'),
                   copied_files={name: sha(OUT / name) for name in files},
                   figures_require_agent_visual_review=True)
    (OUT / 'handoff-report-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
