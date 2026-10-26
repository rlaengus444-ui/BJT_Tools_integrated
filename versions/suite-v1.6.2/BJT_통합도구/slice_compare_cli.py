"""Command line entry point; 0=identical, 1=different, 2=incomplete/error."""
import argparse
import json
import sys
import threading

from slice_compare_engine import CompareOptions, SourceSpec, compare_sources


def main():
    parser = argparse.ArgumentParser(description='PNG / TIFF / JPEG 슬라이싱 묶음 비교')
    parser.add_argument('left', help='A 폴더 / ZIP / 이미지 경로')
    parser.add_argument('right', help='B 폴더 / ZIP / 이미지 경로')
    parser.add_argument('--mode', choices=['path', 'sequence'], default='path')
    parser.add_argument('--deep', action='store_true', help='같은 파일과 한쪽에만 있는 파일도 전체 픽셀을 해독해 손상 검사')
    parser.add_argument('--json', dest='json_path', help='JSON 결과 저장 경로')
    parser.add_argument('--csv', dest='csv_path', help='CSV 결과 저장 경로')
    args = parser.parse_args()
    try:
        report = compare_sources(SourceSpec.from_paths([args.left]), SourceSpec.from_paths([args.right]),
                                 CompareOptions(match_mode=args.mode, verify_identical=args.deep),
                                 progress=lambda percent, message: print(f'[{percent:3}%] {message}', file=sys.stderr))
        if args.json_path:
            report.save_json(args.json_path)
        if args.csv_path:
            report.save_csv(args.csv_path)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        if report.cancelled:
            return 130
        if any(row.status in ('error', 'unsupported') for row in report.rows) or not report.rows:
            return 2
        return 0 if report.summary.get('overall_identical', False) else 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f'검사 실패: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
