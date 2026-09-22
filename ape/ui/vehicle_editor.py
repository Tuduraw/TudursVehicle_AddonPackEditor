"""Editor for one vehicle definition JSON (data/<ns>/vehicles/<name>.json)."""
import json
import tkinter as tk
from tkinter import messagebox, ttk

from .. import i18n, jsoncheck, objparse
from ..i18n import t
from .form import FormContext, RecordForm
from .widgets import CodeEditor, ScrollFrame, Tooltip

BUILTIN_HUDS = ["blank"]


def _all_strings(value, out):
    if isinstance(value, str):
        out.add(value)
    elif isinstance(value, dict):
        for v in value.values():
            _all_strings(v, out)
    elif isinstance(value, list):
        for v in value:
            _all_strings(v, out)
    return out


class VehicleEditor(ttk.Frame):
    MODE_FORM, MODE_JSON = "form", "json"

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.schema = app.registry.get("vehicle")
        self.name = None           # vehicle name relative to data/<ns>/vehicles (may contain '/')
        self.mode = self.MODE_FORM
        self.entity_type = None
        self.extras = {}           # keys not shown by the current form, kept verbatim
        self.dirty = False
        self._loading = False
        self.form = None
        self._build()
        self.show_placeholder()

    # -- layout ------------------------------------------------------------------

    def _build(self):
        bar = ttk.Frame(self, padding=(6, 6, 6, 2))
        bar.pack(fill="x")
        self.title_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.title_var, font=("TkDefaultFont", 11, "bold")).pack(side="left")
        ttk.Button(bar, text=t("common.save"), command=self.save).pack(side="right")
        self.mode_var = tk.StringVar(value=self.MODE_FORM)
        ttk.Radiobutton(bar, text=t("vehicle.mode_json"), value=self.MODE_JSON, variable=self.mode_var,
                        command=self._mode_clicked).pack(side="right", padx=4)
        ttk.Radiobutton(bar, text=t("vehicle.mode_form"), value=self.MODE_FORM, variable=self.mode_var,
                        command=self._mode_clicked).pack(side="right", padx=4)

        typebar = ttk.Frame(self, padding=(6, 0, 6, 4))
        typebar.pack(fill="x")
        lbl = ttk.Label(typebar, text=t("vehicle.entity_type") + " *")
        lbl.pack(side="left")
        et_field = self.schema.field(self.schema.root, "entity_type") or {}
        Tooltip(lbl, lambda: i18n.pick(et_field.get("desc", {})))
        self.type_var = tk.StringVar()
        self.type_box = ttk.Combobox(typebar, textvariable=self.type_var, state="readonly", width=42)
        self.type_box.pack(side="left", padx=6)
        self.type_box.bind("<<ComboboxSelected>>", lambda e: self._type_changed())
        self.fold_bar = ttk.Frame(typebar)
        self.fold_bar.pack(side="left", padx=(12, 0))
        ttk.Button(self.fold_bar, text=t("vehicle.expand_all"), command=lambda: self.form and self.form.expand_all(True)).pack(side="left", padx=(0, 2))
        ttk.Button(self.fold_bar, text=t("vehicle.collapse_all"), command=lambda: self.form and self.form.expand_all(False)).pack(side="left")

        # Problems
        prob = ttk.Frame(self, padding=(6, 2, 6, 6))
        prob.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar()
        self.status = ttk.Label(prob, textvariable=self.status_var)
        self.status.pack(anchor="w")
        self.problems = ttk.Treeview(prob, columns=("sev", "path", "msg"), show="headings", height=4)
        for col, w in (("sev", 70), ("path", 220), ("msg", 520)):
            self.problems.heading(col, text=t(f"problems.{col}"))
            self.problems.column(col, width=w, stretch=(col == "msg"))
        self.problems.pack(fill="x")
        self.problems.bind("<Double-Button-1>", lambda e: self._goto_problem())

        self.paned = ttk.PanedWindow(self, orient="horizontal")
        self.paned.pack(fill="both", expand=True)
        self.center = ttk.Frame(self.paned)
        self.paned.add(self.center, weight=4)
        side = ttk.Frame(self.paned, padding=4)
        self.paned.add(side, weight=1)

        # Form mode
        self.scroll = ScrollFrame(self.center)
        # JSON mode
        self.code = CodeEditor(self.center, on_change=self._json_changed)
        self.placeholder = ttk.Label(self.center, text=t("vehicle.placeholder"), foreground="#777777", anchor="center")

        # OBJ part assist
        ttk.Label(side, text=t("parts.title"), font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.parts_info = ttk.Label(side, foreground="#555555", wraplength=220, justify="left")
        self.parts_info.pack(anchor="w", pady=(0, 4))
        self.parts_list = tk.Listbox(side, activestyle="none", exportselection=False)
        self.parts_list.pack(fill="both", expand=True)
        self.parts_list.bind("<Double-Button-1>", lambda e: self._copy_part())
        Tooltip(self.parts_list, t("parts.tip"))
        ttk.Button(side, text=t("parts.copy"), command=self._copy_part).pack(fill="x", pady=(4, 0))


    def _refresh_type_choices(self):
        self.type_ids = list(self.schema.entity_types)
        labels = [f"{i18n.pick(self.schema.entity_types[i].get('label', {})) or i}  —  {i}" for i in self.type_ids]
        self.type_box["values"] = labels

    def _set_type_display(self, entity_type):
        self._refresh_type_choices()
        if entity_type in self.type_ids:
            self.type_box.current(self.type_ids.index(entity_type))
        else:
            self.type_var.set(entity_type or "")

    # -- public ------------------------------------------------------------------

    def show_placeholder(self):
        self.name = None
        self.title_var.set(t("vehicle.none_open"))
        for w in (self.scroll, self.code):
            w.pack_forget()
        self.placeholder.pack(fill="both", expand=True)
        self._update_parts()

    def open(self, name, data):
        self._loading = True
        self.name = name
        self.dirty = False
        self.title_var.set(t("vehicle.editing", name=name))
        self.placeholder.pack_forget()
        self.entity_type = data.get("entity_type") if isinstance(data, dict) else None
        self._set_type_display(self.entity_type)
        self.mode_var.set(self.MODE_FORM)
        self.mode = self.MODE_FORM
        self._show_form(data)
        self._loading = False
        self._run_checks(data)

    def open_raw(self, name, text):
        """For a file that isn't valid JSON: open straight into manual editing."""
        self._loading = True
        self.name = name
        self.dirty = False
        self.title_var.set(t("vehicle.editing", name=name))
        self.placeholder.pack_forget()
        self.mode_var.set(self.MODE_JSON)
        self.mode = self.MODE_JSON
        self.scroll.pack_forget()
        self.fold_bar.pack_forget()
        self.code.pack(fill="both", expand=True)
        self.code.set(text)
        self._loading = False
        self._json_changed(text)

    def current_data(self):
        """(data dict, None) or (None, SyntaxProblem) in JSON mode with a syntax error."""
        if self.mode == self.MODE_JSON:
            return jsoncheck.check_syntax(self.code.get())
        data = {"entity_type": self.entity_type} if self.entity_type else {}
        data.update(self.form.get() if self.form else {})
        for k, v in self.extras.items():
            data.setdefault(k, v)
        return data, None

    # -- form mode ---------------------------------------------------------------

    def _make_ctx(self):
        ctx = FormContext(self.schema, self.app.project)
        ctx.part_names = self._part_names
        ctx.used_part_names = self._used_part_names
        ctx.request_asset = self.app.request_asset
        ctx.builtin_refs = {"hud": BUILTIN_HUDS}
        ctx.on_change = self._form_changed
        return ctx

    def _show_form(self, data):
        self.code.pack_forget()
        self.fold_bar.pack(side="left", padx=(12, 0))
        if self.form is not None:
            self.form.destroy()
        self.form = RecordForm(self.scroll.inner, self._make_ctx(), self.schema.root,
                               entity_type=self.entity_type, groups=True, exclude=("entity_type",))
        self.form.pack(fill="x", padx=4, pady=4)
        self._loading, was_loading = True, self._loading
        leftovers = self.form.set(data or {})
        leftovers.pop("entity_type", None)
        self.extras = leftovers
        self._loading = was_loading
        self.scroll.pack(fill="both", expand=True)
        self.scroll.scroll_to_top()
        self._update_parts()

    def _form_changed(self):
        if self._loading:
            return
        self.dirty = True
        self._update_parts()
        if getattr(self, "_check_after", None):
            self.after_cancel(self._check_after)
        self._check_after = self.after(500, lambda: self._run_checks(self.current_data()[0]))

    def _type_changed(self):
        idx = self.type_box.current()
        if idx < 0:
            return
        new_type = self.type_ids[idx]
        if new_type == self.entity_type:
            return
        if self.mode == self.MODE_JSON:
            data, err = self.current_data()
            if err:
                messagebox.showwarning(t("app.title"), t("vehicle.fix_json_first"))
                self._set_type_display(self.entity_type)
                return
            data["entity_type"] = new_type
            self.entity_type = new_type
            self.code.set(json.dumps(data, ensure_ascii=False, indent=2))
            self.dirty = True
            return
        data, _ = self.current_data()
        data["entity_type"] = new_type
        self.entity_type = new_type
        self._show_form(data)
        self.dirty = True
        self._run_checks(data)
        hidden = [k for k in self.extras if self.schema.field(self.schema.root, k)]
        if hidden:
            messagebox.showinfo(t("app.title"), t("vehicle.hidden_kept", keys=", ".join(hidden)))

    # -- mode switching ----------------------------------------------------------

    def _mode_clicked(self):
        target = self.mode_var.get()
        if target == self.mode or self.name is None:
            self.mode_var.set(self.mode)
            return
        if target == self.MODE_JSON:
            data, _ = self.current_data()
            self.scroll.pack_forget()
            self.fold_bar.pack_forget()
            self.code.pack(fill="both", expand=True)
            self._loading = True
            self.code.set(json.dumps(data, ensure_ascii=False, indent=2))
            self._loading = False
            self.mode = self.MODE_JSON
            self._json_changed(self.code.get())
        else:
            data, err = jsoncheck.check_syntax(self.code.get())
            if err:
                messagebox.showwarning(t("app.title"), t("vehicle.cannot_switch", err=str(err)))
                self.code.goto(err.line, err.col)
                self.mode_var.set(self.MODE_JSON)
                return
            if not isinstance(data, dict):
                messagebox.showwarning(t("app.title"), t("json.not_object"))
                self.mode_var.set(self.MODE_JSON)
                return
            self.mode = self.MODE_FORM
            new_type = data.get("entity_type")
            self.entity_type = new_type
            self._set_type_display(new_type)
            self._show_form(data)
            self._run_checks(data)

    # -- JSON mode ---------------------------------------------------------------

    def _json_changed(self, text):
        if not self._loading and self.mode == self.MODE_JSON:
            self.dirty = True
        data, err = jsoncheck.check_syntax(text)
        if err:
            self.code.mark_lines([err.line] + ([err.related_line] if err.related_line else []))
            self._show_problems([("error", f"{t('json.line')} {err.line}:{err.col}", err.message)], syntax_line=err.line)
            self.status_var.set("✖ " + str(err))
            self.status.configure(foreground="#b00020")
            return
        self.code.clear_marks()
        if isinstance(data, dict) and data.get("entity_type") in self.schema.entity_types:
            self.entity_type = data["entity_type"]
            self._set_type_display(self.entity_type)
        self._run_checks(data)
        self._update_parts(data)

    # -- checks ------------------------------------------------------------------

    def _run_checks(self, data):
        if data is None:
            return
        problems = jsoncheck.check_schema(self.schema, data, data.get("entity_type") if isinstance(data, dict) else None)
        if isinstance(data, dict):
            et = data.get("entity_type")
            if et and et not in self.schema.entity_types:
                problems.append(("warning", "/entity_type", t("vehicle.unknown_type")))
            for msg in self.app.plugins.validate("vehicle", data, et):
                problems.append(("warning", "plugin", msg))
        self._show_problems(problems)

    def _show_problems(self, problems, syntax_line=None):
        self.problems.delete(*self.problems.get_children())
        self._problem_lines = {}
        for sev, path, msg in problems:
            iid = self.problems.insert("", "end", values=(t(f"problems.{sev}"), path, msg))
            if syntax_line and sev == "error":
                self._problem_lines[iid] = syntax_line
        errors = sum(1 for p in problems if p[0] == "error")
        warns = len(problems) - errors
        if syntax_line:
            return
        if errors:
            self.status_var.set("✖ " + t("problems.summary", e=errors, w=warns))
            self.status.configure(foreground="#b00020")
        elif warns:
            self.status_var.set("⚠ " + t("problems.summary", e=errors, w=warns))
            self.status.configure(foreground="#8a6d00")
        else:
            self.status_var.set("✔ " + t("problems.none"))
            self.status.configure(foreground="#1b7f2a")

    def _goto_problem(self):
        sel = self.problems.selection()
        if sel and sel[0] in getattr(self, "_problem_lines", {}):
            self.code.goto(self._problem_lines[sel[0]])

    # -- OBJ parts ---------------------------------------------------------------

    def _model_path(self, data=None):
        if data is None:
            if self.name is None:
                return None
            data, err = self.current_data()
            if err or not isinstance(data, dict):
                return None
        model = data.get("model") if isinstance(data, dict) else None
        return self.app.project.resolve_identifier(model) if self.app.project and model else None

    def _part_names(self):
        p = self._model_path()
        return objparse.list_groups(p) if p else []

    def _used_part_names(self):
        data, err = self.current_data() if self.name else (None, None)
        return _all_strings(data, set()) if data else set()

    def _update_parts(self, data=None):
        self.parts_list.delete(0, "end")
        if self.name is None:
            self.parts_info.configure(text="")
            return
        if data is None:
            data, err = self.current_data()
            if err:
                return
        path = self._model_path(data)
        if not path:
            self.parts_info.configure(text=t("parts.no_model"))
            return
        names = objparse.list_groups(path)
        used = _all_strings(data, set())
        unused = [n for n in names if n not in used]
        for n in unused:
            self.parts_list.insert("end", "  " + n)
        for n in names:
            if n in used:
                self.parts_list.insert("end", "✓ " + n)
                self.parts_list.itemconfigure("end", foreground="#999999")
        self.parts_info.configure(text=t("parts.summary", total=len(names), unused=len(unused)))

    def _copy_part(self):
        sel = self.parts_list.curselection()
        if not sel:
            return
        name = self.parts_list.get(sel[0])[2:]
        self.clipboard_clear()
        self.clipboard_append(name)
        focus = self.focus_get()
        # If a text field has focus, insert directly there as well.
        if isinstance(focus, (ttk.Entry, ttk.Combobox, tk.Entry)):
            focus.delete(0, "end")
            focus.insert(0, name)
        self.status_var.set(t("parts.copied", name=name))

    # -- save --------------------------------------------------------------------

    def save(self):
        if self.name is None or self.app.project is None:
            return False
        path = self.app.project.vehicle_path(self.name)
        if self.mode == self.MODE_JSON:
            data, err = jsoncheck.check_syntax(self.code.get())
            if err:
                if not messagebox.askyesno(t("app.title"), t("vehicle.save_invalid_json", err=str(err)), default="no"):
                    return False
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(self.code.get(), encoding="utf-8")
                self.dirty = False
                self.app.on_vehicle_saved(self.name)
                return True
        else:
            if self.form and self.form.first_error():
                messagebox.showwarning(t("app.title"), t("vehicle.fix_form_first", err=self.form.first_error()))
                return False
            data, _ = self.current_data()
        errors = [p for p in jsoncheck.check_schema(self.schema, data, data.get("entity_type")) if p[0] == "error"]
        if errors:
            listing = "\n".join(f"・{p[1]}: {p[2]}" for p in errors[:10])
            if not messagebox.askyesno(t("app.title"), t("vehicle.save_with_errors", list=listing), default="no"):
                return False
        data = self.app.plugins.apply_save_hooks("vehicle", data, {
            "project": self.app.project, "name": self.name, "entity_type": data.get("entity_type")})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.dirty = False
        self.app.on_vehicle_saved(self.name)
        return True
