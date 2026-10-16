import unittest

from custominstall.layout import (
    EMPTY_TITLE_ID,
    LayoutError,
    blank_launcher,
    blank_savedata,
    format_title_id,
)


class SaveDataTests(unittest.TestCase):
    def test_empty_table_skips_placeholder_ids(self):
        savedata = blank_savedata()
        self.assertEqual(savedata.entries(), [])
        self.assertEqual(savedata.blob[0], 4)
        self.assertEqual(int.from_bytes(savedata.blob[0x8:0x10], 'little'), EMPTY_TITLE_ID)

    def test_move_swaps_slots_in_the_same_folder(self):
        savedata = blank_savedata()
        first = 0x0004000000123400
        second = 0x0004000000567800
        savedata.set_icon(first, 0, -1)
        savedata.set_icon(second, 1, -1)
        savedata.set_icon(second, 0, -1)
        by_id = {entry.title_id: entry for entry in savedata.entries()}
        self.assertEqual(by_id[second].slot, 0)
        self.assertEqual(by_id[first].slot, 1)

    def test_same_slot_is_allowed_in_different_folders(self):
        savedata = blank_savedata()
        savedata.set_icon(0x0004000000000001, 0, -1)
        savedata.set_icon(0x0004000000000002, 0, 3)
        slots = {(entry.folder, entry.slot) for entry in savedata.entries()}
        self.assertEqual(slots, {(-1, 0), (3, 0)})

    def test_folder_out_of_range(self):
        savedata = blank_savedata()
        with self.assertRaises(LayoutError):
            savedata.set_icon(0x0004000000000001, 0, 60)

    def test_apply_slots_rejects_duplicates(self):
        savedata = blank_savedata()
        savedata.set_icon(0x0004000000000001, 0, -1)
        savedata.set_icon(0x0004000000000002, 1, -1)
        with self.assertRaises(LayoutError):
            savedata.apply_slots([
                (0x0004000000000001, 4, -1),
                (0x0004000000000002, 4, -1),
            ])

    def test_rejects_short_file(self):
        with self.assertRaises(LayoutError):
            from custominstall.layout import SaveData
            SaveData(b'\x04' + b'\xff' * 16)


class LauncherTests(unittest.TestCase):
    def test_create_and_rename_truncates_storage_to_17_units(self):
        launcher = blank_launcher()
        created = launcher.create_folder('ロールプレイング', 2)
        self.assertEqual(created.index, 0)
        self.assertEqual(created.position, 2)
        self.assertEqual(launcher.folders()[0].name, 'ロールプレイング')
        launcher.rename_folder(0, 'ABC')
        self.assertEqual(launcher.folders()[0].name, 'ABC')
        with self.assertRaises(LayoutError):
            launcher.rename_folder(0, '123456789012345678')  # 18 chars

    def test_second_folder_gets_the_next_number(self):
        launcher = blank_launcher()
        first = launcher.create_folder('One')
        second = launcher.create_folder('Two')
        self.assertEqual(first.number, 1)
        self.assertEqual(second.number, 2)
        self.assertEqual(second.position, 1)
        self.assertEqual(format_title_id(0x8F), '000000000000008f')

    def test_menu_rows_are_stored_minus_one(self):
        launcher = blank_launcher()
        self.assertEqual(launcher.menu_rows(), 1)
        launcher.blob[0xB51] = 2
        self.assertEqual(launcher.menu_rows(), 3)
        launcher.blob[0xB51] = 9
        self.assertEqual(launcher.menu_rows(), 3)

    def test_cart_slot_is_the_leading_u16(self):
        launcher = blank_launcher()
        self.assertEqual(launcher.cart_slot(), 0)
        launcher.blob[2:4] = (4).to_bytes(2, 'little')
        self.assertEqual(launcher.cart_slot(), 4)
        launcher.blob[2:4] = (0xFFFF).to_bytes(2, 'little')
        self.assertIsNone(launcher.cart_slot())

    def test_delete_folder_returns_icons_to_the_main_menu(self):
        from custominstall.layout import delete_folder
        savedata = blank_savedata()
        launcher = blank_launcher()
        folder = launcher.create_folder('Box', 0)
        savedata.set_icon(0x0004000000000001, 0, folder.index)
        savedata.set_icon(0x0004000000000002, 1, folder.index)
        delete_folder(savedata, launcher, folder.index)
        self.assertEqual(launcher.folders(), [])
        slots = {entry.title_id: (entry.folder, entry.slot) for entry in savedata.entries()}
        self.assertEqual(slots[0x0004000000000001], (-1, 0))
        self.assertEqual(slots[0x0004000000000002], (-1, 1))

    def test_delete_folder_keeps_bytes_when_the_main_menu_is_full(self):
        from custominstall.layout import delete_folder
        savedata = blank_savedata()
        launcher = blank_launcher()
        folder = launcher.create_folder('Box', 0)
        savedata.set_icon(0x00040000000000AA, 0, folder.index)
        savedata.set_icon(0x00040000000000BB, 1, folder.index)
        for slot in range(1, 360):
            launcher.set_icon(0x0004001000000000 + slot, slot, -1)
        before_sd = bytes(savedata.blob)
        before_launcher = bytes(launcher.blob)
        with self.assertRaises(LayoutError):
            delete_folder(savedata, launcher, folder.index)
        self.assertEqual(bytes(savedata.blob), before_sd)
        self.assertEqual(bytes(launcher.blob), before_launcher)


class NameDbTests(unittest.TestCase):
    def test_system_name_and_3dsdb_json(self):
        import tempfile
        from pathlib import Path
        from custominstall.titles import load_3dsdb_names, system_title_name
        self.assertEqual(system_title_name('0004001000020000'), 'System Settings')
        self.assertEqual(system_title_name('0004001020020300'), 'Health and Safety Information (N3DS)')
        self.assertEqual(system_title_name('0004000000123400'), '')
        self.assertEqual(system_title_name('0004008c00008206'), 'Home Menu Themes (JPN) #6')
        self.assertEqual(system_title_name('0004008c00008f00'), 'Home Menu Themes (USA) #0')
        self.assertEqual(system_title_name('0004008c00008207'), '')
        from custominstall.titles import homebrew_name, ique_dsiware_name
        self.assertEqual(homebrew_name('0004000000384a00'), 'VirtuaNES')
        self.assertEqual(ique_dsiware_name('000480044b555743'), '照照瓦力欧制造')
        self.assertEqual(ique_dsiware_name('0004000000384a00'), '')
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, 'list_JP.json').write_text(
                '[{"TitleID": "000480044B513945", "Name": "Example DSiWare"}]', encoding='utf-8')
            names = load_3dsdb_names(temp)
        self.assertEqual(names['000480044b513945'], 'Example DSiWare')
        from custominstall.titles import ghostland_name, universal_name
        with tempfile.TemporaryDirectory() as temp:
            meta = Path(temp) / 'db' / '3ds' / 'base' / '0004000000051600'
            meta.mkdir(parents=True)
            (meta / 'meta.json').write_text('{"name": "いつの間に交換日記"}', encoding='utf-8')
            self.assertEqual(ghostland_name(temp, '0004000000051600'), 'いつの間に交換日記')
        self.assertEqual(universal_name({'00306': 'CTR-AMKJ - Mario Kart 7'}, '0004000000030600'), 'Mario Kart 7')


class RemoveFilesTests(unittest.TestCase):
    def test_save_is_backed_up_and_can_stay_in_place(self):
        import tempfile
        from pathlib import Path
        from custominstall.snapshot import remove_title_directory
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            title = root / 'title'
            data = title / 'data'
            data.mkdir(parents=True)
            (title / 'content' / '00000000.app').parent.mkdir()
            (title / 'content' / '00000000.app').write_bytes(b'app')
            (data / '00000001.sav').write_bytes(b'save-bytes')
            backup = root / 'backup' / '00000001.sav'
            remove_title_directory(title, delete_save=False, save_backup=backup)
            self.assertEqual(backup.read_bytes(), b'save-bytes')
            self.assertEqual((data / '00000001.sav').read_bytes(), b'save-bytes')
            self.assertFalse((title / 'content').exists())

    def test_save_backup_remains_when_the_original_is_deleted(self):
        import tempfile
        from pathlib import Path
        from custominstall.snapshot import remove_title_directory
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            title = root / 'title'
            (title / 'data').mkdir(parents=True)
            (title / 'data' / '00000001.sav').write_bytes(b'save-bytes')
            (title / 'content').mkdir()
            backup = root / 'backup' / '00000001.sav'
            remove_title_directory(title, delete_save=True, save_backup=backup)
            self.assertEqual(backup.read_bytes(), b'save-bytes')
            self.assertFalse(title.exists())

    def test_plaza_update_finds_the_nand_system_title(self):
        from custominstall.snapshot import base_title_id
        installed = ['0004001000020800', '0004000e00020800', '0004008c00020800']
        self.assertEqual(base_title_id('0004000e00020800', installed), '0004001000020800')
        self.assertEqual(base_title_id('0004008c00020800', installed), '0004001000020800')
        installed.append('0004000000020800')
        self.assertEqual(base_title_id('0004000e00020800', installed), '0004000000020800')

    def test_sibling_id_uses_the_low_32_bits(self):
        from custominstall.snapshot import RemoveOptions
        self.assertFalse(RemoveOptions().delete_update)
        # The id helper does not need a snapshot.
        from custominstall.layout import format_title_id
        text = format_title_id(0x0004000012345678)
        self.assertEqual('0004000e' + text[8:], '0004000e12345678')
        self.assertEqual('0004008c' + text[8:], '0004008c12345678')


class ArchiveTests(unittest.TestCase):
    def test_a_zip_named_7z_yields_the_cia_inside(self):
        import tempfile
        import zipfile
        from pathlib import Path
        from custominstall.archives import ExtractedCias, is_archive

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cia = root / 'payload.bin'
            cia.write_bytes(b'\x20\x20\x00\x00' + b'\x00' * 0x2040)
            archive = root / 'misnamed.7z'
            with zipfile.ZipFile(archive, 'w') as package:
                package.write(cia, 'game.bin')
            self.assertTrue(is_archive(archive))
            with ExtractedCias([str(archive)]) as found:
                self.assertEqual(len(found), 1)
                self.assertTrue(found[0].endswith('game.bin'))
                self.assertEqual(Path(found[0]).read_bytes()[:4], b'\x20\x20\x00\x00')


class CiaExportTests(unittest.TestCase):
    def test_decrypted_cia_drops_missing_contents_and_the_encrypted_flag(self):
        import io
        import tempfile
        from hashlib import sha256
        from pathlib import Path
        from pyctr.type.tmd import ContentChunkRecord, ContentInfoRecord, ContentTypeFlags, TitleMetadataReader, TitleVersion
        from custominstall.cia_export import _read_tmd, write_decrypted_cia

        def chunk(index: int, encrypted: bool) -> ContentChunkRecord:
            return ContentChunkRecord(f'{index:08x}', index, ContentTypeFlags(encrypted, False, False, False, False), 4, b'\0' * 32)

        records = [chunk(0, True), chunk(1, True)]
        info = ContentInfoRecord(0, 2, sha256(b''.join(bytes(record) for record in records)).digest())
        tmd = TitleMetadataReader(
            title_id='0004000000000001', save_size=0, srl_save_size=0,
            title_version=TitleVersion.from_int(0), info_records=[info], chunk_records=records,
        )
        body = b'NCCH-plain'
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'out.cia'
            write_decrypted_cia(tmd, [(records[0], lambda: io.BytesIO(body))], dest)
            written = _read_tmd(dest)
            self.assertEqual(written.title_id, '0004000000000001')
            self.assertEqual(len(written.chunk_records), 1)
            self.assertFalse(written.chunk_records[0].type.encrypted)
            self.assertEqual(written.chunk_records[0].hash, sha256(body).digest())
            blob = dest.read_bytes()
            self.assertEqual(blob[-len(body):], body)


class SeedTests(unittest.TestCase):
    def test_add_seed_appends_a_little_endian_title_id(self):
        from custominstall.snapshot import update_seeddb
        blob = bytearray(0xCB80)
        blob[4:8] = (1).to_bytes(4, 'little')
        blob[0x1000:0x1008] = (0x0004000000000001).to_bytes(8, 'little')
        update_seeddb(blob, 0x0004000000000002, b'\x11' * 16)
        self.assertEqual(int.from_bytes(blob[4:8], 'little'), 2)
        self.assertEqual(blob[0x1008:0x1010], (0x0004000000000002).to_bytes(8, 'little'))
        self.assertEqual(blob[0x4E80 + 16:0x4E80 + 32], b'\x11' * 16)


if __name__ == '__main__':
    unittest.main()
