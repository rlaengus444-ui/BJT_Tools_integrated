"""Read-only source UI captures. No conversion, inspection or solver jobs run."""
from pathlib import Path
import argparse
import copy
import json
import os
import sys
import time
import tkinter as tk
from tkinter import ttk
from PIL import ImageGrab

parser=argparse.ArgumentParser()
parser.add_argument('app_root')
parser.add_argument('output_dir')
parser.add_argument('--prefix',default='windows')
args=parser.parse_args()
app_root=Path(args.app_root)
output=Path(args.output_dir)
output.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(app_root))
from suite_loader import verify_sources
checks=verify_sources()
if not all(row['valid'] for row in checks):
 raise RuntimeError('Actual source manifest must pass before UI capture: '+repr([r for r in checks if not r['valid']]))
from suite_ui import SuiteApp,create_root
root=create_root()
app=SuiteApp(root,test_mode=True)
width=min(1740,root.winfo_screenwidth()-80)
height=min(960,root.winfo_screenheight()-100)
root.geometry(f'{width}x{height}+0+0')
root.update()
report={'platform':sys.platform,'os_name':os.name,'scope':'Source UI only, actual manifest hashes verified. test_mode=True uses in-memory settings. No simulation/conversion/inspection jobs. No application sources or home settings files modified.','manifest_checks':checks,'screenshots':[],'checks':{},'screen_size':[root.winfo_screenwidth(),root.winfo_screenheight()]}

def descendants(widget):
 yield widget
 for child in widget.winfo_children():yield from descendants(child)

def texts(widget):
 result=[]
 for node in descendants(widget):
  try:
   if 'text' in node.keys():
    value=str(node.cget('text'))
    if value:result.append(value)
  except tk.TclError:pass
 return result

def capture(name):
 root.update_idletasks();root.update();time.sleep(.2);root.update()
 x,y=root.winfo_rootx(),root.winfo_rooty()
 w,h=root.winfo_width(),root.winfo_height()
 path=output/(args.prefix+'-'+name+'.png')
 picture=ImageGrab.grab() if os.name=='nt' else ImageGrab.grab(xdisplay=os.environ.get('DISPLAY'))
 picture.crop((x,y,x+w,y+h)).save(path)
 report['screenshots'].append({'file':path.name,'client_size':[w,h],'root_xy':[x,y],'desktop_image_size':list(picture.size)})

def mount(key):
 app.loading.add(key)
 module=app.loader.load(key)
 controller=app.mount_tool(key,module)
 app.select_tool(key);root.update()
 return controller

try:
 home_text=texts(app.home)
 report['checks']['simple_home']=('도구 선택' in home_text and not any('하나의 공간' in t or '11가지 작업' in t for t in home_text))
 capture('home')
 image=mount('m2_converter')
 capture('image-default')
 for name,value in [('width_var','1024'),('height_var','768'),('dpi_x_var','300'),('dpi_y_var','350.5')]:getattr(image,name).set(value)
 report['checks']['image_custom_values']={name:getattr(image,name).get() for name in ('width_var','height_var','dpi_x_var','dpi_y_var')}
 capture('image-custom')
 thermal=mount('thermal');thermal.language_var.set('ko');root.update()
 report['checks']['thermal_ko_labels']=texts(thermal)
 report['checks']['thermal_language_values']=list(thermal.language_combo.cget('values'))
 report['checks']['thermal_ko_tabs']=[thermal.right_notebook.tab(t,'text') for t in thermal.right_notebook.tabs()]
 before=copy.deepcopy(thermal._capture_settings());before['variables'].pop('language')
 capture('oven-ko')
 thermal.left_canvas.yview_moveto(1);root.update();capture('oven-ko-bottom')
 thermal.left_canvas.yview_moveto(0)
 thermal.language_combo.current(1);thermal.language_combo.event_generate('<<ComboboxSelected>>');root.update()
 report['checks']['thermal_en_model']=thermal.language_var.get()
 report['checks']['thermal_en_tabs']=[thermal.right_notebook.tab(t,'text') for t in thermal.right_notebook.tabs()]
 after=copy.deepcopy(thermal._capture_settings());after['variables'].pop('language')
 report['checks']['locale_preserves_inputs_except_language']=before==after
 capture('oven-en')
 chitu=mount('chitu_checker')
 report['checks']['chitu_drop_available']=chitu._dnd_available
 report['checks']['chitu_drop_hint']=chitu.drop_hint_var.get()
 capture('chitu')
 root.geometry('1120x780+0+0');root.update();capture('chitu-small')
 app.select_tool('m2_converter');root.update();capture('image-small')
 app.select_tool('thermal');thermal.language_var.set('ko');root.update();capture('oven-ko-small')
 report['checks']['callback_failures']=app.failures
finally:
 app.request_close(confirm=False)
 report['checks']['close_errors']=app.close_errors
 report['checks']['settings_warnings']=app.settings_warnings
 (output/(args.prefix+'-observations.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps({'screenshots':len(report['screenshots']),'output':str(output),'checks':{k:v for k,v in report['checks'].items() if not k.endswith('labels') and not k.endswith('values') and not k.endswith('tabs')}},ensure_ascii=False))
