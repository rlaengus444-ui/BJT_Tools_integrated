"""Real Tk integration, exact engine snapshots, cancellation and preview budgets."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import threading
import time
import subprocess
import os
import queue

import numpy as np
import pytest
import trimesh

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'sources'))
import packing_service as service
from powdernest.geometry import model_from_mesh
from powdernest.rl import default_policy
from powdernest.types import Placement,Settings,matrix_for


def records(path,quantity=3,unit='mm'):
    return [{'id':'part','path':str(path),'quantity':quantity,'unit':unit,'rotations':list(range(24))}]


def fixture_job(tmp_path):
    path=tmp_path/'part.stl';trimesh.creation.box((4,3,2)).export(path)
    rows=records(path);models,errors,cancelled=service.load_inputs(rows,threading.Event())
    assert not errors and not cancelled
    settings=Settings(size=(12,12,15),gap=.5,wall_gap=1,learning=True,iterations=4,time_limit=20,early_stop=False)
    return rows,models,settings


def test_service_inference_and_both_exports_preserve_policy_source_and_conditions(tmp_path):
    rows,models,settings=fixture_job(tmp_path);before=default_policy().to_dict()
    raw=Path(rows[0]['path']).read_bytes()
    snapshot=service.calculate(models,rows,settings,threading.Event())
    assert snapshot.result.validation['valid'] and snapshot.result.validation['complete']
    stats=snapshot.result.metadata['reinforcement_learning']
    assert stats['enabled'] and stats['td_updates']==0 and stats['policy_before']==default_policy().digest
    assert default_policy().to_dict()==before
    service.save_snapshot(snapshot,tmp_path/'export')
    service.save_snapshot(snapshot,tmp_path/'layout.chitubox','chitubox')
    report=json.loads((tmp_path/'export/report.json').read_text())
    encoded=json.loads((tmp_path/'layout.chitubox.json').read_text())
    assert report['validation']['complete'] and encoded['encoded_geometry_validation']['valid']
    assert report['settings']['size']==list(settings.size)
    assert default_policy().to_dict()==before and Path(rows[0]['path']).read_bytes()==raw
    assert not list(tmp_path.glob('*.history'))


def test_stl_units_and_cancelled_import_commit_no_incomplete_batch(tmp_path,monkeypatch):
    path=tmp_path/'cm.stl';trimesh.creation.box((1,2,3)).export(path)
    loaded,errors,cancelled=service.load_inputs(records(path,unit='cm'),threading.Event())
    np.testing.assert_allclose(loaded['part'].mesh.extents,[10,20,30])
    event=threading.Event();original=service.load_model
    def interrupt(*args,**kwargs):
        value=original(*args,**kwargs);event.set();return value
    monkeypatch.setattr(service,'load_model',interrupt)
    assert service.load_inputs(records(path),event)==({},[],True)


def test_step_input_uses_ocp_and_preserves_declared_units(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPControl import STEPControl_Writer,STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    path=tmp_path/'part.STEP.step';writer=STEPControl_Writer()
    assert writer.Transfer(BRepPrimAPI_MakeBox(10.,20.,30.).Shape(),STEPControl_AsIs)==IFSelect_RetDone
    assert writer.Write(str(path))==IFSelect_RetDone
    models,errors,cancelled=service.load_inputs(records(path),threading.Event())
    assert not errors and not cancelled and models['part'].original_shape is not None
    np.testing.assert_allclose(models['part'].mesh.extents,[10,20,30])


@pytest.mark.parametrize('use_policy',[True,False])
def test_public_integrated_cli_runs_headless_without_training(tmp_path,use_policy):
    path=tmp_path/'part.stl';trimesh.creation.box((4,3,2)).export(path)
    job=tmp_path/'job.json';job.write_text(json.dumps({'models':[{'id':'part','path':str(path),'quantity':3}],
        'settings':{'size':[12,12,15],'gap':.5,'wall_gap':1,'time_limit':2,'iterations':2}}))
    destination=tmp_path/'output'
    command=[sys.executable,str(ROOT/'run_suite.py'),'--tool','nesting','--','--job',str(job),'--output',str(destination)]
    if not use_policy:command.append('--no-policy')
    run=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stdout+run.stderr
    report=json.loads((destination/'report.json').read_text())
    assert report['validation']['valid'] and report['validation']['complete']
    stats=report['metadata']['reinforcement_learning']
    assert stats['td_updates']==0 and stats['enabled'] is use_policy


@pytest.mark.parametrize('backend',['python','rust'])
def test_xy_walls_full_z_and_cancellation_return_independently_valid_incumbent(tmp_path,backend):
    from powdernest import native
    path=tmp_path/'cube.stl';trimesh.creation.box((8,8,8)).export(path)
    rows=records(path,2);models,_,_=service.load_inputs(rows,threading.Event())
    settings=Settings(size=(10,10,17),gap=1,wall_gap=1,iterations=0,time_limit=20)
    with native.backend(backend):snapshot=service.calculate(models,rows,settings,threading.Event())
    assert snapshot.result.validation['complete'] and snapshot.result.height==17
    assert sorted(p.position[2] for p in snapshot.result.placements)==[0.,9.]
    event=threading.Event();event.set()
    cancelled=service.calculate(models,rows,replace(settings,iterations=10),event)
    assert cancelled.result.status=='cancelled' and cancelled.result.validation['valid']


def test_preview_full_mesh_shared_for_all_copies_and_24_transforms(ui):
    root,c=ui
    mesh=trimesh.creation.icosphere(subdivisions=2);mesh.apply_translation((123,-45,67))
    model=model_from_mesh(mesh,'part');before=(model.mesh.vertices.tobytes(),model.mesh.faces.tobytes())
    poses=[Placement('part',i,i%24,(i*3.,1.,0.)) for i in range(100)]
    c.preview.set_scene({'part':model},poses,(500,500,350));c.preview.render()
    assert len(c.preview.model_actors)==100 and len(c.preview.model_mappers)==1
    mapper=c.preview.model_mappers['part']
    assert mapper.GetInput().GetNumberOfPolys()==len(model.mesh.faces)
    for pose,actor in zip(poses,c.preview.model_actors):
        assert actor.GetMapper() is mapper
        matrix=actor.GetUserMatrix()
        actual=np.array([[matrix.GetElement(i,j) for j in range(4)] for i in range(4)])
        np.testing.assert_allclose(actual,matrix_for(model,pose))
    matrices=[actor.GetUserMatrix() for actor in c.preview.model_actors]
    assert len({id(matrix) for matrix in matrices})==100
    assert before==(model.mesh.vertices.tobytes(),model.mesh.faces.tobytes())


def test_preview_depth_buffer_shows_open_bore_opaque_wall_and_fresh_camera(ui,tmp_path):
    from powdernest.rotations import ROTATIONS
    root,c=ui
    mesh=trimesh.creation.annulus(r_min=10,r_max=12,height=20,sections=128)
    mesh.apply_translation((123,-45,67));before=(mesh.vertices.tobytes(),mesh.faces.tobytes())
    model=model_from_mesh(mesh,'tube')
    rotation=next(i for i,value in enumerate(ROTATIONS) if np.array_equal(value,np.eye(3)))
    pose=Placement('tube',0,rotation,(10.,10.,0.))
    c.preview.set_scene({'tube':model},[pose],(50,50,30));c.preview.align('z+');c.preview.render()
    def pixel(local):
        point=trimesh.transform_points([local],matrix_for(model,pose))[0]
        c.preview.renderer.SetWorldPoint(*point,1.);c.preview.renderer.WorldToDisplay()
        x,y,z=c.preview.renderer.GetDisplayPoint()
        return np.array(c.preview.image.getpixel((int(x),c.preview.image.height-1-int(y))))
    assert np.min(pixel((123,-45,77)))>240  # empty bore
    assert np.min(pixel((134,-45,77)))<220  # opaque annular wall
    first=c.preview.image.tobytes()
    c.preview.align('x+');c.preview.save_image(tmp_path/'side.png')
    assert c.preview.image.tobytes()!=first and (tmp_path/'side.png').is_file()
    assert before==(mesh.vertices.tobytes(),mesh.faces.tobytes())


def test_preview_close_cancels_pending_render_and_releases_window(ui):
    root,c=ui;c.preview._request_render()
    assert c.preview._render_id is not None
    c.preview.close();c.preview.close();root.update()
    assert c.preview.closed and c.preview._render_id is None
    with pytest.raises(RuntimeError,match='닫혔습니다'):c.preview.render()


def test_preview_one_render_per_frame_and_fit_preserves_top_view(ui):
    root,c=ui;starts=[]
    c.preview.window.AddObserver('StartEvent',lambda *unused:starts.append(True))
    c.preview.render();assert len(starts)==1
    for view in ('z+','z-'):
        c.preview.align(view);before=c.preview.camera.GetViewUp()
        c.preview.align('fit')
        np.testing.assert_allclose(c.preview.camera.GetViewUp(),before)
        assert abs(np.dot(c.preview.camera.GetViewUp(),c.preview.camera.GetDirectionOfProjection()))<1e-8


@pytest.fixture
def ui(tmp_path,monkeypatch):
    import tkinter as tk
    import powdernest_tool as app
    from suite_theme import ThemeManager
    try:root=tk.Tk()
    except tk.TclError:pytest.skip('a Tk display is required')
    root.geometry('1450x950');ThemeManager(root).apply()
    monkeypatch.setattr(app.messagebox,'showerror',lambda *a,**k:None)
    controller=app.PackingApp(root);root.update()
    yield root,controller
    if not controller.disposed:controller.close()
    try:root.update()
    except tk.TclError:pass


def pump(root,controller,seconds=30):
    deadline=time.monotonic()+seconds
    while (controller.worker is not None or not controller.events.empty()) and not controller.disposed:
        root.update();time.sleep(.005)
        assert time.monotonic()<deadline,'packing worker did not finish'
    if not controller.disposed:root.update()


def test_tk_tab_import_quantity_calculate_export_and_invalidate(ui,tmp_path):
    root,c=ui;path=tmp_path/'part.stl';trimesh.creation.box((4,3,2)).export(path)
    assert c.learning_var.get() and not hasattr(c,'start_training')
    c.add_paths([path]);pump(root,c)
    c.rows[0]['quantity']=3;c._changed();c._refresh_table()
    c.seconds_var.set(1);c.balance_seconds_var.set(0)
    c.start();pump(root,c)
    assert c.snapshot.result.validation['complete'] and len(c.snapshot.result.placements)==3
    assert str(c.export_button.cget('state'))=='normal'
    captured=c.snapshot
    c.gap_var.set(2.5)
    assert c.snapshot is None and str(c.export_button.cget('state'))=='disabled'
    service.save_snapshot(captured,tmp_path/'old-conditions')
    assert json.loads((tmp_path/'old-conditions/report.json').read_text())['settings']['gap']==2


def test_state_restore_does_not_import_geometry_or_restore_results(ui,tmp_path,monkeypatch):
    from suite_state import capture_inputs,restore_inputs
    root,c=ui;path=tmp_path/'missing.stl';c.rows=records(path,7,'cm')
    state=capture_inputs('nesting',c)
    c.rows=[];c.learning_var.set(False)
    monkeypatch.setattr(service,'load_model',lambda *a,**k:pytest.fail('restoration must not read CAD'))
    assert not restore_inputs('nesting',c,state)
    assert c.rows[0]['quantity']==7 and c.rows[0]['unit']=='cm'
    assert c.models=={} and c.snapshot is None and c.worker is None and c.learning_var.get()
    assert '파일 다시 읽기' in c.status_var.get()
    assert not any(key in json.dumps(state) for key in ('weights','mesh.vertices','placements'))


def test_error_and_stale_worker_completion_recover_controls(ui):
    root,c=ui
    def failure(*args):raise RuntimeError('expected import failure')
    c._launch('import',failure);pump(root,c)
    assert 'expected import failure' in c.details.get('1.0','end')
    assert c.progress_bar['value']==0 and '오류' in c.progress_var.get()
    entered=threading.Event();release=threading.Event()
    def stale(*args):entered.set();release.wait(10);return {'invalid':'stale payload'}
    c._launch('import',stale);assert entered.wait(3)
    c._changed();release.set();pump(root,c)
    assert c.models=={} and c.snapshot is None and str(c.run_button.cget('state'))=='normal'
    assert c.progress_bar['value']==0
    c._launch('import',lambda *args:{'bad':'terminal payload'});pump(root,c)
    assert c.snapshot is None and str(c.run_button.cget('state'))=='normal'
    assert '결과 표시 중 오류' in c.status_var.get()


def test_live_progress_search_allowance_phases_and_cancellation(ui,tmp_path,monkeypatch):
    from types import SimpleNamespace
    import powdernest_tool as app
    root,c=ui;clock=[100.]
    rows,models,settings=fixture_job(tmp_path)
    snapshot=service.calculate(models,rows,settings,threading.Event())
    settings=replace(settings,time_limit=10,precision=True,precision_time=4)
    monkeypatch.setattr(app,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    entered=threading.Event();release=threading.Event()
    def waiting(*args):entered.set();release.wait(10);return snapshot
    c._launch('optimize',waiting,settings);assert entered.wait(3)
    try:
        clock[0]=105.;c._poll()
        assert '경과 5.0초' in c.progress_var.get() and '산정 전' in c.progress_var.get()
        identity=c._job_identity
        c.events.put(('progress',identity,{'placed':1,'requested':2,'height':5.,'elapsed':2.,'stage':'algorithm','status':'searching'}))
        c._poll();clock[0]=108.;c._poll()
        assert '최대 9.0초' in c.progress_var.get() and '1/2개 (50%)' in c.status_var.get()
        c.events.put(('progress',identity,{'elapsed':11.,'stage':'precision','status':'searching'}));c._poll()
        assert '정밀 탐색' in c.status_var.get() and '최대 3.0초' in c.progress_var.get()
        c.events.put(('progress',identity,{'status':'xy_balancing','stage':'precision'}));c._poll()
        assert 'XY 정렬' in c.status_var.get() and '1/2개' in c.status_var.get()
        assert str(c.progress_bar.cget('mode'))=='indeterminate'
        c.events.put(('progress',identity,{'status':'validating','stage':'precision'}));c._poll()
        assert '최종 검증 중' in c.progress_var.get() and '계산 불가' in c.progress_var.get()
        c.stop();c._poll()
        assert '취소 요청' in c.status_var.get() and '취소 정리' in c.progress_var.get()
    finally:
        release.set();pump(root,c)
    assert str(c.progress_bar.cget('mode'))=='determinate' and c.progress_bar['value']==0


def test_preview_keeps_build_box_and_xyz_without_grid_or_ticks(ui):
    root,c=ui
    c.redraw();root.update()
    axes=c.preview.axes
    assert (axes.GetXAxisLabelText(),axes.GetYAxisLabelText(),axes.GetZAxisLabelText())==('X','Y','Z')
    for caption in (axes.GetXAxisCaptionActor2D(),axes.GetYAxisCaptionActor2D(),axes.GetZAxisCaptionActor2D()):
        assert max(caption.GetCaptionTextProperty().GetColor())<.85
    assert c.preview.build_box.GetProperty().GetRepresentationAsString()=='Wireframe'
    assert c.preview.renderer.GetViewProps().GetNumberOfItems()==3


def test_preview_failure_keeps_valid_calculation_exportable(ui,tmp_path,monkeypatch):
    root,c=ui;rows,models,settings=fixture_job(tmp_path)
    snapshot=service.calculate(models,rows,settings,threading.Event())
    def broken(*args):raise RuntimeError('display failure')
    monkeypatch.setattr(c.preview,'set_scene',broken)
    c._launch('optimize',lambda *args:snapshot,settings);pump(root,c)
    assert c.snapshot is snapshot and c.snapshot.result.validation['valid']
    assert str(c.export_button.cget('state'))=='normal' and 'display failure' in c.preview_notice.get()


def test_close_waits_for_export_cleanup(ui):
    root,c=ui;entered=threading.Event();release=threading.Event()
    def writing(*args):entered.set();release.wait(10);return 'saved'
    c._launch('export',writing);assert entered.wait(3)
    c.close();root.update()
    assert not c.disposed and c.cancel_event.is_set() and c.is_busy
    release.set();pump(root,c)
    assert c.disposed and (c.worker is None or not c.worker.is_alive())


@pytest.mark.parametrize('operation',['import','optimize'])
def test_completion_arriving_after_empty_queue_keeps_its_operation(ui,tmp_path,operation):
    from suite_ui import SuiteApp
    root,c=ui;release=threading.Event();entered=threading.Event()
    rows,models,settings=fixture_job(tmp_path)
    snapshot=service.calculate(models,rows,settings,threading.Event())
    payload=({},[],False) if operation=='import' else snapshot
    class DelayedTerminal(queue.Queue):
        armed=True
        def get_nowait(self):
            try:return super().get_nowait()
            except queue.Empty:
                if self.armed:
                    self.armed=False;release.set();c.worker.join(5)
                    assert not c.worker.is_alive()
                raise
    c.events=DelayedTerminal()
    def work(*args):entered.set();release.wait(5);return payload
    c._launch(operation,work);assert entered.wait(3)
    c._poll()
    assert c.worker is not None and c.is_busy and c.task_kind==operation
    assert SuiteApp.busy('nesting',c)
    c._poll()
    assert c.worker is None and not c.is_busy
    if operation=='optimize':assert c.snapshot is snapshot
    else:assert '모델 읽기 완료' in c.status_var.get()
    assert '저장 완료' not in c.status_var.get()


@pytest.mark.parametrize('cancelled',[False,True])
def test_failed_or_cancelled_reload_never_reuses_old_geometry(ui,tmp_path,monkeypatch,cancelled):
    root,c=ui;path=tmp_path/'part.stl';trimesh.creation.box().export(path)
    c.add_paths([path]);pump(root,c);key=c.rows[0]['id']
    assert key in c.models
    path.unlink()
    if cancelled:
        import powdernest_tool
        monkeypatch.setattr(powdernest_tool,'load_inputs',lambda *args:({},[],True))
    c.reload_inputs();assert key not in c.models;pump(root,c)
    assert key not in c.models
    c.start();pump(root,c)
    assert c.snapshot is None and str(c.export_button.cget('state'))=='disabled'
    assert '아직 읽지 못한 모델' in c.details.get('1.0','end')


@pytest.mark.parametrize('target_name',['layout.stl','placements.json','report.json','report.txt'])
def test_standard_export_preflight_preserves_inputs_and_all_existing_targets(tmp_path,target_name):
    source=tmp_path/'source.stl';trimesh.creation.box().export(source)
    rows=records(source,2);models,_,_=service.load_inputs(rows,threading.Event())
    snapshot=service.calculate(models,rows,Settings(size=(20,20,20),iterations=0,time_limit=10),threading.Event())
    output=tmp_path/'out';output.mkdir()
    for name in ('layout.stl','placements.json','report.json','report.txt'):(output/name).write_bytes(b'old-output-'+name.encode())
    target=output/target_name;target.unlink();os.link(source,target)
    before={p:p.read_bytes() for p in [source,*output.iterdir()]}
    with pytest.raises(ValueError,match='원본'):service.save_snapshot(snapshot,output)
    assert {p:p.read_bytes() for p in before}==before


def test_export_direct_source_name_and_chitubox_sidecar_alias_are_blocked(tmp_path):
    source=tmp_path/'layout.stl';trimesh.creation.box().export(source)
    rows=records(source,2);models,_,_=service.load_inputs(rows,threading.Event())
    snapshot=service.calculate(models,rows,Settings(size=(20,20,20),iterations=0,time_limit=10),threading.Event())
    original=source.read_bytes()
    with pytest.raises(ValueError,match='원본'):service.save_snapshot(snapshot,tmp_path)
    destination=tmp_path/'part.chitubox';sidecar=Path(str(destination)+'.json')
    os.link(source,sidecar);destination.write_bytes(b'previous chitubox')
    with pytest.raises(ValueError,match='원본'):service.save_snapshot(snapshot,destination,'chitubox')
    assert source.read_bytes()==sidecar.read_bytes()==original and destination.read_bytes()==b'previous chitubox'


def test_ignored_input_and_deleted_original_hardlink_are_still_protected(tmp_path):
    rows,models,settings=fixture_job(tmp_path)
    ignored=tmp_path/'ignored.stl';trimesh.creation.box().export(ignored)
    rows.append(dict(records(ignored,0)[0],id='ignored'))
    snapshot=service.calculate(models,rows,settings,threading.Event())
    out=tmp_path/'out';out.mkdir();target=out/'layout.stl';os.link(ignored,target)
    before=target.read_bytes();ignored.unlink()
    with pytest.raises(ValueError,match='원본'):service.save_snapshot(snapshot,out)
    assert target.read_bytes()==before
