from pathlib import Path
import hashlib,json,sys,traceback
HERE=Path(__file__).resolve().parent
SUITE=HERE.parents[1]/'suite'
sys.path[:0]=[str(SUITE),str(SUITE/'tests')]
from test_oven_playback_controls import OvenPlaybackControlsTests,fully_visible
from PIL import ImageGrab
from tkinter import font as tkfont
from tkinter import ttk
report={'passed':False,'scope':'Actual integrated SuiteApp under Windows CPython/Wine; source UI and real simulated3x3x3 savedframes','views':[]}
case=None
try:
 OvenPlaybackControlsTests.setUpClass()
 case=OvenPlaybackControlsTests('test_controls_stay_visible_in_both_tabs_at_supported_window_sizes');case.setUp()
 root,app=case.root,case.app
 for width,height in ((1660,860),(1120,780)):
  root.geometry(f'{width}x{height}+0+0')
  for language in ('ko','en'):
   app.language_var.set(language)
   for oxygen,tab in ((False,1),(True,3)):
    app.right_notebook.select(tab);root.update();root.lift();root.update()
    data={'size':[root.winfo_width(),root.winfo_height()],'language':language,'field':'oxygen' if oxygen else 'temperature','widgets':{}}
    for name in ('prev_btn','play_btn','pause_btn','stop_btn','next_btn','slider','time_label'):
     w=getattr(app,('o2_field_' if oxygen else 'field_')+name)
     data['widgets'][name]={'mapped':bool(w.winfo_ismapped()),'viewable':bool(w.winfo_viewable()),'fully_visible':fully_visible(w),'state':str(w.cget('state')) if name!='time_label' else None,'rect':[w.winfo_rootx(),w.winfo_rooty(),w.winfo_width(),w.winfo_height()]}
     assert fully_visible(w),(width,height,language,oxygen,name,data['widgets'][name])
     if name.endswith('_btn'):
      style=str(w.cget('style')) or 'TButton'
      font=tkfont.Font(root=root,font=ttk.Style(root).lookup(style,'font'))
      measured=font.measure(str(w.cget('text')))
      data['widgets'][name]['caption_pixels']=measured
      data['widgets'][name]['caption']=str(w.cget('text'))
      assert w.winfo_width()>=measured+8,(language,name,w.winfo_width(),measured)
    file=HERE/f'oven-3d-{width}x{height}-{language}-{data["field"]}.png'
    ImageGrab.grab(bbox=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())).save(file)
    data['screenshot']=str(file);data['screenshot_sha256']=hashlib.sha256(file.read_bytes()).hexdigest()
    report['views'].append(data)
 case.test_play_pause_and_stop_operate_on_saved_frames_without_changing_solver()
 case.test_legacy_toggle_api_keeps_working()
 report['play_pause_stop_and_legacy_api']='passed'
 report['source_sha256']=hashlib.sha256((SUITE/'sources/sic_buildbox_thermal_gui_v5.py').read_bytes()).hexdigest()
 report['passed']=True
except BaseException:
 report['traceback']=traceback.format_exc()
finally:
 if case:
  try:case.tearDown()
  except Exception:report['cleanup_error']=traceback.format_exc()
 (HERE/'windows-source-oven-3d-controls.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(report,ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
