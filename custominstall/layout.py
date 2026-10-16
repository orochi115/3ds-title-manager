# This file is a part of custom-install.
#
# Copyright (c) 2019 Ian Burgwin
# This file is licensed under The MIT License (MIT).

"""Home Menu icon layout stored in SaveData.dat and Launcher.dat.

SaveData.dat (SD extdata) holds SD title IDs, linear icon slots, and folder
ids. Folder names and folder positions live in NAND Launcher.dat.
Offsets follow https://www.3dbrew.org/wiki/Home_Menu
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


TITLE_SLOTS = 360
FOLDER_COUNT = 60
SAVEDATA_SIZE = 0x2DA0
EMPTY_TITLE_ID = 0xFFFFFFFFFFFFFFFF
NAME_CHARS = 0x11
NAME_BYTES = NAME_CHARS * 2

SAVEDATA_TITLES = 0x8
SAVEDATA_PRESENT = 0xB48
SAVEDATA_POSITIONS = 0xCB0
SAVEDATA_FOLDERS = 0xF80

LAUNCHER_MIN_SIZE = 0x1E48
LAUNCHER_TITLES = 0x8
LAUNCHER_NEXT_FOLDER = 0xD80
LAUNCHER_POSITIONS = 0xD9A
LAUNCHER_FOLDERS = 0x106A
LAUNCHER_FOLDER_POSITIONS = 0x11DC
LAUNCHER_MENU_ROWS = 0xB51
LAUNCHER_FOLDER_ROWS = 0x1434
LAUNCHER_FOLDER_NAMES = 0x1560
LAUNCHER_FOLDER_NUMBERS = 0x1D58


class LayoutError(Exception):
    pass


@dataclass
class IconEntry:
    index: int
    title_id: int
    slot: int
    folder: int


@dataclass
class FolderEntry:
    index: int
    position: int
    name: str
    number: int
    rows: int


def _s16(buf: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from('<h', buf, offset)[0]


def _set_s16(buf: bytearray, offset: int, value: int) -> None:
    struct.pack_into('<h', buf, offset, value)


def _s8(buf: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from('<b', buf, offset)[0]


def _set_s8(buf: bytearray, offset: int, value: int) -> None:
    struct.pack_into('<b', buf, offset, value)


def format_title_id(title_id: int) -> str:
    return f'{title_id:016x}'


def parse_title_id(text: str) -> int:
    cleaned = text.strip().lower().removeprefix('0x')
    if len(cleaned) != 16:
        raise LayoutError(f'title id must be 16 hex characters, got {text!r}')
    try:
        return int(cleaned, 16)
    except ValueError as exc:
        raise LayoutError(f'title id must be hex, got {text!r}') from exc


class SaveData:
    """SD Home Menu SaveData.dat (format version 2, 3, or 4)."""

    def __init__(self, blob: bytes):
        if len(blob) != SAVEDATA_SIZE:
            raise LayoutError(f'SaveData.dat must be {SAVEDATA_SIZE:#x} bytes, got {len(blob):#x}')
        version = blob[0]
        if version not in (2, 3, 4):
            raise LayoutError(f'SaveData.dat version {version} is not 2, 3, or 4')
        self.version = version
        self.blob = bytearray(blob)

    def entries(self) -> list[IconEntry]:
        found = []
        for index in range(TITLE_SLOTS):
            title_id = struct.unpack_from('<Q', self.blob, SAVEDATA_TITLES + index * 8)[0]
            if title_id == EMPTY_TITLE_ID:
                continue
            slot = _s16(self.blob, SAVEDATA_POSITIONS + index * 2)
            folder = _s8(self.blob, SAVEDATA_FOLDERS + index)
            found.append(IconEntry(index, title_id, slot, folder))
        return found

    def find(self, title_id: int) -> IconEntry | None:
        for entry in self.entries():
            if entry.title_id == title_id:
                return entry
        return None

    def _empty_index(self) -> int:
        for index in range(TITLE_SLOTS):
            title_id = struct.unpack_from('<Q', self.blob, SAVEDATA_TITLES + index * 8)[0]
            if title_id == EMPTY_TITLE_ID:
                return index
        raise LayoutError('SaveData.dat has no free title slot (360)')

    def set_icon(self, title_id: int, slot: int, folder: int) -> None:
        if not 0 <= slot < TITLE_SLOTS:
            raise LayoutError(f'slot must be 0..{TITLE_SLOTS - 1}')
        if folder < -1 or folder >= FOLDER_COUNT:
            raise LayoutError('folder must be -1..59')
        current = self.find(title_id)
        if current is None:
            index = self._empty_index()
            struct.pack_into('<Q', self.blob, SAVEDATA_TITLES + index * 8, title_id)
            self.blob[SAVEDATA_PRESENT + index] = 1
            _set_s16(self.blob, SAVEDATA_POSITIONS + index * 2, slot)
            _set_s8(self.blob, SAVEDATA_FOLDERS + index, folder)
            return
        # Same slot inside the same folder is one icon. Swap with the occupant.
        for other in self.entries():
            if other.title_id != title_id and other.folder == folder and other.slot == slot:
                _set_s16(self.blob, SAVEDATA_POSITIONS + other.index * 2, current.slot)
                _set_s8(self.blob, SAVEDATA_FOLDERS + other.index, current.folder)
                break
        _set_s16(self.blob, SAVEDATA_POSITIONS + current.index * 2, slot)
        _set_s8(self.blob, SAVEDATA_FOLDERS + current.index, folder)

    def place(self, title_id: int, slot: int, folder: int) -> None:
        """Move one icon without swapping whatever currently uses the slot."""
        if not 0 <= slot < TITLE_SLOTS:
            raise LayoutError(f'slot must be 0..{TITLE_SLOTS - 1}')
        if folder < -1 or folder >= FOLDER_COUNT:
            raise LayoutError('folder must be -1..59')
        current = self.find(title_id)
        if current is None:
            raise LayoutError(f'{format_title_id(title_id)} is not on the menu')
        _set_s16(self.blob, SAVEDATA_POSITIONS + current.index * 2, slot)
        _set_s8(self.blob, SAVEDATA_FOLDERS + current.index, folder)

    def apply_slots(self, updates: list[tuple[int, int, int]]) -> None:
        """Apply (title_id, slot, folder) without swapping mid-loop.

        Writes title ids first, then slots, so two icons can exchange slots.
        """
        by_id = {entry.title_id: entry for entry in self.entries()}
        for title_id, slot, folder in updates:
            if not 0 <= slot < TITLE_SLOTS:
                raise LayoutError(f'slot must be 0..{TITLE_SLOTS - 1}')
            if folder < -1 or folder >= FOLDER_COUNT:
                raise LayoutError('folder must be -1..59')
            if title_id not in by_id:
                index = self._empty_index()
                struct.pack_into('<Q', self.blob, SAVEDATA_TITLES + index * 8, title_id)
                self.blob[SAVEDATA_PRESENT + index] = 1
                by_id[title_id] = IconEntry(index, title_id, slot, folder)
        seen = set()
        for title_id, slot, folder in updates:
            key = (folder, slot)
            if key in seen:
                raise LayoutError(f'duplicate slot {slot} in folder {folder}')
            seen.add(key)
            entry = by_id[title_id]
            _set_s16(self.blob, SAVEDATA_POSITIONS + entry.index * 2, slot)
            _set_s8(self.blob, SAVEDATA_FOLDERS + entry.index, folder)


def _decode_name(raw: bytes) -> str:
    return raw.decode('utf-16-le').rstrip('\x00').rstrip()


def _encode_name(name: str) -> bytes:
    if len(name) > NAME_CHARS:
        raise LayoutError(f'folder name longer than {NAME_CHARS} UTF-16 characters')
    encoded = name.encode('utf-16-le')
    return encoded + b'\x00' * (NAME_BYTES - len(encoded))


class LauncherData:
    """NAND Home Menu Launcher.dat. This file is not on the SD card."""

    def __init__(self, blob: bytes):
        if len(blob) < LAUNCHER_MIN_SIZE:
            raise LayoutError(f'Launcher.dat must be at least {LAUNCHER_MIN_SIZE:#x} bytes, got {len(blob):#x}')
        self.blob = bytearray(blob)

    def folders(self) -> list[FolderEntry]:
        found = []
        for index in range(FOLDER_COUNT):
            position = _s16(self.blob, LAUNCHER_FOLDER_POSITIONS + index * 2)
            if position < 0:
                continue
            name_at = LAUNCHER_FOLDER_NAMES + index * NAME_BYTES
            name = _decode_name(self.blob[name_at:name_at + NAME_BYTES])
            number = struct.unpack_from('<I', self.blob, LAUNCHER_FOLDER_NUMBERS + index * 4)[0]
            rows = self.blob[LAUNCHER_FOLDER_ROWS + index]
            found.append(FolderEntry(index, position, name, number, rows))
        return found

    def _next_folder_number(self) -> int:
        return struct.unpack_from('<H', self.blob, LAUNCHER_NEXT_FOLDER)[0]

    def _set_next_folder_number(self, number: int) -> None:
        struct.pack_into('<H', self.blob, LAUNCHER_NEXT_FOLDER, number & 0xFFFF)

    def create_folder(self, name: str, slot: int | None = None) -> FolderEntry:
        free = None
        used_positions = []
        for index in range(FOLDER_COUNT):
            position = _s16(self.blob, LAUNCHER_FOLDER_POSITIONS + index * 2)
            if position < 0 and free is None:
                free = index
            elif position >= 0:
                used_positions.append(position)
        if free is None:
            raise LayoutError('all 60 folder slots are in use')
        position = max(used_positions) + 1 if slot is None and used_positions else (0 if slot is None else slot)
        if not 0 <= position < TITLE_SLOTS:
            raise LayoutError(f'folder slot must be 0..{TITLE_SLOTS - 1}')
        number = self._next_folder_number()
        if number == 0:
            number = 1
        self._set_next_folder_number(number + 1)
        _set_s16(self.blob, LAUNCHER_FOLDER_POSITIONS + free * 2, position)
        self.blob[LAUNCHER_FOLDER_ROWS + free] = 2
        struct.pack_into('<H', self.blob, 0x1470 + free * 2, 0)  # cursor
        struct.pack_into('<H', self.blob, 0x14E8 + free * 2, 0)  # scroll
        name_at = LAUNCHER_FOLDER_NAMES + free * NAME_BYTES
        self.blob[name_at:name_at + NAME_BYTES] = _encode_name(name)
        struct.pack_into('<I', self.blob, LAUNCHER_FOLDER_NUMBERS + free * 4, number)
        return FolderEntry(free, position, name, number, 2)

    def set_folder_position(self, index: int, slot: int) -> None:
        if not 0 <= index < FOLDER_COUNT:
            raise LayoutError('folder index must be 0..59')
        if not 0 <= slot < TITLE_SLOTS:
            raise LayoutError(f'folder slot must be 0..{TITLE_SLOTS - 1}')
        current = _s16(self.blob, LAUNCHER_FOLDER_POSITIONS + index * 2)
        if current < 0:
            raise LayoutError(f'folder {index} is not created')
        for other in self.folders():
            if other.index != index and other.position == slot:
                _set_s16(self.blob, LAUNCHER_FOLDER_POSITIONS + other.index * 2, current)
                break
        _set_s16(self.blob, LAUNCHER_FOLDER_POSITIONS + index * 2, slot)

    def rename_folder(self, index: int, name: str) -> None:
        if not 0 <= index < FOLDER_COUNT:
            raise LayoutError('folder index must be 0..59')
        if _s16(self.blob, LAUNCHER_FOLDER_POSITIONS + index * 2) < 0:
            raise LayoutError(f'folder {index} is not created')
        name_at = LAUNCHER_FOLDER_NAMES + index * NAME_BYTES
        self.blob[name_at:name_at + NAME_BYTES] = _encode_name(name)

    def menu_rows(self) -> int:
        """Main menu row count. The file stores rows minus one, from 0 to 5."""
        stored = self.blob[LAUNCHER_MENU_ROWS]
        if stored > 5:
            return 3
        return stored + 1

    def cart_slot(self) -> int | None:
        """Linear slot of the game-card icon. It is not one of the title entries."""
        value = struct.unpack_from('<H', self.blob, 2)[0]
        if value >= TITLE_SLOTS:
            return None
        return value

    def entries(self) -> list[IconEntry]:
        found = []
        for index in range(TITLE_SLOTS):
            title_id = struct.unpack_from('<Q', self.blob, LAUNCHER_TITLES + index * 8)[0]
            if title_id == EMPTY_TITLE_ID:
                continue
            slot = _s16(self.blob, LAUNCHER_POSITIONS + index * 2)
            folder = _s8(self.blob, LAUNCHER_FOLDERS + index)
            found.append(IconEntry(index, title_id, slot, folder))
        return found

    def find(self, title_id: int) -> IconEntry | None:
        for entry in self.entries():
            if entry.title_id == title_id:
                return entry
        return None

    def set_icon(self, title_id: int, slot: int, folder: int) -> None:
        if not 0 <= slot < TITLE_SLOTS:
            raise LayoutError(f'slot must be 0..{TITLE_SLOTS - 1}')
        if folder < -1 or folder >= FOLDER_COUNT:
            raise LayoutError('folder must be -1..59')
        current = self.find(title_id)
        if current is None:
            index = self._empty_index()
            struct.pack_into('<Q', self.blob, LAUNCHER_TITLES + index * 8, title_id)
            _set_s16(self.blob, LAUNCHER_POSITIONS + index * 2, slot)
            _set_s8(self.blob, LAUNCHER_FOLDERS + index, folder)
            return
        for other in self.entries():
            if other.title_id != title_id and other.folder == folder and other.slot == slot:
                _set_s16(self.blob, LAUNCHER_POSITIONS + other.index * 2, current.slot)
                _set_s8(self.blob, LAUNCHER_FOLDERS + other.index, current.folder)
                break
        _set_s16(self.blob, LAUNCHER_POSITIONS + current.index * 2, slot)
        _set_s8(self.blob, LAUNCHER_FOLDERS + current.index, folder)

    def place(self, title_id: int, slot: int, folder: int) -> None:
        if not 0 <= slot < TITLE_SLOTS:
            raise LayoutError(f'slot must be 0..{TITLE_SLOTS - 1}')
        if folder < -1 or folder >= FOLDER_COUNT:
            raise LayoutError('folder must be -1..59')
        current = self.find(title_id)
        if current is None:
            raise LayoutError(f'{format_title_id(title_id)} is not on the menu')
        _set_s16(self.blob, LAUNCHER_POSITIONS + current.index * 2, slot)
        _set_s8(self.blob, LAUNCHER_FOLDERS + current.index, folder)

    def place_folder(self, index: int, slot: int) -> None:
        if not 0 <= slot < TITLE_SLOTS:
            raise LayoutError(f'folder slot must be 0..{TITLE_SLOTS - 1}')
        if _s16(self.blob, LAUNCHER_FOLDER_POSITIONS + index * 2) < 0:
            raise LayoutError(f'folder {index} is not created')
        _set_s16(self.blob, LAUNCHER_FOLDER_POSITIONS + index * 2, slot)

    def set_cart_slot(self, slot: int) -> None:
        if not 0 <= slot < TITLE_SLOTS:
            raise LayoutError(f'cart slot must be 0..{TITLE_SLOTS - 1}')
        struct.pack_into('<H', self.blob, 2, slot)

    def _empty_index(self) -> int:
        for index in range(TITLE_SLOTS):
            title_id = struct.unpack_from('<Q', self.blob, LAUNCHER_TITLES + index * 8)[0]
            if title_id == EMPTY_TITLE_ID:
                return index
        raise LayoutError('Launcher.dat has no free title slot (360)')


def main_menu_slots(savedata: SaveData, launcher: LauncherData) -> set[int]:
    """Slots taken on the main menu by an icon or a folder."""
    used = set()
    for entry in savedata.entries():
        if entry.folder == -1 and entry.slot >= 0:
            used.add(entry.slot)
    for entry in launcher.entries():
        if entry.folder == -1 and entry.slot >= 0:
            used.add(entry.slot)
    for folder in launcher.folders():
        used.add(folder.position)
    cart = launcher.cart_slot()
    if cart is not None:
        used.add(cart)
    return used


def delete_folder(savedata: SaveData, launcher: LauncherData, index: int) -> None:
    """Move a folder's icons back to the main menu and clear the folder.

    Raises LayoutError before changing either blob when the main menu does
    not have a free slot for every icon inside the folder.
    """
    folder = next((item for item in launcher.folders() if item.index == index), None)
    if folder is None:
        raise LayoutError(f'folder {index} is not created')
    children_sd = sorted((entry for entry in savedata.entries() if entry.folder == index), key=lambda entry: entry.slot)
    children_nand = sorted((entry for entry in launcher.entries() if entry.folder == index), key=lambda entry: entry.slot)
    used = main_menu_slots(savedata, launcher)
    used.discard(folder.position)
    free = [slot for slot in range(TITLE_SLOTS) if slot not in used]
    if len(free) < len(children_sd) + len(children_nand):
        raise LayoutError('not enough free slots on the main menu to delete this folder')
    cursor = 0
    for entry in children_sd:
        _set_s16(savedata.blob, SAVEDATA_POSITIONS + entry.index * 2, free[cursor])
        _set_s8(savedata.blob, SAVEDATA_FOLDERS + entry.index, -1)
        cursor += 1
    for entry in children_nand:
        _set_s16(launcher.blob, LAUNCHER_POSITIONS + entry.index * 2, free[cursor])
        _set_s8(launcher.blob, LAUNCHER_FOLDERS + entry.index, -1)
        cursor += 1
    _set_s16(launcher.blob, LAUNCHER_FOLDER_POSITIONS + index * 2, -1)
    name_at = LAUNCHER_FOLDER_NAMES + index * NAME_BYTES
    launcher.blob[name_at:name_at + NAME_BYTES] = b'\x00' * NAME_BYTES
    struct.pack_into('<I', launcher.blob, LAUNCHER_FOLDER_NUMBERS + index * 4, 0)


def blank_savedata() -> SaveData:
    blob = bytearray(SAVEDATA_SIZE)
    blob[0] = 4
    for index in range(TITLE_SLOTS):
        struct.pack_into('<Q', blob, SAVEDATA_TITLES + index * 8, EMPTY_TITLE_ID)
        _set_s8(blob, SAVEDATA_FOLDERS + index, -1)
        _set_s16(blob, SAVEDATA_POSITIONS + index * 2, -1)
    return SaveData(bytes(blob))


def blank_launcher() -> LauncherData:
    blob = bytearray(0x2558)
    blob[0] = 4
    for index in range(TITLE_SLOTS):
        struct.pack_into('<Q', blob, LAUNCHER_TITLES + index * 8, EMPTY_TITLE_ID)
        _set_s8(blob, LAUNCHER_FOLDERS + index, -1)
    for index in range(FOLDER_COUNT):
        _set_s16(blob, LAUNCHER_FOLDER_POSITIONS + index * 2, -1)
    struct.pack_into('<H', blob, LAUNCHER_NEXT_FOLDER, 1)
    return LauncherData(bytes(blob))
