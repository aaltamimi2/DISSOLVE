# Identity policy decision for the owner, alongside campaign cost

**No policy has been selected or applied. Phase 2 remains unauthorised.** This characterization used pinned local SMILES, saved pilot identity observations and local optimized XYZ files. No cluster work, DFT rerun, product-database access or accepted-result mutation was performed.

## What the nine failures actually are

**9/9 input SMILES specify neither atom stereochemistry nor bond E/Z stereochemistry. 9/9 computed keys match the input’s first, connectivity block. In 9/9 cases the full InChIs also match after removing only stereo layers. Both RDKit and Open Babel agree on the complete computed InChIKey in 9/9 cases.** The third key block is unchanged throughout.

The check went beyond comparing the key’s second block: the saved coordinates were converted locally to full InChI, the resulting key was required to match both recorded perception engines, and all non-stereo InChI layers were compared directly with the pinned input. Differences are exclusively `/t`, `/m`, `/s` for the tetrahedral/ring cases, or `/b` for the alkene case. No isotope, protonation, formula, connectivity or hydrogen-layer difference was found.

| Source molecule | Input atom stereo specified? | Input bond stereo specified? | First block identical? | Difference |
| --- | --- | --- | --- | --- |
| Bis(2-ethylhexyl) phthalate | No (0) | No (0) | Yes | Branched side-chain tetrahedral configuration (DEHP); `t19-,20+` |
| Oxirane, 2,2',2''-[ethylidynetris(4,1-phenyleneoxymethylene)]tris- | No (0) | No (0) | Yes | Epoxide tetrahedral configurations; `t26-,27-,28+ / m1 / s1` |
| 1-(4-Ethylcyclohexyl)-4-[4-(4-methylcyclohexyl)phenyl]benzene | No (0) | No (0) | Yes | Substituted cyclohexyl ring configurations; `t20-,21-,22+,23-` |
| 2,4,6-Tris(1-phenylethyl)phenol | No (0) | No (0) | Yes | Phenylethyl tetrahedral configurations; `t21-,22-,23+ / m0 / s1` |
| N,N,N',N'-Tetrakis(2,3-epoxypropyl)-m-xylene-alpha,alpha'-diamine | No (0) | No (0) | Yes | Epoxide tetrahedral configurations; `t17-,18-,19-,20+ / m0 / s1` |
| 6-(2-Carboxy-4,5-dihydroxy-6-methoxyoxan-3-yl)oxy-4,5-dihydroxy-3-methoxyoxane-2-carboxylic acid | No (0) | No (0) | Yes | Sugar-ring tetrahedral configurations; `t3-,4-,5+,6-,7+,8+,9-,10+,13+,14- / m1 / s1` |
| Benzoic acid, 4-[2-[4-(5-methyl-2-benzoxazolyl)phenyl]ethenyl]-, methyl ester | No (0) | No (0) | Yes | Alkene E/Z geometry; not a tetrahedral-centre case; `b5-4+` |
| Dodecahedrane | No (0) | No (0) | Yes | Polycyclic cage stereo encoding; `t1-,2+,3-,4+,5+,6-,7-,8+,9+,10-,11-,12+,13+,14-,15-,16+,17+,18-,19+,20-` |
| Dicyclopentadiene diepoxide | No (0) | No (0) | Yes | Fused-ring/epoxide tetrahedral configurations; `t3-,4+,5-,6+,7-,8+,9-,10+ / m1 / s1` |

The [nine-case CSV](nine-cases.csv) contains each exact pinned SMILES, full input key, full computed key, full input/computed InChI, layer comparison and geometry digest. Detailed RDKit potential-stereo observations are in `state/identity-policy/analysis.json`; potential-site counts are perception diagnostics, not counts of independent physical stereoisomers.

An XYZ file contains a concrete 3D arrangement from which stereo can be perceived, although it has no SMILES-style stereo annotations or explicit “unspecified in the source” status. Unspecified input stereo does not constrain that arrangement. Embedding and coordinate perception can therefore add a definite stereo description; this audit does not attribute the addition specifically to DFT rather than initial 3D preparation. The strict comparison treats additional stereo information as an identity mismatch even though the source supplied no conflicting stereo constraint.

DEHP is among the nine. The orchestrator reports that it already belongs to the product’s existing contaminant set; this audit did not query or alter that product set. For DEHP, the pinned unspecified key is `BJQHLKABXJIVAM-UHFFFAOYSA-N`, while both geometry-perception engines yield `BJQHLKABXJIVAM-BGYRXZFFSA-N`. Its DFT completed normally. The rejection is a mismatch in identity representation, not evidence of failed computation or a changed connectivity skeleton.

**The other eight are similar in the relevant policy sense:** they also have no input stereo specification, matching connectivity and exclusively added stereo layers. They are not all the same stereochemical kind: the benzoxazolyl/ethenyl benzoate is an E/Z case, and dodecahedrane is a cage-stereo encoding case; the others involve tetrahedral or ring arrangements. This does not assert that all eight share DEHP’s product membership, use classification, or stereo sensitivity.

## Three options — consequences and conditional acceptance projections

The identity decision applies to **55 executed structures**, not the isotope entry that never reached DFT. Thus strict acceptance is **46/55**, and each broader option would accept **55/55** in this observed pilot. The original historical dispositions remain 46 accepted, nine identity-policy failures and one isotope preflight failure out of 56. No historical record has been reclassified.

| Option | Hypothetical accepted pilot results | Projected accepted identities out of 5,833, conditional on valid execution | Conditional 95% rate range, scaled to 5,833 | Meaning for a results-table reader |
| --- | ---: | ---: | ---: | --- |
| 1. Keep exact_full_inchikey | 46/55 executed; 46/56 selected | 4,879 | 4,184–5,316 | The accepted flag asserts equality of the complete perceived and source keys. Nine otherwise completed pilot surfaces remain rejected because computed stereo was absent from the source. |
| 2. Require first-block connectivity match only | 55/55 executed; 55/56 selected | 5,833 | 5,452–5,833 | The accepted flag asserts the same connectivity block only; it does not validate stereo. A future result could pass despite contradicting stereo explicitly specified in its input. A first-block-only rule is also not an isotope-identity guarantee; it must not silently bypass the separate isotope policy. |
| 3. Connectivity plus computed-stereo provenance | 55/55 executed; 55/56 selected | 5,833 | 5,452–5,833 | The row keeps the source key and the actual computed full key separately. It represents one computed stereochemical arrangement of a source with unspecified stereo, not an experimentally established isomer or a mixture/ensemble average. Define source-declared stereo as a constraint: unspecified stereo can be filled in, but explicitly specified stereo must not be contradicted. |

**Option 3’s source-stereo preservation is an explicit proposed rule, not an adopted policy.** Simply recording computed stereo without checking any source-declared constraints has the same acceptance predicate as option 2, with better disclosure. The owner should specify whether preservation of declared stereo is mandatory. In this pilot either reading has the same 55/55 hypothetical acceptance: all 46 currently accepted cases already match full keys and all nine rejected cases declare no stereo.

For option 3, a table should expose at least `source_inchikey`, `source_smiles`, whether atom/bond stereo was specified in the source, `computed_inchikey`, full computed InChI or stereochemical structure, perception method and disagreements, connectivity/source-constraint checks, identity-policy/version and authority, and the exact geometry/surface digests. Retain the frozen one-conformer provenance. Never silently overwrite the source key or imply that one selected configuration is an average over unspecified isomers. Options 1 and 2 must also retain provenance and failure reasons; their accepted flags have the narrower meanings above.

### What the uncertainty numbers do and do not mean

The table multiplies the observed identity-acceptance rate by 5,833 and scales a **95% Wilson binomial rate interval** by the same population. These are conditional illustrations of small-sample uncertainty, **not design-valid population confidence intervals or predictive guarantees**: the pilot was deliberately selected across size and chemistry rather than sampled randomly. Selection bias, chemistry composition, unobserved failure modes and future perception disagreements are not covered. A 55/55 pilot pass rate does not establish a true 100% campaign pass rate; its conditional lower bound is about 93.47%.

The orchestrator’s **9/56** rate gives about **937** affected structures (conditional scaled interval **507–1,622**). Isolating the identity question uses **9/55**, giving about **954** affected structures; the corresponding accepted projection is **4,879**, with the **4,184–5,316** range above. Neither calculation is a census of future mismatches.

The main table conditions on a valid executed calculation for every logical target. Isotope disposition is still a separate owner decision: the exact local census found **9/5,833** labelled CHNO entries, all with existing parents. Their treatment must not be inferred from the pilot’s one isotope preflight failure. If the owner excludes those nine identities, the same conditional rate calculation on 5,824 eligible identities gives **4,871** strict acceptances (about **4,178–5,308**) versus **5,824** under either broader option (about **5,444–5,824**); nine source identities would be explicitly excluded. If the owner maps them to parents, reporting and acceptance must retain that mapping and its distinct-parent denominator. No isotope option is selected here.

For transparent accounting only, naively scaling the original all-cause dispositions would give 46/56 → **4,791** strict acceptances (conditional **4,092–5,250**) and 55/56 → **5,729** broader-policy acceptances (**5,282–5,815**). These conflate identity and preflight outcomes and extrapolate a known nonrepresentative isotope rate, so they are not the identity-policy projection used in the main table.

## Cost goes to the owner with the identity decision

**The CPU-hour estimate is unchanged under all three identity policies:** **4,832 CPU-hours**, with a conditional Milan bootstrap sensitivity range **4,340–5,295 CPU-hours** for one attempt per structure under the existing workload model. All **55/55 executed OPT + COSMORS calculations completed normally**, including the nine identity-policy failures, and all 55 timings were already included in the fit. The pilot consumed **74.05 serial attempt-hours**. Changing acceptance of these completed surfaces adds no DFT work and removes none of the time already spent.

This remains the research / AMD EPYC 7763 (Milan) estimate, using the frozen serial ORCA recipe. An owner-authorised change to target count, conformer ensembles, retries or new calculations would require a separately updated budget; none is implied by these identity options. The isotope-specific target-count scenarios above likewise do not silently replace the existing campaign cost model.

The [completed pilot report](../pilot-v1/REPORT.md) contains the residuals, RAM and archive estimates, all failures and the C-1 hardware correction. The historical workstation anchors are Intel Core i7-13700 (Raptor Lake); Euler LOGIN alone is Broadwell. The campaign fit uses only Milan timings.

**Owner decision pending: choose the identity semantics alongside this cost estimate, resolve the separate isotope disposition, then decide whether to authorise Phase 2. No recommendation has been applied, no existing result status has changed, and no Phase 2 job has been submitted. Reported and stopped.**
