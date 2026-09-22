"""Regenerates schemas/vehicle.json from the mod's own source code and docs.

Usage:
    python devtools/gen_schema.py [path to tudursvehiclemod checkout]
    (defaults to the repository this tool lives in)

Structure (field names, types, required/optional, defaults) comes from the Codec
declarations - the same thing the mod itself parses JSON with, so it can't drift.
Descriptions come from Readme_Vehicle*.md (Japanese) and Another language/en_US
(English). Which entity types a top-level field applies to comes from which
Readme_Vehicle_<Type>.md documents it: a field documented in one or more
type-specific docs is shown only for those types; anything documented only in the
common Readme_Vehicle.md is shown for every type.

Hand corrections live in devtools/schema_overrides.json and are applied last, so a
regeneration never loses them.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from codec_parser import parse_asset_dir  # noqa: E402
from doc_parser import parse_doc, index_docs  # noqa: E402

TYPE_DOCS = {
    "Car": "tudursvehiclemod:car", "Aircraft": "tudursvehiclemod:aircraft",
    "Helicopter": "tudursvehiclemod:helicopter", "Ship": "tudursvehiclemod:ship",
    "Submarine": "tudursvehiclemod:submarine", "Vtol": "tudursvehiclemod:vtol",
    "StaticEmplacement": "tudursvehiclemod:static_emplacement",
}
ENTITY_TYPE_LABELS = {
    "tudursvehiclemod:car": {"ja": "車両(地上)", "en": "Car (ground)"},
    "tudursvehiclemod:aircraft": {"ja": "固定翼機", "en": "Aircraft (fixed-wing)"},
    "tudursvehiclemod:helicopter": {"ja": "ヘリコプター", "en": "Helicopter"},
    "tudursvehiclemod:ship": {"ja": "艦船", "en": "Ship"},
    "tudursvehiclemod:submarine": {"ja": "潜水艦", "en": "Submarine"},
    "tudursvehiclemod:vtol": {"ja": "VTOL機", "en": "VTOL"},
    "tudursvehiclemod:static_emplacement": {"ja": "固定砲台", "en": "Static emplacement"},
}
# Which string fields point at an asset or an OBJ group, keyed by (record, key)
# or by key alone. Drives the file-link and part-suggestion features.
HINTS_BY_FIELD = {
    ("VehicleDefinition", "model"): "obj_model",
    ("VehicleDefinition", "texture"): "texture",
    ("VehicleDefinition", "entity_type"): "entity_type",
    ("VehicleExtras", "hud"): "hud",
    ("VehicleExtras", "engine_sound"): "sound",
    ("WeaponSoundSettings", "sound"): "sound",
    ("WeaponDefinition", "projectile_item"): "item",
    ("PartAnimation", "vtol_rotor_parent"): "obj_group",
    ("WeaponOffset", "linked_part"): "obj_group",
}
HINTS_BY_KEY = {"part": "obj_group", "weapon_name": "weapon"}


def parse_enums(asset_dir):
    enums = {}
    for path in Path(asset_dir).glob("*.java"):
        src = path.read_text(encoding="utf-8")
        src_nc = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", src, flags=re.S))
        m = re.search(r"enum\s+(\w+)[^{]*\{(.*?);", src_nc, flags=re.S)
        if not m:
            continue
        consts = re.findall(r"\b([A-Z][A-Z0-9_]*)\b\s*(?:\([^)]*\))?\s*(?:,|$)", m.group(2).strip() + ",")
        if not consts:
            continue
        string_identifiable = "StringIdentifiable" in src
        values = [c.lower() for c in consts] if string_identifiable else consts
        enums[m.group(1)] = values
    # enums declared as nested types inside a record (e.g. SearchLightPart.FollowMode)
    for path in Path(asset_dir).glob("*.java"):
        src = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S))
        for m in re.finditer(r"enum\s+(\w+)\s*\{([^;}]*)[;}]", src):
            if m.group(1) in enums:
                continue
            consts = re.findall(r"\b([A-Z][A-Z0-9_]*)\b", m.group(2))
            if consts:
                enums[m.group(1)] = consts
    return enums


def load_docs(repo):
    """Returns {lang: [(doc_type_or_None, entries)]}."""
    docs = {"ja": [], "en": []}
    for lang, base in (("ja", repo), ("en", repo / "Another language" / "en_US")):
        common = base / "Readme_Vehicle.md"
        if common.exists():
            docs[lang].append((None, parse_doc(common)))
        for suffix, etype in TYPE_DOCS.items():
            p = base / f"Readme_Vehicle_{suffix}.md"
            if p.exists():
                docs[lang].append((etype, parse_doc(p)))
    return docs


ENTITY_CLASS_TYPES = {
    "CarEntity": ["tudursvehiclemod:car"],
    "AircraftEntity": ["tudursvehiclemod:aircraft"],
    "HelicopterEntity": ["tudursvehiclemod:helicopter"],
    "ShipEntity": ["tudursvehiclemod:ship"],
    "SubmarineEntity": ["tudursvehiclemod:submarine"],
    "VtolEntity": ["tudursvehiclemod:vtol"],
    "StaticEmplacementEntity": ["tudursvehiclemod:static_emplacement"],
}


def compute_type_scope(repo, records):
    """Which entity types actually READ each top-level field, judged from the source.

    A field's accessor called from a type-specific entity class (or a subclass of one -
    VtolEntity extends AircraftEntity, so aircraft-only fields also reach VTOL) limits it
    to those types. A call from anywhere else - AbstractVehicleEntity, the renderer, HUD,
    networking - means every type can use it, so no restriction is recorded.
    Returns {key: sorted list of entity type ids} only for restricted fields."""
    src_root = repo / "src"
    entity_dir = repo / "src/main/java/com/example/tudursvehiclemod/entity"
    parents = {}
    for p in entity_dir.glob("*.java"):
        m = re.search(r"class\s+(\w+)\s+extends\s+(\w+)", p.read_text(encoding="utf-8"))
        if m:
            parents[m.group(1)] = m.group(2)

    def types_for_class(cls):
        out = set()
        for sub, types in ENTITY_CLASS_TYPES.items():
            c = sub
            while c:
                if c == cls:
                    out.update(types)
                    break
                c = parents.get(c)
        return out

    files = [p for p in src_root.rglob("*.java") if "/asset/" not in p.as_posix()]
    texts = {p: re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", p.read_text(encoding="utf-8"), flags=re.S)) for p in files}

    def root_getters(rname):
        for f in records[rname]["fields"]:
            if "inline" in f:
                yield from root_getters(f["inline"])
            elif "getter" in f:
                yield f["key"], f["getter"]

    scope = {}
    for key, getter in root_getters("VehicleDefinition"):
        pat = re.compile(r"\." + re.escape(getter) + r"\(\)")
        users = set()
        everywhere = False
        for p, t in texts.items():
            if not pat.search(t):
                continue
            cls = p.stem
            if cls in ENTITY_CLASS_TYPES:
                users |= types_for_class(cls)
            else:
                everywhere = True
                break
        if not everywhere and users:
            scope[key] = sorted(users)
    return scope


def find_desc(docs_lang, context, key):
    """Best-effort lookup: same section context first, then any section."""
    for _, entries in docs_lang:
        by_ctx, _ = index_docs(entries)
        if (context, key) in by_ctx:
            return by_ctx[(context, key)]
    for _, entries in docs_lang:
        _, by_key = index_docs(entries)
        if key in by_key:
            return by_key[key][0]
    return None


def build(repo):
    repo = Path(repo)
    asset_dir = repo / "src/main/java/com/example/tudursvehiclemod/asset"
    records = parse_asset_dir(asset_dir)
    enums = parse_enums(asset_dir)
    docs = load_docs(repo)

    type_scope = compute_type_scope(repo, records)

    # Parent list key per record, so nested descriptions can use section context.
    parent_ctx = {}
    for rname, rec in records.items():
        for f in rec["fields"]:
            ref = f.get("item_ref") or f.get("ref")
            if ref and "key" in f:
                parent_ctx.setdefault(ref, f["key"])

    def expand(rname, is_root, group=None):
        out = []
        for f in records[rname]["fields"]:
            if "inline" in f:
                out.extend(expand(f["inline"], is_root, f["inline"]))
                continue
            f = dict(f)
            if is_root:
                f["group"] = group or ("Parts" if f.get("type") == "list" else "Basic")
            ref = f.get("ref")
            if ref in enums:
                f["type"] = "enum"; f["choices"] = enums[ref]; f.pop("ref")
            if f.get("codec", "").startswith("Codec.STRING.xmap(") :
                enum_name = re.match(r"Codec\.STRING\.xmap\((\w+)::", f["codec"])
                if enum_name and enum_name.group(1) in enums:
                    f["type"] = "enum"; f["choices"] = enums[enum_name.group(1)]
                f.pop("codec", None)
            d = f.get("default")
            if isinstance(d, dict) and "__expr__" in d:
                expr = d["__expr__"]
                m = re.fullmatch(r"(\w+)\.([A-Z_]+)", expr)
                if m and m.group(1) in enums:
                    vals = enums[m.group(1)]
                    f["default"] = m.group(2).lower() if m.group(2).lower() in vals else m.group(2)
                elif expr == "Float.MAX_VALUE":
                    f["default"] = "unlimited"
                else:
                    f.pop("default")
            hint = HINTS_BY_FIELD.get((rname, f["key"])) or HINTS_BY_KEY.get(f["key"])
            if hint:
                f["hint"] = hint
            ctx = None if is_root else parent_ctx.get(rname)
            for lang in ("ja", "en"):
                e = find_desc(docs[lang], ctx, f["key"])
                if e:
                    f.setdefault("desc", {})[lang] = e["desc"]
                    if e["example"]:
                        f.setdefault("example", {})[lang] = e["example"]
            if is_root and f["key"] in type_scope:
                f["entity_types"] = sorted(type_scope[f["key"]])
            out.append(f)
        return out

    root_fields = expand("VehicleDefinition", True)
    out_records = {"VehicleDefinition": {"fields": root_fields}}
    pending = [f.get("item_ref") or f.get("ref") for f in root_fields]
    while pending:
        r = pending.pop()
        if not r or r in out_records or r not in records:
            continue
        fields = expand(r, False)
        out_records[r] = {"fields": fields}
        pending.extend(f.get("item_ref") or f.get("ref") for f in fields)

    schema = {
        "schema_version": 1,
        "kind": "vehicle",
        "root": "VehicleDefinition",
        "entity_types": [{"id": k, "label": v} for k, v in ENTITY_TYPE_LABELS.items()],
        "records": out_records,
    }
    overrides_path = HERE / "schema_overrides.json"
    if overrides_path.exists():
        apply_overrides(schema, json.loads(overrides_path.read_text(encoding="utf-8")))
    return schema


def apply_overrides(schema, overrides):
    if overrides.get("vehicle_legacy_keys"):
        schema["legacy_keys"] = dict(overrides["vehicle_legacy_keys"])
    for rname, fields in overrides.get("vehicle", {}).items():
        rec = schema["records"].get(rname)
        if not rec:
            continue
        for key, patch in fields.items():
            for f in rec["fields"]:
                if f["key"] == key:
                    if patch is None:
                        rec["fields"].remove(f)
                    else:
                        f.update(patch)
                    break


if __name__ == "__main__":
    # Default: this tool's own location inside the mod repository (tools/addon_pack_editor/devtools).
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent.parent.parent
    schema = build(repo)
    out = HERE.parent / "schemas" / "vehicle.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(len(r["fields"]) for r in schema["records"].values())
    missing = [(rn, f["key"]) for rn, r in schema["records"].items() for f in r["fields"] if "desc" not in f]
    print(f"wrote {out} - records={len(schema['records'])} fields={n} without_desc={len(missing)}")
    for m in missing:
        print("  no desc:", m)
