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
