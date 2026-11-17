"""Per-user input persistence for the integrated tools.

Only input variables and input models are stored. Image/mesh data, results,
worker state, log contents and temporary preview caches never enter this file.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import tkinter as tk


SCHEMA_VERSION = 1
MAX_SETTINGS_BYTES = 64 * 1024 * 1024

# These are output/view status variables, rather than user inputs. In
# particular, a restored progress value must never imply that a job has run.
OUTPUT_VARIABLES = {
    'progress_var', 'progress_text_var', 'progress_label_var', 'status_var',
    'status', 'detail_var', 'drop_hint_var', 'spec_var', 'count_var',
    'preview_info_var', 'layer_total_var', 'layer_scrub_info_var',
    'selected_info_var', 'mode_hint', 'input_summary', 'message', 'count_text',
    'preview_notice', 'query_status_var', 'table_status_var', 'query_values',
    'field_slider_var', 'o2_field_slider_var',
    # The metal bottom is sealed; this disabled display is a fixed model
    # constraint, not a user-editable oxygen boundary condition.
    'o2_bottom_access_var',
}


def settings_path() -> Path:
    """Use a writable user profile, never the EXE's installation directory."""
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    return base / 'BJT_Tools' / 'settings.json'


class SettingsStore:
    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path is not None else None
        self.data = {'schema_version': SCHEMA_VERSION, 'tools': {}}
        self.load_error = None
        if self.path is None:
            return
        try:
            if not self.path.exists():
                return
            if self.path.stat().st_size > MAX_SETTINGS_BYTES:
                raise ValueError('설정 파일이 너무 큽니다.')
            data = json.loads(self.path.read_text(encoding='utf-8-sig'))
            if (not isinstance(data, dict) or data.get('schema_version') != SCHEMA_VERSION
                    or not isinstance(data.get('tools'), dict)):
                raise ValueError('지원하지 않는 설정 파일입니다.')
            self.data = data
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            self.load_error = str(exc)

    def tool_state(self, key):
        value = self.data['tools'].get(key, {})
        return value if isinstance(value, dict) else {}

    def set_tool(self, key, state):
        self.data['tools'][key] = state

    def save(self):
        if self.path is None:
            return
        encoded = json.dumps(self.data, ensure_ascii=False, indent=2,
                             allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_SETTINGS_BYTES:
            raise ValueError('입력 설정이 저장 가능한 크기를 초과했습니다.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Preserve a damaged original for recovery instead of silently replacing it.
        if self.load_error and self.path.exists():
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = self.path.with_name(self.path.name + '.corrupt-' + stamp)
            shutil.copy2(self.path, backup)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', prefix='.settings-', suffix='.tmp',
                                             dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.load_error = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


def _variables(controller):
    found = {}

    def visit(value, path, depth=0):
        if isinstance(value, tk.Variable):
            found[path] = value
        elif depth < 3 and isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str) and '.' not in key:
                    visit(item, path + '.' + key, depth + 1)
        elif depth < 3 and isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                visit(item, path + '.' + str(index), depth + 1)

    for name, value in vars(controller).items():
        if name not in OUTPUT_VARIABLES and not (name == 'input_var'
                                                and hasattr(controller, 'input_paths')):
            visit(value, name)
    return found


def _raw(variable):
    # IntVar.get() fails while the user has typed an empty/partial number.
    # Tcl's underlying value preserves that input text without coercion.
    return str(variable._tk.globalgetvar(variable._name))


@contextmanager
def _without_variable_traces(variables):
    """Restore a snapshot as a unit, without presets overwriting custom values."""
    removed = []
    try:
        for variable in {id(item): item for item in variables}.values():
            for modes, callback in variable.trace_info():
                variable._tk.call('trace', 'remove', 'variable', variable._name,
                                  modes, callback)
                removed.append((variable, modes, callback))
        yield
    finally:
        for variable, modes, callback in reversed(removed):
            variable._tk.call('trace', 'add', 'variable', variable._name,
                              modes, callback)


def _numeric_variable_valid(variable, value):
    # Keep invalid numeric text in JSON, but do not put an invalid IntVar or
    # DoubleVar back into startup callbacks that expect a numeric value.
    if isinstance(variable, tk.BooleanVar):
        try:
            variable._tk.getboolean(value)
            return True
        except (ValueError, tk.TclError):
            return False
    if isinstance(variable, (tk.IntVar, tk.DoubleVar)):
        try:
            number = float(value)
            return math.isfinite(number) and (not isinstance(variable, tk.IntVar)
                                              or number.is_integer())
        except (ValueError, TypeError, OverflowError):
            return False
    return True


def _plain(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError('입력 설정에 저장할 수 없는 값이 있습니다.')


def _input_widgets(controller):
    """Entries without a Tk variable are uncommon, but are inputs too."""
    host = getattr(controller, 'root', controller)
    found = {}

    def visit(widget, path):
        for child in widget.winfo_children():
            child_path = path + (child.winfo_name(),)
            if child.winfo_class() in {'Entry', 'TEntry', 'Spinbox', 'TSpinbox', 'TCombobox'}:
                try:
                    if not str(child.cget('textvariable')):
                        found['/'.join(child_path)] = child
                except tk.TclError:
                    pass
            visit(child, child_path)
    if isinstance(host, tk.Misc):
        visit(host, ())
    return found


def capture_inputs(key, controller):
    if key == 'ir_feedback':
        return {'variables': {}, 'inputs': _plain(controller.input_state())}
    variables = {name: _raw(variable) for name, variable in _variables(controller).items()}
    state = {'variables': variables, 'inputs': {}}
    unbound = {name: widget.get() for name, widget in _input_widgets(controller).items()}
    if unbound:
        state['entries'] = unbound
    inputs = state['inputs']
    if key == 'nesting':
        inputs.update(_plain(controller.input_state()))
    elif key in {'chitu_checker', 'm2_converter'}:
        inputs['paths'] = _plain(controller.input_paths)
    elif key == 'overlay':
        inputs['folders'] = _plain(controller.folders)
    elif key == 'alignment':
        inputs['source'] = _plain(controller.source)
    elif key == 'compare':
        inputs['left_paths'] = _plain(controller.left.paths)
        inputs['right_paths'] = _plain(controller.right.paths)
    elif key == 'thermal':
        # Tree cells are kept as text: no profile sorting or validation on exit.
        inputs['oven_profile'] = [list(controller.profile_tree.item(iid, 'values'))
                                  for iid in controller.profile_tree.get_children()]
        inputs['tc_path'] = str(controller.tc_path) if controller.tc_path else None
        if controller.tc_time_min is not None and controller.tc_center_c is not None:
            inputs['tc_data'] = {'time_min': _plain(controller.tc_time_min.tolist()),
                                 'center_c': _plain(controller.tc_center_c.tolist())}
    elif key == 'arrange':
        inputs.update({
            'sources': {name: _plain(asdict(value)) for name, value in controller.sources.items()},
            'source_by_folder': _plain(controller.source_by_folder),
            'placements': {name: _plain(asdict(value)) for name, value in controller.placements.items()},
            'placement_order': list(controller.placement_order),
            'selected_id': controller.selected_id, 'copied_id': controller.copied_id,
            'output_folder_manual': bool(controller.output_folder_manual),
            'current_layer': int(controller.current_layer),
        })
    return state


def _paths(value):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError('저장된 입력 경로 목록이 올바르지 않습니다.')
    return value


def _restore_collections(key, controller, inputs):
    if not isinstance(inputs, dict):
        return
    if key == 'nesting':
        controller.restore_input_state(inputs)
    elif key == 'chitu_checker' and 'paths' in inputs:
        controller.set_inputs(_paths(inputs['paths']))
    elif key == 'm2_converter' and 'paths' in inputs:
        controller.input_paths = _paths(inputs['paths'])
        controller._render_inputs()
    elif key == 'overlay' and 'folders' in inputs:
        controller.folders = [Path(item) for item in _paths(inputs['folders'])]
        controller.refresh_folder_listbox()
    elif key == 'alignment' and 'source' in inputs:
        source = inputs['source']
        if source is not None and not isinstance(source, str):
            source = _paths(source)
        controller.source = source
        if isinstance(source, str):
            controller.input_summary.set('ZIP: ' + source)
        elif source:
            controller.input_summary.set(f'이미지 {len(source)}개 선택 · {Path(source[0]).parent}')
    elif key == 'compare':
        for side in ('left', 'right'):
            name = side + '_paths'
            if name not in inputs:
                continue
            panel = getattr(controller, side)
            panel.paths = _paths(inputs[name])
            if panel.paths:
                first = Path(panel.paths[0])
                panel.caption.configure(text=first.name if len(panel.paths) == 1
                                        else f'이미지 파일 {len(panel.paths):,}개 선택됨')
                panel.description.configure(text=str(first) if len(panel.paths) == 1
                                             else f'{first.name} 외 {len(panel.paths)-1:,}개')
    elif key == 'thermal':
        profile = inputs.get('oven_profile')
        if isinstance(profile, list) and all(isinstance(row, list) and len(row) == 2
                                            and all(isinstance(v, (str, int, float)) for v in row)
                                            for row in profile):
            controller.profile_tree.delete(*controller.profile_tree.get_children())
            for row in profile:
                controller.profile_tree.insert('', 'end', values=row)
        tc_path = inputs.get('tc_path')
        if tc_path is None or isinstance(tc_path, str):
            controller.tc_path = tc_path
        # Measurements are user inputs, not simulation results. Keep the
        # imported numeric data so moving the original CSV does not lose them.
        tc_data = inputs.get('tc_data')
        if isinstance(tc_data, dict):
            module = sys.modules.get(type(controller).__module__)
            numpy = getattr(module, 'np', None)
            if numpy is not None:
                times = numpy.asarray(tc_data.get('time_min'), dtype=float)
                centers = numpy.asarray(tc_data.get('center_c'), dtype=float)
                if (times.ndim == centers.ndim == 1 and times.size == centers.size
                        and numpy.isfinite(times).all() and numpy.isfinite(centers).all()):
                    controller.tc_time_min, controller.tc_center_c = times, centers
                    if times.size and hasattr(controller, 'tc_status'):
                        controller.tc_status.config(text=(f'Loaded {len(times)} points | '
                            f'{times[0]:.1f}–{times[-1]:.1f} min | '
                            f'{Path(tc_path).name if tc_path else ""}'))
                    if times.size and hasattr(controller, 'cal_status'):
                        controller.cal_status.config(text='Thermocouple data loaded. Fit uses the currently entered oven profile.')
    elif key == 'arrange' and 'sources' in inputs:
        module = sys.modules[type(controller).__module__]
        sources = {}
        for name, item in inputs.get('sources', {}).items():
            if not isinstance(name, str) or not isinstance(item, dict):
                continue
            source = dict(item)
            source['folder'] = Path(source['folder'])
            source['images'] = [Path(p) for p in _paths(source['images'])]
            if source.get('original_path'):
                source['original_path'] = Path(source['original_path'])
            source['model_size_mm'] = tuple(source.get('model_size_mm', (0., 0., 0.)))
            sources[name] = module.SourceSet(**source)
        placements = {}
        for name, item in inputs.get('placements', {}).items():
            if isinstance(item, dict) and item.get('source_id') in sources:
                placements[name] = module.Placement(**item)
        controller.sources = sources
        controller.placements = placements
        controller.source_by_folder = {str(k): v for k, v in inputs.get('source_by_folder', {}).items()
                                       if v in sources}
        controller.placement_order = [p for p in inputs.get('placement_order', []) if p in placements]
        controller.selected_id = inputs.get('selected_id') if inputs.get('selected_id') in placements else None
        controller.copied_id = inputs.get('copied_id') if inputs.get('copied_id') in placements else None
        controller.current_layer = max(0, int(inputs.get('current_layer', 0)))
        controller.output_folder_manual = bool(inputs.get('output_folder_manual', False))
        controller.refresh_list()
        controller.update_layer_controls()
        controller.redraw_canvas()


def restore_inputs(key, controller, state):
    """Apply data without launching jobs; return recoverable-field warnings."""
    if not isinstance(state, dict):
        return []
    if key == 'ir_feedback':
        controller.restore_input_state(state.get('inputs', {}))
        return []
    warnings = []
    variables = _variables(controller)
    saved = state.get('variables', {})
    if isinstance(saved, dict):
        with _without_variable_traces(variables.values()):
            for name, value in saved.items():
                variable = variables.get(name)
                if variable is None or not isinstance(value, (str, bool, int, float)):
                    continue
                if not _numeric_variable_valid(variable, value):
                    warnings.append(name + ': 숫자 입력이 올바르지 않아 기본값을 사용했습니다.')
                    continue
                variable.set(value)
    entries = state.get('entries', {})
    if isinstance(entries, dict):
        for name, widget in _input_widgets(controller).items():
            value = entries.get(name)
            if isinstance(value, str):
                try:
                    if widget.winfo_class() == 'TCombobox':
                        widget.set(value)
                    else:
                        widget.delete(0, 'end')
                        widget.insert(0, value)
                except tk.TclError:
                    pass
    try:
        _restore_collections(key, controller, state.get('inputs', {}))
    except (KeyError, ValueError, TypeError, OSError, tk.TclError) as exc:
        warnings.append('입력 목록: ' + str(exc))
    for name in {
        'bjt': ('_mode_changed',),
        'alignment': ('_apply_scan_mode',),
        'm2_converter': ('_update_specs', '_refresh_actions'),
        'chitu_checker': ('_refresh_actions',),
        'thermal': ('_update_porosity_label', '_update_psd_label', '_k_mode_changed',
                    '_apply_language'),
    }.get(key, ()):
        try:
            getattr(controller, name)()
        except (ValueError, TypeError, tk.TclError):
            # A partially entered numeric StringVar is still restored exactly;
            # the normal Run validation will explain it when the user proceeds.
            pass
    return warnings
