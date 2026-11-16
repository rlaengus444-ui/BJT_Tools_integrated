"""Isolate Windows VTK failures; no test is skipped or accepted by this probe."""
from pathlib import Path
import argparse
import json
import subprocess
import sys


def probe(case):
    import vtkmodules.vtkRenderingOpenGL2
    import vtkmodules.vtkRenderingFreeType
    from vtkmodules.vtkRenderingCore import vtkRenderer,vtkRenderWindow,vtkActor,vtkPolyDataMapper
    from vtkmodules.vtkFiltersSources import vtkSphereSource
    if case=='tk-preview':
        import tkinter as tk
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'suite'))
        from packing_preview import PackingPreview
        root=tk.Tk();root.geometry('1000x800')
        preview=PackingPreview(root);root.update_idletasks()
        print('Tk canvas',preview.canvas.winfo_width(),preview.canvas.winfo_height(),flush=True)
        print('before PackingPreview.render',flush=True)
        preview.render();print('after PackingPreview.render',preview.image.size,flush=True)
        preview.close();root.destroy();return
    if case=='ocp-sphere':
        from OCP.STEPControl import STEPControl_Reader
        print('OCP imported',flush=True)
    renderer=vtkRenderer();window=vtkRenderWindow();window.AddRenderer(renderer)
    window.SetSize(640,480);window.SetMultiSamples(0)
    window.SetOffScreenRendering(0 if case=='visible' else 1)
    if case!='empty':
        source=vtkSphereSource();mapper=vtkPolyDataMapper();mapper.SetInputConnection(source.GetOutputPort())
        actor=vtkActor();actor.SetMapper(mapper);renderer.AddActor(actor)
    if case=='axes':
        from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
        axes=vtkAxesActor();renderer.AddActor(axes)
    renderer.ResetCamera()
    print('class',window.GetClassName(),'offscreen',window.GetOffScreenRendering(),flush=True)
    print('before Render',flush=True);window.Render();print('after Render',flush=True)
    capabilities=window.ReportCapabilities()
    print('\n'.join(line for line in capabilities.splitlines() if any(word in line.lower() for word in ('vendor','renderer','version'))),flush=True)
    window.Finalize();print('finalized',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--case');args=parser.parse_args()
    if args.case:probe(args.case)
    else:
        reports=[]
        for case in ('empty','sphere','visible','axes','ocp-sphere','tk-preview'):
            try:
                run=subprocess.run([sys.executable,'-u','-X','faulthandler',__file__,'--case',case],capture_output=True,text=True,timeout=45)
                row={'case':case,'exit_code':run.returncode,'stdout':run.stdout,'stderr':run.stderr}
            except subprocess.TimeoutExpired as exc:
                row={'case':case,'timeout':True,'stdout':str(exc.stdout),'stderr':str(exc.stderr)}
            reports.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        Path('windows-renderer-probe.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n')
