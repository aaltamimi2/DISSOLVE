"""Experimental logKow supplement from verified releases, with no COSMO rerun.

Preserves all 1,179 prior reference choices and all earlier validation packages.
Both releases must already be independently verified; no release is modified.
"""
import collections
import csv
import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path

import duckdb
import numpy as np

B=Path('/mnt/r/plastchem-euler')
D=B/'phase10-v1'
REFERENCES=B/'combined-validation-references-2026-09-17-pubchem-final571/experimental-reference-candidates.csv'
REF_PIN='504e8135aa7da149dbbe2192d41a9c636c7868732dc7083219243738fceb7d84'
ANCHORS=B/'post1160-octanol-2026-09-15/opera-extension/cumulative-parity.csv'
ANCHOR_PIN='ffcaf178b6960d2e05274a3ce4e9e9ff650085abc6dbd356bdad9a7e5d67d3b7'
CLASSES={'HSDB_cited_experimental_or_literature_logKow','Sangster_curated_experimental_logP',
         'OPERA_curated_observed_LogP_PHYSPROP','CompTox_explicit_experimental_LogKow'}


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1048576),b''):h.update(block)
    return h.hexdigest()


def stats(rows,weights=None):
    assert rows
    x=np.array([r['measured_logKow'] for r in rows]);y=np.array([r['predicted_logKow'] for r in rows])
    assert np.isfinite(x).all() and np.isfinite(y).all()
    w=np.ones(len(rows)) if weights is None else np.asarray(weights,dtype=float)
    assert np.isfinite(w).all() and (w>0).all();w=w/w.sum()
    e=y-x;mx=float(w@x);my=float(w@y);variance=float(w@((x-mx)**2))
    slope=float(w@((x-mx)*(y-my))/variance) if variance>0 else None
    return dict(n=len(rows),MAE=float(w@abs(e)),RMSE=float(np.sqrt(w@(e*e))),bias=float(w@e),
        predicted_on_measured_slope=slope,intercept=my-slope*mx if slope is not None else None)


def references():
    assert sha(REFERENCES)==REF_PIN and sha(ANCHORS)==ANCHOR_PIN
    rows=list(csv.DictReader(REFERENCES.open()));assert len(rows)==1179
    assert len({r['input_inchikey'] for r in rows})==1179
    for r in rows:
        assert r['source_class'] in CLASSES and r['qualification'].startswith('qualified')
        assert r['observed_operator']=='=' and math.isfinite(float(r['measured_logKow']))
        assert r['solvent']=='octanol' and r['reference']=='water'
        assert all(r[k] for k in ['raw_reference_string','source_url','retrieved_utc','source_sha256'])
    anchors={r['input_inchikey']:r for r in csv.DictReader(ANCHORS.open()) if r['anchor']}
    assert {r['anchor'] for r in anchors.values()}=={'DEP','DBP','BBP','DEHP'}
    for r in rows:
        if r['input_inchikey'] in anchors:
            assert float(r['measured_logKow'])==float(anchors[r['input_inchikey']]['measured_logKow'])
    return rows,anchors


def check_pair(row,source,water_sha,octanol_sha,correction):
    assert row['solute_sha']==source['surface_sha256']
    assert row['water_sha']==water_sha and row['octanol_sha']==octanol_sha
    gw=source['control_activities']['water']
    x=(gw-row['gamma_octanol'])/math.log(10)
    values=[x,row['logK_x'],row['logK_concentration'],correction]
    assert all(math.isfinite(v) for v in values)
    error=max(abs(x-row['logK_x']),abs(x+correction-row['logK_concentration']))
    assert error<=1e-9
    return gw,error


def compare_releases(out):
    primary=B/'promotion-v1';extension=B/'promotion-ext39-v1'
    for root,folder in [(primary,B/'phase9-v1'),(extension,D)]:
        v=json.loads((folder/'delivery-verification.json').read_text())
        assert v['status']=='complete_delivery_verified' and v['manifest_sha256']==sha(root/'manifest.json')
    refs,anchors=references()
    primary_model=json.loads((primary/'provenance/phase8-manifest.json').read_text())
    extension_model=json.loads((extension/'provenance/manifest.json').read_text())
    water=next(s for s in primary_model['solvents'] if s['name']=='water')
    octanol=next(s for s in extension_model['solvents'] if s['name']=='1-octanol')
    physical=json.loads((extension/'provenance/qualified-physical-volumes-v4.json').read_text())
    octanol_volume=next(s['molar_volume_cm3_mol'] for s in physical['entries'] if s['solvent']=='1-octanol')
    correction=math.log10(water['B_volume']/octanol_volume)
    legacy_correction=math.log10(water['B_legacy_volume']/octanol['cavity_volume_cm3_mol'])
    refpath=extension/'provenance/primary-polymer-reference.json.gz'
    receipt=json.loads((extension/'provenance/primary-polymer-reference-receipt.json').read_text())
    assert sha(refpath)==receipt['reference_sha256']
    assert receipt['primary_release_manifest_sha256']==sha(primary/'manifest.json')
    with gzip.open(refpath,'rt') as f:original={r['inchikey']:r for r in map(json.loads,f)}
    assert len(original)==5830
    con=duckdb.connect();con.execute('SET threads=1');con.execute("SET memory_limit='256MB'")
    con.execute('SET temp_directory=?',[str(out/'duckdb-temp')])
    con.execute('''CREATE TEMP TABLE pairs AS SELECT a.input_inchikey,a.campaign_polymer,a.convention,
        a.logP_x-b.logP_x AS logK_x,a.logP_concentration-b.logP_concentration AS logK_concentration,
        a.ln_gamma_solvent AS gamma_octanol,a.solvent_surface_sha256 AS octanol_sha,
        b.solvent_surface_sha256 AS water_sha,a.solute_surface_sha256 AS solute_sha
        FROM read_parquet(?) a JOIN read_parquet(?) b
        ON a.input_inchikey=b.input_inchikey AND a.campaign_polymer=b.campaign_polymer
        AND a.temperature_K=b.temperature_K AND a.convention=b.convention
        WHERE a.product_solvent_key='1-octanol' AND b.product_solvent_key='water'
        AND a.temperature_K=298.15''',[str(extension/'partition.parquet'),str(primary/'partition.parquet')])
    assert con.execute('SELECT count(*) FROM pairs').fetchone()[0]==116600
    assert con.execute('''SELECT count(*) FROM (SELECT input_inchikey,count(*) n,
        max(logK_x)-min(logK_x) spread FROM pairs GROUP BY ALL HAVING n<>20 OR spread>1e-9)''').fetchone()[0]==0
    assert con.execute('''SELECT count(*) FROM pairs WHERE abs(logK_concentration-logK_x-
        CASE WHEN convention='normalized' THEN ? ELSE ? END)>1e-9''',[correction,legacy_correction]).fetchone()[0]==0
    invariant=con.execute('''SELECT max(spread) FROM (SELECT input_inchikey,
        max(logK_x)-min(logK_x) spread FROM pairs GROUP BY input_inchikey)''').fetchone()[0]
    values=con.execute("SELECT * FROM pairs WHERE campaign_polymer='pe' AND convention='normalized' ORDER BY input_inchikey").fetchall()
    columns=[d[0] for d in con.description];con.close()
    predicted={};check_error=0.
    for values_row in values:
        r=dict(zip(columns,values_row));key=r['input_inchikey'];source=original[key]
        gw,error=check_pair(r,source,Path(water['B']).stem,octanol['surface_sha256'],correction)
        check_error=max(check_error,error)
        predicted[key]=dict(input_inchikey=key,ln_gamma_water=gw,ln_gamma_octanol=r['gamma_octanol'],
            logK_x=r['logK_x'],predicted_logKow=r['logK_concentration'],
            volume_correction_log10=correction,water_volume_cm3_mol=water['B_volume'],octanol_volume_cm3_mol=octanol_volume,
            solute_surface_sha256=r['solute_sha'],water_surface_sha256=r['water_sha'],octanol_surface_sha256=r['octanol_sha'])
    assert len(predicted)==5830
    parity=[]
    for source in refs:
        key=source['input_inchikey'];assert key in predicted
        measured=float(source['measured_logKow']);r=dict(source,**{k:v for k,v in predicted[key].items() if k not in source})
        r.update(measured_logKow=measured,residual=r['predicted_logKow']-measured,
            anchor=anchors.get(key,{}).get('anchor',''),
            owner_source_question='Retained PubChem 9.05 versus primary abstract 8.83 +/-0.05; owner decision remains open' if key=='PGIBJVOPLXHHGS-UHFFFAOYSA-N' else '')
        parity.append(r)
    groups=collections.Counter(r['input_inchikey'].split('-')[0] for r in parity)
    summary=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),cohort=5830,
        reference_entries=1179,reference_connectivity_blocks=len(groups),unmatched_reference_entries=0,
        primary_manifest_sha256=sha(primary/'manifest.json'),extension_manifest_sha256=sha(extension/'manifest.json'),
        selected_reference_sha256=REF_PIN,prior_anchor_source_sha256=ANCHOR_PIN,
        statistics=stats(parity),connectivity_equal_weight_statistics=stats(parity,[1/groups[r['input_inchikey'].split('-')[0]] for r in parity]),
        source_class_statistics={c:stats([r for r in parity if r['source_class']==c]) for c in sorted(CLASSES)},
        measured_range_statistics={label:stats(rs) for label,rs in [
            ('below_4',[r for r in parity if r['measured_logKow']<4]),
            ('4_through_7',[r for r in parity if 4<=r['measured_logKow']<=7]),
            ('above_7',[r for r in parity if r['measured_logKow']>7])] if rs},
        maximum_cross_polymer_convention_logK_x_difference=invariant,
        maximum_direct_activity_reconstruction_error=check_error,volume_correction_log10=correction,
        no_recalibration=True,all_prior_reference_choices_preserved=True,
        named_largest_residuals=sorted(parity,key=lambda r:abs(r['residual']),reverse=True)[:15])
    for filename,rows in [('all-frozen-logKow.csv',list(predicted.values())),('parity.csv',parity),
        ('anchors.csv',[r for r in parity if r['anchor']]),('largest-residuals.csv',summary['named_largest_residuals'])]:
        with (out/filename).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    (out/'statistics.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary,parity


def render(out,summary,rows):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':14,'axes.titlesize':14,'axes.labelsize':14,'xtick.labelsize':14,
        'ytick.labelsize':14,'legend.fontsize':14,'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
    x=np.array([r['measured_logKow'] for r in rows]);y=np.array([r['predicted_logKow'] for r in rows]);s=summary['statistics']
    fig,ax=plt.subplots(figsize=(9,9));ax.scatter(x,y,s=16,alpha=.55,color='#245780',edgecolors='none')
    lo=min(x.min(),y.min())-.5;hi=max(x.max(),y.max())+.5;ax.plot([lo,hi],[lo,hi],'k--',linewidth=1)
    ax.set(xlim=(lo,hi),ylim=(lo,hi),xlabel='Experimental logKow (selected references)',
        ylabel='Predicted logKow (dry octanol, exact x=0)',title='Octanol–water validation');ax.set_aspect('equal')
    fig.text(.13,.035,f"n={s['n']}; MAE={s['MAE']:.2f}; RMSE={s['RMSE']:.2f}; bias={s['bias']:+.2f}",color='black',fontsize=14)
    fig.tight_layout(rect=(0,.08,1,1));fig.savefig(out/'parity.png',dpi=300);plt.close(fig)
    fig,ax=plt.subplots(figsize=(9,7));ax.scatter(x,y-x,s=16,alpha=.55,color='#245780',edgecolors='none')
    ax.axhline(0,color='black',linestyle='--',linewidth=1)
    ax.set(xlabel='Experimental logKow',ylabel='Predicted − experimental logKow',title='Residuals across measured logKow')
    fig.tight_layout();fig.savefig(out/'residuals.png',dpi=300);plt.close(fig)


def main():
    out=D/'experimental-validation-v1';assert not out.exists(),'Preserve every completed prior package'
    # Fail before creating output if either delivery is absent or unverified.
    for folder in [B/'phase9-v1',D]:assert json.loads((folder/'delivery-verification.json').read_text())['status']=='complete_delivery_verified'
    out.mkdir();summary,rows=compare_releases(out);render(out,summary,rows);s=summary['statistics']
    (out/'REPORT.md').write_text(f'''# Frozen-cohort octanol–water experimental comparison

Both complete releases were independently verified before this supplement. Neither release nor any earlier validation package was modified. All 5,830 predictions are reconstructed from the released water/octanol tables; 1,179 selected experimental-reference entries (1,143 connectivity blocks) are available, and all are matched. The 4,651 other frozen structures have no selected reference in this pinned compilation; this is not a prediction failure.

n={s['n']}; MAE={s['MAE']:.4f}; RMSE={s['RMSE']:.4f}; bias={s['bias']:+.4f}; predicted-on-measured slope={s['predicted_on_measured_slope']:.4f}, intercept={s['intercept']:.4f}. Regression is descriptive only: no empirical correction was fitted into or applied to any prediction. Equal-connectivity-weight metrics, source-class metrics and measured-range strata are reported separately in statistics.json. Connectivity aliases and overlapping compilation sources are not independent experimental replicates.

All 1,179 prior point-value choices are preserved. Sources comprise explicitly qualified OPERA observed/PHYSPROP, CompTox experimental, HSDB-cited and Sangster-curated entries; no predicted database column enters the experimental values. Each parity row retains the original numeric observation, citation, URL, retrieval time and source hash. Primary papers and measurement conditions have not all been individually rechecked, so this is accuracy against the cited curated reference set, not universal experimental validation. The model uses neutral species and dry pure octanol, while literature conditions may include mutually saturated phases or pH effects; high-logKow measurements can be method-dependent. These limitations are not corrected empirically.

DEP, DBP, BBP and DEHP are named in anchors.csv with unchanged selected measured values. Largest residuals are named in largest-residuals.csv and statistics.json; no outlier was trimmed. Didecyl phthalate retains its previously selected 9.05 value and an explicit owner-question flag (primary abstract 8.83 +/-0.05); no source decision is made here. Generic xylene identity remains an owner question and is not resolved by this water/octanol comparison.

logK_x=(ln gamma_water-ln gamma_octanol)/ln(10); logK_concentration=logK_x+log10(V_water/V_octanol). The physical volume correction is {summary['volume_correction_log10']:.8f}. It was checked directly from the original archived water activities and new octanol activities for every molecule, and against all ten polymer differences under both ensemble conventions. The common polymer term cancels; maximum mole-fraction disagreement across the 20 differences is {summary['maximum_cross_polymer_convention_logK_x_difference']:.3g}. Only the normalized, documented physical-volume concentration basis is compared to experimental logKow; cavity-based existing-convention values are checked separately and not substituted.

![Parity](parity.png)

![Residuals](residuals.png)

Both PNGs are 300 dpi with uniform black text, a full numeric range and no point labels obscuring the data. CSV tables retain all numbers and citations. Reproduce with /home/aaltamimi2/plastchem-euler/scripts/validate_phase10_experimental.py after both delivery verifiers pass. Source release/reference pins are in statistics.json. The published 21-molecule and subsequent historical packages remain unchanged.
''')
    (out/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    (out/'SHA256SUMS').write_text(''.join(sha(p)+'  '+p.name+'\n' for p in sorted(out.iterdir()) if p.is_file() and p.name!='SHA256SUMS'))
    print(json.dumps({k:v for k,v in summary.items() if k!='named_largest_residuals'},indent=2))


if __name__=='__main__':main()
