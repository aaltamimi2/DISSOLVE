"""Owner-facing identity policy decision and unchanged Milan cost; selects no option."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
a=json.loads((ROOT/'state/identity-policy/analysis.json').read_text())
cost=json.loads((ROOT/'reports/pilot-v1/analysis.json').read_text())['all_attempt_budget']
kinds={
 'BJQHLKABXJIVAM':'Branched side-chain tetrahedral configuration (DEHP)',
 'TUXBECXCUZWDPY':'Epoxide tetrahedral configurations',
 'MEXURWLRXYTWGT':'Substituted cyclohexyl ring configurations',
 'BYLSIPUARIZAHZ':'Phenylethyl tetrahedral configurations',
 'SJPFBRJHYRBAGV':'Epoxide tetrahedral configurations',
 'GKFPPCXIBHQRQT':'Sugar-ring tetrahedral configurations',
 'QQKFUTGTZXPBGA':'Alkene E/Z geometry; not a tetrahedral-centre case',
 'OOHPORRAEMMMCX':'Polycyclic cage stereo encoding',
 'BQQUFAMSJAKLNB':'Fused-ring/epoxide tetrahedral configurations',
}
lines=[
 '# Identity policy decision for the owner, alongside campaign cost','',
 '**No policy has been selected or applied. Phase 2 remains unauthorised.** This characterization used pinned local SMILES, saved pilot identity observations and local optimized XYZ files. No cluster work, DFT rerun, product-database access or accepted-result mutation was performed.','',
 '## What the nine failures actually are','',
 '**9/9 input SMILES specify neither atom stereochemistry nor bond E/Z stereochemistry. 9/9 computed keys match the input’s first, connectivity block. In 9/9 cases the full InChIs also match after removing only stereo layers. Both RDKit and Open Babel agree on the complete computed InChIKey in 9/9 cases.** The third key block is unchanged throughout.','',
 'The check went beyond comparing the key’s second block: the saved coordinates were converted locally to full InChI, the resulting key was required to match both recorded perception engines, and all non-stereo InChI layers were compared directly with the pinned input. Differences are exclusively `/t`, `/m`, `/s` for the tetrahedral/ring cases, or `/b` for the alkene case. No isotope, protonation, formula, connectivity or hydrogen-layer difference was found.','',
 '| Source molecule | Input atom stereo specified? | Input bond stereo specified? | First block identical? | Difference |',
 '| --- | --- | --- | --- | --- |',
]
for r in a['cases']:
    lines.append(f"| {r['name']} | No (0) | No (0) | Yes | {kinds[r['input_inchikey'].split('-')[0]]}; `"+' / '.join(r['computed_stereo_layers'])+'` |')
lines += [
 '', 'The [nine-case CSV](nine-cases.csv) contains each exact pinned SMILES, full input key, full computed key, full input/computed InChI, layer comparison and geometry digest. Detailed RDKit potential-stereo observations are in `state/identity-policy/analysis.json`; potential-site counts are perception diagnostics, not counts of independent physical stereoisomers.','',
 'An XYZ file contains a concrete 3D arrangement from which stereo can be perceived, although it has no SMILES-style stereo annotations or explicit “unspecified in the source” status. Unspecified input stereo does not constrain that arrangement. Embedding and coordinate perception can therefore add a definite stereo description; this audit does not attribute the addition specifically to DFT rather than initial 3D preparation. The strict comparison treats additional stereo information as an identity mismatch even though the source supplied no conflicting stereo constraint.','',
 'DEHP is among the nine. The orchestrator reports that it already belongs to the product’s existing contaminant set; this audit did not query or alter that product set. For DEHP, the pinned unspecified key is `BJQHLKABXJIVAM-UHFFFAOYSA-N`, while both geometry-perception engines yield `BJQHLKABXJIVAM-BGYRXZFFSA-N`. Its DFT completed normally. The rejection is a mismatch in identity representation, not evidence of failed computation or a changed connectivity skeleton.','',
 '**The other eight are similar in the relevant policy sense:** they also have no input stereo specification, matching connectivity and exclusively added stereo layers. They are not all the same stereochemical kind: the benzoxazolyl/ethenyl benzoate is an E/Z case, and dodecahedrane is a cage-stereo encoding case; the others involve tetrahedral or ring arrangements. This does not assert that all eight share DEHP’s product membership, use classification, or stereo sensitivity.','',
 '## Three options — consequences and conditional acceptance projections','',
 'The identity decision applies to **55 executed structures**, not the isotope entry that never reached DFT. Thus strict acceptance is **46/55**, and each broader option would accept **55/55** in this observed pilot. The original historical dispositions remain 46 accepted, nine identity-policy failures and one isotope preflight failure out of 56. No historical record has been reclassified.','',
 '| Option | Hypothetical accepted pilot results | Projected accepted identities out of 5,833, conditional on valid execution | Conditional 95% rate range, scaled to 5,833 | Meaning for a results-table reader |',
 '| --- | ---: | ---: | ---: | --- |',
]
descriptions={
 'exact_full_inchikey':('1. Keep exact_full_inchikey','The accepted flag asserts equality of the complete perceived and source keys. Nine otherwise completed pilot surfaces remain rejected because computed stereo was absent from the source.'),
 'connectivity_only':('2. Require first-block connectivity match only','The accepted flag asserts the same connectivity block only; it does not validate stereo. A future result could pass despite contradicting stereo explicitly specified in its input. A first-block-only rule is also not an isotope-identity guarantee; it must not silently bypass the separate isotope policy.'),
 'connectivity_and_computed_stereo_with_source_constraints':('3. Connectivity plus computed-stereo provenance','The row keeps the source key and the actual computed full key separately. It represents one computed stereochemical arrangement of a source with unspecified stereo, not an experimentally established isomer or a mixture/ensemble average. Define source-declared stereo as a constraint: unspecified stereo can be filled in, but explicitly specified stereo must not be contradicted.'),
}
for key,p in a['projections_identity_conditional_on_execution'].items():
    label,meaning=descriptions[key];lo,hi=p['scaled_wilson_95_count_interval']
    lines.append(f"| {label} | {p['accepted']}/{p['denominator']} executed; {p['accepted']}/56 selected | {p['projected_count']:,.0f} | {lo:,.0f}–{hi:,.0f} | {meaning} |")
lines += [
 '', '**Option 3’s source-stereo preservation is an explicit proposed rule, not an adopted policy.** Simply recording computed stereo without checking any source-declared constraints has the same acceptance predicate as option 2, with better disclosure. The owner should specify whether preservation of declared stereo is mandatory. In this pilot either reading has the same 55/55 hypothetical acceptance: all 46 currently accepted cases already match full keys and all nine rejected cases declare no stereo.','',
 'For option 3, a table should expose at least `source_inchikey`, `source_smiles`, whether atom/bond stereo was specified in the source, `computed_inchikey`, full computed InChI or stereochemical structure, perception method and disagreements, connectivity/source-constraint checks, identity-policy/version and authority, and the exact geometry/surface digests. Retain the frozen one-conformer provenance. Never silently overwrite the source key or imply that one selected configuration is an average over unspecified isomers. Options 1 and 2 must also retain provenance and failure reasons; their accepted flags have the narrower meanings above.','',
 '### What the uncertainty numbers do and do not mean','',
 'The table multiplies the observed identity-acceptance rate by 5,833 and scales a **95% Wilson binomial rate interval** by the same population. These are conditional illustrations of small-sample uncertainty, **not design-valid population confidence intervals or predictive guarantees**: the pilot was deliberately selected across size and chemistry rather than sampled randomly. Selection bias, chemistry composition, unobserved failure modes and future perception disagreements are not covered. A 55/55 pilot pass rate does not establish a true 100% campaign pass rate; its conditional lower bound is about 93.47%.','',
 'The orchestrator’s **9/56** rate gives about **937** affected structures (conditional scaled interval **507–1,622**). Isolating the identity question uses **9/55**, giving about **954** affected structures; the corresponding accepted projection is **4,879**, with the **4,184–5,316** range above. Neither calculation is a census of future mismatches.','',
 'The main table conditions on a valid executed calculation for every logical target. Isotope disposition is still a separate owner decision: the exact local census found **9/5,833** labelled CHNO entries, all with existing parents. Their treatment must not be inferred from the pilot’s one isotope preflight failure. If the owner excludes those nine identities, the same conditional rate calculation on 5,824 eligible identities gives **4,871** strict acceptances (about **4,178–5,308**) versus **5,824** under either broader option (about **5,444–5,824**); nine source identities would be explicitly excluded. If the owner maps them to parents, reporting and acceptance must retain that mapping and its distinct-parent denominator. No isotope option is selected here.','',
 'For transparent accounting only, naively scaling the original all-cause dispositions would give 46/56 → **4,791** strict acceptances (conditional **4,092–5,250**) and 55/56 → **5,729** broader-policy acceptances (**5,282–5,815**). These conflate identity and preflight outcomes and extrapolate a known nonrepresentative isotope rate, so they are not the identity-policy projection used in the main table.','',
 '## Cost goes to the owner with the identity decision','',
 f"**The CPU-hour estimate is unchanged under all three identity policies:** **{cost['campaign_cpu_hours']:,.0f} CPU-hours**, with a conditional Milan bootstrap sensitivity range **{cost['bootstrap_cpu_hours_p05_p50_p95'][0]:,.0f}–{cost['bootstrap_cpu_hours_p05_p50_p95'][2]:,.0f} CPU-hours** for one attempt per structure under the existing workload model. All **55/55 executed OPT + COSMORS calculations completed normally**, including the nine identity-policy failures, and all 55 timings were already included in the fit. The pilot consumed **{cost['observed_total_attempt_hours']:.2f} serial attempt-hours**. Changing acceptance of these completed surfaces adds no DFT work and removes none of the time already spent.",'',
 'This remains the research / AMD EPYC 7763 (Milan) estimate, using the frozen serial ORCA recipe. An owner-authorised change to target count, conformer ensembles, retries or new calculations would require a separately updated budget; none is implied by these identity options. The isotope-specific target-count scenarios above likewise do not silently replace the existing campaign cost model.','',
 'The [completed pilot report](../pilot-v1/REPORT.md) contains the residuals, RAM and archive estimates, all failures and the C-1 hardware correction. The historical workstation anchors are Intel Core i7-13700 (Raptor Lake); Euler LOGIN alone is Broadwell. The campaign fit uses only Milan timings.','',
 '**Owner decision pending: choose the identity semantics alongside this cost estimate, resolve the separate isotope disposition, then decide whether to authorise Phase 2. No recommendation has been applied, no existing result status has changed, and no Phase 2 job has been submitted. Reported and stopped.**',
]
(ROOT/'reports/identity-policy/REPORT.md').write_text('\n'.join(lines)+'\n')
print(ROOT/'reports/identity-policy/REPORT.md')
