"""Implementation agreement and retained water-only baseline; not chemical accuracy."""
import ast
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'state/progress-2026-09-14'
reference = Path('/home/aaltamimi2/dissolve-v12-builder-1/src/dissolve/cosmo_logp.py')
source = reference.read_bytes()
tree = ast.parse(source)
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'delta_log_d')
namespace = {'math': math}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(reference), 'exec'), namespace)
baseline = json.loads((P/'water-only-baseline.json').read_text())
ledger = json.loads((ROOT/'state/thermodynamics-v1/processing-ledger.json').read_text())
rows = []
preservation = []
skipped = []
for key, entry in sorted(ledger.items()):
    payload = Path(entry['result_path']).read_bytes()
    if hashlib.sha256(payload).hexdigest() != entry['result_sha256']:
        skipped.append(key)
        continue
    result = json.loads(payload)
    if key in baseline['rows'] and entry['activity_count'] > 1:
        old = baseline['rows'][key]
        preservation.append({'inchikey':key, 'exact_water_record_equal': old['water']==result['activities']['water'],
                             'solute_hash_equal':old['solute_surface_sha256']==result['solute_surface_sha256']})
    for pair in result['partitions_against_water']:
        if pair['status'] != 'predicted':
            continue
        a = result['activities'][pair['solvent']]['ln_gamma']
        b = result['activities']['water']['ln_gamma']
        expected_x = namespace['delta_log_d'](a, b)
        dx = pair['log10_K_mole_fraction']-expected_x
        dc = None
        if pair.get('log10_K_concentration') is not None:
            expected_c = namespace['delta_log_d'](a, b,
                volume_a=pair['molar_volume_solvent_cm3_mol'],
                volume_b=pair['molar_volume_reference_cm3_mol'])
            dc = pair['log10_K_concentration']-expected_c
        rows.append({'inchikey':key, 'solvent':pair['solvent'],
                     'mole_fraction_difference':dx, 'concentration_difference':dc,
                     'passed':abs(dx)<1e-12 and (dc is None or abs(dc)<1e-12),
                     'result_sha256':entry['result_sha256']})
out = {'utc':dt.datetime.now(dt.timezone.utc).isoformat(),
       'reference_file':str(reference), 'reference_file_sha256':hashlib.sha256(source).hexdigest(),
       'reference_function':'delta_log_d; unmodified function AST extracted from read-only source',
       'pair_count':len(rows), 'pair_passed':sum(r['passed'] for r in rows),
       'water_baseline_count':len(baseline['rows']),
       'water_updated_count':len(preservation),
       'water_preserved_count':sum(r['exact_water_record_equal'] and r['solute_hash_equal'] for r in preservation),
       'skipped_concurrently_changing_records':skipped, 'pairs':rows, 'water_preservation':preservation,
       'interpretation':'Implementation agreement using identical activity inputs, plus exact cached water-result preservation. This does not independently validate experimental accuracy or rerun the water solver.'}
(P/'reference-math-and-water-audit.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k not in ['pairs','water_preservation']}))
assert out['pair_count']==out['pair_passed']
assert out['water_updated_count']==out['water_preserved_count']
