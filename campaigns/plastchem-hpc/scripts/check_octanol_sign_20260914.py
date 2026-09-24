"""Independent arithmetic and reference-function check; no numerical recalibration."""
import ast,csv,datetime,hashlib,json,math
from decimal import Decimal,localcontext
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
B=Path('/mnt/r/plastchem-euler/progress-2026-09-14')
D=B/'octanol-validation';O=B/'octanol-sign-review';O.mkdir(exist_ok=True)
reference=Path('/home/aaltamimi2/dissolve-v12-builder-1/src/dissolve/cosmo_logp.py')
raw=reference.read_bytes();sha=lambda b:hashlib.sha256(b).hexdigest()
assert sha(raw)==sha((ROOT/'state/opencosmo-verification-v1/reference_cosmo_logp.py').read_bytes())
node=next(n for n in ast.parse(raw).body if isinstance(n,ast.FunctionDef) and n.name=='delta_log_d')
namespace={'math':math};exec(compile(ast.Module(body=[node],type_ignores=[]),str(reference),'exec'),namespace)
delta=namespace['delta_log_d']
anchors={'FLKPEMZONWLCSK-UHFFFAOYSA-N':'DEP','DOIRQSBPFJWKBE-UHFFFAOYSA-N':'DBP','IRIAEXORFWYRCZ-UHFFFAOYSA-N':'BBP','BJQHLKABXJIVAM-UHFFFAOYSA-N':'DEHP'}
parity_path=D/'validation/best-measured-parity.csv'
parity=list(csv.DictReader(parity_path.open()))
for r in parity:r['anchor_label']=anchors.get(r['input_inchikey'],'')
parity.sort(key=lambda r:(not bool(r['anchor_label']),list(anchors).index(r['input_inchikey']) if r['anchor_label'] else r['name']))
def write_csv(name,rows):
 with (O/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
write_csv('best-measured-parity.csv',parity)
anchor_rows=[r for r in parity if r['anchor_label']];assert len(anchor_rows)==4
write_csv('four-anchor-parity.csv',anchor_rows)
checks=[];dep=None
cohort=json.loads((B/'rehearsal/freeze/manifest.json').read_text())['cohort']
for member in cohort:
 p=D/(member['inchikey']+'.json');raw_result=p.read_bytes();r=json.loads(raw_result)
 a=r['octanol_activity']['ln_gamma'];b=r['water_activity']['ln_gamma'];pred=r['prediction']
 va=pred['molar_volume_solvent_cm3_mol'];vb=pred['molar_volume_reference_cm3_mol']
 with localcontext() as ctx:
  ctx.prec=40
  logx=(Decimal(str(b))-Decimal(str(a)))/Decimal(10).ln()
  correction=(Decimal(str(vb))/Decimal(str(va))).log10()
  manual=float(logx+correction)
 replay=delta(a,b,volume_a=va,volume_b=vb)
 error=abs(manual-pred['log10_K_concentration'])
 assert correction<0 and error<1e-12 and abs(replay-pred['log10_K_concentration'])<1e-12
 assert abs(float(correction)-pred['volume_correction_log10'])<1e-12
 checks.append({'inchikey':member['inchikey'],'result_sha256':sha(raw_result),'stored':pred['log10_K_concentration'],'decimal_manual':manual,'reference_replay':replay,'absolute_error':error})
 if anchors.get(member['inchikey'])=='DEP':
  dep={'ln_gamma_water':b,'ln_gamma_octanol':a,'gamma_water':math.exp(b),'gamma_octanol':math.exp(a),'molar_volume_water_cm3_mol':vb,'molar_volume_octanol_cm3_mol':va,'manual_logKx':float(logx),'manual_volume_correction':float(correction),'manual_logKconc':manual,'stored_logKconc':pred['log10_K_concentration'],'reference_logKconc':replay,'result_sha256':sha(raw_result)}
write_csv('conversion-checks.csv',checks)
x=[float(r['measured_logKow']) for r in parity];y=[float(r['predicted_logKow']) for r in parity];n=len(x)
mx=sum(x)/n;my=sum(y)/n;sxx=sum((v-mx)**2 for v in x)
slope=sum((a-mx)*(b-my) for a,b in zip(x,y))/sxx;intercept=my-slope*mx
res=[b-a for a,b in zip(x,y)];bias=sum(res)/n;mse=sum(v*v for v in res)/n
centered_rms=math.sqrt(sum((v-bias)**2 for v in res)/n)
fit_sse=sum((b-(intercept+slope*a))**2 for a,b in zip(x,y))
slope_se=math.sqrt((fit_sse/(n-2))/sxx)
s={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'no_sign_or_volume_unit_error_found','checked':len(checks),'denominator':590,'reference_path':str(reference),'reference_sha256':sha(raw),'script_sha256':sha(Path(__file__).read_bytes()),'parity_input_sha256':sha(parity_path.read_bytes()),'DEP':dep,'regression':{'n':n,'predicted_on_measured_slope':slope,'predicted_on_measured_intercept':intercept,'residual_on_measured_slope':slope-1,'residual_on_measured_intercept':intercept,'slope_standard_error':slope_se,'R_squared':1-fit_sse/sum((b-my)**2 for b in y),'MAE':sum(abs(v) for v in res)/n,'RMSE':math.sqrt(mse),'bias':bias,'centered_residual_RMS':centered_rms,'bias_squared_fraction_of_MSE':bias*bias/mse},'maximum_decimal_discrepancy':max(r['absolute_error'] for r in checks),'empirical_correction_applied':False,'numerical_code_changed':False,'solver_rerun_required':False,'interpretation':'Regression is descriptive only. The positive bias persists despite the correct negative molar-volume correction; centered residual RMS remains nonzero. No fitted correction applied.'}
(O/'sign-and-regression.json').write_text(json.dumps(s,indent=2)+'\n')
(ROOT/'state/progress-2026-09-14/octanol-sign-review.json').write_text(json.dumps(s,indent=2)+'\n')
lines=['# Octanol sign and offset review','','No sign or molar-volume unit error was found. No numerical code change, solver rerun or empirical correction was applied.','',f"For DEP, ln(gamma_water) = {dep['ln_gamma_water']:.12f}, ln(gamma_octanol) = {dep['ln_gamma_octanol']:.12f}.",f"logKx = (ln(gamma_water) − ln(gamma_octanol))/ln(10) = {dep['manual_logKx']:.12f}.",f"log10(V_water/V_octanol) = log10(18.07/158.5283018867925) = {dep['manual_volume_correction']:.12f}.",f"logKconc = {dep['manual_logKconc']:.12f}; stored and reference-function results agree to 1e-12.",'','Derivation: at equilibrium x_octanol/x_water = gamma_water/gamma_octanol; c = x/V_m, so c_octanol/c_water = (x_octanol/x_water)(V_water/V_octanol). Both molar volumes are in cm³/mol, so their ratio is dimensionless. The stored activities are natural logarithms, divided by ln(10) once.','',f"All {len(checks)}/590 stored predictions agree with independent 40-digit decimal arithmetic and the unmodified reference delta_log_d function (A = octanol, B = water).",f"Reference SHA-256: `{sha(raw)}`.",'',f"Predicted = {slope:.6f} × measured + {intercept:.6f}; residual = {slope-1:.6f} × measured + {intercept:.6f} (n = {n}).",f"MAE {s['regression']['MAE']:.6f}; RMSE {math.sqrt(mse):.6f}; bias {bias:+.6f}; centered residual RMS {centered_rms:.6f}. Bias squared accounts for {100*bias*bias/mse:.1f}% of MSE. This is not a scatter-free constant offset; no regression correction was applied.",'','| Anchor | Name | Measured logKow | Predicted logKow | Residual | Citation |','|---|---|---:|---:|---:|---|']
for r in anchor_rows:
 citation=r['raw_reference_string'].split(' | ')[0].replace('|','/')
 lines.append(f"| {r['anchor_label']} | {r['name']} | {float(r['measured_logKow']):.3f} | {float(r['predicted_logKow']):.3f} | {float(r['residual_predicted_minus_measured']):+.3f} | [{citation}]({r['source_url']}) |")
lines+=['','The full parity CSV now names and flags all four anchors; source URLs, raw citations and retrieval times remain in every row. The original published comparison and its hashes are preserved in the octanol-validation directory.','',f'Reproduce: `python3 scripts/{Path(__file__).name}`. Artifacts: `{O}`.']
(O/'REPORT.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(s))
