"""BJT integrated desktop app and original-tool CLI entry point."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy
import sys
import traceback


def main(argv=None):
    from suite_loader import BASE_DIR, tool_specs, verify_sources
    parser = argparse.ArgumentParser(description='BJT 통합 작업 도구')
    parser.add_argument('--list-tools', action='store_true', help='통합 도구 목록')
    parser.add_argument('--verify-sources', action='store_true', help='포함된 원본 파일 무결성 검사')
    parser.add_argument('--compare-cli', nargs=argparse.REMAINDER, default=None,
                        help='슬라이싱 묶음 비교 CLI: 뒤의 모든 인수 전달')
    parser.add_argument('--tool', choices=list(tool_specs()), help='해당 원본 도구의 독립 GUI/CLI 실행')
    args, extra = parser.parse_known_args(argv)
    if args.compare_cli is not None and args.tool:
        parser.error('--compare-cli와 --tool은 함께 사용할 수 없습니다.')
    if args.list_tools:
        print(json.dumps(list(tool_specs().values()), ensure_ascii=False, indent=2))
        return 0
    if args.verify_sources:
        checks = verify_sources()
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return 0 if all(item['valid'] for item in checks) else 1
    if extra and not args.tool:
        parser.error('도구 인수는 --tool과 함께 사용하세요: ' + ' '.join(extra))
    from suite_runtime import ensure_runtime
    ensure_runtime()
    if args.compare_cli is not None:
        source = BASE_DIR / 'slice_compare_cli.py'
        extra = args.compare_cli
        if extra[:1] == ['--']:
            extra = extra[1:]
        sys.argv = [str(source), *extra]
        runpy.run_path(str(source), run_name='__main__')
        return 0
    if args.tool:
        source = BASE_DIR / tool_specs()[args.tool]['source']
        if extra[:1] == ['--']:
            extra = extra[1:]
        sys.argv = [str(source), *extra]
        if args.tool == 'chitu_checker' and extra:
            from chitu_overlap.cli import main as chitu_main
            return chitu_main(extra)
        if args.tool == 'exchanger':
            from suite_loader import ToolLoader
            ToolLoader().load('exchanger').main()
            return 0
        runpy.run_path(str(source), run_name='__main__')
        return 0
    from suite_ui import main as gui_main
    gui_main()
    return 0


def start():
    try:
        return main()
    except SystemExit:
        raise
    except BaseException:
        trace = traceback.format_exc()
        try:
            directory = Path(__file__).resolve().parent / 'logs'
            directory.mkdir(exist_ok=True)
            with (directory / 'errors.log').open('a', encoding='utf-8') as stream:
                stream.write(trace + '\n')
        except OSError:
            pass
        if sys.stderr is not None:
            print(trace, file=sys.stderr)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror('BJT 통합 작업 도구 · 실행 오류', trace[-3500:], parent=root)
            root.destroy()
        except Exception:
            pass
        return 1


if __name__ == '__main__':
    raise SystemExit(start())
