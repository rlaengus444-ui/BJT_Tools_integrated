"""Native frozen packing gate: STEP DLLs, suite worker, preview and exports."""
from __future__ import annotations
import json
from pathlib import Path
import time


def _wait(root, controller, seconds=60):
    deadline=time.monotonic()+seconds
    while controller.worker is not None or not controller.events.empty():
        root.update();time.sleep(.01)
        if time.monotonic()>deadline:raise TimeoutError('Packing verification did not finish')
    root.update()


def verify(root, controller, directory):
    import trimesh
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Writer,STEPControl_AsIs
    from powdernest.geometry import load_model
    from powdernest.rl import default_policy
    from packing_service import save_snapshot
    directory=Path(directory);directory.mkdir(exist_ok=True)
    before=default_policy().to_dict()
    step=directory/'packing-step.step';writer=STEPControl_Writer()
    assert writer.Transfer(BRepPrimAPI_MakeBox(8.,8.,8.).Shape(),STEPControl_AsIs)==IFSelect_RetDone
    assert writer.Write(str(step))==IFSelect_RetDone
    cad=load_model(step,model_id='native-step')
    assert cad.original_shape is not None and cad.mesh.is_watertight
    stl=directory/'packing-cube.stl';trimesh.creation.box((8,8,8)).export(stl)
    assert controller.add_paths([stl]);_wait(root,controller)
    assert len(controller.models)==1
    controller.rows[0]['quantity']=2;controller._changed();controller._refresh_table()
    for name,value in (('size_x',10),('size_y',10),('size_z',17),('gap',1),('wall',1),('seconds',2),('balance_seconds',0)):
        getattr(controller,name+'_var').set(value)
    controller.start();_wait(root,controller)
    snapshot=controller.snapshot
    assert snapshot is not None and snapshot.result.validation['complete']
    assert snapshot.result.height==17 and len(snapshot.result.placements)==2
    assert sorted(p.position[2] for p in snapshot.result.placements)==[0.,9.]
    for view in ('origin','fit','x+','x-','y+','y-','z+','z-'):controller.preview.align(view)
    controller.preview.save_image(directory/'packing-preview.png')
    save_snapshot(snapshot,directory/'packing-export')
    save_snapshot(snapshot,directory/'packing.chitubox','chitubox')
    encoded=json.loads((directory/'packing.chitubox.json').read_text(encoding='utf-8'))
    assert encoded['encoded_geometry_validation']['valid']
    assert default_policy().to_dict()==before
    assert snapshot.result.metadata['reinforcement_learning']['td_updates']==0
    return {'passed':True,'step_native_import':True,'placed':2,'height_mm':17,
            'policy_digest':default_policy().digest,'new_training_updates':0,'preview_and_exports':True}
