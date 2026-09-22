"""Loads schemas/<kind>.json and merges plugin contributions on top.

Schema file format (schema_version 1) - see schemas/vehicle.json for a full example:

    {
      "schema_version": 1,
      "kind": "vehicle",
      "root": "VehicleDefinition",
      "entity_types": [{"id": "ns:type", "label": {"ja": "...", "en": "..."},
                        "inherits": "tudursvehiclemod:car"}],
      "records": {"RecordName": {"fields": [<field>, ...]}}
    }

A field: {"key", "type", "required", "default"?, "desc"?: {ja, en}, "example"?,
"entity_types"?: [ids], "ref"? (object), "item_ref"/"item_type"? (list),
"choices"? (enum), "hint"? (obj_model / texture / sound / hud / weapon / obj_group /
item / entity_type), "group"?, "advanced"?}.

A plugin schema uses the same format; everything in it is ADDED:
  - "entity_types": new types. "inherits" names an existing type whose
    type-restricted fields should also appear for this one (an addon entity that
    extends CarEntity gets every car-only field).
  - "records": new record definitions.
  - "extend": {"ExistingRecord": [<field>, ...]} appends fields to a record.
"""
import copy
import json
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


class SchemaError(Exception):
    pass


class Schema:
    def __init__(self, data):
        if data.get("schema_version") != 1:
            raise SchemaError(f"unsupported schema_version: {data.get('schema_version')}")
        self.kind = data["kind"]
        self.root = data["root"]
        self.entity_types = {e["id"]: e for e in data.get("entity_types", [])}
        self.records = copy.deepcopy(data.get("records", {}))
        # Top-level keys the mod still accepts for compatibility, moving them into the named
        # object on load: {"hide_entity": "passenger_display", ...}
        self.legacy_keys = dict(data.get("legacy_keys", {}))
        self.sources = {"<builtin>"}

    def merge(self, data, source):
        for e in data.get("entity_types", []):
            self.entity_types[e["id"]] = e
        self.legacy_keys.update(data.get("legacy_keys", {}))
        for name, rec in data.get("records", {}).items():
            self.records[name] = copy.deepcopy(rec)
        for name, fields in data.get("extend", {}).items():
            rec = self.records.setdefault(name, {"fields": []})
            existing = {f["key"] for f in rec["fields"]}
            for f in fields:
                if f["key"] in existing:
                    rec["fields"] = [f if g["key"] == f["key"] else g for g in rec["fields"]]
                else:
                    rec["fields"].append(copy.deepcopy(f))
        self.sources.add(source)

    # -- queries -----------------------------------------------------------------

    def type_chain(self, entity_type):
        """The type itself followed by everything it inherits from."""
        chain, seen = [], set()
        t = entity_type
        while t and t not in seen:
            chain.append(t)
            seen.add(t)
            t = self.entity_types.get(t, {}).get("inherits")
        return chain

    def field_applies(self, field, entity_type):
        allowed = field.get("entity_types")
        if not allowed:
            return True
        return any(t in allowed for t in self.type_chain(entity_type))

    def fields(self, record, entity_type=None):
        rec = self.records.get(record)
        if rec is None:
            return []
        if entity_type is None:
            return list(rec["fields"])
        return [f for f in rec["fields"] if self.field_applies(f, entity_type)]

    def field(self, record, key):
        for f in self.records.get(record, {}).get("fields", []):
            if f["key"] == key:
                return f
        return None


class SchemaRegistry:
    def __init__(self):
        self.schemas = {}

    def load_builtin(self):
        for p in sorted(SCHEMA_DIR.glob("*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            self.schemas[data["kind"]] = Schema(data)

    def merge_file(self, path, source):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        kind = data.get("kind")
        if kind not in self.schemas:
            if "root" not in data:
                raise SchemaError(f"{path}: kind '{kind}' does not exist and the file does not define a new one")
            self.schemas[kind] = Schema(data)
            self.schemas[kind].sources = {source}
        else:
            self.schemas[kind].merge(data, source)

    def get(self, kind):
        return self.schemas[kind]
