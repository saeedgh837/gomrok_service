"""Bounded fixture-only sinks; never use these as a production image cache."""

import io
import os
import struct
import threading
import uuid
import zlib
from pathlib import Path


_image_open = threading.local()


def _consume_image_open(path, flags):
    if path != getattr(_image_open, 'path', None):
        return False
    _image_open.path = None
    required = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    return (flags & required == required and
            not flags & (os.O_RDWR | os.O_TRUNC | os.O_APPEND))


def fixture_png() -> bytes:
    def chunk(kind, data):
        return (struct.pack('!I', len(data)) + kind + data +
                struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff))
    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('!IIBBBBB', 1, 1, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b'\x00\x00\x00\x00\xff')) + chunk(b'IEND', b''))


def checked_path(path: Path) -> Path:
    candidate = Path(os.path.abspath(path))
    for part in (candidate, *candidate.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('PATH_REPARSE')
    return candidate.resolve()


class MemoryStore:
    def __init__(self, app_root: Path, photo_root: Path, png: bytes, limit: int = 1048576):
        if png != fixture_png():
            raise ValueError('FIXTURE_PNG_INVALID')
        if type(limit) is not int or not 0 < limit <= 1048576:
            raise ValueError('BUFFER_LIMIT_INVALID')
        self.app_root, self.photo_root = checked_path(app_root), checked_path(photo_root)
        self.png, self.limit = png, limit
        self.text, self.image_paths, self.violations = {}, {}, []
        self.retained_bytes, self.output_count = 0, 0
        self.photo_root.mkdir(parents=True, exist_ok=True)

    def reject(self, code):
        if len(self.violations) < 4096:
            self.violations.append(code)
        raise ValueError(code)

    def key(self, path):
        try:
            candidate = checked_path(Path(path))
        except (ValueError, TypeError, OSError):
            self.reject('PATH_ESCAPE')
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
            physical = checked_path(self.photo_root) / (uuid.uuid4().hex + '.png')
            _image_open.path = physical
            try:
                with physical.open('xb') as out:
                    out.write(value)
            finally:
                _image_open.path = None
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
        self.size = len(initial.encode('utf-8'))
        self.failed = False

    def write(self, value):
        size = len(value) if self.binary else len(value.encode('utf-8'))
        if self.size + size > self.store.limit:
            self.failed = True
            self.store.reject('BUFFER_LIMIT')
        count = self.buffer.write(value)
        self.size += size
        return count

    def __enter__(self):
        return self

    def __exit__(self, kind, value, trace):
        try:
            if kind is None and not self.failed:
                self.store.commit(self.key, self.binary, self.buffer.getvalue())
        finally:
            self.buffer.close()
        return False
