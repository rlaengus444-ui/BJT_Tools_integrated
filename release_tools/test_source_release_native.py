"""Source archives retain native inputs without shipping Cargo build output."""
from pathlib import Path
import tempfile
import unittest

from build_source_release import app_files


class SourceReleaseNativeTests(unittest.TestCase):
    def test_excludes_only_cargo_target_and_keeps_product_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = ('native/bjt_mesh_native/Cargo.toml', 'native/bjt_mesh_native/src/lib.rs',
                     'native/bjt_mesh_native/target/release/cache.obj', 'native/bjt_mesh_native.dll',
                     'manuals/guide.pdf', 'other/target/keep.txt')
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
            found = {name for _, name in app_files(root)}
            self.assertEqual(found, set(files) - {'native/bjt_mesh_native/target/release/cache.obj'})


if __name__ == '__main__':
    unittest.main()
