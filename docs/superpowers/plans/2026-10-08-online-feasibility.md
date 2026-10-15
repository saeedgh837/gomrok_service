# Online CRM Phase A: Baseline and Compatibility Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible, isolated diagnostic harness that measures existing behavior and characterizes unchanged-robot storage/job-control seams without claiming production compatibility from mocks.

**Architecture:** Keep all new execution code under `lab/`. Inventory the actual source, execute only explicitly selected non-main robot functions with synthetic dependencies in a disposable subprocess, record attempted writes, and test a separate memory-only fencing model. The result is a sanitized CONDITIONAL or BLOCKED report, never a production adapter or an unqualified release PASS.

**Tech Stack:** Windows; existing Python interpreter; standard-library `unittest`, `ast`, `runpy`, `tempfile`, `subprocess`, `io`, `hashlib`, `sys.addaudithook`; no new package or browser/server dependency.

**Spec:** [Approved full architecture](../specs/2026-10-08-server-authoritative-crm-design.md), particularly sections 7, 8 and 10. [Complete subsystem roadmap](2026-10-08-online-delivery-roadmap.md) keeps the remaining product scope explicit.

## Global Constraints

- `gomrok.py` must remain byte-identical. Approved SHA-256: `5d00ac1516728986d2fcc314d7f6c862a7f9d14142b4e9307f812807b9986d5e` at repository commit `08c344511d4f53ac19ad1cd3bbb445e342ddc942`.
- All new source code, tests, UI strings, logs, and checked-in documentation are English per the repository contract.
- Keep framework-free PHP 8.1+ and MySQL 5.7+/MariaDB 10.3+ compatibility; Phase A does not change host code or require those services.
- Do not globally monkeypatch `builtins.open` or unrelated modules. Namespace adapters/fake imports exist only inside the disposable lab subprocess, not in production launchers.
- Only synthetic fixtures. Never read operator business stores/config, `%LOCALAPPDATA%/irica`, credentials, cookies, bot/proxy settings or test DB secrets into a report.
- Do not execute `gomrok.main()`, invoke real Selenium/Chrome/Edge, download drivers, evaluate embedded JavaScript, or contact a third-party website. Do not exercise code guessing, CAPTCHA/MFA bypass or any real submission.
- No panel/host credentials in process arguments or environment. The fixture environment contains only disposable paths and non-secret controls.
- No product source, applied migration, installed app or live-data changes. No production requests, account changes, cutover, cleanup, release, installation, commit or push.
- Reports are explicit lab artifacts, not application runtime stores. Store generated artifacts outside the repository in the execution scratch directory; send only the sanitized completed report.
- Existing historical data stays intact. Program assets and OS/browser storage limits are not evidence of image-only application-owned business storage.
- Observe attempted I/O, not only final directory contents. A Python audit hook is an observer/fail-fast guard, not an OS sandbox or exhaustive native-process monitor.

## Review Focus

1. A robot catches a rejected write exception and returns normally: the audit violation must still block the report. Owned by Tasks 2, 3 and 5.
2. A lease is lost and legacy `mark_profile_success` fallback executes: stale ownership must not publish any successful result. Owned by Task 4.
3. A revoked session still has an in-flight asynchronous command: late results are rejected, but already-dispatched effects remain uncertain and cannot be called cancelled. Owned by Task 4.
4. A helper writes outside the module-local `open` seam, or creates and deletes a text file: observe the attempt and do not infer compliance from an empty folder. Owned by Tasks 1 and 3.
5. A source/dependency/test is changed, missing or skipped: refuse stale coverage and report missing prerequisites as BLOCKED, not PASS. Owned by Tasks 1 and 5.

---

## Scope, evidence and deliverable limits

This is one independently testable sub-project, not the complete online rewrite. It implements the early evidence gate; the roadmap owns the remaining architecture requirements. A successful laboratory run establishes only that selected unchanged function bodies can use explicit fixture seams. Imported real helper persistence, production bootstrap, worker IPC, native browser writes and in-flight asynchronous action control remain unresolved until separately reviewed realistic integration.

Known source evidence:

- `robot_launcher.py::main` imports/configures `system_debug` before `runpy.run_path`.
- `gomrok.py` configures diagnostics and resolves data paths at import; directly appends `logs.jsonl`, writes debug HTML/JSON, payment-link text and PNG bytes.
- `DebugController._dump_state`, `log_event`, `_full_screenshot`, `handle_already_registered` and `handle_receipt_and_payment` are safe to characterize only with inert synthetic dependencies.
- `_full_screenshot` has both a direct CDP byte-write and a `save_screenshot` fallback. Faking the latter does not prove real WebDriver file behavior.
- `_start_profile_heartbeat` returns a thread that stops on a false heartbeat; that is not a main-robot stop mechanism.
- `handle_already_registered` may call `mark_profile_success` after rejected output. The payment handler calls it before click and again after obtaining the link. A future bridge must fence all of them, not only `record_profile_output`.
- Commands can flow through WebElement methods, `cc.fire_refresh_events`, `execute_script`, `execute_async_script`, navigation and CDP. Embedded async actions remain opaque; do not run them to manufacture evidence.
- `captcha_collector.build_driver` creates real Chrome/Edge and may update/download a driver. It is forbidden in these probes.

## File map

| New file | Responsibility |
| --- | --- |
| `lab/online_feasibility/__init__.py` | Empty package marker. |
| `lab/online_feasibility/inventory.py` | Exact hashes, AST call/import sites and pinned-source validation. |
| `lab/online_feasibility/storage.py` | Bounded memory-only non-image sinks and opaque synthetic PNG persistence. |
| `lab/online_feasibility/probe.py` | Explicit non-main source loading, inert dependencies and child-only attempted-I/O observation. |
| `lab/online_feasibility/fencing.py` | Synthetic command-boundary/late-result/fallback model; never a real browser wrapper. |
| `lab/online_feasibility/report.py` | Fail-closed evidence aggregation and sanitized report contract. |
| `lab/online_feasibility/cases.py` | Twelve fixed, bounded synthetic case actions. |
| `lab/online_feasibility/runner.py` | Private path/environment validation and bounded child transport. |
| `lab/online_feasibility/baseline.py` | Pinned allowlisted legacy snapshot and conservative attempted-I/O evidence. |
| `lab/scripts/run_online_feasibility.py` | CLI orchestration of the registered lab cases and summary. |
| `lab/tests/test_online_inventory.py` | Source identity, missing files, call coverage and manifest limits. |
| `lab/tests/test_online_storage.py` | Bounds, containment, invalid-image rejection, opaque image paths. |
| `lab/tests/test_online_probe.py` | Unchanged function bodies, attempted-I/O detection and fake dependency labeling. |
| `lab/tests/test_online_fencing.py` | Revocation/expiry/fencing/in-flight/fallback characterization. |
| `lab/tests/test_online_report.py` | Missing/skipped evidence and no false production PASS. |
| `lab/tests/test_online_baseline.py` | Archive allowlist, sanitized denials, transitive snapshot prerequisites and real guarded counts. |

Do not modify `lab/scripts/run_panel_tests.py` merely to register this experiment. New tests use standard-library discovery; the existing runner is the legacy baseline. Do not modify `robot_launcher.py`, `gomrok.py`, `panel.py`, `crm.py`, `app_paths.py`, `captcha_collector.py`, Telegram helpers or host files in Phase A.

## Shared contracts

`Site` is a JSON-compatible dict with `file: str`, `line: int`, `owner: str`, `call: str`, `kind: str`. No source literals, captured values or absolute user paths appear in it.

`CaseResult` is a dict with exactly `id: str`, `status: str` (`passed`, `failed`, `blocked`), `evidence_kind: str` (`unchanged_body`, `model`, `negative_control`), `violations: int`. Fixed case IDs are declared in Task 5; arbitrary external case IDs are rejected.

All failure diagnostics use fixed codes and relative code paths. Raw exceptions, worker stdout, request bodies, HTML, captions, URLs with queries, credentials and image bytes are never serialized in the report.

## Task 1: Pin source identity, inventory call boundaries and capture a disposable baseline

**Files:** Create `inventory.py`, `__init__.py`; test `test_online_inventory.py`. Read only the source paths in `FILES` below and the existing runner/tests.

**Interfaces:** Consumes `Path` of the repository/fixture root. Produces `source_hash(path: Path) -> str`, `build_inventory(root: Path) -> dict`, `require_approved_robot(root: Path) -> None`. Inventory has `source_hashes: dict[str, str]`, `sites: list[Site]`, `imports: list[dict]`.

- [ ] **Step 1: Add failing tests.** Use this initial test module; the file/branch inventory must later be reviewed against the actual source, not inferred solely from test success.

```python
import tempfile
import unittest
from pathlib import Path
from lab.online_feasibility.inventory import build_inventory, require_approved_robot

ROOT = Path(__file__).resolve().parents[2]

class InventoryTests(unittest.TestCase):
    def test_actual_robot_boundaries_are_visible(self):
        require_approved_robot(ROOT)
        result = build_inventory(ROOT)
        sites = [s for s in result['sites'] if s['file'] == 'gomrok.py']
        calls = {s['call'] for s in sites}
        self.assertIn('open', calls)
        self.assertIn('driver.execute_async_script', calls)
        self.assertIn('driver.save_screenshot', calls)
        self.assertIn('profile_store.mark_profile_success', calls)
        self.assertTrue(any(s['owner'] == '<module>' and
                            s['call'] == 'system_debug.configure' for s in sites))
        self.assertTrue(all(s['line'] > 0 for s in sites))

    def test_missing_dependency_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(FileNotFoundError):
                build_inventory(Path(raw))

    def test_changed_source_requires_new_review(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'gomrok.py').write_text('value = 1\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '^ROBOT_HASH_MISMATCH$'):
                require_approved_robot(root)
```

- [ ] **Step 2: Run the new test and confirm failure is the missing new module.**

```bash
"C:/Program Files/Python310/python.exe" -B -m unittest discover -s lab/tests -p test_online_inventory.py -v
```

- [ ] **Step 3: Implement exact-source inventory.** Keep every call, including unclassified calls, so an unfamiliar writer cannot vanish from evidence. Use the following core implementation and reject more than 50,000 sites with `INVENTORY_LIMIT` rather than truncating.

```python
import ast
import hashlib
from pathlib import Path

APPROVED_ROBOT = '5d00ac1516728986d2fcc314d7f6c862a7f9d14142b4e9307f812807b9986d5e'
FILES = ('gomrok.py', 'robot_launcher.py', 'app_paths.py', 'panel.py',
         'panel_auth.py', 'crm.py', 'panel_gui.pyw', 'system_debug.py',
         'telegram_notify.py', 'captcha_collector.py', 'browser_visibility.py',
         'estelam_hamta.py', 'estelam_gomrok.py', 'estelam_shot.py')

def source_hash(path: Path) -> str:
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
    if leaf in {'open', 'fdopen', 'write', 'write_text', 'write_bytes',
                'dump', 'save', 'mkdir', 'makedirs', 'replace', 'remove', 'unlink'}:
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
        if path.is_symlink():
            raise ValueError('SOURCE_SYMLINK')
        result['source_hashes'][relative] = source_hash(path)
        visitor = Sites(relative)
        visitor.visit(ast.parse(path.read_text(encoding='utf-8-sig')))
        result['sites'].extend(visitor.sites)
        result['imports'].extend(visitor.imports)
    if len(result['sites']) > 50000:
        raise ValueError('INVENTORY_LIMIT')
    return result
```

Function/class header expressions can execute in the enclosing scope. Review their AST sites explicitly; do not treat the visitor's `owner` label as proof that every site is runtime-only. Review aliases, custom writers, dynamic calls, helper imports, downloaded files and native processes. Annotate relevant sites as exercised-body, modeled, or unresolved, including imports beyond `FILES` (for example solver/updater dependencies). Do not inspect secrets to complete the inventory.

- [ ] **Step 4: Re-run the exact new-test command; expect all three tests to pass.** Record actual counts, not this plan's expectation.
- [ ] **Step 5: Capture the legacy baseline in a disposable tracked-source snapshot.** First inspect every selected test for real network/browser/data access; a newly discovered unsafe test is a blocker, not permission to run it. Export only tracked source from the unchanged baseline revision into `%TEMP%`, set `IRICA_DATA_DIR` before imports and execute from that snapshot. The following Python is an execution recipe, not code to put in production:

```python
import io
import os
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

source = Path.cwd()
scratch = Path(tempfile.mkdtemp(prefix='irica_online_baseline_'))
archive = subprocess.check_output(['git', 'archive', '--format=zip',
                                  '08c344511d4f53ac19ad1cd3bbb445e342ddc942'], cwd=source)
with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
    for entry in bundle.infolist():
        target = (scratch / entry.filename).resolve()
        if (not target.is_relative_to(scratch.resolve()) or
                stat.S_ISLNK(entry.external_attr >> 16)):
            raise ValueError('BASELINE_PATH_ESCAPE')
    bundle.extractall(scratch)
modules = ('test_crm_validation', 'test_crm_stage_transitions',
           'test_crm_case_integrity', 'test_crm_ledger_integrity',
           'test_crm_scheduler', 'test_auth_hardening', 'test_runtime_api',
           'test_runtime_heartbeat', 'test_token_privacy')
env = {key: os.environ[key] for key in ('SystemRoot', 'SYSTEMROOT', 'WINDIR',
       'PATH', 'PATHEXT', 'COMSPEC') if key in os.environ}
env['TEMP'] = env['TMP'] = str(scratch)
env['IRICA_DATA_DIR'] = str(scratch / 'fixture-data')
env['PYTHONDONTWRITEBYTECODE'] = '1'
result = subprocess.run([sys.executable, 'lab/scripts/run_panel_tests.py', *modules],
                        cwd=scratch, env=env, timeout=600, capture_output=True)
print('baseline_exit=' + str(result.returncode))
```

Reject symlink zip entries by inspecting `external_attr` before extraction as well. Do not copy ignored live settings, `.Codex/`, `AGENTS.md`, `.playwright-mcp/`, cookies or operator stores into the snapshot. Retain failure evidence privately in scratch and summarize only test names/counts/codes. Do not label a missing prerequisite pre-existing without reproduction. Do not start the live installed panel. Host integration baseline is **not run** in this phase: it needs its dedicated fixture DB/tool prerequisites and separate subsystem setup. Do not invent or provision DB credentials here.

- [ ] **Step 6: Review the task diff and record baseline exit/counts/source hashes.** Do not commit. Baseline failure blocks a green aggregate but does not prevent read-only characterization of remaining safe source seams.

## Task 2: Build bounded memory sinks and opaque PNG fixture storage

**Files:** Create `storage.py`; test `test_online_storage.py`.

**Interfaces:** Produces `MemoryStore(app_root: Path, photo_root: Path, png: bytes, limit: int = 1048576)`, `.open(path, mode='r', encoding=None)`, `.exists(path) -> bool`, `.violations: list[str]`, `.image_paths: dict[str, Path]`, `.text: dict[str, str]`. Paths passed by legacy function bodies are logical fixture paths, never host/operator paths. This is not a production attachment/cache implementation.

- [ ] **Step 1: Write tests for opaque names, invalid paths and bounds.** Generate an actual 1x1 PNG from standard-library CRC/zlib, not placeholder bytes. Keep fixture content fixed; accepting that exact fixture PNG is intentional, not a general image validator.

```python
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from lab.online_feasibility.storage import MemoryStore

def fixture_png():
    def chunk(kind, data):
        return (struct.pack('!I', len(data)) + kind + data +
                struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff))
    header = struct.pack('!IIBBBBB', 1, 1, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) +
            chunk(b'IDAT', zlib.compress(b'\x00\x00\x00\x00\xff')) +
            chunk(b'IEND', b''))

class StorageTests(unittest.TestCase):
    def test_text_stays_in_ram_and_image_name_is_opaque(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = MemoryStore(root / 'app', root / 'photos', fixture_png())
            with store.open(root / 'app' / 'payment-link-Sample.txt', 'w') as out:
                out.write('fixture link')
            logical = root / 'app' / 'Sample_receipt.png'
            with store.open(logical, 'wb') as out:
                out.write(fixture_png())
            self.assertFalse((root / 'app' / 'payment-link-Sample.txt').exists())
            physical = store.image_paths[str(logical.resolve())]
            self.assertRegex(physical.name, r'^[a-f0-9]{32}\.png$')
            self.assertEqual(physical.read_bytes(), fixture_png())
            self.assertTrue(store.exists(logical))

    def test_escape_and_fake_png_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = MemoryStore(root / 'app', root / 'photos', fixture_png())
            with self.assertRaisesRegex(ValueError, '^PATH_ESCAPE$'):
                store.open(root / 'outside.txt', 'w')
            with self.assertRaisesRegex(ValueError, '^FIXTURE_PNG_INVALID$'):
                with store.open(root / 'app' / 'bad.png', 'wb') as out:
                    out.write(b'not a png')
            self.assertEqual(len(store.violations), 2)

    def test_swallowed_overflow_remains_a_violation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = MemoryStore(root / 'app', root / 'photos', fixture_png(), limit=4)
            try:
                with store.open(root / 'app' / 'logs.jsonl', 'w') as out:
                    out.write('too long')
            except ValueError:
                pass
            self.assertIn('BUFFER_LIMIT', store.violations)
```

- [ ] **Step 2: Run the tests and confirm missing-module failure.**

```bash
"C:/Program Files/Python310/python.exe" -B -m unittest discover -s lab/tests -p test_online_storage.py -v
```

- [ ] **Step 3: Implement bounded writers with errors recorded before raising.** Override `write` on both buffer kinds and commit only on a clean context exit. The concrete commit/open logic is:

```python
import io
import uuid
from pathlib import Path

class MemoryStore:
    def __init__(self, app_root: Path, photo_root: Path, png: bytes, limit: int = 1048576):
        self.app_root, self.photo_root = app_root.resolve(), photo_root.resolve()
        self.png, self.limit = png, limit
        self.text, self.image_paths, self.violations = {}, {}, []
        self.retained_bytes, self.output_count = 0, 0
        self.photo_root.mkdir(parents=True, exist_ok=True)
    def reject(self, code):
        self.violations.append(code)
        raise ValueError(code)
    def key(self, path):
        candidate = Path(path).resolve()
        if not candidate.is_relative_to(self.app_root):
            self.reject('PATH_ESCAPE')
        return str(candidate)
    def exists(self, path):
        key = self.key(path)
        return key in self.text or key in self.image_paths
    def commit(self, key, binary, value):
        if self.output_count >= 128:
            self.reject('OUTPUT_LIMIT')
        size = len(value) if binary else len(value.encode('utf-8'))
        prior = len(self.text.get(key, '').encode('utf-8')) if not binary else 0
        if self.retained_bytes - prior + size > 8 * 1024 * 1024:
            self.reject('TOTAL_BUFFER_LIMIT')
        if binary:
            if value != self.png or Path(key).suffix.lower() != '.png':
                self.reject('FIXTURE_PNG_INVALID')
            physical = self.photo_root / (uuid.uuid4().hex + '.png')
            with physical.open('xb') as out:
                out.write(value)
            self.image_paths[key] = physical
        else:
            self.text[key] = value
        self.retained_bytes += size - prior
        self.output_count += 1
    def open(self, path, mode='r', encoding=None):
        key = self.key(path)
        if mode == 'r':
            return io.StringIO(self.text.get(key, ''))
        if mode not in {'w', 'a', 'wb'}:
            self.reject('MODE_UNSUPPORTED')
        initial = self.text.get(key, '') if mode == 'a' else ''
        return Writer(self, key, mode == 'wb', initial)

class Writer:
    def __init__(self, store, key, binary, initial):
        self.store, self.key, self.binary = store, key, binary
        self.buffer = io.BytesIO() if binary else io.StringIO(initial)
        self.buffer.seek(0, io.SEEK_END)
    def write(self, value):
        current = self.buffer.getvalue()
        proposed = current + value
        size = len(proposed) if self.binary else len(proposed.encode('utf-8'))
        if size > self.store.limit:
            self.store.reject('BUFFER_LIMIT')
        return self.buffer.write(value)
    def __enter__(self):
        return self
    def __exit__(self, kind, value, trace):
        try:
            if kind is None:
                self.store.commit(self.key, self.binary, self.buffer.getvalue())
        finally:
            self.buffer.close()
        return False
```

The retained-output cap is 8 MiB and the successful-write cap is 128 per case, including repeat writes to one logical key. Keep the following tests in `StorageTests`; both caps record violations before rejection:

```python
    def test_output_count_cannot_grow_unbounded(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = MemoryStore(root / 'app', root / 'photos', fixture_png())
            for number in range(128):
                with store.open(root / 'app' / f'{number}.txt', 'w') as out:
                    out.write('x')
            with self.assertRaisesRegex(ValueError, '^OUTPUT_LIMIT$'):
                with store.open(root / 'app' / '129.txt', 'w') as out:
                    out.write('x')

    def test_total_retained_bytes_are_bounded(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = MemoryStore(root / 'app', root / 'photos', fixture_png())
            for number in range(8):
                with store.open(root / 'app' / f'{number}.txt', 'w') as out:
                    out.write('x' * (1024 * 1024))
            with self.assertRaisesRegex(ValueError, '^TOTAL_BUFFER_LIMIT$'):
                with store.open(root / 'app' / 'ninth.txt', 'w') as out:
                    out.write('x')
```

Empty or exception-aborted image writers do not claim an acknowledged image. `photo_root` is created and symlink/reparse-free containment is checked before installing the child audit guard; never accept an operator-supplied path.

- [ ] **Step 4: Run the same command and confirm all storage tests pass, including the two aggregate-cap tests.**
- [ ] **Step 5: Review byte limits, overflow/suppression behavior and image-only physical writes.** Record the result; do not commit or promote `MemoryStore` into product code.

## Task 3: Load unchanged non-main bodies with explicit fixtures and observe attempted I/O

**Files:** Create `probe.py`; test `test_online_probe.py`.

**Interfaces:** Consumes `MemoryStore`, copied source `Path`, and a fixture bridge object with `claim_profile`, `get_browser_visibility`, `release_profile`, `record_profile_output`, `mark_profile_unusable`, `heartbeat_profile`, `mark_profile_success` callables. Produces `loaded_robot(source: Path, store: MemoryStore, bridge: object) -> Iterator[dict]` as a context manager, `install_audit(photo_root: Path, read_roots: tuple[Path, ...]) -> list[dict]`, `FixtureDriver(store: MemoryStore, png: bytes, fail_cdp=False, payment_button=True)`, `.events: list[str]`. The context yields the functions' actual shared globals, not an assumed writable copy. Copy unchanged `gomrok.py` into `store.app_root` before loading; never load the original repository path, whose `_HERE` would address the working tree.

- [ ] **Step 1: Write failing tests that exercise original bodies.** Every test using an audit hook runs in a fresh subprocess because hooks cannot be removed. Use the following body for source-loading/storage characterization, with a bridge made of explicitly named no-op fixture functions, not a generic permissive mock:

```python
before = source.read_bytes()
original_open = __import__('builtins').open
with loaded_robot(source, store, bridge) as g:
    self.assertIs(g['log_event'].__globals__['open'].__self__, store)
    g['log_event']('info', 'fixture event')
    driver = FixtureDriver(store, png)
    g['DebugController'](driver, False)._dump_state('fixture')
    logical = store.app_root / 'screenshots' / 'Sample_receipt.png'
    self.assertTrue(g['_full_screenshot'](driver, str(logical)))
    self.assertTrue(store.exists(logical))
    self.assertTrue(store.text)
self.assertIs(__import__('builtins').open, original_open)
self.assertEqual(source.read_bytes(), before)
```

Also run the same `_full_screenshot` assertion with `fail_cdp=True`; assert `fallback_screenshot` appears in `driver.events`. When both CDP and fallback fail, assert false and no mapped image. For each child negative control, deliberately catch `PermissionError` and assert its audit violation still exists:

```python
attempts = install_audit(store.photo_root, read_roots)
try:
    with open(scratch / 'deleted-after-write.txt', 'w') as out:
        out.write('fixture')
except PermissionError:
    pass
self.assertTrue(any(a['kind'] == 'write_denied' for a in attempts))
self.assertFalse((scratch / 'deleted-after-write.txt').exists())
```

Use a socket object attempting a loopback connection as the network negative control; it must be denied before any connection is made. Also attempt `subprocess.Popen([sys.executable, '-c', 'pass'])` inside the guarded child and assert `process_denied`. Never use a real external host even for a negative test.

- [ ] **Step 2: Run the test module and confirm missing-module failure.**

```bash
"C:/Program Files/Python310/python.exe" -B -m unittest discover -s lab/tests -p test_online_probe.py -v
```

- [ ] **Step 3: Implement the loading seam and exact fixtures.** This implementation recipe is intentionally confined to the child:

```python
import contextlib
import os
import runpy
import sys
import types
from pathlib import Path

class FixtureForbidden(RuntimeError):
    pass

def forbidden(*args, **kwargs):
    raise FixtureForbidden('FIXTURE_OPERATION_FORBIDDEN')

@contextlib.contextmanager
def loaded_robot(source: Path, store, bridge):
    if source.resolve().parent != store.app_root:
        raise ValueError('SOURCE_NOT_COPIED')
    modules = {}
    def module(name, **attrs):
        value = types.ModuleType(name)
        value.__dict__.update(attrs)
        value.__path__ = []
        modules[name] = value
        return value
    module('app_paths', data_path=lambda name: str(store.app_root / name),
           data_subdir=lambda name: str(store.app_root / name))
    module('system_debug', configure=lambda service: None, event=lambda message: None)
    module('browser_visibility')
    module('captcha_collector', build_driver=forbidden,
           fire_refresh_events=lambda driver, element: element.click())
    module('live_solve', load_solver=forbidden)
    module('panel', **{name: getattr(bridge, name) for name in (
        'claim_profile', 'get_browser_visibility', 'release_profile',
        'record_profile_output', 'mark_profile_unusable',
        'heartbeat_profile', 'mark_profile_success')})
    for name in ('selenium', 'selenium.webdriver', 'selenium.webdriver.common',
                 'selenium.webdriver.support'):
        module(name)
    module('selenium.webdriver.common.by', By=types.SimpleNamespace(
        CSS_SELECTOR='css selector', XPATH='xpath', TAG_NAME='tag name'))
    module('selenium.webdriver.support.ui', WebDriverWait=forbidden)
    module('selenium.webdriver.support.expected_conditions')
    module('selenium.common')
    module('selenium.common.exceptions', StaleElementReferenceException=type(
        'StaleElementReferenceException', (Exception,), {}))
    saved = {name: sys.modules.get(name) for name in modules}
    saved_path = list(sys.path)
    sys.modules.update(modules)
    try:
        ns = runpy.run_path(str(source), init_globals={'open': store.open},
                            run_name='irica_lab_fixture')
        g = ns['log_event'].__globals__
        path_facade = types.SimpleNamespace(**{name: getattr(os.path, name)
            for name in ('join', 'dirname', 'abspath', 'basename', 'splitext')})
        path_facade.exists = store.exists
        g['os'] = types.SimpleNamespace(path=path_facade,
                                       makedirs=lambda path, exist_ok=False: store.key(path))
        g['time'] = types.SimpleNamespace(sleep=lambda seconds: None,
                 strftime=lambda pattern: '2000-01-01 00:00:00')
        g['_telegram_deliver'] = lambda kind, **kwargs: None
        g['_extract_payment_link'] = lambda driver: 'https://fixture.invalid/payment'
        yield g
    finally:
        sys.path[:] = saved_path
        for name, prior in saved.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior
```

Lazy `import panel` in functions happens **after** loading. The context keeps the exact fixture module map installed for all calls and restores it only after the case and heartbeat thread finish. Tests must join fixture threads before exiting `loaded_robot`; an unjoined thread blocks completion. Do not expose a bare loader or let lazy imports resolve to real `panel`. Preserve and restore `sys.path` because the unchanged module inserts its solver directory.

`FixtureDriver` must implement exactly these inert methods, with no real browser import: `execute_script` returns a fixed dict for debug inspection or `None`; `execute_cdp_cmd` returns base64 of the fixture PNG or raises the fixed fixture CDP failure; `save_screenshot` uses `store.open` and records `fallback_screenshot`; `find_elements` returns zero/one fixture payment button; the button's `get_attribute('onclick')` returns the fixed `.invalid` URL string and `click()` records `fixture_click`; `page_source` is `<html>fixture</html>` and `current_url` is the same fixed URL. No string from `gomrok.py` is evaluated. Telegram and payment-link helpers are **stubbed dependencies**, not exercised-original-function claims. All other unsupported attribute/method access raises `FIXTURE_OPERATION_FORBIDDEN`; never silently synthesize values.

Install the audit guard after scratch/code/module fixture setup and before loading/calling robot bodies. Set `sys.dont_write_bytecode = True` before imports. Reject network/process events and file writes outside the private photo root. Record denied attempts before raising, including `os.open` flag-based writes; ignore ordinary code reads only when under the copied source tree or explicit standard-library roots. Resolve paths, handle descriptor-based opens deliberately, reject symlinks/reparse paths and unknown descriptor writes, and do not record raw absolute paths. Each recorded event is `{kind, file, line}` where `file` is a relative code path or `<outside>` and `line` is a numeric callsite. Bound events at 4,096, preserve an overflow violation, and fail rather than truncate to a clean result.

Use this decision logic for audit `open` events (remaining listed event groups use the same record-before-raise pattern):

```python
if event == 'open':
    path, mode, flags = args
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    writing = bool((flags or 0) & write_flags) or bool(mode and any(c in mode for c in 'wax+'))
    if isinstance(path, int):
        if writing:
            deny('descriptor_write_denied')
    else:
        resolved = Path(path).resolve()
        if writing and not resolved.is_relative_to(photo_root):
            deny('write_denied')
        if not writing and not any(resolved.is_relative_to(root) for root in read_roots):
            deny('read_denied')
```

Here `deny(code)` appends a sanitized event then raises `PermissionError(code)`; define it inside `install_audit`. Deny `socket.connect`, `socket.getaddrinfo`, `socket.bind`, `subprocess.Popen`, `os.system` and `os.exec*`; record `network_denied`/`process_denied`. Record/reject mutations (`os.remove`, `os.rename`, `os.rmdir`, `os.mkdir`, link/symlink) outside scratch; photo file deletes/renames must also stay contained. Prepare directories before guarding. Ordinary directory creation is not image bytes but must not be used to escape containment. This is defense against fixture accidents, not a hostile-code/native I/O sandbox.

- [ ] **Step 4: Run the same test command.** Require original logger/debug/CDP/fallback success cases, both-screenshot-failure case, disk/network/process negative controls, unchanged source, exact fake-import lifetime, and restored global `open`/`sys.path` after context exit. If non-main import needs an unlisted dependency, add explicit inventory evidence and a bounded inert fixture; never import the real helper to make the test pass.
- [ ] **Step 5: Review the task and tag every dependency as fake versus original-body execution.** Actual helper persistence and real browser screenshot writes remain unresolved. Do not commit.

## Task 4: Characterize fencing, stale fallback and heartbeat limits

**Files:** Create `fencing.py`; test `test_online_fencing.py`. Consume Task 3's context-managed loader and fixture driver.

**Interfaces:** Produces `Lease(epoch: int, expires_at: float, active: bool = True)`, `Guard(lease: Lease, clock: Callable[[], float])`, `.dispatch(epoch: int, effect: Callable[[], object]) -> object`, `.publish(epoch: int, result: object) -> bool`, `.revoke() -> None`, `.uncertain: bool`, `.results: list`; and `FixtureBridge(guard: Guard, epoch: int)` exposing the seven exact panel helper callables from Task 3 plus `.fallback_attempts: int`. This is a protocol model only; no real WebDriver/submission interception is implemented.

- [ ] **Step 1: Write failing model tests.** The effects are Python list updates, never browser/JS/authentication actions.

```python
import unittest
from lab.online_feasibility.fencing import Guard, Lease

class FencingTests(unittest.TestCase):
    def test_revocation_prevents_dispatch(self):
        guard = Guard(Lease(7, 60), lambda: 10)
        calls = []
        guard.revoke()
        with self.assertRaisesRegex(RuntimeError, '^JOB_FENCED$'):
            guard.dispatch(7, lambda: calls.append('fixture action'))
        self.assertEqual(calls, [])

    def test_inflight_effect_is_uncertain_not_undone(self):
        guard = Guard(Lease(7, 60), lambda: 10)
        effects = []
        def effect():
            effects.append('already dispatched')
            guard.revoke()
            return 'late result'
        self.assertEqual(guard.dispatch(7, effect), 'late result')
        self.assertEqual(effects, ['already dispatched'])
        self.assertTrue(guard.uncertain)
        self.assertFalse(guard.publish(7, 'late result'))
        self.assertEqual(guard.results, [])

    def test_expiry_and_old_epoch_are_fenced(self):
        for lease, now, epoch in ((Lease(7, 10), 10, 7), (Lease(8, 60), 10, 7)):
            guard = Guard(lease, lambda: now)
            with self.assertRaisesRegex(RuntimeError, '^JOB_FENCED$'):
                guard.dispatch(epoch, lambda: None)
```

- [ ] **Step 2: Confirm missing-module failure.**

```bash
"C:/Program Files/Python310/python.exe" -B -m unittest discover -s lab/tests -p test_online_fencing.py -v
```

- [ ] **Step 3: Implement the model without claiming control of a real browser.**

```python
from dataclasses import dataclass

@dataclass
class Lease:
    epoch: int
    expires_at: float
    active: bool = True

class Guard:
    def __init__(self, lease, clock):
        self.lease, self.clock = lease, clock
        self.results, self.uncertain, self.inflight = [], False, False
    def valid(self, epoch):
        return (self.lease.active and epoch == self.lease.epoch and
                self.clock() < self.lease.expires_at)
    def dispatch(self, epoch, effect):
        if not self.valid(epoch):
            raise RuntimeError('JOB_FENCED')
        self.inflight = True
        try:
            return effect()
        finally:
            if not self.valid(epoch):
                self.uncertain = True
            self.inflight = False
    def publish(self, epoch, result):
        if not self.valid(epoch) or self.uncertain:
            return False
        self.results.append(result)
        return True
    def revoke(self):
        if self.inflight:
            self.uncertain = True
        self.lease.active = False

class FixtureBridge:
    def __init__(self, guard, epoch):
        self.guard, self.epoch, self.fallback_attempts = guard, epoch, 0
    def claim_profile(self, profile_id=None):
        return {'profile_id': 'fixture-profile', 'runner_id': 'fixture-runner'}
    def get_browser_visibility(self, profile_id):
        return True
    def release_profile(self, profile_id, runner_id):
        return self.guard.valid(self.epoch)
    def record_profile_output(self, profile_id, runner_id, receipt, payment, link):
        return self.guard.publish(self.epoch, {'kind': 'fixture-output'})
    def mark_profile_unusable(self, profile_id, error_text):
        return self.guard.publish(self.epoch, {'kind': 'fixture-unusable'})
    def heartbeat_profile(self, profile_id, runner_id):
        return self.guard.valid(self.epoch)
    def mark_profile_success(self, title, national, imei, link=''):
        self.fallback_attempts += 1
        return self.guard.publish(self.epoch, {'kind': 'fixture-success'})
```

This bridge intentionally has no host token or real authorization; it pins the fencing requirement. It does not prove a production bridge or lawful external execution. Make pending/terminal-result semantics explicit in later production planning; this lab is not a ledger or exactly-once implementation.

- [ ] **Step 4: Add original-handler regressions with explicit fixtures.** With `g` from `loaded_robot`, set `_CLAIMED_PROFILE_ID='fixture-profile'` and `_CLAIMED_RUNNER_ID='fixture-runner'`, revoke the guard, call `g['handle_already_registered'](driver, title='Sample', national='', imei='')`, then assert `bridge.fallback_attempts == 1` and `guard.results == []`. This asserts the original fallback actually ran but could not bypass fencing. Call `handle_receipt_and_payment` with a current guard and assert the fixture receipt/photo event precedes the payment event; then run with the revoked guard and assert no publication. Stubbed Telegram delivery records only photo/text role names in RAM, never captions or customer values.

For heartbeat characterization, create a fresh active guard, replace the fixture bridge's `heartbeat_profile` with a named function returning false, invoke the original `_start_profile_heartbeat`, join it with a one-second timeout, assert it is no longer alive, and assert the independent active guard still permits a synthetic list-update dispatch. The expected finding is **thread stopped, main cancellation unproven**, not that this behavior is safe. No fixed sleeps or actual main invocation.

Run dispatch/late-result tests for labels `element`, `helper`, `script`, `async_script`, `navigation` and `cdp` through the same inert effect. Labels describe modeled boundaries, not actual interception coverage. Add a two-step synthetic effect that revokes between steps yet performs its second list update; assert `uncertain` remains true and publication fails. That is the explicit asynchronous-in-flight counterexample.

- [ ] **Step 5: Re-run the same command plus Task 3 tests.** All model regressions should pass while the evidence report retains unresolved main-cancellation/in-flight-script gates. Review, record results and do not commit.

## Task 5: Aggregate a fail-closed report and repeatable CLI

**Files:** Create `report.py`, `lab/scripts/run_online_feasibility.py`; test `test_online_report.py`. Update the existing Phase A plan with observed evidence only after execution, without replacing baseline/source-review facts.

**Interfaces:** Produces `build_report(hashes_before: dict, hashes_after: dict, baseline: str, cases: list[dict]) -> dict`; CLI `--root PATH --scratch PATH` uses only approved source/scratch paths. It emits bounded sanitized JSON to stdout, exits `0` for a completed conditional laboratory report, `2` for BLOCKED, and `1` for a harness defect. Exit `0` never means production release approved.

Required cases: `non_main_load`, `memory_log_debug`, `cdp_image`, `fallback_image`, `image_failure`, `disk_negative`, `network_negative`, `process_negative`, `stale_fallback`, `heartbeat_limit`, `inflight_model`, `receipt_order`.

- [ ] **Step 1: Add failing report tests.**

```python
import unittest
from lab.online_feasibility.report import REQUIRED, EVIDENCE_BY_CASE, build_report

class ReportTests(unittest.TestCase):
    def cases(self):
        return [{'id': name, 'status': 'passed', 'evidence_kind': EVIDENCE_BY_CASE[name],
                 'violations': 0} for name in REQUIRED]
    def test_all_lab_cases_never_claim_production_pass(self):
        report = build_report({'gomrok.py': 'same'}, {'gomrok.py': 'same'},
                              'passed', self.cases())
        self.assertEqual(report['decision'], 'CONDITIONAL')
        self.assertFalse(report['full_release_ready'])
        self.assertIn('real_dependency_storage', report['unresolved'])
    def test_missing_or_blocked_baseline_blocks(self):
        for baseline, cases in (('blocked', self.cases()), ('passed', [])):
            report = build_report({'gomrok.py': 'same'}, {'gomrok.py': 'same'},
                                  baseline, cases)
            self.assertEqual(report['decision'], 'BLOCKED')
    def test_changed_source_or_swallowed_violation_blocks(self):
        cases = self.cases()
        cases[0]['violations'] = 1
        for after, observed in (({'gomrok.py': 'different'}, self.cases()),
                                ({'gomrok.py': 'same'}, cases)):
            report = build_report({'gomrok.py': 'same'}, after, 'passed', observed)
            self.assertEqual(report['decision'], 'BLOCKED')
```

Add strict-schema tests: duplicate case ID raises `CASE_ID_INVALID`; unknown status `skipped` yields BLOCKED; unknown keys or strings containing sample `fixture-secret` never enter the serialized report. Positive negative-controls declare expected denials separately so intended guard rejections are not counted as unexpected violations. A negative control passes only when the correct denial is observed; an ordinary case passes only with zero unexpected attempts.

- [ ] **Step 2: Confirm missing-module failure.**

```bash
"C:/Program Files/Python310/python.exe" -B -m unittest discover -s lab/tests -p test_online_report.py -v
```

- [ ] **Step 3: Implement strict aggregation.**

```python
REQUIRED = ('non_main_load', 'memory_log_debug', 'cdp_image', 'fallback_image',
            'image_failure', 'disk_negative', 'network_negative', 'process_negative',
            'stale_fallback', 'heartbeat_limit', 'inflight_model', 'receipt_order')
EVIDENCE_BY_CASE = {name: 'unchanged_body' for name in REQUIRED}
EVIDENCE_BY_CASE.update({name: 'negative_control' for name in (
    'disk_negative', 'network_negative', 'process_negative')})
EVIDENCE_BY_CASE['inflight_model'] = 'model'
UNRESOLVED = ('real_dependency_storage', 'production_bootstrap',
              'native_browser_storage', 'main_cancellation',
              'inflight_async_effects', 'external_reconciliation')

def build_report(hashes_before, hashes_after, baseline, cases):
    seen, clean = set(), []
    for case in cases:
        if set(case) != {'id', 'status', 'evidence_kind', 'violations'}:
            raise ValueError('CASE_SCHEMA_INVALID')
        if case['id'] not in REQUIRED or case['id'] in seen:
            raise ValueError('CASE_ID_INVALID')
        if case['evidence_kind'] != EVIDENCE_BY_CASE[case['id']]:
            raise ValueError('EVIDENCE_KIND_INVALID')
        if type(case['violations']) is not int or case['violations'] < 0:
            raise ValueError('VIOLATION_COUNT_INVALID')
        if case['status'] not in {'passed', 'failed', 'blocked'}:
            case = dict(case, status='blocked')
        seen.add(case['id'])
        clean.append(dict(case))
    blocked = (baseline != 'passed' or seen != set(REQUIRED) or
               hashes_before != hashes_after or not hashes_before or
               any(c['status'] != 'passed' or c['violations'] for c in clean))
    return {'version': 1, 'decision': 'BLOCKED' if blocked else 'CONDITIONAL',
            'full_release_ready': False, 'baseline': baseline if baseline in
            {'passed', 'failed', 'blocked'} else 'blocked',
            'cases': clean, 'unresolved': list(UNRESOLVED)}
```

Validate hash maps against the exact inventory file keys and lowercase 64-character hex values at the CLI boundary; assert the approved robot hash before starting. The unit-test `same` strings above are aggregation-only fixtures, not accepted CLI source hashes. Include reviewed relative-site coverage and missing-case lists in the completed report, and keep `hashes_before/after` plus exact command names as sanitized provenance. Invalid report fields fail closed; do not echo their contents in exception messages.

CLI execution recipe:

1. Resolve `--root`; require the approved robot hash and all inventory files. Require a newly created empty scratch directory outside operator data; reject symlinks/reparse paths and paths under `%LOCALAPPDATA%/irica`.
2. Record before hashes and inventory; copy only the selected source/code fixtures into scratch. No settings, user stores, proxy files or secrets. Verify copied robot bytes match.
3. Run each required case in a fresh Python `-B` child with a 20-second timeout and a fresh minimal Windows environment (the Task 1 allowlist, not inherited tokens/config). The child receives root/scratch/case ID only. Install the audit guard before non-main loading. Catch top-level fixture failure into fixed codes and return one bounded `CaseResult`, never captured legacy prints. Redirect legacy stdout/stderr to a capped 64 KiB memory sink **before** loading; retain only event roles/counts, discard contents after the case. A dedicated protocol pipe carries at most 16 KiB JSON per child. The parent reads with a hard cap, kills/waits on overflow or timeout, and marks the case failed; do not use unbounded `capture_output=True` for the probe. Kill/wait a timed-out child.
4. Invoke the baseline recipe only after selected legacy tests' safety review; parse runner success/counts, not an invented zero. If baseline prerequisites are absent, set `baseline='blocked'` and retain characterization results without green aggregate.
5. Rehash the original source and construct the report. Write its sanitized version in scratch only; include unresolved imported helpers, async action sites and not-run host checks explicitly. Finish with report exit code as specified above.

The case dispatcher is a fixed mapping, not `eval`, arbitrary module/function loading, arbitrary subprocess arguments or a generic Selenium adapter. Register the original-body test actions from Tasks 2-4 under the twelve exact IDs; no case may execute `main`. Do not evaluate embedded source strings. A full report can remain BLOCKED; that is valid diagnostic completion, not permission to weaken a requirement.

- [ ] **Step 4: Run report tests, then every new suite.**

```bash
"C:/Program Files/Python310/python.exe" -B -m unittest discover -s lab/tests -p 'test_online_*.py' -v
```

Expected: all new diagnostic tests run; any skipped or zero-test module must be explicitly BLOCKED by the orchestrator. Record the real count. The five-module test collection is not a substitute for twelve executed case records.

- [ ] **Step 5: Execute the complete harness, review evidence and run focused regressions.** Use an empty disposable scratch path chosen with `tempfile`, not an operator data directory; use that actual path in the CLI. Confirm an intentional skipped case and changed-source fixture produce BLOCKED, then execute the unchanged-source run. Re-run the focused legacy baseline in its snapshot. Compile only new modules with bytecode directed to scratch. Review `git diff --check`, repository status, exact new files, unchanged `gomrok.py` hash and attempted-write evidence. No browser preview is appropriate: these changes are tooling/tests and do not render the product.

- [ ] **Step 6: Fresh independent evidence review and handoff.** Reviewer checks source coverage, fake-versus-real labels, baseline claims, audit limits, caps and honest in-flight behavior. Reviewer sends no Telegram message and makes no production change. Advisor reviews the sanitized conclusions. The primary session sends the completed report file and one Telegram status report without secrets/PII. Do not commit/push/release unless newly authorized.

## Exit criteria and exact stop rules

- Every new test and case result actually ran; failures/missing prerequisites are preserved.
- Original source hashes match; no live data, product, host or installed-app changes.
- Mocked import/function/driver behavior is labeled separately from exercised original bodies.
- Memory sinks are bounded; only valid fixture PNG bytes are physically written by positive robot probes, under opaque scratch photo names.
- Negative controls observe denied attempts even when caught; no network/browser/driver-update action is performed.
- Original stale success fallback is characterized; synthetic fencing rejects it.
- Original heartbeat thread termination is not presented as main cancellation.
- In-flight synthetic effects remain uncertain and late results are not published.
- Complete evidence yields **CONDITIONAL**, not production PASS. Actual unsafe behavior, missing evidence, changed hashes or baseline failure yields **BLOCKED**.
- If immediate cessation of every already-dispatched page action is required, the unresolved async boundary is a **NO-GO** for that workflow. Do not silently weaken the immutable-source or storage requirement.
- Do not start the full host/client implementation from a conditional result. Present the exact dependency/cancellation/storage incompatibility and a separately reviewed realistic proof plan first.

## Planning review and execution boundary

The plan was grounded in the current repository and a completed Advisor consultation. Advisor agreed that fake imports and command-boundary models cannot justify production compatibility, and that immediate in-flight cessation would be a no-go without proof. The consultation did not execute this plan or independently inspect its eventual implementation.

The user approved Phase A with the recommended native sequential method on 2026-10-08. That approval does not authorize Phase B, production access, real data, migration, deployment, commit/push, release or installation. The recipes above remain the original TDD proposal; the execution record below supersedes their prospective status and records deviations rather than checking off code that differs from the implementation.

## Observed Phase A evidence (2026-10-08)

- The branch is `lab/online-feasibility-phase-a`; HEAD remains `08c344511d4f53ac19ad1cd3bbb445e342ddc942`. Changes are untracked laboratory modules/tests and planning documents only. No product file was edited.
- After the independent review fix, all 52 diagnostic tests passed in 140.423 seconds using `python -B -m unittest discover -s lab/tests -p 'test_online_*.py' -v`. This includes the actual twelve-case CLI integration, changed-source refusal, missing/skipped aggregation, output caps/timeouts, attempted-I/O negative controls, unjoined-heartbeat import lifetime and three image-audit regressions observed RED then GREEN. Compilation of 16 new Python files and whitespace/scope checks on 19 new files passed; exact current source and regenerated harness/report hashes match.
- The inventory covers 14 files, 6,879 calls and 306 imports. Heuristic counts: 208 I/O candidates, 1,387 action candidates, 9 process candidates and 5,275 unclassified calls. Unclassified calls are retained, not treated as safe. `get` and `replace` produce false-positive candidate classifications; alias names are not a resolved call graph.
- Manual header review found `panel.py:3460`'s `functools.wraps` decorator expression. Its visitor owner label is lexical, not proof of runtime-only execution. Imports alias `cc`, `bvis`, `EC`, `tg` and `profile_store`; the selected fixture seams handle only the explicitly declared dependencies.
- Exercised original bodies are `log_event`, `DebugController._dump_state`, `_full_screenshot`, `handle_already_registered`, `handle_receipt_and_payment` and `_start_profile_heartbeat`, only on selected branches with inert dependencies. Other direct text/HTML writes in `main`, input loading, real driver/PIL saves, launcher diagnostics, Telegram helper files, browser/native storage and solver/updater dependencies remain unresolved. No `main`, solver, embedded JavaScript or real WebDriver ran.
- Fixture non-image output stays in bounded RAM. Positive image probes use an independently checked exact 1x1 PNG and opaque private filenames. Audit hooks observe denied attempts even when exceptions are caught. Python audit is not an OS sandbox; startup imports, native I/O and same-user path races are not proved safe.
- The original nine-module baseline is **BLOCKED**. Token privacy has ignored secret prerequisites and zero unittest collection; runtime API uses unreviewed real dependency/bootstrap paths; auth hardening has optional Node/system-proxy behavior. They were not executed.
- The reviewed six-module candidate executes from a bounded allowlisted archive at the pinned baseline, with isolated Python startup, a minimal environment and workspace/no-egress guards. Reviewed `jalali.py` and `log_utils.py` were added to the private archive after actual missing-import denials, not by widening installation access. Latest core run: **85 tests, 10 errors, zero assertion failures/skips, 42 denied I/O events; BLOCKED**. `panel.py:637` uses `os.fdopen` with an integer descriptor whose audit event lacks an approved path. Descriptor denial is retained. This is a guarded-harness failure, not a reproduced product regression or a passed business baseline.
- Synthetic fencing rejects stale legacy success fallback and late publication. Heartbeat thread exit does not cancel the main worker. Already-dispatched model effects can continue after revocation and remain uncertain. Immediate cessation of every in-flight effect is unproved and remains a **NO-GO** requirement boundary.
- A fresh independent original-checkout review found no Critical issues, one Important image-audit exception gap and two Minor reporting/repeatability issues. The image gap was reproduced: a direct helper could write text under an opaque PNG name and delete it without an attempt record. The single fix pass now issues a thread-local, single-use exclusive-create permit only from the trusted fixed-PNG sink after byte/output checks, and denies every physical mutation. Direct text writes, overwrite/truncate/delete attempts and post-sink writes/output limits are covered by three RED-to-GREEN regressions. The full 52-test suite and regenerated twelve-case report passed their diagnostic checks. No re-review is claimed.
- Deferred minors: the CLI does not bind every current dependency/test to a reviewed candidate manifest, and `coverage.call_lines` lists static lexical candidates rather than measured executed call sites. No current drift was found by final checks or the reviewer; pinned historical baseline counts are not current-candidate prerequisite coverage. These limitations remain explicit and do not permit release.
- The final evidence Advisor consultation was denied by the permission policy; it was not retried through another tool. Earlier design/safety consultations and the completed independent repository review do not substitute for that final evidence gate. An auxiliary review whose worktree became unavailable supplied no findings and is not counted.

The aggregate decision is **BLOCKED**, with `full_release_ready=false`. Green diagnostic tests establish the lab's behavior only. This does not authorize the full online rewrite or weaken image-only storage, unchanged source, cancellation or baseline requirements.

## Follow-up blocker remediation (2026-10-09)

The user asked to continue Phase A and clear the remaining blockers. The scope stayed inside the laboratory: no product file, host service, live data, account, migration, commit, push, release or installation was touched. The2026-10-08 record above is retained unchanged as the historical result; the state below supersedes the specific facts it states about descriptor denial and the two deferred reporting minors.

- **Descriptor-based denial resolved without opening the guard.** `panel.py:637` reaches the store through `tempfile.mkstemp` plus `os.fdopen(fd, ...)`, which emits an `open` audit event carrying an integer descriptor and no path. The audit no longer denies integers blindly: on Windows each descriptor is resolved through its live OS handle (`msvcrt.get_osfhandle` + `GetFinalPathNameByHandleW`), must be a one-link regular file whose path passes ancestor reparse checks, and must still match the same identity when re-statted. Non-Windows, pipes, directories, closed/reused handles, descriptors whose current target was renamed or hard-linked outside the workspace, and every unresolvable case fail closed with the same sanitized attempt record. `os.truncate` and friends are checked against the descriptor's current OS target, not a captured string path. Two regressions were observed RED then GREEN: the allowed atomic write (`mkstemp` + `fdopen` + `fsync` + `os.replace`) and the truncation event label. Three further negative-control regressions — outside target, closed/pipe/reused handles, hard link — were already green under the old blanket integer denial and are retention guards, not failing-first evidence for the new allowance.
- **The reviewed six-module core now runs clean under the guard:** 85 tests, zero errors, zero failures, zero skips and zero denied I/O events; `core_status=passed`. The overall baseline is still **BLOCKED** because the original nine-module scope remains incomplete: `test_token_privacy` reads real `upstream_proxy.json` credentials and does not collect through unittest, `test_runtime_api` really launches a sleeping child and `taskkill`s it, and `test_auth_hardening` boots a real loopback HTTP server and optionally runs Node. None of these can run inside the no-network/no-native-prerequisite guard, and none were emulated or skipped to manufacture a pass. Guarded-clean evidence for six modules is not a business-baseline pass.
- **The CLI now binds current prerequisites to the pinned reviewed identity.** Before creating scratch or running a fixture, every inventory source, every pinned baseline source/test, the three excluded test modules and the imported `_GateHarness` module, plus reviewed `cloud_sync/*.py`, are compared against the pinned baseline with CRLF-to-LF normalization; exact bytes are also recorded. Missing or modified prerequisites abort with `CANDIDATE_PREREQUISITE_INVALID` before any execution, and identity is re-verified after. A modified `log_utils.py` is refused before scratch exists.
- **`coverage.call_lines` was replaced.** Each entry now carries `line_evidence=static_lexical_candidates`, `static_candidate_call_lines`, the fixture cases mapped to that owner and the branch labels the author declares those cases target, plus `fixture_branch_evidence=declared_case_mapping_not_traced` and a top-level `runtime_call_coverage_measured=false`. Neither the branch labels nor the case-to-owner mapping are measured: no line-level or branch-level execution trace is collected, and both facts are stated in the report rather than implied. - Verification: 57 diagnostic tests in 143.417 seconds, OK, after the fix pass below; the fresh independent reviewer separately reran the same suite at 140.278 seconds, OK. Regenerated twelve-case report: all cases passed, zero unexpected violations, candidate identity stable across 39 bound files, baseline evidence `blocked/passed` with 85/0/0/0/0.

Still blocked and not claimed: real helper/native browser storage, production bootstrap, actual main-worker cancellation, immediate cessation of already-dispatched asynchronous effects (NO-GO boundary), the three excluded legacy modules, and the final Advisor evidence consultation, which was denied and was not retried or replaced.
