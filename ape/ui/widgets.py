"""Small reusable widgets."""
import sys
import tkinter as tk
from tkinter import ttk


class Tooltip:
    """Shows text in a small popup while the pointer rests on a widget."""

    DELAY_MS = 450
    WRAP_PX = 460

    def __init__(self, widget, text_fn):
        self.widget = widget
        self.text_fn = text_fn if callable(text_fn) else (lambda: text_fn)
        self.tip = None
        self.after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self.after_id = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self):
        if self.after_id:
            self.widget.after_cancel(self.after_id)
            self.after_id = None

    def _show(self):
        text = self.text_fn()
        if not text:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=text, justify="left", wraplength=self.WRAP_PX,
                 background="#ffffe8", foreground="#202020", relief="solid", borderwidth=1,
                 padx=6, pady=4).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class ScrollFrame(ttk.Frame):
    """A vertically scrollable container; put children in .inner."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.bind_all_wheel()

    def bind_all_wheel(self):
        # Wheel events go to the widget under the pointer; route them to this canvas
        # only while the pointer is inside it, so nested editors keep their own scroll.
        self.canvas.bind("<Enter>", lambda e: self._set_wheel(True))
        self.canvas.bind("<Leave>", lambda e: self._set_wheel(False))

    def _set_wheel(self, on):
        if on:
            if sys.platform.startswith("linux"):
                self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
                self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))
            else:
                self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        else:
            for seq in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
                self.canvas.unbind_all(seq)

    def _on_wheel(self, e):
        step = -1 if e.delta > 0 else 1
        if sys.platform == "darwin":
            step = -e.delta
        self.canvas.yview_scroll(step * (1 if sys.platform == "darwin" else 3), "units")

    def scroll_to_top(self):
        self.canvas.yview_moveto(0)


class CodeEditor(ttk.Frame):
    """Monospace text editor with line numbers and a highlighted error line.

    on_change(text) is called at most every DEBOUNCE_MS while typing."""

    DEBOUNCE_MS = 350

    def __init__(self, master, on_change=None, **kw):
        super().__init__(master, **kw)
        self.on_change = on_change
        self._after = None
        font = ("Consolas", 10) if sys.platform == "win32" else ("Menlo", 11) if sys.platform == "darwin" else ("DejaVu Sans Mono", 10)
        self.gutter = tk.Text(self, width=5, padx=4, takefocus=0, borderwidth=0, state="disabled",
                              background="#f0f0f0", foreground="#888888", font=font)
        self.text = tk.Text(self, wrap="none", undo=True, font=font, borderwidth=0, tabs=("2c",))
        vsb = ttk.Scrollbar(self, orient="vertical", command=self._yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=lambda a, b: (vsb.set(a, b), self.gutter.yview_moveto(a)),
                            xscrollcommand=hsb.set)
        self.gutter.grid(row=0, column=0, sticky="ns")
        self.text.grid(row=0, column=1, sticky="nsew")
        vsb.grid(row=0, column=2, sticky="ns")
        hsb.grid(row=1, column=1, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.text.tag_configure("error_line", background="#ffd6d6")
        self.text.tag_configure("warn_line", background="#fff2c2")
        self.text.bind("<<Modified>>", self._modified)

    def _yview(self, *args):
        self.text.yview(*args)
        self.gutter.yview(*args)

    def _modified(self, _e=None):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        self._update_gutter()
        if self.on_change:
            if self._after:
                self.after_cancel(self._after)
            self._after = self.after(self.DEBOUNCE_MS, lambda: self.on_change(self.get()))

    def _update_gutter(self):
        lines = int(self.text.index("end-1c").split(".")[0])
        self.gutter.configure(state="normal")
        self.gutter.delete("1.0", "end")
        self.gutter.insert("1.0", "\n".join(str(i) for i in range(1, lines + 1)))
        self.gutter.configure(state="disabled")
        self.gutter.yview_moveto(self.text.yview()[0])

    def get(self):
        return self.text.get("1.0", "end-1c")

    def set(self, content):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self._update_gutter()

    def mark_lines(self, lines, tag="error_line"):
        self.text.tag_remove(tag, "1.0", "end")
        for ln in lines:
            self.text.tag_add(tag, f"{ln}.0", f"{ln}.0 lineend+1c")

    def clear_marks(self):
        for tag in ("error_line", "warn_line"):
            self.text.tag_remove(tag, "1.0", "end")

    def goto(self, line, col=1):
        self.text.mark_set("insert", f"{line}.{max(col - 1, 0)}")
        self.text.see("insert")
        self.text.focus_set()
