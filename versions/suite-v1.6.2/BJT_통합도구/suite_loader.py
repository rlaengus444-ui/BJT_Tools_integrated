"""Original source loader; only the root-window class base is adapted."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import threading

BASE_DIR = Path(__file__).resolve().parent


def read_manifest():
    return json.loads((BASE_DIR / 'source_manifest.json').read_text(encoding='utf-8'))


def tool_specs():
    return {tool['id']: tool for tool in read_manifest()['tools']}


def verify_sources():
    checks = []
    for tool in read_manifest()['tools']:
        path = BASE_DIR / tool['source']
        data = path.read_bytes()
        checks.append({'id': tool['id'], 'valid': hashlib.sha256(data).hexdigest() == tool['sha256'],
                       'bytes': len(data), 'path': str(path)})
    for dependency in read_manifest().get('dependencies', []):
        path = BASE_DIR / dependency['path']
        data = path.read_bytes()
        checks.append({'id': dependency['path'], 'valid': hashlib.sha256(data).hexdigest() == dependency['sha256'],
                       'bytes': len(data), 'path': str(path)})
    return checks


def compile_tool(tool, source):
    tree = ast.parse(source, filename=str(BASE_DIR / tool['source']))
    changed = []
    if tool['embedded_base']:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == tool['controller']:
                if len(node.bases) != 1:
                    raise RuntimeError('지원하지 않는 GUI 상속 구조: ' + tool['id'])
                node.bases = [ast.copy_location(ast.Name(id='_suite_root_base', ctx=ast.Load()), node.bases[0])]
                changed.append(node.name)
        if changed != [tool['controller']]:
            raise RuntimeError('GUI 클래스를 찾을 수 없습니다: ' + tool['id'])
    ast.fix_missing_locations(tree)
    return compile(tree, str(BASE_DIR / tool['source']), 'exec', dont_inherit=True), changed


def core_fingerprint(tool, source):
    """Ignore only window superclass expressions; preserve every body/statement."""
    tree = ast.parse(source)
    if tool['embedded_base']:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == tool['controller']:
                node.bases = []
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


class ToolLoader:
    def __init__(self, base_factory=None):
        self.specs = tool_specs()
        self.modules = {}
        self.base_factory = base_factory
        self.lock = threading.RLock()

    def load(self, key):
        with self.lock:
            if key in self.modules:
                return self.modules[key]
            tool = self.specs[key]
            if key in ('compare', 'exchanger', 'm2_converter', 'chitu_checker'):
                for dependency in read_manifest().get('dependencies', []):
                    path = BASE_DIR / dependency['path']
                    if hashlib.sha256(path.read_bytes()).hexdigest() != dependency['sha256']:
                        raise RuntimeError('검사 엔진의 무결성 검사가 실패했습니다: ' + str(path))
            path = BASE_DIR / tool['source']
            source = path.read_bytes()
            if hashlib.sha256(source).hexdigest() != tool['sha256']:
                raise RuntimeError('원본 모듈이 변경되었습니다. source_manifest.json과 대조하세요: ' + str(path))
            code, changed = compile_tool(tool, source)
            name = '_bjt_suite_' + key
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            if tool['embedded_base']:
                if self.base_factory is None:
                    import tkinter as tk
                    module._suite_root_base = tk.Frame
                else:
                    module._suite_root_base = self.base_factory(key)
            module._suite_adapted_classes = tuple(changed)
            sys.modules[name] = module
            try:
                exec(code, module.__dict__)
                if key == 'exchanger':
                    from slice_replace_validation import install
                    install(module)
            except BaseException:
                for alias, item in list(sys.modules.items()):
                    if item is module:
                        sys.modules.pop(alias, None)
                raise
            self.modules[key] = module
            return module
