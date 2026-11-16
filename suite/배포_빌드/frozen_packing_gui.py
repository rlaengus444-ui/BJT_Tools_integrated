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
    import numpy as np
    import trimesh
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Writer,STEPControl_AsIs
    from powdernest.geometry import load_model, model_from_mesh
    from powdernest.rl import default_policy
    from powdernest.rotations import ROTATIONS
    from powdernest.types import Placement, matrix_for
    from packing_service import save_snapshot
    directory=Path(directory);directory.mkdir(exist_ok=True)
    assert controller.preview.widget.winfo_ismapped(), 'Packing preview must be visible'
    assert controller.preview.widget.winfo_width()>=320 and controller.preview.widget.winfo_height()>=240
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
    # Check actual pixels from the frozen renderer, including the hollow bore.
    tube=model_from_mesh(trimesh.creation.annulus(r_min=10,r_max=12,height=20,sections=128),'frozen-hollow')
    rotation=next(i for i,value in enumerate(ROTATIONS) if np.array_equal(value,np.eye(3)))
    pose=Placement(tube.model_id,0,rotation,(10.,10.,0.))
    controller.preview.set_scene({tube.model_id:tube},[pose],(50,50,30))
    controller.preview.align('z+');controller.preview.save_image(directory/'packing-hollow-preview.png')
    image=controller.preview.image
    assert image.width>=320 and image.height>=240, 'Packing capture is too small'
    def pixel(local):
        point=trimesh.transform_points([local],matrix_for(tube,pose))[0]
        renderer=controller.preview.renderer
        renderer.SetWorldPoint(*point,1.);renderer.WorldToDisplay()
        x,y,unused=renderer.GetDisplayPoint()
        return list(image.getpixel((int(x),image.height-1-int(y))))
    bore,wall=pixel((0.,0.,10.)),pixel((11.,0.,10.))
    assert min(bore)>240 and max(wall)<245, 'Frozen depth-buffer bore/wall pixels failed'
    pixels={'size':[image.width,image.height],'bore_rgb':bore,'wall_rgb':wall}
    assert default_policy().to_dict()==before
    assert snapshot.result.metadata['reinforcement_learning']['td_updates']==0
    return {'passed':True,'step_native_import':True,'placed':2,'height_mm':17,
            'policy_digest':default_policy().digest,'new_training_updates':0,'preview_and_exports':True,
            'depth_buffer_preview':pixels}
