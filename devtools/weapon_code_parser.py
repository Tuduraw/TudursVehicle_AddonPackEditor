"""Extracts which keys WeaponStatsLoader.parseContent() reads, and - where the code
makes it unambiguous - each key's default value and numeric kind.

Only the simple, common shapes are recognized:
    toFloat(entries.get("k"), 0.0f)                -> float, default 0.0
    (int) toFloat(entries.get("k"), 7.0f)          -> int, default 7
    toFloat(entries.get("k"), SOME_CONSTANT)       -> constant resolved in the same file
    toFloat(entries.get("k"), otherLocal)          -> {"same_as": <key that local came from>}
    "true".equalsIgnoreCase(getOrDefault("k", "false"))   -> bool, default false
    !"false".equalsIgnoreCase(getOrDefault("k", "true"))  -> bool, default true
Anything else (composite comma-separated values, enums, strings) is left to the
hand-written metadata; this module only reports that the key exists.
"""
import re
from pathlib import Path

LOADER = "src/main/java/com/example/tudursvehiclemod/asset/WeaponStatsLoader.java"


def _body(src):
    start = src.index("private static WeaponStats parseContent")
    end = src.index("private static int parseArgbColor")
    return re.sub(r"//[^\n]*", "", src[start:end])


def extract(repo):
    src = (Path(repo) / LOADER).read_text(encoding="utf-8")
    consts = {m.group(1): float(m.group(2)) for m in re.finditer(
        r"static final (?:float|double) (\w+)\s*=\s*(-?[\d.]+)[fFdD]?;", src)}
    body = _body(src)
    keys = list(dict.fromkeys(
        re.findall(r'entries\.(?:get|getOrDefault|containsKey)\("([a-z0-9]+)"', body)
        + re.findall(r'key\.equals\("([a-z0-9]+)"\)', body)))
    repeatable = set(re.findall(r'key\.equals\("([a-z0-9]+)"\)', body))
    # local variable name -> key it was read from (for "defaults to the same as X")
    local_of = {}
    for m in re.finditer(r'(?:float|int|double)\s+(\w+)\s*=\s*[^;]*?entries\.get\("([a-z0-9]+)"\)', body):
        local_of[m.group(1)] = m.group(2)

    info = {k: {"repeatable": k in repeatable} for k in keys}
    guarded = set(re.findall(r'entries\.containsKey\("([a-z0-9]+)"\)\s*\?', body))
    for m in re.finditer(r'(\(int\)\s*)?toFloat\(entries\.get\("([a-z0-9]+)"\),\s*([^)]+?)\)', body):
        is_int, key, raw = bool(m.group(1)), m.group(2), m.group(3).strip()
        if key in guarded:
            info[key]["kind"] = "int" if is_int else "float"
            continue
        num = re.fullmatch(r"(-?[\d.]+)[fFdD]?", raw)
        if num:
            val = float(num.group(1))
        elif raw in consts:
            val = consts[raw]
        elif raw in local_of:
            info[key]["default"] = {"same_as": local_of[raw]}
            info[key]["kind"] = "int" if is_int else "float"
            continue
        else:
            continue
        info[key]["kind"] = "int" if is_int else "float"
        info[key]["default"] = int(val) if is_int else val
    for m in re.finditer(r'(!?)"(true|false)"\.equalsIgnoreCase\(entries\.getOrDefault\("([a-z0-9]+)",\s*"(true|false)"\)', body):
        info[m.group(3)]["kind"] = "bool"
        info[m.group(3)]["default"] = m.group(4) == "true"
    return info


if __name__ == "__main__":
    import sys, json
    data = extract(sys.argv[1])
    print(len(data), "keys")
    print(json.dumps({k: v for k, v in data.items() if "default" in v}, ensure_ascii=False)[:1500])
