"""Extracts per-field descriptions from the mod's own Readme_*.md documents.

The docs follow a fixed shape: a "## ..." section whose heading contains a
backtick-quoted list key (e.g. "## 3. 座席(`seats`)") scopes the fields below it,
and each "### `field`" (or "### `a` / `b` / `c`") heading documents one or more
fields with "- **説明**:" / "- **Description**:" and "- **例**:" / "- **Example**:"
bullets. Anything else is ignored.
"""
import re
from pathlib import Path

DESC_LABELS = ("**説明**", "**Description**", "**書式・説明**", "**Format/description**")
EXAMPLE_LABELS = ("**例**", "**Example**")
FORMAT_LABELS = ("**書式**", "**Format**")


def clean_text(text):
    """Joins the docs' hard-wrapped lines back into running text and drops Markdown
    emphasis. Japanese lines are joined directly; a break between two ASCII words gets
    a space (so English wraps don't glue words together)."""
    out = ""
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if out and line.startswith("- "):   # keep nested bullet lists readable
            out += "\n" + line
            continue
        if out and (out[-1].isascii() and out[-1] not in "(（「" and line[0].isascii()):
            out += " "
        out += line
    return out.replace("**", "")


def clean_example(text):
    """Examples keep their line structure (a multi-line weapon file excerpt must stay
    one key per line); only blank lines and Markdown emphasis are dropped."""
    return "\n".join(l.strip() for l in text.split("\n") if l.strip()).replace("**", "")


def _bullet_blocks(lines):
    """Yields (label, text) for each '- **Label**: text' bullet, joining its
    indented continuation lines."""
    cur_label, cur = None, []
    for line in lines:
        m = re.match(r"^- (\*\*[^*]+\*\*)\s*[:：]\s*(.*)$", line)
        if m:
            if cur_label:
                yield cur_label, "\n".join(cur).strip()
            first = m.group(2)
            cur_label, cur = m.group(1), ([] if first.strip().startswith("```") else [first])
        elif cur_label and (line.startswith("  ") or line.strip() == ""):
            if not line.strip().startswith("```"):  # drop Markdown code fences from examples
                cur.append(line.strip())
        elif cur_label:
            yield cur_label, "\n".join(cur).strip()
            cur_label, cur = None, []
    if cur_label:
        yield cur_label, "\n".join(cur).strip()


SNAKE = r"[a-z_][a-z0-9_]*"
CAMEL = r"[A-Za-z][A-Za-z0-9]*"

INLINE_RE = re.compile(r"^\s*- ((?:`[a-z_][a-z0-9_]*`\s*/?\s*)+)(?:[(（][^)）]*[)）])?\s*(?:[:：]\s*(.*))?$")


def _inline_entries(body, context, key_re=SNAKE):
    """'- `a` / `b`(既定値...): text' bullets inside a section body, each becoming
    an entry scoped to context (the enclosing field). Continuation lines are the
    following lines indented deeper than the bullet itself."""
    out = []
    i = 0
    while i < len(body):
        m = (INLINE_RE if key_re == SNAKE else re.compile(INLINE_RE.pattern.replace(SNAKE, key_re))).match(body[i])
        if not m:
            i += 1
            continue
        indent = len(body[i]) - len(body[i].lstrip())
        keys = re.findall(r"`(" + key_re + r")`", m.group(1))
        text = [m.group(2) or ""]
        j = i + 1
        while j < len(body):
            ln = body[j]
            if ln.strip() == "" or (len(ln) - len(ln.lstrip())) <= indent or ln.lstrip().startswith(("- ", "```", "{", "}")):
                break
            text.append(ln.strip()); j += 1
        # A bullet whose own label carries the default, e.g. "(既定値`false`)", is still
        # useful as a description even with no text after the colon.
        label_note = re.search(r"[(（]([^)）]*)[)）]", body[i][: body[i].find(":") if ":" in body[i] else len(body[i])])
        desc = clean_text("\n".join(t for t in text if t))
        if not desc and label_note:
            desc = label_note.group(1)
        out.append({"context": context, "keys": keys, "desc": desc, "example": "", "format": "", "inline": True})
        i = j
    return out


def _section_intro(body):
    """First plain paragraph of a section (skipping blank lines, stopping at the
    first bullet, code fence, table or heading)."""
    para = []
    for ln in body:
        s = ln.strip()
        if not s:
            if para:
                break
            continue
        if s.startswith(("- ", "```", "|", "#", ">")):
            break
        para.append(s)
    return clean_text("\n".join(para))


def parse_doc(path, key_re=SNAKE):
    """Returns a list of entries: {context, keys, desc, example, format}."""
    text = Path(path).read_text(encoding="utf-8")
    entries = []
    context = None
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            keys = re.findall(r"`(" + key_re + r")`", line)
            context = keys[0] if keys else None
            j = i + 1
            body = []
            while j < len(lines) and not lines[j].startswith("#"):
                body.append(lines[j]); j += 1
            if context:
                entry = {"context": None, "keys": [context], "desc": _section_intro(body), "example": "", "format": ""}
                for label, val in _bullet_blocks(body):
                    if label in DESC_LABELS:
                        entry["desc"] = clean_text(val)
                    elif label in EXAMPLE_LABELS:
                        entry["example"] = clean_example(val)
                if entry["desc"]:
                    entries.append(entry)
            entries.extend(_inline_entries(body, context, key_re))
            i = j
            continue
        if line.startswith("### "):
            keys = re.findall(r"`(" + key_re + r")`", line)
            j = i + 1
            while line.rstrip().endswith("/") and j < len(lines) and lines[j].startswith("### "):
                line = lines[j]
                keys += re.findall(r"`(" + key_re + r")`", line)
                j += 1
            body = []
            while j < len(lines) and not lines[j].startswith("#"):
                body.append(lines[j]); j += 1
            if keys:
                entry = {"context": context, "keys": keys, "desc": "", "example": "", "format": ""}
                for label, val in _bullet_blocks(body):
                    if label in DESC_LABELS:
                        entry["desc"] = clean_text(val)
                    elif label in EXAMPLE_LABELS:
                        entry["example"] = clean_example(val)
                    elif label in FORMAT_LABELS:
                        entry["format"] = clean_text(val)
                entries.append(entry)
                entries.extend(_inline_entries(body, keys[0], key_re))
            else:
                entries.extend(_inline_entries(body, context, key_re))
            i = j
            continue
        i += 1
    return entries


def index_docs(entries):
    """Maps (context, key) -> entry, plus key -> [entries] for fallback lookup."""
    by_ctx, by_key = {}, {}
    for e in entries:
        for k in e["keys"]:
            by_ctx.setdefault((e["context"], k), e)
            by_key.setdefault(k, []).append(e)
    return by_ctx, by_key
