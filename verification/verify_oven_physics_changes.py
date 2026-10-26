"""Compare unchanged thermal/default behavior and intentional O2 changes."""
from __future__ import annotations
import dataclasses,hashlib,importlib.util,json,sys,time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
SUITE=ROOT/'suite'
sys.path.insert(0,str(SUITE))
def load(path,name):
 spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod
oldpath=ROOT/'versions/suite-v1.6.2/BJT_통합도구/sources/sic_buildbox_thermal_gui_v5.py'
newpath=SUITE/'sources/sic_buildbox_thermal_gui_v5.py'
old=load(oldpath,'_physics_parity_old');new=load(newpath,'_physics_parity_new')
fixtures=load(SUITE/'배포_빌드/helpers/thermal_fixtures.py','_physics_legacy_fixtures')
checker=fixtures.Checker();started=time.perf_counter();cases=[]
scenarios=[('psd_uniform_o2',{}),('manual_heat_only_closed_lid',dict(k_mode='Manual',o2_enabled=False,top_open=False)),('calibrated_gradient_o2_side_access',dict(k_mode='Calibrated',o2_initial_percent=1.,o2_side_access=True,calibration_k_scale=1.8,calibration_h_wm2k=20.)),('manual_tortuosity_no_knudsen',dict(o2_initial_percent=0.,o2_tortuosity_mode='Manual',o2_manual_tortuosity=2.,o2_use_knudsen=False)),('sealed_o2_and_cooling',dict(o2_initial_percent=5.,o2_top_access=False,o2_side_access=False,initial_temp_c=200.,oven_profile_min_c=[(0.,200.),(1.,30.),(3.,30.)])),('no_radiation_no_snapshots',dict(emissivity=0.,floor_emissivity=0.,bottom_view_factor=0.))]
changed_o2={'o2_center_percent','o2_top_percent','o2_avg_percent','o2_min_percent','o2_max_percent','o2_center_dose_air_h','o2_snapshots_percent'}
for name,overrides in scenarios:
 results=[mod.simulate(fixtures.inputs(mod,**overrides),store_snapshots=name!='no_radiation_no_snapshots') for mod in (old,new)]
 gradient=name in {'calibrated_gradient_o2_side_access','manual_tortuosity_no_knudsen'}
 checked=[]
 for field in dataclasses.fields(results[0]):
  if gradient and field.name in changed_o2:continue
  checker.equal(getattr(results[0],field.name),getattr(results[1],field.name),f'{name}.{field.name}');checked.append(field.name)
 if gradient:
  assert results[1].o2_center_percent[-1]>results[0].o2_center_percent[-1]
  assert np.all(results[1].o2_min_percent>=overrides['o2_initial_percent']-1e-12)
  assert np.all(results[1].o2_max_percent<=20.95+1e-12)
 cases.append({'name':name,'unchanged_fields':checked,'gradient_o2_intentionally_changed':gradient,'old_final_center_o2':float(results[0].o2_center_percent[-1]) if np.isfinite(results[0].o2_center_percent[-1]) else None,'new_final_center_o2':float(results[1].o2_center_percent[-1]) if np.isfinite(results[1].o2_center_percent[-1]) else None,'old_dt':results[0].dt_used_s,'new_dt':results[1].dt_used_s})
# Synthetic data verify exact calibration API/result parity; not real oven data.
truth=old.simulate(fixtures.inputs(old,k_mode='Calibrated',o2_enabled=False),store_snapshots=False)
fits=[];events=[[],[]]
for idx,mod in enumerate((old,new)):
 fits.append(mod.fit_thermal_calibration(fixtures.inputs(mod),truth.time_min,truth.center_c,status_cb=lambda *ev,i=idx:events[i].append(ev)))
checker.equal(fits[0],fits[1],'synthetic_calibration');checker.equal(events[0],events[1],'synthetic_calibration_progress')
report={'passed':True,'source_sha256':hashlib.sha256(newpath.read_bytes()).hexdigest(),'baseline_sha256':hashlib.sha256(oldpath.read_bytes()).hexdigest(),'assertions':checker.count,'cases':cases,'synthetic_calibration':fits[1],'elapsed_seconds':time.perf_counter()-started,'limits':'Numerical/model cross-check only; not real oven or thermocouple validation.'}
(ROOT/'verification/oven-physics-parity.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
print(json.dumps({'passed':True,'assertions':checker.count,'cases':len(cases),'calibration':fits[1],'elapsed_seconds':report['elapsed_seconds']},ensure_ascii=False))
