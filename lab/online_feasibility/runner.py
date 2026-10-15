"""Bounded subprocess protocol and private laboratory path validation."""

import os
import re
import subprocess
import threading
from pathlib import Path

from lab.online_feasibility.inventory import APPROVED_ROBOT, FILES
from lab.online_feasibility.storage import checked_path


def validate_hashes(hashes):
    if (not isinstance(hashes, dict) or set(hashes) != set(FILES) or
            any(not isinstance(h, str) or re.fullmatch('[a-f0-9]{64}', h) is None
                for h in hashes.values()) or hashes['gomrok.py'] != APPROVED_ROBOT):
        raise ValueError('SOURCE_HASH_INVALID')


def validate_scratch(scratch, root):
    try:
        scratch, root = checked_path(scratch), checked_path(root)
        forbidden = [root]
        for key in ('LOCALAPPDATA', 'APPDATA'):
            if os.environ.get(key):
                forbidden.append(checked_path(Path(os.environ[key]) / 'irica'))
        if os.environ.get('IRICA_DATA_DIR'):
            forbidden.append(checked_path(Path(os.environ['IRICA_DATA_DIR'])))
        if (any(scratch.is_relative_to(path) or path.is_relative_to(scratch)
                for path in forbidden) or
                scratch.exists() and (not scratch.is_dir() or any(scratch.iterdir()))):
            raise ValueError('SCRATCH_INVALID')
    except (OSError, ValueError):
        raise ValueError('SCRATCH_INVALID') from None
    return scratch


def minimal_env(scratch):
    env = {key: os.environ[key] for key in
           ('SystemRoot', 'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'COMSPEC')
           if key in os.environ}
    env.update(TEMP=str(scratch), TMP=str(scratch), IRICA_DATA_DIR=str(scratch / 'data'),
               PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    return env


def bounded_child(command, cwd, env, timeout=20, cap=16384):
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    data, overflow = bytearray(), threading.Event()

    def drain():
        while True:
            block = process.stdout.read(1024)
            if not block:
                break
            if len(data) + len(block) > cap:
                overflow.set()
                process.kill()
                break
            data.extend(block)

    reader = threading.Thread(target=drain, name='lab-protocol-reader', daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait(timeout=5)
    reader.join(timeout=5)
    process.stdout.close()
    if reader.is_alive() or timed_out or overflow.is_set() or process.returncode != 0:
        return None
    return bytes(data)
