"""Reconcile current ORCA/panel coverage against immutable A-10 releases.
Metadata and saved-result hashes only; no calculations or release mutations.
"""
import argparse, collections, csv, datetime, hashlib, io, json
from pathlib import Path
R=Path(__file__).resolve().parents[1]
B=Path('/mnt/r/plastchem-euler')
pins={}
def read(path):
    path=Path(path); raw=path.read_bytes()
    pins[str(path)]=hashlib.sha256(raw).hexdigest()
    return json.loads(raw)
def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--audit',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path);args=ap.parse_args()
    out=args.output.resolve();assert out.is_relative_to(B) and not out.exists()
    main=read(R/'state/campaign-v1/summary.json')
    tier=read(R/'state/tier2-v1/summary.json')
    ledger=read(R/'state/tier2-v1/thermodynamics/processing-ledger.json')
    records={}
    for p in sorted((R/'state/tier2-v1/records').glob('*.json')):
        d=read(p)
        if d.get('status')=='converged':records[d['inchikey']]=d
    assert set(ledger)<=set(records), 'Ledger contains nonaccepted structure'
    missing=sorted(set(records)-set(ledger))
    for key,rec in ledger.items():
        d=read(rec['result_path'])
        assert pins[rec['result_path']]==rec['result_sha256']
        assert d['input_inchikey']==key and d['solute_surface_sha256']==records[key]['surface_sha256']
    cohort=read(B/'promotion-v1/provenance/cohort.json')['rows']
    released={r['inchikey'] for r in cohort}
    assert len(released)==5830
    audit=read(args.audit/'manifest.json')
    assert audit['status']=='completed_snapshot_verified'
    latest=read(args.audit/'sealed-thermodynamics/processing-ledger.json')
    numeric=read(args.audit/'sealed-thermodynamics/production-record-audit.json')
    surfaces=read(args.audit/'completed-surface-audit.json')
    assert numeric['failed']==surfaces['failed']==0
    assert numeric['passed']==surfaces['passed']==len(latest)
    for rel in ['sealed-thermodynamics/processing-ledger.json','sealed-thermodynamics/production-record-audit.json','completed-surface-audit.json']:
        assert digest(args.audit/rel)==audit['files'][rel]
    audited={k for k,v in latest.items() if k in ledger and v['result_sha256']==ledger[k]['result_sha256']}
    post=set(records)-released
    refresh=read(R/'reports/tier2-v1/diphenyl-refresh-verification.json')
    unchanged_refresh={r['inchikey'] for r in refresh['rows'] if r['inchikey'] in ledger and r['after_sha256']==ledger[r['inchikey']]['result_sha256'] and r['activities_unchanged'] and r['other_31_pairs_unchanged']}
    proofs=[]
    for release,phase in [('promotion-v1','phase9-v1'),('promotion-ext39-v1','phase10-v1')]:
        m=read(B/release/'manifest.json');v=read(B/phase/'delivery-verification.json')
        assert m['status']=='complete' and v['status']=='complete_delivery_verified'
        assert digest(B/release/'manifest.json')==v['manifest_sha256']
        proofs.append(v)
    outcomes=read(B/'phase10-v1/final-outcome-reconciliation/summary.json')
    stats=read(B/'phase10-v1/experimental-validation-v1/statistics.json')
    throttle=read(R/'state/phase83-v1/throttle-latest.json')
    running=sum(throttle['running_after'].values())
    assert throttle['status']=='verified' and running<=throttle['cap']==64
    polymers=read(R/'state/polymer-v1/summary.json')
    map_path=B/'promotion-v1/polymer-product-map.csv'
    map_raw=map_path.read_bytes();pins[str(map_path)]=hashlib.sha256(map_raw).hexdigest()
    frozen_polymers={row['campaign_polymer'] for row in csv.DictReader(io.StringIO(map_raw.decode()))}
    polymer_groups=collections.defaultdict(collections.Counter)
    polymer_species=collections.defaultdict(lambda: collections.defaultdict(set))
    for path in sorted((R/'state/polymer-v1/records').glob('*.json')):
        record=read(path)
        polymer_groups[record['input']['polymer']][record['status']]+=1
        polymer_species[record['input']['polymer']][record['input']['species']].add(record['input']['inchikey'].split('-')[0])
    assert sum(sum(c.values()) for c in polymer_groups.values())==polymers['counts']['denominator']
    complete_polymers={p for p,c in polymer_groups.items() if set(c)=={'converged'}}
    assert frozen_polymers<=complete_polymers
    assert all(len(blocks)==1 for species in polymer_species.values() for blocks in species.values())
    mixed_groups={p for p,species in polymer_species.items() if len(set().union(*species.values()))>1}
    assert not mixed_groups & frozen_polymers, 'Frozen ensemble spans multiple connectivities'
    # A later panel prediction is not an exact-zero polymer/LLE release row.
    # This is a coverage inventory, not authorization to expand the frozen grid.
    separate_scope=dict(
        frozen_contaminants=len(released),frozen_polymer_ensembles=sorted(frozen_polymers),
        later_accepted_contaminant_keys=sorted(post),
        later_accepted_with_legacy_panel=sorted(post&set(ledger)),
        later_accepted_not_in_frozen_exact_zero_releases=len(post),
        partition_rows_absent_for_later_contaminants_at_frozen_grid=len(post)*71*len(frozen_polymers)*2,
        lle_rows_absent_for_later_contaminants_at_frozen_grid=len(post)*(32*2+39),
        polymer_conformer_statuses={p:dict(c) for p,c in sorted(polymer_groups.items())},
        complete_polymer_campaign_groups_outside_frozen_release=sorted(complete_polymers-frozen_polymers),
        complete_single_connectivity_ensembles_outside_frozen_release=sorted(complete_polymers-frozen_polymers-mixed_groups),
        multi_connectivity_groups_requiring_representation_decision={p:{s:next(iter(b)) for s,b in sorted(polymer_species[p].items())} for p in sorted(complete_polymers & mixed_groups)},
        interpretation='Inventory only. Both releases retain their frozen cohort and polymer snapshot; future separately keyed extensions are not represented here. No missing prediction is inferred from a legacy-panel value.')
    label_review=read(R/'reports/tier2-v1/cellulase-label-review.json')
    remaining=[
        'Finish terminal disposition, collection and serial processing for all 270 tier-2 targets.',
        'Audit and export each later accepted result; preserve failed and unavailable values.',
        'Later accepted tier-2 molecules have the legacy solvent/water panel only; they are outside the frozen 5,830 exact-zero polymer/LLE releases. Do not equate these scopes.',
        f"Complete the {polymers['counts']['denominator'] - polymers['counts']['converged']} polymer conformers outside the accepted set; retain TIMEOUT distinctly from chemistry failure.",
        'Generic xylene identity and didecyl-phthalate reference remain owner questions.',
        'Review the source label Cellulase/CAS 61788-77-0 versus the pinned beta-cellotriose structure; original labels and counts preserved.'
    ]
    if complete_polymers & mixed_groups:
        remaining.append('Resolve serving representation for the explicitly listed multi-connectivity polymer groups; do not pool species by their campaign label or infer material proportions from electronic energies.')
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),goal_complete=False,
        reconciliation_script_sha256=digest(Path(__file__)),
        main=main['counts'],tier2=tier['counts'],accepted_tier2_record_count=len(records),
        tier2_processed=len(ledger),tier2_awaiting_processing=missing,
        tier2_partition_predictions=sum(v['partition_count'] for v in ledger.values()),
        tier2_activity_failures=sum(v['failed_activity_count'] for v in ledger.values()),
        post_release_accepted=len(post),post_release_current_hash_audited=len(post&audited),
        post_release_not_covered_by_current_audit=sorted(post-audited),
        original_27_current_hashes_bound_to_verified_volume_refresh=len(released&unchanged_refresh),
        release_verifications=proofs,combined_LLE_unresolved=outcomes['combined_unresolved_rows'],
        combined_contaminants_with_unresolved_LLE=outcomes['contaminants_with_any_unresolved_LLE'],
        experimental_statistics=stats['statistics'],polymer_counts=polymers['counts'],
        coverage_by_calculation_scope=separate_scope,
        source_label_review=dict(key=label_review['input_inchikey'],status=label_review['status'],artifact=str(R/'reports/tier2-v1/CELLULASE-LABEL-REVIEW.md')),
        scheduler=dict(utc=throttle['utc'],running=running,cap=64),remaining=remaining,
        scope='Captured per-file metadata, current ledger/result hash binding and prior audit evidence; not simultaneous scheduler census or independent COSMOspace solve.',source_sha256=pins)
    out.mkdir(parents=True)
    (out/'review.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ['source_sha256','release_verifications']}))
if __name__=='__main__':main()
