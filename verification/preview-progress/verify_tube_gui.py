"""Verify a supplied STEP in the actual Tk suite without bundling private geometry."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys
import tempfile
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--launcher',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    source=Path(__file__).resolve().parents[2]/'suite'
    if args.launcher:
        namespace=runpy.run_path(str(args.launcher.resolve()),run_name='_preview_capsule_check')
        source=namespace['_extract_source']()
    sys.path.insert(0,str(source))
    import numpy as np
    import tkinter as tk
    import trimesh
    from PIL import ImageGrab
    from packing_service import save_snapshot
    from powdernest.rl import default_policy
    from powdernest.types import Placement
    from powdernest.validation import validate
    from suite_loader import verify_sources
    from suite_ui import SuiteApp
    from suite_theme import FONTS
    # Windows product fonts are unavailable on this Linux verification desktop.
    for name,value in FONTS.items():FONTS[name]=('Noto Sans CJK KR',*value[1:])

    assert all(check['valid'] for check in verify_sources())
    before=hashlib.sha256(args.model.read_bytes()).hexdigest()
    policy=default_policy().to_dict();samples=[]
    root=tk.Tk();root.geometry('1350x970+0+0')
    def capture(name):
        c.preview.render();root.lift();root.update()
        ImageGrab.grab(bbox=(root.winfo_rootx(),root.winfo_rooty(),
            root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())).save(args.output/name)
    def wait(c):
        deadline=time.monotonic()+120
        while c.is_busy or not c.events.empty():
            root.update();time.sleep(.01)
            if c.is_busy:
                samples.append({'status':c.status_var.get(),'progress':c.progress_var.get()})
            if time.monotonic()>deadline:raise TimeoutError('packing worker did not finish')
        root.update()
    with tempfile.TemporaryDirectory(prefix='bjt-preview-profile-') as temporary:
        suite=SuiteApp(root,test_mode=True,settings_path=Path(temporary)/'settings.json')
        root.geometry('1350x970+0+0')
        c=suite.mount_tool('nesting',suite.loader.load('nesting'));suite.select_tool('nesting');root.update()
        assert c.add_paths([args.model]);wait(c)
        model=next(iter(c.models.values()));mesh_before=(model.mesh.vertices.tobytes(),model.mesh.faces.tobytes())
        c.rows[0]['quantity']=2;c._refresh_table()
        c.seconds_var.set(3);c.balance_seconds_var.set(.2);c.early_var.set(False)
        c.start();wait(c)
        snapshot=c.snapshot
        assert snapshot and snapshot.result.validation['complete']
        fresh=validate(snapshot.models,snapshot.items,snapshot.settings,snapshot.result.placements,force_fresh=True)
        assert fresh['valid'] and fresh['complete'] and len(snapshot.result.placements)==2
        save_snapshot(snapshot,args.output/'export')
        save_snapshot(snapshot,args.output/'layout.chitubox','chitubox')
        encoded=json.loads((args.output/'layout.chitubox.json').read_text())
        assert encoded['encoded_geometry_validation']['valid']
        c.preview.align('fit');capture('integrated-tube.png')
        c.preview.save_image(args.output/'tube-preview.png')
        assert len(c.preview.model_actors)==2
        assert (c.preview.axes.GetXAxisLabelText(),c.preview.axes.GetYAxisLabelText(),c.preview.axes.GetZAxisLabelText())==('X','Y','Z')
        for name in ('size_x','size_y','size_z'):getattr(c,name+'_var').set(300)
        c.start();wait(c)
        small=c.snapshot.result
        assert len(small.placements)==0 and small.validation['valid'] and small.status=='not_found'
        assert small.metadata['termination']=='necessary_bounds'
        # Display stress uses synthetic poses; this is not a packing result.
        poses=[Placement(model.model_id,i,0,((i%10)*350.,(i//10)*350.,0.)) for i in range(100)]
        started=time.perf_counter();c.preview.set_scene({model.model_id:model},poses,(3500,3500,350))
        c.preview.render();root.update();first=time.perf_counter()-started
        assert len(c.preview.model_actors)==100 and len(c.preview.model_mappers)==1
        assert c.preview.model_mappers[model.model_id].GetInput().GetNumberOfPolys()==len(model.mesh.faces)
        started=time.perf_counter();c.preview.align('z+');c.preview.render();root.update();camera=time.perf_counter()-started
        original=displayed=len(model.mesh.faces)*100
        assert mesh_before==(model.mesh.vertices.tobytes(),model.mesh.faces.tobytes())
        assert default_policy().to_dict()==policy and snapshot.result.metadata['reinforcement_learning']['td_updates']==0
        suite.request_close(confirm=False)
        for unused in range(100):
            try:root.update()
            except tk.TclError:break
    assert hashlib.sha256(args.model.read_bytes()).hexdigest()==before
    report={'passed':True,'source_sha256':before,'source_faces':len(model.mesh.faces),
        'source_extent_mm':model.mesh.extents.tolist(),'large_box':{'requested':2,'placed':2,'fresh_valid':True,
        'height_mm':snapshot.result.height,'elapsed_seconds':snapshot.result.elapsed},
        'small_box':{'placed':0,'status':small.status},'stl_and_chitubox_export':True,
        'source_and_policy_unchanged':True,'grid_and_ticks_hidden':True,
        'display_stress':{'copies':100,'original_faces':original,'displayed_faces':displayed,
            'all_copies_full_source':True,'first_draw_seconds':first,'camera_draw_seconds':camera},
        'observed_progress':list({json.dumps(row,ensure_ascii=False):row for row in samples}.values()),
        'capsule':bool(args.launcher),'loaded_source':str(source)}
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({key:value for key,value in report.items() if key!='observed_progress'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
