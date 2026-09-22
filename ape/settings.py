"""Per-user settings, stored as JSON in the user's home directory so they survive
tool updates (the tool folder itself may be replaced wholesale)."""
import json
from pathlib import Path

SETTINGS_PATH = Path.home() / ".tudursvehiclemod_addon_editor" / "settings.json"
DEFAULTS = {"language": "ja", "recent_packs": [], "last_subfolders": {}}


class Settings:
    def __init__(self, path=SETTINGS_PATH):
        self.path = Path(path)
        self.data = json.loads(json.dumps(DEFAULTS))
        try:
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def add_recent(self, pack_path):
        recent = [p for p in self.data.get("recent_packs", []) if p != str(pack_path)]
        self.data["recent_packs"] = [str(pack_path)] + recent[:9]
        self.save()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass  # settings are a convenience; never block the user over them
