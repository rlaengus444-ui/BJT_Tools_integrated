"""Isolate Windows VTK failures; no test is skipped or accepted by this probe."""
from pathlib import Path
import argparse
import json
import subprocess
import sys


def probe(case):
    print('initializing',case,flush=True)
    if case=='ocp-first':
        from OCP.STEPControl import STEPControl_Reader
        print('OCP imported before VTK',flush=True)
    import vtkmodules.vtkRenderingOpenGL2
    import vtkmodules.vtkRenderingFreeType
    from vtkmodules.vtkRenderingCore import vtkRenderer,vtkRenderWindow,vtkActor,vtkPolyDataMapper
    from vtkmodules.vtkFiltersSources import vtkSphereSource
    if case in ('tk-preview','tk-hidden-native'):
        import tkinter as tk
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'suite'))
        from packing_preview import PackingPreview
        class ManualPreview(PackingPreview):
            def _request_render(self):pass
        root=tk.Tk();root.geometry('1000x800')
        preview=ManualPreview(root);root.update()
        if case=='tk-hidden-native':
            preview.window.SetOffScreenRendering(0);preview.window.SetShowWindow(False)
        print('Tk canvas',preview.canvas.winfo_width(),preview.canvas.winfo_height(),flush=True)
        print('before SetSize',flush=True);preview._size_window();print('after SetSize',flush=True)
        print('before clipping',flush=True);preview.renderer.ResetCameraClippingRange();print('after clipping',flush=True)
        print('before window.Render',flush=True);preview.window.Render();print('after window.Render',flush=True)
        print('before capture',flush=True);preview.capture.Modified();preview.capture.Update();print('after capture',flush=True)
        print('before PackingPreview.render',flush=True)
        preview.render();print('after PackingPreview.render',preview.image.size,flush=True)
        print('before Finalize',flush=True)
        preview.close();root.destroy();return
    if case=='ocp-sphere':
        from OCP.STEPControl import STEPControl_Reader
        print('OCP imported',flush=True)
    renderer=vtkRenderer();window=vtkRenderWindow();window.AddRenderer(renderer)
    print('before SetSize',flush=True);window.SetSize(640,480);print('after SetSize',flush=True)
    window.SetMultiSamples(0)
    window.SetOffScreenRendering(0 if case in ('visible','hidden-native') else 1)
    if case=='hidden-native':window.SetShowWindow(False)
    if case!='empty':
        source=vtkSphereSource();mapper=vtkPolyDataMapper();mapper.SetInputConnection(source.GetOutputPort())
        actor=vtkActor();actor.SetMapper(mapper);renderer.AddActor(actor)
    if case=='axes':
        from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
        axes=vtkAxesActor();renderer.AddActor(axes)
    print('before clipping',flush=True);renderer.ResetCamera();renderer.ResetCameraClippingRange();print('after clipping',flush=True)
    print('class',window.GetClassName(),'offscreen',window.GetOffScreenRendering(),'buffers',window.GetUseOffScreenBuffers(),flush=True)
    print('before Render',flush=True);window.Render();print('after Render',flush=True)
    capabilities=window.ReportCapabilities()
    print('\n'.join(line for line in capabilities.splitlines() if any(word in line.lower() for word in ('vendor','renderer','version'))),flush=True)
    print('before Finalize',flush=True);window.Finalize();print('finalized',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--case');args=parser.parse_args()
    if args.case:probe(args.case)
    else:
        reports=[]
        for case in ('empty','sphere','visible','hidden-native','axes','ocp-first','ocp-sphere','tk-preview','tk-hidden-native'):
            try:
                run=subprocess.run([sys.executable,'-u','-X','faulthandler',__file__,'--case',case],capture_output=True,text=True,timeout=45)
                row={'case':case,'exit_code':run.returncode,'stdout':run.stdout,'stderr':run.stderr}
            except subprocess.TimeoutExpired as exc:
                row={'case':case,'timeout':True,'stdout':str(exc.stdout),'stderr':str(exc.stderr)}
            reports.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        passed=any(row['case']=='tk-preview' and row.get('exit_code')==0 for row in reports)
        Path('windows-renderer-probe.json').write_text(json.dumps({'passed':passed,'cases':reports},ensure_ascii=False,indent=2)+'\n')
        raise SystemExit(0 if passed else 1)
