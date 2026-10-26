import importlib.util,sys,math,json,time
from pathlib import Path
from dataclasses import replace
import numpy as np
p=(Path(sys.argv[1]) if len(sys.argv)>1 else
   Path(__file__).resolve().parents[1]/'versions/suite-v1.6.2/BJT_통합도구/sources/sic_buildbox_thermal_gui_v5.py')
s=importlib.util.spec_from_file_location('_probe_oven_review',p);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
def inputs(**changes):
 d=dict(lx_mm=100.,ly_mm=100.,lz_mm=30.,box_k=16.2,box_density_kgm3=7930.,box_cp_jkgk=500.,side_thickness_mm=3.,bottom_thickness_mm=5.,top_thickness_mm=3.,top_open=True,bed_density_kgm3=1600.,true_density_kgm3=3200.,cp_jkgk=750.,k_eff_wmk=.6,k_solid_wmk=80.,gas_k_wmk=.026,k_mode='Manual',d10_um=40.6,d50_um=65.4,d90_um=105.,solid_k_temp_exponent=.75,contact_blend=.5,calibration_k_scale=1.,calibration_h_wm2k=12.,h_conv_wm2k=12.,emissivity=.75,bottom_gap_mm=40.,floor_emissivity=.8,bottom_view_factor=.95,o2_enabled=True,o2_ambient_percent=20.,o2_initial_percent=0.,o2_diff_air_25_m2s=2e-5,o2_diff_temp_exponent=1.75,o2_tortuosity_mode='Manual',o2_manual_tortuosity=2.,o2_use_knudsen=False,o2_top_access=True,o2_side_access=False,o2_bottom_access=False,nx=3,ny=3,nz=20,max_dt_s=1.,record_interval_s=.36,snapshot_interval_s=7.2,initial_temp_c=25.,oven_profile_min_c=[(0.,25.),(.12,25.)]);d.update(changes);return m.SimulationInputs(**d)
def slab(z,L,D,t):
 n=np.arange(200);mu=(n+.5)*np.pi
 return 1-np.sum(2*(-1.)**n/mu*np.cos(mu*z/L)*np.exp(-mu*mu*D*t/L**2))
rows=[]
for por in [.25,.5,.75]:
 inp=inputs(bed_density_kgm3=3200*(1-por));t0=time.perf_counter();r=m.simulate(inp)
 z=np.array([.03*(9.5/20),.03*(10.5/20)]);expected=float(np.mean([slab(zz,.03,1e-5,7.2) for zz in z]))*20
 rows.append(dict(porosity=por,center_percent=float(r.o2_center_percent[-1]),analytic_percent=expected,dt=r.dt_used_s,elapsed=time.perf_counter()-t0,heat_error=float(np.max(abs(r.avg_c-25)))))
print(json.dumps(rows,indent=2))
p=inputs(o2_enabled=False,lx_mm=.01,ly_mm=.01,lz_mm=.01,nx=3,ny=3,nz=3)
dx=p.lx_mm/1000/p.nx;alpha=p.k_eff_wmk/(p.bed_density_kgm3*p.cp_jkgk)
print('dtfloor',m.estimate_stable_dt(p,200),'heat_stability_bound',.5/(alpha*3/dx**2))
for name,kw in [('nan_h',dict(h_conv_wm2k=float('nan'))),('negative_kelvin',dict(initial_temp_c=-300)),('nan_profile',dict(oven_profile_min_c=[(0,25),(.12,float('nan'))])),('closed_negative_lid',dict(top_open=False,top_thickness_mm=-1)),('noninteger_grid',dict(nx=3.5))]:
 try:m.validate_inputs(inputs(**kw));print(name,'ACCEPTED')
 except Exception as e:print(name,type(e).__name__,str(e))
original_d=m.oxygen_effective_diffusivity
m.oxygen_effective_diffusivity=lambda inp,t:original_d(inp,t)/(1-inp.bed_density_kgm3/inp.true_density_kgm3)
for nz in [10,20,40]:
 inp=inputs(nz=nz);r=m.simulate(inp);z=np.array([.03*((nz//2-.5)/nz),.03*((nz//2+.5)/nz)]);expected=np.mean([slab(zz,.03,1e-5,7.2) for zz in z])*20
 print('corrected temporary comparison',nz,'center',r.o2_center_percent[-1],'analytic',expected,'error',abs(r.o2_center_percent[-1]-expected))
