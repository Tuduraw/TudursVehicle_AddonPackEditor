"""Plugin loading.

A plugin is a folder under plugins/ (next to main.py) or under the user plugin folder
(~/.tudursvehiclemod_addon_editor/plugins/), containing:

    plugin.json    required - {"id", "name": {ja, en}, "version", "schemas": [...],
                   "python": "plugin.py" (optional)}
    <schema>.json  schema contributions, listed in plugin.json's "schemas"
                   (format: see ape/schema.py)
    lang/          optional ja.json / en.json with extra UI strings
    plugin.py      optional code; must define register(api)

register(api) receives a PluginAPI and may call:
    api.add_validator(kind, fn)   fn(data: dict, entity_type: str) -> list[str]
                                  (warning messages shown before saving)
    api.add_save_hook(kind, fn)   fn(data: dict, ctx: dict) -> dict
                                  (may adjust the data just before it is written;
                                  ctx has "project", "name", "entity_type")
    api.add_schema_file(path)     same as listing it in plugin.json
    api.t / api.pick              the tool's own i18n helpers
A plugin that fails to load is reported and skipped; it never stops the tool.
"""
import importlib.util
import json
import traceback
from pathlib import Path

from . import i18n

BUILTIN_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins"
USER_PLUGIN_DIR = Path.home() / ".tudursvehiclemod_addon_editor" / "plugins"


class PluginAPI:
    def __init__(self, manager, plugin):
        self._m = manager
        self._p = plugin
        self.t = i18n.t
        self.pick = i18n.pick

    def add_validator(self, kind, fn):
        self._m.validators.setdefault(kind, []).append((self._p["id"], fn))

    def add_save_hook(self, kind, fn):
        self._m.save_hooks.setdefault(kind, []).append((self._p["id"], fn))

    def add_schema_file(self, path):
        self._m.registry.merge_file(Path(self._p["dir"]) / path, self._p["id"])


class PluginManager:
    def __init__(self, registry):
        self.registry = registry
        self.plugins = []
        self.errors = []
        self.validators = {}
        self.save_hooks = {}

    def discover(self):
        for base in (BUILTIN_PLUGIN_DIR, USER_PLUGIN_DIR):
            if not base.is_dir():
                continue
            for d in sorted(p for p in base.iterdir() if (p / "plugin.json").exists()):
                self._load(d)

    def _load(self, d):
        try:
            meta = json.loads((d / "plugin.json").read_text(encoding="utf-8"))
            meta["dir"] = str(d)
            meta.setdefault("id", d.name)
            if (d / "lang").is_dir():
                i18n.add_lang_dir(d / "lang")
            for s in meta.get("schemas", []):
                self.registry.merge_file(d / s, meta["id"])
            if meta.get("python"):
                spec = importlib.util.spec_from_file_location(f"ape_plugin_{meta['id']}", d / meta["python"])
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "register"):
                    module.register(PluginAPI(self, meta))
            self.plugins.append(meta)
        except Exception:  # noqa: BLE001 - a broken plugin must not take the tool down
            self.errors.append((d.name, traceback.format_exc()))

    def validate(self, kind, data, entity_type):
        out = []
        for pid, fn in self.validators.get(kind, []):
            try:
                out.extend(f"[{pid}] {m}" for m in (fn(data, entity_type) or []))
            except Exception as e:  # noqa: BLE001
                out.append(f"[{pid}] validator error: {e}")
        return out

    def apply_save_hooks(self, kind, data, ctx):
        for pid, fn in self.save_hooks.get(kind, []):
            try:
                result = fn(data, ctx)
                if isinstance(result, dict):
                    data = result
            except Exception as e:  # noqa: BLE001
                self.errors.append((pid, f"save hook error: {e}"))
        return data
