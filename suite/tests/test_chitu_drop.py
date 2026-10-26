"""Explorer/Tcl file-list handling and optional native drop registration."""
from pathlib import Path
import os
import sys
from types import SimpleNamespace
import tkinter as tk
from unittest.mock import Mock

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "sources"))
import chitu_checker as app


def tcl_payload(interpreter, paths):
    # Tcl performs its own quoting, including braces and Windows backslashes.
    return interpreter.call("format", "%s", interpreter.call("list", *map(str, paths)))


def test_explorer_list_keeps_korean_spaces_braces_and_windows_paths():
    interpreter = tk.Tcl()
    names = [
        r"C:\작업 폴더\카메라 모듈.STL",
        r"C:\프로젝트\프레임{수정}[01] $2.chitubox",
        'D:/작업/따옴표 "원본".chitu',
    ]
    assert app.parse_dropped_paths(interpreter, tcl_payload(interpreter, names)) == tuple(names)


@pytest.mark.parametrize("payload", ["{닫히지 않은 경로", "", None])
def test_bad_or_empty_tcl_list_is_rejected(payload):
    with pytest.raises(ValueError, match="끌어놓은"):
        app.parse_dropped_paths(tk.Tcl(), payload)


def test_new_stl_files_append_in_drop_order_without_reading_contents(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ("먼저.STL", "모듈 {수정}[2].stl", "마지막.stl")]
    for path in paths:
        path.write_bytes(b"Not a mesh; drops must not parse or open file contents.")
    monkeypatch.setattr(Path, "open", Mock(side_effect=AssertionError("drop opened file")))
    selected = app.collect_dropped_inputs([str(paths[0])], [str(paths[1]), str(paths[2])])
    assert selected.paths == tuple(map(str, paths))
    assert selected.added_count == 2
    assert not selected.rejected


def test_supported_project_extensions_are_case_insensitive(tmp_path):
    for extension in (".CHITUBOX", ".ChItU"):
        path = tmp_path / ("프로젝트" + extension)
        path.write_bytes(b"no geometry reading")
        selected = app.collect_dropped_inputs([], [str(path)])
        assert selected.paths == (str(path),)
        assert selected.added_count == 1


def test_duplicate_spellings_are_ignored_without_reordering(tmp_path):
    path = tmp_path / "프레임.STL"
    path.touch()
    equivalent = str(tmp_path / "subdir" / ".." / path.name)
    (tmp_path / "subdir").mkdir()
    selected = app.collect_dropped_inputs([str(path)], [str(path), equivalent])
    assert selected.paths == (str(path),)
    assert selected.added_count == 0
    assert selected.duplicate_count == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive filesystem check")
def test_explorer_deduplicates_windows_case_aliases(tmp_path):
    path = tmp_path / "MixedCase.STL"
    path.touch()
    selected = app.collect_dropped_inputs([str(path)], [str(path).swapcase()])
    assert selected.paths == (str(path),)
    assert selected.duplicate_count == 1


def test_rejected_folder_missing_file_and_unsupported_extension_are_reported(tmp_path):
    bad = tmp_path / "sliced.ctb"
    bad.touch()
    selected = app.collect_dropped_inputs([], [str(tmp_path), str(tmp_path / "missing.stl"), str(bad)])
    assert selected.paths == ()
    assert selected.added_count == 0
    assert len(selected.rejected) == 3
    assert "폴더" in selected.rejected[0]
    assert "지원하지 않는" in selected.rejected[2]


def test_valid_files_still_append_when_other_dropped_items_are_rejected(tmp_path):
    good, bad = tmp_path / "모듈.stl", tmp_path / "이미지.png"
    good.touch()
    bad.touch()
    selected = app.collect_dropped_inputs([], [str(bad), str(good)])
    assert selected.paths == (str(good),)
    assert selected.added_count == 1
    assert len(selected.rejected) == 1


@pytest.mark.parametrize("suffixes", [(".chitu", ".stl"), (".chitubox", ".chitu")])
def test_unsupported_project_combinations_preserve_the_existing_selection(tmp_path, suffixes):
    paths = [tmp_path / (str(index) + suffix) for index, suffix in enumerate(suffixes)]
    for path in paths:
        path.touch()
    existing = [str(paths[0])]
    with pytest.raises(ValueError, match="프로젝트"):
        app.collect_dropped_inputs(existing, [str(paths[1])])
    assert existing == [str(paths[0])]


def test_invalid_path_does_not_escape_drop_validation(tmp_path):
    selected = app.collect_dropped_inputs([], ["\0bad.stl", None])
    assert selected.paths == ()
    assert len(selected.rejected) == 2


class FakeDropTarget:
    def __init__(self, fail=False):
        self.fail = fail
        self.registered = False
        self.binding = None

    def drop_target_register(self, kind):
        if self.fail:
            raise tk.TclError("Unavailable native target")
        assert kind == "DND_Files"
        self.registered = True

    def dnd_bind(self, sequence, function):
        assert sequence == "<<Drop>>"
        self.binding = function

    def drop_target_unregister(self):
        self.registered = False


def fake_controller(targets, *, available=True):
    root = SimpleNamespace(winfo_toplevel=lambda: SimpleNamespace(_suite_dnd_available=available))
    return SimpleNamespace(root=root, drop_targets=targets, drop_hint_var=SimpleNamespace(set=Mock()),
                           _drop_files=Mock(), _dnd_available=False)


def test_native_drop_registers_all_visible_targets(monkeypatch):
    monkeypatch.setitem(sys.modules, "tkinterdnd2", SimpleNamespace(DND_FILES="DND_Files"))
    targets = (FakeDropTarget(), FakeDropTarget(), FakeDropTarget())
    controller = fake_controller(targets)
    app.ChituCheckerApp._enable_drag_drop(controller)
    assert controller._dnd_available
    assert all(target.registered and target.binding is controller._drop_files for target in targets)


def test_partial_registration_failure_rolls_back_and_explains_file_picker(monkeypatch):
    monkeypatch.setitem(sys.modules, "tkinterdnd2", SimpleNamespace(DND_FILES="DND_Files"))
    targets = (FakeDropTarget(), FakeDropTarget(fail=True))
    controller = fake_controller(targets)
    app.ChituCheckerApp._enable_drag_drop(controller)
    assert not controller._dnd_available
    assert not targets[0].registered
    assert "파일 선택" in controller.drop_hint_var.set.call_args.args[0]


def test_unavailable_integrated_root_preserves_file_picker_fallback():
    target = FakeDropTarget()
    controller = fake_controller((target,), available=False)
    app.ChituCheckerApp._enable_drag_drop(controller)
    assert not controller._dnd_available
    assert not target.registered
    assert "사용할 수 없습니다" in controller.drop_hint_var.set.call_args.args[0]


@pytest.fixture
def gui():
    try:
        root = app.create_root()
    except tk.TclError:
        pytest.skip("Tk display is unavailable")
    root.withdraw()
    controller = app.ChituCheckerApp(root)
    try:
        yield root, controller
    finally:
        controller.running = False
        controller.close()


def test_gui_drop_appends_dedupes_and_invalidates_old_results(gui, tmp_path):
    root, controller = gui
    paths = [tmp_path / name for name in ("원래 파일.stl", "새 카메라 {8t}.STL")]
    for path in paths:
        path.touch()
    controller.set_inputs([str(paths[0])])
    revision = controller._revision
    controller.last_report = {"previous": True}
    action = controller._drop_files(SimpleNamespace(data=tcl_payload(root, [paths[0], paths[1]])))
    assert action == "copy"
    assert controller.input_paths == list(map(str, paths))
    assert controller.last_report is None
    assert controller._revision == revision + 1
    assert str(paths[1]) in controller.input_var.get()
    revision = controller._revision
    assert controller._drop_files(SimpleNamespace(data=tcl_payload(root, paths))) == "copy"
    assert controller._revision == revision


def test_gui_busy_drop_keeps_the_inspection_snapshot(gui, tmp_path):
    root, controller = gui
    path = tmp_path / "검사 중.stl"
    path.touch()
    controller.running = True
    controller._refresh_actions()
    revision = controller._revision
    action = controller._drop_files(SimpleNamespace(data=tcl_payload(root, [path])))
    assert action == "refuse_drop"
    assert controller.input_paths == []
    assert controller._revision == revision
    assert "검사 중에는" in controller.drop_hint_var.get()
    controller.running = False
    controller._refresh_actions()
    if controller._dnd_available:
        assert "검사 중에는" not in controller.drop_hint_var.get()


def test_gui_native_tcl_drop_binding_delivers_unicode_file_list(gui, tmp_path):
    root, controller = gui
    if not controller._dnd_available:
        pytest.skip("Native TkDND is unavailable")
    target = controller.drop_targets[0]
    script = target.dnd_bind("<<Drop>>")
    assert script
    command = root.tk.splitlist(script)[0]
    source = tmp_path / "카메라 모듈 {기본}[8t].chitubox"
    source.write_bytes(b"A drop must not parse the actual project.")
    payload = tcl_payload(root, [source])
    # Invoke the registered native Tcl callback using its actual substitution
    # contract. This tests Tcl -> DndEvent -> handler, not a physical OS drag.
    values = {"%D": payload, "%W": str(target), "%A": "copy", "%a": "copy",
              "%ST": "DND_Files", "%TT": "DND_Files", "%T": "DND_Files"}
    args = [values.get(token, "0") for token in target._subst_format_dnd]
    assert root.tk.call(command, *args) == "copy"
    assert controller.input_paths == [str(source)]


def test_gui_rejected_drop_does_not_clear_the_existing_input(gui, tmp_path):
    root, controller = gui
    source, unsupported = tmp_path / "기본.chitu", tmp_path / "슬라이스.ctb"
    source.touch()
    unsupported.touch()
    controller.set_inputs([str(source)])
    revision = controller._revision
    assert controller._drop_files(SimpleNamespace(data=tcl_payload(root, [unsupported]))) == "refuse_drop"
    assert controller.input_paths == [str(source)]
    assert controller._revision == revision
    assert "지원하지 않는" in controller.drop_hint_var.get()
