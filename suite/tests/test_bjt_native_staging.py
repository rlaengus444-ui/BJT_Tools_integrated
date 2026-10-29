"""Check optional native build inputs without importing the release runner."""
import ast
from pathlib import Path
import shutil
import tempfile
import unittest

BUILD = Path(__file__).resolve().parents[1] / '배포_빌드'


def definition(filename, name):
    path = BUILD / filename
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(nodes) != 1:
        raise AssertionError(f'Expected one {name} definition')
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    namespace = {'shutil': shutil}
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace[name]


stage = definition('prepare_build.py', 'stage_optional_native')
spec_binary = definition('BJT_Suite.spec', 'optional_native_binary')


class NativeStagingTests(unittest.TestCase):
    def test_missing_and_unsupported_are_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'src', Path(directory) / 'stage'
            self.assertIsNone(stage(source, output, 'linux'))
            self.assertIsNone(stage(source, output, 'unsupported'))
            self.assertIsNone(spec_binary(output, 'linux'))
            self.assertFalse(output.exists())

    def test_exact_platform_artifact_only(self):
        for platform, expected in [('win32', 'bjt_mesh_native.dll'), ('linux', 'libbjt_mesh_native.so'), ('darwin', 'libbjt_mesh_native.dylib')]:
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                source, output = Path(directory) / 'src', Path(directory) / 'stage'
                native = source / 'native'
                native.mkdir(parents=True)
                for name in ('bjt_mesh_native.dll', 'libbjt_mesh_native.so', 'libbjt_mesh_native.dylib', 'Cargo.toml'):
                    (native / name).write_bytes(name.encode())
                (native / 'target').mkdir()
                (native / 'target' / 'other.dll').write_bytes(b'ignored')
                result = stage(source, output, platform)
                self.assertEqual(result, output / 'native' / expected)
                self.assertEqual(result.read_bytes(), expected.encode())
                self.assertEqual([p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()], ['native/' + expected])
                self.assertEqual(spec_binary(output, platform), (str(result), 'native'))
                other = 'linux' if platform == 'win32' else 'win32'
                self.assertIsNone(spec_binary(output, other))

    def test_build_provenance_and_rust_notices_follow_the_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'src', Path(directory) / 'stage'
            native = source / 'native'
            (native / 'licenses').mkdir(parents=True)
            (native / 'bjt_mesh_native.dll').write_bytes(b'binary')
            (native / 'bjt_mesh_native.build.json').write_bytes(b'{"target":"x86_64-pc-windows-msvc"}')
            (native / 'licenses/COPYRIGHT-library.html').write_bytes(b'Rust standard library notices')
            stage(source, output, 'win32')
            self.assertEqual((output / 'native/bjt_mesh_native.build.json').read_bytes(),
                             (native / 'bjt_mesh_native.build.json').read_bytes())
            self.assertEqual((output / 'licenses/BJT_Rust/COPYRIGHT-library.html').read_bytes(),
                             (native / 'licenses/COPYRIGHT-library.html').read_bytes())


    def test_spec_data_loop_does_not_duplicate_binary(self):
        tree = ast.parse((BUILD / 'BJT_Suite.spec').read_text(encoding='utf-8'))
        loops = [node for node in tree.body if isinstance(node, ast.For)
                 and isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Attribute)
                 and node.iter.func.attr == 'rglob']
        self.assertEqual(len(loops), 1)
        module = ast.fix_missing_locations(ast.Module(body=loops, type_ignores=[]))
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            native = app / 'native'
            native.mkdir()
            binary = native / 'bjt_mesh_native.dll'
            binary.write_bytes(b'binary')
            (app / 'bjt_mesh_native.py').write_text('# wrapper')
            datas = []
            exec(compile(module, 'BJT_Suite.spec', 'exec'),
                 {'APP': app, 'Path': Path, 'native_binary': (str(binary), 'native'), 'datas': datas})
            self.assertEqual(datas, [(str(app / 'bjt_mesh_native.py'), '.')])


if __name__ == '__main__':
    unittest.main()
