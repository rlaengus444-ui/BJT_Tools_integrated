"""Real source capsule construction, extraction and independent payload hashes."""
import ast
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def test_empty_template_checksum_is_replaced_and_standalone_is_in_source_zip(tmp_path,monkeypatch):
    app=tmp_path/'suite';app.mkdir();output=tmp_path/'release'
    (app/'source_manifest.json').write_text('{"suite_version":"1.9.0","tools":[]}')
    (app/'run_suite.py').write_text('def start():\n    return 0\n')
    manuals=app/'manuals';manuals.mkdir();(manuals/'index.html').write_text('manual')
    separate=tmp_path/'standalone';separate.mkdir();(separate/'PowderNest.py').write_text('# separate reference\n')
    template=(ROOT/'BJT_통합프로그램.py').read_text()
    tree=ast.parse(template)
    lines=template.splitlines(keepends=True)
    for node in reversed(tree.body):
        if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('PAYLOAD','PAYLOAD_SHA256'):
            replacement="PAYLOAD_SHA256 = ''\n" if node.targets[0].id=='PAYLOAD_SHA256' else 'PAYLOAD = b""""""\n'
            lines[node.lineno-1:node.end_lineno]=[replacement]
    launcher=tmp_path/'BJT.py';launcher.write_text(''.join(lines))
    subprocess.run([sys.executable,str(ROOT/'release_tools/build_source_release.py'),'--app',str(app),
                    '--output',str(output),'--launcher-template',str(launcher),'--launcher-output',str(launcher)],check=True,capture_output=True)
    monkeypatch.setenv('XDG_CACHE_HOME',str(tmp_path/'cache'))
    namespace=runpy.run_path(str(launcher),run_name='_capsule_test')
    extracted=namespace['_extract_source']()
    assert (extracted/'run_suite.py').read_bytes()==(app/'run_suite.py').read_bytes()
    assert namespace['_extract_source']()==extracted
    (extracted/'run_suite.py').write_text('corrupted cached source')
    fresh=namespace['_extract_source']()
    assert fresh!=extracted and (fresh/'run_suite.py').read_bytes()==(app/'run_suite.py').read_bytes()
    assert (extracted/'run_suite.py').read_text()=='corrupted cached source'
    with zipfile.ZipFile(next(output.glob('*전체소스.zip'))) as archive:
        assert archive.testzip() is None
        assert archive.read('standalone/PowderNest.py')==(separate/'PowderNest.py').read_bytes()


def test_new_backend_hash_failure_is_scoped_to_packing_tool(tmp_path,monkeypatch):
    sys.path.insert(0,str(ROOT/'suite'))
    import suite_loader
    source=b'class App:\n    pass\n';digest=hashlib.sha256(source).hexdigest()
    for name in ('old.py','new.py'):(tmp_path/name).write_bytes(source)
    (tmp_path/'packing_core.py').write_text('altered data')
    tools=[{'id':key,'source':name,'sha256':digest,'controller':'App','embedded_base':False}
           for key,name in (('chitu_checker','old.py'),('nesting','new.py'))]
    manifest={'tools':tools,'dependencies':[{'path':'packing_core.py','sha256':'0'*64,'tools':['nesting']}]}
    (tmp_path/'source_manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(suite_loader,'BASE_DIR',tmp_path)
    loader=suite_loader.ToolLoader()
    assert loader.load('chitu_checker').App
    import pytest
    with pytest.raises(RuntimeError,match='무결성'):loader.load('nesting')


def test_native_release_cannot_pass_by_changing_tool_count_alone():
    sys.path.insert(0,str(ROOT/'suite/배포_빌드'))
    from release_exe import require_packing_check
    import pytest
    with pytest.raises(RuntimeError,match='packing'):require_packing_check({'passed':True,'checks':[]})
    proof={'passed':True,'step_native_import':True,'placed':2,'height_mm':17,
           'step_material_union':{'passed':True,'source_solids':6,'material_solids':1,
                                  'volume_mm3':512.,'unicode_path':True,'gui_import':True},
           'new_training_updates':0,'preview_and_exports':True,
           'export_buttons':{'passed':True,'window_size':[1120,780],'calculate_button_invoked':True,
                             'save_buttons_invoked':2,'object_count':2,'unicode_output':True},
           'depth_buffer_preview':{'size':[640,480],'bore_rgb':[255,255,255],'wall_rgb':[90,100,130]},
           'software_opengl':{'passed':True,'dll_sha256':__import__('mesa_runtime').DLL_SHA256,
                              'capabilities':['OpenGL renderer string: llvmpipe']}}
    require_packing_check({'checks':[{'phase':'save','report':{'checks':{'packing_inference_and_exports':proof}}}]})
    for exports in (None, dict(proof['export_buttons'], window_size=[1400,950]),
                    dict(proof['export_buttons'], calculate_button_invoked=False),
                    dict(proof['export_buttons'], object_count=1)):
        with pytest.raises(RuntimeError,match='CHITUBOX export button'):
            require_packing_check({'checks':[{'phase':'save','report':{'checks':{
                'packing_inference_and_exports':dict(proof,export_buttons=exports)}}}]})
    for union in (None, {}, 'invalid', dict(proof['step_material_union'],gui_import=False),
                  dict(proof['step_material_union'],material_solids=6),
                  *[dict(proof['step_material_union'],volume_mm3=value)
                    for value in (float('nan'),float('inf'),None,'512',True)]):
        with pytest.raises(RuntimeError,match='STEP'):
            require_packing_check({'checks':[{'phase':'save','report':{'checks':{
                'packing_inference_and_exports':dict(proof,step_material_union=union)}}}]})
    bad=dict(proof,new_training_updates=1)
    with pytest.raises(RuntimeError,match='packing'):
        require_packing_check({'checks':[{'phase':'save','report':{'checks':{'packing_inference_and_exports':bad}}}]})
    for preview in (None,{}, {'size':[2,2],'bore_rgb':[255,255,255],'wall_rgb':[90,100,130]},
                    {'size':[640,480],'bore_rgb':[255,255,255],'wall_rgb':[255,255,255]},
                    {'size':[640,480],'bore_rgb':[20,20,20],'wall_rgb':[90,100,130]}):
        bad=dict(proof,depth_buffer_preview=preview)
        with pytest.raises(RuntimeError,match='packing'):
            require_packing_check({'checks':[{'phase':'save','report':{'checks':{'packing_inference_and_exports':bad}}}]})
