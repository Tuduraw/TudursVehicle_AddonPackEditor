"""Main window."""
import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import __version__, i18n
from ..i18n import t
from ..plugins import PluginManager, USER_PLUGIN_DIR
from ..project import ASSET_KINDS, PackProject, normalize_subfolder, NAMESPACE_RE
from ..schema import SchemaRegistry
from ..settings import Settings
from .asset_manager import AssetManager
from .vehicle_editor import VehicleEditor
from .weapon_editor import WeaponEditor
from .hud_editor import HudEditor


class App:
    def __init__(self, root, settings=None):
        self.root = root
        self.settings = settings or Settings()
        i18n.set_language(self.settings.get("language", "ja"))
        self.registry = SchemaRegistry()
        self.registry.load_builtin()
        self.plugins = PluginManager(self.registry)
        self.plugins.discover()
        self.project = None
        self._style()
        self.build_ui()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        if self.plugins.errors:
            root.after(300, self._report_plugin_errors)

    def _style(self):
        s = ttk.Style(self.root)
        s.configure("Section.TButton", anchor="w", padding=(6, 3))
        s.configure("Error.TEntry", fieldbackground="#ffd6d6")

    # -- UI ----------------------------------------------------------------------

    def build_ui(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.root.title(t("app.title"))
        self._build_menu()
        outer = ttk.PanedWindow(self.root, orient="horizontal")
        outer.pack(fill="both", expand=True)

        left = ttk.Frame(outer, padding=6)
        outer.add(left, weight=1)
        self.pack_label = ttk.Label(left, text=t("pack.none"), wraplength=240, justify="left", foreground="#444444")
        self.pack_label.pack(anchor="w", pady=(0, 6))
        ttk.Label(left, text=t("vehicles.title"), font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        # Packed before the tree with side="bottom" so a short window never pushes them off-screen.
        btns = ttk.Frame(left)
        btns.pack(side="bottom", fill="x")
        self.vehicle_tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.vehicle_tree.pack(fill="both", expand=True, pady=4)
        self.vehicle_tree.bind("<<TreeviewSelect>>", lambda e: self._vehicle_selected())
        ttk.Button(btns, text=t("vehicles.new"), command=self.new_vehicle).pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text=t("vehicles.delete"), command=self.delete_vehicle).pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.tabs = ttk.Notebook(outer)
        outer.add(self.tabs, weight=5)
        self.vehicle_editor = VehicleEditor(self.tabs, self)
        self.tabs.add(self.vehicle_editor, text=t("tab.vehicle"))
        self.weapon_editor = WeaponEditor(self.tabs, self)
        self.tabs.add(self.weapon_editor, text=t("tab.weapon"))
        self.hud_editor = HudEditor(self.tabs, self)
        self.tabs.add(self.hud_editor, text=t("tab.hud"))
        self.asset_manager = AssetManager(self.tabs, self)
        self.tabs.add(self.asset_manager, text=t("tab.assets"))
        self._update_pack_label()
        self.refresh_vehicle_list()
        self.weapon_editor.refresh_list()
        self.hud_editor.refresh_list()
        self.asset_manager.refresh()

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        m_file = tk.Menu(menubar, tearoff=False)
        m_file.add_command(label=t("menu.new_pack"), command=self.new_pack)
        m_file.add_command(label=t("menu.open_pack"), command=self.open_pack_dialog)
        recent = tk.Menu(m_file, tearoff=False)
        for p in self.settings.get("recent_packs", []):
            recent.add_command(label=p, command=lambda p=p: self.open_pack(p))
        m_file.add_cascade(label=t("menu.recent"), menu=recent)
        m_file.add_separator()
        m_file.add_command(label=t("menu.save"), command=self.save_current, accelerator="Ctrl+S")
        m_file.add_separator()
        m_file.add_command(label=t("menu.quit"), command=self.quit)
        menubar.add_cascade(label=t("menu.file"), menu=m_file)

        m_set = tk.Menu(menubar, tearoff=False)
        m_lang = tk.Menu(m_set, tearoff=False)
        self.lang_var = tk.StringVar(value=i18n.current())
        for code, name in i18n.SUPPORTED.items():
            m_lang.add_radiobutton(label=name, value=code, variable=self.lang_var, command=lambda c=code: self.change_language(c))
        m_set.add_cascade(label=t("menu.language"), menu=m_lang)
        menubar.add_cascade(label=t("menu.settings"), menu=m_set)

        m_help = tk.Menu(menubar, tearoff=False)
        m_help.add_command(label=t("menu.plugins"), command=self.show_plugins)
        m_help.add_command(label=t("menu.about"), command=lambda: messagebox.showinfo(
            t("app.title"), t("app.about", version=__version__)))
        menubar.add_cascade(label=t("menu.help"), menu=m_help)
        self.root.config(menu=menubar)
        self.root.bind_all("<Control-s>", lambda e: self.save_current())

    def _update_pack_label(self):
        if self.project:
            self.pack_label.configure(text=t("pack.info", path=str(self.project.root), ns=self.project.namespace))
        else:
            self.pack_label.configure(text=t("pack.none"))

    # -- language ----------------------------------------------------------------

    def change_language(self, code):
        if code == i18n.current():
            return
        state = self._capture_editor()
        wstate = None
        we = self.weapon_editor
        if we.path is not None:
            wstate = {"path": we.path, "text": we.current_text(), "dirty": we.dirty, "tab": self.tabs.index(self.tabs.select())}
        hstate = None
        he = self.hud_editor
        if he.path is not None:
            hstate = {"path": he.path, "text": he.code.get(), "dirty": he.dirty}
        tab_index = self.tabs.index(self.tabs.select())
        self.settings.set("language", code)
        i18n.set_language(code)
        self.build_ui()
        self._restore_editor(state)
        if wstate:
            self.weapon_editor.open(wstate["path"], wstate["text"])
            self.weapon_editor.dirty = wstate["dirty"]
            self.weapon_editor._select_path(wstate["path"])
        if hstate:
            self.hud_editor.open(hstate["path"], hstate["text"])
            self.hud_editor.dirty = hstate["dirty"]
            self.hud_editor._select_path(hstate["path"])
        self.tabs.select(tab_index)

    def _capture_editor(self):
        """Current editor contents, so a UI rebuild doesn't lose unsaved work."""
        ed = self.vehicle_editor
        if ed.name is None:
            return None
        if ed.mode == ed.MODE_JSON:
            return {"name": ed.name, "raw": ed.code.get(), "dirty": ed.dirty}
        data, _ = ed.current_data()
        return {"name": ed.name, "data": data, "dirty": ed.dirty}

    def _restore_editor(self, state):
        if not state:
            return
        if "raw" in state:
            self.vehicle_editor.open_raw(state["name"], state["raw"])
        else:
            self.vehicle_editor.open(state["name"], state["data"])
        self.vehicle_editor.dirty = state["dirty"]
        self._select_vehicle_in_tree(state["name"])

    # -- pack --------------------------------------------------------------------

    def new_pack(self):
        if not self.confirm_discard():
            return
        dlg = NewPackDialog(self.root)
        if not dlg.result:
            return
        parent, pack_name, ns = dlg.result
        root = Path(parent) / pack_name
        if root.exists() and any(root.iterdir()):
            if not messagebox.askyesno(t("app.title"), t("pack.exists_open", path=str(root))):
                return
            self.open_pack(root)
            return
        self.project = PackProject.create(root, ns)
        self._after_pack_opened()

    def open_pack_dialog(self):
        if not self.confirm_discard():
            return
        d = filedialog.askdirectory(parent=self.root, title=t("menu.open_pack"))
        if d:
            self.open_pack(d)

    def open_pack(self, path):
        try:
            self.project = PackProject.open(path)
        except (FileNotFoundError, OSError):
            messagebox.showerror(t("app.title"), t("pack.not_a_pack", path=str(path)))
            return
        self._after_pack_opened()

    def _after_pack_opened(self):
        self.settings.add_recent(self.project.root)
        self.vehicle_editor.show_placeholder()
        self.weapon_editor.show_placeholder()
        self.hud_editor.show_placeholder()
        self._build_menu()
        self._update_pack_label()
        self.refresh_vehicle_list()
        self.weapon_editor.refresh_list()
        self.hud_editor.refresh_list()
        self.asset_manager.refresh()

    # -- vehicles ----------------------------------------------------------------

    def refresh_vehicle_list(self):
        tree = self.vehicle_tree
        tree.delete(*tree.get_children())
        self._vehicle_nodes = {}
        if not self.project:
            return
        folders = {"": ""}
        for name in self.project.list_vehicles():
            parts = name.split("/")
            parent = ""
            for i in range(len(parts) - 1):
                key = "/".join(parts[: i + 1])
                if key not in folders:
                    folders[key] = tree.insert(folders[parent], "end", text=parts[i] + "/", open=True)
                parent = key
            node = tree.insert(folders[parent], "end", text=parts[-1])
            self._vehicle_nodes[node] = name

    def _select_vehicle_in_tree(self, name):
        for node, n in getattr(self, "_vehicle_nodes", {}).items():
            if n == name:
                self._suppress_select = True
                self.vehicle_tree.selection_set(node)
                self.vehicle_tree.see(node)
                return

    def _vehicle_selected(self):
        if getattr(self, "_suppress_select", False):
            self._suppress_select = False
            return
        sel = self.vehicle_tree.selection()
        if not sel or sel[0] not in self._vehicle_nodes:
            return
        name = self._vehicle_nodes[sel[0]]
        if name == self.vehicle_editor.name:
            return
        if not self.confirm_discard_vehicle():
            if self.vehicle_editor.name:
                self._select_vehicle_in_tree(self.vehicle_editor.name)
            return
        self.load_vehicle(name)

    def load_vehicle(self, name):
        text = self.project.vehicle_path(name).read_text(encoding="utf-8")
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError
            self.vehicle_editor.open(name, data)
        except ValueError:
            self.vehicle_editor.open_raw(name, text)
        self.tabs.select(self.vehicle_editor)

    def new_vehicle(self):
        if not self.project:
            messagebox.showinfo(t("app.title"), t("pack.open_first"))
            return
        if not self.confirm_discard_vehicle():
            return
        schema = self.registry.get("vehicle")
        dlg = NewVehicleDialog(self.root, schema)
        if not dlg.result:
            return
        name, entity_type = dlg.result
        if self.project.vehicle_path(name).exists():
            messagebox.showerror(t("app.title"), t("vehicles.exists", name=name))
            return
        data = {"entity_type": entity_type}
        # Written straight to disk WITHOUT the save-time checks: a brand-new vehicle has its
        # required fields empty by definition, so warning about them here would only be noise.
        # The checks still run (and warn) when the user saves for real.
        path = self.project.vehicle_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.refresh_vehicle_list()
        self.asset_manager.refresh()
        self.vehicle_editor.open(name, data)
        self._select_vehicle_in_tree(name)

    def delete_vehicle(self):
        sel = self.vehicle_tree.selection()
        if not sel or sel[0] not in self._vehicle_nodes:
            return
        name = self._vehicle_nodes[sel[0]]
        if not messagebox.askyesno(t("app.title"), t("vehicles.confirm_delete", name=name), default="no"):
            return
        self.project.vehicle_path(name).unlink(missing_ok=True)
        if self.vehicle_editor.name == name:
            self.vehicle_editor.show_placeholder()
        self.refresh_vehicle_list()
        self.asset_manager.refresh()

    def on_vehicle_saved(self, name):
        self.refresh_vehicle_list()
        self._select_vehicle_in_tree(name)
        self.asset_manager.refresh()

    def save_current(self):
        current = self.tabs.nametowidget(self.tabs.select()) if self.tabs.select() else None
        if current is self.weapon_editor:
            self.weapon_editor.save()
        elif current is self.hud_editor:
            self.hud_editor.save()
        elif self.vehicle_editor.name:
            self.vehicle_editor.save()

    def confirm_discard(self):
        """Used before anything that replaces the open pack or quits: covers every editor."""
        return (self.confirm_discard_vehicle() and self.weapon_editor.confirm_discard()
                and self.hud_editor.confirm_discard())

    def confirm_discard_vehicle(self):
        ed = self.vehicle_editor
        if not ed.name or not ed.dirty:
            return True
        ans = messagebox.askyesnocancel(t("app.title"), t("vehicle.unsaved", name=ed.name))
        if ans is None:
            return False
        if ans:
            return ed.save()
        return True

    # -- assets ------------------------------------------------------------------

    def ask_subfolder(self, kind, preset=None):
        last = self.settings.get("last_subfolders", {})
        initial = preset if preset is not None else last.get(kind, ASSET_KINDS[kind][1])
        dlg = SubfolderDialog(self.root, kind, initial, self.project)
        if dlg.result is None:
            return None
        last[kind] = dlg.result
        self.settings.set("last_subfolders", last)
        return dlg.result

    def import_one(self, kind, src, sub):
        try:
            _, ref = self.project.import_asset(kind, src, sub)
            return ref
        except FileExistsError as e:
            if messagebox.askyesno(t("app.title"), t("assets.overwrite", path=str(e))):
                _, ref = self.project.import_asset(kind, src, sub, overwrite=True)
                return ref
        except ValueError:
            messagebox.showerror(t("app.title"), t("assets.bad_type", name=Path(src).name))
        return None

    def request_asset(self, kind, callback):
        """Used by form fields' '…' button: pick a file, copy it into the pack if it
        isn't there already, and hand back its reference string."""
        if not self.project:
            messagebox.showinfo(t("app.title"), t("pack.open_first"))
            return
        exts = ASSET_KINDS[kind][2]
        f = filedialog.askopenfilename(parent=self.root, title=t(f"kind.{kind}"),
                                       filetypes=[(t(f"kind.{kind}"), " ".join("*" + e for e in exts))])
        if not f:
            return
        f = Path(f)
        root = self.project.asset_root(kind)
        if root in f.resolve().parents:
            callback(self.project.reference_for(kind, f))
            return
        sub = self.ask_subfolder(kind)
        if sub is None:
            return
        ref = self.import_one(kind, f, sub)
        if ref:
            callback(ref)
            self.asset_manager.refresh()

    # -- misc --------------------------------------------------------------------

    def show_plugins(self):
        lines = [f"・{i18n.pick(p.get('name', {})) or p['id']}  ({p['id']} {p.get('version', '')})" for p in self.plugins.plugins]
        text = "\n".join(lines) if lines else t("plugins.none")
        text += "\n\n" + t("plugins.location", builtin="plugins/", user=str(USER_PLUGIN_DIR))
        if self.plugins.errors:
            text += "\n\n" + t("plugins.errors") + "\n" + "\n".join(f"・{n}" for n, _ in self.plugins.errors)
        messagebox.showinfo(t("menu.plugins"), text)

    def _report_plugin_errors(self):
        detail = "\n\n".join(f"[{n}]\n{e[-600:]}" for n, e in self.plugins.errors)
        messagebox.showwarning(t("app.title"), t("plugins.load_failed") + "\n\n" + detail)

    def quit(self):
        if self.confirm_discard():
            self.root.destroy()


class _Dialog(tk.Toplevel):
    def __init__(self, master, title):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.destroy())

    def buttons(self, row):
        f = ttk.Frame(self.body)
        f.grid(row=row, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(f, text=t("common.ok"), command=self._ok).pack(side="left", padx=4)
        ttk.Button(f, text=t("common.cancel"), command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda e: self._ok())

    def run(self):
        self.wait_visibility()
        self.grab_set()
        self.wait_window()


class NewPackDialog(_Dialog):
    def __init__(self, master):
        super().__init__(master, t("menu.new_pack"))
        b = self.body
        self.parent_var = tk.StringVar(value=str(Path.home()))
        self.name_var = tk.StringVar(value="my_addon")
        self.ns_var = tk.StringVar(value="myaddon")
        ttk.Label(b, text=t("pack.parent_dir")).grid(row=0, column=0, sticky="w")
        ttk.Entry(b, textvariable=self.parent_var, width=46).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(b, text="…", width=3, command=self._browse).grid(row=0, column=2)
        ttk.Label(b, text=t("pack.name")).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(b, textvariable=self.name_var).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Label(b, text=t("pack.namespace")).grid(row=2, column=0, sticky="w")
        ttk.Entry(b, textvariable=self.ns_var).grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Label(b, text=t("pack.hint"), foreground="#666666", wraplength=420, justify="left").grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.buttons(4)
        self.run()

    def _browse(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.parent_var.get())
        if d:
            self.parent_var.set(d)

    def _ok(self):
        ns = self.ns_var.get().strip()
        name = self.name_var.get().strip()
        if not NAMESPACE_RE.match(ns):
            messagebox.showerror(t("app.title"), t("pack.bad_namespace"), parent=self)
            return
        if not name or any(c in name for c in '\\/:*?"<>|'):
            messagebox.showerror(t("app.title"), t("pack.bad_name"), parent=self)
            return
        self.result = (self.parent_var.get(), name, ns)
        self.destroy()


class NewVehicleDialog(_Dialog):
    def __init__(self, master, schema):
        super().__init__(master, t("vehicles.new"))
        b = self.body
        self.ids = list(schema.entity_types)
        self.name_var = tk.StringVar()
        ttk.Label(b, text=t("vehicles.name")).grid(row=0, column=0, sticky="w")
        e = ttk.Entry(b, textvariable=self.name_var, width=40)
        e.grid(row=0, column=1, sticky="ew", padx=4)
        e.focus_set()
        ttk.Label(b, text=t("vehicles.name_hint"), foreground="#666666").grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(b, text=t("vehicle.entity_type")).grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.type_box = ttk.Combobox(b, state="readonly", width=40,
                                     values=[f"{i18n.pick(schema.entity_types[i].get('label', {})) or i}  —  {i}" for i in self.ids])
        self.type_box.current(0)
        self.type_box.grid(row=2, column=1, sticky="ew", padx=4, pady=(8, 0))
        self.buttons(3)
        self.run()

    def _ok(self):
        try:
            name = normalize_subfolder(self.name_var.get())
        except ValueError as e:
            messagebox.showerror(t("app.title"), t("assets.bad_folder", part=str(e)), parent=self)
            return
        if not name:
            return
        self.result = (name, self.ids[self.type_box.current()])
        self.destroy()


class SubfolderDialog(_Dialog):
    def __init__(self, master, kind, initial, project):
        super().__init__(master, t("assets.subfolder_title"))
        b = self.body
        root = project.asset_root(kind).relative_to(project.root).as_posix()
        ttk.Label(b, text=t("assets.subfolder_prompt", root=root + "/"), wraplength=440, justify="left").grid(row=0, column=0, columnspan=2, sticky="w")
        self.var = tk.StringVar(value=initial or "")
        e = ttk.Entry(b, textvariable=self.var, width=44)
        e.grid(row=1, column=0, sticky="ew", pady=6)
        e.focus_set()
        existing = sorted({p.parent.relative_to(project.asset_root(kind)).as_posix()
                           for p in project.list_assets(kind)} - {"."})
        if existing:
            box = ttk.Combobox(b, values=existing, state="readonly", width=24)
            box.grid(row=1, column=1, padx=4)
            box.bind("<<ComboboxSelected>>", lambda ev: self.var.set(box.get()))
        if ASSET_KINDS[kind][3] == "name":
            ttk.Label(b, text=t("assets.name_unique_note"), foreground="#8a6d00", wraplength=440, justify="left").grid(row=2, column=0, columnspan=2, sticky="w")
        self.buttons(3)
        self.run()

    def _ok(self):
        try:
            self.result = normalize_subfolder(self.var.get())
        except ValueError as e:
            messagebox.showerror(t("app.title"), t("assets.bad_folder", part=str(e)), parent=self)
            return
        self.destroy()
