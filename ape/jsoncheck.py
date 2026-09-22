"""Manual-edit checks for vehicle JSON.

check_syntax() reports the first syntax error with its line/column (what the mod's own
loader would choke on). check_schema() runs after a successful parse and reports what
the mod would silently ignore or reject: missing required keys, wrong value types,
keys the schema doesn't know (typos, or top-level keys that belong inside a nested
object), and keys the chosen entity type doesn't use.
"""
import json

from .i18n import t


# Python's json module reports errors in English; these cover the ones people actually hit.
_MESSAGE_KEYS = [
    ("Expecting ',' delimiter", "json.msg.comma"),
    ("Expecting ':' delimiter", "json.msg.colon"),
    ("Expecting property name enclosed in double quotes", "json.msg.property"),
    ("Expecting value", "json.msg.value"),
    ("Unterminated string", "json.msg.unterminated"),
    ("Extra data", "json.msg.extra"),
    ("Invalid control character", "json.msg.control"),
    ("Invalid \\escape", "json.msg.escape"),
]
# For these, the real mistake is usually at the end of the previous line (a missing
# comma, or a trailing comma before a closing bracket) while the parser only notices
# on the next token - so the previous non-blank line is highlighted as well.
_LOOK_BACK = ("json.msg.comma", "json.msg.property")


class SyntaxProblem:
    def __init__(self, line, col, message, related_line=None):
        self.line, self.col, self.message, self.related_line = line, col, message, related_line

    def __str__(self):
        return t("json.syntax_error", line=self.line, col=self.col, msg=self.message)


def check_syntax(text):
    """Returns (data, None) or (None, SyntaxProblem)."""
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        key = next((k for prefix, k in _MESSAGE_KEYS if e.msg.startswith(prefix)), None)
        message = t(key) if key else e.msg
        related = None
        if key in _LOOK_BACK:
            lines = text.split("\n")
            i = e.lineno - 1                      # 0-based index of the reported line
            if lines[i][: e.colno - 1].strip() == "":  # offending token starts its line
                j = i - 1
                while j >= 0 and not lines[j].strip():
                    j -= 1
                related = j + 1 if j >= 0 else None     # back to 1-based
        return None, SyntaxProblem(e.lineno, e.colno, message, related)


_PY_TYPES = {
    "string": (str,), "identifier": (str,), "enum": (str,),
    "int": (int,), "float": (int, float), "bool": (bool,),
    "object": (dict,), "list": (list,),
}


def _type_ok(field, value):
    types = _PY_TYPES.get(field["type"], (object,))
    if isinstance(value, bool) and field["type"] in ("int", "float"):
        return False  # True/False is an int in Python but not a number in JSON
    if field["type"] == "int" and isinstance(value, float) and not value.is_integer():
        return False
    return isinstance(value, types)


def check_schema(schema, data, entity_type=None, record=None, path=""):
    """Returns a list of (severity, path, message); severity is 'error' or 'warning'."""
    record = record or schema.root
    problems = []
    if not isinstance(data, dict):
        return [("error", path or "/", t("json.not_object"))]
    all_fields = {f["key"]: f for f in schema.fields(record)}
    applicable = {f["key"] for f in schema.fields(record, entity_type)} if entity_type else set(all_fields)
    for key, field in all_fields.items():
        if field.get("required") and key not in data and key in applicable:
            problems.append(("error", f"{path}/{key}", t("json.missing_required")))
    for key, value in data.items():
        here = f"{path}/{key}"
        field = all_fields.get(key)
        if field is None and record == schema.root and key in schema.legacy_keys:
            problems.append(("warning", here, t("json.legacy_key", parent=schema.legacy_keys[key])))
            continue
        if field is None:
            hint = _misplaced_hint(schema, record, key)
            problems.append(("warning", here, t("json.unknown_key") + (f" {hint}" if hint else "")))
            continue
        if key not in applicable:
            problems.append(("warning", here, t("json.not_for_type")))
        if not _type_ok(field, value):
            problems.append(("error", here, t("json.wrong_type", expected=field["type"])))
            continue
        if field["type"] == "enum" and field.get("choices") and value not in field["choices"]:
            problems.append(("error", here, t("json.bad_choice", choices=", ".join(field["choices"]))))
        if field["type"] == "object" and field.get("ref"):
            problems += check_schema(schema, value, None, field["ref"], here)
        if field["type"] == "list" and field.get("item_ref"):
            for i, item in enumerate(value):
                problems += check_schema(schema, item, None, field["item_ref"], f"{here}[{i}]")
    return problems


def _misplaced_hint(schema, record, key):
    """If an unknown key exists inside one of this record's nested objects, say so -
    e.g. hide_entity written at the top level instead of inside passenger_display."""
    for f in schema.fields(record):
        ref = f.get("ref") if f["type"] == "object" else None
        if ref and schema.field(ref, key):
            return t("json.belongs_in", parent=f["key"])
    return ""
