"""Lists the named groups in an OBJ file - the same "o" / "g" names the mod's own
ObjModelLoader uses for spinning_parts, toggle_parts, weapon_parts and so on."""
from pathlib import Path

_cache = {}


def list_groups(path):
    """Group names in file order, without duplicates. Cached per (path, mtime)."""
    path = Path(path)
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return []
    if key in _cache:
        return _cache[key]
    names, seen = [], set()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(("o ", "g ")):
                    name = line[2:].strip()
                    if name and name not in seen and name != "default":
                        seen.add(name)
                        names.append(name)
    except OSError:
        return []
    _cache[key] = names
    return names
