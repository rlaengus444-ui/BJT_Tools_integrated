"""Command-line interface; stdout is a summary and optional JSON is a file."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from .geometry import check_overlaps
from .inputs import load_objects

try:
    from display_numbers import format_number, format_lower_bound
except ModuleNotFoundError:
    # Also support the package entry point without changing sys.path.
    import importlib.util
    _display_spec = importlib.util.spec_from_file_location("_chitu_display_numbers", Path(__file__).resolve().parents[1] / "display_numbers.py")
    _display_module = importlib.util.module_from_spec(_display_spec)
    _display_spec.loader.exec_module(_display_module)
    format_number, format_lower_bound = _display_module.format_number, _display_module.format_lower_bound


def _tolerance(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise argparse.ArgumentTypeError("0 이상의 유한한 부피를 입력하세요.")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CHITUBOX Basic 프로젝트 내 겹침·표면 접촉·최소 간격을 검사합니다."
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="프로젝트 1개 또는 배치 좌표를 유지한 개별 STL 파일/폴더")
    parser.add_argument("--json", dest="json_path", type=Path, help="상세 결과를 저장할 JSON 경로")
    parser.add_argument("--html", dest="html_path", type=Path, help="문제 오브젝트 쌍을 강조하는 회전·확대 가능한 3D HTML 경로")
    parser.add_argument("--volume-tolerance", type=_tolerance, default=1e-6, metavar="VOLUME", help="이 값보다 큰 교차 부피만 겹침으로 판정 (기본 1e-6, mm³ 가정)")
    parser.add_argument("--min-gap-mm", type=_tolerance, default=0.0, metavar="MM", help="유지해야 할 최소 간격(mm). 기본 0; 표면 접촉은 항상 불합격")
    parser.add_argument("--distance-tolerance-mm", type=_tolerance, default=1e-6, metavar="MM", help="접촉 및 간격 판정의 수치 허용 오차 (기본 1e-6 mm)")
    args = parser.parse_args(argv)
    outputs = [path for path in (args.json_path, args.html_path) if path is not None]
    if len({p.resolve() for p in outputs}) != len(outputs):
        print("오류: JSON과 HTML 결과 경로는 서로 달라야 합니다.", file=sys.stderr)
        return 2
    if any(
        output.resolve() == p.resolve()
        or (
            p.is_dir()
            and output.resolve().parent == p.resolve()
            and output.suffix.lower() == ".stl"
        )
        for output in outputs
        for p in args.inputs
    ):
        print("오류: 결과 경로는 입력 파일과 달라야 합니다.", file=sys.stderr)
        return 2
    objects = []
    try:
        objects, source_format = load_objects(args.inputs)
        report = check_overlaps(
            objects,
            volume_tolerance=args.volume_tolerance,
            min_gap_mm=args.min_gap_mm,
            distance_tolerance_mm=args.distance_tolerance_mm,
        )
        report.update(
            source_format=source_format,
            inputs=[str(p) for p in args.inputs],
            units="mm (assumed)",
            volume_units="mm^3 (assumed)",
        )
    except (OSError, ValueError, RuntimeError, IndexError, MemoryError) as exc:
        report = {
            "status": "inconclusive",
            "complete": False,
            "placement_status": "unknown", "placement_complete": False,
            "placement_minimum_distance_mm": None, "placement_minimum_distance_pair": None,
            "placement_minimum_distance_is_certified": False,
            "overlap_found": None,
            "violation_found": None,
            "object_count": None,
            "min_gap_mm": args.min_gap_mm,
            "inputs": [str(p) for p in args.inputs],
            "error": f"{type(exc).__name__}: " + ("검사에 필요한 메모리가 부족합니다." if isinstance(exc, MemoryError) else str(exc)),
        }
    if args.json_path:
        try:
            args.json_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        except (OSError, ValueError) as exc:
            print(f"JSON 저장 실패: {exc}", file=sys.stderr)
            return 2
    if args.html_path:
        try:
            from .viewer import write_html_report

            write_html_report(objects, report, args.html_path)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"3D HTML 저장 실패: {exc}", file=sys.stderr)
            return 2
        print(f"3D 배치 결과: {args.html_path}")
    _print_summary(report)
    return 1 if report["status"] == "violation" else 0 if report["status"] == "clear" else 2


def _print_summary(report: dict) -> None:
    if "error" in report:
        print(f"판정 불가: {report['error']}", file=sys.stderr)
        return
    print(f"오브젝트 {report['object_count']}개 / 비교 쌍 {len(report['pairs'])}개")
    invalid = [obj for obj in report.get("objects", []) if not obj.get("valid")]
    if report.get("placement_complete") and report.get("placement_status") == "clear":
        print(f"배치 분리 확인: {len(report['pairs'])}쌍 · 요구 간격 {format_number(report['min_gap_mm'])} mm 충족")
        if not report.get("complete"):
            print(f"형상 검증 미완료: {len(invalid)}개 · 배치 분리는 메쉬의 닫힘·품질 합격을 뜻하지 않습니다.", file=sys.stderr)
    elif "placement_status" in report:
        label = "위반 확인" if report["placement_status"] == "violation" else "미완료"
        print(f"배치 검사 {label}")
    normalized = [obj for obj in report.get("objects", []) if obj.get("normalization")]
    if normalized:
        print(f"원본 형상을 유지한 검사용 연결면 정리: {len(normalized)}개 (원본 파일·좌표 유지)")
    measured_pairs = [pair for pair in report["pairs"] if pair.get("distance_mm") is not None]
    if report.get("placement_minimum_distance_is_certified"):
        a, b = report["placement_minimum_distance_pair"]
        print(f"배치 전체 최소 간격 (인증): {format_number(report['placement_minimum_distance_mm'])} mm ([{a}] ↔ [{b}])")
    elif measured_pairs:
        nearest = min(measured_pairs, key=lambda pair: pair["distance_mm"])
        scope = "측정된 최소 간격" if report.get("closest_distance_is_global", report.get("complete")) else "검사된 유효 메쉬 쌍의 최소 간격"
        print(f"{scope}: {format_number(nearest['distance_mm'])} mm ([{nearest['index_a']}] ↔ [{nearest['index_b']}])")
    if report.get("distance_mode") == "certified-clearance":
        print("멀리 떨어진 통과 쌍의 거리 하한은 JSON의 distance_lower_bound_mm에 기록했습니다.")
    if report["status"] == "violation":
        print(f"불합격: {len(report['violations'])}쌍 (요구 간격 {format_number(report['min_gap_mm'])} mm / 표면 접촉 금지)")
        labels = {"overlap": "부피 겹침", "touching": "표면 접촉", "too_close": "최소 간격 미달"}
        for pair in report["violations"]:
            detail = labels[pair["status"]]
            if pair.get("distance_mm") is not None:
                detail += f", 간격 {format_number(pair['distance_mm'])} mm"
            if pair.get("intersection_volume"):
                detail += f", 교차 부피 {format_number(pair['intersection_volume'])} mm³"
            print(
                f"  [{pair['index_a']}] {pair['object_a']} ↔ "
                f"[{pair['index_b']}] {pair['object_b']}: {detail}"
            )
    elif report["status"] == "clear":
        print(f"합격: 겹침·접촉 없음, 최소 간격 {format_number(report['min_gap_mm'])} mm 충족")
        if report["object_count"] == 1:
            print("  인식된 오브젝트가 1개이므로 비교 대상 쌍이 없습니다.")
    if not report["complete"]:
        print("검사 미완료: 판정하지 못한 오브젝트/쌍이 있습니다.", file=sys.stderr)
        for obj in report["objects"]:
            for error in obj["errors"]:
                print(f"  [{obj['index']}] {obj['name']}: {error}", file=sys.stderr)
            diagnostic = obj.get("topology_diagnostics", {})
            if obj.get("errors") and diagnostic.get("face_counts_complete"):
                print(f"    진단: 퇴화면 {diagnostic['degenerate_face_count']}개, "
                      f"면적 수치 오류 {diagnostic['invalid_area_face_count']}개, "
                      f"비다양체 edge {format_number(diagnostic.get('nonmanifold_edge_count'))}개, "
                      f"경계 edge {format_number(diagnostic.get('boundary_edge_count'))}개", file=sys.stderr)
            if obj.get("normalization_error"):
                print("    연결면 정리 보류: 원본 표면·체적 보존을 증명하지 못해 자동 수정하지 않았습니다. "
                      + obj["normalization_error"], file=sys.stderr)
        for pair in report["pairs"]:
            if pair.get("error") and pair["error"] != "One or both objects failed solid-mesh validation.":
                print(f"  [{pair['index_a']}] ↔ [{pair['index_b']}]: {pair['error']}", file=sys.stderr)
        unknown = [pair for pair in report["pairs"] if pair.get("status") == "inconclusive"]
        if unknown:
            print(f"  형상 미판정 {len(unknown)}쌍: 전체 쌍의 형상·배치 판정은 JSON에 각각 보존됩니다.", file=sys.stderr)
