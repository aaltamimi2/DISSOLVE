from pathlib import Path
import json,datetime,hashlib,collections
ROOT=Path(__file__).resolve().parents[1];R=Path('/mnt/r/plastchem-euler');D=R/'progress-2026-09-17';D.mkdir(exist_ok=False)
sources={n:(ROOT/p).read_bytes() for n,p in {'campaign-summary.json':'state/campaign-v1/summary.json','processing-ledger.json':'state/thermodynamics-v1/processing-ledger.json','solvent-registry.json':'state/thermodynamics-v1/library-registry.json','controller.json':'state/campaign-v1/chunk-release-loop-latest.json'}.items()}
for n,b in sources.items():(D/n).write_bytes(b)
s=json.loads(sources['campaign-summary.json']);l=json.loads(sources['processing-ledger.json']);lib=json.loads(sources['solvent-registry.json']);q=json.loads(sources['controller.json']);v=json.loads((R/'octanol-post3883-2026-09-17/cumulative-validation/statistics.json').read_text());exp=json.loads((ROOT/'completed-contaminants-2026-09-17.metadata.json').read_text());utc=datetime.datetime.now(datetime.timezone.utc).isoformat();c=s['counts'];cpuh=sum(x['measured_terminal_attempt_hours'] for x in s['groups'].values());pred=sum(x['partition_count'] for x in l.values());fail=sum(x['failed_activity_count'] for x in l.values());states=dict(collections.Counter(x['status'] for x in lib['solvents']))
text=f'''# Contaminant campaign progress, 17 September 2026

Captured {utc}. This is a dated cumulative progress overview, not campaign completion or a replacement of the released 648-molecule package. Inputs below were read separately from live workers; the saved metadata is reproducible but is not an atomic scheduler freeze. The fixed batch-2 cumulative report remains 944 molecules.

| Campaign disposition | Count / 5,824 |
|---|---:|
| Converged and identity accepted | {c['converged']} |
| Failed | {c['failed']} |
| Running at collector snapshot | {c['running']} |
| Not yet run | {c['not_yet_run']} |
| Awaiting verification | {c['awaiting_verification']} |

Pinned arithmetic: 5,833 minus nine excluded isotope-labelled entries equals 5,824. Failure modes: {s['failures_by_mode']}. Completed-attempt cost is {cpuh:.3f} one-CPU hours; in-flight work is excluded. The separate >80-atom tail is terminal: 88 accepted, one geometry nonconvergence, denominator 89, {s['groups']['tail_gt80']['measured_terminal_attempt_hours']:.3f} completed-attempt CPU-hours. No failures are interpolated or silently retried.

## Thermodynamic coverage

The saved ledger contains {len(l)} panel-processed molecules and {pred} available solvent/water predictions. It retains {fail} failed solvent activities. Library states among 33 requested references: {states}. Full requested-panel completion is zero. Successful available-panel coverage is distinct from full requested-panel completion.

Audited validation-only dry-octanol coverage is 4,060 molecules. These checks establish numerical, identity and provenance consistency, not experimental accuracy. The accepted identity criterion is InChIKey connectivity-first-block agreement; full perceived keys and engine results are retained.

## Validation

All 410 currently selected qualified experimental references have audited predictions. MAE {v['MAE']:.6f}, RMSE {v['RMSE']:.6f}, bias {v['bias']:+.6f}; predicted-on-measured slope {v['slope']:.6f}, intercept {v['intercept']:.6f}. No empirical correction. The 410 entries represent 389 connectivity blocks; equal-block weighting gives MAE 1.065388. This is a selected available-reference set, not a random or representative sample of the full campaign. Measurement conditions/species are not resolved for every source; the calculation is for the specified neutral structure and dry octanol. Database qualification retains citations but does not constitute independent inspection of every primary paper.

[Validation report, anchors, named outliers and per-row citations]({R}/octanol-post3883-2026-09-17/cumulative-validation/REPORT.md). [Connectivity/range sensitivity]({R}/validation-sensitivity-20260917-n410/SENSITIVITY.md). Four workstation-anchor implementation checks remain separate in the [released report]({R}/progress-2026-09-14/REPORT.md); that release and its n=21 accuracy result are unchanged.

[Surface and numerical catch-up audit]({R}/audits/catchup-2026-09-17/REPORT.md) and [incremental audit]({R}/audits/incremental-2026-09-17/REPORT.md) jointly cover 3,919 distinct accepted surfaces and 3,883 numerical records. Newer records need later audit coverage. Octanol cohorts carry separate passed numerical audits and package manifests.

## Figures and data

- [Latest experimental parity: n=410]({ROOT}/2026-09-17-cumulative-parity-n410.png), 300 dpi; [CSV]({R}/octanol-post3883-2026-09-17/cumulative-validation/parity.csv).
- [Audited value distribution: n=2,851]({ROOT}/2026-09-17-cumulative-computed-value-distribution.png), earlier dated cohort, not all 4,060 audited octanol results.
- [Progress by chunk]({ROOT}/2026-09-17-cumulative-campaign-progress.png), earlier snapshot with 3,883 accepted entries.
- [Wall time versus atoms]({ROOT}/2026-09-17-cumulative-walltime-vs-atoms.png), 3,883 accepted runs; Milan pilot fit shown only over 15–80 atoms. Failed runs remain in cost/disposition accounting and are omitted from this accepted-run scatter.
- [Figure CSVs and cohort description]({R}/cumulative-figures-2026-09-17/REPORT.md).
- [Completed-contaminant CSV]({ROOT}/completed-contaminants-2026-09-17.csv): {exp['exported_molecules']} success-only rows, seven panel predictions each, {exp['octanol_validation_available']} audited octanol entries, snapshot {exp['snapshot_utc']}. Eleven molecules with failed activities are excluded from that CSV and listed in its metadata; all remain in campaign accounting. Missing solvents are blank.

## Operations and remaining work

Authorised concurrency is 64, 1 CPU and 4 GB per task; research, milan&cpu, euler09/10 excluded. Controller readback {q['utc']}: {q['status']}, running count {q.get('running_total_after',q.get('running_total_before'))}. Readbacks, changes and guards are in the saved controller.json and local campaign logs. A transient pending job 50511 caused one safety deferral; it belongs to another campaign and was untouched. SSH/scp use the own-directory multiplex socket and backoff; no rsync.

Remaining work: terminal accounting for all 5,824; return collection; all available panel activities; remaining solvent references and panel expansion; incremental numerical/surface checks for newer results; later octanol and measured-reference coverage; final frozen report and plots. Open owner questions remain **xylene identity** and **didecyl-phthalate reference discrepancy**. Neither is resolved here.

Bulk results remain under `/mnt/r/plastchem-euler/`. Reproduce this overview with `{ROOT}/scripts/report_current_progress_20260917.py` in a new dated output directory; it refuses overwrite. Calculation and validation scripts are linked in `{ROOT}/CONTAMINANT_METHODS_WORKFLOW.md` and copied into their pinned result packages. This overview's source metadata and script are pinned by artifacts.sha256.
'''
(D/'REPORT.md').write_text(text);(D/Path(__file__).name).write_bytes(Path(__file__).read_bytes());(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(D.iterdir()) if p.is_file() and p.name!='artifacts.sha256'));print(str(D/'REPORT.md'))
