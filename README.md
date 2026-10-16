# custom-install
Installs a title directly to an SD card for the Nintendo 3DS. Originally created late June 2019.

## Title manager

The title manager edits a GodMode9 snapshot of the HOME Menu, then writes it back in one import. It does not edit the console while HOME Menu is running. The original CIA installer is still `python -m custominstall` and `python -m custominstall.gui`.

### One-time setup

```
git submodule update --init third_party/3dsdb third_party/universal-db third_party/3DS-rom-tools
```

`third_party/3dsdb-ghostland` is optional. It is large because it contains images. Skip it if you do not need its names and icons. `third_party/3DS-rom-tools` supplies `seeddb/seeddb.bin` for seed-encrypted CIAs.

Linux still needs `save3ds_fuse` on `PATH`, in `custominstall/bin/linux`, or in `CUSTOM_INSTALL_SAVE3DS_PATH`. Tk is required for the window (`python3-tk` on Debian/Ubuntu).

### Export and import

Copy `extras/gm9/ci-manage.gm9` to `sdmc:/gm9/scripts/`. Boot GodMode9 with START, then open Scripts and run `ci-manage`.

1. Export. The snapshot is written to `sdmc:/gm9/out/ci-manage/<date>-<time>/`.
2. Edit that folder on a computer. Do not open HOME Menu between export and import.
3. Import with the same script. It writes the SD databases, extdata, `Launcher.dat`, `SEEDDB`, and the queued tickets. It does not write `boot9`, `otp`, or `movable.sed`.

### Manager window

```
python -m custominstall.manage_gui
```

The window only asks for two paths:

| Field | Meaning |
| --- | --- |
| SD root | Root of the SD card, such as `E:\` |
| Snapshot | The `ci-manage` export directory |

`boot9.bin` and `movable.sed` are read from `<snapshot>/nand/`. Names come from the submodules, then from `custominstall/data/`. Icons prefer `icon.jpg` from the ghost-land checkout. The capacity bar is the free space of the SD volume.

`CUSTOMINSTALL_SD` and `CUSTOMINSTALL_SNAPSHOT` fill those two fields. `CUSTOMINSTALL_ARCHIVE_PASSWORD` is the password used when an added file is a zip, 7z, or rar. The archive type is taken from the file header, not the extension. patool and rarfile are tried first. zip and 7z then fall back to in-process readers. A rar still needs UnRAR, unar, or bsdtar.

The grid is the HOME Menu: column first, row count from the console, horizontal scroll. The list shows every title. NAND titles are marked `(nand)` and cannot be deleted. Selecting an update or DLC highlights the installed base with the same low 32 bits (`00040000`, `00040002`, or `00040010`). Home Menu theme DLC (`0004008c00008200`–`0004008c00008206` on a Japanese console, and the same range for the other regions) has no base icon.

Save writes the snapshot only when something changed. It first copies the replaced files to `backup-<timestamp>/`. If that copy fails, the window asks whether to continue. Closing the window with unsaved edits asks before discarding them.

Add CIA writes the title to the SD card immediately and queues a stub ticket in the snapshot. Dump CIA writes a title-key-decrypted CIA for an SD application, update, or DLC. It does not restore the original title-key encryption. DSiWare and NAND-only titles have no SD contents, so they cannot be dumped. Delete can remove an SD application, with optional update, DLC, save, and extdata, or an update or DLC by itself. Import the snapshot before opening HOME Menu after any of these.

### Command line

```
python -m custominstall titles list --snapshot <export>
python -m custominstall titles layout --snapshot <export>
python -m custominstall titles move --snapshot <export> --id <title-id> --slot <n> --folder <id>
python -m custominstall titles sort --snapshot <export> --by name
python -m custominstall titles folder --snapshot <export> create --name <name>
python -m custominstall titles add --snapshot <export> --sd <sd> <file.cia>
python -m custominstall titles remove --snapshot <export> --sd <sd> --id <title-id>
```

`move`, `sort`, `folder`, `add`, and `remove` save the snapshot immediately. `add` and `remove` also change the SD card.

## Summary

### Windows standalone

1. [Dump boot9.bin and movable.sed](https://wiki.hacks.guide/wiki/3DS:Dump_system_files) from a 3DS system.
2. Download the [latest releases](https://github.com/ihaveamac/custom-install/releases).
3. Extract and run ci-gui. Read `windows-quickstart.txt`.

### With installed Python

> [!NOTE]
> Windows users: Enabling "Add Python 3.X to PATH" is **NOT** required! Python is installed with the `py` launcher by default.

1. [Dump boot9.bin and movable.sed](https://ihaveamac.github.io/dump.html) from a 3DS system.
2. Install the packages:
  * Windows: `py -3 -m pip install --user --upgrade https://github.com/ihaveamac/custom-install/archive/refs/heads/python-package.zip`
  * macOS/Linux: `python3 -m pip install --user --upgrade https://github.com/ihaveamac/custom-install/archive/refs/heads/python-package.zip`

To run the GUI:
* Windows: `py -3 -m custominstall.gui`
* macOS/Linux: `python3 -m custominstall.gui`

To run the command line version:
* Windows: `py -3 -m custominstall`
* macOS/Linux: `python3 -m custominstall`

## Setup
Linux users must build [wwylele/save3ds](https://github.com/wwylele/save3ds) and place `save3ds_fuse` in one of these places:
* A directory in `PATH`
* In `custominstall/bin/linux`
* Set the environment variable `CUSTOM_INSTALL_SAVE3DS_PATH` to the `save3ds_fuse` binary

movable.sed is required and can be provided with `-m` or `--movable`.

boot9 is needed:
* `-b` or `--boot9` argument (if set)
* `BOOT9_PATH` environment variable (if set)
* `%APPDATA%\3ds\boot9.bin` (Windows-specific)
* `~/Library/Application Support/3ds/boot9.bin` (macOS-specific)
* `~/.3ds/boot9.bin`
* `~/3ds/boot9.bin`

A [SeedDB](https://github.com/ihaveamac/3DS-rom-tools/wiki/SeedDB-list) is needed for newer games (2015+) that use seeds.  
SeedDB is checked in order of:
* `-s` or `--seeddb` argument (if set)
* `SEEDDB_PATH` environment variable (if set)
* `%APPDATA%\3ds\seeddb.bin` (Windows-specific)
* `~/Library/Application Support/3ds/seeddb.bin` (macOS-specific)
* `~/.3ds/seeddb.bin`
* `~/3ds/seeddb.bin`

## custom-install-finalize
custom-install-finalize installs a ticket, plus a seed if required. This is required for the title to appear and function.

This can be built as most 3DS homebrew projects [with devkitARM](https://www.3dbrew.org/wiki/Setting_up_Development_Environment).

## Usage
Use `-h` to view arguments.

Examples:
```
py -3 -m custominstall -b boot9.bin -m movable.sed --sd E:\ file.cia file2.cia
python3 -m custominstall -b boot9.bin -m movable.sed --sd /Volumes/GM9SD file.cia file2.cia
python3 -m custominstall -b boot9.bin -m movable.sed --sd /media/GM9SD file.cia file2.cia
```

## GUI
A GUI is provided to make the process easier.

### GUI Setup
Linux users may need to install a Tk package:
* Ubuntu/Debian: `sudo apt install python3-tk`
* Arch: `sudo pacman -S tk`
* Fedora: `sudo dnf install python3-tkinter`

## Development

### Building Windows standalone

> [!WARNING]
> This section is OUTDATED and currently does not work with the Python package setup.

## License/Credits
[save3ds by wwylele](https://github.com/wwylele/save3ds) is used to interact with the Title Database (details in `bin/README`).

Thanks to @nek0bit for redesigning `custominstall.py` to work as a module, and for implementing an earlier GUI.

Thanks to @LyfeOnEdge from the [brewtools Discord](https://brewtools.dev) for designing the second version of the GUI. Special thanks to CrafterPika and archbox for testing.

Thanks to @BpyH64 for [researching how to generate the cmacs](https://github.com/d0k3/GodMode9/issues/340#issuecomment-487916606).
