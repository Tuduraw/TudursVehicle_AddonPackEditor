#!/usr/bin/env python3
"""Addon Pack Editor for Tudur's Vehicle Mod.

Run:  python main.py
Requires Python 3.9+ with tkinter (bundled with the python.org installers on Windows
and macOS; on Linux install the python3-tk package).
"""
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ape.ui.app import App  # noqa: E402


def main():
    root = tk.Tk()
    root.geometry("1280x820")
    root.minsize(960, 600)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
