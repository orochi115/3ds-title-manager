# This file is a part of custom-install.
#
# Copyright (c) 2019 Ian Burgwin
# This file is licensed under The MIT License (MIT).

"""Home Menu style manager for a ci-manage snapshot.

The grid shows application icons and folders. The list shows every title.
Both read the same Snapshot object.
"""

from __future__ import annotations

import os
from os.path import isfile
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
import tkinter.simpledialog as simpledialog
import tkinter.ttk as ttk
from threading import Thread

from .layout import TITLE_SLOTS, LayoutError, format_title_id
from .snapshot import RemoveOptions, Snapshot
from .titles import KIND_BY_HIGH, TitleManager, load_3dsdb_names, system_title_name

CELL = 68


def _env(name: str) -> str:
    return os.environ.get(name, '')


def _rgb_photo(array) -> tk.PhotoImage:
    image = tk.PhotoImage(width=48, height=48)
    rows = []
    for row in array:
        rows.append('{' + ' '.join(f'#{r:02x}{g:02x}{b:02x}' for r, g, b in row) + '}')
    image.put(' '.join(rows))
    return image


class ManagerApp(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master)
        self.snap: Snapshot | None = None
        self.dirty = False
        self.generation = 0
        self.view_folder = -1
        self.selected: tuple | None = None
        self.photos: dict[str, tk.PhotoImage] = {}
        self.extdata_ids: dict[str, str] = {}
        self.cells: dict[int, tuple] = {}
        self.names_pending = False
        self._build()

    def _build(self) -> None:
        paths = ttk.Frame(self)
        paths.pack(fill=tk.X, padx=6, pady=4)
        self.path_vars = {}
        defaults = {
            'Snapshot': _env('CUSTOMINSTALL_SNAPSHOT'),
            'SD root': _env('CUSTOMINSTALL_SD'),
            'boot9': _env('CUSTOMINSTALL_BOOT9'),
            'movable.sed': _env('CUSTOMINSTALL_MOVABLE'),
            'seeddb': _env('CUSTOMINSTALL_SEEDDB') or _env('SEEDDB_PATH'),
        }
        for row, (label, value) in enumerate(defaults.items()):
            ttk.Label(paths, text=label, width=12).grid(row=row, column=0, sticky=tk.W)
            var = tk.StringVar(value=value)
            ttk.Entry(paths, textvariable=var).grid(row=row, column=1, sticky=tk.EW)
            self.path_vars[label] = var
        paths.columnconfigure(1, weight=1)

        buttons = ttk.Frame(self)
        buttons.pack(fill=tk.X, padx=6)
        for col, (text, command) in enumerate((
            ('Refresh', self.refresh),
            ('Save snapshot', self.save_snapshot),
            ('Add CIA', self.add_cia),
            ('Sort by name', lambda: self.sort_apps('name')),
            ('Sort by ID', lambda: self.sort_apps('id')),
            ('New folder', self.create_folder),
            ('Extdata', self.open_extdata),
        )):
            ttk.Button(buttons, text=text, command=command).grid(row=0, column=col, padx=2, pady=2)

        body = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        left = ttk.Frame(body)
        nav = ttk.Frame(left)
        nav.pack(fill=tk.X)
        ttk.Button(nav, text='Main menu', command=lambda: self.show_folder(-1)).pack(side=tk.LEFT)
        self.place_label = ttk.Label(nav, text='Main menu')
        self.place_label.pack(side=tk.LEFT, padx=8)
        grid_frame = ttk.Frame(left)
        grid_frame.pack(fill=tk.BOTH, expand=True)
        grid_frame.rowconfigure(0, weight=1)
        grid_frame.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(grid_frame, background='#1b2838', highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky=tk.NSEW)
        grid_scroll = ttk.Scrollbar(grid_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        grid_scroll.grid(row=1, column=0, sticky=tk.EW)
        self.canvas.configure(xscrollcommand=grid_scroll.set)
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_motion)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<Double-Button-1>', self._on_double)
        self.canvas.bind('<MouseWheel>', lambda event: self.canvas.xview_scroll(int(-event.delta / 120), 'units'))
        self.grid_rows = 3
        self.grid_span = 0
        body.add(left, weight=3)

        right = ttk.Frame(body)
        columns = ('tid', 'kind', 'slot', 'folder', 'name')
        list_frame = ttk.Frame(right)
        list_frame.pack(fill=tk.BOTH, expand=True)
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(list_frame, columns=columns, show='headings')
        for key, label, width in (
            ('tid', 'Title ID', 140),
            ('kind', 'Type', 70),
            ('slot', 'Slot', 46),
            ('folder', 'Folder', 56),
            ('name', 'Name', 180),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=tk.W)
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        list_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        list_scroll.grid(row=0, column=1, sticky=tk.NS)
        self.tree.configure(yscrollcommand=list_scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self._on_tree)
        self.tree.bind('<Double-Button-1>', self._on_tree_double)
        self.tree.bind('<MouseWheel>', lambda event: self.tree.yview_scroll(int(-event.delta / 120), 'units'))
        self._ignore_tree = False
        self.detail = tk.Text(right, height=8, wrap='word')
        self.detail.pack(fill=tk.X, pady=4)
        actions = ttk.Frame(right)
        actions.pack(fill=tk.X)
        self.buttons = {}
        for text, command in (
            ('Open folder', self.open_selected_folder),
            ('Move to...', self.move_to_screen),
            ('Rename folder', self.rename_folder),
            ('Delete folder', self.delete_folder),
            ('Delete app', self.delete_app),
        ):
            button = ttk.Button(actions, text=text, command=command)
            button.pack(side=tk.LEFT, padx=2, pady=2)
            self.buttons[text] = button
        self._sync_buttons()
        body.add(right, weight=2)

        self.status = ttk.Label(self, text='Open a snapshot with Refresh. Icons load after the list.')
        self.status.pack(fill=tk.X, padx=6, pady=4)

    def _paths(self) -> dict[str, str]:
        return {key: var.get().strip() for key, var in self.path_vars.items()}

    def refresh(self) -> None:
        if self.dirty and not mb.askyesno('Refresh', 'Discard unsaved layout changes and read the snapshot again?'):
            return
        paths = self._paths()
        if not paths['Snapshot']:
            self._set_status('Choose a ci-manage export folder.')
            return
        self._set_status('Reading the snapshot...')
        self.generation += 1
        generation = self.generation

        def work():
            try:
                snap = Snapshot(paths['Snapshot'], sd=paths['SD root'] or None, seeddb=paths['seeddb'] or None)
                rows = snap.list_titles(read_names=False)
            except Exception as exc:
                self.after(0, lambda: self._set_status(f'{type(exc).__name__}: {exc}'))
                return
            self.after(0, lambda: self._loaded(generation, snap, rows))

        Thread(target=work, daemon=True).start()

    def _loaded(self, generation: int, snap: Snapshot, rows) -> None:
        if generation != self.generation:
            return
        self.snap = snap
        self.dirty = False
        self.photos.clear()
        self.extdata_ids.clear()
        self.view_folder = -1
        self._fill_names_from_db()
        self._draw()
        apps = [row.title_id for row in rows if row.kind == 'app']
        limit = ''
        if len(apps) > 300:
            limit = ' More than 300 application icons.'
        self._set_status(f'{len(rows)} titles, {len(apps)} applications.{limit} Loading names and icons.')
        self.names_pending = True
        paths = self._paths()

        def names():
            try:
                manager = TitleManager(paths['SD root'], paths['movable.sed'], paths['boot9'], paths['seeddb'] or None)
            except Exception as exc:
                self.after(0, lambda: self._names_finished(generation, str(exc)))
                return
            for title_id in apps:
                if generation != self.generation:
                    return
                name, icon, extdata_id = manager.read_display(title_id)
                self.after(0, lambda tid=title_id, name=name, icon=icon, extdata_id=extdata_id:
                           self._apply_meta(generation, tid, name, icon, extdata_id))
            self.after(0, lambda: self._names_finished(generation, ''))

        Thread(target=names, daemon=True).start()

    def _apply_meta(self, generation: int, title_id: str, name: str, icon, extdata_id: str | None) -> None:
        if generation != self.generation or self.snap is None:
            return
        if name:
            self.snap._names[title_id] = name
            if self.tree.exists(f't-{title_id}'):
                values = list(self.tree.item(f't-{title_id}', 'values'))
                values[4] = name
                self.tree.item(f't-{title_id}', values=values)
        if icon is not None:
            self.photos[title_id] = _rgb_photo(icon)
        if extdata_id:
            self.extdata_ids[title_id] = extdata_id
        self._draw_grid()
        if self.selected and self.selected[1] == title_id:
            self._draw_detail()

    def _names_finished(self, generation: int, error: str) -> None:
        if generation != self.generation:
            return
        self.names_pending = False
        self._set_status(error or 'Names and icons are loaded. Save snapshot writes the export.')

    def _fill_names_from_db(self) -> None:
        directory = os.environ.get('CUSTOMINSTALL_3DSDB') or r'D:\LLM_Work\CustomInstall\3dsdb\jsons'
        try:
            known = load_3dsdb_names(directory)
        except OSError:
            known = {}
        for title_id in [row.title_id for row in self.snap.list_titles(read_names=False)]:
            if not self.snap._names.get(title_id):
                self.snap._names[title_id] = known.get(title_id) or system_title_name(title_id)
        for entry in self.snap.launcher.entries():
            title_id = format_title_id(entry.title_id)
            if not self.snap._names.get(title_id):
                self.snap._names[title_id] = known.get(title_id) or system_title_name(title_id)

    def _all_rows(self) -> list[tuple]:
        """Every SD title, NAND title, and folder from the open snapshot."""
        snap = self.snap
        rows = []
        seen = set()
        for row in snap.list_titles(read_names=False):
            seen.add(row.title_id)
            rows.append((row.title_id, row.kind, row.slot, row.folder, snap._names.get(row.title_id, row.name)))
        for entry in snap.launcher.entries():
            title_id = format_title_id(entry.title_id)
            if title_id in seen:
                continue
            kind = KIND_BY_HIGH.get(title_id[:8], 'system')
            rows.append((title_id, kind, entry.slot, entry.folder, snap._names.get(title_id, '')))
        for folder in snap.folders():
            rows.append((f'folder-{folder.index}', 'folder', folder.position, folder.index, folder.name))
        cart = snap.launcher.cart_slot()
        if cart is not None:
            rows.append(('gamecard', 'card', cart, -1, 'Game Card'))
        return rows

    def _draw(self) -> None:
        if self.snap is None:
            return
        self._draw_list()
        self._draw_grid()
        self._draw_detail()

    def _draw_list(self) -> None:
        selected = self.tree.selection()
        yview = self.tree.yview()
        self._ignore_tree = True
        self.tree.delete(*self.tree.get_children())
        for title_id, kind, slot, folder, name in self._all_rows():
            iid = title_id if title_id == 'gamecard' or title_id.startswith('folder-') else f't-{title_id}'
            self.tree.insert('', tk.END, iid=iid, values=(
                title_id, kind, '' if slot is None else slot, '' if folder is None else folder, name,
            ))
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self.tree.yview_moveto(yview[0])
        self._ignore_tree = False

    def _view_rows(self) -> int:
        if self.view_folder < 0:
            return self.snap.launcher.menu_rows()
        for folder in self.snap.folders():
            if folder.index == self.view_folder:
                return min(6, max(1, folder.rows or 2))
        return 2

    def _grid_size(self, cells: dict[int, tuple], rows: int) -> int:
        highest = max(cells) if cells else 0
        span = max(highest + 1, rows * 6)
        remainder = span % rows
        if remainder:
            span += rows - remainder
        return min(TITLE_SLOTS, span)

    def cells_source(self) -> dict[int, tuple]:
        snap = self.snap
        cells = {}
        view = self.view_folder
        if view == -1:
            for folder in snap.folders():
                cells[folder.position] = ('folder', folder.index, folder.name)
        for entry in snap.savedata.entries():
            title_id = format_title_id(entry.title_id)
            if entry.folder != view:
                continue
            if entry.slot not in cells:
                kind = 'app' if title_id.startswith('00040000') else 'sd'
                cells[entry.slot] = (kind, title_id, snap._names.get(title_id, ''))
        for entry in snap.launcher.entries():
            if entry.folder != view or entry.slot in cells:
                continue
            title_id = format_title_id(entry.title_id)
            cells[entry.slot] = ('system', title_id, snap._names.get(title_id, ''))
        if view == -1:
            cart = snap.launcher.cart_slot()
            if cart is not None and cart not in cells:
                cells[cart] = ('card', 'gamecard', 'Game Card')
        return cells

    def _draw_grid(self) -> None:
        self.cells = self.cells_source()
        self.canvas.delete('all')
        rows = self._view_rows()
        span = self._grid_size(self.cells, rows)
        self.grid_rows = rows
        self.grid_span = span
        self.slot_rects = {}
        columns = (span + rows - 1) // rows
        self.canvas.configure(scrollregion=(0, 0, columns * CELL, rows * CELL + 8))
        for slot in range(span):
            column = slot // rows
            row = slot % rows
            x = column * CELL + 4
            y = row * CELL + 4
            kind = self.cells.get(slot)
            selected = kind is not None and self.selected == kind[:2]
            fill = '#3d5a80' if selected else '#243447'
            self.slot_rects[slot] = self.canvas.create_rectangle(x, y, x + 60, y + 60, fill=fill, outline='#0d1b2a')
            if kind is None:
                continue
            if kind[0] in ('app', 'sd') and kind[1] in self.photos:
                self.canvas.create_image(x + 30, y + 26, image=self.photos[kind[1]])
            elif kind[0] == 'folder':
                self.canvas.create_rectangle(x + 10, y + 16, x + 50, y + 48, fill='#e9c46a', outline='')
                self.canvas.create_text(x + 30, y + 32, text=kind[2][:6], width=48)
            elif kind[0] == 'card':
                self.canvas.create_rectangle(x + 16, y + 8, x + 46, y + 50, fill='#d8e2dc', outline='#1b2838')
                self.canvas.create_text(x + 30, y + 30, text='CARD', fill='#1b2838')
            elif kind[0] == 'system':
                label = kind[2] or kind[1][-8:]
                self.canvas.create_text(x + 30, y + 30, text=label[:12], fill='#8d99ae', width=52)
            elif kind[0] == 'sd':
                self.canvas.create_text(x + 30, y + 30, text=(kind[2] or kind[1][-8:])[:12], fill='#f1faee', width=52)
            else:
                self.canvas.create_text(x + 30, y + 30, text=kind[1][-4:], fill='#f1faee', font=('TkDefaultFont', 8))
        label = 'Main menu' if self.view_folder < 0 else self._folder_name(self.view_folder)
        self.place_label.configure(text=f'{label}  ·  {rows} rows, down each column')

    def _folder_name(self, index: int) -> str:
        for folder in self.snap.folders():
            if folder.index == index:
                return folder.name or f'Folder {index}'
        return f'Folder {index}'

    def _draw_detail(self) -> None:
        self.detail.delete('1.0', tk.END)
        if self.selected is None or self.snap is None:
            self.detail.insert(tk.END, 'Drag an icon onto another slot. Drop it on a folder to put it inside. Double-click a folder to open it.')
            self._sync_buttons()
            return
        kind, ident = self.selected
        if kind == 'card':
            self.detail.insert(tk.END, f'Game Card\nSlot {self.snap.launcher.cart_slot()}\nDrag it to another main-menu slot. It cannot go inside a folder.')
            self._sync_buttons()
            return
        if kind == 'folder':
            folder = next(item for item in self.snap.folders() if item.index == ident)
            inside = sum(1 for entry in self.snap.savedata.entries() if entry.folder == ident)
            self.detail.insert(tk.END, f'Folder {folder.index}\n{folder.name}\nSlot {folder.position}\n{inside} icons inside\nDrop an icon on this folder to move it in.')
            self._sync_buttons()
            return
        row = next((item for item in self._all_rows() if item[0] == ident), None)
        if row is None:
            self._sync_buttons()
            return
        title_id, row_kind, slot, folder, name = row
        extra = ''
        if row_kind == 'app':
            low = title_id[8:]
            related = [item[0] for item in self._all_rows() if item[0].endswith(low) and item[0] != title_id and item[1] != 'folder']
            ext = self.extdata_ids.get(title_id, 'not read yet' if self.names_pending else 'none')
            extra = f'\nRelated: {", ".join(related) if related else "none"}\nExtdata: {ext}'
        self.detail.insert(tk.END, f'{name or "(no name yet)"}\n{title_id}\n{row_kind}  slot {slot}  folder {folder}{extra}')
        self._sync_buttons()

    def _slot_at(self, event) -> int | None:
        rows = self.grid_rows
        if rows <= 0:
            return None
        column = int(self.canvas.canvasx(event.x) // CELL)
        row = int(self.canvas.canvasy(event.y) // CELL)
        if column < 0 or row < 0 or row >= rows:
            return None
        slot = column * rows + row
        if slot >= self.grid_span:
            return None
        return slot

    def _row_iid(self, kind: str, ident) -> str:
        if kind == 'folder':
            return f'folder-{ident}'
        if kind == 'card':
            return 'gamecard'
        return f't-{ident}'

    def _select_cell(self, slot: int | None) -> None:
        cell = self.cells.get(slot) if slot is not None else None
        self.selected = None if cell is None else cell[:2]
        for drawn, rect in self.slot_rects.items():
            chosen = self.cells.get(drawn)
            on = chosen is not None and self.selected == chosen[:2]
            self.canvas.itemconfig(rect, fill='#3d5a80' if on else '#243447')
        if self.selected is not None:
            iid = self._row_iid(*self.selected)
            if self.tree.exists(iid):
                self._ignore_tree = True
                self.tree.selection_set(iid)
                self.tree.see(iid)
                self._ignore_tree = False
        self._draw_detail()

    def _on_press(self, event) -> None:
        self._press_slot = self._slot_at(event)
        self._press_xy = (event.x, event.y)
        self._dragging = False
        self._ghost = None
        self._drop_mark = None
        self._select_cell(self._press_slot)

    def _on_motion(self, event) -> None:
        source = getattr(self, '_press_slot', None)
        if source is None or self.snap is None or source not in self.cells:
            return
        start = getattr(self, '_press_xy', (event.x, event.y))
        if not self._dragging and abs(event.x - start[0]) + abs(event.y - start[1]) < 6:
            return
        if not self._dragging:
            self._dragging = True
            self._ghost = self._make_ghost(source)
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        self.canvas.coords(self._ghost, x, y)
        self.canvas.tag_raise(self._ghost)
        dest = self._slot_at(event)
        if self._drop_mark is not None:
            self.canvas.delete(self._drop_mark)
            self._drop_mark = None
        if dest is not None:
            column = dest // self.grid_rows
            row = dest % self.grid_rows
            left = column * CELL + 4
            top = row * CELL + 4
            self._drop_mark = self.canvas.create_rectangle(
                left, top, left + 60, top + 60, outline='#f4a261', width=3)

    def _make_ghost(self, slot: int) -> int:
        cell = self.cells[slot]
        x = self.canvas.canvasx(self._press_xy[0])
        y = self.canvas.canvasy(self._press_xy[1])
        if cell[0] in ('app', 'sd') and cell[1] in self.photos:
            return self.canvas.create_image(x, y, image=self.photos[cell[1]])
        return self.canvas.create_text(x, y, text=(cell[2] or cell[0])[:12], fill='#f4a261')

    def _clear_drag(self) -> None:
        if getattr(self, '_ghost', None) is not None:
            self.canvas.delete(self._ghost)
        if getattr(self, '_drop_mark', None) is not None:
            self.canvas.delete(self._drop_mark)
        self._ghost = None
        self._drop_mark = None
        self._dragging = False
        self._press_slot = None

    def _on_release(self, event) -> None:
        source = getattr(self, '_press_slot', None)
        dragging = getattr(self, '_dragging', False)
        dest = self._slot_at(event)
        self._clear_drag()
        if not dragging or source is None or dest is None or source == dest or self.snap is None:
            return
        self._drop(source, dest)

    def _on_double(self, event) -> None:
        slot = self._slot_at(event)
        cell = self.cells.get(slot) if slot is not None else None
        if cell and cell[0] == 'folder':
            # The second click of a double-click also releases. Ignore that release
            # so it does not act on the folder that was just opened.
            self._press_slot = None
            self.show_folder(cell[1])

    def _on_tree(self, _event) -> None:
        if self._ignore_tree or self.snap is None:
            return
        selected = self.tree.selection()
        if not selected:
            return
        iid = selected[0]
        if iid == 'gamecard':
            self.selected = ('card', 'gamecard')
        elif iid.startswith('folder-'):
            self.selected = ('folder', int(iid.split('-', 1)[1]))
        elif iid.startswith('t-'):
            title_id = iid[2:]
            kind = 'app' if title_id.startswith('00040000') else 'sd' if self.snap.savedata.find(int(title_id, 16)) else 'system'
            self.selected = (kind, title_id)
        else:
            return
        self._draw_grid()
        self._draw_detail()

    def _on_tree_double(self, _event) -> None:
        self.open_selected_folder()

    def show_folder(self, index: int) -> None:
        self.view_folder = index
        self._draw()
        self.canvas.xview_moveto(0)

    def open_selected_folder(self) -> None:
        if self.selected and self.selected[0] == 'folder':
            self.show_folder(self.selected[1])

    def _drop(self, source: int, dest: int) -> None:
        origin = self.cells.get(source)
        if origin is None:
            return
        target = self.cells.get(dest)
        try:
            if target is not None and target[0] == 'folder' and origin[0] in ('app', 'sd', 'system'):
                self._place(origin, self._free_slot(target[1]), target[1])
            elif origin[0] == 'folder' or (target is not None and target[0] == 'folder'):
                if self.view_folder != -1:
                    raise LayoutError('folders stay on the main menu')
                if origin[0] == 'card' or (target is not None and target[0] == 'card'):
                    raise LayoutError('the game card stays on the main menu')
                self._place(origin, dest, -1)
                if target is not None:
                    self._place(target, source, -1)
            elif origin[0] == 'card' and self.view_folder != -1:
                raise LayoutError('the game card stays on the main menu')
            else:
                if target is not None:
                    self._place(target, source, self.view_folder)
                self._place(origin, dest, self.view_folder)
        except LayoutError as exc:
            self._set_status(str(exc))
            return
        self.dirty = True
        self.selected = origin[:2]
        self._draw()
        self._set_status('Moved in memory. Save snapshot before importing.')

    def _place(self, cell: tuple, slot: int, folder: int) -> None:
        kind, ident, _name = cell
        if kind == 'card':
            if folder != -1:
                raise LayoutError('the game card stays on the main menu')
            self.snap.launcher.set_cart_slot(slot)
        elif kind == 'folder':
            if folder != -1:
                raise LayoutError('folders stay on the main menu')
            self.snap.launcher.place_folder(ident, slot)
        elif kind in ('app', 'sd'):
            self.snap.savedata.place(int(ident, 16), slot, folder)
        else:
            self.snap.launcher.place(int(ident, 16), slot, folder)

    def _free_slot(self, folder: int) -> int:
        used = {entry.slot for entry in self.snap.savedata.entries() if entry.folder == folder}
        used.update(entry.slot for entry in self.snap.launcher.entries() if entry.folder == folder)
        if folder == -1:
            used.update(item.position for item in self.snap.folders())
            cart = self.snap.launcher.cart_slot()
            if cart is not None:
                used.add(cart)
        for slot in range(TITLE_SLOTS):
            if slot not in used:
                return slot
        raise LayoutError('that screen has no free slot')

    def _selected_cell(self) -> tuple | None:
        if self.selected is None:
            return None
        for cell in self.cells.values():
            if cell[:2] == self.selected:
                return cell
        kind, ident = self.selected
        if kind == 'card':
            return ('card', 'gamecard', 'Game Card')
        name = ''
        for title_id, _kind, _slot, _folder, row_name in self._all_rows():
            if title_id == ident or (kind == 'folder' and title_id == f'folder-{ident}'):
                name = row_name
                break
        return (kind, ident, name)

    def _current_folder(self) -> int:
        if not self.selected or self.selected[0] not in ('app', 'sd', 'system') or self.snap is None:
            return -1
        for title_id, _kind, _slot, folder, _name in self._all_rows():
            if title_id == self.selected[1]:
                return -1 if folder in (None, -1) else folder
        return -1

    def move_to_screen(self) -> None:
        cell = self._selected_cell()
        if cell is None or cell[0] not in ('app', 'sd', 'system') or self.snap is None:
            self._set_status('Select an application or system icon first.')
            return
        current = self._current_folder()
        choices: list[tuple[str, int]] = []
        if current != -1:
            choices.append(('Main menu', -1))
        for folder in self.snap.folders():
            if folder.index != current:
                choices.append((folder.name or f'Folder {folder.index}', folder.index))
        if not choices:
            self._set_status('Create a folder first.')
            return
        dialog = tk.Toplevel(self)
        dialog.title('Move to')
        dialog.transient(self.winfo_toplevel())
        dialog.geometry('280x260')
        ttk.Label(dialog, text='Choose a screen').pack(anchor=tk.W, padx=8, pady=6)
        listed = tk.Listbox(dialog, height=8)
        for label, _folder in choices:
            listed.insert(tk.END, label)
        listed.selection_set(0)
        listed.pack(fill=tk.BOTH, expand=True, padx=8)
        listed.focus_set()

        def confirm(_event=None):
            picked = listed.curselection()
            if not picked:
                return
            folder = choices[picked[0]][1]
            dialog.destroy()
            try:
                self._place(cell, self._free_slot(folder), folder)
            except LayoutError as exc:
                self._set_status(str(exc))
                return
            self.dirty = True
            if folder == -1:
                self.view_folder = -1
            self._draw()
            self._set_status('Moved in memory. Save snapshot before importing.')

        listed.bind('<Double-Button-1>', confirm)
        ttk.Button(dialog, text='Move', command=confirm).pack(pady=8)
        dialog.grab_set()

    def _sync_buttons(self) -> None:
        if not hasattr(self, 'buttons'):
            return
        kind = self.selected[0] if self.selected else ''
        ident = self.selected[1] if self.selected else ''
        current = self._current_folder() if kind in ('app', 'sd', 'system') else -1
        other_folders = bool(self.snap and any(item.index != current for item in self.snap.folders()))
        can_move = kind in ('app', 'sd', 'system') and (current != -1 or other_folders)
        enabled = {
            'Open folder': kind == 'folder',
            'Move to...': can_move,
            'Rename folder': kind == 'folder',
            'Delete folder': kind == 'folder',
            'Delete app': kind == 'app' and str(ident).startswith('00040000'),
        }
        for name, button in self.buttons.items():
            button.state(['!disabled'] if enabled.get(name) else ['disabled'])

    def create_folder(self) -> None:
        if self.snap is None:
            return
        name = simpledialog.askstring('New folder', 'Folder name (up to 17 characters)', parent=self)
        if not name:
            return
        try:
            created = self.snap.create_folder(name, self._free_slot(-1))
        except (LayoutError, OSError) as exc:
            self._set_status(str(exc))
            return
        self.dirty = True
        self.selected = ('folder', created.index)
        self.view_folder = -1
        self._draw()

    def rename_folder(self) -> None:
        if not self.selected or self.selected[0] != 'folder':
            self._set_status('Select a folder first.')
            return
        current = self._folder_name(self.selected[1])
        name = simpledialog.askstring('Rename folder', 'Folder name', initialvalue=current, parent=self)
        if not name:
            return
        try:
            self.snap.rename_folder(self.selected[1], name)
        except LayoutError as exc:
            self._set_status(str(exc))
            return
        self.dirty = True
        self._draw()

    def delete_folder(self) -> None:
        if not self.selected or self.selected[0] != 'folder':
            self._set_status('Select a folder first.')
            return
        index = self.selected[1]
        if not mb.askyesno('Delete folder', 'Move the icons inside back to the main menu and remove this folder?'):
            return
        try:
            self.snap.delete_folder(index)
        except LayoutError as exc:
            self._set_status(str(exc))
            return
        self.dirty = True
        self.selected = None
        self.view_folder = -1
        self._draw()
        self._set_status('Folder removed in memory. Save snapshot before importing.')

    def sort_apps(self, key: str) -> None:
        if self.snap is None:
            return
        if key == 'name' and self.names_pending:
            self._set_status('Wait until names finish loading, then sort by name.')
            return
        self.snap.sort_apps(key)
        self.dirty = True
        self._draw()
        self._set_status(f'Sorted by {key} in memory. Save snapshot before importing.')

    def save_snapshot(self) -> None:
        if self.snap is None:
            return
        self._set_status('Saving the snapshot...')

        def work():
            try:
                backup = self.snap.save()
            except Exception as exc:
                self.after(0, lambda: self._set_status(f'{type(exc).__name__}: {exc}'))
                return
            self.after(0, lambda: self._saved(backup))

        Thread(target=work, daemon=True).start()

    def _saved(self, backup) -> None:
        self.dirty = False
        self._set_status(f'Saved. Backup: {backup}. Import with GodMode9 before opening HOME Menu.')

    def add_cia(self) -> None:
        if self.snap is None or not self._paths()['SD root']:
            self._set_status('Refresh a snapshot and set the SD root before adding a CIA.')
            return
        paths = fd.askopenfilenames(parent=self, title='CIA files', filetypes=[('CIA', '*.cia'), ('All', '*.*')])
        if not paths:
            return
        if not mb.askyesno('Add CIA', 'Install onto the SD card now and record tickets in the snapshot?'):
            return
        seeddb = self._paths()['seeddb'] or None

        def work():
            try:
                installed = self.snap.add_cias(list(paths), seeddb=seeddb)
            except Exception as exc:
                self.after(0, lambda: self._set_status(f'{type(exc).__name__}: {exc}'))
                return
            self.after(0, lambda: self._added(installed))

        Thread(target=work, daemon=True).start()

    def _added(self, installed: list[str]) -> None:
        self.dirty = True
        self._draw()
        self._set_status(f'Installed {", ".join(installed)}. Save snapshot, then import before HOME Menu.')

    def delete_app(self) -> None:
        if not self.selected or self.selected[0] != 'app' or self.snap is None:
            self._set_status('Select an application. Updates and DLC are removed from its dialog.')
            return
        title_id = self.selected[1]
        paths = self._paths()
        self._set_status('Looking up the save, extdata, update, and DLC...')

        def work():
            try:
                info = _delete_info(self.snap, title_id, paths)
            except Exception as exc:
                self.after(0, lambda: self._set_status(f'{type(exc).__name__}: {exc}'))
                return
            self.after(0, lambda: self._confirm_delete(title_id, info))

        Thread(target=work, daemon=True).start()

    def _confirm_delete(self, title_id: str, info: dict) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(f'Delete {title_id}')
        dialog.transient(self.winfo_toplevel())
        ttk.Label(dialog, text='Save data and extdata are copied to custom-install-backup either way.').pack(anchor=tk.W, padx=8, pady=6)
        flags = {}
        for key, label in (
            ('delete_update', f'Delete update ({info["update"] or "not installed"})'),
            ('delete_dlc', f'Delete DLC ({info["dlc"] or "not installed"})'),
            ('delete_save', f'Delete save data ({"present" if info["save"] else "none"})'),
            ('delete_extdata', f'Delete extdata ({info["extdata"] or "none"})'),
        ):
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(dialog, text=label, variable=var).pack(anchor=tk.W, padx=8)
            flags[key] = var

        def confirm():
            options = RemoveOptions(
                delete_update=flags['delete_update'].get() and bool(info['update']),
                delete_dlc=flags['delete_dlc'].get() and bool(info['dlc']),
                delete_save=flags['delete_save'].get(),
                delete_extdata=flags['delete_extdata'].get() and bool(info['extdata']),
            )
            dialog.destroy()
            self._run_delete(title_id, options, info['extdata'])

        ttk.Button(dialog, text='Delete application', command=confirm).pack(pady=8)
        dialog.grab_set()

    def _run_delete(self, title_id: str, options: RemoveOptions, extdata_id: str | None) -> None:
        self._set_status('Deleting...')

        def work():
            try:
                backup = self.snap.remove(title_id, options, extdata_id=extdata_id if options.delete_extdata or extdata_id else None)
            except Exception as exc:
                self.after(0, lambda: self._set_status(f'{type(exc).__name__}: {exc}'))
                return
            self.after(0, lambda: self._deleted(title_id, backup))

        Thread(target=work, daemon=True).start()

    def _deleted(self, title_id: str, backup) -> None:
        self.dirty = True
        self.selected = None
        self.photos.pop(title_id, None)
        self._draw()
        self._set_status(f'Removed {title_id}. Backup: {backup}. Save snapshot, then import before HOME Menu.')

    def open_extdata(self) -> None:
        if self.snap is None or not self._paths()['SD root']:
            self._set_status('Refresh a snapshot and set the SD root first.')
            return
        ExtdataWindow(self)

    def _set_status(self, text: str) -> None:
        self.status.configure(text=text)


def _delete_info(snap: Snapshot, title_id: str, paths: dict) -> dict:
    update_id = snap.sibling_id(title_id, '0004000e')
    dlc_id = snap.sibling_id(title_id, '0004008c')
    manager = TitleManager(paths['SD root'], paths['movable.sed'], paths['boot9'], paths['seeddb'] or None)
    id1 = manager.id1_root()

    def installed(tid: str) -> str:
        return tid if (id1 / 'title' / tid[:8] / tid[8:]).is_dir() else ''

    save = id1 / 'title' / title_id[:8] / title_id[8:] / 'data' / '00000001.sav'
    _name, _icon, extdata_id = manager.read_display(title_id)
    if extdata_id and extdata_id == snap.extdata_id:
        extdata_id = None
    return {'update': installed(update_id), 'dlc': installed(dlc_id), 'save': save.is_file(), 'extdata': extdata_id}


class ExtdataWindow(tk.Toplevel):
    def __init__(self, app: ManagerApp):
        super().__init__(app)
        self.app = app
        self.title('Extdata')
        self.geometry('640x420')
        self.tree = ttk.Treeview(self, columns=('id', 'owner'), show='headings')
        self.tree.heading('id', text='Extdata ID')
        self.tree.heading('owner', text='Owner')
        self.tree.column('id', width=120)
        self.tree.column('owner', width=420)
        self.tree.pack(fill=tk.BOTH, expand=True)
        buttons = ttk.Frame(self)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text='Backup', command=lambda: self._act(False)).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(buttons, text='Backup and delete', command=lambda: self._act(True)).pack(side=tk.LEFT, padx=4)
        self.status = ttk.Label(self, text='Reading directories...')
        self.status.pack(fill=tk.X, padx=4, pady=4)
        Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            rows = self.app.snap.list_extdata()
            owners = {value: key for key, value in self.app.extdata_ids.items()}
        except Exception as exc:
            self.after(0, lambda: self.status.configure(text=str(exc)))
            return

        def show():
            for extdata_id, _path in rows:
                owner = owners.get(extdata_id, '')
                if extdata_id == self.app.snap.extdata_id:
                    owner = 'Home Menu (backup only)'
                elif not owner:
                    owner = 'unknown until that application icon has been read'
                self.tree.insert('', tk.END, iid=extdata_id, values=(extdata_id, owner))
            self.status.configure(text=f'{len(rows)} directories. Deleting makes a backup first.')

        self.after(0, show)

    def _act(self, delete: bool) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        extdata_id = selected[0]
        if delete and not mb.askyesno('Delete extdata', f'Back up {extdata_id} and then delete it from the SD card?'):
            return

        def work():
            try:
                backup = self.app.snap.backup_extdata(extdata_id, delete)
            except Exception as exc:
                self.after(0, lambda: self.status.configure(text=str(exc)))
                return
            self.after(0, lambda: self._done(extdata_id, backup, delete))

        Thread(target=work, daemon=True).start()

    def _done(self, extdata_id: str, backup, delete: bool) -> None:
        if delete and self.tree.exists(extdata_id):
            self.tree.delete(extdata_id)
        self.status.configure(text=f'Backup: {backup}')


def main() -> None:
    window = tk.Tk()
    window.title('custom-install title manager')
    window.geometry('1100x720')
    ManagerApp(window).pack(fill=tk.BOTH, expand=True)
    window.mainloop()


if __name__ == '__main__':
    main()
