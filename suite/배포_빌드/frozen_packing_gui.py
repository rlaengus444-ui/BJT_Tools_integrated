"""Native frozen packing gate: STEP DLLs, suite worker, preview and exports."""
from __future__ import annotations
import json
from pathlib import Path
import time
import os


def _wait(root, controller, seconds=60):
    deadline=time.monotonic()+seconds
    while controller.worker is not None or not controller.events.empty():
        root.update();time.sleep(.01)
        if time.monotonic()>deadline:raise TimeoutError('Packing verification did not finish')
    root.update()


def _require_visible_button(button, host):
    assert button.winfo_viewable() and str(button.cget('state')) == 'normal'
    x = button.winfo_rootx() - host.winfo_rootx()
    y = button.winfo_rooty() - host.winfo_rooty()
    assert 0 <= x and x + button.winfo_width() <= host.winfo_width()
    assert 0 <= y and y + button.winfo_height() <= host.winfo_height()
    assert button.winfo_width() >= button.winfo_reqwidth()
    assert button.winfo_height() >= button.winfo_reqheight()


def _verify_export_buttons(root, controller, directory, snapshot):
    from tkinter import filedialog

    import numpy as np
    from chitu_overlap import read_chitubox

    standard = directory / 'packing-export'
    project = directory / '배치 결과.chitubox'
    original_directory = filedialog.askdirectory
    original_filename = filedialog.asksaveasfilename
    try:
        for button in (controller.export_button, controller.chitubox_button):
            _require_visible_button(button, controller.root)

        filedialog.askdirectory = lambda **options: str(standard)
        filedialog.asksaveasfilename = lambda **options: str(project)
        controller.export_button.invoke()
        _wait(root, controller)
        assert (standard / 'layout.stl').is_file() and (standard / 'placements.json').is_file()
        controller.chitubox_button.invoke()
        _wait(root, controller)
        assert controller.snapshot is snapshot
        encoded = json.loads(project.with_suffix('.chitubox.json').read_text(encoding='utf-8'))
        assert encoded['encoded_geometry_validation']['valid']
        objects = read_chitubox(project)
        assert len(objects) == 2 and len({obj.name for obj in objects}) == 2
        bounds = sorted((obj.mesh.bounds for obj in objects), key=lambda bounds: bounds[0, 2])
        np.testing.assert_allclose(bounds, [((-4, -4, 0), (4, 4, 8)),
                                            ((-4, -4, 9), (4, 4, 17))], atol=0.01, rtol=0)
        return {'passed': True, 'window_size': [root.winfo_width(), root.winfo_height()],
                'host_size': [controller.root.winfo_width(), controller.root.winfo_height()],
                'calculate_button_invoked': True, 'save_buttons_invoked': 2, 'object_count': len(objects),
                'plate_bounds_mm': [value.tolist() for value in bounds], 'unicode_output': True}
    finally:
        filedialog.askdirectory = original_directory
        filedialog.asksaveasfilename = original_filename


def verify(root, controller, directory):
    import numpy as np
    import trimesh
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    from OCP.gp import gp_Pnt
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Writer,STEPControl_AsIs
    from powdernest.geometry import load_model, model_from_mesh
    from powdernest.rl import default_policy
    from powdernest.rotations import ROTATIONS
    from powdernest.types import Placement, matrix_for
    directory=Path(directory);directory.mkdir(exist_ok=True)
    assert controller.preview.widget.winfo_ismapped(), 'Packing preview must be visible'
    assert controller.preview.widget.winfo_width()>=320 and controller.preview.widget.winfo_height()>=240
    before=default_policy().to_dict()
    step=directory/'packing-step.step';writer=STEPControl_Writer()
    assert writer.Transfer(BRepPrimAPI_MakeBox(8.,8.,8.).Shape(),STEPControl_AsIs)==IFSelect_RetDone
    assert writer.Write(str(step))==IFSelect_RetDone
    cad=load_model(step,model_id='native-step')
    assert cad.original_shape is not None and cad.mesh.is_watertight
    bodies=TopoDS_Compound();builder=BRep_Builder();builder.MakeCompound(bodies)
    builder.Add(bodies,BRepPrimAPI_MakeBox(8.,8.,8.).Shape())
    for x in range(1,6):
        builder.Add(bodies,BRepPrimAPI_MakeBox(gp_Pnt(float(x),1.,1.),2.,2.,2.).Shape())
    multi=directory/'overlapping-bodies.step';writer=STEPControl_Writer()
    assert writer.Transfer(bodies,STEPControl_AsIs)==IFSelect_RetDone
    assert writer.Write(str(multi))==IFSelect_RetDone
    multi=multi.rename(directory/'보수시편 M8 (1).STEP.step')
    # Exercise the same asynchronous GUI import path as a user's CAD file.
    assert controller.add_paths([multi]);_wait(root,controller)
    assert len(controller.models)==1, 'Frozen multi-body STEP GUI import failed'
    imported=next(iter(controller.models.values()))
    assert imported.diagnostics['source_solid_count']==6
    assert imported.diagnostics['material_solid_count']==1
    assert imported.diagnostics['step_material_union_applied']
    assert abs(imported.mesh.volume-512.)<1e-6 and imported.mesh.is_watertight
    union_proof={'passed':True,'source_solids':6,'material_solids':1,
                 'volume_mm3':float(imported.mesh.volume),'unicode_path':True,'gui_import':True}
    controller.rows.clear();controller.models.clear();controller._changed();controller._refresh_table()
    stl=directory/'packing-cube.stl';trimesh.creation.box((8,8,8)).export(stl)
    assert controller.add_paths([stl]);_wait(root,controller)
    assert len(controller.models)==1
    controller.rows[0]['quantity']=2;controller._changed();controller._refresh_table()
    for name,value in (('size_x',10),('size_y',10),('size_z',17),('gap',1),('wall',1),('seconds',2),('balance_seconds',0)):
        getattr(controller,name+'_var').set(value)
    # Exercise the same calculate-to-save path at the compact integrated size.
    # Only the native save dialogs are stubbed by the export check.
    full_size = root.geometry()
    root.geometry('1120x780+0+0')
    root.update()
    try:
        assert (root.winfo_width(), root.winfo_height()) == (1120, 780)
        _require_visible_button(controller.run_button, controller.root)
        _require_visible_button(controller.inspection_button, controller.root)
        assert controller.chitubox_button.instate(['disabled'])
        controller.run_button.invoke()
        _wait(root, controller)
        snapshot = controller.snapshot
        assert snapshot is not None and snapshot.result.validation['complete']
        assert snapshot.result.height == 17 and len(snapshot.result.placements) == 2
        assert sorted(p.position[2] for p in snapshot.result.placements) == [0., 9.]
        export_proof = _verify_export_buttons(root, controller, directory, snapshot)
    finally:
        root.geometry(full_size)
        root.update()
    for view in ('origin','fit','x+','x-','y+','y-','z+','z-'):controller.preview.align(view)
    controller.preview.save_image(directory/'packing-preview.png')
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
    software=None
    if os.name=='nt':
        from mesa_runtime import graphics_proof
        software=graphics_proof(controller.preview.window)
    stress=model_from_mesh(trimesh.creation.annulus(r_min=10,r_max=12,height=20,sections=512),'frozen-stress')
    copies=[Placement(stress.model_id,i,rotation,((i%10)*28.,(i//10)*28.,0.)) for i in range(100)]
    started=time.perf_counter();controller.preview.set_scene({stress.model_id:stress},copies,(300,300,30))
    controller.preview.render();first_draw=time.perf_counter()-started
    assert len(controller.preview.model_actors)==100 and len(controller.preview.model_mappers)==1
    assert controller.preview.model_mappers[stress.model_id].GetInput().GetNumberOfPolys()==len(stress.mesh.faces)
    started=time.perf_counter();controller.preview.align('origin');controller.preview.render()
    camera_draw=time.perf_counter()-started
    benchmark={'copies':100,'triangles':len(stress.mesh.faces)*100,
               'first_draw_seconds':first_draw,'camera_draw_seconds':camera_draw}
    assert default_policy().to_dict()==before
    assert snapshot.result.metadata['reinforcement_learning']['td_updates']==0
    return {'passed':True,'step_native_import':True,'placed':2,'height_mm':17,
            'step_material_union':union_proof,
            'policy_digest':default_policy().digest,'new_training_updates':0,'preview_and_exports':True,
            'export_buttons': export_proof, 'depth_buffer_preview':pixels,
            'software_opengl':software,'preview_benchmark':benchmark}
