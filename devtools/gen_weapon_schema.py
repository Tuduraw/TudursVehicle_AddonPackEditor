"""Regenerates schemas/weapon.json.

Usage:
    python devtools/gen_weapon_schema.py [path to tudursvehiclemod checkout]

Sources, in priority order:
  - Which keys exist, and their numeric kind/default where the code is unambiguous:
    WeaponStatsLoader.parseContent() (weapon_code_parser.py). The code is the truth.
  - Description text: the mod's Readme_Weapon*.md (Japanese) and Another language/en_US
    (English), for keys they document; otherwise devtools/weapon_meta.json.
  - Canonical spelling, grouping, which Type values use a key, composite-value column
    layouts, enum choices: devtools/weapon_meta.json (hand-maintained).

The script fails loudly if the loader reads a key weapon_meta.json doesn't describe,
or weapon_meta.json describes a key the loader no longer reads - so a mod update that
adds or drops a key can't slip through unnoticed. A default stated in weapon_meta.json
that disagrees with the code is reported and the code's value is used.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from doc_parser import CAMEL, parse_doc  # noqa: E402
from weapon_code_parser import extract  # noqa: E402

TYPE_DOCS = ["Readme_Weapon.md", "Readme_Weapon_Bomb.md", "Readme_Weapon_Cas.md", "Readme_Weapon_Missile.md",
             "Readme_Weapon_Special.md", "Readme_Weapon_Torpedo.md"]


def load_docs(repo):
    out = {}
    for lang, base in (("ja", repo), ("en", repo / "Another language" / "en_US")):
        entries = []
        for name in TYPE_DOCS:
            p = base / name
            if p.exists():
                entries += parse_doc(p, CAMEL)
        out[lang] = entries
    return out


def doc_lookup(entries, key, context_candidates):
    for ctx in context_candidates:
        for e in entries:
            if key in e["keys"] and e["context"] == ctx and e["desc"]:
                return e
    for e in entries:
        if key in e["keys"] and e["desc"]:
            return e
    return None


def build(repo):
    repo = Path(repo)
    code = extract(repo)
    meta = json.loads((HERE / "weapon_meta.json").read_text(encoding="utf-8"))
    keys_meta = meta["keys"]
    missing = sorted(set(code) - set(keys_meta))
    stale = sorted(set(keys_meta) - set(code))
    if missing or stale:
        raise SystemExit(f"weapon_meta.json out of sync with the loader.\n  undescribed keys: {missing}\n  keys no longer read: {stale}")

    projectile = meta["weapon_types"]["projectile"]
    all_types = projectile + meta["weapon_types"]["other"]
    docs = load_docs(repo)
    type_desc = {}
    for t in all_types:
        for lang in ("ja", "en"):
            e = doc_lookup(docs[lang], t, [None])
            if e:
                type_desc.setdefault(t, {})[lang] = e["desc"]

    warnings = []
    fields = []
    for key, m in keys_meta.items():
        c = code[key]
        f = {"key": m["name"], "group": m["group"], "required": bool(m.get("required"))}
        kind = m.get("kind") or c.get("kind") or "string"
        if kind == "list_columns":
            f.update(type="list", item_type="columns", columns=m["columns"])
            for opt in ("min_rows", "max_rows"):
                if opt in m:
                    f[opt] = m[opt]
        elif kind == "columns":
            f.update(type="columns", columns=m["columns"])
        else:
            f["type"] = kind
            if "choices" in m:
                f["choices"] = m["choices"]
        if key == "type":
            f["choices"] = all_types
        # default: code wins; metadata fills in what the code can't state plainly
        code_default = c.get("default")
        if isinstance(code_default, dict) and "same_as" in code_default:
            ref = keys_meta.get(code_default["same_as"], {}).get("name", code_default["same_as"])
            f["default_display"] = {"ja": f"{ref}と同じ", "en": f"same as {ref}"}
        elif code_default is not None:
            f["default"] = code_default
            if "default" in m and m["default"] != code_default:
                warnings.append(f"{m['name']}: weapon_meta default {m['default']!r} != code {code_default!r} (code used)")
        elif "default" in m:
            f["default"] = m["default"]
        if "default_display" in m:
            f["default_display"] = m["default_display"]
        if m.get("hint"):
            f["hint"] = m["hint"]
        # which Type values use this key
        types = m["types"]
        if types == "P":
            f["entity_types"] = list(projectile)
        elif isinstance(types, list):
            f["entity_types"] = list(types)
        # descriptions: docs first (scoped to the key's own type section when it has one)
        ctx = [t for t in (types if isinstance(types, list) else [])] + [None]
        for lang in ("ja", "en"):
            e = doc_lookup(docs[lang], m["name"], ctx)
            if e:
                f.setdefault("desc", {})[lang] = e["desc"]
                if e["example"]:
                    f.setdefault("example", {})[lang] = e["example"]
            elif m.get("desc", {}).get(lang):
                f.setdefault("desc", {})[lang] = m["desc"][lang]
        if m["name"].startswith("Carrier"):
            twin = "Cas" + m["name"][len("Carrier"):]
            twin_meta = next((v for v in keys_meta.values() if v["name"] == twin), None)
            if twin_meta:
                for lang in ("ja", "en"):
                    e = doc_lookup(docs[lang], twin, twin_meta["types"] if isinstance(twin_meta["types"], list) else [None])
                    if e and e["desc"]:
                        base = f.get("desc", {}).get(lang, "")
                        f.setdefault("desc", {})[lang] = (base + "\n" if base else "") + f"({twin}) " + e["desc"]
        if "desc" not in f:
            warnings.append(f"{m['name']}: no description in docs or weapon_meta.json")
        fields.append(f)

    schema = {
        "schema_version": 1,
        "kind": "weapon",
        "root": "WeaponFile",
        "entity_types": [{"id": t, "label": {"ja": t, "en": t}, "category": "projectile" if t in projectile else "other",
                          **({"desc": type_desc[t]} if t in type_desc else {})} for t in all_types],
        "records": {"WeaponFile": {"fields": fields}},
    }
    return schema, warnings


if __name__ == "__main__":
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent.parent.parent
    schema, warnings = build(repo)
    out = HERE.parent / "schemas" / "weapon.json"
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out} - keys={len(schema['records']['WeaponFile']['fields'])} types={len(schema['entity_types'])}")
    for w in warnings:
        print("  warning:", w)
