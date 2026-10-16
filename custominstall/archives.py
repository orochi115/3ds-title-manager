# This file is a part of custom-install.
#
# Copyright (c) 2019 Ian Burgwin
# This file is licensed under The MIT License (MIT).

"""Expand zip, 7z, and rar files to the CIA files inside them.

The format comes from the file header. A .7z name may still be a rar archive.
patool and rarfile are tried first. If they fail, zip and 7z are read in-process.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from .layout import LayoutError

_ZIP = (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')
_7Z = b'7z\xbc\xaf\x27\x1c'
_RAR = b'Rar!\x1a\x07'
_CIA = b'\x20\x20\x00\x00'


def archive_password() -> str:
    return os.environ.get('CUSTOMINSTALL_ARCHIVE_PASSWORD', '')


def is_archive(path: str) -> bool:
    header = _header(path, 8)
    return header.startswith(_ZIP) or header.startswith(_7Z) or header.startswith(_RAR)


def is_cia(path: str) -> bool:
    file = Path(path)
    if file.suffix.lower() == '.cia':
        return True
    return file.is_file() and file.stat().st_size >= 0x2020 and _header(path, 4) == _CIA


class ExtractedCias:
    """CIA paths from a selection, with a temp directory for anything extracted."""

    def __init__(self, paths: list[str]):
        self._temp = tempfile.TemporaryDirectory(prefix='custom-install-cia-')
        self.paths = _expand(paths, Path(self._temp.name))

    def __enter__(self) -> list[str]:
        return self.paths

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._temp.cleanup()


def _expand(paths: list[str], root: Path) -> list[str]:
    cias: list[str] = []
    pending = [str(path) for path in paths]
    seen: set[str] = set()
    generation = 0
    while pending:
        generation += 1
        if generation > 4:
            raise LayoutError('archive nesting is deeper than 4 levels')
        next_archives: list[str] = []
        for path in pending:
            key = str(Path(path).resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            if is_cia(path) and not is_archive(path):
                cias.append(path)
                continue
            if not is_archive(path):
                raise LayoutError(f'{path} is not a CIA, zip, 7z, or rar file')
            folder = root / f'a{len(seen)}'
            folder.mkdir()
            _extract(path, folder)
            for child in folder.rglob('*'):
                if not child.is_file():
                    continue
                if is_archive(child):
                    next_archives.append(str(child))
                elif is_cia(child):
                    cias.append(str(child))
        pending = next_archives
    if not cias:
        raise LayoutError('the archive does not contain a CIA')
    return cias


def _extract(path: str, dest: Path) -> None:
    header = _header(path, 8)
    if not (header.startswith(_ZIP) or header.startswith(_7Z) or header.startswith(_RAR)):
        raise LayoutError(f'{Path(path).name} is not a zip, 7z, or rar file')
    password = archive_password()
    errors = []
    for extractor in (_extract_patool, _extract_rarfile, _extract_in_process):
        try:
            extractor(path, dest, password, header)
            return
        except Exception as exc:
            errors.append(f'{extractor.__name__}: {exc}')
            _clear(dest)
    raise LayoutError(f'could not extract {Path(path).name}: {errors[-1]}')


def _extract_patool(path: str, dest: Path, password: str, header: bytes) -> None:
    import patoolib
    options = {'outdir': str(dest), 'verbosity': -1, 'interactive': False}
    try:
        patoolib.extract_archive(path, password=password or None, **options)
    except TypeError:
        patoolib.extract_archive(path, **options)


def _extract_rarfile(path: str, dest: Path, password: str, header: bytes) -> None:
    if not header.startswith(_RAR):
        raise LayoutError('not a rar archive')
    import rarfile
    tool = shutil.which('unrar') or shutil.which('unar') or shutil.which('bsdtar')
    if not tool:
        raise LayoutError('UnRAR, unar, or bsdtar is not on PATH')
    rarfile.UNRAR_TOOL = tool
    with rarfile.RarFile(path) as archive:
        if password:
            archive.setpassword(password)
        archive.extractall(dest)


def _extract_in_process(path: str, dest: Path, password: str, header: bytes) -> None:
    if header.startswith(_ZIP):
        _extract_zip(path, dest, password)
    elif header.startswith(_7Z):
        _extract_7z(path, dest, password)
    else:
        raise LayoutError('no in-process reader for this rar archive')


def _clear(dest: Path) -> None:
    for child in dest.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _extract_zip(path: str, dest: Path, password: str) -> None:
    """Read a zip in-process. pyzipper covers AES passwords; zipfile covers the rest."""
    try:
        import pyzipper
        opener = pyzipper.AESZipFile
    except ImportError:
        opener = zipfile.ZipFile
    with opener(path) as archive:
        if password:
            archive.setpassword(password.encode('utf-8'))
        archive.extractall(dest)


def _extract_7z(path: str, dest: Path, password: str) -> None:
    try:
        import py7zr
    except ImportError as exc:
        raise LayoutError('py7zr is required to read 7z archives') from exc
    with py7zr.SevenZipFile(path, mode='r', password=password or None) as archive:
        archive.extractall(path=dest)


def _header(path: str, size: int) -> bytes:
    with open(path, 'rb') as handle:
        return handle.read(size)
