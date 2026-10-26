import json
from unittest import mock

import pytest
import trimesh

from chitu_overlap.cli import main


def test_allocation_failure_cannot_be_reported_as_pass(tmp_path):
    output = tmp_path / "memory-error.json"
    with mock.patch("chitu_overlap.cli.load_objects", side_effect=MemoryError):
        assert main([str(tmp_path / "input.chitubox"), "--json", str(output)]) == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "inconclusive"
    assert report["complete"] is False
    assert "MemoryError" in report["error"]


def export_boxes(tmp_path, center):
    first = tmp_path / "first.stl"
    second = tmp_path / "second.stl"
    trimesh.creation.box().export(first)
    mesh = trimesh.creation.box()
    mesh.apply_translation(center)
    mesh.export(second)
    return first, second


def test_contact_is_a_failure_and_json_preserves_result(tmp_path):
    first, second = export_boxes(tmp_path, (1, 0, 0))
    output = tmp_path / "result.json"
    assert main([str(first), str(second), "--json", str(output)]) == 1
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "violation"
    assert result["complete"]
    assert result["pairs"][0]["status"] == "touching"


def test_configured_gap_changes_acceptance(tmp_path):
    first, second = export_boxes(tmp_path, (1.5, 0, 0))
    assert main([str(first), str(second)]) == 0
    assert main([str(first), str(second), "--min-gap-mm", "0.6"]) == 1
    assert main([str(first), str(second), "--min-gap-mm", "0.5"]) == 0


def test_unknown_native_file_is_inconclusive(tmp_path):
    source = tmp_path / "unknown.chitu"
    source.write_bytes(b"unknown format")
    output = tmp_path / "result.json"
    assert main([str(source), "--json", str(output)]) == 2
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "inconclusive"
    assert result["violation_found"] is None


def test_output_cannot_overwrite_input(tmp_path):
    first, second = export_boxes(tmp_path, (1.5, 0, 0))
    original = first.read_bytes()
    assert main([str(first), str(second), "--json", str(first)]) == 2
    assert first.read_bytes() == original
    assert main([str(tmp_path), "--json", str(first)]) == 2
    assert first.read_bytes() == original


def test_cli_writes_interactive_failure_report(tmp_path):
    first, second = export_boxes(tmp_path, (1, 0, 0))
    output = tmp_path / "failure.html"
    assert main([str(first), str(second), "--html", str(output)]) == 1
    page = output.read_text(encoding="utf-8")
    assert '"status":"touching"' in page
    assert "Plotly.newPlot" in page


def test_unreadable_project_still_gets_diagnostic_html(tmp_path):
    source = tmp_path / "unknown.chitu"
    source.write_bytes(b"unknown format")
    output = tmp_path / "failure.html"
    assert main([str(source), "--html", str(output)]) == 2
    page = output.read_text(encoding="utf-8")
    assert '"status":"inconclusive"' in page
    assert '"object_count":0' in page


@pytest.mark.parametrize("gap", ["-1", "nan", "inf"])
def test_invalid_gap_rejected(tmp_path, gap):
    with pytest.raises(SystemExit) as exc:
        main([str(tmp_path), "--min-gap-mm", gap])
    assert exc.value.code == 2
