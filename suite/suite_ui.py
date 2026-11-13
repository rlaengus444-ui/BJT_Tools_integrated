"""Complete tool interfaces hosted in one Tk interpreter/window."""
from __future__ import annotations

import json
import functools
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from tkinter import messagebox, ttk

from suite_loader import BASE_DIR, ToolLoader, read_manifest
from suite_theme import ThemeManager, COLORS
from suite_layout import adapt_tool_layout
from suite_state import SettingsStore, capture_inputs, restore_inputs, settings_path as default_settings_path


class ScopedBindings:
    def __init__(self, suite):
        self.suite = suite
        self.handlers = {}
        self.installed = set()
        self.serial = 0
        self.original_bind = tk.Misc.bind_all
        self.original_unbind = tk.Misc.unbind_all
        manager = self

        def bind_all(widget, sequence=None, func=None, add=None):
            owner = manager.owner(widget)
            if owner is None:
                return manager.original_bind(widget, sequence, func, add)
            return manager.bind(owner, sequence, func, add)

        def unbind_all(widget, sequence):
            owner = manager.owner(widget)
            if owner is None:
                return manager.original_unbind(widget, sequence)
            manager.handlers.pop((owner.tool_key, sequence), None)

        tk.Misc.bind_all = bind_all
        tk.Misc.unbind_all = unbind_all
        self.patched_bind = bind_all
        self.patched_unbind = unbind_all

    @staticmethod
    def owner(widget):
        while widget is not None:
            if isinstance(widget, ToolRoot):
                return widget
            widget = getattr(widget, 'master', None)
        return None

    def bind(self, owner, sequence, func, add=None):
        if sequence is None or func is None:
            return ''
        key = (owner.tool_key, sequence)
        if sequence not in self.installed:
            def dispatch(event, seq=sequence):
                return self.dispatch(seq, event)
            self.original_bind(self.suite.root, sequence, dispatch, '+')
            self.installed.add(sequence)
        if not add:
            self.handlers[key] = []
        self.serial += 1
        token = 'suite_binding_' + str(self.serial)
        self.handlers.setdefault(key, []).append((token, func))
        return token

    def dispatch(self, sequence, event):
        active = self.suite.active_tool()
        event_owner = self.owner(event.widget)
        if event_owner is None or event_owner.tool_key != active:
            return None
        for _, callback in list(self.handlers.get((active, sequence), [])):
            if callback(event) == 'break':
                return 'break'
        return None

    def restore(self):
        if tk.Misc.bind_all is self.patched_bind:
            tk.Misc.bind_all = self.original_bind
            tk.Misc.unbind_all = self.original_unbind


class ToolRoot(tk.Frame):
    """The original root-window APIs, confined to a tool's notebook page."""
    def __init__(self, master, suite, key):
        self.suite = suite
        self.tool_key = key
        self.close_callback = None
        self._tool_title = ''
        self._tool_geometry = ''
        self._after_ids = set()
        self._disposed = False
        super().__init__(master, borderwidth=0, highlightthickness=0)

    def title(self, value=None):
        if value is not None:
            self._tool_title = value
        return self._tool_title

    wm_title = title

    def geometry(self, value=None):
        if value is not None:
            self._tool_geometry = value
        return self._tool_geometry

    wm_geometry = geometry

    def minsize(self, width=None, height=None):
        if width is not None:
            self._tool_minimum = (width, height)
        return getattr(self, '_tool_minimum', (0, 0))

    wm_minsize = minsize

    def resizable(self, width=None, height=None):
        return (True, True)

    wm_resizable = resizable

    def protocol(self, name=None, func=None):
        if name == 'WM_DELETE_WINDOW' and func is not None:
            self.close_callback = func
        return self.close_callback if name == 'WM_DELETE_WINDOW' else ''

    wm_protocol = protocol

    def after(self, ms, func=None, *args):
        if func is None:
            return super().after(ms)
        holder = {'done': False}
        def invoke():
            holder['done'] = True
            if 'id' in holder:
                self._after_ids.discard(holder['id'])
            if not self._disposed:
                return func(*args)
        identifier = super().after(ms, invoke)
        holder['id'] = identifier
        if not holder['done']:
            self._after_ids.add(identifier)
        return identifier

    def after_cancel(self, identifier):
        self._after_ids.discard(identifier)
        return super().after_cancel(identifier)

    def destroy(self):
        if self._disposed:
            return
        self._disposed = True
        for identifier in list(self._after_ids):
            try:
                super().after_cancel(identifier)
            except tk.TclError:
                pass
        self._after_ids.clear()
        super().destroy()


class SuiteApp:
    def __init__(self, root, *, test_mode=False, settings_path=None):
        self.root = root
        self.test_mode = test_mode
        path = settings_path if settings_path is not None else (None if test_mode else default_settings_path())
        self.settings_store = SettingsStore(path)
        self._settings_closed_saved = False
        self.settings_warnings = []
        manifest = read_manifest()
        self.version = manifest['suite_version']
        self.specs = {item['id']: item for item in manifest['tools']}
        self.theme = ThemeManager(root)
        self.theme.apply()
        self.pages = {}
        self.tool_contents = {}
        self.manual_buttons = {}
        self.manual_windows = {}
        self.hosts = {}
        self.controllers = {}
        self.loading = set()
        self.failures = {}
        self.events = queue.Queue()
        self.closing = False
        self.close_errors = []
        self._poll_id = None
        self.loader = ToolLoader(self._base_factory)
        root.title('BJT 통합도구 ' + self.version)
        width = min(1740, max(1100, root.winfo_screenwidth() - 80))
        height = min(1020, max(760, root.winfo_screenheight() - 100))
        root.geometry(f'{width}x{height}')
        root.minsize(min(1120, width), min(780, height))
        root.protocol('WM_DELETE_WINDOW', self.request_close)
        root.report_callback_exception = self._callback_error
        self.bindings = ScopedBindings(self)
        self._build()
        if self.settings_store.load_error:
            self.status.set('저장된 설정을 읽지 못해 기본값으로 시작했습니다.')
            self._write_error('Input settings load: ' + self.settings_store.load_error)
        root.bind('<F1>', self._manual_key, add='+')
        self._poll_id = root.after(75, self._poll)

    def _build(self):
        top = ttk.Frame(self.root, style='Suite.Header.TFrame', padding=(20, 12))
        top.pack(fill='x')
        ttk.Label(top, text='BJT 통합도구', style='Suite.Brand.TLabel').pack(side='left')
        ttk.Label(top, text=self.version,
                  style='Suite.HeaderMeta.TLabel').pack(side='left', padx=(18, 0))
        ttk.Button(top, text='사용 안내  F1', style='Suite.Header.TButton',
                   command=self.show_help).pack(side='right')
        self.tool_selector = ttk.Combobox(
            top, state='readonly', width=22,
            values=['시작', *(spec['label'] for spec in self.specs.values())])
        self.tool_selector.current(0)
        self.tool_selector.pack(side='right', padx=8)
        self.tool_selector.bind('<<ComboboxSelected>>', self._select_from_menu)
        self.stop_button = ttk.Button(top, text='현재 작업 중단',
                                      style='Suite.Header.TButton', command=self.stop_current)
        self.stop_button.pack(side='right', padx=8)
        self.notebook = ttk.Notebook(self.root, style='Suite.Nav.TNotebook')
        self.notebook.pack(fill='both', expand=True, padx=16, pady=(12, 0))
        self.home = ttk.Frame(self.notebook, padding=(20, 18), style='Suite.Tool.TFrame')
        self.notebook.add(self.home, text='시작')
        columns = 3 if len(self.specs) > 8 else 2
        hero = ttk.Frame(self.home, style='Suite.Hero.TFrame', padding=(20, 14))
        hero.grid(row=0, column=0, columnspan=columns, sticky='ew', pady=(0, 16))
        ttk.Label(hero, text='도구 선택',
                  style='Suite.HeroTitle.TLabel').pack(anchor='w')
        for index, (key, spec) in enumerate(self.specs.items()):
            card = ttk.Frame(self.home, style='Suite.Card.TFrame', padding=(16, 12))
            column = index % columns
            card.grid(row=1 + index // columns, column=column, sticky='nsew',
                      padx=(0 if column == 0 else 7, 0 if column == columns - 1 else 7), pady=(0, 12))
            ttk.Label(card, text=f'{index + 1:02d}   {spec["label"]}',
                      style='Suite.CardTitle.TLabel').pack(anchor='w')
            ttk.Label(card, text=spec['description'], style='Suite.CardText.TLabel',
                      wraplength=270 if columns == 3 else 470).pack(anchor='w', pady=(5, 10))
            actions = ttk.Frame(card, style='Suite.Card.TFrame')
            actions.pack(fill='x')
            ttk.Button(actions, text='도구 열기  →', style='Suite.Primary.TButton',
                       command=lambda name=key: self.select_tool(name)).pack(side='left')
            ttk.Button(actions, text='사용설명서', style='Suite.Secondary.TButton',
                       command=lambda name=key: self.open_manual(name)).pack(side='left', padx=(8, 0))
            page = ttk.Frame(self.notebook, style='Suite.Tool.TFrame')
            self.pages[key] = page
            self.notebook.add(page, text=spec['label'])
            toolbar = ttk.Frame(page, padding=(16, 12), style='Suite.Tool.TFrame')
            toolbar.pack(fill='x')
            ttk.Label(toolbar, text=spec['label'], style='Suite.Title.TLabel').pack(side='left')
            button = ttk.Button(toolbar, text='사용설명서  F1', style='Suite.Secondary.TButton',
                                command=lambda name=key: self.open_manual(name))
            button.pack(side='right')
            self.manual_buttons[key] = button
            ttk.Label(toolbar, text=spec['description'], style='Suite.Muted.TLabel').pack(side='left', padx=18)
            ttk.Separator(page, orient='horizontal').pack(fill='x')
            content = ttk.Frame(page, style='Suite.Tool.TFrame', padding=(8, 4))
            content.pack(fill='both', expand=True)
            self.tool_contents[key] = content
            ttk.Label(content, text=spec['description'], padding=24).pack(anchor='w')
        for column in range(columns):
            self.home.columnconfigure(column, weight=1, uniform='tool-cards')
        self.status = tk.StringVar(value='도구를 선택하세요.')
        ttk.Label(self.root, textvariable=self.status, style='Suite.Status.TLabel',
                  padding=(20, 8)).pack(side='bottom', fill='x', before=self.notebook)
        self.notebook.bind('<<NotebookTabChanged>>', self._tab_changed)

    def active_tool(self):
        current = self.notebook.select()
        return next((key for key, page in self.pages.items() if str(page) == current), None)

    def select_tool(self, key):
        self.notebook.select(self.pages[key])

    def _select_from_menu(self, _event=None):
        index = self.tool_selector.current()
        if index == 0:
            self.notebook.select(self.home)
        elif 0 < index <= len(self.specs):
            self.select_tool(list(self.specs)[index - 1])

    def _tab_changed(self, _event=None):
        key = self.active_tool()
        self.tool_selector.current(0 if key is None else list(self.specs).index(key) + 1)
        if key is None:
            self.stop_button.configure(state='disabled')
            return
        if key not in self.controllers and key not in self.loading and not self.closing:
            self.load_tool(key)
        else:
            self.status.set(self.specs[key]['label'] + (' · 준비 중' if key in self.loading else ''))
            if key == 'compare' and key in self.controllers:
                # Another original tool may have selected its own Tk theme.
                # Refresh only this tab's namespaced styles on return.
                self.controllers[key]._style()
        self.theme.apply()
        if key in self.controllers:
            self.theme.style_tool(self.hosts[key], key, self.controllers[key])
        self.stop_button.configure(state='disabled' if key == 'exchanger' else 'normal')

    def _base_factory(self, key):
        page, suite = self.tool_contents[key], self
        class EmbeddedRoot(ToolRoot):
            def __init__(self):
                super().__init__(page, suite, key)
        return EmbeddedRoot

    def load_tool(self, key):
        self.loading.add(key)
        self.status.set(self.specs[key]['label'] + ' · 준비 중입니다. 처음 열 때는 잠시 걸릴 수 있습니다.')
        def load():
            try:
                self.events.put(('loaded', key, self.loader.load(key)))
            except BaseException as exc:
                self.events.put(('error', key, (str(exc), traceback.format_exc())))
        threading.Thread(target=load, name='bjt-suite-load-' + key, daemon=True).start()

    def mount_tool(self, key, module):
        page = self.tool_contents[key]
        for child in page.winfo_children():
            child.destroy()
        self._prepare_module(key, module)
        self._configure_tool_persistence(key, module)
        constructor = getattr(module, self.specs[key]['controller'])
        if self.specs[key]['embedded_base']:
            controller = constructor()
            host = controller
        else:
            host = ToolRoot(page, self, key)
            controller = constructor(host)
        host.pack(fill='both', expand=True)
        self.hosts[key] = host
        self.controllers[key] = controller
        controller._suite_settings_owner = self
        self.settings_warnings.extend(restore_inputs(key, controller, self.settings_store.tool_state(key)))
        adapt_tool_layout(host, key, controller)
        self.theme.apply()
        self.theme.style_tool(host, key, controller)
        if key == 'compare':
            # Its original banner is decorative; the shared toolbar names
            # every tool in the same place and keeps more room for results.
            children = host.winfo_children()
            if children and isinstance(children[0], tk.Frame):
                labels = children[0].winfo_children()
                if len(labels) == 3 and all(isinstance(item, tk.Label) for item in labels):
                    children[0].pack_forget()
        self.loading.discard(key)
        self.failures.pop(key, None)
        self.status.set(self.specs[key]['label'] + ' · 준비되었습니다.')
        return controller

    def _prepare_module(self, key, module):
        if key == 'compare':
            module.BG = COLORS['background']
            module.INK = COLORS['ink']
            module.BLUE = COLORS['primary']
            module.MUTED = COLORS['muted']
        if not getattr(self.root, '_suite_dnd_available', True):
            # Preserve the original optional-DnD fallback for all other input
            # methods when a platform's tkdnd native library cannot load.
            if hasattr(module, 'DND_FILES'):
                module.DND_FILES = None
            if hasattr(module, 'TkinterDnD'):
                module.TkinterDnD = None
            if hasattr(module, 'DND_AVAILABLE'):
                module.DND_AVAILABLE = False
        if key == 'arrange':
            original = module.SliceLayoutApp._export_worker
            if not getattr(original, '_suite_tracked', False):
                @functools.wraps(original)
                def tracked(controller, *args, **kwargs):
                    controller._suite_export_worker = threading.current_thread()
                    try:
                        return original(controller, *args, **kwargs)
                    finally:
                        controller._suite_export_worker = None
                tracked._suite_tracked = True
                module.SliceLayoutApp._export_worker = tracked

    def _configure_tool_persistence(self, key, module):
        """Standalone modules keep their original behavior; integrated modules
        share the suite's per-user store, including their Save/Load buttons.
        Central state is applied after construction; legacy values are read
        once for tools that have no central state yet.
        """
        if key not in ('bjt', 'arrange', 'thermal'):
            return
        constructor = getattr(module, self.specs[key]['controller'])
        load_name = '_load_saved_settings' if key == 'thermal' else '_load_settings'
        if not hasattr(constructor, '_suite_legacy_input_load'):
            constructor._suite_legacy_input_load = getattr(constructor, load_name)
        legacy_load = constructor._suite_legacy_input_load
        suite = self

        def load(controller, silent=True):
            owner = getattr(controller, '_suite_settings_owner', None)
            if owner is None:
                # Carry forward existing 1.6.x inputs on the first integrated
                # launch. Never read a real user profile in test_mode, and never
                # let an older per-tool file replace established suite values.
                store = suite.settings_store
                if (not suite.test_mode and store.path is not None
                        and store.load_error is None and key not in store.data['tools']):
                    return legacy_load(controller, silent=silent) if key == 'thermal' else legacy_load(controller)
                return {} if key == 'arrange' else False
            state = owner.settings_store.tool_state(key)
            owner.settings_warnings.extend(restore_inputs(key, controller, state))
            if not silent and not owner.test_mode:
                messagebox.showinfo('입력 불러오기', '저장된 입력을 불러왔습니다.' if state
                                    else '저장된 입력이 없습니다.', parent=owner.root)
            return bool(state)

        def save(controller, silent=True):
            owner = getattr(controller, '_suite_settings_owner', None)
            if owner is None or owner.closing:
                return True
            return owner._save_controller_settings(key, controller, silent=silent)

        if key == 'thermal':
            constructor._load_saved_settings = load
        else:
            constructor._load_settings = load
        constructor._save_settings = save
        if key == 'arrange':
            # The suite writes on orderly exit; no extra per-tool home file or
            # background debounce write is needed on every numeric keystroke.
            constructor._schedule_settings_save = lambda controller, *args: None

    def _isolate_test_settings(self, key, module):
        # Retained for existing external validation scripts. test_mode uses an
        # in-memory store unless a dedicated settings_path was supplied.
        self._configure_tool_persistence(key, module)

    def _save_controller_settings(self, key, controller, *, silent=True):
        try:
            self.settings_store.set_tool(key, capture_inputs(key, controller))
            self.settings_store.save()
            if not silent and not self.test_mode:
                messagebox.showinfo('입력 저장', '현재 입력을 저장했습니다.', parent=self.root)
            return True
        except (OSError, ValueError, TypeError, tk.TclError) as exc:
            self._write_error(traceback.format_exc())
            if not silent and not self.test_mode:
                messagebox.showwarning('입력 저장 실패', '입력값을 저장하지 못했습니다.\n' + str(exc),
                                       parent=self.root)
            return False

    def _persist_before_close(self):
        if self._settings_closed_saved:
            return
        self._settings_closed_saved = True
        errors = []
        # Capture before any close callback can destroy widgets. Keep saved
        # states for lazy tabs that were never opened in this session.
        for key, controller in self.controllers.items():
            try:
                self.settings_store.set_tool(key, capture_inputs(key, controller))
            except (OSError, ValueError, TypeError, tk.TclError) as exc:
                errors.append(self.specs[key]['label'] + ': ' + str(exc))
                self._write_error(traceback.format_exc())
        try:
            self.settings_store.save()
        except (OSError, ValueError, TypeError) as exc:
            errors.append(str(exc))
            self._write_error(traceback.format_exc())
        if errors:
            self.close_errors.extend(errors)
            if not self.test_mode:
                messagebox.showwarning('입력 저장 실패', '일부 입력값을 저장하지 못했습니다.\n'
                                       + '\n'.join(errors), parent=self.root)

    def _poll(self):
        self._poll_id = None
        try:
            while True:
                kind, key, payload = self.events.get_nowait()
                if kind == 'loaded':
                    try:
                        self.mount_tool(key, payload)
                    except BaseException as exc:
                        self._load_error(key, str(exc), traceback.format_exc())
                else:
                    self._load_error(key, *payload)
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self._poll_id = self.root.after(75, self._poll)

    def _load_error(self, key, message, trace):
        self.loading.discard(key)
        self.failures[key] = message
        self._write_error(trace)
        page = self.tool_contents[key]
        for child in page.winfo_children():
            child.destroy()
        ttk.Label(page, text='도구를 열지 못했습니다.\n' + message, padding=24, wraplength=1000).pack(anchor='w')
        ttk.Button(page, text='다시 열기', command=lambda: self.load_tool(key)).pack(anchor='w', padx=24)
        self.status.set(self.specs[key]['label'] + ' · 오류 기록을 확인하세요.')

    @staticmethod
    def busy(key, controller):
        if key == 'nesting':
            return controller.is_busy
        for name in ('worker', 'worker_thread', '_suite_export_worker'):
            worker = getattr(controller, name, None)
            if isinstance(worker, threading.Thread) and worker.is_alive():
                return True
        if key == 'compare':
            preview = getattr(controller, 'preview_future', None)
            return preview is not None and preview.running()
        if key == 'exchanger':
            return bool(getattr(controller, 'is_running', False))
        if key == 'arrange':
            if getattr(controller, 'stl_import_active', False):
                return True
            button = getattr(controller, 'export_button', None)
            try:
                return button is not None and str(button.cget('state')) == 'disabled'
            except tk.TclError:
                return False
        return False

    @staticmethod
    def signal_stop(controller):
        stopped = False
        for name in ('cancel_event', 'export_cancel', 'stop_event'):
            event = getattr(controller, name, None)
            if isinstance(event, threading.Event):
                event.set()
                stopped = True
        return stopped

    def stop_current(self):
        key = self.active_tool()
        controller = self.controllers.get(key)
        if controller is not None and self.signal_stop(controller):
            self.status.set(self.specs[key]['label'] + ' · 중단을 요청했습니다. 현재 처리와 정리가 끝날 때까지 기다립니다.')

    def request_close(self, *, confirm=True):
        if self.closing:
            return
        active = [self.specs[key]['label'] for key, controller in self.controllers.items() if self.busy(key, controller)]
        if active and confirm and not messagebox.askyesno('작업 중',
                '진행 중인 작업에 중단을 요청하고 정리 후 종료할까요?\n\n' + '\n'.join(active) +
                '\n\n이미지 교체·복구 또는 STL 입력 변환은 완료될 때까지 기다립니다.', parent=self.root):
            return
        self.closing = True
        self.stop_button.configure(state='disabled')
        for controller in self.controllers.values():
            self.signal_stop(controller)
        self._finish_close()

    def _finish_close(self):
        if self.loading or any(self.busy(key, item) for key, item in self.controllers.items()):
            self.status.set('작업 중단·파일 정리를 기다리고 있습니다. 완료되면 자동으로 종료합니다.')
            self.root.after(100, self._finish_close)
            return
        self._persist_before_close()
        for key, host in list(self.hosts.items()):
            if host._disposed:
                continue
            try:
                if host.close_callback is not None:
                    host.close_callback()
                else:
                    host.destroy()
            except BaseException:
                self.close_errors.append(traceback.format_exc())
                self._write_error(self.close_errors[-1])
        self.bindings.restore()
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        self.root.destroy()

    @staticmethod
    def _write_error(trace):
        try:
            directory = BASE_DIR / 'logs'
            directory.mkdir(exist_ok=True)
            with (directory / 'errors.log').open('a', encoding='utf-8') as stream:
                stream.write(trace + '\n')
        except OSError:
            pass

    def _callback_error(self, exc_type, exc, tb):
        trace = ''.join(traceback.format_exception(exc_type, exc, tb))
        self._write_error(trace)
        self.status.set('작업 중 오류가 발생했습니다. logs/errors.log에서 자세한 내용을 확인할 수 있습니다.')
        if not self.test_mode:
            messagebox.showerror('BJT 통합 작업 도구', str(exc), parent=self.root)

    def show_help(self):
        window = tk.Toplevel(self.root)
        window.title('BJT 통합 작업 도구 · 사용 안내')
        window.geometry('900x650')
        manuals = ttk.LabelFrame(window, text='도구별 사용설명서', padding=12)
        manuals.pack(fill='x', padx=14, pady=12)
        for index, (key, spec) in enumerate(self.specs.items()):
            ttk.Button(manuals, text=spec['label'], command=lambda name=key: self.open_manual(name)).grid(
                row=index // 3, column=index % 3, sticky='ew', padx=5, pady=5)
        for column in range(3):
            manuals.columnconfigure(column, weight=1)
        text = tk.Text(window, wrap='word', padx=18, pady=18, font=('맑은 고딕', 10))
        text.pack(fill='both', expand=True)
        text.insert('1.0', '각 탭에서 원래 도구의 전체 화면을 사용할 수 있습니다.\n\n' +
                    '\n\n'.join(spec['label'] + '\n' + spec['description'] for spec in self.specs.values()) +
                    '\n\n입력값은 종료할 때 저장되며 다음 실행에서 복원됩니다.\n'
                    '저장 위치: ' + str(self.settings_store.path or '시험용 메모리 저장소') + '\n' +
                    '여러 탭에서 동시에 작업할 때에는 출력 폴더가 겹치지 않도록 선택하세요.\n'
                    '종료 시 진행 중인 작업에 중단을 요청하고 파일 정리가 끝날 때까지 기다립니다.')
        text.configure(state='disabled')
        self.theme.style_window(window)

    def open_manual(self, key):
        from suite_manuals import ManualWindow
        cached = self.manual_windows.get(key)
        if cached is not None:
            try:
                if cached.winfo_exists():
                    cached.deiconify()
                    cached.lift()
                    return cached
            except tk.TclError:
                pass
        window = ManualWindow(self.root, key)
        self.theme.style_window(window)
        self.manual_windows[key] = window
        return window

    def _manual_key(self, _event=None):
        key = self.active_tool()
        if key is None:
            self.show_help()
        else:
            self.open_manual(key)
        return 'break'


def create_root():
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        root._suite_dnd_available = True
    except Exception as exc:
        partial = getattr(tk, '_default_root', None)
        if partial is not None:
            try:
                partial.destroy()
            except tk.TclError:
                pass
        root = tk.Tk()
        root._suite_dnd_available = False
        root._suite_dnd_error = str(exc)
    return root


def main():
    root = create_root()
    SuiteApp(root)
    root.mainloop()
