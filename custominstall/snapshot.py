# This file is a part of custom-install.
#
# Copyright (c) 2019 Ian Burgwin
# This file is licensed under The MIT License (MIT).

"""Edit a GodMode9 ci-manage export without touching the live HOME Menu."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from pyctr.crypto import CryptoEngine, load_seeddb

from . import titles as titles_mod
from .layout import (
    EMPTY_TITLE_ID,
    SAVEDATA_FOLDERS,
    SAVEDATA_POSITIONS,
    SAVEDATA_PRESENT,
    SAVEDATA_TITLES,
    LauncherData,
    LayoutError,
    SaveData,
    format_title_id,
    parse_title_id,
)
from .titles import InstalledTitle, KIND_BY_HIGH, save3ds_fuse_path

EXTDATA_BY_REGION = {
    'JPN': '00000082',
    'USA': '0000008f',
    'EUR': '00000098',
    'CHN': '000000a1',
    'KOR': '000000a9',
    'TWN': '000000b1',
}
SEED_IDS = 0x1000
SEED_DATA = 0x4E80
SEED_LIMIT = 2000
TICKET_HEADER = bytes.fromhex('0100000050030000')
TICKET_ID_OFFSET = 0x1DC


@dataclass
class ManagedTitle:
    title_id: str
    kind: str
    name: str
    slot: int | None
    folder: int | None

    def as_installed(self) -> InstalledTitle:
        return InstalledTitle(self.title_id, self.kind, self.name, self.slot, self.folder)


class Snapshot:
    def __init__(self, root: str, sd: str | None = None, extdata_id: str = '00000082', seeddb: str | None = None):
        self.root = Path(root)
        self.sd = str(Path(sd)) if sd else None
        self.extdata_id = extdata_id.lower()
        self.nand = self.root / 'nand'
        self.boot9 = self.nand / 'boot9.bin'
        self.movable = self.nand / 'movable.sed'
        self.otp = self.nand / 'otp.bin'
        self.home_save = self.nand / 'homemenu-save.bin'
        self.seed_save = self.nand / 'seed-save.bin'
        self.title_db = self.root / 'sd' / 'title.db'
        self.import_db = self.root / 'sd' / 'import.db'
        self.extdata = self.root / 'sd' / 'homemenu-extdata'
        self.ticket_dir = self.nand / 'tickets'
        self.ticket_remove_dir = self.nand / 'tickets-remove'
        for path in (self.boot9, self.movable, self.otp, self.home_save, self.seed_save, self.title_db, self.import_db):
            if not path.is_file():
                raise LayoutError(f'missing {path}')
        if not self.extdata.is_dir():
            raise LayoutError(f'missing {self.extdata}')
        crypto = CryptoEngine(boot9=str(self.boot9))
        crypto.setup_sd_key_from_file(str(self.movable))
        self.id0 = crypto.id0.hex()
        if seeddb:
            load_seeddb(seeddb)
        self.launcher = LauncherData(self._extract_bare(self.home_save, 'Launcher.dat'))
        self.savedata = SaveData(self._extract_savedata())
        self.seed_blob = bytearray(self._extract_bare(self.seed_save, 'SEEDDB'))
        self._names: dict[str, str] = {}

    def _fuse(self, args: list[str]) -> None:
        cmd = [save3ds_fuse_path(), '-b', str(self.boot9), '-m', str(self.movable), *args]
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
        if out.returncode:
            raise LayoutError(out.stdout.strip() or f'save3ds_fuse failed ({out.returncode})')

    def _extract_bare(self, container: Path, name: str) -> bytes:
        with TemporaryDirectory(prefix='ci-bare-') as temp:
            self._fuse(['--bare', str(container), '-x', temp])
            path = Path(temp) / name
            if not path.is_file():
                raise LayoutError(f'{name} was not found in {container.name}')
            return path.read_bytes()

    def _fake_sd(self, temp: Path) -> Path:
        id1 = '0' * 32
        base = temp / 'Nintendo 3DS' / self.id0 / id1
        (base / 'dbs').mkdir(parents=True)
        shutil.copy(self.title_db, base / 'dbs' / 'title.db')
        shutil.copy(self.import_db, base / 'dbs' / 'import.db')
        ext = base / 'extdata' / '00000000' / self.extdata_id
        shutil.copytree(self.extdata, ext)
        return temp

    def _extract_savedata(self) -> bytes:
        with TemporaryDirectory(prefix='ci-ext-') as temp:
            root = self._fake_sd(Path(temp))
            plain = Path(temp) / 'plain'
            self._fuse(['--sd', str(root), '--sdext', self.extdata_id, '-x', str(plain)])
            path = plain / 'user' / 'SaveData.dat'
            if not path.is_file():
                raise LayoutError('SaveData.dat was not found in the exported extdata')
            return path.read_bytes()

    def _write_savedata(self) -> None:
        with TemporaryDirectory(prefix='ci-ext-') as temp:
            root = self._fake_sd(Path(temp))
            plain = Path(temp) / 'plain'
            self._fuse(['--sd', str(root), '--sdext', self.extdata_id, '-x', str(plain)])
            (plain / 'user' / 'SaveData.dat').write_bytes(self.savedata.blob)
            self._fuse(['--sd', str(root), '--sdext', self.extdata_id, '-i', str(plain)])
            src = Path(temp) / 'Nintendo 3DS' / self.id0 / ('0' * 32) / 'extdata' / '00000000' / self.extdata_id
            shutil.rmtree(self.extdata)
            shutil.copytree(src, self.extdata)

    def seed_count(self) -> int:
        return int.from_bytes(self.seed_blob[4:8], 'little')

    def add_seed(self, title_id: int, seed: bytes) -> None:
        update_seeddb(self.seed_blob, title_id, seed)

    def title_ids(self) -> list[str]:
        with TemporaryDirectory(prefix='ci-tdb-') as temp:
            root = self._fake_sd(Path(temp))
            plain = Path(temp) / 'plain'
            self._fuse(['--sd', str(root), '--db', 'sdtitle', '-x', str(plain)])
            return sorted(path.name.lower() for path in plain.iterdir() if path.is_file() and len(path.name) == 16)

    def _load_names(self, ids: list[str]) -> None:
        if not self.sd:
            return
        try:
            manager = titles_mod.TitleManager(self.sd, str(self.movable), str(self.boot9))
        except LayoutError:
            return
        for title_id in ids:
            if title_id[0:8] != '00040000':
                continue
            self._names[title_id] = manager._read_name(title_id)

    def list_titles(self, read_names: bool = True) -> list[ManagedTitle]:
        ids = self.title_ids()
        if read_names:
            self._load_names(ids)
        by_id = {format_title_id(entry.title_id): entry for entry in self.savedata.entries()}
        rows = []
        for title_id in ids:
            kind = KIND_BY_HIGH.get(title_id[:8], 'other')
            entry = by_id.get(title_id)
            slot = entry.slot if entry else None
            folder = entry.folder if entry else None
            rows.append(ManagedTitle(title_id, kind, self._names.get(title_id, ''), slot, folder))
        return rows

    def folders(self):
        return self.launcher.folders()

    def move(self, title_id: str, slot: int, folder: int | None = None) -> None:
        tid = parse_title_id(title_id)
        current = self.savedata.find(tid)
        use_folder = folder if folder is not None else (current.folder if current else -1)
        self.savedata.set_icon(tid, slot, use_folder)

    def sort_apps(self, by: str) -> None:
        if by not in ('name', 'id'):
            raise LayoutError('--by must be name or id')
        rows = [row for row in self.list_titles(read_names=(by == 'name')) if row.kind == 'app']
        groups: dict[int, list[ManagedTitle]] = {}
        for row in rows:
            groups.setdefault(-1 if row.folder is None else row.folder, []).append(row)
        updates = []
        for folder, items in groups.items():
            if by == 'name':
                items.sort(key=lambda item: (item.name.lower(), item.title_id))
            else:
                items.sort(key=lambda item: item.title_id)
            used = sorted({item.slot for item in items if item.slot is not None})
            cursor = 0
            while len(used) < len(items):
                if cursor not in used:
                    used.append(cursor)
                cursor += 1
            used.sort()
            for item, slot in zip(items, used):
                updates.append((parse_title_id(item.title_id), slot, folder))
        self.savedata.apply_slots(updates)

    def create_folder(self, name: str, slot: int | None = None):
        return self.launcher.create_folder(name, slot)

    def rename_folder(self, index: int, name: str) -> None:
        self.launcher.rename_folder(index, name)

    def _backup(self) -> Path:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        dest = self.root / f'backup-{stamp}'
        dest.mkdir()
        shutil.copy(self.home_save, dest / 'homemenu-save.bin')
        shutil.copy(self.seed_save, dest / 'seed-save.bin')
        shutil.copytree(self.extdata, dest / 'homemenu-extdata')
        return dest

    def save(self) -> Path:
        backup = self._backup()
        self._write_savedata()
        (self.nand / 'Launcher.dat').write_bytes(self.launcher.blob)
        (self.nand / 'SEEDDB').write_bytes(self.seed_blob)
        return backup

    def _write_stub_ticket(self, title_id: str) -> None:
        template = Path(__file__).resolve().parents[1] / 'finalize' / 'data' / 'basetik.bin'
        if not template.is_file():
            raise LayoutError(f'missing ticket template {template}')
        body = bytearray(template.read_bytes())
        if len(body) != 0x350:
            raise LayoutError(f'basetik.bin must be 0x350 bytes, got {len(body):#x}')
        body[TICKET_ID_OFFSET:TICKET_ID_OFFSET + 8] = bytes.fromhex(title_id)
        self.ticket_dir.mkdir(exist_ok=True)
        (self.ticket_dir / title_id).write_bytes(TICKET_HEADER + bytes(body))
        removed = self.ticket_remove_dir / title_id
        if removed.exists():
            removed.unlink()

    def _queue_ticket_removal(self, title_id: str) -> None:
        self.ticket_remove_dir.mkdir(exist_ok=True)
        (self.ticket_remove_dir / title_id).write_bytes(b'')
        added = self.ticket_dir / title_id
        if added.exists():
            added.unlink()

    def _sync_databases_from_sd(self) -> None:
        if not self.sd:
            raise LayoutError('SD root is required to add or remove titles')
        manager = titles_mod.TitleManager(self.sd, str(self.movable), str(self.boot9))
        id1 = manager.id1_root()
        shutil.copy(id1 / 'dbs' / 'title.db', self.title_db)
        shutil.copy(id1 / 'dbs' / 'import.db', self.import_db)

    def _next_main_slot(self) -> int:
        used = {entry.slot for entry in self.savedata.entries() if entry.folder == -1}
        slot = 0
        while slot in used:
            slot += 1
        if slot >= 360:
            raise LayoutError('the main menu icon list is full')
        return slot

    def add_cias(self, paths: list[str], seeddb: str | None = None) -> list[str]:
        from .__main__ import CustomInstall
        installer = CustomInstall(movable=str(self.movable), sd=self.sd, boot9=str(self.boot9),
                                   seeddb=seeddb, skip_contents=False)
        installer.prepare_titles(paths)
        result = installer.start()
        if result is None:
            raise LayoutError('install did not start')
        _state, _success, _apps = result
        self._sync_databases_from_sd()
        cifinish = Path(self.sd) / 'cifinish.bin'
        installed = []
        if cifinish.is_file():
            from .__main__ import load_cifinish
            for title_id, info in load_cifinish(cifinish).items():
                text = format_title_id(title_id)
                installed.append(text)
                if text.startswith('00040000') and self.savedata.find(title_id) is None:
                    self.move(text, self._next_main_slot(), -1)
                self._write_stub_ticket(text)
                seed = info.get('seed') if isinstance(info, dict) else None
                if seed:
                    self.add_seed(title_id, seed)
        return installed

    def remove(self, title_id: str) -> None:
        tid = parse_title_id(title_id)
        text = format_title_id(tid)
        if not self.sd:
            raise LayoutError('SD root is required to remove a title')
        manager = titles_mod.TitleManager(self.sd, str(self.movable), str(self.boot9))
        id1 = manager.id1_root()
        content = id1 / 'title' / text[:8] / text[8:]
        if content.is_dir():
            shutil.rmtree(content)
        self._remove_from_database(text)
        self._sync_databases_from_sd()
        # Drop the icon. Leaving the title id out of the repacked SaveData is enough.
        self._clear_icon(tid)
        self._queue_ticket_removal(text)

    def _clear_icon(self, title_id: int) -> None:
        entry = self.savedata.find(title_id)
        if entry is None:
            return
        import struct
        struct.pack_into('<Q', self.savedata.blob, SAVEDATA_TITLES + entry.index * 8, EMPTY_TITLE_ID)
        self.savedata.blob[SAVEDATA_PRESENT + entry.index] = 0
        struct.pack_into('<h', self.savedata.blob, SAVEDATA_POSITIONS + entry.index * 2, -1)
        struct.pack_into('<b', self.savedata.blob, SAVEDATA_FOLDERS + entry.index, -1)

    def _remove_from_database(self, title_id: str) -> None:
        """Remove the title from the live SD title.db, which is then copied back."""
        manager = titles_mod.TitleManager(self.sd, str(self.movable), str(self.boot9))
        id1 = manager.id1_root()
        with TemporaryDirectory(prefix='ci-tdb-') as temp:
            plain = Path(temp) / 'plain'
            cmd_base = ['--sd', self.sd, '--db', 'sdtitle']
            self._fuse([*cmd_base, '-x', str(plain)])
            target = plain / title_id
            if target.is_file():
                target.unlink()
            self._fuse([*cmd_base, '-i', str(plain)])
            import_plain = Path(temp) / 'import'
            self._fuse(['--sd', self.sd, '--db', 'sdimport', '-x', str(import_plain)])
            imported = import_plain / title_id
            if imported.is_file():
                imported.unlink()
            self._fuse(['--sd', self.sd, '--db', 'sdimport', '-i', str(import_plain)])
        # id1 is used so a missing SD fails before the database edit
        if not id1.is_dir():
            raise LayoutError(f'missing {id1}')


def update_seeddb(blob: bytearray, title_id: int, seed: bytes) -> None:
    if len(seed) != 16:
        raise LayoutError('seed must be 16 bytes')
    count = int.from_bytes(blob[4:8], 'little')
    for index in range(count):
        existing = int.from_bytes(blob[SEED_IDS + index * 8:SEED_IDS + index * 8 + 8], 'little')
        if existing == title_id:
            start = SEED_DATA + index * 16
            blob[start:start + 16] = seed
            return
    if count >= SEED_LIMIT:
        raise LayoutError('SEEDDB is full')
    blob[SEED_IDS + count * 8:SEED_IDS + count * 8 + 8] = title_id.to_bytes(8, 'little')
    start = SEED_DATA + count * 16
    blob[start:start + 16] = seed
    blob[4:8] = (count + 1).to_bytes(4, 'little')


def kind_of(title_id: str) -> str:
    return KIND_BY_HIGH.get(title_id[:8], 'other')
