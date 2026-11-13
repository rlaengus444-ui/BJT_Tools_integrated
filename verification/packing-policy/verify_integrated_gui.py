"""Exercise the real integrated source capsule; never changes source models."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import runpy
import sys
import tempfile
import time


def wait(root, controller, limit=120):
    deadline=time.monotonic()+limit
    while controller.worker is not None or not controller.events.empty():
        root.update();time.sleep(.005)
        if time.monotonic()>deadline:raise TimeoutError('integrated worker did not finish')
    root.update()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--launcher',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    ns=runpy.run_path(str(args.launcher.resolve()),run_name='_integrated_capsule_review')
    source=ns['_extract_source']();sys.path.insert(0,str(source))
    import numpy as np
    import tkinter as tk
    from PIL import ImageGrab
    from suite_ui import SuiteApp
    from suite_loader import verify_sources,tool_specs
    from powdernest.rl import default_policy
    from powdernest.validation import validate
    from powdernest.types import Placement
    from packing_service import save_snapshot
    assert len(tool_specs())==12 and all(item['valid'] for item in verify_sources())
    policy_before=default_policy().to_dict();model_hash=hashlib.sha256(args.model.read_bytes()).hexdigest()
    try:
        from tkinterdnd2 import TkinterDnD
        root=TkinterDnD.Tk();root._suite_dnd_available=True
    except Exception:
        root=tk.Tk();root._suite_dnd_available=False
    root.geometry('1700x1030+0+0')
    with tempfile.TemporaryDirectory(prefix='bjt-gui-profile-') as temporary:
        app=SuiteApp(root,test_mode=True,settings_path=Path(temporary)/'inputs.json')
        for key in app.specs:
            app.mount_tool(key,app.loader.load(key));root.update()
        assert len(app.controllers)==12 and not app.failures
        controller=app.controllers['nesting']
        app.notebook.select(app.pages['nesting']);root.update()
        assert controller.add_paths([args.model]);wait(root,controller)
        assert len(controller.models)==1
        model=next(iter(controller.models.values()));model_bytes=(model.mesh.vertices.tobytes(),model.mesh.faces.tobytes())
        controller.rows[0]['quantity']=2;controller._refresh_table()
        controller.early_var.set(False);controller.seconds_var.set(.6);controller.balance_seconds_var.set(0)
        cases=[]
        for size in ((450,500,350),(300,300,300)):
            for name,value in zip(('size_x','size_y','size_z'),size):getattr(controller,name+'_var').set(value)
            controller.start();wait(root,controller)
            snapshot=controller.snapshot
            assert snapshot and snapshot.result.validation['complete']
            fresh=validate(snapshot.models,snapshot.items,snapshot.settings,snapshot.result.placements,force_fresh=True)
            assert fresh['valid'] and fresh['complete']
            directory=args.output/('x'.join(map(str,size)));directory.mkdir(exist_ok=True)
            save_snapshot(snapshot,directory/'export')
            save_snapshot(snapshot,directory/'layout.chitubox','chitubox')
            encoded=json.loads((directory/'layout.chitubox.json').read_text())
            assert encoded['encoded_geometry_validation']['valid']
            stats=snapshot.result.metadata['reinforcement_learning']
            assert stats['td_updates']==0 and default_policy().to_dict()==policy_before
            cases.append({'size_mm':size,'requested':2,'placed':len(snapshot.result.placements),
                'height_mm':snapshot.result.height,'elapsed_seconds':snapshot.result.elapsed,
                'policy':stats,'fresh_validation':fresh,'encoded_validation':encoded['encoded_geometry_validation']})
        # Save the normal full-suite screenshot before the display-only stress.
        controller.preview.align('origin');root.update()
        ImageGrab.grab(bbox=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())).save(args.output/'integrated-packing.png')
        controller.control_notebook.select(1);root.update()
        ImageGrab.grab(bbox=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())).save(args.output/'integrated-packing-settings.png')
        controller.control_notebook.select(0);root.update()
        count=100
        poses=[Placement(model.model_id,i,0,(1+(i%10)*172,1+(i//10)*172,model.tessellation_error)) for i in range(count)]
        start=time.perf_counter();notice=controller.preview.set_scene({model.model_id:model},poses,(1725,1725,350));root.update()
        first_draw=time.perf_counter()-start;timings=[]
        for view in ('x+','y+','z+','origin'):
            start=time.perf_counter();controller.preview.align(view);root.update();timings.append(time.perf_counter()-start)
        assert model_bytes==(model.mesh.vertices.tobytes(),model.mesh.faces.tobytes())
        preview={'copies':count,'source_triangles':len(model.mesh.faces)*count,'notice':notice,
            'initial_draw_seconds':first_draw,'camera_draw_seconds':timings,
            'p95_seconds':float(np.percentile(timings,95)),'source_mesh_bytes_unchanged':True}
        manual=app.open_manual('nesting');root.update()
        for window in list(app.manual_windows.values()):window.destroy()
        app.request_close(confirm=False)
        while True:
            try:
                if not root.winfo_exists():break
                root.update()
            except tk.TclError:break
    assert hashlib.sha256(args.model.read_bytes()).hexdigest()==model_hash
    result={'passed':True,'frozen':False,'platform':platform.platform(),'python':sys.version,
        'launcher':str(args.launcher),'payload_sha256':ns['PAYLOAD_SHA256'],'tool_count':12,
        'backend_loaded_from_capsule':str(sys.modules['powdernest'].__file__).startswith(str(source)),
        'model_name':args.model.name,'source_sha256':model_hash,'policy_digest':default_policy().digest,
        'new_training_updates':0,'cases':cases,'preview_benchmark':preview}
    (args.output/'integration-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':True,'tools':12,'cases':[{k:case[k] for k in ('size_mm','placed','height_mm','elapsed_seconds')} for case in cases],'preview':preview},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
