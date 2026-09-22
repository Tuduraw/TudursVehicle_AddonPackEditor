"""Weapon config files (assets/<ns>/weapons/<name>.txt): MC Heli-style "Key = Value" text.

WeaponDoc edits a file line by line, so hand-written comments, blank lines, key order
and keys the schema doesn't know all survive a round trip through the form.

check() reports what the mod's own WeaponStatsLoader would do with each line - which is
mostly "silently fall back": an unreadable number becomes the default, an unknown Type
becomes OTHER, a malformed waypoint row is dropped. None of that stops the file from
loading, so it's easy to miss; this makes it visible.

How the loader reads a line (mirrored here):
  - blank lines, lines starting with ';' and lines without '=' are skipped
  - key = text before the first '=', trimmed, case-insensitive
  - value = text after it, cut at the first ';' (inline comment), trimmed
  - a repeated ordinary key: the LAST occurrence wins
  - Item / CasWaypoint / Carrier*Waypoint / CarrierRecoveryPoint may repeat (one entry per line)
"""
import re

from .i18n import t


def _parse_line(raw):
    """Returns (key_raw, value, comment) or None for a line the loader skips."""
    line = raw.strip()
    if not line or line.startswith(";") or "=" not in line:
        return None
    eq = line.index("=")
    key = line[:eq].strip()
    rest = line[eq + 1:]
    semi = rest.find(";")
    value = (rest[:semi] if semi >= 0 else rest).strip()
    comment = rest[semi:].strip() if semi >= 0 else ""
    return key, value, comment


class WeaponDoc:
    def __init__(self, text=""):
        self.lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if self.lines and self.lines[-1] == "":
            self.lines.pop()

    def text(self):
        return "\n".join(self.lines) + "\n"

    def entries(self):
        """[(line_index, key_lower, key_raw, value, comment)]"""
        out = []
        for i, raw in enumerate(self.lines):
            p = _parse_line(raw)
            if p:
                out.append((i, p[0].lower(), p[0], p[1], p[2]))
        return out

    def values(self, schema):
        """{canonical key name: value str | [str, ...] for repeatable keys}, plus
        unknown keys under their own spelling (last occurrence wins, like the loader)."""
        fields = {f["key"].lower(): f for f in schema.fields(schema.root)}
        out = {}
        for _, low, raw, value, _ in self.entries():
            f = fields.get(low)
            if f and f["type"] == "list":
                out.setdefault(f["key"], []).append(value)
            else:
                out[f["key"] if f else raw] = value
        return out

    def apply(self, schema, data):
        """Writes the form's values into the document: existing lines are updated in
        place (keeping their inline comments), cleared keys are removed, new keys are
        appended at the end. Keys the schema doesn't know are never touched."""
        fields = schema.fields(schema.root)
        by_low = {f["key"].lower(): f for f in fields}
        occurrences = {}
        for i, low, raw, value, comment in self.entries():
            if low in by_low:
                occurrences.setdefault(low, []).append((i, raw, comment))
        replace = {}   # line index -> list of replacement lines ([] deletes)
        appended = []
        for f in fields:
            low = f["key"].lower()
            occ = occurrences.get(low, [])
            want = data.get(f["key"])
            if f["type"] == "list":
                new_lines = [f"{f['key']} = {v}" for v in (want or [])]
                if occ:
                    replace[occ[0][0]] = new_lines
                    for i, _, _ in occ[1:]:
                        replace[i] = []
                elif new_lines:
                    appended += new_lines
                continue
            if want is None or want == "":
                for i, _, _ in occ:
                    replace[i] = []
                continue
            if occ:
                i, raw, comment = occ[-1]  # the occurrence the loader actually uses
                replace[i] = [f"{raw} = {want}" + (f" {comment}" if comment else "")]
                for j, _, _ in occ[:-1]:
                    replace[j] = []   # earlier duplicates were dead anyway
            else:
                appended.append(f"{f['key']} = {want}")
        out = []
        for i, line in enumerate(self.lines):
            out.extend(replace[i] if i in replace else [line])
        if appended:
            if out and out[-1].strip():
                out.append("")
            out.extend(appended)
        self.lines = out


# -- checking ------------------------------------------------------------------------

_JAVA_FLOAT = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?[fFdD]?$")
_INT = re.compile(r"^[+-]?\d+$")


def _is_float(s):
    return bool(_JAVA_FLOAT.match(s.strip()))


def _check_column(col, raw):
    """None if acceptable, else a message."""
    v = raw.strip()
    if v == "" and col.get("optional"):
        return None
    ctype = col["type"]
    if ctype == "int" and not _INT.match(v):
        return t("weapon.col_int", col=col["name"], v=v)
    if ctype == "float" and not _is_float(v):
        return t("weapon.col_float", col=col["name"], v=v)
    if ctype == "enum" and v.lower() not in [c.lower() for c in col["choices"]]:
        return t("weapon.col_choice", col=col["name"], v=v, choices="/".join(col["choices"]))
    return None


def _check_waypoint(key, value):
    """Exact row rules of CasWaypoint / CarrierLaunchWaypoint / CarrierRecoveryPoint parsers.
    Returns (dropped: bool, messages)."""
    parts = value.split(",")
    low = key.lower()
    msgs = []
    if low in ("caswaypoint", "carrierwaypoint"):
        if len(parts) != 5:
            return True, [t("weapon.wp_columns", n=5, got=len(parts))]
        for idx, name in enumerate(("relX", "relY", "relZ")):
            if not _INT.match(parts[idx].strip()):
                return True, [t("weapon.wp_int", col=name, v=parts[idx].strip())]
        if not _is_float(parts[3]):
            return True, [t("weapon.col_float", col="speed%", v=parts[3].strip())]
        if parts[4].strip().lower() not in ("true", "false", "1", "0"):
            return True, [t("weapon.col_choice", col="attack", v=parts[4].strip(), choices="true/false/1/0")]
        sp = float(parts[3].strip().rstrip("fFdD"))
        if not 0 <= sp <= 100:
            msgs.append(t("weapon.wp_speed_clamped", v=sp))
        return False, msgs
    if low in ("carrierlaunchwaypoint", "carrierlandingwaypoint"):
        if len(parts) < 4:
            return True, [t("weapon.wp_min_columns", n=4, got=len(parts))]
        for idx, name in enumerate(("relX", "relY", "relZ")):
            if not _INT.match(parts[idx].strip()):
                return True, [t("weapon.wp_int", col=name, v=parts[idx].strip())]
        if not _is_float(parts[3]):
            return True, [t("weapon.col_float", col="speed%", v=parts[3].strip())]
        for idx, name in ((4, "gear"), (5, "bay")):
            if len(parts) > idx and parts[idx].strip() not in ("", "-", "0", "1"):
                return True, [t("weapon.col_choice", col=name, v=parts[idx].strip(), choices="-/0/1")]
        if len(parts) > 6 and parts[6].strip() not in ("", "-") and not _is_float(parts[6]):
            return True, [t("weapon.col_float", col="boost", v=parts[6].strip())]
        return False, msgs
    if low == "carrierrecoverypoint":
        if len(parts) != 4:
            return True, [t("weapon.wp_columns", n=4, got=len(parts))]
        for p, name in zip(parts, ("relX", "relY", "relZ", "radius")):
            if not _is_float(p):
                return True, [t("weapon.col_float", col=name, v=p.strip())]
        return False, msgs
    if low == "item":
        pieces = value.split(",", 1)
        if len(pieces) != 2:
            return True, [t("weapon.item_format")]
        if not _is_float(pieces[0]):
            return False, [t("weapon.col_float", col="count", v=pieces[0].strip())]
        if pieces[1].strip().lower() not in ("iron_ingot", "gunpowder", "redstone"):
            return True, [t("weapon.item_name", v=pieces[1].strip())]
        return False, msgs
    return False, msgs


def check(schema, text):
    """Returns [(severity, line_number or None, key, message)]; line numbers are 1-based."""
    fields = {f["key"].lower(): f for f in schema.fields(schema.root)}
    doc = WeaponDoc(text)
    problems = []
    seen = {}
    type_value = None
    valid_rows = {}
    present = set()
    for idx, raw in enumerate(doc.lines):
        ln = idx + 1
        s = raw.strip()
        if s and not s.startswith(";") and "=" not in s:
            problems.append(("warning", ln, "", t("weapon.no_equals")))
            continue
        p = _parse_line(raw)
        if not p:
            continue
        key_raw, value, _ = p
        low = key_raw.lower()
        f = fields.get(low)
        if f is None:
            problems.append(("warning", ln, key_raw, t("weapon.unknown_key")))
            continue
        present.add(f["key"])
        if f["type"] == "list":
            dropped, msgs = _check_waypoint(f["key"], value)
            for m in msgs:
                problems.append(("error" if dropped else "warning", ln, f["key"], m + (" " + t("weapon.row_dropped") if dropped else "")))
            if not dropped:
                valid_rows[f["key"]] = valid_rows.get(f["key"], 0) + 1
            continue
        if low in seen:
            problems.append(("warning", seen[low], f["key"], t("weapon.duplicate", line=ln)))
        seen[low] = ln
        if f["key"] == "Type":
            type_value = value
        problems += [(sev, ln, f["key"], msg) for sev, msg in _check_value(f, value)]
    # Type applicability and completeness
    if type_value is None:
        problems.append(("error", None, "Type", t("weapon.no_type")))
    else:
        known = {tt.lower(): tt for tt in schema.entity_types}
        canonical = known.get(type_value.lower())
        if canonical is None and ":" not in type_value:
            problems.append(("error", seen.get("type"), "Type", t("weapon.bad_type", v=type_value)))
        if canonical:
            for key in present:
                f = fields[key.lower()]
                if not schema.field_applies(f, canonical):
                    problems.append(("warning", seen.get(key.lower()), key, t("weapon.not_for_type", type=canonical)))
            problems += _completeness(canonical, present, valid_rows)
    return problems


def _check_value(f, value):
    out = []
    ftype = f["type"]
    if ftype in ("float", "int") and value and not _is_float(value):
        out.append(("warning", t("weapon.not_number")))
    elif ftype == "int" and value and _is_float(value) and not _INT.match(value):
        out.append(("warning", t("weapon.truncated")))
    elif ftype == "bool" and value.lower() not in ("true", "false"):
        fallback = "true" if f.get("default") is True else "false"
        out.append(("warning", t("weapon.not_bool", fallback=fallback)))
    elif ftype == "enum" and f["key"] != "Type" and value.lower() not in [c.lower() for c in f.get("choices", [])]:
        out.append(("warning", t("weapon.bad_choice", choices=" / ".join(f.get("choices", [])))))
    elif ftype == "columns":
        parts = value.split(",")
        cols = f["columns"]
        if len(parts) > len(cols):
            out.append(("warning", t("weapon.extra_columns", n=len(cols))))
        for col, part in zip(cols, parts):
            msg = _check_column(col, part) if col["type"] != "string" else None
            if msg:
                out.append(("warning", msg + " " + t("weapon.col_default_used")))
    return out


def _completeness(weapon_type, present, valid_rows):
    """CAS/Carrier silently become inert if their required parts are missing."""
    out = []
    if weapon_type == "CAS":
        if "CasAircraft" not in present:
            out.append(("error", None, "CasAircraft", t("weapon.cas_needs_aircraft")))
        if not valid_rows.get("CasWaypoint"):
            out.append(("error", None, "CasWaypoint", t("weapon.cas_needs_waypoint")))
    if weapon_type == "Carrier":
        if "CarrierAircraft" not in present:
            out.append(("error", None, "CarrierAircraft", t("weapon.carrier_needs_aircraft")))
        if not valid_rows.get("CarrierWaypoint"):
            out.append(("error", None, "CarrierWaypoint", t("weapon.carrier_needs_waypoint")))
        if not valid_rows.get("CarrierLandingWaypoint"):
            out.append(("error", None, "CarrierLandingWaypoint", t("weapon.carrier_needs_landing")))
    return out
