"""An addon pack on disk.

Layout (see the mod's Readme_Addon.md). The parts the mod fixes are fixed here too;
the pack folder name, the namespace, and any subfolders BELOW each fixed root are free
(any depth - every loader in the mod scans its root recursively):

    <pack>/data/<ns>/vehicles/<sub...>/<name>.json
    <pack>/assets/<ns>/models/<sub...>/<name>.obj      referenced as ns:models/<sub>/<name>.obj
    <pack>/assets/<ns>/textures/<sub...>/<name>.png    referenced as ns:textures/<sub>/<name>.png
    <pack>/assets/<ns>/textures/gui/<sub...>/<name>.png   HUD textures, referenced by bare name
    <pack>/assets/<ns>/weapons/<sub...>/<name>.txt     referenced by bare name
    <pack>/assets/<ns>/hud/<sub...>/<name>.txt         referenced by bare name
    <pack>/assets/<ns>/sounds/<sub...>/<name>.ogg      referenced by bare name

Weapons, HUD scripts, HUD textures and sounds are looked up by file name alone, so
their names must be unique across subfolders; models and textures carry their full
path in the reference, so only the path has to be unique.

The editor keeps its own small metadata file, addon_pack_editor.json, at the pack
root. The mod never reads it.
"""
import json
import re
import shutil
from pathlib import Path

META_FILE = "addon_pack_editor.json"
NAMESPACE_RE = re.compile(r"^[a-z0-9_.-]+$")
PATH_PART_RE = re.compile(r"^[a-z0-9_.-]+$")

# kind -> (root relative to assets/<ns>, default subfolder, extensions, reference style)
ASSET_KINDS = {
    "model": ("models", "obj", (".obj",), "identifier"),
    "texture": ("textures", "vehicle", (".png",), "identifier"),
    "gui_texture": ("textures/gui", "", (".png",), "name"),
    "weapon": ("weapons", "", (".txt",), "name"),
    "hud": ("hud", "", (".txt",), "name"),
    "sound": ("sounds", "", (".ogg",), "name"),
}


def normalize_subfolder(sub):
    """'Obj\\Tanks/ modern/' -> 'obj/tanks/modern'. Raises ValueError for anything the
    game's resource identifiers can't hold (they must be lowercase [a-z0-9_.-])."""
    parts = [p.strip() for p in re.split(r"[\\/]+", sub or "") if p.strip()]
    parts = [p.lower() for p in parts]
    for p in parts:
        if not PATH_PART_RE.match(p) or p in (".", ".."):
            raise ValueError(p)
    return "/".join(parts)


def normalize_file_name(name):
    stem = Path(name).stem.lower().replace(" ", "_")
    stem = re.sub(r"[^a-z0-9_.-]", "_", stem)
    return stem or "unnamed"


class PackProject:
    def __init__(self, root, namespace):
        self.root = Path(root)
        self.namespace = namespace

    # -- lifecycle ---------------------------------------------------------------

    @classmethod
    def create(cls, root, namespace):
        if not NAMESPACE_RE.match(namespace):
            raise ValueError(namespace)
        p = cls(root, namespace)
        p.vehicles_dir.mkdir(parents=True, exist_ok=True)
        for kind in ASSET_KINDS:
            p.asset_root(kind).mkdir(parents=True, exist_ok=True)
        p.save_meta()
        return p

    @classmethod
    def open(cls, root):
        root = Path(root)
        ns = None
        try:
            ns = json.loads((root / META_FILE).read_text(encoding="utf-8")).get("namespace")
        except (OSError, ValueError):
            pass
        if not ns:
            # An existing pack not made with this tool: take the first namespace found.
            for base in (root / "data", root / "assets"):
                if base.is_dir():
                    dirs = sorted(d.name for d in base.iterdir() if d.is_dir())
                    if dirs:
                        ns = dirs[0]
                        break
        if not ns:
            raise FileNotFoundError(root)
        p = cls(root, ns)
        p.save_meta()
        return p

    def save_meta(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / META_FILE).write_text(
            json.dumps({"namespace": self.namespace, "format": 1}, indent=1), encoding="utf-8")

    # -- paths -------------------------------------------------------------------

    @property
    def vehicles_dir(self):
        return self.root / "data" / self.namespace / "vehicles"

    @property
    def assets_dir(self):
        return self.root / "assets" / self.namespace

    def asset_root(self, kind):
        return self.assets_dir / ASSET_KINDS[kind][0]

    def vehicle_path(self, name):
        """name may contain subfolders: 'tanks/modern/t90'."""
        return self.vehicles_dir / (normalize_subfolder(name) + ".json")

    def list_vehicles(self):
        if not self.vehicles_dir.is_dir():
            return []
        return sorted(p.relative_to(self.vehicles_dir).with_suffix("").as_posix()
                      for p in self.vehicles_dir.rglob("*.json"))

    def list_assets(self, kind):
        root = self.asset_root(kind)
        if not root.is_dir():
            return []
        exts = ASSET_KINDS[kind][2]
        files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts]
        if kind == "texture":  # HUD textures live under textures/gui and are a separate kind
            gui = self.asset_root("gui_texture")
            files = [p for p in files if gui not in p.parents]
        return sorted(files)

    def reference_for(self, kind, path):
        """How a vehicle JSON refers to this file."""
        path = Path(path)
        style = ASSET_KINDS[kind][3]
        if style == "name":
            return path.stem
        rel = path.relative_to(self.assets_dir).as_posix()
        return f"{self.namespace}:{rel}"

    def list_references(self, kind):
        return [self.reference_for(kind, p) for p in self.list_assets(kind)]

    def resolve_identifier(self, identifier):
        """ns:models/obj/x.obj -> file path inside this pack (or None if it lives in
        another namespace / doesn't exist)."""
        if not identifier or ":" not in identifier:
            return None
        ns, rel = identifier.split(":", 1)
        if ns != self.namespace:
            return None
        p = self.assets_dir / rel
        return p if p.exists() else None

    def duplicate_name(self, kind, name, exclude=None):
        """For name-referenced kinds: another file with the same stem already exists."""
        if ASSET_KINDS[kind][3] != "name":
            return None
        for p in self.list_assets(kind):
            if p.stem == name and (exclude is None or Path(p) != Path(exclude)):
                return p
        return None

    # -- import ------------------------------------------------------------------

    def import_asset(self, kind, src, subfolder=None, overwrite=False):
        """Copies src into the pack. Returns (destination path, reference string).
        Raises FileExistsError if the destination (or, for name-referenced kinds, a
        same-named file elsewhere) already exists and overwrite is False."""
        root_rel, default_sub, exts, _ = ASSET_KINDS[kind]
        src = Path(src)
        if src.suffix.lower() not in exts:
            raise ValueError(src.suffix)
        sub = normalize_subfolder(default_sub if subfolder is None else subfolder)
        dest_dir = self.asset_root(kind) / sub if sub else self.asset_root(kind)
        dest = dest_dir / (normalize_file_name(src.name) + src.suffix.lower())
        if not overwrite:
            clash = dest if dest.exists() else self.duplicate_name(kind, dest.stem)
            if clash:
                raise FileExistsError(str(clash))
        dest_dir.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        return dest, self.reference_for(kind, dest)
