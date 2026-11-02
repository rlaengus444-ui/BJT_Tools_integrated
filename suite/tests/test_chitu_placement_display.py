"""Keep strict errors visible alongside separately certified placement."""
import copy
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
sys.path.insert(0, str(PACKAGE / "sources"))
import chitu_checker as app
from chitu_overlap.cli import _print_summary


def report():
    return {"status": "inconclusive", "complete": False, "placement_status": "clear", "placement_complete": True,
            "object_count": 19, "min_gap_mm": 0.0, "elapsed_seconds": 1.2345678,
            "processing_completed": True, "violations": [], "placement_minimum_distance_is_certified": False,
            "objects": [{"index": i, "name": f"cup {i}", "valid": False,
                         "errors": ["Mesh contains degenerate or invalid triangles."],
                         "normalization_error": "Unsafe ambiguous topology",
                         "topology_diagnostics": {"face_counts_complete": True, "edge_counts_complete": True,
                            "degenerate_face_count": 1, "invalid_area_face_count": 0,
                            "nonmanifold_edge_count": 8, "boundary_edge_count": 0}}
                        for i in (7, 13, 15, 17)],
            "pairs": [{"index_a": 7, "index_b": 8, "object_a": "cup 7", "object_b": "valid",
                       "status": "inconclusive", "placement_status": "clear", "distance_mm": None,
                       "placement_distance_lower_bound_mm": 3.4785022735595703}]}


def test_ui_summary_does_not_confuse_placement_with_strict_pass():
    data = report()
    before = copy.deepcopy(data)
    text = app._report_status_text(data)
    assert "배치 분리 확인" in text and "형상 검증 미완료 4개" in text
    assert "PASS" not in text
    assert "1.23초" in text
    assert data == before
    assert "PASS" not in app._pair_label(data["pairs"][0])


def test_all_four_invalid_objects_and_normalization_causes_are_visible():
    data = report()
    controller = SimpleNamespace(status_var=SimpleNamespace(set=Mock()), detail_var=SimpleNamespace(set=Mock()),
                                 progress_bar=Mock(), _populate_pairs=Mock())
    app.ChituCheckerApp._show_report(controller, app.InspectionResult([], data))
    details = controller.detail_var.set.call_args.args[0]
    for index in (7, 13, 15, 17):
        assert f"[{index}] cup {index}" in details
    assert details.count("비다양체 edge 8개") == 4
    assert "자동 수정하지 않았습니다" in details
    assert "형상 검증은 미완료" in details


def test_table_uses_safe_floor_for_bound_and_two_decimals_for_measurement():
    data = report()
    exact = {**data["pairs"][0], "index_a": 0, "status": "clear", "distance_mm": .123456789}
    data["pairs"].append(exact)
    tree = Mock()
    tree.get_children.return_value = []
    controller = SimpleNamespace(tree=tree, last_report=data, _displayed_pairs={},
                                 show_all_var=SimpleNamespace(get=lambda: True))
    app.ChituCheckerApp._populate_pairs(controller)
    rows = [call.kwargs["values"] for call in tree.insert.call_args_list]
    assert rows[0][3] == "≥ 3.47"
    assert rows[1][3] == "0.12"
    assert data["pairs"][0]["placement_distance_lower_bound_mm"] == 3.4785022735595703
    assert data["pairs"][1]["distance_mm"] == .123456789


def test_cli_exposes_independent_clear_and_all_four_strict_errors(capsys):
    _print_summary(report())
    output = capsys.readouterr()
    assert "배치 분리 확인" in output.out and "요구 간격 0.00" in output.out
    assert "형상 검증 미완료: 4개" in output.err
    for index in (7, 13, 15, 17):
        assert f"[{index}] cup {index}" in output.err
    assert output.err.count("비다양체 edge 8개") == 4
    assert "자동 수정하지 않았습니다" in output.err
    assert "합격:" not in output.out


def test_late_cancellation_revokes_placement_completion_and_minimum(monkeypatch):
    event = threading.Event()
    monkeypatch.setattr("chitu_overlap.inputs.load_objects", lambda *a, **kw: ([], "test"))
    def finish(*args, **kwargs):
        result = report()
        result.update(status="clear", complete=True, placement_minimum_distance_mm=1.23456789,
                      placement_minimum_distance_pair=[0, 1], placement_minimum_distance_is_certified=True)
        event.set()
        return result
    monkeypatch.setattr("chitu_overlap.geometry.check_overlaps", finish)
    result = app.inspect_inputs(["unused.chitubox"], cancel_event=event)
    assert result.report["cancelled"] and not result.report["complete"]
    assert result.report["placement_status"] == "unknown" and not result.report["placement_complete"]
    assert not result.report["placement_minimum_distance_is_certified"]
    assert result.report["placement_minimum_distance_mm"] is None


def test_html_export_forwards_cancellation_without_changing_raw_report(monkeypatch, tmp_path):
    event = threading.Event()
    received = []
    def export(objects, data, destination, *, cancel_event=None):
        received.append(cancel_event)
        Path(destination).write_text("<html>cancel-aware fixture</html>")
    monkeypatch.setattr("chitu_overlap.viewer.write_html_report", export)
    data = report()
    data["inputs"] = []
    before = copy.deepcopy(data)
    assert app.write_result(app.InspectionResult([], data), tmp_path / "result.html", kind="html", cancel_event=event)
    assert received == [event]
    assert data == before
