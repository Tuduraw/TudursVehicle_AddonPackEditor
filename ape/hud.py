"""HUD scripts (assets/<ns>/hud/<name>.txt) - a port of the mod's own
client.hud.HudScript / HudExpr / HudCommand, used for checking and previewing.

Behavior deliberately mirrors the mod, including its quirks:
  - a bad line is skipped on its own; the rest of the file still works
  - "If" collects every following line up to the next "EndIf" - an If without EndIf
    swallows the rest of the file; an If inside an If isn't supported (the inner If is
    an unknown directive and the inner EndIf closes the OUTER If)
  - unknown variables evaluate to 0 without any error
  - Call to a missing script does nothing; a script already on the Call chain is skipped;
    Exit ends only the script it's in
  - coordinates are relative to the screen center, in GUI-scaled pixels
"""
import math
import re

from .i18n import t

# Variables the mod defines (client.hud.HudVariables). Values are preview samples.
NUMERIC_VARIABLES = {
    "center_x": None, "center_y": None, "width": None, "height": None,   # filled from the screen size
    "yaw": 35.0, "pitch": -4.0, "roll": 8.0, "plyr_yaw": 42.0, "plyr_pitch": -6.0,
    "altitude": 64.0, "sea_alt": 120.0, "pos_x": 120.0, "pos_y": 184.0, "pos_z": -340.0,
    "motion_x": 0.3, "motion_y": 0.0, "motion_z": 0.4, "speed": 0.5, "speed_kbh": 36.0,
    "hp": 75.0, "max_hp": 100.0, "hp_rto": 0.75, "hp_per": 75.0, "fuel": 0.6, "low_fuel": 0.0,
    "throttle": 0.8, "stick_x": 0.0, "stick_y": 0.0, "free_look": 0.0, "manual_mode": 0.0,
    "stalling": 0.0, "gear_deployed": 1.0,
    "wpn_ammo": 24.0, "wpn_rm_ammo": 30.0, "reloading": 0.0, "reload_time": 0.0,
    "wpn_heat": 0.3, "is_heat_wpn": 0.0, "lock_progress": 0.0, "locked": 0.0, "lock": 0.0,
    "wpn_mode": 1.0, "has_modes": 0.0, "sight_type": 0.0,
    "mortar_distance": 0.0, "has_mortar_distance": 0.0, "gun_yaw": 0.0, "gun_pitch": 0.0,
    "time": 6000.0,
    # present for compatibility, always fixed in the mod
    "dsp_mt_dist": 0.0, "mt_dist": 0.0, "have_radar": 0.0, "radar_rot": 0.0, "vtol_stat": 0.0,
    "cam_mode": 0.0, "cam_zoom": 1.0, "auto_pilot": 0.0, "have_flare": 0.0, "can_flare": 0.0,
    "inventory": 0.0, "hovering": 0.0, "is_uav": 0.0, "uav_fs": 0.0, "gunner_mode": 0.0, "test_mode": 0.0,
}
STRING_VARIABLES = {"wpn_name": "M2 Browning", "mortar_distance_str": "---"}
FIXED_VARIABLES = {"dsp_mt_dist", "mt_dist", "have_radar", "radar_rot", "vtol_stat", "cam_mode", "cam_zoom",
                   "auto_pilot", "have_flare", "can_flare", "inventory", "hovering", "is_uav", "uav_fs",
                   "gunner_mode", "test_mode"}
DIRECTIVES = ["If", "EndIf", "Exit", "Call", "Color", "DrawString", "DrawCenteredString", "DrawTexture",
              "DrawRect", "DrawLine", "DrawLineStipple", "DrawGraduationYaw", "DrawGraduationPitch1",
              "DrawGraduationPitch2", "DrawCameraRot", "DrawEntityRadar", "DrawEnemyRadar"]
MIN_ARGS = {"drawrect": 4, "drawgraduationyaw": 3, "drawgraduationpitch1": 3, "drawgraduationpitch2": 4,
            "drawcamerarot": 2, "drawstring": 3, "drawcenteredstring": 3, "drawtexture": 9}

# Minecraft default font advance widths (ASCII); anything else is treated as 6, CJK as 9.
_NARROW = {**{c: 2 for c in "!.,:;|'i"}, **{c: 3 for c in "l`"}, **{c: 4 for c in "It[] "},
           **{c: 5 for c in "\"()*<>fk{}"}, **{c: 7 for c in "@~"}}
FONT_HEIGHT = 9


def text_width(s):
    w = 0
    for ch in s:
        if ord(ch) > 0x2E80:
            w += 9
        else:
            w += _NARROW.get(ch, 6)
    return w


class HudError(Exception):
    pass


# -- expressions ----------------------------------------------------------------------

class Expr:
    """Same grammar and precedence as the mod's HudExpr: ternary, ||, &&, == !=,
    < > <= >=, + -, * /, unary - + !, parentheses, numbers, #hex / 0xhex, identifiers."""

    def __init__(self, source):
        self.source = source
        self.pos = 0
        self.variables_used = set()
        self.root = self._ternary()
        self._ws()
        if self.pos != len(source):
            raise HudError(t("hud.err_trailing", expr=source))

    def _ws(self):
        while self.pos < len(self.source) and self.source[self.pos].isspace():
            self.pos += 1

    def _peek(self):
        return self.source[self.pos] if self.pos < len(self.source) else "\0"

    def _match(self, lit):
        if self.source.startswith(lit, self.pos):
            self.pos += len(lit)
            return True
        return False

    def _ternary(self):
        cond = self._or()
        self._ws()
        if self._peek() == "?":
            self.pos += 1
            a = self._ternary()
            self._ws()
            if self._peek() != ":":
                raise HudError(t("hud.err_ternary", expr=self.source))
            self.pos += 1
            b = self._ternary()
            return ("?", cond, a, b)
        return cond

    def _binary_loop(self, sub, ops):
        left = sub()
        while True:
            self._ws()
            for op in ops:
                if self._match(op):
                    left = (op, left, sub())
                    break
            else:
                return left

    def _or(self):
        return self._binary_loop(self._and, ["||"])

    def _and(self):
        return self._binary_loop(self._eq, ["&&"])

    def _eq(self):
        return self._binary_loop(self._cmp, ["==", "!="])

    def _cmp(self):
        return self._binary_loop(self._add, [">=", "<=", ">", "<"])

    def _add(self):
        return self._binary_loop(self._mul, ["+", "-"])

    def _mul(self):
        return self._binary_loop(self._unary, ["*", "/"])

    def _unary(self):
        self._ws()
        c = self._peek()
        if c == "-":
            self.pos += 1
            return ("neg", self._unary())
        if c == "+":
            self.pos += 1
            return self._unary()
        if c == "!":
            self.pos += 1
            return ("not", self._unary())
        return self._primary()

    def _primary(self):
        self._ws()
        c = self._peek()
        if c == "(":
            self.pos += 1
            inner = self._ternary()
            self._ws()
            if self._peek() != ")":
                raise HudError(t("hud.err_paren", expr=self.source))
            self.pos += 1
            return inner
        if c == "#" or (c == "0" and self.source[self.pos + 1:self.pos + 2] in ("x", "X")):
            self.pos += 1 if c == "#" else 2
            m = re.match(r"[0-9a-fA-F]+", self.source[self.pos:])
            if not m:
                raise HudError(t("hud.err_hex", expr=self.source))
            self.pos += m.end()
            return ("num", float(int(m.group(0), 16)))
        if c.isdigit() or c == ".":
            m = re.match(r"[0-9.]+", self.source[self.pos:])
            self.pos += m.end()
            try:
                return ("num", float(m.group(0)))
            except ValueError:
                raise HudError(t("hud.err_number", expr=self.source))
        if c.isalpha() or c == "_":
            m = re.match(r"[A-Za-z0-9_]+", self.source[self.pos:])
            self.pos += m.end()
            name = m.group(0).lower()
            self.variables_used.add(name)
            return ("var", name)
        if c == "\0":
            raise HudError(t("hud.err_empty", expr=self.source))
        raise HudError(t("hud.err_char", ch=c, expr=self.source))

    def eval(self, v, node=None):
        n = self.root if node is None else node
        op = n[0]
        if op == "num":
            return n[1]
        if op == "var":
            return v.get(n[1], 0.0)
        if op == "neg":
            return -self.eval(v, n[1])
        if op == "not":
            return 1.0 if self.eval(v, n[1]) == 0 else 0.0
        if op == "?":
            return self.eval(v, n[2]) if self.eval(v, n[1]) != 0 else self.eval(v, n[3])
        a = self.eval(v, n[1])
        if op == "&&":
            return 1.0 if a != 0 and self.eval(v, n[2]) != 0 else 0.0
        if op == "||":
            return 1.0 if a != 0 or self.eval(v, n[2]) != 0 else 0.0
        b = self.eval(v, n[2])
        return {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
                "/": lambda: a / b if b != 0 else 0.0,
                "==": lambda: float(a == b), "!=": lambda: float(a != b), ">": lambda: float(a > b),
                "<": lambda: float(a < b), ">=": lambda: float(a >= b), "<=": lambda: float(a <= b)}[op]()


# -- parsing ----------------------------------------------------------------------------

def strip_comment(line):
    in_q = False
    for i, c in enumerate(line):
        if c == '"':
            in_q = not in_q
        elif c == ";" and not in_q:
            return line[:i]
    return line


def split_args(value):
    parts, cur, in_q = [], [], False
    for c in value:
        if c == '"':
            in_q = not in_q
            cur.append(c)
        elif c == "," and not in_q:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    if cur or parts:
        parts.append("".join(cur))
    if parts and not parts[-1].strip():
        parts.pop()
    return parts


def unquote(s):
    return s[1:-1] if len(s) >= 2 and s[0] == '"' and s[-1] == '"' else s


class Command:
    def __init__(self, kind, line, args=None, **extra):
        self.kind, self.line, self.args = kind, line, args or []
        self.__dict__.update(extra)


def _parse_line(line_text, ln, problems):
    eq = line_text.find("=")
    directive = (line_text[:eq] if eq >= 0 else line_text).strip()
    value = line_text[eq + 1:] if eq >= 0 else ""
    low = directive.lower()
    if not directive:
        return None
    if low == "exit":
        return Command("exit", ln)
    if low == "call":
        return Command("call", ln, name=value.strip().lower())
    if low == "endif":
        problems.append(("warning", ln, t("hud.stray_endif")))
        return None
    if low == "if":
        problems.append(("error", ln, t("hud.nested_if")))
        return None
    if low in ("drawentityradar", "drawenemyradar"):
        problems.append(("warning", ln, t("hud.radar_unsupported")))
        return None
    if low not in [d.lower() for d in DIRECTIVES]:
        problems.append(("error", ln, t("hud.unknown_directive", d=directive)))
        return None
    parts = split_args(value)
    need = MIN_ARGS.get(low)
    if need and len(parts) < need:
        problems.append(("error", ln, t("hud.too_few_args", d=directive, n=need, got=len(parts))))
        return None
    try:
        if low in ("drawstring", "drawcenteredstring"):
            fmt = unquote(parts[2].strip())
            data = [Expr(p.strip()) for p in parts[3:]]
            return Command("string", ln, [Expr(parts[0].strip()), Expr(parts[1].strip())], fmt=fmt, data=data,
                           centered=(low == "drawcenteredstring"), data_src=[p.strip() for p in parts[3:]])
        if low == "drawtexture":
            tex = unquote(parts[0].strip()).lower()
            exprs = [Expr(p.strip()) for p in parts[1:9]]
            rot = Expr(parts[9].strip()) if len(parts) > 9 else None
            return Command("texture", ln, exprs, texture=tex, rot=rot)
        exprs = [Expr(p.strip()) for p in parts]
        if low == "color" and len(exprs) not in (1,) and len(exprs) < 4:
            problems.append(("warning", ln, t("hud.color_args")))
        if low == "drawlinestipple":
            exprs = exprs[2:]
        if low in ("drawline", "drawlinestipple"):
            if len(exprs) < 4:
                problems.append(("warning", ln, t("hud.line_too_short")))
            elif len(exprs) % 2:
                problems.append(("warning", ln, t("hud.line_odd")))
        return Command(low, ln, exprs)
    except HudError as e:
        problems.append(("error", ln, str(e)))
        return None


def parse(text):
    """Returns (commands, problems). problems: [(severity, line, message)]."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    commands, problems = [], []
    i = 0
    while i < len(lines):
        ln = i + 1
        line = strip_comment(lines[i]).strip()
        i += 1
        if not line:
            continue
        eq = line.find("=")
        directive = (line[:eq] if eq >= 0 else line).strip()
        if directive.lower() == "if":
            body = []
            closed = False
            while i < len(lines):
                b = strip_comment(lines[i]).strip()
                i += 1
                bd = (b[:b.find("=")] if "=" in b else b).strip().lower()
                if bd == "endif":
                    closed = True
                    break
                if b:
                    body.append((i, b))
            if not closed:
                problems.append(("error", ln, t("hud.unclosed_if")))
            try:
                cond = Expr(line[eq + 1:].strip() if eq >= 0 else "")
            except HudError as e:
                problems.append(("error", ln, str(e)))
                continue
            sub = [c for c in (_parse_line(b, bl, problems) for bl, b in body) if c]
            commands.append(Command("if", ln, [cond], body=sub))
        else:
            c = _parse_line(line, ln, problems)
            if c:
                commands.append(c)
    return commands, problems


# -- formatting ---------------------------------------------------------------------------

_SPEC = re.compile(r"%[-+0#, ]*[0-9]*(\.[0-9]+)?([a-zA-Z%])")


def format_string(fmt, data, data_src, variables, string_vars):
    out, idx, last = [], 0, 0
    for m in _SPEC.finditer(fmt):
        out.append(fmt[last:m.start()])
        last = m.end()
        conv = m.group(2)
        spec = m.group(0)
        if conv == "%":
            out.append("%")
            continue
        expr = data[idx] if idx < len(data) else None
        src = data_src[idx].lower() if idx < len(data_src) else ""
        idx += 1
        py_spec = spec.replace(",", "")
        try:
            if conv == "s" and expr is not None and src in string_vars:
                out.append(py_spec % string_vars[src])
                continue
            value = expr.eval(variables) if expr else 0.0
            if conv == "d":
                out.append(py_spec % int(round(value)))
            elif conv == "s":
                out.append(py_spec % (str(int(value)) if value == math.floor(value) else str(value)))
            else:
                out.append(py_spec % value)
        except (TypeError, ValueError):
            out.append(spec)
    out.append(fmt[last:])
    return "".join(out)


def count_specifiers(fmt):
    return sum(1 for m in _SPEC.finditer(fmt) if m.group(2) != "%")


# -- execution ------------------------------------------------------------------------------

class Op:
    """One drawn primitive, in absolute GUI pixels. bbox = (x1, y1, x2, y2)."""

    def __init__(self, kind, line, script, color, bbox, **data):
        self.kind, self.line, self.script, self.color, self.bbox = kind, line, script, color, bbox
        self.__dict__.update(data)


class _Exit(Exception):
    pass


def run(commands, width, height, sample, strings, scripts=None, script_name=None):
    """Executes a parsed script like the mod does and returns the draw operations.
    scripts: {name: commands} for Call. Coordinates are relative to the center."""
    cx, cy = width // 2, height // 2
    v = dict(sample)
    v.update({"center_x": float(cx), "center_y": float(cy), "width": float(width), "height": float(height)})
    ops = []
    state = {"color": 0xFFFFFFFF}
    stack = set()

    def R(e):
        return int(round(e.eval(v)))

    def exec_list(cmds, name):
        for c in cmds:
            exec_one(c, name)

    def exec_one(c, name):
        k = c.kind
        if k == "exit":
            raise _Exit()
        if k == "call":
            if c.name in stack or not scripts or c.name not in scripts:
                return
            stack.add(c.name)
            try:
                exec_list(scripts[c.name], c.name)
            except _Exit:
                pass
            finally:
                stack.discard(c.name)
            return
        if k == "if":
            if c.args[0].eval(v) != 0:
                exec_list(c.body, name)
            return
        if k == "color":
            a = c.args
            if len(a) == 1:
                state["color"] = int(a[0].eval(v)) & 0xFFFFFFFF
            elif len(a) >= 4:
                ch = [int(x.eval(v)) & 0xFF for x in a[:4]]
                state["color"] = (ch[0] << 24) | (ch[1] << 16) | (ch[2] << 8) | ch[3]
            return
        col = state["color"]
        if k == "string":
            text = format_string(c.fmt, c.data, c.data_src, v, strings)
            px, py = cx + R(c.args[0]), cy + R(c.args[1])
            w = text_width(text)
            if c.centered:
                px -= w // 2
            ops.append(Op("text", c.line, name, col, (px, py, px + w, py + FONT_HEIGHT), text=text))
        elif k == "texture":
            x, y, w, h, u, vv, uw, vh = [e.eval(v) for e in c.args]
            if round(uw) == 0 or round(vh) == 0:
                return
            px, py = cx + round(x), cy + round(y)
            rot = c.rot.eval(v) if c.rot else 0.0
            corners = _rotated_rect(px + w / 2, py + h / 2, w, h, rot)
            xs, ys = [p[0] for p in corners], [p[1] for p in corners]
            ops.append(Op("texture", c.line, name, col, (min(xs), min(ys), max(xs), max(ys)), texture=c.texture,
                          x=px, y=py, w=w, h=h, u=round(u), v=round(vv), uw=round(uw), vh=round(vh), rot=rot, corners=corners))
        elif k == "drawrect":
            px, py = cx + R(c.args[0]), cy + R(c.args[1])
            w, h = R(c.args[2]), R(c.args[3])
            ops.append(Op("rect", c.line, name, col, (min(px, px + w), min(py, py + h), max(px, px + w), max(py, py + h))))
        elif k in ("drawline", "drawlinestipple"):
            pts = [e.eval(v) for e in c.args]
            for i in range(0, len(pts) - 3, 2):
                x1, y1, x2, y2 = cx + round(pts[i]), cy + round(pts[i + 1]), cx + round(pts[i + 2]), cy + round(pts[i + 3])
                ops.append(Op("line", c.line, name, col, (min(x1, x2), min(y1, y2), max(x1, x2) + 1, max(y1, y2) + 1), pts=(x1, y1, x2, y2)))
        elif k == "drawgraduationyaw":
            ang = c.args[0].eval(v)
            px, py = cx + R(c.args[1]), cy + R(c.args[2])
            for deg in range(-60, 61, 10):
                shown = _wrap(ang + deg)
                tx = px + deg * 2
                major = round(shown) % 30 == 0
                ops.append(Op("rect", c.line, name, col, (tx, py, tx + 1, py + (6 if major else 3))))
                if major:
                    label = str(int(round(shown)))
                    lw = text_width(label)
                    ops.append(Op("text", c.line, name, col, (tx - lw // 2, py + 8, tx - lw // 2 + lw, py + 8 + FONT_HEIGHT), text=label))
        elif k == "drawgraduationpitch1":
            ang = c.args[0].eval(v)
            px, py = cx + R(c.args[1]), cy + R(c.args[2])
            for deg in range(-40, 41, 10):
                ty = py - deg * 2
                tw = 10 if deg % 30 == 0 else 5
                ops.append(Op("rect", c.line, name, col, (px - tw, ty, px, ty + 1)))
                if deg % 30 == 0:
                    label = str(int(round(ang + deg)))
                    ops.append(Op("text", c.line, name, col, (px + 4, ty - 4, px + 4 + text_width(label), ty - 4 + FONT_HEIGHT), text=label))
        elif k == "drawgraduationpitch2":
            pitch, roll = c.args[0].eval(v), math.radians(c.args[1].eval(v))
            px = cx + R(c.args[2])
            py = cy + R(c.args[3]) - int(round(pitch * 2))
            dx, dy = math.cos(roll) * 40, math.sin(roll) * 40
            x1, y1, x2, y2 = int(px - dx), int(py - dy), int(px + dx), int(py + dy)
            ops.append(Op("line", c.line, name, col, (min(x1, x2), min(y1, y2), max(x1, x2) + 1, max(y1, y2) + 1), pts=(x1, y1, x2, y2)))
        elif k == "drawcamerarot":
            delta = _wrap(v.get("plyr_yaw", 0.0) - v.get("yaw", 0.0))
            px = cx + R(c.args[0]) + int(round(delta))
            py = cy + R(c.args[1])
            ops.append(Op("rect", c.line, name, col, (px - 1, py - 1, px + 1, py + 1)))

    try:
        exec_list(commands, script_name)
    except _Exit:
        pass
    return ops


def _wrap(d):
    d = math.fmod(d, 360.0)
    if d >= 180:
        d -= 360
    elif d < -180:
        d += 360
    return d


def _rotated_rect(cx, cy, w, h, deg):
    r = math.radians(deg)
    cos, sin = math.cos(r), math.sin(r)
    out = []
    for sx, sy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        out.append((cx + sx * cos - sy * sin, cy + sx * sin + sy * cos))
    return out


# -- checking --------------------------------------------------------------------------------

def check(text, known_scripts=(), known_textures=(), width=None, height=None, sample=None, strings=None, scripts=None):
    """Returns [(severity, line, message)] covering syntax, silent fallbacks and - when a
    screen size is given - elements drawn outside the screen (warnings only)."""
    commands, problems = parse(text)
    known_vars = set(NUMERIC_VARIABLES)
    known_scripts = {s.lower() for s in known_scripts}
    known_textures = {s.lower() for s in known_textures}

    def walk(cmds):
        for c in cmds:
            exprs = list(c.args) + list(getattr(c, "data", [])) + ([c.rot] if getattr(c, "rot", None) else [])
            for e in exprs:
                for var in sorted(e.variables_used - known_vars):
                    if var in STRING_VARIABLES:
                        if c.kind != "string":
                            problems.append(("warning", c.line, t("hud.string_var_numeric", v=var)))
                    else:
                        problems.append(("warning", c.line, t("hud.unknown_var", v=var)))
                for var in sorted(e.variables_used & FIXED_VARIABLES):
                    problems.append(("warning", c.line, t("hud.fixed_var", v=var)))
            if c.kind == "call" and c.name not in known_scripts:
                problems.append(("warning", c.line, t("hud.call_missing", name=c.name)))
            if c.kind == "texture" and c.texture not in known_textures:
                problems.append(("warning", c.line, t("hud.texture_missing", name=c.texture)))
            if c.kind == "string":
                n = count_specifiers(c.fmt)
                if n != len(c.data):
                    problems.append(("warning", c.line, t("hud.format_count", spec=n, args=len(c.data))))
            if c.kind == "if":
                walk(c.body)
    walk(commands)
    if width and height:
        ops = run(commands, width, height, sample or {}, strings or {}, scripts)
        reported = set()
        for op in ops:
            x1, y1, x2, y2 = op.bbox
            if x1 < 0 or y1 < 0 or x2 > width or y2 > height:
                key = (op.line if op.script is None else None, op.script)
                if key in reported:
                    continue
                reported.add(key)
                if op.script is None:
                    problems.append(("warning", op.line, t("hud.offscreen", w=width, h=height)))
                else:
                    problems.append(("warning", None, t("hud.offscreen_called", name=op.script, w=width, h=height)))
    problems.sort(key=lambda p: (p[1] or 0))
    return problems
