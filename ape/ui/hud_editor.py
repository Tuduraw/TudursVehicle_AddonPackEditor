"""Editor tab for HUD scripts (assets/<ns>/hud/<name>.txt): text editing with live checks
and a preview of what the mod would draw."""
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .. import hud, i18n
from ..i18n import t
from ..project import normalize_subfolder
from .widgets import CodeEditor, ScrollFrame, Tooltip

try:  # optional: real texture rendering in the preview
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover - the preview falls back to outlined boxes
    Image = ImageTk = None

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"
BUILTIN_SCRIPTS = ["blank"]
# (label key, width, height) in GUI-scaled pixels - what the HUD actually draws in.
# Minecraft's automatic GUI scale picks the largest scale that keeps at least 320x240,
# so 720p ends up at 427x240 and 1080p at 480x270 by default.
SCREEN_PRESETS = [
    ("hud.preset_720", 427, 240),
    ("hud.preset_1080_auto", 480, 270),
    ("hud.preset_1080_gui3", 640, 360),
    ("hud.preset_1080_gui2", 960, 540),
    ("hud.preset_1440_gui4", 640, 360),
]
BACKGROUND = (38, 44, 52)
ALL_ON = {"reloading": 1.0, "is_heat_wpn": 1.0, "has_modes": 1.0, "has_mortar_distance": 1.0, "stalling": 1.0,
          "low_fuel": 1.0, "manual_mode": 1.0, "gear_deployed": 1.0, "free_look": 1.0, "lock_progress": 0.5}


def load_template():
    for name in (f"hud_default_{i18n.current()}.txt", "hud_default_en.txt"):
        p = TEMPLATE_DIR / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def _tk_color(argb):
    a = ((argb >> 24) & 0xFF) / 255.0
    r, g, b = (argb >> 16) & 0xFF, (argb >> 8) & 0xFF, argb & 0xFF
    mix = [round(c * a + bg * (1 - a)) for c, bg in zip((r, g, b), BACKGROUND)]
    return "#%02x%02x%02x" % tuple(mix), a


class HudEditor(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.path = None
        self.dirty = False
        self._loading = False
        self._nodes = {}
        self._images = []   # keep PhotoImage references alive
        self._item_lines = {}
        self.sample = {k: v for k, v in hud.NUMERIC_VARIABLES.items() if v is not None}
        self.strings = dict(hud.STRING_VARIABLES)
        self._build()
        self.show_placeholder()

    # -- layout ------------------------------------------------------------------------

    def _build(self):
        outer = ttk.PanedWindow(self, orient="horizontal")
        outer.pack(fill="both", expand=True)
        left = ttk.Frame(outer, padding=4)
        outer.add(left, weight=1)
        ttk.Label(left, text=t("hud.list_title"), font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        btns = ttk.Frame(left)
        btns.pack(side="bottom", fill="x")
        ttk.Button(btns, text=t("hud.new"), command=self.new_hud).pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text=t("weapon.delete"), command=self.delete_hud).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.tree.pack(fill="both", expand=True, pady=4)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._selected())

        right = ttk.Frame(outer)
        outer.add(right, weight=6)
        bar = ttk.Frame(right, padding=(6, 6, 6, 2))
        bar.pack(fill="x")
        self.title_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.title_var, font=("TkDefaultFont", 11, "bold")).pack(side="left")
        ttk.Button(bar, text=t("common.save"), command=self.save).pack(side="right")
        ttk.Button(bar, text=t("hud.insert_template"), command=self.insert_template).pack(side="right", padx=4)
        # side="right" packs right-to-left: the box first, then its label to its left
        self.preset_box = ttk.Combobox(bar, state="readonly", width=30,
                                       values=[f"{t(k)}  ({w}×{h})" for k, w, h in SCREEN_PRESETS])
        self.preset_box.current(0)
        self.preset_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.preset_box.pack(side="right", padx=(0, 8))
        ttk.Label(bar, text=t("hud.screen")).pack(side="right", padx=(12, 2))

        prob = ttk.Frame(right, padding=(6, 2, 6, 6))
        prob.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar()
        self.status = ttk.Label(prob, textvariable=self.status_var)
        self.status.pack(anchor="w")
        self.problems = ttk.Treeview(prob, columns=("sev", "line", "msg"), show="headings", height=5)
        for col, w in (("sev", 70), ("line", 50), ("msg", 700)):
            self.problems.heading(col, text=t(f"weapon.col_{col}"))
            self.problems.column(col, width=w, stretch=(col == "msg"))
        self.problems.pack(fill="x")
        self.problems.bind("<Double-Button-1>", lambda e: self._goto_problem())

        self.work = ttk.PanedWindow(right, orient="horizontal")
        self.work.pack(fill="both", expand=True)
        # The pane only has a real width once the tab is actually shown, so the initial split
        # is applied on the first <Configure> with a usable size rather than at open().
        self.work.bind("<Configure>", lambda e: self._place_sash())
        self.code = CodeEditor(self.work, on_change=self._text_changed)
        self.work.add(self.code, weight=1)
        side = ttk.Frame(self.work)
        self.work.add(side, weight=1)
        self.canvas = tk.Canvas(side, background="#1b1f24", highlightthickness=0, height=280)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.render())
        Tooltip(self.canvas, t("hud.preview_tip"))
        vbar = ttk.Frame(side)
        vbar.pack(fill="x", pady=(4, 0))
        ttk.Label(vbar, text=t("hud.sample_values"), font=("TkDefaultFont", 9, "bold")).pack(side="left")
        ttk.Button(vbar, text=t("hud.reset_values"), command=self._reset_values).pack(side="right")
        ttk.Button(vbar, text=t("hud.all_on"), command=self._all_on).pack(side="right", padx=4)
        Tooltip(vbar.winfo_children()[-1], t("hud.all_on_tip"))
        self.values_frame = ScrollFrame(side, height=180)
        self.values_frame.pack(fill="x")
        self._value_vars = {}
        grid = self.values_frame.inner
        names = [k for k in hud.NUMERIC_VARIABLES if hud.NUMERIC_VARIABLES[k] is not None and k not in hud.FIXED_VARIABLES]
        names += list(hud.STRING_VARIABLES)
        for i, name in enumerate(names):
            r, c = divmod(i, 2)
            ttk.Label(grid, text=name).grid(row=r, column=c * 2, sticky="w", padx=(4, 2))
            var = tk.StringVar(value=str(self.strings.get(name, self.sample.get(name))))
            e = ttk.Entry(grid, textvariable=var, width=8 if name not in hud.STRING_VARIABLES else 14)
            e.grid(row=r, column=c * 2 + 1, sticky="w", padx=(0, 6), pady=1)
            var.trace_add("write", lambda *_a, n=name: self._value_changed(n))
            self._value_vars[name] = var
        self.placeholder = ttk.Label(right, text=t("hud.placeholder"), foreground="#777777", anchor="center")

    # -- list -----------------------------------------------------------------------------

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        self._nodes = {}
        p = self.app.project
        if not p:
            return
        root = p.asset_root("hud")
        folders = {"": ""}
        for f in p.list_assets("hud"):
            parts = f.relative_to(root).with_suffix("").as_posix().split("/")
            parent = ""
            for i in range(len(parts) - 1):
                key = "/".join(parts[: i + 1])
                if key not in folders:
                    folders[key] = self.tree.insert(folders[parent], "end", text=parts[i] + "/", open=True)
                parent = key
            self._nodes[self.tree.insert(folders[parent], "end", text=parts[-1])] = f

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
        if not sel or sel[0] not in self._nodes or self._nodes[sel[0]] == self.path:
            return
        if not self.confirm_discard():
            if self.path:
                self._select_path(self.path)
            return
        self.open(self._nodes[sel[0]])

    # -- open / edit -----------------------------------------------------------------------

    def show_placeholder(self):
        self.path = None
        self.title_var.set(t("hud.none_open"))
        self.work.pack_forget()
        self.placeholder.pack(fill="both", expand=True, before=self.problems.master)
        self.problems.delete(*self.problems.get_children())
        self.status_var.set("")

    def open(self, path, text=None):
        self.path = path
        self.placeholder.pack_forget()
        self.work.pack(fill="both", expand=True, before=self.problems.master)
        self.title_var.set(t("hud.editing", name=path.stem))
        self._loading = True
        self.code.set(path.read_text(encoding="utf-8-sig") if text is None else text)
        self._loading = False
        self.dirty = False
        self.refresh()

    def _place_sash(self):
        width = self.work.winfo_width()
        if width > 300 and not getattr(self, "_sash_set", False):
            self.work.sashpos(0, int(width * 0.42))
            self._sash_set = True

    def insert_template(self):
        if self.path is None:
            return
        if self.code.get().strip() and not messagebox.askyesno(t("app.title"), t("hud.replace_confirm"), default="no"):
            return
        self.code.set(load_template())
        self.dirty = True
        self.refresh()

    def _text_changed(self, _text):
        if not self._loading and self.path is not None:
            self.dirty = True
        self.refresh()

    def _value_changed(self, name):
        raw = self._value_vars[name].get().strip()
        if name in self.strings:
            self.strings[name] = raw
        else:
            try:
                self.sample[name] = float(raw)
            except ValueError:
                return
        if getattr(self, "_after", None):
            self.after_cancel(self._after)
        self._after = self.after(250, self.refresh)

    def _reset_values(self):
        for name, var in self._value_vars.items():
            var.set(str(hud.STRING_VARIABLES.get(name, hud.NUMERIC_VARIABLES.get(name))))

    def _all_on(self):
        for name, val in ALL_ON.items():
            if name in self._value_vars:
                self._value_vars[name].set(str(val))

    # -- checks and preview ------------------------------------------------------------------

    def screen_size(self):
        i = self.preset_box.current()
        _, w, h = SCREEN_PRESETS[i if i >= 0 else 0]
        return w, h

    def _other_scripts(self):
        """Parsed commands of the pack's other HUD scripts, for Call."""
        out = {}
        if not self.app.project:
            return out
        for f in self.app.project.list_assets("hud"):
            if f == self.path:
                continue
            try:
                out[f.stem.lower()] = hud.parse(f.read_text(encoding="utf-8-sig"))[0]
            except OSError:
                pass
        return out

    def _texture_files(self):
        if not self.app.project:
            return {}
        return {f.stem.lower(): f for f in self.app.project.list_assets("gui_texture")}

    def refresh(self):
        if self.path is None:
            return
        text = self.code.get()
        w, h = self.screen_size()
        scripts = self._other_scripts()
        problems = hud.check(text, known_scripts=list(scripts) + BUILTIN_SCRIPTS,
                             known_textures=list(self._texture_files()), width=w, height=h,
                             sample=self.sample, strings=self.strings, scripts=scripts)
        self._show_problems(problems)
        self.render(problems)

    def _show_problems(self, problems):
        self.problems.delete(*self.problems.get_children())
        self._lines = {}
        err_lines, warn_lines = [], []
        for sev, ln, msg in problems:
            iid = self.problems.insert("", "end", values=(t(f"problems.{sev}"), ln or "", msg))
            if ln:
                self._lines[iid] = ln
                (err_lines if sev == "error" else warn_lines).append(ln)
        self.code.clear_marks()
        self.code.mark_lines(warn_lines, "warn_line")
        self.code.mark_lines(err_lines, "error_line")
        errors = sum(1 for p in problems if p[0] == "error")
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

    def _goto_problem(self):
        sel = self.problems.selection()
        if sel and sel[0] in self._lines:
            self.code.goto(self._lines[sel[0]])

    def render(self, problems=None):
        c = self.canvas
        c.delete("all")
        self._images = []
        self._item_lines = {}
        if self.path is None:
            return
        w, h = self.screen_size()
        cw, ch = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        margin = 0.12  # show a little of what falls outside the screen
        scale = min(cw / (w * (1 + 2 * margin)), ch / (h * (1 + 2 * margin)))
        ox = (cw - w * scale) / 2
        oy = (ch - h * scale) / 2

        def X(x):
            return ox + x * scale

        def Y(y):
            return oy + y * scale
        c.create_rectangle(X(0), Y(0), X(w), Y(h), fill="#%02x%02x%02x" % BACKGROUND, outline="#8899aa")
        c.create_line(X(w / 2), Y(0), X(w / 2), Y(h), fill="#3a434e", dash=(2, 4))
        c.create_line(X(0), Y(h / 2), X(w), Y(h / 2), fill="#3a434e", dash=(2, 4))
        commands, _ = hud.parse(self.code.get())
        ops = hud.run(commands, w, h, self.sample, self.strings, self._other_scripts())
        textures = self._texture_files()
        font_px = max(6, int(round(8 * scale)))
        for op in ops:
            color, alpha = _tk_color(op.color)
            if alpha <= 0:
                continue
            x1, y1, x2, y2 = op.bbox
            off = x1 < 0 or y1 < 0 or x2 > w or y2 > h
            items = []
            if op.kind == "rect":
                items.append(c.create_rectangle(X(x1), Y(y1), X(x2), Y(y2), fill=color, outline=""))
            elif op.kind == "line":
                a, b, d, e = op.pts
                items.append(c.create_line(X(a + 0.5), Y(b + 0.5), X(d + 0.5), Y(e + 0.5), fill=color, width=max(1, scale)))
            elif op.kind == "text":
                items.append(c.create_text(X(x1) + 1, Y(y1) + 1, text=op.text, anchor="nw", fill="#000000",
                                           font=("TkFixedFont", -font_px)))
                items.append(c.create_text(X(x1), Y(y1), text=op.text, anchor="nw", fill=color,
                                           font=("TkFixedFont", -font_px)))
            elif op.kind == "texture":
                items += self._draw_texture(op, textures.get(op.texture), X, Y, scale, color)
            if off:
                items.append(c.create_rectangle(X(x1) - 1, Y(y1) - 1, X(x2) + 1, Y(y2) + 1, outline="#ff4d4d", dash=(3, 2)))
            for it in items:
                if op.script is None:
                    self._item_lines[it] = op.line
                    c.tag_bind(it, "<Button-1>", lambda e, ln=op.line: self.code.goto(ln))
        c.create_text(4, 4, anchor="nw", fill="#8899aa", font=("TkDefaultFont", 8),
                      text=t("hud.preview_label", w=w, h=h))

    def _draw_texture(self, op, path, X, Y, scale, color):
        c = self.canvas
        pts = [coord for x, y in op.corners for coord in (X(x), Y(y))]
        if Image is not None and path is not None:
            try:
                img = Image.open(path).convert("RGBA")
                crop = img.crop((op.u, op.v, op.u + op.uw, op.v + op.vh))
                tw, th = max(1, int(round(abs(op.w) * scale))), max(1, int(round(abs(op.h) * scale)))
                crop = crop.resize((tw, th), Image.NEAREST)
                if op.rot:
                    crop = crop.rotate(-op.rot, expand=True, resample=Image.NEAREST)
                photo = ImageTk.PhotoImage(crop)
                self._images.append(photo)
                cx = sum(p[0] for p in op.corners) / 4
                cy = sum(p[1] for p in op.corners) / 4
                return [c.create_image(X(cx), Y(cy), image=photo)]
            except (OSError, ValueError):
                pass
        # No Pillow, or the texture isn't in this pack: an outlined box with the name.
        items = [c.create_polygon(*pts, fill="", outline=color, dash=(4, 2))]
        cx = sum(p[0] for p in op.corners) / 4
        cy = sum(p[1] for p in op.corners) / 4
        items.append(c.create_text(X(cx), Y(cy), text=op.texture, fill=color, font=("TkDefaultFont", 7)))
        return items

    # -- create / delete / save ----------------------------------------------------------------

    def new_hud(self):
        if not self.app.project:
            messagebox.showinfo(t("app.title"), t("pack.open_first"))
            return
        if not self.confirm_discard():
            return
        dlg = NewHudDialog(self)
        if not dlg.result:
            return
        name = dlg.result
        stem = name.split("/")[-1]
        if self.app.project.duplicate_name("hud", stem) or stem in BUILTIN_SCRIPTS:
            messagebox.showerror(t("app.title"), t("hud.exists", name=stem))
            return
        path = self.app.project.asset_root("hud") / (name + ".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Created with the template and without save-time checks (see WeaponEditor.new_weapon).
        path.write_text(load_template(), encoding="utf-8")
        self.refresh_list()
        self.app.asset_manager.refresh()
        self.open(path)
        self._select_path(path)

    def delete_hud(self):
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
        """Syntax errors ask for confirmation; off-screen elements are only warnings and
        never block saving (a HUD may be meant for larger screens)."""
        if self.path is None:
            return False
        text = self.code.get()
        errors = [p for p in hud.check(text) if p[0] == "error"]
        if errors:
            listing = "\n".join(f"・{(str(p[1]) + ': ') if p[1] else ''}{p[2]}" for p in errors[:10])
            if not messagebox.askyesno(t("app.title"), t("hud.save_with_errors", list=listing), default="no"):
                return False
        self.path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
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


class NewHudDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title(t("hud.new"))
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        b = ttk.Frame(self, padding=12)
        b.pack(fill="both", expand=True)
        self.var = tk.StringVar()
        ttk.Label(b, text=t("hud.name")).grid(row=0, column=0, sticky="w")
        e = ttk.Entry(b, textvariable=self.var, width=34)
        e.grid(row=0, column=1, sticky="ew", padx=4)
        e.focus_set()
        ttk.Label(b, text=t("hud.name_hint"), foreground="#666666", wraplength=320, justify="left").grid(row=1, column=1, sticky="w", padx=4)
        f = ttk.Frame(b)
        f.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(f, text=t("common.ok"), command=self._ok).pack(side="left", padx=4)
        ttk.Button(f, text=t("common.cancel"), command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda ev: self._ok())
        self.bind("<Escape>", lambda ev: self.destroy())
        self.wait_visibility()
        self.grab_set()
        self.wait_window()

    def _ok(self):
        try:
            name = normalize_subfolder(self.var.get())
        except ValueError as e:
            messagebox.showerror(t("app.title"), t("assets.bad_folder", part=str(e)), parent=self)
            return
        if name:
            self.result = name
            self.destroy()
