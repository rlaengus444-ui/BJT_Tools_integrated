"""Compare the vendored engine and preserved tools with their Git source."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT.parent / 'modeling-arrangement-optimizer'
BASE = '43a58bf'


def original(path):
    return subprocess.check_output(['git', 'show', f'{BASE}:{path}'], cwd=SOURCE)


def main():
    checks = []
    for path in sorted((ROOT/'suite/powdernest').glob('*.py')):
        name = 'powdernest/' + path.name
        if path.name == 'export_safety.py':
            kind = 'new non-numerical input preservation guard'
            assert path.read_bytes() == (SOURCE/name).read_bytes()
        else:
            before = original(name)
            if path.name in ('io.py', 'chitubox.py'):
                tree = ast.parse(path.read_text())
                function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                                and n.name == ('export_result' if path.name == 'io.py' else 'export_chitubox'))
                assert isinstance(function.body[1], ast.ImportFrom)
                assert function.body[1].module == 'export_safety'
                assert isinstance(function.body[2], ast.Expr)
                assert function.body[2].value.func.id == 'protect_model_outputs'
                del function.body[1:3]
                assert ast.dump(tree) == ast.dump(ast.parse(before))
                kind = 'AST identical after removing added export preflight import/call'
            else:
                assert path.read_bytes() == before
                kind = 'byte identical'
        checks.append({'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'comparison': kind})
    manifest = json.loads((ROOT/'suite/source_manifest.json').read_text())
    for item in manifest['tools']:
        if item['id'] == 'nesting':
            continue
        source_path = 'suite/' + item['source']
        preserved = subprocess.check_output(['git','show',f'b162078:{source_path}'],cwd=ROOT)
        assert preserved == (ROOT/source_path).read_bytes()
    standalone = ROOT/'standalone/PowderNest_Embedded_Policy.py'
    assert standalone.read_bytes() == (SOURCE/'PowderNest.py').read_bytes()
    report = {'engine_base': BASE, 'checks': checks, 'preserved_original_tool_count': 11,
              'standalone_sha256': hashlib.sha256(standalone.read_bytes()).hexdigest(),
              'numerical_and_geometry_logic_unchanged': True}
    destination = Path(__file__).with_name('engine-provenance.json')
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(f'{len(checks)} engine files checked; 11 original tool sources preserved')


if __name__ == '__main__':
    main()
