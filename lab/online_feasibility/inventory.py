"""Read-only exact-source call inventory for the Phase A laboratory."""

import ast
import hashlib
import stat
from pathlib import Path

APPROVED_ROBOT = '5d00ac1516728986d2fcc314d7f6c862a7f9d14142b4e9307f812807b9986d5e'
FILES = ('gomrok.py', 'robot_launcher.py', 'app_paths.py', 'panel.py',
         'panel_auth.py', 'crm.py', 'panel_gui.pyw', 'system_debug.py',
         'telegram_notify.py', 'captcha_collector.py', 'browser_visibility.py',
         'estelam_hamta.py', 'estelam_gomrok.py', 'estelam_shot.py')
MAX_SITES = 50000


def source_hash(path: Path) -> str:
    if path.is_symlink() or getattr(path.stat(), 'st_file_attributes', 0) & 0x400:
        raise ValueError('SOURCE_SYMLINK')
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('SOURCE_NOT_FILE')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_approved_robot(root: Path) -> None:
    if source_hash(root / 'gomrok.py') != APPROVED_ROBOT:
        raise ValueError('ROBOT_HASH_MISMATCH')


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return dotted(node.value) + '.' + node.attr
    return '<dynamic>'


def classify(call: str) -> str:
    leaf = call.rsplit('.', 1)[-1]
    if leaf in {'open', 'fdopen', 'write', 'write_text', 'write_bytes', 'dump',
                'save', 'mkdir', 'makedirs', 'replace', 'remove', 'unlink'}:
        return 'io_candidate'
    if leaf in {'execute_script', 'execute_async_script', 'execute_cdp_cmd',
                'click', 'send_keys', 'get', 'save_screenshot', 'screenshot',
                'fire_refresh_events'}:
        return 'action_candidate'
    if leaf in {'Popen', 'run', 'system', 'execv', 'spawnv'}:
        return 'process_candidate'
    return 'unclassified_call'


class Sites(ast.NodeVisitor):
    def __init__(self, file: str):
        self.file, self.owners, self.sites, self.imports = file, [], [], []

    def visit_FunctionDef(self, node):
        self.owners.append(node.name)
        self.generic_visit(node)
        self.owners.pop()

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Call(self, node):
        call = dotted(node.func)
        self.sites.append({'file': self.file, 'line': node.lineno,
                           'owner': '.'.join(self.owners) or '<module>',
                           'call': call, 'kind': classify(call)})
        if len(self.sites) > MAX_SITES:
            raise ValueError('INVENTORY_LIMIT')
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append({'file': self.file, 'line': node.lineno,
                                 'module': alias.name})

    def visit_ImportFrom(self, node):
        self.imports.append({'file': self.file, 'line': node.lineno,
                             'module': node.module or '<relative>'})


def build_inventory(root: Path) -> dict:
    result = {'source_hashes': {}, 'sites': [], 'imports': []}
    for relative in FILES:
        path = root / relative
        result['source_hashes'][relative] = source_hash(path)
        visitor = Sites(relative)
        visitor.visit(ast.parse(path.read_text(encoding='utf-8-sig')))
        result['sites'].extend(visitor.sites)
        result['imports'].extend(visitor.imports)
        if len(result['sites']) > MAX_SITES:
            raise ValueError('INVENTORY_LIMIT')
    return result
