# This file is a part of custom-install.
#
# Copyright (c) 2019 Ian Burgwin
# This file is licensed under The MIT License (MIT).

"""Build a title-key-decrypted CIA from an installed SD title.

The NCCH data is copied as the console stored it. The original title-key
encryption is not restored. DSiWare and NAND titles have no SD contents.
"""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from pyctr.type.tmd import ContentChunkRecord, ContentInfoRecord, ContentTypeFlags, TitleMetadataReader

from .layout import LayoutError

DUMPABLE_HIGHS = ('00040000', '0004000e', '0004008c')
ALIGN = 0x40
CHUNK = 1024 * 1024


def export_cia(sd: str, movable: str, boot9: str, title_id: str, dest: str, seeddb: str | None = None) -> Path:
    """Read one SD title and write a decrypted CIA."""
    from .titles import TitleManager

    text = title_id.lower()
    if len(text) != 16 or text[:8] not in DUMPABLE_HIGHS:
        raise LayoutError('only an SD application, update, or DLC can be exported')
    manager = TitleManager(sd, movable, boot9, seeddb)
    tmd_path = f'/title/{text[:8]}/{text[8:]}/content/00000000.tmd'
    if not manager.sdfs.isfile(tmd_path):
        raise LayoutError(f'{text} has no 00000000.tmd on the SD card')
    with manager.sdfs.open(tmd_path) as tmd_file:
        tmd = TitleMetadataReader.load(tmd_file)
    present = []
    for record in tmd.chunk_records:
        path = _content_path(text, record)
        if manager.sdfs.isfile(path):
            present.append((record, path))
    if not present:
        raise LayoutError(f'{text} has no content files on the SD card')
    output = Path(dest)
    output.parent.mkdir(parents=True, exist_ok=True)

    def opener(path: str):
        return manager.sdfs.open(path)

    write_decrypted_cia(tmd, [(record, lambda path=path: opener(path)) for record, path in present], output)
    written = _read_tmd(output)
    if written.title_id != text:
        raise LayoutError(f'exported CIA title id is {written.title_id}')
    if any(record.type.encrypted for record in written.chunk_records):
        raise LayoutError(f'{text} was exported with the title-key flag still set')
    return output


def write_decrypted_cia(tmd: TitleMetadataReader, contents: list, dest: Path) -> None:
    """Write a CIA whose contents are already decrypted.

    ``contents`` is ``(chunk record, opener)``. ``opener()`` returns a binary
    file positioned at the start of that content. Missing contents are omitted,
    which is how an incomplete DLC title is stored.
    """
    rebuilt = []
    openers = []
    for record, opener in contents:
        size, digest = _hash_stream(opener())
        flags = ContentTypeFlags(False, record.type.disc, record.type.cfm, record.type.optional, record.type.shared)
        rebuilt.append(ContentChunkRecord(record.id, record.cindex, flags, size, digest))
        openers.append(opener)
    chunk_bytes = b''.join(bytes(record) for record in rebuilt)
    info = ContentInfoRecord(0, len(rebuilt), sha256(chunk_bytes).digest())
    tmd.chunk_records = tuple(rebuilt)
    tmd.info_records = (info,)
    tmd.content_count = len(rebuilt)
    tmd_bytes = bytes(tmd)
    ticket = _stub_ticket(tmd.title_id)
    content_size = sum(record.size for record in rebuilt)
    header = _cia_header(len(ticket), len(tmd_bytes), content_size, [record.cindex for record in rebuilt])
    with dest.open('wb') as out:
        _write_section(out, header)
        _write_section(out, ticket)
        _write_section(out, tmd_bytes)
        for opener in openers:
            with opener() as source:
                _copy(source, out)


def _content_path(title_id: str, record: ContentChunkRecord) -> str:
    high, low = title_id[:8], title_id[8:]
    name = f'{record.id}.app'
    if high == '0004008c':
        return f'/title/{high}/{low}/content/{record.cindex // 256:08x}/{name}'
    return f'/title/{high}/{low}/content/{name}'


def _hash_stream(source) -> tuple[int, bytes]:
    digest = sha256()
    size = 0
    try:
        while True:
            block = source.read(CHUNK)
            if not block:
                break
            digest.update(block)
            size += len(block)
    finally:
        source.close()
    return size, digest.digest()


def _copy(source, dest) -> None:
    try:
        while True:
            block = source.read(CHUNK)
            if not block:
                break
            dest.write(block)
    finally:
        source.close()


def _stub_ticket(title_id: str) -> bytes:
    template = Path(__file__).resolve().parents[1] / 'finalize' / 'data' / 'basetik.bin'
    if not template.is_file():
        raise LayoutError(f'missing ticket template {template}')
    body = bytearray(template.read_bytes())
    if len(body) != 0x350:
        raise LayoutError(f'basetik.bin must be 0x350 bytes, got {len(body):#x}')
    body[0x1DC:0x1E4] = bytes.fromhex(title_id)
    return bytes(body)


def _cia_header(ticket_size: int, tmd_size: int, content_size: int, indexes: list[int]) -> bytes:
    header = bytearray(0x2020)
    header[0:4] = (0x2020).to_bytes(4, 'little')
    header[0x0C:0x10] = ticket_size.to_bytes(4, 'little')
    header[0x10:0x14] = tmd_size.to_bytes(4, 'little')
    header[0x18:0x20] = content_size.to_bytes(8, 'little')
    for index in indexes:
        if index < 0 or index >= 0x10000:
            raise LayoutError(f'content index {index} does not fit in a CIA')
        header[0x20 + (index // 8)] |= 0x80 >> (index % 8)
    return bytes(header)


def _read_tmd(path: Path) -> TitleMetadataReader:
    with path.open('rb') as handle:
        header = handle.read(0x20)
        ticket_size = int.from_bytes(header[0x0C:0x10], 'little')
        tmd_size = int.from_bytes(header[0x10:0x14], 'little')
        tmd_offset = _align(0x2020) + _align(ticket_size)
        handle.seek(tmd_offset)
        return TitleMetadataReader.load(BytesIO(handle.read(tmd_size)))


def _align(size: int) -> int:
    return (size + (ALIGN - 1)) & ~(ALIGN - 1)


def _write_section(out, data: bytes) -> None:
    out.write(data)
    padding = (-len(data)) % ALIGN
    if padding:
        out.write(b'\0' * padding)
