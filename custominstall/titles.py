# This file is a part of custom-install.
#
# Copyright (c) 2019 Ian Burgwin
# This file is licensed under The MIT License (MIT).

"""List SD titles and edit Home Menu icon layout."""

from __future__ import annotations

import json
import shutil
import subprocess
from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import datetime
from os import environ
from pathlib import Path
from shutil import which
from sys import executable, platform
from tempfile import TemporaryDirectory
from typing import Iterable

from pyctr.crypto import CryptoEngine, load_seeddb
from pyctr.type.sd import SDFilesystem

from . import __version__
from .layout import (
    FOLDER_COUNT,
    LauncherData,
    LayoutError,
    SaveData,
    format_title_id,
    parse_title_id,
)

if platform == 'msys':
    _platform = 'win32'
else:
    _platform = platform

is_windows = _platform == 'win32'

HOME_MENU_EXTDATA = ('00000082', '0000008f', '00000098')
APP_HIGH = '00040000'
KIND_BY_HIGH = {
    '00040000': 'app',
    '0004000e': 'update',
    '0004008c': 'dlc',
    '00040010': 'system',
    '00040030': 'applet',
    '00048004': 'dsiware',
    '00048005': 'dsiware',
}

# Names for NAND system titles. The eShop 3dsdb files do not list these.
# Low 32 bits differ by region; the high half is 00040010 or 00040030.
_SYSTEM_BY_LOW = {
    '00020000': 'System Settings', '00021000': 'System Settings', '00022000': 'System Settings',
    '00026000': 'System Settings', '00027000': 'System Settings', '00028000': 'System Settings',
    '00020100': 'Download Play', '00021100': 'Download Play', '00022100': 'Download Play',
    '00026100': 'Download Play', '00027100': 'Download Play', '00028100': 'Download Play',
    '00020200': 'Activity Log', '00021200': 'Activity Log', '00022200': 'Activity Log',
    '00026200': 'Activity Log', '00027200': 'Activity Log', '00028200': 'Activity Log',
    '00020300': 'Health and Safety', '00021300': 'Health and Safety', '00022300': 'Health and Safety',
    '00026300': 'Health and Safety', '00027300': 'Health and Safety', '00028300': 'Health and Safety',
    '20020300': 'Health and Safety', '20021300': 'Health and Safety', '20022300': 'Health and Safety',
    '20027300': 'Health and Safety',
    '00020a00': 'System Transfer', '00021a00': 'System Transfer', '00022a00': 'System Transfer',
    '00020b00': 'Nintendo Zone', '00021b00': 'Nintendo Zone', '00022b00': 'Nintendo Zone',
    '00020f00': 'System Updater', '00021f00': 'System Updater', '00022f00': 'System Updater',
    '0002bf00': 'Nintendo Network ID Settings', '0002c000': 'Nintendo Network ID Settings',
    '0002c100': 'Nintendo Network ID Settings',
    '00020400': 'Nintendo 3DS Camera', '00021400': 'Nintendo 3DS Camera', '00022400': 'Nintendo 3DS Camera',
    '00020500': 'Nintendo 3DS Sound', '00021500': 'Nintendo 3DS Sound', '00022500': 'Nintendo 3DS Sound',
    '00020700': 'Mii Maker', '00021700': 'Mii Maker', '00022700': 'Mii Maker',
    '00020800': 'StreetPass Mii Plaza', '00021800': 'StreetPass Mii Plaza', '00022800': 'StreetPass Mii Plaza',
    '00020900': 'Nintendo eShop', '00021900': 'Nintendo eShop', '00022900': 'Nintendo eShop',
    '00020d00': 'Face Raiders', '00021d00': 'Face Raiders', '00022d00': 'Face Raiders',
    '00020e00': 'AR Games', '00021e00': 'AR Games', '00022e00': 'AR Games',
    '00008202': 'HOME Menu', '00008f02': 'HOME Menu', '00009802': 'HOME Menu',
    '0000a102': 'HOME Menu', '0000a902': 'HOME Menu', '0000b102': 'HOME Menu',
    '00008d02': 'Friend List', '00009602': 'Friend List', '00009f02': 'Friend List',
    '00008802': 'Internet Browser', '00009402': 'Internet Browser', '00009d02': 'Internet Browser',
    '00008602': 'Instruction Manual', '00009202': 'Instruction Manual', '00009b02': 'Instruction Manual',
    '00008702': 'Game Notes', '00009302': 'Game Notes', '00009c02': 'Game Notes',
    '00008e02': 'Notifications', '00009702': 'Notifications', '0000a002': 'Notifications',
}


def system_title_name(title_id: str) -> str:
    text = title_id.lower()
    if text[:8] not in ('00040010', '00040030'):
        return ''
    return _SYSTEM_BY_LOW.get(text[8:], '')


def load_3dsdb_names(directory: str) -> dict[str, str]:
    """Read hax0kartik 3dsdb list_*.json files. Later regions do not replace earlier ones.

    JP is loaded last so a Japanese name wins when the same id is in several lists.
    """
    import json
    root = Path(directory)
    names: dict[str, str] = {}
    order = ['list_US.json', 'list_GB.json', 'list_KR.json', 'list_TW.json', 'list_JP.json']
    paths = [root / name for name in order if (root / name).is_file()]
    if not paths:
        paths = sorted(root.glob('list_*.json'))
    for path in paths:
        try:
            rows = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        for row in rows:
            title_id = str(row.get('TitleID', '')).lower()
            name = str(row.get('Name', '')).strip()
            if len(title_id) == 16 and name:
                names[title_id] = name
    return names


def save3ds_fuse_path() -> str:
    if 'CUSTOM_INSTALL_SAVE3DS_PATH' in environ:
        return environ['CUSTOM_INSTALL_SAVE3DS_PATH']
    name = 'save3ds_fuse.exe' if is_windows else 'save3ds_fuse'
    frozen = getattr(__import__('sys'), 'frozen', False)
    script_dir = Path(executable).parent if frozen else Path(__file__).resolve().parent
    if frozen:
        candidate = script_dir / 'bin' / name
    else:
        candidate = script_dir / 'bin' / _platform / name
    if candidate.is_file():
        return str(candidate)
    found = which('save3ds_fuse')
    if not found:
        raise LayoutError('save3ds_fuse was not found')
    return found


@dataclass
class InstalledTitle:
    title_id: str
    kind: str
    name: str
    slot: int | None
    folder: int | None

    def as_dict(self) -> dict:
        return {
            'title_id': self.title_id,
            'kind': self.kind,
            'name': self.name,
            'slot': self.slot,
            'folder': self.folder,
        }


class TitleManager:
    def __init__(self, sd: str, movable: str, boot9: str | None = None, seeddb: str | None = None,
                 extdata_id: str | None = None):
        self.sd = str(Path(sd))
        self.movable = str(Path(movable))
        self.boot9 = str(Path(boot9)) if boot9 else None
        self.seeddb = str(Path(seeddb)) if seeddb else None
        self.extdata_id = extdata_id.lower() if extdata_id else None
        if not Path(self.movable).is_file():
            raise LayoutError(f'movable.sed not found: {self.movable}')
        crypto = CryptoEngine(boot9=self.boot9) if self.boot9 else CryptoEngine()
        crypto.setup_sd_key_from_file(self.movable)
        n3ds = str(Path(self.sd) / 'Nintendo 3DS')
        self.sdfs = SDFilesystem(n3ds, crypto=crypto)
        if len(self.sdfs.id1s) != 1:
            raise LayoutError(f'expected one id1 directory, found {len(self.sdfs.id1s)}')
        self.id0 = crypto.id0.hex()
        self.id1 = self.sdfs.current_id1
        if self.seeddb:
            load_seeddb(self.seeddb)

    def id1_root(self) -> Path:
        return Path(self.sd) / 'Nintendo 3DS' / self.id0 / self.id1

    def scan(self, read_names: bool = True) -> list[InstalledTitle]:
        title_root = self.id1_root() / 'title'
        found: list[InstalledTitle] = []
        if not title_root.is_dir():
            return found
        for high in sorted(p.name for p in title_root.iterdir() if p.is_dir()):
            kind = KIND_BY_HIGH.get(high.lower(), 'other')
            high_dir = title_root / high
            for low_dir in sorted(p for p in high_dir.iterdir() if p.is_dir()):
                title_id = (high + low_dir.name).lower()
                if len(title_id) != 16:
                    continue
                name = ''
                if read_names and kind == 'app':
                    name = self._read_name(title_id)
                found.append(InstalledTitle(title_id, kind, name, None, None))
        return found

    def _open_content(self, title_id: str):
        reader = self.sdfs.open_title(title_id, load_contents=True)
        content = reader.contents.get(0)
        return reader, content

    def _read_name(self, title_id: str) -> str:
        reader = None
        try:
            reader, content = self._open_content(title_id)
            if content is None or content.exefs is None or content.exefs.icon is None:
                return ''
            return content.exefs.icon.get_app_title().short_desc
        except Exception:
            return ''
        finally:
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    pass

    def read_display(self, title_id: str) -> tuple[str, list | None, str | None]:
        """Return the short title, a 48x48 RGB grid, and the extdata id."""
        reader = None
        try:
            reader, content = self._open_content(title_id)
            if content is None:
                return '', None, None
            name = ''
            icon = None
            if content.exefs is not None and content.exefs.icon is not None:
                smdh = content.exefs.icon
                title = smdh.get_app_title()
                name = title.short_desc if title else ''
                icon = smdh.icon_large_array
            extdata_id = None
            try:
                from pyctr.type.ncch import NCCHSection
                with content.open_raw_section(NCCHSection.ExtendedHeader) as header:
                    header.seek(0x200 + 0x30)
                    raw = header.read(8)
                if len(raw) == 8 and raw != b'\x00' * 8:
                    extdata_id = raw[0:4][::-1].hex()
            except Exception:
                extdata_id = None
            return name, icon, extdata_id
        except Exception:
            return '', None, None
        finally:
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    pass

    def resolve_extdata_id(self) -> str:
        base = self.id1_root() / 'extdata' / '00000000'
        if self.extdata_id:
            if not (base / self.extdata_id).is_dir():
                raise LayoutError(f'extdata {self.extdata_id} was not found')
            return self.extdata_id
        found = [item for item in HOME_MENU_EXTDATA if (base / item).is_dir()]
        if len(found) != 1:
            raise LayoutError(
                'could not choose a Home Menu extdata id '
                f'(found {found or "none"}). Pass --extdata-id.'
            )
        return found[0]

    def _save3ds(self, extra: list[str], workdir: str) -> None:
        if not self.boot9:
            raise LayoutError('boot9 is required to read Home Menu extdata')
        cmd = [
            save3ds_fuse_path(),
            '-b', self.boot9,
            '-m', self.movable,
            '--sd', self.sd,
            '--sdext', self.resolve_extdata_id(),
            *extra,
            workdir,
        ]
        kwargs = {}
        if is_windows:
            kwargs['creationflags'] = 0x08000000
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace', **kwargs)
        if out.returncode:
            raise LayoutError(f'save3ds_fuse failed ({out.returncode}): {out.stdout}')

    def load_savedata(self) -> SaveData:
        with TemporaryDirectory(prefix='ci-extdata-') as temp:
            self._save3ds(['-x'], temp)
            path = _find_named(Path(temp), 'SaveData.dat')
            return SaveData(path.read_bytes())

    def write_savedata(self, savedata: SaveData) -> Path:
        extdata_id = self.resolve_extdata_id()
        source = self.id1_root() / 'extdata' / '00000000' / extdata_id
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = Path(self.sd) / 'custom-install-backup' / stamp / extdata_id
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, backup)
        with TemporaryDirectory(prefix='ci-extdata-') as temp:
            self._save3ds(['-x'], temp)
            path = _find_named(Path(temp), 'SaveData.dat')
            path.write_bytes(savedata.blob)
            self._save3ds(['-i'], temp)
        return backup

    def list_with_layout(self, read_names: bool = True) -> tuple[list[InstalledTitle], SaveData]:
        installed = self.scan(read_names=read_names)
        savedata = self.load_savedata()
        by_id = {format_title_id(entry.title_id): entry for entry in savedata.entries()}
        for title in installed:
            entry = by_id.get(title.title_id)
            if entry is not None:
                title.slot = entry.slot
                title.folder = entry.folder
        return installed, savedata

    def move(self, title_id: str, slot: int, folder: int | None = None) -> Path:
        tid = parse_title_id(title_id)
        installed = {item.title_id for item in self.scan(read_names=False)}
        if format_title_id(tid) not in installed:
            raise LayoutError(f'{format_title_id(tid)} is not installed on the SD card')
        savedata = self.load_savedata()
        current = savedata.find(tid)
        use_folder = folder if folder is not None else (current.folder if current else -1)
        savedata.set_icon(tid, slot, use_folder)
        return self.write_savedata(savedata)

    def sort_apps(self, by: str) -> Path:
        if by not in ('name', 'id'):
            raise LayoutError('--by must be name or id')
        installed, savedata = self.list_with_layout(read_names=(by == 'name'))
        apps = [item for item in installed if item.kind == 'app']
        groups: dict[int, list[InstalledTitle]] = {}
        for item in apps:
            folder = -1 if item.folder is None else item.folder
            groups.setdefault(folder, []).append(item)
        updates: list[tuple[int, int, int]] = []
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
        savedata.apply_slots(updates)
        return self.write_savedata(savedata)


def _find_named(root: Path, name: str) -> Path:
    matches = [path for path in root.rglob(name) if path.is_file()]
    if len(matches) != 1:
        raise LayoutError(f'expected one {name} in the extracted extdata, found {len(matches)}')
    return matches[0]


def _print_titles(titles: Iterable[InstalledTitle], as_json: bool) -> None:
    rows = list(titles)
    apps = sum(1 for row in rows if row.kind == 'app')
    if as_json:
        print(json.dumps([row.as_dict() for row in rows], ensure_ascii=False, indent=2))
    else:
        print(f'{"title id":<18} {"type":<8} {"slot":>5} {"folder":>7}  name')
        for row in rows:
            slot = '' if row.slot is None else str(row.slot)
            folder = '' if row.folder is None else str(row.folder)
            print(f'{row.title_id:<18} {row.kind:<8} {slot:>5} {folder:>7}  {row.name}')
    print(f'{len(rows)} titles, {apps} applications. The HOME Menu shows at most 300 application icons.')


def _add_sd_args(parser: ArgumentParser, boot9_required: bool = False) -> None:
    parser.add_argument('--snapshot', help='ci-manage export directory')
    parser.add_argument('--sd', help='path to SD root')
    parser.add_argument('-m', '--movable', help='movable.sed file')
    parser.add_argument('-b', '--boot9', required=False, help='boot9 file')
    parser.add_argument('-s', '--seeddb', help='seeddb file')
    parser.add_argument('--extdata-id', default='00000082', help='Home Menu SD extdata id')
    if boot9_required:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = ArgumentParser(prog='custominstall titles', description='List and arrange installed 3DS titles.')
    sub = parser.add_subparsers(dest='command', required=True)

    list_p = sub.add_parser('list', help='list titles installed on the SD card')
    _add_sd_args(list_p, boot9_required=True)
    list_p.add_argument('--json', action='store_true')
    list_p.add_argument('--no-names', action='store_true', help='do not read SMDH names')
    list_p.add_argument('--no-layout', action='store_true', help='do not read SaveData.dat')

    layout_p = sub.add_parser('layout', help='show SaveData.dat slots and optional Launcher.dat folders')
    _add_sd_args(layout_p, boot9_required=True)
    layout_p.add_argument('--launcher', help='dumped NAND Launcher.dat')

    move_p = sub.add_parser('move', help='set one SD title slot and optional folder id')
    _add_sd_args(move_p, boot9_required=True)
    move_p.add_argument('--id', required=True, help='title id')
    move_p.add_argument('--slot', required=True, type=int)
    move_p.add_argument('--folder', type=int, help='folder id -1..59, default keeps the current folder')

    sort_p = sub.add_parser('sort', help='reorder application icons inside each folder')
    _add_sd_args(sort_p, boot9_required=True)
    sort_p.add_argument('--by', choices=('name', 'id'), required=True)

    add_p = sub.add_parser('add', help='install CIAs onto the SD card and record them in the snapshot')
    _add_sd_args(add_p, boot9_required=True)
    add_p.add_argument('cia', nargs='+')

    remove_p = sub.add_parser('remove', help='remove one title from the SD card and the snapshot')
    _add_sd_args(remove_p, boot9_required=True)
    remove_p.add_argument('--id', required=True)

    folder_p = sub.add_parser('folder', help='create or rename a folder inside a Launcher.dat dump')
    folder_p.add_argument('--snapshot', help='ci-manage export directory')
    folder_p.add_argument('--launcher')
    folder_p.add_argument('--out', help='output path (default: <launcher>.out)')
    folder_p.add_argument('--in-place', action='store_true')
    folder_sub = folder_p.add_subparsers(dest='folder_command', required=True)
    create_p = folder_sub.add_parser('create')
    create_p.add_argument('--name', required=True)
    create_p.add_argument('--slot', type=int)
    rename_p = folder_sub.add_parser('rename')
    rename_p.add_argument('--index', required=True, type=int)
    rename_p.add_argument('--name', required=True)

    args = parser.parse_args(argv)
    try:
        if getattr(args, 'snapshot', None):
            return _snapshot_command(args)
        if args.command == 'folder':
            if not args.launcher:
                raise LayoutError('pass --launcher or --snapshot')
            return _cmd_folder(args)
        if not args.sd or not args.movable or not args.boot9:
            raise LayoutError('pass --sd, --movable, and --boot9, or pass --snapshot')
        manager = TitleManager(args.sd, args.movable, args.boot9, args.seeddb, args.extdata_id)
        if args.command == 'list':
            if args.no_layout:
                rows = manager.scan(read_names=not args.no_names)
            else:
                rows, _savedata = manager.list_with_layout(read_names=not args.no_names)
            _print_titles(rows, args.json)
        elif args.command == 'layout':
            _savedata_rows, savedata = manager.list_with_layout(read_names=False)
            print(f'SaveData.dat version {savedata.version}, {len(savedata.entries())} SD icon entries')
            for entry in savedata.entries():
                print(f'  {format_title_id(entry.title_id)}  slot {entry.slot:3d}  folder {entry.folder:3d}')
            if args.launcher:
                launcher = LauncherData(Path(args.launcher).read_bytes())
                print('Launcher.dat folders:')
                for folder in launcher.folders():
                    print(f'  [{folder.index:02d}] slot {folder.position:3d}  #{folder.number}  {folder.name}')
        elif args.command == 'move':
            if args.folder is not None and (args.folder < -1 or args.folder >= FOLDER_COUNT):
                raise LayoutError('folder must be -1..59')
            backup = manager.move(args.id, args.slot, args.folder)
            print(f'updated SaveData.dat; backup: {backup}')
        elif args.command == 'sort':
            backup = manager.sort_apps(args.by)
            print(f'sorted applications by {args.by}; backup: {backup}')
    except LayoutError as exc:
        print(f'error: {exc}')
        return 1
    return 0


def _snapshot_command(args) -> int:
    from .snapshot import Snapshot
    snap = Snapshot(args.snapshot, sd=getattr(args, 'sd', None), extdata_id=getattr(args, 'extdata_id', '00000082'),
                    seeddb=getattr(args, 'seeddb', None))
    if args.command == 'list':
        _print_titles((row.as_installed() for row in snap.list_titles(read_names=not args.no_names)), args.json)
        print(f'{len(snap.folders())} folders, {snap.seed_count()} seeds')
    elif args.command == 'layout':
        print(f'SaveData.dat version {snap.savedata.version}, {len(snap.savedata.entries())} SD icon entries')
        for entry in snap.savedata.entries():
            print(f'  {format_title_id(entry.title_id)}  slot {entry.slot:3d}  folder {entry.folder:3d}')
        print('Launcher.dat folders:')
        for folder in snap.folders():
            print(f'  [{folder.index:02d}] slot {folder.position:3d}  #{folder.number}  {folder.name}')
    elif args.command == 'move':
        if args.folder is not None and (args.folder < -1 or args.folder >= FOLDER_COUNT):
            raise LayoutError('folder must be -1..59')
        snap.move(args.id, args.slot, args.folder)
        backup = snap.save()
        print(f'updated snapshot; backup: {backup}')
    elif args.command == 'sort':
        snap.sort_apps(args.by)
        backup = snap.save()
        print(f'sorted by {args.by}; backup: {backup}')
    elif args.command == 'folder':
        if args.folder_command == 'create':
            created = snap.create_folder(args.name, args.slot)
            print(f'created folder {created.index} at slot {created.position}')
        else:
            snap.rename_folder(args.index, args.name)
            print(f'renamed folder {args.index}')
        backup = snap.save()
        print(f'wrote Launcher.dat into the snapshot; backup: {backup}')
    elif args.command == 'add':
        if not args.sd:
            raise LayoutError('add needs --sd so the CIA contents have a place to go')
        installed = snap.add_cias(args.cia, args.seeddb)
        backup = snap.save()
        print(f'installed {len(installed)} titles; backup: {backup}')
        print('Import the snapshot from GodMode9 before opening HOME Menu.')
    elif args.command == 'remove':
        snap.remove(args.id)
        backup = snap.save()
        print(f'removed {args.id}; backup: {backup}')
        print('Import the snapshot from GodMode9 before opening HOME Menu.')
    else:
        raise LayoutError(f'{args.command} does not support --snapshot')
    return 0


def _cmd_folder(args) -> int:
    source = Path(args.launcher)
    launcher = LauncherData(source.read_bytes())
    if args.folder_command == 'create':
        created = launcher.create_folder(args.name, args.slot)
        print(f'created folder {created.index} number {created.number} at slot {created.position}')
    else:
        launcher.rename_folder(args.index, args.name)
        print(f'renamed folder {args.index}')
    if args.in_place:
        dest = source
    else:
        dest = Path(args.out) if args.out else source.with_name(source.name + '.out')
    dest.write_bytes(launcher.blob)
    print(f'wrote {dest}')
    return 0


