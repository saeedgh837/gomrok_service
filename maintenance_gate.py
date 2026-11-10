import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading


class MaintenanceBlocked(RuntimeError):
    pass


class _Windows:
    WAIT_OBJECT_0 = 0
    WAIT_ABANDONED = 128
    WAIT_TIMEOUT = 258
    SYNCHRONIZE = 0x100000
    QUERY_PROCESS = 0x1000

    def __init__(self):
        if os.name != "nt":
            raise MaintenanceBlocked("Automatic installation requires Windows")
        self.dll = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateMutexW": ([ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "ReleaseMutex": ([wintypes.HANDLE], wintypes.BOOL),
            "CreateEventW": ([ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "OpenEventW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetEvent": ([wintypes.HANDLE], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "GetProcessTimes": ([wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
            "QueryFullProcessImageNameW": ([wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                            ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes = arguments
            function.restype = result

    def process(self, pid):
        handle = self.dll.OpenProcess(self.QUERY_PROCESS | self.SYNCHRONIZE, False, pid)
        if not handle:
            if ctypes.get_last_error() in (87, 1168):
                return None
            raise MaintenanceBlocked("Cannot verify an active process")
        try:
            if self.dll.WaitForSingleObject(handle, 0) == self.WAIT_OBJECT_0:
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not self.dll.GetProcessTimes(handle, *[ctypes.byref(value) for value in times]):
                raise MaintenanceBlocked("Cannot verify process identity")
            size = wintypes.DWORD(32768)
            executable = ctypes.create_unicode_buffer(size.value)
            if not self.dll.QueryFullProcessImageNameW(handle, 0, executable, ctypes.byref(size)):
                raise MaintenanceBlocked("Cannot verify process location")
            created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return {"pid": pid, "created": created, "executable": executable.value}
        finally:
            self.dll.CloseHandle(handle)

    def python_processes(self):
        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
            ]
        create = self.dll.CreateToolhelp32Snapshot
        create.argtypes, create.restype = [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE
        first, next_entry = self.dll.Process32FirstW, self.dll.Process32NextW
        for function in (first, next_entry):
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
            function.restype = wintypes.BOOL
        snapshot = create(2, 0)
        if snapshot in (None, ctypes.c_void_p(-1).value):
            raise MaintenanceBlocked("Cannot inspect active jobs")
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = first(snapshot, ctypes.byref(entry))
            while found:
                if entry.szExeFile.lower() in ("python.exe", "pythonw.exe"):
                    yield int(entry.th32ProcessID)
                found = next_entry(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:
                raise MaintenanceBlocked("Cannot inspect all active jobs")
        finally:
            self.dll.CloseHandle(snapshot)


class InstallLease:
    def __init__(self, api, handle, event_name):
        self._api, self._handle = api, handle
        self.event_name = event_name

    def close(self):
        if self._handle:
            self._api.dll.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class MaintenanceGate:
    def __init__(self, *, install_dir, registry_path):
        self.install_dir = os.path.normcase(os.path.realpath(install_dir))
        self.registry_path = os.path.abspath(registry_path)
        identity = hashlib.sha256(self.install_dir.encode("utf-8")).hexdigest()[:24]
        self.prefix = "Local\\IRICA_" + identity
        self.event_name = self.prefix + "_install"
        self.mutex_name = self.prefix + "_admission"
        self._api = _Windows()

    @contextmanager
    def _mutex(self):
        handle = self._api.dll.CreateMutexW(None, False, self.mutex_name)
        if not handle:
            raise MaintenanceBlocked("Cannot acquire the update admission guard")
        acquired = False
        try:
            result = self._api.dll.WaitForSingleObject(handle, 5000)
            acquired = result in (self._api.WAIT_OBJECT_0, self._api.WAIT_ABANDONED)
            if not acquired:
                raise MaintenanceBlocked("The update admission guard is busy")
            yield
        finally:
            if acquired:
                self._api.dll.ReleaseMutex(handle)
            self._api.dll.CloseHandle(handle)

    def _assert_open(self):
        handle = self._api.dll.OpenEventW(self._api.SYNCHRONIZE, False, self.event_name)
        if not handle:
            if ctypes.get_last_error() == 2:
                return
            raise MaintenanceBlocked("Cannot verify the update admission guard")
        try:
            if self._api.dll.WaitForSingleObject(handle, 0) != self._api.WAIT_TIMEOUT:
                raise MaintenanceBlocked("An update is in progress; new jobs are paused")
        finally:
            self._api.dll.CloseHandle(handle)

    def assert_admission_open(self):
        with self._mutex():
            self._assert_open()

    @contextmanager
    def admit(self):
        with self._mutex():
            self._assert_open()
            yield

    def _read_jobs(self):
        path = Path(self.registry_path)
        if not path.exists():
            return []
        try:
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("size")
            document = json.loads(path.read_text(encoding="utf-8"))
            jobs = document["jobs"]
            if document.get("version") != 1 or not isinstance(jobs, list) or len(jobs) > 2048:
                raise ValueError("shape")
            for job in jobs:
                if (not isinstance(job, dict) or type(job.get("pid")) is not int
                        or job["pid"] <= 0 or type(job.get("created")) is not int
                        or job["created"] <= 0):
                    raise ValueError("identity")
            return jobs
        except (OSError, ValueError, KeyError, TypeError):
            raise MaintenanceBlocked("The active-job registry is unreadable") from None

    def _write_jobs(self, jobs):
        directory = os.path.dirname(self.registry_path)
        os.makedirs(directory, exist_ok=True)
        staged = None
        try:
            handle, staged = tempfile.mkstemp(prefix=".jobs-", dir=directory)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "jobs": jobs}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, self.registry_path)
            staged = None
        finally:
            if staged:
                os.unlink(staged)

    def register(self, pid):
        with self.admit():
            process = self._api.process(int(pid))
            if process is None:
                return
            jobs = [job for job in self._read_jobs() if job["pid"] != pid]
            jobs.append({"pid": process["pid"], "created": process["created"]})
            self._write_jobs(jobs)
            return process["created"]

    def unregister(self, pid, *, expected_created):
        with self._mutex():
            jobs = self._read_jobs()
            kept = [job for job in jobs if (job["pid"], job["created"])
                    != (pid, expected_created)]
            if len(kept) != len(jobs):
                self._write_jobs(kept)

    def _running_jobs(self, excluded):
        jobs, live = self._read_jobs(), {}
        kept = []
        for job in jobs:
            process = self._api.process(job["pid"])
            if process and process["created"] == job["created"]:
                kept.append(job)
                if job["pid"] not in excluded:
                    live[job["pid"]] = process
        if kept != jobs:
            self._write_jobs(kept)
        for pid in self._api.python_processes():
            if pid in excluded or pid in live:
                continue
            process = self._api.process(pid)
            if process is None:
                continue
            try:
                in_install = os.path.commonpath([
                    self.install_dir, os.path.normcase(os.path.realpath(process["executable"]))
                ]) == self.install_dir
            except ValueError:
                in_install = False
            if in_install:
                live[pid] = process
        return list(live.values())

    def running_jobs(self, *, exclude_pids=()):
        with self._mutex():
            return self._running_jobs(set(exclude_pids))

    def begin_install(self, *, exclude_pids=()):
        with self._mutex():
            self._assert_open()
            if self._running_jobs(set(exclude_pids)):
                raise MaintenanceBlocked("Installation is waiting for active jobs to finish")
            handle = self._api.dll.CreateEventW(None, True, True, self.event_name)
            if not handle:
                raise MaintenanceBlocked("Cannot close admission for the update")
            if not self._api.dll.SetEvent(handle):
                self._api.dll.CloseHandle(handle)
                raise MaintenanceBlocked("Cannot close admission for the update")
            return InstallLease(self._api, handle, self.event_name)


_JOB_LIFETIME_LOCK = threading.RLock()
_JOB_LIFETIMES = {}


def _job_domain(gate, pid):
    """Separate installations and fixture gates, without environment sentinels."""
    install_dir = getattr(gate, "install_dir", None)
    registry_path = getattr(gate, "registry_path", None)
    if isinstance(install_dir, str) and isinstance(registry_path, str):
        return (pid, os.path.normcase(os.path.realpath(install_dir)),
                os.path.normcase(os.path.realpath(registry_path)))
    return (pid, id(gate))


@contextmanager
def job_lifetime(*, gate=None):
    """Admit and register this root job until the outermost owner exits.

    Launcher and direct entry points share one PID/creation registration. A
    nested direct entry still checks installation admission, but cannot remove
    its live launcher's registration when it returns. The process-local count
    is synchronized across threads and keyed by the actual process and domain.
    """
    gate = default_gate() if gate is None else gate
    pid = os.getpid()
    key = _job_domain(gate, pid)
    with _JOB_LIFETIME_LOCK:
        with gate.admit():
            owner = _JOB_LIFETIMES.get(key)
            if owner is None:
                created = gate.register(pid)
                if type(created) is not int or created <= 0:
                    raise MaintenanceBlocked("Cannot register this job's exact process identity")
                owner = {"gate": gate, "created": created, "depth": 0}
                _JOB_LIFETIMES[key] = owner
            owner["depth"] += 1
    try:
        yield owner["created"]
    finally:
        with _JOB_LIFETIME_LOCK:
            owner["depth"] -= 1
            if owner["depth"] == 0:
                try:
                    owner["gate"].unregister(pid, expected_created=owner["created"])
                finally:
                    _JOB_LIFETIMES.pop(key, None)


def run_job(work, /, *args, **kwargs):
    """Run only admitted root work; preserve its result and exact exceptions."""
    with job_lifetime():
        return work(*args, **kwargs)


def default_gate():
    import app_paths
    install_dir = os.path.dirname(os.path.abspath(__file__))
    identity = hashlib.sha256(os.path.normcase(os.path.realpath(install_dir)).encode("utf-8")).hexdigest()[:24]
    return MaintenanceGate(
        install_dir=install_dir,
        registry_path=app_paths.data_path("maintenance_jobs_" + identity + ".json"),
    )
