"""Native Tk packing tab for the BJT suite; supplied policy is inference only."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import traceback
import uuid

from packing_preview import PackingPreview
from packing_service import calculate, input_records, load_inputs, save_snapshot
from powdernest.profiles import BuildProfile, ProfileLibrary
from powdernest.rl import default_policy
from powdernest.types import Placement, Settings


class PackingApp:
    def __init__(self, root):
        self.root = root
        self.rows = []
        self.models = {}
        self.snapshot = None
        self.worker = None
        self.cancel_event = threading.Event()
        self.events = queue.Queue()
        self.revision = 0
        self.job_id = 0
        self.task_kind = None
        self._job_identity = None
        self._terminal_received = False
        self._job_success = False
        self._job_started = None
        self._progress_data = {}
        self._progress_received = None
        self._search_allowance = 0.
        self.disposed = self.closing = False
        self.profile_library = ProfileLibrary.defaults()
        self.profile_var = tk.StringVar(root, value='대형 450 × 500 × 350')
        self.unit_var = tk.StringVar(root, value='mm')
        for name,value in (('size_x',450),('size_y',500),('size_z',350),('gap',2),('wall',1),
                           ('seconds',10),('precision_seconds',10),('balance_seconds',3)):
            setattr(self,name+'_var',tk.DoubleVar(root,value=value))
        self.seed_var = tk.IntVar(root,value=42)
        self.learning_var = tk.BooleanVar(root,value=True)
        self.precision_var = tk.BooleanVar(root,value=False)
        self.native_var = tk.BooleanVar(root,value=True)
        self.interlocking_var = tk.BooleanVar(root,value=True)
        self.early_var = tk.BooleanVar(root,value=True)
        self.mode_var = tk.StringVar(root,value='메시')
        self.balance_var = tk.StringVar(root,value='중앙 정렬 + 대칭 개선')
        self.status_var = tk.StringVar(root,value='파일을 추가하거나 저장된 파일 목록을 다시 읽으세요.')
        self.progress_var = tk.StringVar(root,value='')
        self.preview_notice = tk.StringVar(root,value='마우스 회전·휠 확대')
        self.drop_hint_var = tk.StringVar(root,value='STEP/STP/STL을 끌어 놓아 추가')
        self.controls = []
        self._build()
        for name in ('size_x','size_y','size_z','gap','wall','seconds','precision_seconds','balance_seconds',
                     'seed','learning','precision','native','interlocking','early','mode','balance'):
            getattr(self,name+'_var').trace_add('write',self._changed)
        self._enable_drop()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Destroy>',self._destroyed,add='+')
        self._poll_id = root.after(60,self._poll)

    @property
    def is_busy(self):
        return self.worker is not None

    def _button(self,parent,text,command):
        widget=ttk.Button(parent,text=text,command=command)
        self.controls.append(widget)
        return widget

    def _build(self):
        # Reserve the save row before the flexible panes request window space.
        # Keep it outside the sash so narrowing the controls cannot hide it.
        exports = ttk.Frame(self.root)
        exports.pack(side='bottom', fill='x', pady=(6, 0))
        self.export_button = ttk.Button(exports, text='STL·좌표·보고서 저장',
                                        command=lambda: self.export('standard'), state='disabled')
        self.export_button.pack(side='left')
        self.chitubox_button = ttk.Button(exports, text='CHITUBOX 프로젝트 저장',
                                          command=lambda: self.export('chitubox'), state='disabled')
        self.chitubox_button.pack(side='left', padx=(5, 0))
        panes=ttk.Panedwindow(self.root,orient='horizontal');panes.pack(fill='both',expand=True)
        left=ttk.Frame(panes,width=435);right=ttk.Frame(panes)
        panes.add(left,weight=0);panes.add(right,weight=1)
        notebook=ttk.Notebook(left)
        self.control_notebook=notebook
        inputs=ttk.Frame(notebook,padding=8);option_page=ttk.Frame(notebook)
        notebook.add(inputs,text='모델·수량');notebook.add(option_page,text='배치 설정')
        from suite_theme import COLORS
        option_canvas=tk.Canvas(option_page,background=COLORS['background'],highlightthickness=0,width=420)
        option_scroll=ttk.Scrollbar(option_page,orient='vertical',command=option_canvas.yview)
        option_canvas.configure(yscrollcommand=option_scroll.set)
        option_scroll.pack(side='right',fill='y');option_canvas.pack(side='left',fill='both',expand=True)
        options=ttk.Frame(option_canvas,padding=8)
        option_window=option_canvas.create_window((0,0),window=options,anchor='nw')
        options.bind('<Configure>',lambda event:option_canvas.configure(scrollregion=option_canvas.bbox('all')))
        option_canvas.bind('<Configure>',lambda event:option_canvas.itemconfigure(option_window,width=event.width))
        row=ttk.Frame(inputs);row.pack(fill='x')
        for text,command in (('파일 추가',self.choose_files),('선택 삭제',self.remove_selected),('파일 다시 읽기',self.reload_inputs)):
            self._button(row,text,command).pack(side='left',padx=(0,4))
        row=ttk.Frame(inputs);row.pack(fill='x',pady=7)
        ttk.Label(row,text='추가할 STL 단위').pack(side='left')
        units=ttk.Combobox(row,textvariable=self.unit_var,values=('mm','cm','m','in'),state='readonly',width=6)
        units.pack(side='left',padx=6);self.controls.append(units)
        ttk.Label(inputs,textvariable=self.drop_hint_var,wraplength=390).pack(anchor='w',pady=(0,6))
        self.table=ttk.Treeview(inputs,columns=('name','quantity','unit','state'),show='headings',height=12)
        for key,title,width in (('name','모델',155),('quantity','수량',48),('unit','단위',45),('state','상태',110)):
            self.table.heading(key,text=title);self.table.column(key,width=width,minwidth=35,stretch=key in ('name','state'))
        self.table.bind('<Double-1>',self.edit_quantity)
        self.table.bind('<<TreeviewSelect>>',lambda event:self.redraw())
        self.inspection_button = self._button(inputs, '선택 모델 검사 정보', self.inspection_info)
        self.inspection_button.pack(side='bottom', anchor='w')
        ttk.Label(inputs,text='수량 칸을 두 번 눌러 수정합니다. STEP 단위는 자동 확인합니다.',
                  wraplength=390).pack(side='bottom', anchor='w', pady=6)
        self.table.pack(fill='both',expand=True)
        form=ttk.Frame(options);form.pack(fill='x')
        fields=(('size_x','빌드 X (mm)'),('size_y','빌드 Y (mm)'),('size_z','최대 Z (mm)'),
                ('gap','모델 간격 (mm)'),('wall','X·Y 벽 간격 (mm)'),('seconds','계산 제한 (초)'),('seed','난수 시드'))
        for index,(name,label) in enumerate(fields):
            ttk.Label(form,text=label).grid(row=index,column=0,sticky='w',pady=3)
            entry=ttk.Entry(form,textvariable=getattr(self,name+'_var'),width=15)
            entry.grid(row=index,column=1,sticky='ew',padx=(10,0),pady=3);self.controls.append(entry)
        form.columnconfigure(1,weight=1)
        ttk.Label(options,text='벽 간격은 X·Y에만 적용합니다. STEP 삼각화 오차 여유는 별도입니다.',wraplength=390).pack(anchor='w',pady=7)
        for name,text in (('learning','내장 학습 정책 사용 · 학습 608회'),('precision','정밀 형상 탐색 추가'),
                          ('native','Rust 연산 사용'),('interlocking','고리 얽힘 방지'),('early','높이 하한 도달 시 빠른 종료')):
            box=ttk.Checkbutton(options,text=text,variable=getattr(self,name+'_var'))
            box.pack(anchor='w',pady=2);self.controls.append(box)
        for name,label in (('precision_seconds','정밀 추가 시간 (초)'),('balance_seconds','XY 정렬 추가 시간 (초)')):
            row=ttk.Frame(options);row.pack(fill='x',pady=3)
            ttk.Label(row,text=label).pack(side='left')
            entry=ttk.Entry(row,textvariable=getattr(self,name+'_var'),width=10);entry.pack(side='right');self.controls.append(entry)
        for variable,values in ((self.mode_var,('메시','경계 상자 (AABB)')),
                                (self.balance_var,('사용 안 함','전체 중앙 정렬','중앙 정렬 + 대칭 개선'))):
            widget=ttk.Combobox(options,textvariable=variable,values=values,state='readonly')
            widget.pack(fill='x',pady=4);self.controls.append(widget)
        profiles=ttk.LabelFrame(options,text='저장된 빌드박스',padding=6);profiles.pack(fill='x',pady=6)
        self.profile_combo=ttk.Combobox(profiles,textvariable=self.profile_var,state='readonly')
        self.profile_combo.pack(fill='x');self.controls.append(self.profile_combo)
        self.profile_combo.bind('<<ComboboxSelected>>',lambda event:self.apply_profile())
        row=ttk.Frame(profiles);row.pack(fill='x',pady=(5,0))
        for text,command in (('추가',self.add_profile),('수정',self.update_profile),('삭제',self.delete_profile),
                             ('가져오기',self.import_profiles),('내보내기',self.export_profiles)):
            self._button(row,text,command).pack(side='left',padx=(0,2))
        self._refresh_profiles()
        # Give calculation and its status priority over the flexible notebook.
        results = ttk.Frame(left)
        results.pack(side='bottom', fill='x', padx=(0,10))
        notebook.pack(fill='both', expand=True, padx=(0,10))
        actions=ttk.Frame(results);actions.pack(fill='x',pady=6)
        self.run_button=self._button(actions,'배치 계산',self.start)
        self.run_button.pack(side='left',fill='x',expand=True)
        self.stop_button=ttk.Button(actions,text='계산 취소',command=self.stop,state='disabled')
        self.stop_button.pack(side='left',padx=(5,0))
        ttk.Label(results,textvariable=self.status_var,wraplength=405).pack(fill='x')
        self.progress_bar=ttk.Progressbar(results,maximum=100,mode='determinate')
        self.progress_bar.pack(fill='x',pady=(5,2))
        ttk.Label(results,textvariable=self.progress_var,wraplength=405).pack(fill='x')
        self.details=tk.Text(results,height=8,wrap='word',state='disabled');self.details.pack(fill='x',pady=6)
        camera=ttk.Frame(right);camera.pack(fill='x')
        for key,label in (('origin','원점'),('fit','전체'),('x+','X+'),('x-','X−'),('y+','Y+'),('y-','Y−'),('z+','Z+'),('z-','Z−')):
            ttk.Button(camera,text=label,width=5,command=lambda value=key:self.preview.align(value)).pack(side='left',padx=2)
        self.preview=PackingPreview(right,on_error=lambda text:self.preview_notice.set('3D 표시 오류 · '+text))
        ttk.Label(right,textvariable=self.preview_notice).pack(fill='x',pady=5)

    def _changed(self,*unused):
        self.revision+=1;self.snapshot=None
        self.export_button.configure(state='disabled');self.chitubox_button.configure(state='disabled')
        if not self.is_busy:self.status_var.set('입력이 변경되었습니다. 배치를 다시 계산하세요.')

    def current_settings(self):
        balance={'사용 안 함':'off','전체 중앙 정렬':'center','중앙 정렬 + 대칭 개선':'symmetric'}.get(self.balance_var.get())
        if balance is None or self.mode_var.get() not in ('메시','경계 상자 (AABB)'):
            raise ValueError('검사 방식과 XY 정렬 설정을 다시 선택하세요.')
        return Settings(size=tuple(getattr(self,name+'_var').get() for name in ('size_x','size_y','size_z')),
            gap=self.gap_var.get(),wall_gap=self.wall_var.get(),time_limit=self.seconds_var.get(),seed=self.seed_var.get(),
            mode='mesh' if self.mode_var.get()=='메시' else 'aabb',learning=self.learning_var.get(),
            precision=self.precision_var.get(),precision_time=self.precision_seconds_var.get(),
            xy_balance=balance,
            xy_balance_time=self.balance_seconds_var.get(),early_stop=self.early_var.get(),
            check_interlocking=self.interlocking_var.get())

    def choose_files(self):
        paths=filedialog.askopenfilenames(parent=self.root,title='모델 추가',filetypes=[('STEP/STL','*.step *.stp *.stl *.STEP *.STP *.STL')])
        self.add_paths(paths)

    def add_paths(self,paths):
        if self.is_busy:return False
        existing={str(Path(row['path']).resolve()) for row in self.rows};added=[]
        for value in paths:
            path=Path(value).expanduser().resolve()
            if path.suffix.lower() not in ('.step','.stp','.stl') or str(path) in existing:continue
            row={'id':uuid.uuid4().hex,'path':str(path),'unit':self.unit_var.get(),'quantity':1,'rotations':list(range(24))}
            self.rows.append(row);added.append(row);existing.add(str(path))
        if added:
            self._changed();self._refresh_table();self._import(added)
        return bool(added)

    def _enable_drop(self):
        try:
            from tkinterdnd2 import DND_FILES
            self.table.drop_target_register(DND_FILES)
            self.table.dnd_bind('<<Drop>>',self._drop)
        except Exception:
            self.drop_hint_var.set('이 환경에서는 파일 추가 버튼을 이용하세요.')

    def _drop(self,event):
        if self.is_busy:return 'refuse_drop'
        return 'copy' if self.add_paths(self.root.tk.splitlist(event.data)) else 'refuse_drop'

    def _refresh_table(self,failures=None):
        failures=dict(failures or []);selected=self.table.selection()
        self.table.delete(*self.table.get_children())
        for row in self.rows:
            model=self.models.get(row['id'])
            state=('진단 미리보기' if model and model.diagnostics.get('inspection_only') else '준비' if model else '파일 다시 읽기')
            if row['id'] in failures:state=failures[row['id']][:70]
            self.table.insert('', 'end',iid=row['id'],values=(Path(row['path']).name,row['quantity'],row['unit'],state))
        for key in selected:
            if self.table.exists(key):self.table.selection_add(key)

    def edit_quantity(self,event):
        if self.is_busy or self.table.identify_column(event.x)!='#2':return
        key=self.table.identify_row(event.y)
        if not key:return
        row=next(item for item in self.rows if item['id']==key)
        bounds=self.table.bbox(key,'quantity')
        editor=ttk.Entry(self.table);editor.insert(0,str(row['quantity']));editor.place(x=bounds[0],y=bounds[1],width=bounds[2],height=bounds[3])
        editor.focus_set();editor.selection_range(0,'end')
        def commit(unused=None):
            if not editor.winfo_exists():return
            try:
                quantity=int(editor.get())
                if not 0<=quantity<=100000:raise ValueError()
                row['quantity']=quantity;self._changed();self._refresh_table()
            except ValueError:self.status_var.set('수량은 0~100000의 정수여야 합니다.')
            editor.destroy()
        editor.bind('<Return>',commit);editor.bind('<FocusOut>',commit)
        editor.bind('<Escape>',lambda unused:editor.destroy())

    def remove_selected(self):
        if self.is_busy:return
        selected=set(self.table.selection());self.rows=[row for row in self.rows if row['id'] not in selected]
        for key in selected:self.models.pop(key,None)
        self._changed();self._refresh_table();self.redraw()

    def reload_inputs(self):
        if self.is_busy:return
        self._changed();self._import(list(self.rows))

    def _import(self,rows):
        if not rows:return
        records=input_records(rows)
        for record in records:self.models.pop(record['id'],None)
        self._refresh_table();self.redraw()
        self._launch('import',lambda cancel,progress:load_inputs(records,cancel,progress))

    def _launch(self,kind,function,settings=None):
        if self.is_busy or self.disposed:return
        self.job_id+=1;identity=(self.job_id,self.revision,kind)
        self._job_identity=identity;self._terminal_received=False
        self._job_success=False
        self.cancel_event=threading.Event();self.task_kind=kind
        self._job_started=time.monotonic();self._progress_received=None;self._progress_data={}
        self._search_allowance=(settings.time_limit + (settings.precision_time if settings.precision else 0.)) if settings else 0.
        self.status_var.set({'optimize':'입력 준비·초기 배치 계산 중…','import':'모델 불러오는 중…','export':'결과 저장 중…'}[kind])
        self._update_progress()
        use_native=self.native_var.get()
        def progress(data):self.events.put(('progress',identity,data))
        def work():
            try:
                from powdernest import native
                with native.backend('auto' if use_native else 'python'):
                    value=function(self.cancel_event,progress)
                self.events.put(('done',identity,value))
            except Exception:self.events.put(('error',identity,traceback.format_exc()))
        self.worker=threading.Thread(target=work,name='bjt-packing-'+kind,daemon=True)
        self._set_busy(True);self.worker.start()

    def _set_busy(self,busy):
        for widget in self.controls:
            widget.configure(state='disabled' if busy else 'readonly' if isinstance(widget,ttk.Combobox) else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')
        valid=not busy and self.snapshot is not None and self.snapshot.result.validation.get('valid')
        self.export_button.configure(state='normal' if valid else 'disabled')
        self.chitubox_button.configure(state='normal' if valid and self.snapshot.result.placements else 'disabled')

    def start(self):
        if self.is_busy:return
        try:settings=self.current_settings();records=input_records(self.rows)
        except Exception as exc:messagebox.showerror('배치 조건',str(exc),parent=self.root);return
        models=dict(self.models);self.snapshot=None
        self._launch('optimize',lambda cancel,progress:calculate(models,records,settings,cancel,progress),settings)

    def stop(self):
        self.cancel_event.set()
        self.status_var.set('저장 정리를 기다리는 중…' if self.task_kind=='export' else '취소 요청 · 최선의 결과 검증/입력 정리 대기…')
        self.stop_button.configure(state='disabled')
        self._update_progress()

    def _update_progress(self,finished=None):
        """A GUI heartbeat keeps elapsed time live between engine callbacks.

        The bounded quantity is search allowance, not a completion-time ETA.
        Preparation, postprocessing and independent verification are separate.
        """
        if self._job_started is None:return
        elapsed=max(0.,time.monotonic()-self._job_started)
        prefix=f'경과 {elapsed:.1f}초'
        mode='indeterminate';value=0
        data=self._progress_data
        if finished is not None:
            mode='determinate';value=100 if finished else 0
            detail='작업 종료' if finished else '작업 중단·오류 또는 입력 변경'
        elif self.cancel_event.is_set():detail='취소 정리·최종 검증 중 · 남은 시간 계산 불가'
        elif self.task_kind!='optimize':detail='모델 읽기 중' if self.task_kind=='import' else '저장 중'
        elif data.get('status')=='validating':detail='최종 검증 중 · 남은 시간 계산 불가'
        elif data.get('status')=='xy_balancing':detail='XY 정렬 중 · 완료 후 최종 검증'
        elif self._progress_received is None:detail='입력 준비·초기 배치 중 · 남은 시간 산정 전'
        else:
            used=max(0.,float(data.get('elapsed',0.))+time.monotonic()-self._progress_received)
            remaining=max(0.,self._search_allowance-used)
            fraction=min(1.,used/self._search_allowance) if self._search_allowance>0 else 1.
            mode='determinate';value=min(99.,fraction*100.)
            detail=(f'탐색 한도 {fraction:.0%} · 남은 탐색 한도 최대 {remaining:.1f}초'
                    if remaining else '탐색 한도 도달 · 현재 연산 마무리 중')
            detail+='\nXY 정렬·최종 검증 시간 별도 · 조기 종료 가능'
        if str(self.progress_bar.cget('mode'))!=mode:
            self.progress_bar.stop();self.progress_bar.configure(mode=mode)
            if mode=='indeterminate':self.progress_bar.start(15)
        if mode=='determinate':self.progress_bar['value']=value
        self.progress_var.set(prefix+' · '+detail)

    def _poll(self):
        if self.disposed:return
        try:
            while True:
                kind,identity,data=self.events.get_nowait()
                belongs=identity==self._job_identity
                if belongs and kind in ('done','error'):
                    self._terminal_received=True
                    self._job_success=kind=='done' and identity[1]==self.revision and not self.cancel_event.is_set()
                if not belongs or identity[1]!=self.revision:continue
                if kind=='progress':
                    if isinstance(data,dict):
                        self._progress_data.update(data);self._progress_received=time.monotonic()
                        phase={'policy':'정책 1차 배치','algorithm':'알고리즘 개선','precision':'정밀 탐색','xy_balance':'XY 정렬'}.get(data.get('stage'),'계산')
                        if data.get('status')=='xy_balancing':phase='XY 정렬'
                        if data.get('status')=='validating':phase='최종 형상·간격·경계 검증'
                        placed=self._progress_data.get('placed',0);requested=self._progress_data.get('requested',0)
                        count=f'{placed}/{requested}개'+(f' ({placed/requested:.0%})' if requested else '')
                        if not self.cancel_event.is_set():self.status_var.set(f"{phase} · {count} · {self._progress_data.get('height',0):.3f} mm")
                    elif not self.cancel_event.is_set():self.status_var.set(str(data))
                elif kind=='error':
                    self.status_var.set('작업 오류 · 입력과 마지막 정책은 유지됩니다.');self._details(data)
                elif identity[2]=='import':
                    loaded,failures,cancelled=data
                    if not cancelled:self.models.update(loaded)
                    self._refresh_table(failures);self.redraw()
                    self.status_var.set('모델 읽기 취소됨' if cancelled else f'모델 읽기 완료 · {len(loaded)}개 · 실패 {len(failures)}개')
                    if failures:self._details('\n'.join(text for key,text in failures))
                elif identity[2]=='optimize':
                    self.snapshot=data;self._show_result()
                else:self.status_var.set('저장 완료 · '+str(data))
        except queue.Empty:pass
        except Exception:
            self.cancel_event.set();self.revision+=1;self.snapshot=None
            self.status_var.set('결과 표시 중 오류 · 입력을 확인하고 다시 실행하세요.')
            self._details(traceback.format_exc())
        if self.worker is not None and not self.worker.is_alive() and self._terminal_received:
            self._update_progress(finished=self._job_success and not self.cancel_event.is_set())
            self.worker=None;self.task_kind=None;self._job_identity=None;self._set_busy(False)
        elif self.worker is not None:self._update_progress()
        if self.closing and not self.is_busy:
            self.root.destroy();return
        self._poll_id=self.root.after(60,self._poll)

    def _details(self,text):
        self.details.configure(state='normal');self.details.delete('1.0','end');self.details.insert('end',text);self.details.configure(state='disabled')

    def _show_result(self):
        from powdernest.io import result_label
        snapshot=self.snapshot;result=snapshot.result;valid=result.validation
        requested=sum(item.quantity for item in snapshot.items);placed=len(result.placements)
        self.status_var.set(f"{result_label(result,valid)} · {placed}/{requested}개 · 높이 {result.height:.3f} mm · {result.elapsed:.2f}초")
        counts=Counter(p.model_id for p in result.placements)
        lines=[f"{snapshot.models[item.model_id].name}: 요청 {item.quantity}, 배치 {counts[item.model_id]}, 미배치 {item.quantity-counts[item.model_id]}" for item in snapshot.items]
        lines.append(f'종료 {result.status} · 시드 {result.seed} · 최적성 미증명')
        learning=result.metadata.get('reinforcement_learning',{})
        lines.append(f"내장 정책 작업 {learning.get('policy_steps',0)}회 · 알고리즘 개선 {learning.get('heuristic_steps',0)}회 · 새 학습 없음" if learning.get('enabled') else '기존 알고리즘 사용'+(' · 내장 정책 범위 밖' if learning.get('fallback') else ''))
        lines.extend(valid.get('errors',[]));self._details('\n'.join(lines));self.redraw()

    def redraw(self):
        if self.disposed:return
        try:self._redraw()
        except Exception as exc:self.preview_notice.set('3D 표시 오류 · '+str(exc))

    def _redraw(self):
        if self.snapshot is not None:
            snapshot=self.snapshot;notice=self.preview.set_scene(snapshot.models,snapshot.result.placements,snapshot.settings.size)
        else:
            try:settings=self.current_settings()
            except (ValueError,tk.TclError):return
            selected=self.table.selection();key=selected[0] if selected else next(iter(self.models),None)
            model=self.models.get(key)
            poses=[] if model is None else [Placement(key,0,model.allowed_rotations[0],tuple(settings.boundary_margins(model.tessellation_error)))]
            notice=self.preview.set_scene({key:model} if model else {},poses,settings.size)
        self.preview_notice.set(notice)

    def export(self,kind):
        if self.is_busy or self.snapshot is None or not self.snapshot.result.validation.get('valid'):return
        if kind=='chitubox':destination=filedialog.asksaveasfilename(parent=self.root,defaultextension='.chitubox',filetypes=[('CHITUBOX','*.chitubox')])
        else:destination=filedialog.askdirectory(parent=self.root,title='결과 저장 폴더')
        if destination:
            snapshot=self.snapshot
            self._launch('export',lambda cancel,progress:save_snapshot(snapshot,destination,kind,progress))

    def inspection_info(self):
        selected=self.table.selection()
        if selected and selected[0] in self.models:
            self._details(json.dumps(self.models[selected[0]].diagnostics,ensure_ascii=False,indent=2,default=str))

    def _refresh_profiles(self):
        self.profile_combo.configure(values=[profile.name for profile in self.profile_library.profiles])
        profile=next((p for p in self.profile_library.profiles if p.profile_id==self.profile_library.active_id),self.profile_library.profiles[0])
        self.profile_var.set(profile.name)

    def apply_profile(self):
        profile=next((p for p in self.profile_library.profiles if p.name==self.profile_var.get()),None)
        if profile is None:return
        self.profile_library.active_id=profile.profile_id
        for name,value in zip(('size_x','size_y','size_z','gap','wall'),(*profile.size,profile.gap,profile.wall_gap)):
            getattr(self,name+'_var').set(value)
        self.redraw()

    def add_profile(self):self._save_profile(False)
    def update_profile(self):self._save_profile(True)

    def _save_profile(self,update):
        if self.is_busy:return
        name=simpledialog.askstring('빌드박스 저장','프로필 이름',parent=self.root,initialvalue=self.profile_var.get() if update else '')
        if not name:return
        try:
            settings=self.current_settings();key=self.profile_library.active_id if update else uuid.uuid4().hex
            profile=BuildProfile(key,name,settings.size,settings.gap,settings.wall_gap)
            profiles=[p for p in self.profile_library.profiles if p.profile_id!=key]+[profile]
            self.profile_library=ProfileLibrary(profiles,key,list(self.profile_library.training_ids));self._refresh_profiles();self._changed()
        except ValueError as exc:messagebox.showerror('프로필',str(exc),parent=self.root)

    def delete_profile(self):
        if self.is_busy or len(self.profile_library.profiles)<=1:return
        key=self.profile_library.active_id;profiles=[p for p in self.profile_library.profiles if p.profile_id!=key]
        self.profile_library=ProfileLibrary(profiles,profiles[0].profile_id,[v for v in self.profile_library.training_ids if v!=key])
        self._refresh_profiles();self.apply_profile()

    def import_profiles(self):
        if self.is_busy:return
        path=filedialog.askopenfilename(parent=self.root,filetypes=[('프로필 JSON','*.json')])
        if path:
            try:self.profile_library=ProfileLibrary.load(path);self._refresh_profiles();self.apply_profile()
            except (OSError,ValueError) as exc:messagebox.showerror('프로필',str(exc),parent=self.root)

    def export_profiles(self):
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension='.json',filetypes=[('프로필 JSON','*.json')])
        if path:
            try:self.profile_library.save(path)
            except (OSError,ValueError) as exc:messagebox.showerror('프로필',str(exc),parent=self.root)

    def input_state(self):
        return {'models':input_records(self.rows),'profiles':self.profile_library.to_dict()}

    def restore_input_state(self,state):
        rows=[]
        for record in state.get('models',[]):
            if not isinstance(record,dict):raise ValueError('모델 목록 형식이 올바르지 않습니다.')
            quantity=record['quantity'];rotations=record['rotations']
            if type(quantity)is not int or not 0<=quantity<=100000:raise ValueError('모델 수량이 올바르지 않습니다.')
            if not rotations or any(type(v)is not int or not 0<=v<24 for v in rotations):raise ValueError('허용 자세가 올바르지 않습니다.')
            if record['unit'] not in ('mm','cm','m','in'):raise ValueError('모델 단위가 올바르지 않습니다.')
            rows.append({'id':str(record['id']),'path':str(record['path']),'unit':record['unit'],'quantity':quantity,'rotations':list(dict.fromkeys(rotations))})
        profiles=state.get('profiles')
        if profiles:
            library=ProfileLibrary([BuildProfile(**p) for p in profiles['profiles']],profiles['active_id'],profiles.get('training_ids',[]))
            self.profile_library=library;self._refresh_profiles()
        self.rows=rows;self.models={};self.snapshot=None;self._refresh_table()
        self.status_var.set(f'저장된 모델 {len(rows)}개 · 파일 다시 읽기를 눌러 불러오세요.');self.redraw()

    def _destroyed(self,event):
        if event.widget is self.root:
            self.disposed=True;self.cancel_event.set()

    def close(self):
        self.closing=True
        if self.is_busy:self.stop()
        else:self.root.destroy()


def main():
    import argparse
    import sys
    parser=argparse.ArgumentParser(description='BJT 모델 자동 배치 · 내장 정책 추론')
    parser.add_argument('--job',help='형상·수량·빌드 조건 JSON')
    parser.add_argument('--output',help='STL·좌표·보고서 폴더')
    parser.add_argument('--chitubox',help='추가 CHITUBOX 저장 경로')
    parser.add_argument('--policy',action=argparse.BooleanOptionalAction,default=True,help='내장 정책 사용 (기본 켬)')
    args=parser.parse_args()
    if args.job:
        if not args.output:parser.error('--job에는 --output이 필요합니다.')
        try:
            from powdernest.io import load_job
            models,items,settings=load_job(args.job);settings.learning=args.policy
            rows=[{'id':item.model_id,'path':models[item.model_id].source_path,'unit':'mm',
                   'quantity':item.quantity,'rotations':list(models[item.model_id].allowed_rotations)} for item in items]
            snapshot=calculate(models,rows,settings,threading.Event())
            save_snapshot(snapshot,args.output)
            if args.chitubox:save_snapshot(snapshot,args.chitubox,'chitubox')
            print(json.dumps({'valid':snapshot.result.validation['valid'],'complete':snapshot.result.validation['complete'],
                'placed':len(snapshot.result.placements),'height_mm':snapshot.result.height,'output':args.output},ensure_ascii=False))
            return 0
        except Exception as exc:
            print('배치 오류: '+str(exc),file=sys.stderr);return 1
    if args.output or args.chitubox:parser.error('--output/--chitubox에는 --job이 필요합니다.')
    from suite_theme import ThemeManager
    try:
        from tkinterdnd2 import TkinterDnD
        root=TkinterDnD.Tk()
    except Exception:root=tk.Tk()
    root.title('BJT 모델 자동 배치');root.geometry('1450x900');ThemeManager(root).apply()
    PackingApp(root);root.mainloop();return 0


if __name__=='__main__':raise SystemExit(main())
