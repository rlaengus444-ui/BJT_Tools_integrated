"""Load selected production definitions without bootstrapping the GUI app."""

import ast
import base64
import zlib
from pathlib import Path


def load_definitions(source_path, names, namespace=None):
    """Execute selected top-level definitions/assignments in a supplied namespace.

    Dependencies must be supplied by the caller; definitions execute in source order.
    Numba decorators are removed so kernels can run without Numba.
    """
    source_path = Path(source_path)
    return _load_source(source_path.read_text(encoding="utf-8"), str(source_path), names, namespace)


def load_embedded_definitions(source_path, encoded_name, names, namespace=None):
    """Decode a literal base64/zlib embedded source and load selected definitions."""
    source_path = Path(source_path)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == encoded_name for t in node.targets):
            encoded = ast.literal_eval(node.value)
            source = zlib.decompress(base64.b64decode(encoded)).decode("utf-8")
            return _load_source(source, f"{source_path}:{encoded_name}", names, namespace)
    raise ValueError(f"Embedded source not found: {encoded_name}")


def _load_source(source, filename, names, namespace):
    tree = ast.parse(source, filename=filename)
    requested = set(names)
    found = set()
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            declared = {node.name}
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            declared = {target.id for target in targets if isinstance(target, ast.Name)}
        else:
            continue
        if declared & requested:
            selected.append(node)
            found.update(declared & requested)
    if requested - found:
        raise ValueError(f"Definitions not found: {sorted(requested - found)}")

    class StripNumbaDecorators(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            def is_numba(decorator):
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                return (isinstance(target, ast.Name) and target.id in {"jit", "njit"}) or (
                    isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                    and target.value.id == "numba" and target.attr in {"jit", "njit"})
            node.decorator_list = [d for d in node.decorator_list if not is_numba(d)]
            return self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected], type_ignores=[])
    module = ast.fix_missing_locations(StripNumbaDecorators().visit(module))
    result = dict(namespace or {})
    exec(compile(module, filename, "exec"), result)
    return result
