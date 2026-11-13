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
from packing_preview import preview_triangles
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


def test_preview_budget_repeated_copies_and_nonzero_origins_leave_geometry_unchanged():
    mesh=trimesh.creation.icosphere(subdivisions=2);mesh.apply_translation((123,-45,67))
    model=model_from_mesh(mesh,'part');before=(model.mesh.vertices.tobytes(),model.mesh.faces.tobytes())
    poses=[Placement('part',i,i%24,(i*3.,1.,0.)) for i in range(100)]
    groups,displayed,original=preview_triangles({'part':model},poses,limit=18000)
    assert len(groups)==100 and displayed<=18000 and original==100*len(mesh.faces)
    first=groups[0][0];matrix=matrix_for(model,poses[0])
    expected=trimesh.transform_points(model.mesh.vertices,matrix)
    assert np.all(first>=expected.min(0)-1e-12) and np.all(first<=expected.max(0)+1e-12)
    assert before==(model.mesh.vertices.tobytes(),model.mesh.faces.tobytes())


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
    entered=threading.Event();release=threading.Event()
    def stale(*args):entered.set();release.wait(10);return {'invalid':'stale payload'}
    c._launch('import',stale);assert entered.wait(3)
    c._changed();release.set();pump(root,c)
    assert c.models=={} and c.snapshot is None and str(c.run_button.cget('state'))=='normal'
    c._launch('import',lambda *args:{'bad':'terminal payload'});pump(root,c)
    assert c.snapshot is None and str(c.run_button.cget('state'))=='normal'
    assert '결과 표시 중 오류' in c.status_var.get()


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
