"""Builds editing widgets from a schema record.

Every value is optional in the UI sense: an empty entry means "leave this key out of
the JSON", so the mod's own default applies. That keeps generated files minimal and
means a later change to a default in the mod is picked up automatically.

Keys the form doesn't know (plugin data without a schema, hand-written extras,
fields not applicable to the current entity type) are never dropped: RecordForm.set()
hands them back so the owner can keep them and write them out again unchanged.
"""
import json
import tkinter as tk
from tkinter import ttk

from .. import i18n
from ..i18n import t
from .widgets import Tooltip

MISSING = object()


def format_default(field):
    if field.get("default_display"):
        return i18n.pick(field["default_display"])
    d = field.get("default", MISSING)
    if d is MISSING or d is None:
        return ""
    if isinstance(d, bool):
        return "true" if d else "false"
    if isinstance(d, list):
        return "[]" if not d else json.dumps(d)
    if d == "unlimited":
        return t("form.unlimited")
    return str(d)


def field_label(field):
    text = field["key"]
    if field.get("required"):
        text += " *"
    d = format_default(field)
    if d:
        text += f"  (default={d})"
    return text


def field_tooltip(field):
    parts = []
    desc = i18n.pick(field.get("desc", {}))
    if desc:
        parts.append(desc)
    ex = i18n.pick(field.get("example", {}))
    if ex:
        parts.append(t("form.example") + ("\n" if "\n" in ex else " ") + ex)
    type_name = field["type"]
    if field.get("columns"):
        type_name = ("list: " if type_name == "list" else "") + ", ".join(c["name"] for c in field["columns"])
    elif type_name == "list":
        type_name = f"list<{field.get('item_ref') or field.get('item_type')}>"
    elif type_name == "enum":
        type_name = "enum: " + " / ".join(field.get("choices", []))
    parts.append(t("form.type") + " " + type_name + ("   " + t("form.required") if field.get("required") else ""))
    return "\n\n".join(parts)


class FormContext:
    """What a form needs from its surroundings. Owned by the editor."""

    def __init__(self, schema, project=None):
        self.schema = schema
        self.project = project
        self.part_names = lambda: []          # all OBJ group names of the current model
        self.used_part_names = lambda: set()  # group names already referenced somewhere
        self.request_asset = None             # fn(kind, callback(reference))
        self.builtin_refs = {}                # kind -> extra suggestions (e.g. bundled HUD names)
        self.on_change = lambda: None
        # True for text formats (weapon files): values stay exactly as typed, as strings.
        self.raw_values = False


class ScalarField:
    """A single primitive value (string / identifier / number / bool / enum)."""

    ASSET_HINTS = {"obj_model": "model", "texture": "texture", "sound": "sound", "hud": "hud", "weapon": "weapon", "vehicle": "vehicle"}

    def __init__(self, parent, field, ctx, row):
        self.field, self.ctx = field, ctx
        self.label = ttk.Label(parent, text=field_label(field))
        self.label.grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=2)
        Tooltip(self.label, lambda: field_tooltip(field))
        self.var = tk.StringVar()
        box = ttk.Frame(parent)
        box.grid(row=row, column=1, sticky="ew", pady=2)
        box.columnconfigure(0, weight=1)
        ftype, hint = field["type"], field.get("hint")
        if ftype == "bool":
            self.widget = ttk.Combobox(box, textvariable=self.var, values=["", "true", "false"], state="readonly", width=10)
            self.widget.grid(row=0, column=0, sticky="w")
        elif ftype == "enum":
            self.widget = ttk.Combobox(box, textvariable=self.var, values=[""] + list(field.get("choices", [])), state="readonly")
            self.widget.grid(row=0, column=0, sticky="ew")
        elif ftype == "enum_open":  # suggestions, but free text allowed
            self.widget = ttk.Combobox(box, textvariable=self.var, values=[""] + list(field.get("choices", [])))
            self.widget.grid(row=0, column=0, sticky="ew")
        elif hint in self.ASSET_HINTS or hint == "obj_group":
            self.widget = ttk.Combobox(box, textvariable=self.var, postcommand=self._refresh_choices)
            self.widget.grid(row=0, column=0, sticky="ew")
            if hint in ("obj_model", "texture", "sound") and ctx.request_asset:
                b = ttk.Button(box, text="…", width=3, command=self._pick_file)
                b.grid(row=0, column=1, padx=(4, 0))
                Tooltip(b, t("form.pick_file_tip"))
        else:
            self.widget = ttk.Entry(box, textvariable=self.var)
            self.widget.grid(row=0, column=0, sticky="ew")
        self.var.trace_add("write", lambda *_: (self._validate(), ctx.on_change()))
        self.error = None

    def _refresh_choices(self):
        hint = self.field.get("hint")
        if hint == "obj_group":
            names = self.ctx.part_names()
            used = self.ctx.used_part_names()
            unused = [n for n in names if n not in used or n == self.var.get()]
            self.widget["values"] = unused + [n for n in names if n not in unused]
            return
        kind = self.ASSET_HINTS[hint]
        if kind == "vehicle":
            self.widget["values"] = [n.split("/")[-1] for n in self.ctx.project.list_vehicles()] if self.ctx.project else []
            return
        refs = list(self.ctx.project.list_references(kind)) if self.ctx.project else []
        refs += [r for r in self.ctx.builtin_refs.get(kind, []) if r not in refs]
        self.widget["values"] = refs

    def _pick_file(self):
        kind = self.ASSET_HINTS[self.field["hint"]]
        self.ctx.request_asset(kind, lambda ref: self.var.set(ref))

    def _validate(self):
        self.error = None
        raw = self.var.get().strip()
        if raw and self.field["type"] in ("int", "float"):
            try:
                float(raw) if self.field["type"] == "float" else int(raw)
            except ValueError:
                self.error = t("form.bad_number")
        style = "Error.TEntry" if self.error else "TEntry"
        if isinstance(self.widget, ttk.Entry) and not isinstance(self.widget, ttk.Combobox):
            self.widget.configure(style=style)

    def get(self):
        raw = self.var.get().strip()
        if raw == "":
            return MISSING
        if self.ctx.raw_values:
            return raw
        ftype = self.field["type"]
        if ftype == "bool":
            return raw == "true"
        if ftype == "int":
            return int(raw)
        if ftype == "float":
            v = float(raw)
            return v
        return raw

    def set(self, value):
        if value is MISSING or value is None:
            self.var.set("")
        elif isinstance(value, bool):
            self.var.set("true" if value else "false")
        else:
            self.var.set(str(value))


class ObjectField:
    """A nested object, shown only when switched on (so absent stays absent)."""

    def __init__(self, parent, field, ctx, row):
        self.field, self.ctx = field, ctx
        self.enabled = tk.BooleanVar(value=bool(field.get("required")))
        # A plain bordered frame with the checkbox as its first row. (Used as a LabelFrame's
        # labelwidget instead, the header collapses to nothing while the body is empty -
        # which is exactly the switched-off state most optional objects start in.)
        self.frame = ttk.Frame(parent, relief="groove", borderwidth=1, padding=(4, 2))
        self.check = ttk.Checkbutton(self.frame, text=field_label(field), variable=self.enabled, command=self._toggle)
        if field.get("required"):
            self.check.state(["disabled"])
        self.check.pack(anchor="w")
        Tooltip(self.check, lambda: field_tooltip(field))
        self.frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        self.body = None
        self.pending_extra = {}
        self._toggle()

    def _toggle(self):
        if self.enabled.get() and self.body is None:
            self.body = RecordForm(self.frame, self.ctx, self.field["ref"])
            self.body.pack(fill="x", padx=(16, 4), pady=(2, 4))
        elif not self.enabled.get() and self.body is not None:
            self.body.destroy()
            self.body = None
        self.ctx.on_change()

    def get(self):
        if not self.enabled.get() or self.body is None:
            return MISSING
        data = self.body.get()
        data.update(self.pending_extra)
        return data

    def set(self, value):
        self.enabled.set(isinstance(value, dict))
        self._toggle()
        self.pending_extra = {}
        if isinstance(value, dict) and self.body:
            self.pending_extra = self.body.set(value)

    @property
    def error(self):
        return self.body.first_error() if self.body else None


class ListField:
    """A list of objects or primitives, with add/remove per row."""

    def __init__(self, parent, field, ctx, row):
        self.field, self.ctx = field, ctx
        self.rows = []
        self.frame = ttk.LabelFrame(parent)
        head = ttk.Frame(self.frame)
        self.title = ttk.Label(head, text=field_label(field))
        self.title.pack(side="left")
        Tooltip(self.title, lambda: field_tooltip(field))
        self.count = ttk.Label(head, foreground="#666666")
        self.count.pack(side="left", padx=6)
        self.frame.configure(labelwidget=head)
        self.frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        self.container = ttk.Frame(self.frame)
        self.container.pack(fill="x", padx=(16, 4))
        ttk.Button(self.frame, text=t("form.add_row"), command=lambda: (self.add_row(), ctx.on_change())).pack(anchor="w", padx=(16, 4), pady=(2, 4))
        self._update_count()

    def _update_count(self):
        self.count.configure(text=t("form.row_count", n=len(self.rows)))

    def add_row(self, value=MISSING):
        if self.field.get("max_rows") and len(self.rows) >= self.field["max_rows"] and value is MISSING:
            return None
        idx = len(self.rows)
        rf = ttk.Frame(self.container, relief="groove", borderwidth=1, padding=4)
        rf.pack(fill="x", pady=2)
        top = ttk.Frame(rf)
        top.pack(fill="x")
        num = ttk.Label(top, text=f"#{idx}", foreground="#666666")
        num.pack(side="left")
        entry = {"frame": rf, "num": num}
        ttk.Button(top, text="✕", width=3, command=lambda: self.remove_row(entry)).pack(side="right")
        ttk.Button(top, text="↓", width=3, command=lambda: self.move_row(entry, 1)).pack(side="right")
        ttk.Button(top, text="↑", width=3, command=lambda: self.move_row(entry, -1)).pack(side="right")
        if self.field.get("item_type") == "columns":
            ed = ColumnsEditor(rf, self.field["columns"], self.ctx)
            ed.pack(anchor="w")
            entry["columns"] = ed
            if value is not MISSING:
                ed.set(value)
        elif self.field.get("item_ref"):
            form = RecordForm(rf, self.ctx, self.field["item_ref"])
            form.pack(fill="x")
            entry["form"] = form
            if isinstance(value, dict):
                entry["extra"] = form.set(value)
        else:
            grid = ttk.Frame(rf)
            grid.pack(fill="x")
            grid.columnconfigure(1, weight=1)
            item_field = {"key": t("form.value"), "type": self.field.get("item_type", "string"), "required": True}
            sf = ScalarField(grid, item_field, self.ctx, 0)
            entry["scalar"] = sf
            if value is not MISSING:
                sf.set(value)
        self.rows.append(entry)
        self._update_count()
        return entry

    def remove_row(self, entry):
        entry["frame"].destroy()
        self.rows.remove(entry)
        self._renumber()
        self.ctx.on_change()

    def move_row(self, entry, delta):
        i = self.rows.index(entry)
        j = i + delta
        if not 0 <= j < len(self.rows):
            return
        self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
        for r in self.rows:
            r["frame"].pack_forget()
        for r in self.rows:
            r["frame"].pack(fill="x", pady=2)
        self._renumber()
        self.ctx.on_change()

    def _renumber(self):
        for i, r in enumerate(self.rows):
            r["num"].configure(text=f"#{i}")
        self._update_count()

    def get(self):
        out = []
        for r in self.rows:
            if "columns" in r:
                v = r["columns"].get()
                if v is not MISSING:
                    out.append(v)
            elif "form" in r:
                item = r["form"].get()
                item.update(r.get("extra", {}))
                out.append(item)
            else:
                v = r["scalar"].get()
                if v is not MISSING:
                    out.append(v)
        return out if out else MISSING

    def set(self, value):
        for r in list(self.rows):
            r["frame"].destroy()
        self.rows = []
        if isinstance(value, list):
            for item in value:
                self.add_row(item)
        self._update_count()

    @property
    def error(self):
        for r in self.rows:
            if "columns" in r:
                continue
            e = r["form"].first_error() if "form" in r else r["scalar"].error
            if e:
                return e
        return None


class ColumnsEditor(ttk.Frame):
    """One comma-separated composite value, one small input per column."""

    def __init__(self, master, columns, ctx):
        super().__init__(master)
        self.columns = columns
        self.vars = []
        for i, col in enumerate(columns):
            ttk.Label(self, text=col["name"] + ("?" if col.get("optional") else ""), foreground="#666666").grid(row=0, column=i, sticky="w", padx=(0, 4))
            v = tk.StringVar()
            if col["type"] == "enum":
                w = ttk.Combobox(self, textvariable=v, values=[""] + col["choices"], width=max(6, max(len(c) for c in col["choices"]) + 2), state="readonly")
            else:
                w = ttk.Entry(self, textvariable=v, width=14 if col["type"] == "string" else 8)
            w.grid(row=1, column=i, sticky="w", padx=(0, 4))
            if col.get("default") not in (None, ""):
                Tooltip(w, f"default={col['default']}")
            v.trace_add("write", lambda *_: ctx.on_change())
            self.vars.append(v)

    def get(self):
        vals = [v.get().strip() for v in self.vars]
        if not any(vals):
            return MISSING
        # trailing empty optional/defaulted columns are simply left off
        while vals and vals[-1] == "":
            vals.pop()
        return ", ".join(vals)

    def set(self, value):
        parts = [] if value in (MISSING, None) else [p.strip() for p in str(value).split(",")]
        for i, v in enumerate(self.vars):
            v.set(parts[i] if i < len(parts) else "")
        if len(parts) > len(self.vars):  # keep extra columns rather than lose them
            self.vars[-1].set(", ".join(parts[len(self.vars) - 1:]))


class ColumnsField:
    def __init__(self, parent, field, ctx, row):
        self.field, self.ctx = field, ctx
        self.label = ttk.Label(parent, text=field_label(field))
        self.label.grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=2)
        Tooltip(self.label, lambda: field_tooltip(field))
        self.editor = ColumnsEditor(parent, field["columns"], ctx)
        self.editor.grid(row=row, column=1, sticky="w", pady=2)
        self.error = None

    def get(self):
        return self.editor.get()

    def set(self, value):
        self.editor.set(value)


def make_field(parent, field, ctx, row):
    if field["type"] == "columns":
        return ColumnsField(parent, field, ctx, row)
    if field["type"] == "object" and field.get("ref"):
        return ObjectField(parent, field, ctx, row)
    if field["type"] == "list":
        return ListField(parent, field, ctx, row)
    return ScalarField(parent, field, ctx, row)


class RecordForm(ttk.Frame):
    """All fields of one record. With groups=True (the root form) fields are placed in
    collapsible sections by their schema "group"."""

    def __init__(self, master, ctx, record, entity_type=None, groups=False, exclude=()):
        super().__init__(master)
        self.ctx = ctx
        self.record = record
        fields = [f for f in ctx.schema.fields(record, entity_type) if f["key"] not in exclude]
        self.widgets = {}
        self.sections = {}
        if not groups:
            self.columnconfigure(1, weight=1)
            for i, f in enumerate(fields):
                self.widgets[f["key"]] = make_field(self, f, ctx, i)
            return
        order = []
        for f in fields:
            g = f.get("group", "Other")
            if g not in order:
                order.append(g)
        for g in order:
            sec = CollapsibleSection(self, t(f"group.{g}"), expanded=(g == "Basic"))
            sec.pack(fill="x", pady=(0, 6))
            self.sections[g] = sec
            row = 0
            for f in fields:
                if f.get("group", "Other") == g:
                    self.widgets[f["key"]] = make_field(sec.body, f, ctx, row)
                    row += 1

    def get(self):
        out = {}
        for key, w in self.widgets.items():
            v = w.get()
            if v is not MISSING:
                out[key] = v
        return out

    def set(self, data):
        """Fills the widgets; returns the keys this form has no widget for."""
        leftovers = {}
        for key, w in self.widgets.items():
            w.set(data.get(key, MISSING))
        for key, v in data.items():
            if key not in self.widgets:
                leftovers[key] = v
        return leftovers

    def first_error(self):
        for key, w in self.widgets.items():
            e = w.error
            if e:
                return f"{key}: {e}"
        return None

    def expand_all(self, on=True):
        for s in self.sections.values():
            s.set_expanded(on)


class CollapsibleSection(ttk.Frame):
    def __init__(self, master, title, expanded=True):
        super().__init__(master)
        self.title = title
        self.btn = ttk.Button(self, command=self.toggle, style="Section.TButton")
        self.btn.pack(fill="x")
        self.body = ttk.Frame(self, padding=(12, 4, 4, 4))
        self.body.columnconfigure(1, weight=1)
        self.expanded = not expanded
        self.toggle()

    def toggle(self):
        self.set_expanded(not self.expanded)

    def set_expanded(self, on):
        self.expanded = on
        self.btn.configure(text=("▼ " if on else "▶ ") + self.title)
        if on:
            self.body.pack(fill="x")
        else:
            self.body.pack_forget()
