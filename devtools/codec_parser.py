"""Extracts record structure from the mod's own Codec declarations.

Only reads the Java source as text - no compilation. Handles the patterns the mod
actually uses: RecordCodecBuilder groups, fieldOf / optionalFieldOf (with or without
a default), .listOf() / Codec.list(...), and a bare X.CODEC.forGetter(...) entry,
which is a MapCodec whose fields are flattened into the parent object.
"""
import re
from pathlib import Path

PRIMITIVES = {
    "Codec.STRING": "string", "Codec.DOUBLE": "float", "Codec.FLOAT": "float",
    "Codec.INT": "int", "Codec.BOOL": "bool", "Codec.LONG": "int",
    "Identifier.CODEC": "identifier",
}


def _split_top_level(text):
    """Splits a group(...) body on commas that are not nested in brackets/strings."""
    parts, depth, cur, in_str = [], 0, [], False
    i = 0
    while i < len(text):
        c = text[i]
        if in_str:
            cur.append(c)
            if c == "\\":
                cur.append(text[i + 1]); i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True; cur.append(c)
        elif c in "([{":
            depth += 1; cur.append(c)
        elif c in ")]}":
            depth -= 1; cur.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(cur)); cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _group_body(src, start):
    """Returns the text inside instance.group( ... ) beginning at index start."""
    i = src.index("group(", start) + len("group(")
    depth, j = 1, i
    in_str = False
    while depth:
        c = src[j]
        if in_str:
            if c == "\\": j += 1
            elif c == '"': in_str = False
        elif c == '"': in_str = True
        elif c == "(": depth += 1
        elif c == ")": depth -= 1
        j += 1
    return src[i:j - 1]


def _parse_default(raw):
    raw = raw.strip()
    if raw in ("true", "false"):
        return raw == "true"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    m = re.fullmatch(r"(-?\d*\.?\d+(?:[eE]-?\d+)?)[fFdD]?", raw)
    if m:
        return float(m.group(1))
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw.startswith("List.of()") or raw == "java.util.List.of()":
        return []
    if raw.startswith("Optional.empty"):
        return None
    return {"__expr__": raw}  # a constant reference; resolved later if possible


def _classify(codec_expr):
    """Returns (kind, detail): kind in primitive/list/object/inline/unknown."""
    e = codec_expr.strip()
    e = re.sub(r"\s+", "", e)
    for prefix in ("com.example.tudursvehiclemod.asset.", "net.minecraft.util."):
        e = e.replace(prefix, "")
    m = re.fullmatch(r"Codec\.list\((.+)\)", e) or re.fullmatch(r"(.+)\.listOf\(\)", e)
    if m:
        inner_kind, inner = _classify(m.group(1))
        return "list", (inner_kind, inner)
    if e in PRIMITIVES:
        return "primitive", PRIMITIVES[e]
    m = re.fullmatch(r"([A-Z]\w*)\.CODEC", e) or re.fullmatch(r"([A-Z]\w*)\.MAP_CODEC", e)
    if m:
        return "object", m.group(1)
    if "floatRange" in e or "intRange" in e:
        return "primitive", "float" if "float" in e else "int"
    return "unknown", e


def parse_entry(entry):
    entry = " ".join(entry.split())
    m = re.search(r"^(.*?)\.(fieldOf|optionalFieldOf)\((.*)\)\s*\.forGetter\(", entry)
    if not m:
        m2 = re.search(r"^(.*?)\.forGetter\(", entry)
        if m2:
            kind, detail = _classify(m2.group(1))
            if kind == "object":
                return {"inline": detail}
        return {"unparsed": entry[:120]}
    codec_expr, method, args = m.group(1), m.group(2), m.group(3)
    arg_parts = _split_top_level(args)
    key = arg_parts[0].strip().strip('"')
    field = {"key": key, "required": method == "fieldOf"}
    g = re.search(r"\.forGetter\(\s*\w+::(\w+)\s*\)", entry)
    if g:
        field["getter"] = g.group(1)
    if len(arg_parts) > 1:
        field["default"] = _parse_default(arg_parts[1])
    kind, detail = _classify(codec_expr)
    if kind == "primitive":
        field["type"] = detail
    elif kind == "object":
        field["type"] = "object"; field["ref"] = detail
    elif kind == "list":
        ik, idetail = detail
        field["type"] = "list"
        if ik == "object":
            field["item_ref"] = idetail
        else:
            field["item_type"] = idetail if ik == "primitive" else "string"
    else:
        field["type"] = "string"; field["codec"] = detail
    return field


def parse_asset_dir(asset_dir):
    records = {}
    for path in sorted(Path(asset_dir).glob("*.java")):
        src = _strip_comments(path.read_text(encoding="utf-8"))
        for m in re.finditer(r"(?:MapCodec|Codec)<\s*(\w+)\s*>\s+(CODEC|MAP_CODEC)\s*=\s*RecordCodecBuilder\.(create|mapCodec)", src):
            name = m.group(1)
            body = _group_body(src, m.end())
            fields = [parse_entry(e) for e in _split_top_level(body)]
            records[name] = {"fields": fields, "source": path.name}
    return records


if __name__ == "__main__":
    import json, sys
    recs = parse_asset_dir(sys.argv[1])
    total = sum(len(r["fields"]) for r in recs.values())
    bad = [(n, f) for n, r in recs.items() for f in r["fields"] if "unparsed" in f]
    print(f"records={len(recs)} entries={total} unparsed={len(bad)}")
    for n, f in bad[:20]:
        print("  ", n, f)
    unknown = [(n, f["key"], f.get("codec")) for n, r in recs.items() for f in r["fields"] if "codec" in f]
    for u in unknown[:20]:
        print("  codec?", u)
    exprs = [(n, f["key"], f["default"]) for n, r in recs.items() for f in r["fields"] if isinstance(f.get("default"), dict)]
    print("expr defaults:", len(exprs)); [print("  ", e) for e in exprs[:30]]
