"""Editor tab for weapon config files (assets/<ns>/weapons/<name>.txt)."""
import tkinter as tk
from tkinter import messagebox, ttk

from .. import i18n, weaponfile
from ..i18n import t
from ..project import normalize_subfolder
from .form import FormContext, RecordForm
from .widgets import CodeEditor, ScrollFrame, Tooltip


class WeaponEditor(ttk.Frame):
    MODE_FORM, MODE_TEXT = "form", "text"

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.schema = app.registry.get("weapon")
        self.path = None          # current file
        self.doc = None           # WeaponDoc for the current file
        self.mode = self.MODE_FORM
        self.weapon_type = None
        self.extras = {}
        self.dirty = False
        self._loading = False
        self.form = None
        self._nodes = {}
        self._build()
        self.show_placeholder()

    # -- layout ------------------------------------------------------------------

    def _build(self):
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left = ttk.Frame(paned, padding=4)
        paned.add(left, weight=1)
        ttk.Label(left, text=t("weapon.list_title"), font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        btns = ttk.Frame(left)
        btns.pack(side="bottom", fill="x")
        ttk.Button(btns, text=t("weapon.new"), command=self.new_weapon).pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text=t("weapon.delete"), command=self.delete_weapon).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True, pady=4)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._selected())

        right = ttk.Frame(paned)
        paned.add(right, weight=5)
        bar = ttk.Frame(right, padding=(6, 6, 6, 2))
        bar.pack(fill="x")
        self.title_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.title_var, font=("TkDefaultFont", 11, "bold")).pack(side="left")
        ttk.Button(bar, text=t("common.save"), command=self.save).pack(side="right")
        self.mode_var = tk.StringVar(value=self.MODE_FORM)
        ttk.Radiobutton(bar, text=t("weapon.mode_text"), value=self.MODE_TEXT, variable=self.mode_var, command=self._mode_clicked).pack(side="right", padx=4)
        ttk.Radiobutton(bar, text=t("vehicle.mode_form"), value=self.MODE_FORM, variable=self.mode_var, command=self._mode_clicked).pack(side="right", padx=4)

        typebar = ttk.Frame(right, padding=(6, 0, 6, 4))
        typebar.pack(fill="x")
        lbl = ttk.Label(typebar, text="Type *")
        lbl.pack(side="left")
        type_field = self.schema.field(self.schema.root, "Type") or {}
        Tooltip(lbl, lambda: i18n.pick(type_field.get("desc", {})))
        self.type_var = tk.StringVar()
        self.type_box = ttk.Combobox(typebar, textvariable=self.type_var, width=26)
        self.type_box.pack(side="left", padx=6)
        self.type_box.bind("<<ComboboxSelected>>", lambda e: self._type_changed())
        self.type_box.bind("<FocusOut>", lambda e: self._type_changed())
        self.type_box.bind("<Return>", lambda e: self._type_changed())
        self.fold_bar = ttk.Frame(typebar)
        self.fold_bar.pack(side="right")
        ttk.Button(self.fold_bar, text=t("vehicle.expand_all"), command=lambda: self.form and self.form.expand_all(True)).pack(side="left", padx=(0, 2))
        ttk.Button(self.fold_bar, text=t("vehicle.collapse_all"), command=lambda: self.form and self.form.expand_all(False)).pack(side="left")

        # The Type's own description gets a row of its own (it can be long); the full text
        # is also on its tooltip.
        self.type_desc = ttk.Label(right, foreground="#555555", wraplength=820, justify="left", padding=(6, 0, 6, 4))
        self.type_desc.pack(fill="x")
        self._type_desc_full = ""
        Tooltip(self.type_desc, lambda: self._type_desc_full)

        prob = ttk.Frame(right, padding=(6, 2, 6, 6))
        prob.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar()
        self.status = ttk.Label(prob, textvariable=self.status_var)
        self.status.pack(anchor="w")
        self.problems = ttk.Treeview(prob, columns=("sev", "line", "key", "msg"), show="headings", height=5)
        for col, w in (("sev", 70), ("line", 50), ("key", 180), ("msg", 520)):
            self.problems.heading(col, text=t(f"weapon.col_{col}"))
            self.problems.column(col, width=w, stretch=(col == "msg"))
        self.problems.pack(fill="x")
        self.problems.bind("<Double-Button-1>", lambda e: self._goto_problem())

        self.center = ttk.Frame(right)
        self.center.pack(fill="both", expand=True)
        self.scroll = ScrollFrame(self.center)
        self.code = CodeEditor(self.center, on_change=self._text_changed)
        self.placeholder = ttk.Label(self.center, text=t("weapon.placeholder"), foreground="#777777", anchor="center")
        self.type_box["values"] = list(self.schema.entity_types)

    # -- list ------------------------------------------------------------------------

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        self._nodes = {}
        p = self.app.project
        if not p:
            return
        root = p.asset_root("weapon")
        folders = {"": ""}
        for f in p.list_assets("weapon"):
            rel = f.relative_to(root).with_suffix("").as_posix()
            parts = rel.split("/")
            parent = ""
            for i in range(len(parts) - 1):
                key = "/".join(parts[: i + 1])
                if key not in folders:
                    folders[key] = self.tree.insert(folders[parent], "end", text=parts[i] + "/", open=True)
                parent = key
            node = self.tree.insert(folders[parent], "end", text=parts[-1])
            self._nodes[node] = f

    def _select_path(self, path):
        for node, f in self._nodes.items():
            if f == path:
                self._suppress = True
                self.tree.selection_set(node)
                self.tree.see(node)

    def _selected(self):
        if getattr(self, "_suppress", False):
            self._suppress = False
            return
        sel = self.tree.selection()
        if not sel or sel[0] not in self._nodes:
            return
        path = self._nodes[sel[0]]
        if path == self.path:
            return
        if not self.confirm_discard():
            if self.path:
                self._select_path(self.path)
            return
        self.open(path)

    # -- open / modes --------------------------------------------------------------------

    def show_placeholder(self):
        self.path = None
        self.doc = None
        self.title_var.set(t("weapon.none_open"))
        self.type_desc.configure(text="")
        for w in (self.scroll, self.code):
            w.pack_forget()
        self.placeholder.pack(fill="both", expand=True)
        self.problems.delete(*self.problems.get_children())
        self.status_var.set("")

    def open(self, path, text=None):
        self._loading = True
        self.path = path
        self.doc = weaponfile.WeaponDoc(path.read_text(encoding="utf-8-sig") if text is None else text)
        self.dirty = False
        self.title_var.set(t("weapon.editing", name=path.stem))
        self.placeholder.pack_forget()
        self.mode = self.MODE_FORM
        self.mode_var.set(self.MODE_FORM)
        self._show_form()
        self._loading = False
        self._run_checks()

    def _canonical_type(self, value):
        known = {x.lower(): x for x in self.schema.entity_types}
        return known.get((value or "").strip().lower(), (value or "").strip() or None)

    def _show_form(self):
        self.code.pack_forget()
        self.fold_bar.pack(side="right")
        values = self.doc.values(self.schema)
        self.weapon_type = self._canonical_type(values.get("Type"))
        self.type_var.set(values.get("Type", ""))
        self._update_type_desc()
        if self.form is not None:
            self.form.destroy()
        ctx = FormContext(self.schema, self.app.project)
        ctx.raw_values = True
        ctx.request_asset = self.app.request_asset
        ctx.on_change = self._form_changed
        applies_to = self.weapon_type if self.weapon_type in self.schema.entity_types else None
        self.form = RecordForm(self.scroll.inner, ctx, self.schema.root, entity_type=applies_to, groups=True, exclude=("Type",))
        self.form.pack(fill="x", padx=4, pady=4)
        was = self._loading
        self._loading = True
        self.extras = self.form.set(values)
        self._loading = was
        self.scroll.pack(fill="both", expand=True)
        self.scroll.scroll_to_top()

    def _update_type_desc(self):
        et = self.schema.entity_types.get(self.weapon_type or "")
        text = i18n.pick(et.get("desc", {})) if et else ""
        if self.weapon_type and not et and ":" in self.weapon_type:
            text = t("weapon.custom_type")
        self._type_desc_full = text
        first = text.split("\n")[0]
        self.type_desc.configure(text=first[:110] + ("…" if len(first) > 110 or "\n" in text else ""))

    def _collect_into_doc(self):
        """Pushes the form's values into self.doc (form mode only)."""
        data = self.form.get() if self.form else {}
        # Keys the current form doesn't show (not used by this Type) must survive: apply()
        # treats every schema key missing from `data` as cleared.
        for k, v in self.extras.items():
            data.setdefault(k, v)
        typed = self.type_var.get().strip()
        if typed:
            data["Type"] = typed
        self.doc.apply(self.schema, data)

    def _form_changed(self):
        if self._loading:
            return
        self.dirty = True
        if getattr(self, "_after", None):
            self.after_cancel(self._after)
        self._after = self.after(500, self._run_checks)

    def _type_changed(self):
        if self.path is None:
            return
        new = self.type_var.get().strip()
        if self._canonical_type(new) == self.weapon_type:
            return
        if self.mode == self.MODE_TEXT:
            self.doc = weaponfile.WeaponDoc(self.code.get())
            vals = self.doc.values(self.schema)
            vals["Type"] = new
            self.doc.apply(self.schema, {k: v for k, v in vals.items() if self.schema.field(self.schema.root, k)})
            self._loading = True
            self.code.set(self.doc.text())
            self._loading = False
            self.weapon_type = self._canonical_type(new)
            self._update_type_desc()
            self.dirty = True
            self._run_checks()
            return
        self._collect_into_doc()
        self.doc.apply(self.schema, {**self.doc.values(self.schema), "Type": new})
        self._show_form()
        self.dirty = True
        self._run_checks()
        hidden = [k for k in self.extras if k != "Type" and self.schema.field(self.schema.root, k)]
        if hidden:
            messagebox.showinfo(t("app.title"), t("vehicle.hidden_kept", keys=", ".join(hidden)))

    def _mode_clicked(self):
        target = self.mode_var.get()
        if target == self.mode or self.path is None:
            self.mode_var.set(self.mode)
            return
        if target == self.MODE_TEXT:
            self._collect_into_doc()
            self.scroll.pack_forget()
            self.fold_bar.pack_forget()
            self.code.pack(fill="both", expand=True)
            self._loading = True
            self.code.set(self.doc.text())
            self._loading = False
            self.mode = self.MODE_TEXT
            self._run_checks()
        else:
            # The text format never fails to "parse" (the loader skips what it can't read),
            # so switching back is always possible; problems stay listed below.
            self.doc = weaponfile.WeaponDoc(self.code.get())
            self.mode = self.MODE_FORM
            self._show_form()
            self._run_checks()

    def _text_changed(self, text):
        if not self._loading and self.mode == self.MODE_TEXT:
            self.dirty = True
        self._run_checks(text)

    # -- checks -----------------------------------------------------------------------

    def current_text(self):
        if self.mode == self.MODE_TEXT:
            return self.code.get()
        self._collect_into_doc()
        return self.doc.text()

    def _run_checks(self, text=None):
        if self.path is None:
            return
        text = self.current_text() if text is None else text
        problems = weaponfile.check(self.schema, text)
        for msg in self.app.plugins.validate("weapon", weaponfile.WeaponDoc(text).values(self.schema), self.weapon_type):
            problems.append(("warning", None, "plugin", msg))
        self.problems.delete(*self.problems.get_children())
        self._lines = {}
        err_lines, warn_lines = [], []
        for sev, ln, key, msg in problems:
            iid = self.problems.insert("", "end", values=(t(f"problems.{sev}"), ln or "", key, msg))
            if ln:
                self._lines[iid] = ln
                (err_lines if sev == "error" else warn_lines).append(ln)
        if self.mode == self.MODE_TEXT:
            self.code.clear_marks()
            self.code.mark_lines(warn_lines, "warn_line")
            self.code.mark_lines(err_lines, "error_line")
        errors = len([p for p in problems if p[0] == "error"])
        warns = len(problems) - errors
        if errors:
            self.status_var.set("✖ " + t("problems.summary", e=errors, w=warns))
            self.status.configure(foreground="#b00020")
        elif warns:
            self.status_var.set("⚠ " + t("problems.summary", e=errors, w=warns))
            self.status.configure(foreground="#8a6d00")
        else:
            self.status_var.set("✔ " + t("problems.none"))
            self.status.configure(foreground="#1b7f2a")
        return problems

    def _goto_problem(self):
        sel = self.problems.selection()
        if not sel or sel[0] not in self._lines:
            return
        if self.mode != self.MODE_TEXT:
            self.mode_var.set(self.MODE_TEXT)
            self._mode_clicked()
        self.code.goto(self._lines[sel[0]])

    # -- create / delete / save ---------------------------------------------------------

    def new_weapon(self):
        if not self.app.project:
            messagebox.showinfo(t("app.title"), t("pack.open_first"))
            return
        if not self.confirm_discard():
            return
        dlg = NewWeaponDialog(self, list(self.schema.entity_types))
        if not dlg.result:
            return
        name, wtype = dlg.result
        stem = name.split("/")[-1]
        clash = self.app.project.duplicate_name("weapon", stem)
        if clash:
            messagebox.showerror(t("app.title"), t("weapon.exists", name=stem))
            return
        path = self.app.project.asset_root("weapon") / (name + ".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written without the save-time checks, like a new vehicle: an incomplete file is
        # expected at this point, warnings belong to the user's own save.
        path.write_text(f"Type = {wtype}\nDisplayName = {stem}\n", encoding="utf-8")
        self.refresh_list()
        self.app.asset_manager.refresh()
        self.open(path)
        self._select_path(path)

    def delete_weapon(self):
        sel = self.tree.selection()
        if not sel or sel[0] not in self._nodes:
            return
        path = self._nodes[sel[0]]
        if not messagebox.askyesno(t("app.title"), t("vehicles.confirm_delete", name=path.stem), default="no"):
            return
        path.unlink(missing_ok=True)
        if path == self.path:
            self.show_placeholder()
        self.refresh_list()
        self.app.asset_manager.refresh()

    def save(self):
        if self.path is None:
            return False
        text = self.current_text()
        errors = [p for p in weaponfile.check(self.schema, text) if p[0] == "error"]
        if errors:
            listing = "\n".join(f"・{(str(p[1]) + ': ') if p[1] else ''}{p[2]} - {p[3]}" for p in errors[:10])
            if not messagebox.askyesno(t("app.title"), t("vehicle.save_with_errors", list=listing), default="no"):
                return False
        self.path.write_text(text, encoding="utf-8")
        self.doc = weaponfile.WeaponDoc(text)
        self.dirty = False
        self.app.asset_manager.refresh()
        return True

    def confirm_discard(self):
        if not self.path or not self.dirty:
            return True
        ans = messagebox.askyesnocancel(t("app.title"), t("vehicle.unsaved", name=self.path.stem))
        if ans is None:
            return False
        return self.save() if ans else True


class NewWeaponDialog(tk.Toplevel):
    def __init__(self, master, types):
        super().__init__(master)
        self.title(t("weapon.new"))
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        b = ttk.Frame(self, padding=12)
        b.pack(fill="both", expand=True)
        self.name_var = tk.StringVar()
        ttk.Label(b, text=t("weapon.name")).grid(row=0, column=0, sticky="w")
        e = ttk.Entry(b, textvariable=self.name_var, width=36)
        e.grid(row=0, column=1, sticky="ew", padx=4)
        e.focus_set()
        ttk.Label(b, text=t("weapon.name_hint"), foreground="#666666", wraplength=320, justify="left").grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(b, text="Type").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.type_box = ttk.Combobox(b, values=types, state="readonly", width=34)
        self.type_box.current(0)
        self.type_box.grid(row=2, column=1, sticky="ew", padx=4, pady=(8, 0))
        f = ttk.Frame(b)
        f.grid(row=3, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(f, text=t("common.ok"), command=self._ok).pack(side="left", padx=4)
        ttk.Button(f, text=t("common.cancel"), command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda ev: self._ok())
        self.bind("<Escape>", lambda ev: self.destroy())
        self.wait_visibility()
        self.grab_set()
        self.wait_window()

    def _ok(self):
        try:
            name = normalize_subfolder(self.name_var.get())
        except ValueError as e:
            messagebox.showerror(t("app.title"), t("assets.bad_folder", part=str(e)), parent=self)
            return
        if name:
            self.result = (name, self.type_box.get())
            self.destroy()
