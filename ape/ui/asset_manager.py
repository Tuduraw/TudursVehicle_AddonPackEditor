"""File management for the open pack: import, organize into subfolders, delete."""
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..i18n import t
from ..project import ASSET_KINDS, normalize_subfolder
from .widgets import Tooltip

KIND_ORDER = ["model", "texture", "sound", "weapon", "hud", "gui_texture"]


def open_in_file_manager(path):
    path = str(path)
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


class AssetManager(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.node_paths = {}
        bar = ttk.Frame(self, padding=6)
        bar.pack(fill="x")
        ttk.Label(bar, text=t("assets.kind")).pack(side="left")
        self.kind_var = tk.StringVar()
        self.kind_box = ttk.Combobox(bar, textvariable=self.kind_var, state="readonly", width=22,
                                     values=[t(f"kind.{k}") for k in KIND_ORDER])
        self.kind_box.current(0)
        self.kind_box.pack(side="left", padx=4)
        ttk.Button(bar, text=t("assets.add_files"), command=self.add_files).pack(side="left", padx=4)
        ttk.Button(bar, text=t("assets.new_folder"), command=self.new_folder).pack(side="left", padx=4)
        ttk.Button(bar, text=t("assets.delete"), command=self.delete_selected).pack(side="left", padx=4)
        ttk.Button(bar, text=t("assets.reload"), command=self.refresh).pack(side="left", padx=4)
        ttk.Button(bar, text=t("assets.open_folder"), command=self.open_folder).pack(side="right")

        self.tree = ttk.Treeview(self, columns=("ref",), show="tree headings")
        self.tree.heading("#0", text=t("assets.path"))
        self.tree.heading("ref", text=t("assets.reference"))
        self.tree.column("#0", width=380)
        self.tree.column("ref", width=380)
        self.tree.pack(fill="both", expand=True, padx=6)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._selected())

        info = ttk.Frame(self, padding=6)
        info.pack(fill="x")
        ttk.Label(info, text=t("assets.reference") + ":").pack(side="left")
        self.ref_var = tk.StringVar()
        e = ttk.Entry(info, textvariable=self.ref_var, state="readonly")
        e.pack(side="left", fill="x", expand=True, padx=4)
        Tooltip(e, t("assets.reference_tip"))
        ttk.Button(info, text=t("assets.copy"), command=self._copy_ref).pack(side="left")
        ttk.Label(self, text=t("assets.note"), foreground="#666666", wraplength=820, justify="left",
                  padding=(6, 0, 6, 6)).pack(fill="x")

    def current_kind(self):
        i = self.kind_box.current()
        return KIND_ORDER[i if i >= 0 else 0]

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.node_paths = {}
        p = self.app.project
        if not p:
            return
        vroot = self.tree.insert("", "end", text=f"data/{p.namespace}/vehicles", open=True)
        self.node_paths[vroot] = ("vehicles", p.vehicles_dir)
        self._fill(vroot, p.vehicles_dir, None)
        for kind in KIND_ORDER:
            root = p.asset_root(kind)
            rel = root.relative_to(p.root).as_posix()
            node = self.tree.insert("", "end", text=f"{rel}   [{t('kind.' + kind)}]", open=True)
            self.node_paths[node] = (kind, root)
            self._fill(node, root, kind)

    def _fill(self, parent, directory, kind):
        if not directory.is_dir():
            return
        gui = self.app.project.asset_root("gui_texture")
        for d in sorted(x for x in directory.iterdir() if x.is_dir()):
            if kind == "texture" and d == gui:
                continue  # shown under its own root
            node = self.tree.insert(parent, "end", text=d.name + "/", open=True)
            self.node_paths[node] = (kind or "vehicles", d)
            self._fill(node, d, kind)
        for f in sorted(x for x in directory.iterdir() if x.is_file()):
            ref = self.app.project.reference_for(kind, f) if kind else f.relative_to(self.app.project.vehicles_dir).with_suffix("").as_posix()
            node = self.tree.insert(parent, "end", text=f.name, values=(ref,))
            self.node_paths[node] = (kind or "vehicles", f)

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        self.ref_var.set(vals[0] if vals else "")
        kind, _ = self.node_paths.get(sel[0], (None, None))
        if kind in KIND_ORDER:
            self.kind_box.current(KIND_ORDER.index(kind))

    def _copy_ref(self):
        if self.ref_var.get():
            self.clipboard_clear()
            self.clipboard_append(self.ref_var.get())

    def _selected_subfolder(self, kind):
        """If a folder under this kind's root is selected, preselect it as the target."""
        sel = self.tree.selection()
        if not sel:
            return None
        k, path = self.node_paths.get(sel[0], (None, None))
        if k != kind or path is None:
            return None
        folder = path if path.is_dir() else path.parent
        try:
            rel = folder.relative_to(self.app.project.asset_root(kind)).as_posix()
        except ValueError:
            return None
        return "" if rel == "." else rel

    def add_files(self):
        if not self.app.project:
            return
        kind = self.current_kind()
        exts = ASSET_KINDS[kind][2]
        files = filedialog.askopenfilenames(parent=self, title=t("assets.add_files"),
                                            filetypes=[(t(f"kind.{kind}"), " ".join("*" + e for e in exts))])
        if not files:
            return
        preset = self._selected_subfolder(kind)
        sub = self.app.ask_subfolder(kind, preset)
        if sub is None:
            return
        for f in files:
            self.app.import_one(kind, f, sub)
        self.refresh()

    def new_folder(self):
        if not self.app.project:
            return
        kind = self.current_kind()
        preset = self._selected_subfolder(kind) or ""
        name = simpledialog.askstring(t("assets.new_folder"), t("assets.new_folder_prompt"), parent=self,
                                      initialvalue=(preset + "/" if preset else ""))
        if not name:
            return
        try:
            sub = normalize_subfolder(name)
        except ValueError as e:
            messagebox.showerror(t("app.title"), t("assets.bad_folder", part=str(e)))
            return
        (self.app.project.asset_root(kind) / sub).mkdir(parents=True, exist_ok=True)
        self.refresh()

    def delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        kind, path = self.node_paths.get(sel[0], (None, None))
        if path is None or path in [r for _, r in self._roots()]:
            return
        if path.is_dir() and any(path.iterdir()):
            messagebox.showwarning(t("app.title"), t("assets.folder_not_empty"))
            return
        if not messagebox.askyesno(t("app.title"), t("assets.confirm_delete", name=path.name), default="no"):
            return
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        self.refresh()
        if kind == "vehicles":
            self.app.refresh_vehicle_list()

    def _roots(self):
        p = self.app.project
        return [("vehicles", p.vehicles_dir)] + [(k, p.asset_root(k)) for k in KIND_ORDER]

    def open_folder(self):
        if self.app.project:
            open_in_file_manager(self.app.project.root)
